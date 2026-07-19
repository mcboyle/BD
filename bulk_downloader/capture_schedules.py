"""Recurring-capture schedules (Cut 8, first new write surface).

Stores per-site recurring capture schedules and fires the due ones on a
cadence. Mirrors the `scheduled_exports` table pattern.

Schedule items live in the `capture_schedules` table:
  - site_id:       which configured site to re-capture
  - cadence_hours: how often to enqueue a fresh capture
  - urls_json:     optional explicit URL list (else the runner uses the
                   site's own start/pending URLs)
  - last_run_ts / last_run_ok / last_run_message
  - next_run_ts:   guards the scheduler against double-firing within a cadence

The bg_scheduler invokes `run_due(enqueue_fn=...)` periodically. The
`enqueue_fn(site_id, urls) -> int` seam is injected by the caller (in
production a thin adapter over `SiteRunner.load_urls`, the same path
`discovery` uses; tests pass a fake). This module deliberately knows
NOTHING about capture / extraction internals -- it only schedules and
delegates the enqueue.
"""
from __future__ import annotations

import json
import sys
import time
from typing import Callable, List, Optional


def _ensure_table():
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS capture_schedules(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id TEXT NOT NULL,
                label TEXT DEFAULT '',
                cadence_hours INTEGER NOT NULL,
                urls_json TEXT DEFAULT '',
                enabled INTEGER DEFAULT 1,
                created_at REAL NOT NULL,
                last_run_ts REAL DEFAULT 0,
                last_run_ok INTEGER,
                last_run_message TEXT DEFAULT '',
                next_run_ts REAL DEFAULT 0
            )""")
    except Exception as e:
        sys.stderr.write(f"[capture_schedules] schema init: {e}\n")


def add_schedule(*, site_id: str, cadence_hours: int,
                 label: str = "",
                 urls: Optional[List[str]] = None) -> Optional[int]:
    """Register a recurring-capture schedule. Returns the row id, or None
    on invalid input. Idempotent: an identical enabled (site_id,
    cadence_hours) schedule returns the existing id instead of duplicating
    -- the double-submit guard for the POST surface."""
    _ensure_table()
    site_id = (site_id or "").strip()
    if not site_id:
        return None
    try:
        cadence_hours = int(cadence_hours)
    except Exception:
        return None
    if cadence_hours <= 0:
        return None
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            existing = cx.execute(
                """SELECT id FROM capture_schedules
                   WHERE site_id = ? AND cadence_hours = ? AND enabled = 1""",
                (site_id, cadence_hours)).fetchone()
            if existing is not None:
                return int(existing["id"] if hasattr(existing, "keys")
                           else existing[0])
            now = time.time()
            cur = cx.execute(
                """INSERT INTO capture_schedules(
                    site_id, label, cadence_hours, urls_json,
                    created_at, next_run_ts
                ) VALUES (?,?,?,?,?,?)""",
                (site_id, (label or "")[:200], cadence_hours,
                 json.dumps(list(urls) if urls else []), now, now))
            return cur.lastrowid
    except Exception:
        return None


def remove_schedule(sid: int) -> bool:
    _ensure_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cur = cx.execute("DELETE FROM capture_schedules WHERE id = ?",
                             (int(sid),))
            return cur.rowcount > 0
    except Exception:
        return False


def list_schedules() -> list:
    _ensure_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute(
                "SELECT * FROM capture_schedules ORDER BY id ASC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["urls"] = json.loads(d.pop("urls_json") or "[]")
            except Exception:
                d["urls"] = []
            out.append(d)
        return out
    except Exception:
        return []


def _fire_one(cx, row: dict, enqueue_fn: Callable, now: float) -> dict:
    """Enqueue one schedule via the injected seam and update its timing.
    Never raises -- a failing enqueue is recorded as last_run_ok=0."""
    sid = row["id"]
    site_id = row["site_id"]
    try:
        urls = json.loads(row.get("urls_json") or "[]")
    except Exception:
        urls = []
    ok = True
    msg = ""
    n_enqueued = 0
    try:
        n = enqueue_fn(site_id, urls or None)
        n_enqueued = int(n) if n is not None else 0
        msg = f"enqueued {n_enqueued}"
    except Exception as e:  # the seam may fail; the schedule must not
        ok = False
        msg = str(e)[:200]
    # SCH-1: a run that enqueued new items is a change signal for this site.
    if ok and n_enqueued > 0:
        record_change(site_id)
    # SCH-1: self-tune the next interval from the site's change-rate when the
    # site opts in (sites_config.json: adaptive_cadence + cadence_min_h/max_h).
    # Default (unset) -> fixed cadence_hours, byte-identical. Site config only,
    # no env var -> nothing added to the config-surface env inventory.
    cadence = int(row["cadence_hours"])
    _acfg = _adaptive_cfg_for(site_id)
    if _acfg.get("adaptive"):
        try:
            cadence = adaptive_cadence_hours(
                cadence, change_count=change_count(site_id),
                min_hours=_acfg.get("min_h", 1), max_hours=_acfg.get("max_h", 168))
        except Exception:
            cadence = int(row["cadence_hours"])
    next_ts = now + cadence * 3600
    try:
        cx.execute(
            """UPDATE capture_schedules
               SET last_run_ts = ?, last_run_ok = ?, last_run_message = ?,
                   next_run_ts = ?
               WHERE id = ?""",
            (now, 1 if ok else 0, msg, next_ts, sid))
    except Exception:
        pass
    return {"id": sid, "site_id": site_id, "ok": ok, "message": msg}


def run_due(*, enqueue_fn: Callable,
            now: Optional[float] = None) -> dict:
    """Fire every enabled schedule whose next_run_ts has elapsed, via the
    injected enqueue_fn. The next_run_ts bump is the double-fire guard.
    Best-effort and side-effect-isolated: one bad enqueue can't abort the
    sweep or raise."""
    _ensure_table()
    if now is None:
        now = time.time()
    results: list = []
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute(
                """SELECT * FROM capture_schedules
                   WHERE enabled = 1 AND next_run_ts <= ?
                   ORDER BY id ASC""",
                (now,)).fetchall()
            for r in rows:
                results.append(_fire_one(cx, dict(r), enqueue_fn, now))
    except Exception as e:
        return {"ran": len(results), "results": results,
                "error": str(e)[:200]}
    return {"ran": len(results), "results": results}


def run_one(sid: int, *, enqueue_fn: Callable,
            now: Optional[float] = None) -> dict:
    """Force-run a single schedule now (the /run_now surface), bypassing
    the next_run guard. Returns the per-row result or an error dict."""
    _ensure_table()
    if now is None:
        now = time.time()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            r = cx.execute("SELECT * FROM capture_schedules WHERE id = ?",
                           (int(sid),)).fetchone()
            if r is None:
                return {"ok": False, "error": "schedule not found"}
            res = _fire_one(cx, dict(r), enqueue_fn, now)
        return {"ok": True, "result": res}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# ── SCH-1: self-tuning cadence from observed change-rate ─────────────
def _adaptive_cfg_for(site_id: str) -> dict:
    """Per-site adaptive-cadence opt-in read from sites_config.json (relative
    path, the same file the app persists). Undeclared site keys -- not surfaced
    in site_editor -- so invisible to the config-surface inventory. Fail-open to
    disabled. Returns {adaptive: bool, min_h: int, max_h: int}."""
    out = {"adaptive": False, "min_h": 1, "max_h": 168}
    try:
        from pathlib import Path as _P
        p = _P("sites_config.json")
        if not p.is_file():
            return out
        data = json.loads(p.read_text(encoding="utf-8"))
        sites = data.get("sites", data) if isinstance(data, dict) else {}
        sc = sites.get(site_id, {}) if isinstance(sites, dict) else {}
        out["adaptive"] = bool(sc.get("adaptive_cadence"))
        out["min_h"] = int(sc.get("cadence_min_h", 1) or 1)
        out["max_h"] = int(sc.get("cadence_max_h", 168) or 168)
    except Exception:
        pass
    return out


def adaptive_cadence_hours(base_hours, *, change_count: int, window_days: int = 7,
                           min_hours: int = 1, max_hours: int = 168) -> int:
    """Next cadence (hours) for a site given its recent change_count.

    A site that changed often shortens toward min_hours (base / (1+changes));
    a quiet site (0 changes) lengthens toward max_hours (base * 2). Always
    clamped to [min_hours, max_hours]. window_days is the ledger window the
    caller used for change_count (kept in the signature for call-site clarity).
    """
    try:
        base = int(base_hours)
    except (TypeError, ValueError):
        base = 24
    cc = max(0, int(change_count or 0))
    interval = (base * 2) if cc == 0 else (base / (1 + cc))
    interval = int(round(interval))
    lo, hi = int(min_hours), int(max_hours)
    if interval <= 0:
        interval = lo
    return max(lo, min(hi, interval))


def _ensure_change_ledger(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS schedule_changes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        site_id TEXT NOT NULL,
        ts REAL DEFAULT(strftime('%s','now')))""")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_schedchg_site "
               "ON schedule_changes(site_id, ts)")


