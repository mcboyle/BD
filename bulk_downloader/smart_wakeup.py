"""Smart wakeup (Phase 134, Block Q).

Existing scheduling (Phase 97) enforces hard windows: "only run between
22:00 and 06:00." But that wastes the window when there's nothing in
the queue. This module adds the inverse signal: "wake the workers if
there's work, even outside the window — but only do so opportunistically."

Three knobs:

  • quiet_hours: list of [start, end] HH:MM ranges where BD nominally
    sleeps (e.g. office hours when the VPS shares bandwidth with work)
  • wakeup_threshold: how many pending jobs trigger a wakeup (default 50)
  • wakeup_max_duration: cap on the wakeup burst (default 30 min)
  • cool_down_between_wakeups: minimum gap between wakeups (default 4h)

Records every wakeup decision in `smart_wakeup_log` for audit.

Integration: runner.py's worker loop queries `should_wake_now(s_cfg)`
before respecting the quiet window. When true, the window is ignored
for one wakeup period.
"""
from __future__ import annotations

import datetime
import sys
import time
from typing import Optional


def _ensure_table():
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS smart_wakeup_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                decision TEXT NOT NULL,
                reason TEXT,
                pending_count INTEGER,
                quiet_hours_active INTEGER
            )""")
    except Exception as e:
        sys.stderr.write(f"[smart_wakeup] schema init: {e}\n")


def _is_in_quiet_window(now: datetime.datetime,
                       quiet_hours: list) -> bool:
    """Check if `now` falls within any quiet [start,end] HH:MM range.
    Handles overnight windows (e.g. ['22:00','06:00'])."""
    cur = now.hour * 60 + now.minute
    for w in quiet_hours or []:
        try:
            start_s, end_s = w
            sh, sm = map(int, start_s.split(":"))
            eh, em = map(int, end_s.split(":"))
            start = sh * 60 + sm
            end = eh * 60 + em
            if start <= end:
                if start <= cur < end:
                    return True
            else:
                # Overnight: e.g. 22:00 → 06:00
                if cur >= start or cur < end:
                    return True
        except Exception:
            continue
    return False


def _pending_count() -> int:
    """Total pending (queued + needs_review) across all sites."""
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            row = cx.execute("""SELECT COUNT(*) FROM history
                                 WHERE status IN ('pending', 'needs_review')""").fetchone()
        return int(row[0] or 0) if row else 0
    except Exception:
        return 0


def _last_wakeup_age_seconds() -> Optional[float]:
    """Seconds since last 'wakeup_authorized' decision. None if no
    wakeup ever recorded."""
    _ensure_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            r = cx.execute("""SELECT ts FROM smart_wakeup_log
                              WHERE decision = 'wakeup_authorized'
                              ORDER BY id DESC LIMIT 1""").fetchone()
        if not r:
            return None
        return time.time() - float(r[0])
    except Exception:
        return None


def _record(decision: str, reason: str, pending: int,
            quiet_active: bool):
    _ensure_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""INSERT INTO smart_wakeup_log(
                ts, decision, reason, pending_count, quiet_hours_active
            ) VALUES (?,?,?,?,?)""",
                (time.time(), decision, reason[:300],
                 pending, 1 if quiet_active else 0))
    except Exception:
        pass


def should_wake_now(*, quiet_hours: list = None,
                   wakeup_threshold: int = 50,
                   cool_down_seconds: int = 14400) -> dict:
    """Decision function. Returns {wake, reason, pending, in_quiet,
    last_wakeup_seconds_ago}.

    `wake=True` means: ignore the quiet window and run workers.
    `wake=False` means: respect the quiet window as configured.

    Logic:
      • Outside quiet window → wake=True (nothing to override)
      • Inside quiet window + pending < threshold → wake=False
      • Inside quiet window + pending >= threshold + cooldown elapsed
        → wake=True
      • Inside quiet window + threshold exceeded BUT cooldown active
        → wake=False (we just woke; throttle)
    """
    now = datetime.datetime.now()
    in_quiet = _is_in_quiet_window(now, quiet_hours or [])
    pending = _pending_count()
    last_age = _last_wakeup_age_seconds()
    out = {"wake": False, "reason": "", "pending": pending,
           "in_quiet": in_quiet,
           "last_wakeup_seconds_ago": last_age}
    if not in_quiet:
        out["wake"] = True
        out["reason"] = "outside quiet hours"
        return out
    if pending < wakeup_threshold:
        out["reason"] = f"in quiet hours, pending {pending} < threshold {wakeup_threshold}"
        _record("respecting_quiet", out["reason"], pending, in_quiet)
        return out
    # Pending exceeds threshold inside quiet window
    if last_age is not None and last_age < cool_down_seconds:
        out["reason"] = (f"pending {pending} but cooldown active "
                        f"({last_age:.0f}s < {cool_down_seconds}s)")
        _record("cooldown", out["reason"], pending, in_quiet)
        return out
    # Wake up!
    out["wake"] = True
    out["reason"] = (f"pending {pending} >= {wakeup_threshold} in quiet hours "
                    f"(cooldown elapsed)")
    _record("wakeup_authorized", out["reason"], pending, in_quiet)
    return out


def recent_decisions(limit: int = 50) -> list:
    _ensure_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute("""SELECT * FROM smart_wakeup_log
                                  ORDER BY id DESC LIMIT ?""",
                              (int(limit),)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
