"""Phase 9.16 -- library semantic dedup planning (preview only).

Preview-only near-duplicate planning. The deterministic EXACT-hash grouping stays
the source of truth for exact dupes; semantic/title similarity only *suggests*
near-dupe candidates and confidence is used for SORTING only. There is no
delete/archive/move path anywhere here -- `plan()` returns a preview that requires
human confirmation.
"""

import re
from typing import Any, Dict, List, Optional

_NEAR_THRESHOLD = 0.6


def _norm_title(t: str) -> str:
    t = (t or "").lower()
    t = re.sub(r"\b(1080p|720p|480p|2160p|4k|x264|x265|hevc|web-?dl|bluray|proper|repack)\b", " ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def _similarity(a: str, b: str) -> float:
    ta, tb = set(_norm_title(a).split()), set(_norm_title(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _quality_rank(item: Dict[str, Any]) -> tuple:
    res_order = {"2160p": 4, "4k": 4, "1080p": 3, "720p": 2, "480p": 1}
    res = res_order.get(str(item.get("resolution", "")).lower(), 0)
    return (res, int(item.get("size", 0) or 0))


def plan(items: List[Dict[str, Any]], *, _call=None) -> Dict[str, Any]:
    """Group dupes for a preview. Returns exact_groups (deterministic authority),
    near_groups (advisory), keep_recommendations, and requires_confirmation."""
    items = items or []

    # ── deterministic exact-hash grouping (authority) ─────────────────────
    by_hash: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        by_hash.setdefault(str(it.get("hash", "")), []).append(it)
    exact_groups = [g for h, g in by_hash.items() if h and len(g) > 1]

    # ── advisory near-dup grouping by normalized-title similarity ─────────
    remaining = [it for it in items]
    near_groups: List[Dict[str, Any]] = []
    used = set()
    for i, a in enumerate(remaining):
        if id(a) in used:
            continue
        group = [a]
        for b in remaining[i + 1:]:
            if id(b) in used:
                continue
            if str(a.get("hash", "")) == str(b.get("hash", "")) and a.get("hash"):
                continue  # exact dupes handled above
            sim = _similarity(a.get("title", ""), b.get("title", ""))
            if sim >= _NEAR_THRESHOLD:
                group.append(b)
                used.add(id(b))
        if len(group) > 1:
            used.add(id(a))
            sims = [_similarity(group[0].get("title", ""), x.get("title", ""))
                    for x in group[1:]]
            conf = round(min(sims) if sims else 0.0, 2)
            keep = max(group, key=_quality_rank)
            near_groups.append({
                "members": [g.get("id") for g in group],
                "keep_candidate": keep.get("id"),
                "confidence": conf,
                "review": conf < 0.75,
            })

    keep_recs = [{"keep": g["keep_candidate"], "confidence": g["confidence"]}
                 for g in near_groups]

    return {
        "exact_groups": [[it.get("id") for it in g] for g in exact_groups],
        "near_groups": near_groups,
        "keep_recommendations": keep_recs,
        "requires_confirmation": True,
        "advisory": True,
    }
