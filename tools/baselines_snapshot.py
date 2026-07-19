#!/usr/bin/env python3
"""Baselines snapshot — pre-F1 measurement (Track F1 wave gate).

Captures a timestamped JSON snapshot of the metrics F1-A (and the wider F1
wave) are meant to move, so "improved" is measurable rather than asserted.
Run it ONCE before deploying the F1 cut to record the before-state, then
again after soak; diff the two snapshots.

Metrics (per the Track-F execution order):
  1. hourly_stats / site          — history rows bucketed by site x hour-of-day
  2. 7-day heartbeat-fail          — session_history auth-failure events / site
                                     (the signal F1.3 cookie-expiry admission targets)
  3. 7-day dup-URL fetch           — URLs fetched more than once (wasted work;
                                     the signal F1.5 dedup targets)
  4. idle-tab request rate         — NOT PERSISTED. Emitted as an explicit
                                     not-instrumented stub rather than fabricated;
                                     needs runtime per-request-rate logging (future).

stdlib-only (sqlite3 / json / argparse) — runs under the stash system python3,
no venv needed. Read-only: opens the DB and never writes to it.

Usage:
  python3 tools/baselines_snapshot.py                      # live DB -> stdout
  python3 tools/baselines_snapshot.py --out baselines.json # write a snapshot file
  python3 tools/baselines_snapshot.py --db /path/to.db --window-days 7
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

# Auth-failure event types in session_history that represent a session that
# lapsed under load — exactly what F1.3 holds before, instead of after.
AUTH_FAIL_EVENTS = ("heartbeat_fail", "auto_relogin_fail", "needs_takeover")

# history.ts is an ISO string 'YYYY-MM-DDTHH:MM:SS' (local-naive, written by
# SQLite strftime 'now'); session_history.ts is a REAL epoch seconds.


def _iso_cutoff(now_epoch: float, window_days: int) -> str:
    """ISO cutoff string matching history.ts format."""
    cut = now_epoch - window_days * 86400
    return datetime.fromtimestamp(cut).strftime("%Y-%m-%dT%H:%M:%S")


def _hourly_stats_per_site(cx: sqlite3.Connection, iso_cut: str) -> dict:
    """history rows since cutoff, grouped by site x hour-of-day, counted by
    status. Returns {site_id: {"by_hour": {HH: {status: n}}, "total": n}}."""
    rows = cx.execute(
        "SELECT site_id, status, ts FROM history WHERE ts >= ?",
        (iso_cut,),
    ).fetchall()
    out: dict = defaultdict(lambda: {"by_hour": defaultdict(lambda: defaultdict(int)),
                                     "total": 0})
    for site_id, status, ts in rows:
        site_id = site_id or "(none)"
        status = status or "(none)"
        hh = "??"
        if isinstance(ts, str) and "T" in ts:
            hh = ts.split("T", 1)[1][:2]
        out[site_id]["by_hour"][hh][status] += 1
        out[site_id]["total"] += 1
    # Convert defaultdicts to plain dicts for JSON.
    return {
        sid: {
            "total": d["total"],
            "by_hour": {hh: dict(stat) for hh, stat in d["by_hour"].items()},
        }
        for sid, d in out.items()
    }


def _heartbeat_fail_7d(cx: sqlite3.Connection, epoch_cut: float) -> dict:
    """session_history auth-failure events since cutoff, per site + per type."""
    placeholders = ",".join("?" * len(AUTH_FAIL_EVENTS))
    rows = cx.execute(
        f"SELECT site_id, event_type FROM session_history "
        f"WHERE ts >= ? AND event_type IN ({placeholders})",
        (epoch_cut, *AUTH_FAIL_EVENTS),
    ).fetchall()
    per_site: dict = defaultdict(lambda: defaultdict(int))
    total = 0
    for site_id, event_type in rows:
        per_site[site_id or "(none)"][event_type] += 1
        total += 1
    return {
        "total": total,
        "per_site": {sid: dict(ev) for sid, ev in per_site.items()},
        "note": ("auth-lapse events (heartbeat_fail / auto_relogin_fail / "
                 "needs_takeover); these only fire while a session is being "
                 "monitored -> a proxy for 'fail-during-active'."),
    }


def _dup_url_fetch_7d(cx: sqlite3.Connection, iso_cut: str) -> dict:
    """URLs in history fetched more than once since cutoff = redundant work."""
    rows = cx.execute(
        "SELECT url, COUNT(*) c FROM history WHERE ts >= ? AND url IS NOT NULL "
        "GROUP BY url HAVING c > 1",
        (iso_cut,),
    ).fetchall()
    dup_urls = len(rows)
    redundant_fetches = sum(c - 1 for _u, c in rows)
    return {
        "dup_urls": dup_urls,
        "redundant_fetches": redundant_fetches,
        "note": "URLs appearing >1x; redundant_fetches = total extra pulls beyond the first.",
    }


def _idle_tab_request_rate() -> dict:
    """Not derivable from the DB — no per-request-rate persistence exists."""
    return {
        "available": False,
        "reason": ("idle-tab request rate is a runtime metric with no DB "
                   "persistence today; capturing it needs per-request-rate "
                   "instrumentation (future F1 work). Emitted as a stub so the "
                   "snapshot shape is stable across the before/after diff."),
    }


def compute_baselines(cx: sqlite3.Connection,
                      *,
                      now_epoch: float | None = None,
                      window_days: int = 7) -> dict:
    """Pure-ish core: given an open connection, compute the snapshot dict.
    Each metric is independently fail-soft — a missing table yields an
    {'error': ...} entry rather than aborting the whole snapshot."""
    if now_epoch is None:
        now_epoch = time.time()
    iso_cut = _iso_cutoff(now_epoch, window_days)
    epoch_cut = now_epoch - window_days * 86400

    def _safe(fn):
        try:
            return fn()
        except sqlite3.Error as e:
            return {"error": str(e)}

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_days": window_days,
        "metrics": {
            "hourly_stats_per_site": _safe(lambda: _hourly_stats_per_site(cx, iso_cut)),
            "heartbeat_fail_7d": _safe(lambda: _heartbeat_fail_7d(cx, epoch_cut)),
            "dup_url_fetch_7d": _safe(lambda: _dup_url_fetch_7d(cx, iso_cut)),
            "idle_tab_request_rate": _idle_tab_request_rate(),
        },
    }


def _resolve_live_db() -> str:
    """Reuse the package's own DB-path resolution so we read the SAME db the
    live app uses (BD_HOME / install-dir aware)."""
    from bulk_downloader.db import _resolve_db_path
    return _resolve_db_path()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Pre-F1 baselines snapshot (read-only).")
    ap.add_argument("--db", default=None,
                    help="path to the sqlite DB (default: the live BD DB)")
    ap.add_argument("--window-days", type=int, default=7)
    ap.add_argument("--out", default=None,
                    help="write JSON snapshot here (default: stdout)")
    args = ap.parse_args(argv)

    db_path = args.db or _resolve_live_db()
    try:
        cx = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
    except sqlite3.Error:
        # Fall back to a normal (still read-only-by-intent) open if the URI
        # ro mode isn't supported for some reason.
        cx = sqlite3.connect(db_path, timeout=10.0)
    try:
        snap = compute_baselines(cx, window_days=args.window_days)
    finally:
        cx.close()
    snap["db_path"] = db_path

    blob = json.dumps(snap, indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(blob + "\n")
        # Human one-liner to stderr so stdout stays clean for piping.
        m = snap["metrics"]
        hs = m["hourly_stats_per_site"]
        sys.stderr.write(
            f"baselines: {len(hs) if isinstance(hs, dict) else '?'} site(s) | "
            f"auth-fail(7d)={m['heartbeat_fail_7d'].get('total', '?')} | "
            f"dup-urls(7d)={m['dup_url_fetch_7d'].get('dup_urls', '?')} -> {args.out}\n")
    else:
        print(blob)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
