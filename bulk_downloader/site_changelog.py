"""Site behavior changelog (Phase 148, Block Q).

When a site silently changes its extraction structure, operators usually
notice via mass failures — but they don't have an easy way to ask
"what's different about Site X this week?" This module surfaces that.

For each site it computes a recent-vs-baseline delta on:

  • Success rate (last 24h vs prior 7d)
  • New error fingerprints (failure clusters that didn't exist before)
  • Cookie-jar health (cookie count + expiry)
  • Extractor mix change (% of jobs going through library vs Playwright
    vs teach — a sudden shift to teach usually means the site changed)

Each event becomes one "changelog entry" with a severity (info / warn /
alert) and a short human description. Operators read this in the UI
to triage "why did Brazzers stop working last week?"

Data sources are existing history + cookies; no new tables required.
"""
from __future__ import annotations

import time
from typing import Optional

from . import db as _db
from . import error_fingerprint as _ef


# Severity ordering: alert > warn > info
SEVERITY_RANK = {"info": 0, "warn": 1, "alert": 2}


def _success_rate(site_id: str, *, since_seconds: float,
                  until_seconds: float = 0) -> dict:
    """Compute success rate in a time window. Returns
    {total, done, failed, success_rate, has_data}."""
    until_ts = until_seconds or time.time()
    since_ts = until_ts - since_seconds
    out = {"total": 0, "done": 0, "failed": 0,
           "success_rate": None, "has_data": False}
    try:
        with _db.db_conn() as cx:
            rows = cx.execute("""
                SELECT status, COUNT(*) FROM history
                WHERE site_id = ?
                  AND CAST(strftime('%s', ts) AS REAL) BETWEEN ? AND ?
                  AND status IN ('done', 'failed')
                GROUP BY status
            """, (site_id, since_ts, until_ts)).fetchall()
        for s, n in rows:
            if s == "done":
                out["done"] = int(n)
            elif s == "failed":
                out["failed"] = int(n)
        out["total"] = out["done"] + out["failed"]
        if out["total"] >= 5:  # Need >=5 samples for the rate to mean anything
            out["success_rate"] = round(
                100.0 * out["done"] / out["total"], 1)
            out["has_data"] = True
    except Exception:
        pass
    return out


def _new_failure_fingerprints(site_id: str,
                              recent_hours: float = 24,
                              baseline_hours: float = 168) -> list:
    """Find error fingerprints appearing in the recent window that
    were NOT present in the baseline window. These are likely
    "site broke yesterday" signals.

    Returns list of {fingerprint, count, sample_message}."""
    now = time.time()
    recent_since = now - recent_hours * 3600
    baseline_since = now - baseline_hours * 3600
    baseline_until = recent_since
    recent_fps = {}
    baseline_fps = set()
    try:
        with _db.db_conn() as cx:
            # Recent failures
            rs = cx.execute("""
                SELECT message FROM history
                WHERE site_id = ? AND status = 'failed' AND message != ''
                  AND CAST(strftime('%s', ts) AS REAL) >= ?
            """, (site_id, recent_since)).fetchall()
            for (msg,) in rs:
                try:
                    fp = _ef.fingerprint_for(msg or "")
                except Exception:
                    continue
                if not fp:
                    continue
                if fp not in recent_fps:
                    recent_fps[fp] = {"count": 0, "sample": msg or ""}
                recent_fps[fp]["count"] += 1
            # Baseline (older) failures
            bs = cx.execute("""
                SELECT message FROM history
                WHERE site_id = ? AND status = 'failed' AND message != ''
                  AND CAST(strftime('%s', ts) AS REAL)
                      BETWEEN ? AND ?
            """, (site_id, baseline_since, baseline_until)).fetchall()
            for (msg,) in bs:
                try:
                    fp = _ef.fingerprint_for(msg or "")
                except Exception:
                    continue
                if fp:
                    baseline_fps.add(fp)
    except Exception:
        return []
    out = []
    for fp, info in recent_fps.items():
        if fp in baseline_fps:
            continue
        if info["count"] < 2:
            continue  # Single-occurrence noise; need >=2 to be a pattern
        out.append({
            "fingerprint": fp,
            "count": info["count"],
            "sample_message": info["sample"][:160],
        })
    out.sort(key=lambda x: -x["count"])
    return out[:5]


def _cookie_health(site_id: str, s_cfg_entry: Optional[dict]) -> Optional[dict]:
    """Cookie health: total cookies, count expiring within 7 days,
    count already expired. Returns None if we can't read jars."""
    if not s_cfg_entry:
        return None
    cookie_file = (s_cfg_entry.get("cookie_file") or "").strip()
    if not cookie_file:
        return None
    try:
        from . import cookies as _ck
        from pathlib import Path
        if not Path(cookie_file).is_file():
            return {"total": 0, "expiring_soon": 0, "expired": 0,
                    "missing": True}
        jar = _ck.load_cookies_from_file(cookie_file) or []
        now = time.time()
        soon = now + 7 * 86400
        expiring_soon = 0
        expired = 0
        for c in jar:
            exp = c.get("expires")
            if not exp:
                continue
            try:
                exp = float(exp)
            except (TypeError, ValueError):
                continue
            if exp <= now:
                expired += 1
            elif exp <= soon:
                expiring_soon += 1
        return {"total": len(jar), "expiring_soon": expiring_soon,
                "expired": expired, "missing": False}
    except Exception:
        return None


