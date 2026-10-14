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
import re
import stat
import sys
import time
import uuid
from pathlib import Path
from typing import Optional


# ── sc-f9: where an export may be written, and what retention may remove ──

ALLOWLIST_KEY = "scheduled_export_allowed_dirs"
NOT_ALLOWED = "destination not allowed"


def _allowlist_text() -> str:
    """The stored allowlist setting: one absolute directory per line. A missing
    key or any non-string stored value reads as "" (root only)."""
    try:
        from . import global_config as _gc
        value = _gc.get_config().get(ALLOWLIST_KEY, "")
    except Exception:
        return ""
    return value if isinstance(value, str) else ""


def _allowlist_unreadable() -> bool:
    """True when the settings store exists but cannot be read. The allowlist
    then reads as "" (root only): exports outside the root are skipped, and
    the skip log says the setting is the reason."""
    try:
        from . import global_config as _gc
        _gc._read_current_or_refuse()
        return False
    except Exception:
        return True


def _allowlist_wrong_type() -> bool:
    """True when the store holds the setting as something other than text."""
    try:
        from . import global_config as _gc
        raw = _gc._read_current_or_refuse()
    except Exception:
        return False                # unreadable: set_config refuses that itself
    return ALLOWLIST_KEY in raw and not isinstance(raw[ALLOWLIST_KEY], str)


def _allowlist_line(path: str) -> str:
    """``path`` as one line of the setting. A path the line format would
    change -- outer whitespace, a line break inside it, a leading # or quote --
    is written as a JSON string, which export_roots reads back exactly."""
    if (path == path.strip() and path.splitlines() == [path]
            and not path.startswith(("#", '"'))):
        return path
    return json.dumps(path)


def _line_dir(line: str) -> Optional[str]:
    """The directory one line of the setting names, as written; None for a
    blank, a comment, a malformed quoted line or a relative path."""
    line = line.strip()
    if line.startswith('"'):
        try:
            line = json.loads(line)
        except ValueError:
            return None
        if not isinstance(line, str):
            return None
    elif line.startswith("#"):
        return None
    # a relative entry would mean "wherever cwd is"
    return line if line and os.path.isabs(line) else None


def _storable(path: str) -> bool:
    """Can the settings store hold ``path`` as a line and give it back? Not a
    name that is not UTF-8 (the store is a UTF-8 file): saving one fails the
    whole write, so it is judged before it is put beside the others."""
    try:
        line = _allowlist_line(path)
        line.encode("utf-8")
    except (UnicodeError, ValueError):
        return False
    return _line_dir(line) == path


def _allowlist_dirs() -> list:
    """The directories the setting names, as written (not yet resolved)."""
    dirs = [_line_dir(line) for line in _allowlist_text().splitlines()]
    return [d for d in dirs if d is not None]


def export_roots(*, pending: bool = True) -> list:
    """The directories a scheduled export may write under, symlinks resolved:
    first ``exports/`` in the app home (BD_HOME, else BD_INSTALL_DIR, else the
    working directory), then every absolute directory of the
    ``scheduled_export_allowed_dirs`` setting. Read at call time, so a settings
    save or a changed home is honoured without a restart.

    ``pending`` adds the destinations the upgrade seed has judged and not yet
    been able to save (seed_allowlist_once): their schedules go on exporting as
    they did before the rule, until the line is stored."""
    home = (os.environ.get("BD_HOME") or os.environ.get("BD_INSTALL_DIR")
            or os.getcwd())
    roots = [os.path.realpath(os.path.join(home, "exports"))]
    for line in _allowlist_dirs():
        try:
            real = os.path.realpath(line)
        except (OSError, ValueError):
            continue
        if real not in roots:
            roots.append(real)
    if pending:
        roots.extend(r for r in _pending.values() if r not in roots)
    return roots


