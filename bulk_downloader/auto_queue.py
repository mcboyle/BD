"""bulk_downloader.auto_queue -- A8: queue self-management.

Three PURE planners over the pending queue plus one gated orchestration entry:

  * ``plan_dedup``        -- collapse duplicate URLs (normalized), keep the
    earliest enqueued row per URL, drop the rest.
  * ``plan_prioritize``  -- order pending rows by descending priority score
    (delegates to ``queue_priority`` by default; injectable for tests).
  * ``plan_pause_resume`` -- given per-site {rate_limited, paused} state,
    decide which sites to pause (rate-limited & running) and which to resume
    (paused & recovered). Rides the 470 ``site.cooldown``/``site.recovered``
    semantics -- this is the queue-side reaction to those transitions.

``manage_queue_if_enabled`` is the single gated entry. It is a no-op unless the
``auto_queue`` toggle is on (DEFAULT OFF). When on it computes the combined plan
and, if ``apply_fns`` are injected, applies it through the caller's existing
primitives (``app_dedup`` remove, a reorder hook, runner pause/resume) and
reports applied counts. The planners never mutate; application is always through
injected callables, so the live wiring (and its reversibility) stays with the
caller / the A9 controller.

Posture (AUTOMATION_POLICY-aligned):
  * ``auto_queue`` is NOT keystone-required. Queue ops are reversible and never
    overwrite a serving template -- the keystone gate guards template
    overwrites, which this module never performs.
  * Fail-safe: a throwing planner degrades that sub-plan (dedup still planned,
    order falls back to input order) rather than raising into the caller.
"""
from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit
from typing import Any, Callable, Dict, List, Optional

from . import lifecycle_automation as la


def _norm_url(u: Any) -> str:
    """Normalize a URL for dedup: lowercase scheme+host, drop the fragment,
    strip a single trailing slash on the path. Query is preserved (it can be
    download-significant). Fail-safe: returns the stripped raw string on error."""
    s = (str(u) if u is not None else "").strip()
    if not s:
        return ""
    try:
        parts = urlsplit(s if "://" in s else "https://" + s)
        scheme = (parts.scheme or "https").lower()
        netloc = parts.netloc.lower()
        path = parts.path or ""
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]
        return urlunsplit((scheme, netloc, path, parts.query, ""))
    except Exception:
        return s.lower()


def _hid(row: Dict[str, Any]) -> Any:
    return row.get("history_id")