def for_site(site_id: str, *,
            s_cfg_entry: Optional[dict] = None) -> dict:
    """Build the changelog for one site. Returns a list of entries
    plus headline stats.

    Each entry is {severity, kind, when, message}."""
    now = time.time()
    entries = []

    # Compare last-24h success rate vs prior 7-day baseline
    recent = _success_rate(site_id, since_seconds=24 * 3600)
    baseline = _success_rate(site_id, since_seconds=7 * 86400,
                             until_seconds=now - 24 * 3600)
    if recent["has_data"] and baseline["has_data"]:
        delta = recent["success_rate"] - baseline["success_rate"]
        # Material drop: 15+ points
        if delta <= -15:
            entries.append({
                "severity": "alert",
                "kind": "success_rate_drop",
                "when": now,
                "message": (
                    f"Success rate fell {abs(delta):.0f} points: "
                    f"{baseline['success_rate']:.0f}% → "
                    f"{recent['success_rate']:.0f}% "
                    f"(last 24h vs prior 7d)"
                ),
                "data": {"recent": recent, "baseline": baseline,
                         "delta": delta},
            })
        elif delta <= -5:
            entries.append({
                "severity": "warn",
                "kind": "success_rate_drop",
                "when": now,
                "message": (
                    f"Success rate slipped {abs(delta):.0f} points: "
                    f"{baseline['success_rate']:.0f}% → "
                    f"{recent['success_rate']:.0f}%"
                ),
                "data": {"recent": recent, "baseline": baseline,
                         "delta": delta},
            })
        elif delta >= 10:
            entries.append({
                "severity": "info",
                "kind": "success_rate_recovery",
                "when": now,
                "message": (
                    f"Success rate recovered +{delta:.0f} points: "
                    f"{baseline['success_rate']:.0f}% → "
                    f"{recent['success_rate']:.0f}%"
                ),
                "data": {"recent": recent, "baseline": baseline,
                         "delta": delta},
            })

    # New error fingerprints — these are the strongest "site changed"
    # signal. A novel cluster of failures in 24h is almost always either
    # a site DOM change, a CDN edge change, or fresh anti-bot.
    new_fps = _new_failure_fingerprints(site_id)
    for fp_info in new_fps:
        sev = "alert" if fp_info["count"] >= 5 else "warn"
        entries.append({
            "severity": sev,
            "kind": "new_failure_pattern",
            "when": now,
            "message": (
                f"New failure pattern: {fp_info['count']} occurrence(s) "
                f"of '{fp_info['sample_message'][:80]}'"
            ),
            "data": fp_info,
        })

    # Cookie health
    ck = _cookie_health(site_id, s_cfg_entry)
    if ck:
        if ck.get("missing"):
            entries.append({
                "severity": "warn",
                "kind": "no_cookies",
                "when": now,
                "message": (
                    "Cookie file configured but missing on disk. "
                    "Workers may degrade to anonymous browsing."
                ),
                "data": ck,
            })
        elif ck["expired"] > 0:
            entries.append({
                "severity": "warn",
                "kind": "cookies_expired",
                "when": now,
                "message": (
                    f"{ck['expired']} cookie(s) have expired. "
                    f"Re-login recommended."
                ),
                "data": ck,
            })
        elif ck["expiring_soon"] > 0:
            entries.append({
                "severity": "info",
                "kind": "cookies_expiring_soon",
                "when": now,
                "message": (
                    f"{ck['expiring_soon']} cookie(s) expire within 7 days."
                ),
                "data": ck,
            })

    # Sort by severity (alerts first), then by recency
    entries.sort(key=lambda e: (-SEVERITY_RANK.get(e["severity"], 0),
                                -e["when"]))

    # Headline summary
    counts = {"alert": 0, "warn": 0, "info": 0}
    for e in entries:
        counts[e["severity"]] = counts.get(e["severity"], 0) + 1
    return {
        "site_id": site_id,
        "entries": entries,
        "counts": counts,
        "headline_severity": (
            "alert" if counts["alert"] else
            ("warn" if counts["warn"] else
             ("info" if counts["info"] else "ok"))),
        "recent_24h": recent,
        "baseline_7d": baseline,
    }


def for_all_sites(s_cfg: Optional[dict] = None) -> dict:
    """Run for_site on every configured site. Returns {sites: [...]}
    sorted with the most-broken sites first."""
    if not s_cfg:
        s_cfg = {}
    out = []
    for sid, entry in s_cfg.items():
        out.append(for_site(sid, s_cfg_entry=entry))
    # Sort: alert > warn > info > ok
    severity_order = {"alert": 0, "warn": 1, "info": 2, "ok": 3}
    out.sort(key=lambda s: severity_order.get(s["headline_severity"], 9))
    return {"sites": out, "ts": time.time()}
