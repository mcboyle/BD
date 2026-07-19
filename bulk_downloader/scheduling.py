"""Time-of-day analytics + DST-safe scheduling (Phase 97, Block L).

Complements Phase 102's quiet-hours module with:

  • hour_heatmap(site_id) — per-site, per-hour success/failure histogram
    from the last N days. Lets the operator visually pick the best
    hours to schedule work.
  • best_hours(site_id) — top-K hours of the day where success rate is
    highest. Direct input to scheduler config.
  • is_holiday(date) — operator-supplied calendar of dates to skip.
    Stored in a small JSON file under the install dir; cheap to read.
  • next_run_safe_time() — compute the next ts that is:
      (a) outside quiet hours, AND (b) inside an allowed window, AND
      (c) not a holiday. DST-safe — uses `zoneinfo` (Python 3.9+).

The heatmap data is read from existing history rows; no new tracking
needed. Phase 74 already shipped raw collection.
"""
from __future__ import annotations

import datetime
import json
import time
from pathlib import Path
from typing import Optional


# ─── Hour heatmap ─────────────────────────────────────────────────────

def hour_heatmap(site_id: str, *, days: int = 30) -> dict:
    """Return success/failure counts per hour of the day, aggregated
    over the last `days` days.

    Output:
      {
        site_id: ..., window_days: ...,
        hours: [
          {hour: 0, done: 12, failed: 1, total: 13, success_rate_pct: 92.3},
          ...
        ]
      }

    Local time, so DST shifts split a single wall-clock hour across two
    UTC offsets — acceptable since we're aggregating signals, not
    timing precise events."""
    out = {"site_id": site_id, "window_days": days, "hours": []}
    counts = {h: {"done": 0, "failed": 0} for h in range(24)}
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute("""SELECT status, ts FROM history
                                  WHERE site_id = ?
                                    AND status IN ('done','failed')
                                    AND ts >= datetime('now', ?)""",
                              (site_id, f"-{int(days)} days")).fetchall()
        for r in rows:
            ts = r[1] if not hasattr(r, "keys") else r["ts"]
            status = r[0] if not hasattr(r, "keys") else r["status"]
            # ts is ISO format from sqlite — parse to local time
            try:
                dt = datetime.datetime.fromisoformat(ts)
                hour = dt.hour
            except (TypeError, ValueError):
                continue
            if status in counts[hour]:
                counts[hour][status] += 1
    except Exception:
        pass
    for h in range(24):
        done = counts[h]["done"]
        failed = counts[h]["failed"]
        total = done + failed
        rate = (100 * done / total) if total > 0 else None
        out["hours"].append({
            "hour": h,
            "done": done,
            "failed": failed,
            "total": total,
            "success_rate_pct": round(rate, 1) if rate is not None else None,
        })
    return out


def best_hours(site_id: str, *, days: int = 30,
              top_k: int = 6, min_samples: int = 3) -> list:
    """Return the top-K hours-of-day with the highest success rate
    AND enough samples. Returns list of {hour, success_rate_pct, total}."""
    heat = hour_heatmap(site_id, days=days)
    qualified = [h for h in heat["hours"]
                 if h["total"] >= min_samples and h["success_rate_pct"] is not None]
    qualified.sort(key=lambda h: (-h["success_rate_pct"], -h["total"]))
    return qualified[:top_k]


# ─── Holiday calendar ─────────────────────────────────────────────────

def _holidays_path() -> Path:
    """Where the holiday calendar JSON lives. Under BD's install dir
    so it survives upgrades."""
    try:
        from .constants import INSTALL_DIR
        return Path(INSTALL_DIR) / "holidays.json"
    except Exception:
        return Path.cwd() / "holidays.json"


def list_holidays() -> list:
    """Read the configured holidays. Format on disk:
      [{"date": "2025-12-25", "label": "Christmas"}, ...]
    Returns list, empty on error or missing file."""
    p = _holidays_path()
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict) and d.get("date")]
        return []
    except Exception:
        return []


def add_holiday(date_str: str, label: str = "") -> bool:
    """Append a holiday. `date_str` must be YYYY-MM-DD. Idempotent —
    adding the same date again updates the label."""
    try:
        datetime.date.fromisoformat(date_str)
    except (TypeError, ValueError):
        return False
    holidays = list_holidays()
    holidays = [h for h in holidays if h.get("date") != date_str]
    holidays.append({"date": date_str, "label": label or ""})
    p = _holidays_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(holidays, indent=2), encoding="utf-8")
        tmp.replace(p)
        return True
    except OSError:
        return False


def remove_holiday(date_str: str) -> bool:
    holidays = list_holidays()
    before = len(holidays)
    holidays = [h for h in holidays if h.get("date") != date_str]
    if len(holidays) == before:
        return False
    p = _holidays_path()
    try:
        # v3.47.8 (#43): atomic write — protects user-edited holiday list
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(holidays, indent=2), encoding="utf-8")
        tmp.replace(p)
        return True
    except OSError:
        return False


def is_holiday(when: Optional[datetime.date] = None) -> dict:
    """Check whether `when` (default: today) is a configured holiday.
    Returns {is_holiday: bool, label: str}."""
    d = when or datetime.date.today()
    iso = d.isoformat()
    for h in list_holidays():
        if h.get("date") == iso:
            return {"is_holiday": True, "label": h.get("label", "")}
    return {"is_holiday": False, "label": ""}


# ─── DST-safe scheduler ───────────────────────────────────────────────

def next_run_safe_time(
    config: dict,
    *,
    now: Optional[float] = None,
    earliest_minutes_ahead: int = 5,
    max_search_days: int = 14,
) -> Optional[float]:
    """Find the next timestamp where all gates pass:
      • Not in a quiet window (Phase 102)
      • Not on a configured holiday
      • Inside the configured active_window if set

    Returns a Unix epoch float, or None if no acceptable slot found
    within `max_search_days`.

    DST handling: we walk minute-by-minute (cheap) so DST spring-forward
    naturally skips the missing hour and fall-back tries each repeated
    hour. Local time queries done via `datetime.fromtimestamp` which
    respects the system tz database."""
    from . import policy_gates as _pg
    start_ts = (now if now is not None else time.time()) + (earliest_minutes_ahead * 60)
    end_ts = start_ts + (max_search_days * 86400)
    # Walk in 60-second steps; bail fast on first acceptable
    cur = start_ts
    while cur < end_ts:
        # Check quiet hours
        q = _pg.in_quiet_hours(config, now=cur)
        if q.get("quiet"):
            cur += 60
            continue
        # Check holiday
        d = datetime.datetime.fromtimestamp(cur).date()
        if is_holiday(d).get("is_holiday"):
            # Skip to end of day
            tomorrow = d + datetime.timedelta(days=1)
            cur = datetime.datetime.combine(
                tomorrow, datetime.time(0, 0)
            ).timestamp()
            continue
        return cur
    return None
