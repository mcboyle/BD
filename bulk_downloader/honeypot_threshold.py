"""Per-site honeypot threshold learning — P5-2b (v3.66.36).

R-P5-2 (v3.66.27) ships a deterministic honeypot scorer with a single
GLOBAL drop threshold (``BD_HONEYPOT_SCORE_THRESHOLD``, default off). This
module is the follow-up: derive a PER-SITE drop threshold from the site's
own history, so a site that has historically produced traps at low scores
gets a tighter boundary while a clean site keeps the conservative default.

One knob per site. No model file, no training pipeline — just a quantile of
the scores that turned out to be traps, clamped to a sane band.

Training signal
---------------

A "trap" is a finished download whose file is suspiciously small
(``cleanup_helpers.find_tinies`` semantics: ``status='done'`` and
``0 < file_size < threshold``) AND which carries a persisted
``honeypot_score`` on its history row (the score stamped at resolve time
in v3.66.27, persisted on the row as of v3.66.36's additive column). The
learner takes those per-site trap scores and sets the drop threshold to
their low-end quantile: drop anything scoring at least as high as the
lowest-scoring confirmed trap (with a small outlier tolerance).

Opt-in
------

``BD_HONEYPOT_PER_SITE=1`` switches the threshold seam in
``provider_resolve`` from the global value to the per-site learned value.
Default off → the global behaviour (itself default off) is unchanged.
Until the history column has been populated by real downloads, every site
has zero trap samples → the learner returns the supplied default → still
no behaviour change. The feature activates only once real evidence exists,
which is exactly the §P5-2b gate.

The threshold is also gated on a minimum sample count: with fewer than
``DEFAULT_MIN_SAMPLES`` confirmed traps the learner refuses to move the
threshold (too little evidence to trust a per-site number).
"""
from __future__ import annotations

import os
from typing import List, Optional


# ── Tunables ────────────────────────────────────────────────────────
# Minimum confirmed-trap samples before a per-site threshold is trusted.
DEFAULT_MIN_SAMPLES = 5
# Quantile of confirmed-trap scores used as the drop threshold. 0.10 = the
# 10th percentile: drop at the low end of "scores that turned out to be
# traps", tolerating the single lowest outlier.
DEFAULT_QUANTILE = 0.10
# Clamp band. Never drop below the downscore boundary (0.5) — that's the
# scorer's "weak signal" floor — and always leave headroom below 1.0 so a
# perfectly-clean-but-unlucky candidate can still pass.
THRESHOLD_FLOOR = 0.5
THRESHOLD_CEIL = 0.95
# How small (MB) a finished file must be to count as a likely trap.
DEFAULT_TINY_MB = 5

_ENV_FLAG = "BD_HONEYPOT_PER_SITE"


def enabled() -> bool:
    """True iff per-site threshold learning is opted in. v3.66.313 (CLI->GUI parity):
    read store > env seed at call time — the global_config `honeypot_per_site` bool wins
    when set, else the BD_HONEYPOT_PER_SITE env (=="1") is the seed. global_config
    imported lazily; falls back to env on any error."""
    try:
        from . import global_config as _gc
        v = _gc.get("honeypot_per_site", None)
        if isinstance(v, bool):
            return v
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get(_ENV_FLAG) == "1"


def _percentile(sorted_vals: List[float], q: float) -> float:
    """Linear-interpolation percentile of an already-sorted, non-empty
    list. ``q`` in [0, 1]."""
    if not sorted_vals:
        raise ValueError("empty")
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    q = min(max(q, 0.0), 1.0)
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return float(sorted_vals[lo]) + (float(sorted_vals[hi]) - float(sorted_vals[lo])) * frac


def learn_threshold(
    trap_scores: List[float],
    *,
    default: float,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    quantile: float = DEFAULT_QUANTILE,
    floor: float = THRESHOLD_FLOOR,
    ceil: float = THRESHOLD_CEIL,
) -> float:
    """Compute a per-site drop threshold from confirmed-trap scores.

    Returns ``default`` (unchanged) when there isn't enough evidence
    (fewer than ``min_samples`` usable scores). Otherwise returns the
    ``quantile`` of the trap scores, clamped to ``[floor, ceil]``.

    A returned value <= the caller's downscore boundary still means
    "drop aggressively"; the clamp floor stops it going below the weak-
    signal line.
    """
    usable = []
    for s in (trap_scores or []):
        try:
            v = float(s)
        except (TypeError, ValueError):
            continue
        if 0.0 <= v <= 1.0:
            usable.append(v)
    if len(usable) < max(1, min_samples):
        return default
    usable.sort()
    thr = _percentile(usable, quantile)
    thr = min(max(thr, floor), ceil)
    return round(thr, 4)


def trap_scores_for_site(
    site_id: str,
    *,
    tiny_mb: int = DEFAULT_TINY_MB,
    limit: int = 1000,
    conn=None,
) -> List[float]:
    """Return the persisted ``honeypot_score`` values for this site's
    confirmed traps — finished downloads with a suspiciously small file
    that also carry a stamped score.

    Degrades to ``[]`` on any error (missing column on an un-migrated DB,
    no DB, bad site_id) — the learner then falls back to the default, so a
    missing data surface can never tighten or break anything.
    """
    if not site_id:
        return []
    sql = (
        "SELECT honeypot_score FROM history "
        "WHERE site_id = ? AND status = 'done' "
        "  AND file_size > 0 AND file_size < ? "
        "  AND honeypot_score IS NOT NULL "
        "ORDER BY id DESC LIMIT ?"
    )
    params = [site_id, tiny_mb * 1024 * 1024, int(limit)]
    try:
        if conn is not None:
            rows = conn.execute(sql, params).fetchall()
        else:
            from . import db as _db
            with _db.db_conn() as cx:
                rows = cx.execute(sql, params).fetchall()
    except Exception:
        return []
    out: List[float] = []
    for r in rows:
        val = r[0] if not hasattr(r, "keys") else r["honeypot_score"]
        try:
            out.append(float(val))
        except (TypeError, ValueError):
            continue
    return out


def learned_drop_threshold(
    site_id: str,
    *,
    default: float,
    conn=None,
    **kw,
) -> float:
    """Convenience: trap scores for ``site_id`` → learned threshold.
    Returns ``default`` when there's insufficient evidence."""
    scores = trap_scores_for_site(site_id, conn=conn)
    return learn_threshold(scores, default=default, **kw)
