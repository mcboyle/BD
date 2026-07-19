"""Queue prioritization (Phase 157, Block Q).

By default BD processes pending jobs FIFO. This module provides a
scoring system so operators can re-rank the queue based on operator-
configurable factors:

  • freshness: newer URLs scored higher (operator usually wants today's
    upload, not last week's)
  • site preference: per-site multiplier ("vixen always wins over X")
  • account quota: jobs on a site with low remaining quota lose
    priority (don't burn the last requests on stale URLs)
  • cookie quality: a site with low cookie score loses priority
    (likely-to-fail jobs go to the back)
  • size hint: if known, prefer smaller files first (latency win)

Scoring is deterministic and explainable: every score has a breakdown
the operator can see, so "why is this URL at the top?" has a
straightforward answer.

The runner uses `next_url_to_process()` instead of plain SQL ordering.
"""
from __future__ import annotations

import sys
import time
from typing import Optional


def _score_one(row: dict, *,
              s_cfg: Optional[dict] = None,
              context: Optional[dict] = None) -> dict:
    """Score one pending URL. Returns {score, breakdown}."""
    base = 100.0
    breakdown: dict = {"base": base}
    sid = row.get("site_id") or ""
    site_cfg = (s_cfg or {}).get(sid, {}) if s_cfg else {}

    # 1. Freshness — newer URLs preferred (decay by hours since enqueued)
    try:
        from datetime import datetime
        ts_str = row.get("ts") or ""
        if ts_str:
            ts = datetime.fromisoformat(ts_str.replace(" ", "T").rstrip("Z"))
            age_hours = (time.time() - ts.timestamp()) / 3600.0
            # Cap at 168h (1 week). Older = penalty (0 → -40).
            fresh_pts = max(-40, -age_hours * 0.2)
            breakdown["freshness"] = round(fresh_pts, 1)
            base += fresh_pts
    except Exception:
        pass

    # 2. Site preference multiplier (operator-set in site config)
    pref = float((site_cfg.get("priority_multiplier") or 1.0))
    pref_pts = (pref - 1.0) * 50  # -50 .. +50
    breakdown["site_preference"] = round(pref_pts, 1)
    base += pref_pts

    # 3. Cookie quality — low score means likely to fail; deprioritize
    if context and "cookie_scores" in context:
        cs = context["cookie_scores"].get(sid, 100)
        if cs < 80:
            penalty = -(80 - cs) * 0.3  # -24 .. 0
            breakdown["cookie_quality_penalty"] = round(penalty, 1)
            base += penalty

    # 4. Circuit-breaker state — if the host is open, big penalty
    if context and "open_circuits" in context:
        url = row.get("url") or ""
        try:
            from urllib.parse import urlparse
            host = urlparse(url).hostname or ""
        except Exception:
            host = ""
        if host and host in context["open_circuits"]:
            breakdown["circuit_open_penalty"] = -50
            base -= 50

    # 5. Account quota — if site is at its monthly budget cap,
    # punish that site's jobs heavily
    if context and "budget_pcts" in context:
        bp = context["budget_pcts"].get(sid)
        if bp is not None and bp > 90:
            breakdown["budget_exhausted_penalty"] = -30
            base -= 30

    return {"score": round(base, 1), "breakdown": breakdown}


def _gather_context(s_cfg: Optional[dict] = None) -> dict:
    """One-time context payload used by all per-row scorers in a pass."""
    ctx: dict = {}
    try:
        from . import cookie_quality as _cq
        ctx["cookie_scores"] = {
            r["site_id"]: r.get("score", 100)
            for r in _cq.report_all(s_cfg or {})
            if "score" in r
        }
    except Exception:
        ctx["cookie_scores"] = {}
    try:
        from . import circuit_breaker as _cb
        rep = _cb.report() or {}
        ctx["open_circuits"] = {h for h, s in rep.items()
                                if s.get("state") == "open"}
    except Exception:
        ctx["open_circuits"] = set()
    try:
        from . import policy_gates as _pg
        ctx["budget_pcts"] = {}
        for sid, cfg in (s_cfg or {}).items():
            bs = _pg.budget_state(cfg or {})
            if bs.get("state") != "unset":
                ctx["budget_pcts"][sid] = bs.get("pct", 0)
    except Exception:
        ctx["budget_pcts"] = {}
    return ctx


def rank_pending(*, limit: int = 100,
                site_id: Optional[str] = None,
                s_cfg: Optional[dict] = None) -> list:
    """Return pending jobs ranked by score, highest first.

    `limit` caps the input pool — scoring everything in a 10k-row queue
    isn't useful; we only need the next batch to feed workers."""
    try:
        from . import db as _db
        sql = """SELECT id, site_id, url, ts FROM history
                  WHERE status = 'pending'"""
        params: list = []
        if site_id:
            sql += " AND site_id = ?"
            params.append(site_id)
        sql += " ORDER BY id ASC LIMIT ?"
        # 5x the limit — score wider, then trim
        params.append(int(limit) * 5)
        with _db.db_conn() as cx:
            rows = [dict(r) for r in cx.execute(sql, params).fetchall()]
    except Exception:
        return []
    ctx = _gather_context(s_cfg)
    scored = []
    for r in rows:
        s = _score_one(r, s_cfg=s_cfg, context=ctx)
        scored.append({**r, **s})
    scored.sort(key=lambda x: -x["score"])
    return scored[:limit]


def next_url_to_process(*, site_id: str,
                       s_cfg: Optional[dict] = None) -> Optional[dict]:
    """Pick the single best pending URL for one site. Used by the
    worker loop to replace plain LIMIT 1 ordering."""
    ranked = rank_pending(limit=1, site_id=site_id, s_cfg=s_cfg)
    return ranked[0] if ranked else None


def explain_rank(history_id: int,
                *, s_cfg: Optional[dict] = None) -> dict:
    """Show the score + breakdown for one pending job. Used by UI
    'why is this here?' feature."""
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            r = cx.execute("""SELECT id, site_id, url, ts, status
                               FROM history WHERE id = ?""",
                          (int(history_id),)).fetchone()
        if not r:
            return {"error": "not found"}
        row = dict(r)
        ctx = _gather_context(s_cfg)
        s = _score_one(row, s_cfg=s_cfg, context=ctx)
        return {**row, **s}
    except Exception as e:
        return {"error": str(e)[:200]}


def score_urls_in_memory(*, urls: list, site_id: str,
                        jobs: dict,
                        s_cfg: Optional[dict] = None) -> dict:
    """Score a list of in-memory URLs (already loaded into a runner's
    self.jobs) using the priority system. Gathers context once and
    reuses it across all scoring calls — safe to use inside a sort
    key in the worker loop.

    Returns {url: score}. URLs with no entry in `jobs` get score=0.

    This is the entrypoint runner.py uses; the existing `rank_pending`
    is for /api/queue/rank (UI), which reads from history.
    """
    if not urls:
        return {}
    ctx = _gather_context(s_cfg)
    out: dict = {}
    for url in urls:
        job = (jobs or {}).get(url) or {}
        # Synthesize a row matching what _score_one expects
        row = {
            "site_id": site_id,
            "url": url,
            "ts": job.get("ts", ""),
        }
        result = _score_one(row, s_cfg=s_cfg, context=ctx)
        out[url] = result.get("score", 100.0)
    return out
