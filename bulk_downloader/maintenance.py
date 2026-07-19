"""Maintenance windows (Phase 160, Block Q).

Operator wants to schedule planned downtime: "every Sunday 02:00-04:00,
pause workers because the upstream is doing backups." Different from
quiet_hours (which is bandwidth conservation): maintenance windows are
about communicating intent — UI shows a banner, webhooks fire on
start/end, all writes can be paused.

Schema:
  maintenance_windows(
    id, label,
    start_iso, end_iso,    # exact one-shot OR
    rrule,                  # recurring (RFC 5545 subset)
    actions_paused          # JSON: which subsystems pause
  )

Actions that can pause:
  • workers — stop downloading
  • discovery — stop polling RSS
  • exports — skip scheduled exports
  • webhooks — queue but don't deliver
  • all — everything above

Hooks: when a window starts, fire 'maintenance.start' webhook event;
when it ends, fire 'maintenance.end'. UI shows a banner during.
"""
from __future__ import annotations

import datetime
import json
import sys
import time
from typing import Optional


def _ensure_tables():
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS maintenance_windows(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT DEFAULT '',
                start_iso TEXT NOT NULL,
                end_iso TEXT NOT NULL,
                rrule TEXT DEFAULT '',
                actions_paused TEXT NOT NULL,
                created_at REAL NOT NULL,
                enabled INTEGER DEFAULT 1
            )""")
            cx.execute("""CREATE TABLE IF NOT EXISTS maintenance_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                window_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                message TEXT
            )""")
    except Exception as e:
        sys.stderr.write(f"[maintenance] schema: {e}\n")


def add_window(*, label: str, start_iso: str, end_iso: str,
              rrule: str = "",
              actions_paused: Optional[list] = None) -> Optional[int]:
    """Register a maintenance window. ISO times in operator-local TZ."""
    _ensure_tables()
    if not start_iso or not end_iso:
        return None
    # Validate by attempting parse
    try:
        s = datetime.datetime.fromisoformat(start_iso)
        e = datetime.datetime.fromisoformat(end_iso)
        if e <= s:
            return None
    except (ValueError, TypeError):
        return None
    paused = actions_paused or ["workers"]
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cur = cx.execute("""INSERT INTO maintenance_windows(
                label, start_iso, end_iso, rrule,
                actions_paused, created_at
            ) VALUES (?,?,?,?,?,?)""",
                (label[:200], start_iso, end_iso, rrule,
                 json.dumps(paused), time.time()))
            return cur.lastrowid
    except Exception:
        return None


def remove_window(wid: int) -> bool:
    _ensure_tables()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cur = cx.execute("DELETE FROM maintenance_windows WHERE id = ?",
                             (int(wid),))
            return cur.rowcount > 0
    except Exception:
        return False


def list_windows(*, include_past: bool = False) -> list:
    _ensure_tables()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute("""SELECT * FROM maintenance_windows
                                  ORDER BY start_iso ASC""").fetchall()
        out = []
        now = datetime.datetime.now()
        for r in rows:
            d = dict(r)
            try:
                d["actions_paused"] = json.loads(d.get("actions_paused") or "[]")
            except Exception:
                d["actions_paused"] = []
            try:
                end_dt = datetime.datetime.fromisoformat(d["end_iso"])
                if not include_past and not d.get("rrule") and end_dt < now:
                    continue
                d["status"] = ("active" if _is_active_now(d, now)
                              else ("future" if datetime.datetime.fromisoformat(d["start_iso"]) > now
                                    else "past"))
            except Exception:
                pass
            out.append(d)
        return out
    except Exception:
        return []


def _is_active_now(window: dict, now: datetime.datetime) -> bool:
    """Check if `window` is currently active."""
    try:
        start = datetime.datetime.fromisoformat(window["start_iso"])
        end = datetime.datetime.fromisoformat(window["end_iso"])
    except (ValueError, KeyError):
        return False
    if not window.get("enabled", True):
        return False
    # One-shot
    if not window.get("rrule"):
        return start <= now <= end
    # Recurring — best-effort: only handle 'FREQ=WEEKLY;BYDAY=...' for now
    rrule = window["rrule"]
    if "FREQ=WEEKLY" in rrule:
        # Extract BYDAY
        days_part = ""
        for token in rrule.split(";"):
            if token.startswith("BYDAY="):
                days_part = token[6:]
                break
        if not days_part:
            return False
        day_map = {"MO": 0, "TU": 1, "WE": 2, "TH": 3,
                   "FR": 4, "SA": 5, "SU": 6}
        days = {day_map.get(d.strip().upper())
                for d in days_part.split(",")}
        if now.weekday() not in days:
            return False
        # Match the start/end time-of-day
        start_t = start.time()
        end_t = end.time()
        now_t = now.time()
        if start_t <= end_t:
            return start_t <= now_t <= end_t
        # Overnight window
        return now_t >= start_t or now_t <= end_t
    return False


def active_now() -> list:
    """All windows currently active."""
    now = datetime.datetime.now()
    return [w for w in list_windows(include_past=True)
            if _is_active_now(w, now)]


def is_action_paused(action: str) -> bool:
    """Is `action` currently paused by ANY active window?"""
    for w in active_now():
        paused = w.get("actions_paused") or []
        if action in paused or "all" in paused:
            return True
    return False


# ─── State transitions (call from bg_scheduler) ────────────────────────

_LAST_KNOWN_ACTIVE: set = set()


def detect_transitions() -> dict:
    """Find windows that just started or just ended since last check.
    Fires webhooks. Returns {started: [...], ended: [...]}.
    bg_scheduler should call this once a minute."""
    global _LAST_KNOWN_ACTIVE
    now_active = {w["id"] for w in active_now()}
    started = now_active - _LAST_KNOWN_ACTIVE
    ended = _LAST_KNOWN_ACTIVE - now_active
    _LAST_KNOWN_ACTIVE = now_active
    out = {"started": [], "ended": []}
    if started:
        windows = list_windows(include_past=True)
        for wid in started:
            w = next((x for x in windows if x["id"] == wid), None)
            if w:
                _record_event(wid, "started", w.get("label", ""))
                _fire_webhook("maintenance.start", w)
                out["started"].append(w)
    if ended:
        windows = list_windows(include_past=True)
        for wid in ended:
            w = next((x for x in windows if x["id"] == wid), None)
            if w:
                _record_event(wid, "ended", w.get("label", ""))
                _fire_webhook("maintenance.end", w)
                out["ended"].append(w)
    return out


def _record_event(wid: int, kind: str, message: str):
    _ensure_tables()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""INSERT INTO maintenance_events(
                ts, window_id, kind, message
            ) VALUES (?,?,?,?)""", (time.time(), wid, kind, message[:200]))
    except Exception:
        pass