def record_change(site_id: str) -> None:
    """Record that `site_id` had new content on a scheduled run (a change).
    Best-effort; never raises."""
    if not site_id:
        return
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            _ensure_change_ledger(cx)
            cx.execute("INSERT INTO schedule_changes(site_id, ts) "
                       "VALUES(?, strftime('%s','now'))", (str(site_id),))
    except Exception:
        pass


def change_count(site_id: str, *, window_days: int = 7) -> int:
    """How many changes `site_id` recorded in the last window_days. 0 on error."""
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            _ensure_change_ledger(cx)
            row = cx.execute(
                "SELECT COUNT(*) FROM schedule_changes WHERE site_id=? "
                "AND ts >= strftime('%s','now', ?)",
                (str(site_id), f"-{int(window_days)} days")).fetchone()
            return int(row[0] or 0) if row else 0
    except Exception:
        return 0


# ── F7: iCalendar (ICS) export of recurring schedules ────────────────
#
# Render the recurring-capture schedules as an RFC-5545 VCALENDAR so an
# operator can subscribe to (or import) the capture cadence in any calendar
# client. Pure string generation -- no `icalendar` dependency. Each schedule
# becomes one recurring VEVENT (RRULE FREQ=HOURLY;INTERVAL=cadence_hours,
# DTSTART=next_run_ts).

