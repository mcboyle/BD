"""Scheduled exports (Phase 139, Block Q).

Auto-run EOL exports / CSV dumps on a cadence. Useful for operators
who want weekly backups without manual triggering, or who pipe BD
state into other systems.

Schedule items stored in `scheduled_exports` table:
  • format: 'eol' | 'csv' | 'json' | 'ndjson' | 'm3u'
  • destination: a directory path
  • cadence_hours: how often to run
  • last_run_ts, last_run_ok, last_run_message
  • filter_json: optional JSON of filter args (for csv/json)
  • retention_count: how many past exports to keep (rolling)

The bg_scheduler invokes `run_due_exports()` periodically.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional


def _ensure_table():
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS scheduled_exports(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT DEFAULT '',
                format TEXT NOT NULL,
                destination TEXT NOT NULL,
                cadence_hours INTEGER NOT NULL,
                filter_json TEXT DEFAULT '',
                retention_count INTEGER DEFAULT 10,
                enabled INTEGER DEFAULT 1,
                created_at REAL NOT NULL,
                last_run_ts REAL DEFAULT 0,
                last_run_ok INTEGER,
                last_run_message TEXT DEFAULT '',
                next_run_ts REAL DEFAULT 0,
                source_saved_search INTEGER
            )""")
            # CAP-3 (v3.66.667): lazy-migrate the source_saved_search column onto
            # an existing table (null => today's behavior, byte-identical).
            cols = {r[1] for r in cx.execute(
                "PRAGMA table_info(scheduled_exports)").fetchall()}
            if "source_saved_search" not in cols:
                cx.execute("ALTER TABLE scheduled_exports "
                           "ADD COLUMN source_saved_search INTEGER")
    except Exception as e:
        sys.stderr.write(f"[scheduled_exports] schema init: {e}\n")