def _fire_webhook(event_name: str, window: dict):
    try:
        from . import webhooks as _wh
        _wh.fire(event_name, {
            "window_id": window.get("id"),
            "label": window.get("label"),
            "actions_paused": window.get("actions_paused"),
            "start_iso": window.get("start_iso"),
            "end_iso": window.get("end_iso"),
        })
    except Exception:
        pass


# ─── T36 / D-122 — immediate maintenance-mode override ─────────────────
#
# "Maintenance mode" as in turn it on NOW, leave it on until I turn it
# off. Implemented as a one-shot window starting now with a 1-year end
# date; labelled with a recognisable tag so the toggle can find and
# remove it. The existing add_window / list_windows / is_action_paused
# pipeline does all the heavy lifting — this is just the operator-
# facing on/off semantics.

_OVERRIDE_LABEL_PREFIX = "_immediate_override_"


def add_window_now(*, actions_paused: Optional[list] = None,
                   note: str = "") -> Optional[int]:
    """Start an immediate maintenance override that lasts until
    end_active_overrides() is called (or one year, whichever sooner).

    `actions_paused` defaults to ["workers"] — matching add_window's
    default and the runner's existing pause-check.

    Returns the new window's id, or None on failure. Idempotent in the
    "operator already enabled override" sense — repeated calls add
    multiple overrides, but end_active_overrides clears them all.
    """
    paused = actions_paused or ["workers"]
    now = datetime.datetime.now()
    # One year is well past any reasonable maintenance duration but
    # still bounded — a runaway override won't last forever even if
    # the operator forgets it.
    end = now + datetime.timedelta(days=365)
    label = _OVERRIDE_LABEL_PREFIX + (note or "manual")[:180]
    return add_window(label=label,
                       start_iso=now.isoformat(timespec="seconds"),
                       end_iso=end.isoformat(timespec="seconds"),
                       rrule="",
                       actions_paused=paused)


def end_active_overrides() -> int:
    """Remove every active immediate-override window. Returns the
    count of windows removed. Real scheduled windows (those without
    the _OVERRIDE_LABEL_PREFIX) are untouched."""
    _ensure_tables()
    removed = 0
    try:
        windows = list_windows(include_past=False)
    except Exception:
        return 0
    for w in windows:
        label = w.get("label") or ""
        if not label.startswith(_OVERRIDE_LABEL_PREFIX):
            continue
        if remove_window(w["id"]):
            removed += 1
    return removed


def list_active_overrides() -> list:
    """Read-only — list active windows that were created via
    add_window_now (i.e. carry the _OVERRIDE_LABEL_PREFIX). For
    operator UI + the T36 inspector."""
    try:
        active = active_now()
    except Exception:
        return []
    return [w for w in active
            if (w.get("label") or "").startswith(_OVERRIDE_LABEL_PREFIX)]