_SEEDED = "allowlist_seeded"
_NOT_STORABLE = "not_storable"
NOT_STORABLE = "destination not storable"
SEED_PENDING = "seed pending, retry"
# schedule id -> realpath of a legacy destination whose allowlist line the last
# seed pass could not save. Rebuilt by every pass; empty once the seed is done.
_pending: dict = {}
_pending_logged: set = set()


def pending_ids() -> set:
    """Schedules whose legacy destination is judged but not yet stored."""
    return set(_pending)


def _meta_get(key: str) -> Optional[str]:
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            row = cx.execute("SELECT value FROM scheduled_exports_meta WHERE key = ?",
                             (key,)).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def not_storable_ids() -> set:
    """Schedules the upgrade seed could not put in the setting (their ids)."""
    try:
        return {int(x) for x in json.loads(_meta_get(_NOT_STORABLE) or "[]")}
    except (ValueError, TypeError):
        return set()


def seed_allowlist_once() -> list:
    """Upgrade step, run until it has succeeded once: add the realpath of every
    stored schedule's destination that is not already allowed to the allowlist
    setting, so no schedule saved before the rule existed stops working.
    Returns the directories it added.

    Per destination: each one is judged and saved on its own.
    - A line the setting cannot hold at all (_storable: a folder whose name is
      not UTF-8 or has a control character, a line too long) is left out,
      logged, and its schedule is listed as "destination not storable". Only
      that pre-check says so; a failed save never does.
    - A save that fails for one line, while the store still takes a write,
      leaves that line PENDING: every later pass tries it again, and until it
      is stored its schedule goes on exporting (export_roots) and is listed as
      "seed pending, retry". Every other destination is still seeded.
    - A store that takes no write at all (unreadable file, wrong-typed
      setting) stops the pass, so the next call retries; nothing is replaced,
      lines are only added.

    "Once" is a row in scheduled_exports_meta, written after a pass that left
    nothing pending. It must not run again after that: a later run would
    allowlist wherever a destination has come to resolve since, and would put
    back a folder the operator has removed."""
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS scheduled_exports_meta(
                key TEXT PRIMARY KEY, value TEXT)""")
            if cx.execute("SELECT 1 FROM scheduled_exports_meta WHERE key = ?",
                          (_SEEDED,)).fetchone():
                _pending.clear()
                return []
            rows = cx.execute("SELECT id, destination FROM scheduled_exports "
                              "ORDER BY id ASC").fetchall()
    except Exception as e:
        sys.stderr.write(f"[scheduled_exports] allowlist seed skipped: {e}\n")
        return []
    added, bad_ids, waiting = [], [], {}
    for sid, d in rows:
        # judged against the STORED lines only: a pending line is not stored
        if (not isinstance(d, str) or not d
                or resolve_destination(d, pending=False) is not None):
            continue                # nothing to seed: empty, or allowed already
        try:
            real = os.path.realpath(d)
        except (OSError, ValueError):
            continue                # no such path can exist: it never exported
        if not _storable(real):
            bad_ids.append(sid)
            sys.stderr.write(f"[scheduled_exports] schedule {sid}: {NOT_STORABLE}, "
                             f"not seeded: {real!r}\n")
            continue
        saved = _seed_one(real)
        if saved is None:           # the store takes no write: retry next call
            _pending.update(waiting)
            return added
        if saved:
            added.append(real)
            _pending_logged.discard(sid)
            sys.stderr.write("[scheduled_exports] allowlist seeded with existing "
                             f"destination {real}\n")
        else:
            waiting[sid] = real
            if sid not in _pending_logged:
                _pending_logged.add(sid)
                sys.stderr.write(f"[scheduled_exports] schedule {sid}: {SEED_PENDING}: "
                                 f"the allowlist line for {real!r} was not saved\n")
    _pending.clear()
    _pending.update(waiting)
    try:
        with _db.db_conn() as cx:
            cx.execute("INSERT OR REPLACE INTO scheduled_exports_meta(key, value) VALUES (?,?)",
                       (_NOT_STORABLE, json.dumps(bad_ids)))
            if not waiting:
                cx.execute("INSERT OR REPLACE INTO scheduled_exports_meta(key, value) VALUES (?,?)",
                           (_SEEDED, str(time.time())))
    except Exception as e:
        sys.stderr.write(f"[scheduled_exports] allowlist seed marker not saved: {e}\n")
    return added


def _seed_one(real: str) -> Optional[bool]:
    """Add one directory to the setting. True = saved; False = the store took
    a write but not this line; None = the store takes no write at all."""
    if _allowlist_wrong_type():
        # A stored value that is not text is the operator's to repair: it reads
        # as "" (root only), and the seed does not write over it.
        sys.stderr.write("[scheduled_exports] allowlist seed waits: the stored "
                         f"{ALLOWLIST_KEY} setting is not text\n")
        return None
    text = "\n".join([ln for ln in _allowlist_text().splitlines() if ln.strip()]
                     + [_allowlist_line(real)])
    try:
        from . import global_config as _gc
        if not _gc.set_config({ALLOWLIST_KEY: text}):
            # this line, or the store? An empty update writes nothing new.
            return False if _gc.set_config({}) else None
    except Exception as e:
        sys.stderr.write(f"[scheduled_exports] allowlist seed not saved: {e}\n")
        return None
    # The running app answers GET /api/global_config from its own dict and
    # overlays that dict on the file at every save: keep it in step.
    kernel = sys.modules.get("bulk_downloader.app_kernel")
    live = getattr(kernel, "_app_cfg", None)
    if isinstance(live, dict):
        live[ALLOWLIST_KEY] = text
    return True


def resolve_destination(destination, *, pending: bool = True) -> Optional[str]:
    """``destination`` with every symlink resolved when it is an export root
    or lies under one, else None. The realpath is what gets judged AND what is
    then used, so a link that points out of the root is refused, not followed."""
    if not isinstance(destination, str) or not destination:
        return None
    try:
        real = os.path.realpath(destination)
    except (OSError, ValueError):
        return None
    for root in export_roots(pending=pending):
        try:
            if os.path.commonpath([real, root]) == root:
                return real
        except ValueError:
            continue
    return None


def _outside_root_error(destination) -> str:
    return (f"destination {destination!r} is outside the export root "
            f"{export_roots()[0]} and the allowed folders setting")


def _retention_count(value) -> Optional[int]:
    """A whole retention count >= 1, else None. 0 or a negative count made the
    slice in _apply_retention delete every export, the one just written too."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    if n < 1 or (isinstance(value, float) and value != n):
        return None
    return n