def plan_dedup(pending_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Group pending rows by normalized URL. The keeper is the row with the
    lowest ``history_id`` in each group (earliest enqueued); every other row in
    a multi-row group is a drop. Pure + deterministic.

    Returns ``{keep: [history_id...], drop: [history_id...], groups: {url:[ids]}}``.
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in (pending_rows or []):
        key = _norm_url(row.get("url"))
        groups.setdefault(key, []).append(row)

    keep: List[Any] = []
    drop: List[Any] = []
    out_groups: Dict[str, List[Any]] = {}
    for key, rows in groups.items():
        ids = [_hid(r) for r in rows]
        out_groups[key] = ids
        # Keeper = lowest history_id (numeric when possible, else first seen).
        try:
            keeper = min(rows, key=lambda r: (_hid(r) is None, _hid(r)))
        except TypeError:
            keeper = rows[0]
        keep.append(_hid(keeper))
        drop.extend(h for h in ids if h != _hid(keeper))
    return {"keep": keep, "drop": drop, "groups": out_groups}


def _default_rank(row: Dict[str, Any], *, s_cfg: Optional[dict]) -> float:
    """Default score for a single pending row via queue_priority. Higher is
    more urgent. Fail-safe: 0.0 on any error."""
    try:
        from . import queue_priority as qp
        scored = qp._score_one(row, s_cfg=s_cfg, context=None)
        return float(scored.get("score", 0.0))
    except Exception:
        return 0.0


def plan_prioritize(pending_rows: List[Dict[str, Any]], *,
                    rank_fn: Optional[Callable[[Dict[str, Any]], float]] = None,
                    s_cfg: Optional[dict] = None) -> List[Any]:
    """Return the pending rows' ``history_id``s ordered by DESCENDING score.
    Ties preserve input order (stable). ``rank_fn`` is injectable; the default
    scores via ``queue_priority``. Pure (no DB)."""
    rows = list(pending_rows or [])
    rf = rank_fn if rank_fn is not None else (lambda r: _default_rank(r, s_cfg=s_cfg))
    decorated = []
    for idx, row in enumerate(rows):
        try:
            score = float(rf(row))
        except Exception:
            score = 0.0
        decorated.append((-score, idx, _hid(row)))
    decorated.sort()
    return [hid for _s, _i, hid in decorated]


def plan_pause_resume(site_states: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    """Decide pause/resume actions from per-site state.

    ``site_states``: ``{site_id: {"rate_limited": bool, "paused": bool}}``.
      * pause  = rate_limited AND not paused (a running site just got limited)
      * resume = paused AND not rate_limited (a paused site has recovered)
    Steady states (both true, or both false) -> no action. Deterministic order.
    """
    pause: List[str] = []
    resume: List[str] = []
    for sid in sorted((site_states or {}).keys()):
        st = site_states.get(sid) or {}
        limited = bool(st.get("rate_limited"))
        paused = bool(st.get("paused"))
        if limited and not paused:
            pause.append(sid)
        elif paused and not limited:
            resume.append(sid)
    return {"pause": pause, "resume": resume}


def manage_queue_if_enabled(pending_rows: List[Dict[str, Any]],
                            site_states: Dict[str, Dict[str, Any]], *,
                            rank_fn: Optional[Callable[[Dict[str, Any]], float]] = None,
                            s_cfg: Optional[dict] = None,
                            apply_fns: Optional[Dict[str, Callable]] = None
                            ) -> Dict[str, Any]:
    """Gated A8 entry. No-op when ``auto_queue`` is disabled (DEFAULT OFF).

    When enabled, computes ``{dedup, order, pause, resume}`` and -- if
    ``apply_fns`` is supplied -- applies the plan through the caller's
    primitives, returning applied counts. ``apply_fns`` keys (all optional):
    ``remove(ids)->int``, ``reorder(order)``, ``pause(site_id)``,
    ``resume(site_id)``. Never raises into the caller."""
    if not la.is_enabled("auto_queue"):
        return {"ok": True, "skipped": "auto_queue disabled"}

    try:
        dedup = plan_dedup(pending_rows)
    except Exception as e:
        dedup = {"keep": [], "drop": [], "groups": {}, "error": str(e)[:120]}
    try:
        order = plan_prioritize(pending_rows, rank_fn=rank_fn, s_cfg=s_cfg)
    except Exception:
        order = [_hid(r) for r in (pending_rows or [])]  # degrade to input order
    try:
        pr = plan_pause_resume(site_states)
    except Exception:
        pr = {"pause": [], "resume": []}

    plan = {"dedup": dedup, "order": order,
            "pause": pr["pause"], "resume": pr["resume"]}

    out: Dict[str, Any] = {"ok": True, "plan": plan}
    if apply_fns:
        applied = {"removed": 0, "paused": 0, "resumed": 0, "reordered": False}
        try:
            rm = apply_fns.get("remove")
            if rm and dedup.get("drop"):
                n = rm(list(dedup["drop"]))
                applied["removed"] = int(n) if isinstance(n, int) else len(dedup["drop"])
            ro = apply_fns.get("reorder")
            if ro and order:
                ro(list(order))
                applied["reordered"] = True
            pf = apply_fns.get("pause")
            if pf:
                for sid in pr["pause"]:
                    pf(sid)
                    applied["paused"] += 1
            rf = apply_fns.get("resume")
            if rf:
                for sid in pr["resume"]:
                    rf(sid)
                    applied["resumed"] += 1
        except Exception as e:
            out["apply_error"] = str(e)[:160]
        out["applied"] = applied
    return out
