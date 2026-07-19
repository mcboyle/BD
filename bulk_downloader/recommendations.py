"""Recommendations + recent additions (Phase 95, Block K).

Builds on Phase 92 (FTS5), Phase 94 (tag inference + performer
canonicalization), and Phase 104 (provenance ledger).

Three capabilities:

  • similar_to(filename_or_url) — find library items most similar to
    a given anchor, ranked by tag overlap + performer overlap
  • recent_additions(days, limit) — newest done rows from history
  • top_tags(window_days, limit) — most-downloaded tags in window

No ML, no embeddings. Pure tag/performer overlap with a few
heuristics — fast and predictable. The operator can read the
ranking justification and override.

Performance note: scans `history` rows in the window. At 100k rows
that's a few hundred ms; bounded by sqlite + Python loop overhead.
Acceptable for an interactive endpoint.
"""
from __future__ import annotations

import time
from typing import Optional


def _row_tags(row: dict) -> set:
    """Extract a set of tags from a history row using Phase 94's
    inference. Combines filename + URL + message for max signal."""
    try:
        from .tag_inference import infer_tags
        parts = " ".join([
            row.get("filename", "") or "",
            row.get("url", "") or "",
            row.get("message", "") or "",
        ])
        return set(infer_tags(parts))
    except Exception:
        return set()


def _row_performers(row: dict, known: list) -> set:
    """Extract performer canonical keys from the row."""
    try:
        from .tag_inference import infer_performers
        text = " ".join([
            row.get("filename", "") or "",
            row.get("url", "") or "",
        ])
        return set(infer_performers(text, known=known))
    except Exception:
        return set()


def _all_done_rows(limit: int = 2000, days: Optional[int] = None) -> list:
    """Recent done rows, newest first, capped at limit."""
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            if days:
                rows = cx.execute("""SELECT id, site_id, url, filename, message, ts
                                      FROM history
                                      WHERE status='done'
                                        AND ts >= datetime('now', ?)
                                      ORDER BY id DESC LIMIT ?""",
                                   (f"-{int(days)} days", int(limit))).fetchall()
            else:
                rows = cx.execute("""SELECT id, site_id, url, filename, message, ts
                                      FROM history
                                      WHERE status='done'
                                      ORDER BY id DESC LIMIT ?""",
                                   (int(limit),)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _known_performers() -> list:
    """All performer aliases registered with the tag_inference module."""
    try:
        from . import tag_inference as _ti
        return list(_ti._PERFORMER_ALIASES.keys()) if hasattr(_ti, "_PERFORMER_ALIASES") else []
    except Exception:
        return []


def similar_to(anchor_text: str, *, limit: int = 20,
               lookback_days: Optional[int] = 180) -> list:
    """Find library items most similar to the anchor.

    Similarity score (0.0-1.0):
      • +0.6 × jaccard(tags_anchor, tags_candidate)
      • +0.4 × jaccard(performers_anchor, performers_candidate)

    Anchor with no inferred tags or performers returns [] — we can't
    score similarity against nothing."""
    if not anchor_text:
        return []
    anchor_tags = set()
    try:
        from .tag_inference import infer_tags
        anchor_tags = set(infer_tags(anchor_text))
    except Exception:
        pass
    known = _known_performers()
    anchor_performers = set()
    if known:
        try:
            from .tag_inference import infer_performers
            anchor_performers = set(infer_performers(anchor_text, known=known))
        except Exception:
            pass
    if not anchor_tags and not anchor_performers:
        return []
    pool = _all_done_rows(limit=2000, days=lookback_days)
    scored = []
    for row in pool:
        # Skip the anchor itself if it happens to be in the pool
        if anchor_text in (row.get("filename") or "") or \
           anchor_text in (row.get("url") or ""):
            continue
        r_tags = _row_tags(row)
        r_perfs = _row_performers(row, known)
        score = 0.0
        if anchor_tags and r_tags:
            inter = len(anchor_tags & r_tags)
            union = len(anchor_tags | r_tags)
            if union > 0:
                score += 0.6 * (inter / union)
        if anchor_performers and r_perfs:
            inter = len(anchor_performers & r_perfs)
            union = len(anchor_performers | r_perfs)
            if union > 0:
                score += 0.4 * (inter / union)
        if score > 0.0:
            scored.append({
                "id": row.get("id"),
                "site_id": row.get("site_id"),
                "url": row.get("url"),
                "filename": row.get("filename"),
                "ts": row.get("ts"),
                "score": round(score, 3),
                "shared_tags": sorted(anchor_tags & r_tags),
                "shared_performers": sorted(anchor_performers & r_perfs),
            })
    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored[:limit]


def recent_additions(*, days: int = 7, limit: int = 50,
                     site_id: Optional[str] = None) -> list:
    """List newest done rows in window. Used by dashboard
    "recently added" widget. Returns rows newest first."""
    try:
        from . import db as _db
        sql = """SELECT id, site_id, site_name, url, filename, file_size, ts
                  FROM history
                  WHERE status='done' AND ts >= datetime('now', ?)"""
        params = [f"-{int(days)} days"]
        if site_id:
            sql += " AND site_id = ?"
            params.append(site_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        with _db.db_conn() as cx:
            return [dict(r) for r in cx.execute(sql, params).fetchall()]
    except Exception:
        return []


def top_tags(*, days: int = 30, limit: int = 25) -> list:
    """Most-frequent inferred tags from done rows in window. Returns
    list of {tag, count} dicts sorted by count DESC."""
    rows = _all_done_rows(limit=5000, days=days)
    counts: dict = {}
    for r in rows:
        for tag in _row_tags(r):
            counts[tag] = counts.get(tag, 0) + 1
    pairs = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return [{"tag": t, "count": c} for t, c in pairs[:limit]]


def top_performers(*, days: int = 30, limit: int = 25) -> list:
    """Most-downloaded performers from done rows in window."""
    known = _known_performers()
    if not known:
        return []
    rows = _all_done_rows(limit=5000, days=days)
    counts: dict = {}
    for r in rows:
        for p in _row_performers(r, known):
            counts[p] = counts.get(p, 0) + 1
    pairs = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return [{"performer": p, "count": c} for p, c in pairs[:limit]]
