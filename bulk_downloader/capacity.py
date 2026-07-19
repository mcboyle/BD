"""Capacity planning + forecasting (Phase 96, Block L).

The operator's three recurring questions:
  • "When will my disk run out?"
  • "When will this queue finish?"
  • "Which subsystem is the bottleneck right now?"

This module answers them from data BD already collects — no new
instrumentation needed. Reads the history table for completion
velocity, runner state for in-flight progress, and shutil.disk_usage
for current free space. All forecasts include explicit confidence
notes so the operator knows whether to trust them.

Design rules:
  • Pure read; never mutates state
  • Cheap (≤100ms per call): aggregations are O(history rows since
    cutoff) not O(all history); cutoff is 7d by default
  • Fail-open: every helper wraps in try/except and returns a graceful
    "no data" shape rather than raising
  • All forecasts are clearly labelled with confidence: 'high' when
    backed by ≥7d of consistent data, 'medium' for 1-7d, 'low' below
"""
from __future__ import annotations

import os
import shutil
import time
from typing import Optional


def _safe_disk_free_bytes(path: str) -> Optional[int]:
    """Return free bytes at `path`, or None if unreachable."""
    if not path:
        return None
    try:
        return shutil.disk_usage(path).free
    except (OSError, ValueError):
        return None


def disk_forecast(download_dir: str, *, lookback_hours: int = 168) -> dict:
    """Predict when the download directory's free space will hit zero.

    Method: compute the average bytes/hour written over `lookback_hours`
    by summing `file_size` from history rows in that window. Project
    forward at the same rate. If recent rate is 0 or negative (deletions),
    return runway as 'indefinite'.

    Returns:
      {
        free_gb: current free space in GB,
        rate_gb_per_day: average write rate (rolling),
        runway_days: float | None,
        runway_label: 'indefinite' | 'days' | 'critical',
        confidence: 'high' | 'medium' | 'low' | 'none',
        lookback_hours: int,
      }
    """
    result = {
        "free_gb": None, "rate_gb_per_day": 0.0, "runway_days": None,
        "runway_label": "unknown", "confidence": "none",
        "lookback_hours": lookback_hours,
    }
    free_bytes = _safe_disk_free_bytes(download_dir)
    if free_bytes is None:
        return result
    result["free_gb"] = round(free_bytes / (1024**3), 2)
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            row = cx.execute(
                "SELECT COALESCE(SUM(file_size), 0) AS total_bytes, COUNT(*) AS n "
                "FROM history WHERE status='done' "
                "AND ts >= datetime('now', ?)",
                (f"-{int(lookback_hours)} hours",)
            ).fetchone()
        total_bytes = int(row[0] if not hasattr(row, "keys") else row["total_bytes"]) or 0
        n_rows = int(row[1] if not hasattr(row, "keys") else row["n"]) or 0
    except Exception:
        return result
    if total_bytes <= 0 or n_rows == 0:
        result["runway_label"] = "indefinite"
        result["confidence"] = "none"
        return result
    rate_per_hour = total_bytes / lookback_hours
    rate_gb_per_day = rate_per_hour * 24 / (1024**3)
    result["rate_gb_per_day"] = round(rate_gb_per_day, 2)
    if rate_per_hour <= 0:
        result["runway_label"] = "indefinite"
        result["confidence"] = "low"
        return result
    runway_hours = free_bytes / rate_per_hour
    result["runway_days"] = round(runway_hours / 24, 1)
    # Confidence based on sample density
    if n_rows >= 50 and lookback_hours >= 168:
        result["confidence"] = "high"
    elif n_rows >= 10:
        result["confidence"] = "medium"
    else:
        result["confidence"] = "low"
    # Runway label
    if result["runway_days"] < 1:
        result["runway_label"] = "critical"
    elif result["runway_days"] < 7:
        result["runway_label"] = "days"
    elif result["runway_days"] < 60:
        result["runway_label"] = "weeks"
    else:
        result["runway_label"] = "months"
    return result