_ICS_PRODID = "-//BulkDownloader//schedules//EN"


def _ics_escape(text: str) -> str:
    """RFC 5545 3.3.11 TEXT escaping: backslash first, then ; , and newline."""
    s = str(text or "")
    s = s.replace("\\", "\\\\")
    s = s.replace(";", "\\;").replace(",", "\\,")
    s = s.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    return s


def _ics_fold(line: str) -> str:
    """Fold a content line to <=75 octets per RFC 5545 3.1 (continuation
    lines start with a single space)."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    out, i = [], 0
    limit = 75
    while i < len(raw):
        chunk = raw[i:i + limit]
        out.append(chunk.decode("utf-8", "ignore"))
        i += limit
        limit = 74  # continuation lines carry a leading space
    return "\r\n ".join(out)


def _ics_dt(ts) -> str:
    try:
        t = float(ts)
    except (TypeError, ValueError):
        t = 0.0
    if t <= 0:
        t = time.time()
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(t))


def schedules_to_ics(schedules, *, now: Optional[float] = None) -> str:
    """Build an RFC-5545 VCALENDAR string from a list of schedule dicts (the
    shape :func:`list_schedules` returns). One recurring VEVENT per schedule
    whose ``cadence_hours`` > 0; disabled schedules are emitted with
    ``STATUS:CANCELLED``. Pure; CRLF line endings; TEXT fields escaped."""
    now = time.time() if now is None else now
    dtstamp = _ics_dt(now)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{_ICS_PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for s in (schedules or []):
        try:
            cadence = int(s.get("cadence_hours", 0) or 0)
        except (TypeError, ValueError):
            cadence = 0
        if cadence <= 0:
            continue
        sid = s.get("id")
        site_id = str(s.get("site_id", "") or "")
        label = str(s.get("label", "") or "").strip()
        summary = label or f"Capture: {site_id}"
        status = "CONFIRMED" if s.get("enabled", 1) else "CANCELLED"
        n_urls = len(s.get("urls") or [])
        desc = (f"BulkDownloader recurring capture for site {site_id}; "
                f"every {cadence}h" + (f"; {n_urls} pinned url(s)" if n_urls else ""))
        lines += [
            "BEGIN:VEVENT",
            f"UID:bd-schedule-{sid}@bulkdownloader",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART:{_ics_dt(s.get('next_run_ts'))}",
            "DURATION:PT15M",
            f"RRULE:FREQ=HOURLY;INTERVAL={cadence}",
            _ics_fold(f"SUMMARY:{_ics_escape(summary)}"),
            _ics_fold(f"DESCRIPTION:{_ics_escape(desc)}"),
            f"STATUS:{status}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