def add_schedule(*, label: str, format: str, destination: str,
                cadence_hours: int,
                filter_dict: Optional[dict] = None,
                retention_count: int = 10,
                source_saved_search: Optional[int] = None) -> Optional[int]:
    """Register a new schedule. Returns the row id.

    CAP-3 (v3.66.667): ``source_saved_search`` links the export to a saved
    search; at run time the export tracks that search's live criteria instead
    of the frozen ``filter_dict`` (null => unchanged behavior)."""
    _ensure_table()
    if format not in ("eol", "csv", "json", "ndjson", "m3u"):
        return None
    if not destination or cadence_hours <= 0:
        return None
    try:
        os.makedirs(destination, exist_ok=True)
    except OSError:
        return None
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cur = cx.execute("""INSERT INTO scheduled_exports(
                label, format, destination, cadence_hours,
                filter_json, retention_count, created_at, next_run_ts,
                source_saved_search
            ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (label[:200], format, destination, int(cadence_hours),
                 json.dumps(filter_dict or {}), int(retention_count),
                 time.time(), time.time(),
                 int(source_saved_search) if source_saved_search else None))
            return cur.lastrowid
    except Exception:
        return None


def remove_schedule(sid: int) -> bool:
    _ensure_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cur = cx.execute("DELETE FROM scheduled_exports WHERE id = ?",
                             (int(sid),))
            return cur.rowcount > 0
    except Exception:
        return False


def list_schedules() -> list:
    _ensure_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute("""SELECT * FROM scheduled_exports
                                  ORDER BY id ASC""").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["filter"] = json.loads(d.pop("filter_json") or "{}")
            except Exception:
                d["filter"] = {}
            out.append(d)
        return out
    except Exception:
        return []


def _resolve_source_rows(source_saved_search) -> Optional[list]:
    """CAP-3 (v3.66.667): when a scheduled export is linked to a saved search,
    resolve its rows through the SAME FTS path the search uses
    (``db_search_fts(query, site_id, status)``), so the export tracks the LIVE
    search criteria. Returns a row list, or None when there is no linked search
    or the search is missing/disabled -- the caller then falls back to the
    stored ``filter_json`` (fail-open). Function-local imports keep this off the
    module import graph."""
    if not source_saved_search:
        return None
    try:
        from . import saved_searches as _ss
        crit = _ss.criteria_for(int(source_saved_search))
        if not crit:
            return None
        from .db import db_search_fts
        return db_search_fts(crit.get("query") or "",
                             site_id=crit.get("site_id") or None,
                             status=crit.get("status") or None,
                             limit=10000)
    except Exception:
        return None


def _run_one(schedule: dict, *, s_cfg: Optional[dict] = None) -> dict:
    """Execute one scheduled export. Returns {ok, file, error}."""
    fmt = schedule["format"]
    dest_dir = schedule["destination"]
    if not os.path.isdir(dest_dir):
        try:
            os.makedirs(dest_dir, exist_ok=True)
        except OSError as e:
            return {"ok": False, "error": f"dest dir: {e}"}
    ts = time.strftime("%Y%m%d-%H%M%S")
    try:
        filter_dict = json.loads(schedule.get("filter_json") or "{}")
    except Exception:
        filter_dict = {}
    # CAP-3: a linked saved search overrides the frozen filter with its LIVE
    # FTS matches (None => not linked / search gone => fall back to filter_dict).
    src_rows = _resolve_source_rows(schedule.get("source_saved_search"))

    if fmt == "eol":
        out_dir = os.path.join(dest_dir, f"bd-eol-{ts}")
        try:
            from . import eol_export as _eol
            r = _eol.full_export(out_dir, s_cfg=s_cfg)
            return {"ok": r["ok"], "file": out_dir,
                    "files_written": len(r.get("files_written", []))}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    try:
        from . import exports as _exp
        if fmt == "csv":
            body = _exp.to_csv(filter_dict, rows=src_rows)
            ext = "csv"
        elif fmt == "json":
            body = _exp.to_json(filter_dict, rows=src_rows)
            ext = "json"
        elif fmt == "ndjson":
            body = _exp.to_ndjson(filter_dict, rows=src_rows)
            ext = "ndjson"
        elif fmt == "m3u":
            body = _exp.to_m3u(filter_dict, rows=src_rows)
            ext = "m3u"
        else:
            return {"ok": False, "error": f"unknown format: {fmt}"}
        path = os.path.join(dest_dir, f"bd-{fmt}-{ts}.{ext}")
        Path(path).write_bytes(body)
        return {"ok": True, "file": path, "size_bytes": len(body)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _apply_retention(schedule: dict):
    """Keep only the most recent N exports per schedule. Rotates
    old artifacts out."""
    dest = schedule.get("destination")
    fmt = schedule.get("format")
    keep = int(schedule.get("retention_count") or 10)
    if not dest or not os.path.isdir(dest):
        return
    try:
        # Find files matching the schedule's pattern
        prefix = "bd-eol-" if fmt == "eol" else f"bd-{fmt}-"
        entries = []
        for name in os.listdir(dest):
            if not name.startswith(prefix):
                continue
            full = os.path.join(dest, name)
            try:
                mtime = os.path.getmtime(full)
                entries.append((mtime, full, os.path.isdir(full)))
            except OSError:
                continue
        # Sort newest first, drop everything past `keep`
        entries.sort(reverse=True)
        for _mtime, full, is_dir in entries[keep:]:
            try:
                if is_dir:
                    import shutil
                    shutil.rmtree(full)
                else:
                    os.unlink(full)
            except OSError:
                pass
    except Exception:
        pass


def run_due_exports(*, s_cfg: Optional[dict] = None) -> dict:
    """Run all schedules whose next_run_ts is in the past.
    Called by bg_scheduler. Updates last_run + next_run on each."""
    _ensure_table()
    now = time.time()
    out = {"checked": 0, "ran": 0, "errors": 0, "results": []}
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            due = [dict(r) for r in cx.execute(
                """SELECT * FROM scheduled_exports
                   WHERE enabled = 1 AND next_run_ts <= ?
                   ORDER BY id ASC""", (now,)).fetchall()]
    except Exception as e:
        out["error"] = str(e)[:200]
        return out
    for sched in due:
        out["checked"] += 1
        result = _run_one(sched, s_cfg=s_cfg)
        # Update state
        try:
            from . import db as _db
            next_run = time.time() + (sched["cadence_hours"] * 3600)
            with _db.db_conn() as cx:
                cx.execute("""UPDATE scheduled_exports
                              SET last_run_ts = ?, last_run_ok = ?,
                                  last_run_message = ?,
                                  next_run_ts = ?
                              WHERE id = ?""",
                           (time.time(), 1 if result.get("ok") else 0,
                            (result.get("file") or result.get("error", ""))[:300],
                            next_run, sched["id"]))
        except Exception:
            pass
        # Apply retention
        try:
            _apply_retention(sched)
        except Exception:
            pass
        out["results"].append({**sched, **result})
        if result.get("ok"):
            out["ran"] += 1
        else:
            out["errors"] += 1
    return out