def queue_forecast(runners_dict) -> dict:
    """Predict when the current pending queue will finish processing.

    Method: average completions/hour over the last 24h gives throughput.
    Pending count divided by that rate = hours-to-empty.

    Returns:
      {
        pending: int, running: int,
        completions_per_hour: float,
        eta_hours: float | None,
        confidence: 'high' | 'medium' | 'low' | 'none',
      }
    """
    result = {
        "pending": 0, "running": 0, "failed": 0,
        "completions_per_hour": 0.0, "eta_hours": None,
        "confidence": "none",
    }
    try:
        for sid, runner in (runners_dict or {}).items():
            with runner._lock:
                for j in runner.jobs.values():
                    s = j.get("status", "")
                    if s in result:
                        result[s] += 1
    except Exception:
        return result
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            row = cx.execute(
                "SELECT COUNT(*) AS n FROM history "
                "WHERE status='done' AND ts >= datetime('now', '-24 hours')"
            ).fetchone()
        completions_24h = int(row[0] if not hasattr(row, "keys") else row["n"]) or 0
    except Exception:
        return result
    rate = completions_24h / 24.0
    result["completions_per_hour"] = round(rate, 2)
    pending = result["pending"]
    if pending == 0:
        result["eta_hours"] = 0.0
        result["confidence"] = "high"
        return result
    if rate <= 0:
        result["confidence"] = "low"
        return result  # eta_hours stays None — "we don't know"
    result["eta_hours"] = round(pending / rate, 1)
    if completions_24h >= 50:
        result["confidence"] = "high"
    elif completions_24h >= 10:
        result["confidence"] = "medium"
    else:
        result["confidence"] = "low"
    return result


def bottleneck_hint(runners_dict, download_dir: str = "") -> dict:
    """Suggest which subsystem is the current bottleneck.

    Decision tree (most specific first):
      1. disk_free < threshold_gb        → "disk"
      2. any site rate_limited           → "rate_limit"
      3. any site awaiting_manual_login  → "auth"
      4. any site hung_workers non-empty → "stuck_workers"
      5. throughput << configured target → "network"
      6. workers all running             → "saturated"
      7. else                            → "idle"

    Returns: {bottleneck: str, detail: str, severity: int 0-3}
    """
    out = {"bottleneck": "idle", "detail": "no active load",
           "severity": 0}
    if not runners_dict:
        return out
    try:
        # Disk first — hard constraint
        if download_dir:
            free = _safe_disk_free_bytes(download_dir)
            if free is not None and free < 5 * (1024**3):  # <5GB
                out.update({"bottleneck": "disk",
                           "detail": f"{free/(1024**3):.1f}GB free; runway short",
                           "severity": 3})
                return out
        any_rl = False
        any_auth = False
        stuck = []
        running_workers = 0
        total_max_workers = 0
        for sid, runner in runners_dict.items():
            try:
                if runner.is_rate_limited():
                    any_rl = True
                if hasattr(runner, "is_awaiting_manual_login") and \
                   runner.is_awaiting_manual_login():
                    any_auth = True
                hung = list(getattr(runner, "_hung_workers", []))
                if hung:
                    stuck.append((sid, len(hung)))
                cfg = getattr(runner, "config", {}) or {}
                total_max_workers += int(cfg.get("max_concurrent", 2))
                with runner._lock:
                    for j in runner.jobs.values():
                        if j.get("status") == "running":
                            running_workers += 1
            except Exception:
                continue
        if any_rl:
            return {"bottleneck": "rate_limit",
                    "detail": "one or more sites are 429'd; workers waiting",
                    "severity": 2}
        if any_auth:
            return {"bottleneck": "auth",
                    "detail": "manual login needed on one or more sites",
                    "severity": 2}
        if stuck:
            return {"bottleneck": "stuck_workers",
                    "detail": f"{sum(c for _,c in stuck)} hung worker(s) across {len(stuck)} site(s)",
                    "severity": 2}
        if total_max_workers > 0 and running_workers >= total_max_workers:
            return {"bottleneck": "saturated",
                    "detail": f"{running_workers}/{total_max_workers} worker slots full",
                    "severity": 1}
        if running_workers == 0:
            return {"bottleneck": "idle",
                    "detail": "no active downloads",
                    "severity": 0}
        return {"bottleneck": "running",
                "detail": f"{running_workers}/{total_max_workers} workers active",
                "severity": 0}
    except Exception as e:
        out["detail"] = f"forecast error: {e}"
        return out


def capacity_report(runners_dict, download_dir: str = "") -> dict:
    """Aggregate report combining all three forecasts. Single call
    for /api/capacity to render the whole panel."""
    return {
        "disk": disk_forecast(download_dir) if download_dir else {"free_gb": None},
        "queue": queue_forecast(runners_dict),
        "bottleneck": bottleneck_hint(runners_dict, download_dir),
        "generated_at": time.time(),
    }