def _own_export_name(schedule: dict):
    """Regex for the exact file names _run_one writes for this schedule:
    bd-<fmt>-<id>-<YYYYMMDD>-<HHMMSS>[-<32 hex>].<fmt>. An id is never a prefix
    of another id here (schedule 1 does not own bd-csv-10-...)."""
    fmt = re.escape(str(schedule.get("format")))
    return re.compile(rf"bd-{fmt}-{int(schedule['id'])}-\d{{8}}-\d{{6}}"
                      rf"(?:-[0-9a-f]{{32}})?\.{fmt}")


_skip_logged: set = set()


def _skip_not_allowed(schedule: dict) -> dict:
    """A due schedule whose destination is not under an export root or allowed
    folder is skipped, and that is all: the row is not written (it stays
    enabled and due), so it exports on the first pass after the folder is
    allowed again -- or after an unreadable setting can be read again. Logged
    once per schedule until it next runs, not at every scheduler tick."""
    sid = schedule.get("id")
    if sid not in _skip_logged:
        _skip_logged.add(sid)
        why = ("; the allowed folders setting could not be read"
               if _allowlist_unreadable() else "")
        what = NOT_STORABLE if sid in not_storable_ids() else NOT_ALLOWED
        sys.stderr.write(f"[scheduled_exports] schedule {sid} skipped, {what}: "
                         f"{_outside_root_error(schedule.get('destination'))}{why}\n")
    return {"id": sid, "label": schedule.get("label"),
            "destination": schedule.get("destination")}


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
    seed_allowlist_once()
    retention_count = _retention_count(retention_count)
    if retention_count is None:
        return None
    # Judged BEFORE anything is created: nothing is made outside the root.
    # judged against the stored setting only: a pending legacy line admits
    # its own schedule, not a new one
    destination = resolve_destination(destination, pending=False)
    if destination is None:
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
    seed_allowlist_once()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute("""SELECT * FROM scheduled_exports
                                  ORDER BY id ASC""").fetchall()
        out = []
        unstorable = not_storable_ids()
        for r in rows:
            d = dict(r)
            try:
                d["filter"] = json.loads(d.pop("filter_json") or "{}")
            except Exception:
                d["filter"] = {}
            d["destination_allowed"] = resolve_destination(d.get("destination")) is not None
            if d.get("id") in _pending:
                d["last_status"] = SEED_PENDING     # exporting; its line is retried
            elif not d["destination_allowed"]:
                # the status the SPA row shows
                d["last_status"] = NOT_STORABLE if d.get("id") in unstorable else NOT_ALLOWED
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
    # Re-judged at every run: the row may predate the root, or a path inside
    # the root may have become a link out of it since it was saved.
    dest_dir = resolve_destination(schedule["destination"])
    if dest_dir is None:
        return {"ok": False, "error": _outside_root_error(schedule["destination"])}
    if not os.path.isdir(dest_dir):
        try:
            os.makedirs(dest_dir, exist_ok=True)
        except OSError as e:
            return {"ok": False, "error": f"dest dir: {e}"}
    ts = time.strftime("%Y%m%d-%H%M%S")
    run_tag = f"{schedule['id']}-{ts}-{uuid.uuid4().hex}"
    try:
        filter_dict = json.loads(schedule.get("filter_json") or "{}")
    except Exception:
        filter_dict = {}
    # CAP-3: a linked saved search overrides the frozen filter with its LIVE
    # FTS matches (None => not linked / search gone => fall back to filter_dict).
    src_rows = _resolve_source_rows(schedule.get("source_saved_search"))
    if (src_rows is None and schedule.get("source_saved_search")
            and not filter_dict and fmt != "eol"):
        # The fallback is the FROZEN filter. A link-only export has none
        # ({}), and {} means "no filter": with its search deleted, disabled or
        # unrunnable the export silently widened to the whole history. There is
        # nothing to fall back to, so the run fails and writes nothing.
        return {"ok": False,
                "error": f"linked saved search {schedule['source_saved_search']} "
                         "is missing, disabled or could not be run, and this "
                         "export has no stored filter to fall back to"}

    if fmt == "eol":
        out_dir = os.path.join(dest_dir, f"bd-eol-{run_tag}")
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
        path = os.path.join(dest_dir, f"bd-{fmt}-{run_tag}.{ext}")
        Path(path).write_bytes(body)
        return {"ok": True, "file": path, "size_bytes": len(body)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _apply_retention(schedule: dict, *, keep_path: Optional[str] = None):
    """Keep only the most recent N exports of THIS schedule; unlink the rest.

    Removes regular files only, and only names this schedule itself writes
    (_own_export_name): no directory is removed (so an eol export, which is a
    directory, is never rotated here), no symlink is followed or removed, and
    ``keep_path`` -- the export this run just wrote -- is never a candidate.
    Names are judged and unlinked relative to one open handle on the resolved
    destination, so a path swapped mid-sweep cannot redirect a removal."""
    fmt = schedule.get("format")
    keep = _retention_count(schedule.get("retention_count"))
    if keep is None:
        # A stored row add_schedule would refuse today: NULL / 0 keep meaning
        # the default 10 (as before); a negative count keeps 1, where it used
        # to delete every export of the schedule.
        raw = schedule.get("retention_count")
        keep = 1 if isinstance(raw, (int, float)) and raw < 0 else 10
    dest = resolve_destination(schedule.get("destination"))
    if not dest or not fmt or not os.path.isdir(dest):
        return
    keep_name = os.path.basename(keep_path) if keep_path else None
    try:
        own = _own_export_name(schedule)
        dir_fd = os.open(dest, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except (OSError, TypeError, ValueError, KeyError):
        return
    try:
        entries = []
        for name in os.listdir(dir_fd):
            if not own.fullmatch(name):
                continue
            try:
                st = os.lstat(name, dir_fd=dir_fd)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                # The export just written sorts first whatever its mtime says,
                # so it always takes one of the `keep` places.
                entries.append((name == keep_name, st.st_mtime_ns, name))
        # Sort newest first, drop everything past `keep`
        entries.sort(reverse=True)
        for _kept, _mtime, name in entries[keep:]:
            try:
                os.unlink(name, dir_fd=dir_fd)
            except OSError:
                pass
    except Exception:
        pass
    finally:
        os.close(dir_fd)


def run_due_exports(*, s_cfg: Optional[dict] = None,
                    force: bool = False) -> dict:
    """Run all schedules whose next_run_ts is in the past.
    Called by bg_scheduler. Updates last_run + next_run on each.

    ``force`` (the run_now surface) runs every ENABLED schedule whether or not
    it is due; its cadence then restarts from now."""
    _ensure_table()
    now = time.time()
    out = {"checked": 0, "ran": 0, "errors": 0, "results": []}
    seed_allowlist_once()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            due = [dict(r) for r in cx.execute(
                """SELECT * FROM scheduled_exports
                   WHERE enabled = 1 AND (next_run_ts <= ? OR ?)
                   ORDER BY id ASC""", (now, 1 if force else 0)).fetchall()]
    except Exception as e:
        out["error"] = str(e)[:200]
        return out
    for sched in due:
        if resolve_destination(sched.get("destination")) is None:
            out.setdefault("skipped_destination_not_allowed", []).append(
                _skip_not_allowed(sched))
            continue
        _skip_logged.discard(sched.get("id"))
        out["checked"] += 1
        # Advance next_run_ts BEFORE exporting: if that write fails the
        # schedule stays due, so exporting anyway would repeat on every tick.
        try:
            from . import db as _db
            next_run = time.time() + (sched["cadence_hours"] * 3600)
            with _db.db_conn() as cx:
                cx.execute("""UPDATE scheduled_exports SET next_run_ts = ?
                              WHERE id = ?""", (next_run, sched["id"]))
        except Exception as e:
            sys.stderr.write(f"[scheduled_exports] schedule {sched['id']}: "
                             f"next_run_ts update failed, export skipped: {e}\n")
            out["results"].append({**sched, "ok": False,
                                   "error": f"next_run_ts update failed: {e}"[:200]})
            out["errors"] += 1
            continue
        result = _run_one(sched, s_cfg=s_cfg)
        # Record the outcome
        try:
            with _db.db_conn() as cx:
                cx.execute("""UPDATE scheduled_exports
                              SET last_run_ts = ?, last_run_ok = ?,
                                  last_run_message = ?
                              WHERE id = ?""",
                           (time.time(), 1 if result.get("ok") else 0,
                            (result.get("file") or result.get("error", ""))[:300],
                            sched["id"]))
        except Exception as e:
            sys.stderr.write(f"[scheduled_exports] schedule {sched['id']}: "
                             f"last_run update failed: {e}\n")
        # Apply retention
        try:
            _apply_retention(sched, keep_path=result.get("file") if result.get("ok") else None)
        except Exception:
            pass
        out["results"].append({**sched, **result})
        if result.get("ok"):
            out["ran"] += 1
        else:
            out["errors"] += 1
    return out
