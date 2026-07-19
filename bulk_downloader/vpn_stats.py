"""VPN profile statistics (Phase 142, Block Q).

Per-VPN-endpoint outcome tracking. When BD runs through a VPN
rotation (existing Phase 16 VPN routes module already swaps profiles
mid-queue), it should know which endpoints actually work well for
which sites — Tokyo for site A, Frankfurt for site B, etc.

This module:

  • record_outcome(vpn_profile, site_id, success, *, latency_ms)
    Called from the runner after each job. Cheap.

  • profile_report() — aggregate stats per (profile, site_id) pair:
      attempts, success rate, average latency, last_used, last_failed

  • best_profile_for(site_id) — return the profile with highest
    success rate over the last N attempts for this site

  • blacklist_profile(profile, site_id, *, ttl_hours)
    Temporarily exclude a profile from a site after repeated failures.

Used by the VPN router (existing module) to make smarter selections.
"""
from __future__ import annotations

import sys
import time
from typing import Optional


def _ensure_tables():
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS vpn_outcomes(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                vpn_profile TEXT NOT NULL,
                site_id TEXT NOT NULL,
                success INTEGER NOT NULL,
                latency_ms INTEGER,
                reason TEXT
            )""")
            cx.execute("""CREATE INDEX IF NOT EXISTS idx_vpn_outcomes
                          ON vpn_outcomes(vpn_profile, site_id, ts)""")
            cx.execute("""CREATE TABLE IF NOT EXISTS vpn_blacklist(
                vpn_profile TEXT NOT NULL,
                site_id TEXT NOT NULL,
                blacklisted_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                reason TEXT,
                PRIMARY KEY (vpn_profile, site_id)
            )""")
    except Exception as e:
        sys.stderr.write(f"[vpn_stats] schema init: {e}\n")


def record_outcome(vpn_profile: str, site_id: str,
                  *, success: bool,
                  latency_ms: Optional[int] = None,
                  reason: str = ""):
    """Insert one outcome record. Called by the runner after each job
    completes — should be cheap enough to never block."""
    if not vpn_profile or not site_id:
        return
    _ensure_tables()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""INSERT INTO vpn_outcomes(
                ts, vpn_profile, site_id, success, latency_ms, reason
            ) VALUES (?,?,?,?,?,?)""",
                (time.time(), vpn_profile, site_id,
                 1 if success else 0,
                 int(latency_ms) if latency_ms is not None else None,
                 (reason or "")[:200]))
    except Exception:
        pass


def profile_report(*, since_hours: int = 168) -> list:
    """Per-(profile, site) aggregate stats over the last N hours.

    Returns list of:
      {vpn_profile, site_id, attempts, successes, failures,
       success_rate, avg_latency_ms, last_used, last_failed}"""
    _ensure_tables()
    cutoff = time.time() - (since_hours * 3600)
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute("""SELECT
                vpn_profile,
                site_id,
                COUNT(*) AS attempts,
                SUM(success) AS successes,
                AVG(CASE WHEN latency_ms IS NOT NULL THEN latency_ms END) AS avg_latency,
                MAX(ts) AS last_used,
                MAX(CASE WHEN success = 0 THEN ts END) AS last_failed
                FROM vpn_outcomes
                WHERE ts >= ?
                GROUP BY vpn_profile, site_id
                ORDER BY attempts DESC""", (cutoff,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            att = int(d["attempts"] or 0)
            suc = int(d["successes"] or 0)
            out.append({
                "vpn_profile": d["vpn_profile"],
                "site_id": d["site_id"],
                "attempts": att,
                "successes": suc,
                "failures": att - suc,
                "success_rate": round(100 * suc / max(1, att), 1),
                "avg_latency_ms": round(d["avg_latency"], 1)
                                  if d["avg_latency"] else None,
                "last_used": d["last_used"],
                "last_failed": d["last_failed"],
            })
        return out
    except Exception:
        return []


def best_profile_for(site_id: str, *,
                     candidate_profiles: Optional[list] = None,
                     since_hours: int = 168,
                     min_attempts: int = 5) -> Optional[dict]:
    """Choose the best VPN profile for a site, based on recent
    success rate. Skips blacklisted profiles.

    Returns {vpn_profile, success_rate, attempts} or None if no
    profile has enough data yet."""
    report = profile_report(since_hours=since_hours)
    blacklist = current_blacklist()
    blocked = {(b["vpn_profile"], b["site_id"]) for b in blacklist}
    # Filter to this site + min attempts + not blacklisted
    eligible = [r for r in report
                if r["site_id"] == site_id
                   and r["attempts"] >= min_attempts
                   and (r["vpn_profile"], site_id) not in blocked]
    if candidate_profiles:
        eligible = [r for r in eligible
                    if r["vpn_profile"] in candidate_profiles]
    if not eligible:
        return None
    # Pick highest success rate; tiebreak by attempts (more data is better)
    eligible.sort(key=lambda r: (-r["success_rate"], -r["attempts"]))
    top = eligible[0]
    return {
        "vpn_profile": top["vpn_profile"],
        "success_rate": top["success_rate"],
        "attempts": top["attempts"],
    }


# ─── Blacklist ────────────────────────────────────────────────────────

def blacklist_profile(vpn_profile: str, site_id: str,
                     *, ttl_hours: int = 24, reason: str = ""):
    """Block a profile from being chosen for a site for `ttl_hours`."""
    _ensure_tables()
    now = time.time()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""INSERT INTO vpn_blacklist(
                vpn_profile, site_id, blacklisted_at, expires_at, reason
            ) VALUES (?,?,?,?,?)
            ON CONFLICT(vpn_profile, site_id) DO UPDATE SET
                blacklisted_at = excluded.blacklisted_at,
                expires_at = excluded.expires_at,
                reason = excluded.reason""",
                (vpn_profile, site_id, now,
                 now + ttl_hours * 3600,
                 (reason or "")[:200]))
        return True
    except Exception:
        return False


def current_blacklist() -> list:
    _ensure_tables()
    now = time.time()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            # Sweep expired entries
            cx.execute("DELETE FROM vpn_blacklist WHERE expires_at < ?", (now,))
            rows = cx.execute("""SELECT * FROM vpn_blacklist
                                  ORDER BY expires_at DESC""").fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def auto_blacklist_check(*, fail_threshold: int = 5,
                        window_minutes: int = 30,
                        ttl_hours: int = 4) -> list:
    """Sweep for (profile, site) pairs with >= fail_threshold failures
    in the last `window_minutes`. Auto-blacklist them. Returns the
    list of newly-blacklisted pairs."""
    _ensure_tables()
    cutoff = time.time() - (window_minutes * 60)
    newly = []
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute("""SELECT vpn_profile, site_id, COUNT(*) AS fails
                                  FROM vpn_outcomes
                                  WHERE success = 0 AND ts >= ?
                                  GROUP BY vpn_profile, site_id
                                  HAVING COUNT(*) >= ?""",
                              (cutoff, int(fail_threshold))).fetchall()
        for r in rows:
            d = dict(r)
            if blacklist_profile(d["vpn_profile"], d["site_id"],
                                ttl_hours=ttl_hours,
                                reason=f"{d['fails']} fails in {window_minutes}min"):
                newly.append({"vpn_profile": d["vpn_profile"],
                              "site_id": d["site_id"],
                              "failure_count": d["fails"]})
    except Exception:
        pass
    return newly
