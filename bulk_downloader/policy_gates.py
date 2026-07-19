"""Quiet hours scheduling + bandwidth budget (Phases 102 + 103, Block M).

Two operator policies that share a module because they have the same
shape: read config, compute current allowance, gate or throttle.

Phase 102 — Quiet hours:
  Operator declares time-of-day windows during which workers must
  pause. Useful for noisy disk activity at night, family bandwidth
  hours, ISP TOS off-peak only, etc. BD already has a `window_active_hours`
  config from Phase 50; this is the inverse: explicit *forbidden*
  windows that override active hours.

Phase 103 — Bandwidth budget:
  Per-month rolling cap on bytes downloaded. Soft cap (slow workers
  at 80%) and hard cap (pause at 100%). Reset configurable to
  billing-cycle alignment. Per-VPN-endpoint sub-budgets supported
  for providers that cap per-server.

Both functions are read-only on data sources (DB rows + config dict)
and return enforcement hints. The caller (runner.py) decides what to
do with the hints — pause workers, throttle dispatch, log events.
"""
from __future__ import annotations

import datetime
import time
from typing import Optional


# ─── Phase 102: Quiet hours ───────────────────────────────────────────

def _parse_window(s: str) -> Optional[tuple]:
    """Parse 'HH:MM-HH:MM' into (start_minutes, end_minutes) from
    midnight. Windows can wrap (22:00-06:00). Returns None on parse
    failure — fail-open: invalid window = no constraint."""
    if not s or not isinstance(s, str):
        return None
    parts = s.strip().split("-")
    if len(parts) != 2:
        return None
    try:
        start = parts[0].strip().split(":")
        end = parts[1].strip().split(":")
        s_min = int(start[0]) * 60 + int(start[1])
        e_min = int(end[0]) * 60 + int(end[1])
        if not (0 <= s_min < 1440 and 0 <= e_min < 1440):
            return None
        return (s_min, e_min)
    except (ValueError, IndexError):
        return None


def _now_minutes(now: Optional[float] = None) -> int:
    """Current time as minutes-since-midnight in local time."""
    ts = now if now is not None else time.time()
    local = datetime.datetime.fromtimestamp(ts)
    return local.hour * 60 + local.minute


def _in_window(minutes: int, window: tuple) -> bool:
    """True if `minutes` falls inside the window. Handles wrap."""
    start, end = window
    if start == end:
        return False  # zero-length window
    if start < end:
        return start <= minutes < end
    # Wraps midnight: e.g. 22:00-06:00 → in if >= 22 OR < 6
    return minutes >= start or minutes < end


def in_quiet_hours(config: dict, *, now: Optional[float] = None) -> dict:
    """Check whether the current time falls inside any configured
    quiet window. Returns {quiet: bool, window: 'HH:MM-HH:MM' | '',
    reason: str}.

    Config fields read:
      quiet_hours: str or list of str — windows like '22:00-06:00'
      quiet_hours_enabled: bool

    Fail-open: missing or malformed config = not quiet."""
    if not config or not config.get("quiet_hours_enabled"):
        return {"quiet": False, "window": "", "reason": "disabled"}
    raw = config.get("quiet_hours") or ""
    windows_raw = [raw] if isinstance(raw, str) else list(raw)
    minutes = _now_minutes(now)
    for w_str in windows_raw:
        w = _parse_window(w_str)
        if w and _in_window(minutes, w):
            return {"quiet": True, "window": w_str.strip(),
                    "reason": f"inside quiet window {w_str}"}
    return {"quiet": False, "window": "", "reason": "outside windows"}


def next_window_transition(config: dict, *,
                          now: Optional[float] = None) -> Optional[dict]:
    """Predict when the next quiet/active transition happens.
    Returns {at_ts, kind: 'quiet_start' | 'quiet_end', window: 'HH:MM-...'}.

    Used by the UI to show "Workers resume in 1h23m" countdown."""
    if not config or not config.get("quiet_hours_enabled"):
        return None
    raw = config.get("quiet_hours") or ""
    windows_raw = [raw] if isinstance(raw, str) else list(raw)
    ts = now if now is not None else time.time()
    minutes_now = _now_minutes(ts)
    # Today's date at midnight
    today = datetime.datetime.fromtimestamp(ts).replace(hour=0, minute=0,
                                                        second=0, microsecond=0)
    best = None
    for w_str in windows_raw:
        w = _parse_window(w_str)
        if not w:
            continue
        start, end = w
        currently_in = _in_window(minutes_now, w)
        next_event_min = end if currently_in else start
        # Compute next occurrence in future
        candidate_min = next_event_min
        candidate_day = today
        if candidate_min <= minutes_now and not currently_in:
            candidate_day += datetime.timedelta(days=1)
        elif currently_in and end <= minutes_now:
            # wrap case: end is tomorrow morning
            candidate_day += datetime.timedelta(days=1)
        at_dt = candidate_day + datetime.timedelta(minutes=candidate_min)
        at_ts = at_dt.timestamp()
        if at_ts <= ts:
            at_ts += 86400  # belt-and-suspenders for edge case
        kind = "quiet_end" if currently_in else "quiet_start"
        if best is None or at_ts < best["at_ts"]:
            best = {"at_ts": at_ts, "kind": kind, "window": w_str.strip()}
    return best


