"""Selector drift detection (Phase 198).

Sites refactor their HTML. Templates that worked yesterday return
"0 elements matched" today. Right now BD just fails the download +
moves on; the operator finds out by noticing the queue isn't moving.

This module tracks per-site "0 elements matched" events for the
configured `dl_selector` and surfaces sustained drift as a flag the
Review tab can show: "Template stale: 5 consecutive 0-match failures
in the last 7 days. Click to re-teach."

Threshold tuning:
  • 5 failures = high signal — single-page anomalies / login walls don't
    accumulate to 5 in 7d unless there's real breakage.
  • 7d window = forgive transient blips. Wider would be lenient; we'd
    miss legit refactors. Narrower would flag too eagerly.

Recording is fail-silent (a flaky DB write shouldn't break a download).
Reset is automatic: when a successful match comes in for the same
selector, the failure counter resets — drift "heals" itself when the
operator re-teaches.
"""
from __future__ import annotations

import time
from typing import Optional

from . import db as _db


_TABLE_READY = False
_THRESHOLD = 5             # consecutive failures to flag as stale
_WINDOW_S = 7 * 86400      # 7-day window


def _ensure_table():
    global _TABLE_READY
    if _TABLE_READY:
        return
    try:
        with _db.db_conn() as cx:
            cx.execute("""
                CREATE TABLE IF NOT EXISTS selector_drift (
                    site_id TEXT PRIMARY KEY,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    last_failure_ts REAL,
                    last_success_ts REAL,
                    last_selector TEXT DEFAULT '',
                    last_url TEXT DEFAULT '',
                    flagged_stale INTEGER NOT NULL DEFAULT 0
                )
            """)
        _TABLE_READY = True
    except Exception:
        pass


def record_zero_match(site_id: str, selector: str, url: str):
    """Called by the extractor when a `dl_selector` matched 0 elements
    on a page that's expected to have downloadable content.

    Increments the consecutive-failures counter. If we hit threshold
    within the window, flags the site as stale. Idempotent."""
    if not site_id:
        return
    _ensure_table()
    now = time.time()
    try:
        with _db.db_conn() as cx:
            row = cx.execute("""
                SELECT consecutive_failures, last_failure_ts
                FROM selector_drift WHERE site_id = ?
            """, (site_id,)).fetchone()
            if row:
                # Has the window expired?
                last_ts = row["last_failure_ts"] or 0
                if (now - last_ts) > _WINDOW_S:
                    # Counter is stale; reset and start fresh at 1
                    new_count = 1
                else:
                    new_count = (row["consecutive_failures"] or 0) + 1
            else:
                new_count = 1
            stale = 1 if new_count >= _THRESHOLD else 0
            cx.execute("""
                INSERT INTO selector_drift
                (site_id, consecutive_failures, last_failure_ts,
                 last_selector, last_url, flagged_stale)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(site_id) DO UPDATE SET
                    consecutive_failures = excluded.consecutive_failures,
                    last_failure_ts = excluded.last_failure_ts,
                    last_selector = excluded.last_selector,
                    last_url = excluded.last_url,
                    flagged_stale = excluded.flagged_stale
            """, (site_id, new_count, now,
                  (selector or "")[:200], (url or "")[:500],
                  stale))
    except Exception:
        pass


def record_success(site_id: str):
    """A download succeeded — reset the failure counter. The site's
    template is working again (operator may have just re-taught, or
    the failures were noise)."""
    if not site_id:
        return
    _ensure_table()
    try:
        with _db.db_conn() as cx:
            cx.execute("""
                INSERT INTO selector_drift
                (site_id, consecutive_failures, last_success_ts,
                 flagged_stale)
                VALUES (?, 0, ?, 0)
                ON CONFLICT(site_id) DO UPDATE SET
                    consecutive_failures = 0,
                    last_success_ts = excluded.last_success_ts,
                    flagged_stale = 0
            """, (site_id, time.time()))
    except Exception:
        pass


def is_stale(site_id: str) -> bool:
    """Quick check for the routing logic — should we warn the
    operator before kicking off another download?"""
    _ensure_table()
    try:
        with _db.db_conn() as cx:
            row = cx.execute(
                "SELECT flagged_stale FROM selector_drift WHERE site_id = ?",
                (site_id,)).fetchone()
        return bool(row and row["flagged_stale"])
    except Exception:
        return False


def status_for(site_id: str) -> dict:
    """Full state for one site."""
    _ensure_table()
    try:
        with _db.db_conn() as cx:
            row = cx.execute("""
                SELECT consecutive_failures, last_failure_ts,
                       last_success_ts, last_selector, last_url,
                       flagged_stale
                FROM selector_drift WHERE site_id = ?
            """, (site_id,)).fetchone()
        if not row:
            return {"site_id": site_id, "consecutive_failures": 0,
                    "flagged_stale": False, "last_failure_ts": None,
                    "last_success_ts": None, "last_selector": "",
                    "last_url": ""}
        return {
            "site_id": site_id,
            "consecutive_failures": row["consecutive_failures"] or 0,
            "last_failure_ts": row["last_failure_ts"],
            "last_success_ts": row["last_success_ts"],
            "last_selector": row["last_selector"] or "",
            "last_url": row["last_url"] or "",
            "flagged_stale": bool(row["flagged_stale"]),
        }
    except Exception:
        return {"site_id": site_id, "consecutive_failures": 0,
                "flagged_stale": False}


def status_all() -> list:
    """Snapshot of every site's drift state. Stale-flagged sites first."""
    _ensure_table()
    try:
        with _db.db_conn() as cx:
            rs = cx.execute("""
                SELECT site_id, consecutive_failures, last_failure_ts,
                       last_success_ts, last_selector, last_url,
                       flagged_stale
                FROM selector_drift
                ORDER BY flagged_stale DESC, consecutive_failures DESC
            """).fetchall()
        return [{
            "site_id": r["site_id"],
            "consecutive_failures": r["consecutive_failures"] or 0,
            "last_failure_ts": r["last_failure_ts"],
            "last_success_ts": r["last_success_ts"],
            "last_selector": r["last_selector"] or "",
            "last_url": r["last_url"] or "",
            "flagged_stale": bool(r["flagged_stale"]),
        } for r in rs]
    except Exception:
        return []


def reset(site_id: str) -> bool:
    """Clear the drift state for one site. Used after a re-teach."""
    _ensure_table()
    try:
        with _db.db_conn() as cx:
            cur = cx.execute(
                "DELETE FROM selector_drift WHERE site_id = ?",
                (site_id,))
        return (cur.rowcount or 0) > 0
    except Exception:
        return False