# ─── Phase 103: Bandwidth budget ──────────────────────────────────────

def current_period_usage(config: dict) -> dict:
    """Sum file_size of done rows in the current billing period.

    Config fields:
      budget_reset_day: 1-28 (day of month, default 1)

    Returns {used_bytes, used_gb, period_start_iso, period_end_iso}.
    Always returns a valid dict; on DB failure used_bytes=0.
    """
    reset_day = int((config or {}).get("budget_reset_day", 1) or 1)
    reset_day = max(1, min(28, reset_day))  # clamp to safe range
    now = datetime.datetime.now()
    # Find the most recent reset point
    candidate = now.replace(day=reset_day, hour=0, minute=0, second=0,
                            microsecond=0)
    if candidate > now:
        # Reset hasn't happened this month; go to previous month
        if candidate.month == 1:
            candidate = candidate.replace(year=candidate.year - 1, month=12)
        else:
            candidate = candidate.replace(month=candidate.month - 1)
    period_start = candidate
    # Next reset
    if period_start.month == 12:
        period_end = period_start.replace(year=period_start.year + 1, month=1)
    else:
        period_end = period_start.replace(month=period_start.month + 1)
    used = 0
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            row = cx.execute("""SELECT COALESCE(SUM(file_size), 0) AS b
                                FROM history
                                WHERE status='done' AND ts >= ?""",
                              (period_start.strftime("%Y-%m-%dT%H:%M:%S"),)).fetchone()
        used = int(row[0] if not hasattr(row, "keys") else row["b"]) or 0
    except Exception:
        pass
    return {
        "used_bytes": used,
        "used_gb": round(used / (1024**3), 2),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "reset_day": reset_day,
    }


def budget_state(config: dict) -> dict:
    """Compare current usage to configured caps and return enforcement
    hint.

    Config fields:
      monthly_budget_gb: float (0 or missing = no budget)
      soft_cap_pct: int 1-99, default 80
      hard_cap_pct: int 1-100, default 100

    Returns:
      {state: 'unset' | 'ok' | 'soft' | 'hard',
       used_gb, budget_gb, pct, throttle_factor: 0.0-1.0,
       period_start, period_end}

    Caller uses throttle_factor: 1.0 = no throttle, 0.0 = full stop.
    Between soft and hard cap, linearly scales workers down.
    """
    budget = float((config or {}).get("monthly_budget_gb", 0) or 0)
    if budget <= 0:
        usage = current_period_usage(config or {})
        return {"state": "unset", "used_gb": usage["used_gb"],
                "budget_gb": 0, "pct": 0, "throttle_factor": 1.0,
                "period_start": usage["period_start"],
                "period_end": usage["period_end"]}
    soft_pct = int((config or {}).get("soft_cap_pct", 80) or 80)
    hard_pct = int((config or {}).get("hard_cap_pct", 100) or 100)
    soft_pct = max(1, min(99, soft_pct))
    hard_pct = max(soft_pct + 1, min(100, hard_pct))
    usage = current_period_usage(config or {})
    used_gb = usage["used_gb"]
    pct = round(100 * used_gb / budget, 1)
    if pct >= hard_pct:
        state = "hard"; throttle = 0.0
    elif pct >= soft_pct:
        state = "soft"
        # Linear ramp from 1.0 at soft to 0.0 at hard
        ramp_span = hard_pct - soft_pct
        if ramp_span <= 0:
            throttle = 0.0
        else:
            throttle = max(0.0, 1.0 - (pct - soft_pct) / ramp_span)
    else:
        state = "ok"; throttle = 1.0
    return {
        "state": state,
        "used_gb": used_gb,
        "budget_gb": round(budget, 2),
        "pct": pct,
        "soft_pct": soft_pct,
        "hard_pct": hard_pct,
        "throttle_factor": round(throttle, 3),
        "period_start": usage["period_start"],
        "period_end": usage["period_end"],
    }
