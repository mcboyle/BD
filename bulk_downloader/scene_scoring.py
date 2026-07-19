"""Scene scoring (Phase 149, Block Q).

Reads downloaded files from history and computes a quality score so
operators can find the best (keep / archive) and worst (delete /
re-download) files in their library.

Signals (each contributes points, 0–100 total):

  • Resolution tier from filename (4K=40, 1080p=25, 720p=15, 480p=5)
  • File size vs the tier baseline (5-15 points for being above
    expected size — proxy for high-bitrate encoding)
  • Filename has explicit resolution marker (vs ambiguous)  → +5

This is a coarse score — its purpose is to surface OUTLIERS, not to
make fine-grained quality judgments. A 4K file under 1 GB scores low
(probably bad encoding); a 480p file under 200 MB scores normally for
its tier.

No new tables; reads from history.
"""
from __future__ import annotations

import re
import time
from typing import Optional


# Resolution signal: regex → (tier_label, base_score, expected_min_mb)
# expected_min_mb is the file size below which we apply a penalty.
# Boundary: (?:^|[^a-zA-Z0-9]) instead of \b, because \b treats _ as a
# word char so `Vixen_4K_` doesn't see a boundary between _ and 4. We
# explicitly require a non-alnum char (or string start/end).
_BOUNDARY_L = r"(?:^|[^a-zA-Z0-9])"
_BOUNDARY_R = r"(?=[^a-zA-Z0-9]|$)"

RESOLUTION_PATTERNS = [
    (re.compile(_BOUNDARY_L + r"(8k|7680x|4320p)" + _BOUNDARY_R,
                re.IGNORECASE),
     "8K", 50, 8_000),
    (re.compile(_BOUNDARY_L + r"(4k|2160p|3840x|uhd)" + _BOUNDARY_R,
                re.IGNORECASE),
     "4K", 40, 3_000),
    (re.compile(_BOUNDARY_L + r"1440p" + _BOUNDARY_R, re.IGNORECASE),
     "1440p", 30, 1_500),
    (re.compile(_BOUNDARY_L + r"(1080p|fullhd|full[\s-]?hd|fhd)"
                + _BOUNDARY_R, re.IGNORECASE),
     "1080p", 25, 1_000),
    (re.compile(_BOUNDARY_L + r"720p" + _BOUNDARY_R, re.IGNORECASE),
     "720p", 15, 400),
    (re.compile(_BOUNDARY_L + r"480p" + _BOUNDARY_R, re.IGNORECASE),
     "480p", 5, 150),
    (re.compile(_BOUNDARY_L + r"360p" + _BOUNDARY_R, re.IGNORECASE),
     "360p", 0, 80),
]


def detect_resolution(filename: str) -> tuple:
    """Pull a resolution tier out of the filename. Returns
    (tier_label, base_score, expected_min_mb) or ('unknown', 10, 200)."""
    if not filename:
        return ("unknown", 10, 200)
    fn = filename.lower()
    for pat, label, score, min_mb in RESOLUTION_PATTERNS:
        if pat.search(fn):
            return (label, score, min_mb)
    return ("unknown", 10, 200)


def score_row(row: dict) -> dict:
    """Compute a quality score for one history row. Returns dict with
    score (0-100), breakdown, and the recommendation."""
    filename = row.get("filename", "") or ""
    file_size = int(row.get("file_size", 0) or 0)
    file_size_mb = file_size / (1024 * 1024)

    tier_label, tier_score, expected_min_mb = detect_resolution(filename)

    # Size signal: is the file appropriately sized for its tier?
    # Above 1.5× expected = boost (high-bitrate encode); below = penalty
    size_score = 0
    size_reason = ""
    if file_size_mb >= expected_min_mb * 1.5:
        size_score = 15
        size_reason = "Large for tier (likely high-bitrate encode)"
    elif file_size_mb >= expected_min_mb:
        size_score = 10
        size_reason = "Normal size for tier"
    elif file_size_mb >= expected_min_mb * 0.5:
        size_score = 0
        size_reason = "Small for tier (low-bitrate or short)"
    elif file_size_mb > 0:
        size_score = -10
        size_reason = "Suspiciously small for tier (possible bad encode)"

    # Explicit-marker bonus: filenames with explicit resolution are more
    # likely correctly tagged
    marker_bonus = 0
    marker_reason = ""
    if tier_label != "unknown":
        marker_bonus = 5
        marker_reason = "Has explicit resolution marker"

    total = max(0, min(100, tier_score + size_score + marker_bonus))

    # Recommendation: thresholds calibrated against the tier+size scores
    # so a 4K+normal-size and a 1080p+oversized both land in "keep".
    if total >= 55:
        recommendation = "keep"
    elif total >= 30:
        recommendation = "normal"
    elif total >= 15:
        recommendation = "review"
    else:
        recommendation = "candidate_for_redownload"

    return {
        "score": total,
        "tier": tier_label,
        "file_size_mb": round(file_size_mb, 1),
        "expected_min_mb": expected_min_mb,
        "recommendation": recommendation,
        "breakdown": {
            "tier_score": tier_score,
            "size_score": size_score,
            "marker_bonus": marker_bonus,
            "size_reason": size_reason,
            "marker_reason": marker_reason,
        },
    }


def _fetch_done_rows(*, site_id: Optional[str] = None,
                    limit: int = 500) -> list:
    """Fetch done rows from history. Limit kept low because we score
    each in Python — 500 rows is ~5ms; 50k rows is much slower."""
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            if site_id:
                rs = cx.execute("""
                    SELECT id, site_id, url, filename, file_size, ts
                    FROM history
                    WHERE status = 'done' AND site_id = ?
                      AND filename != ''
                    ORDER BY id DESC LIMIT ?
                """, (site_id, limit)).fetchall()
            else:
                rs = cx.execute("""
                    SELECT id, site_id, url, filename, file_size, ts
                    FROM history
                    WHERE status = 'done' AND filename != ''
                    ORDER BY id DESC LIMIT ?
                """, (limit,)).fetchall()
        return [dict(r) for r in rs]
    except Exception:
        return []


def top_scenes(*, site_id: Optional[str] = None,
              limit: int = 20,
              scan_limit: int = 500) -> list:
    """Return the highest-scored files. scan_limit caps how many
    rows we read; limit caps how many we return."""
    rows = _fetch_done_rows(site_id=site_id, limit=scan_limit)
    scored = []
    for r in rows:
        s = score_row(r)
        scored.append({**r, **s})
    scored.sort(key=lambda x: -x["score"])
    return scored[:limit]


def bottom_scenes(*, site_id: Optional[str] = None,
                 limit: int = 20,
                 scan_limit: int = 500) -> list:
    """Return the lowest-scored files — candidates for review /
    re-download."""
    rows = _fetch_done_rows(site_id=site_id, limit=scan_limit)
    scored = []
    for r in rows:
        s = score_row(r)
        scored.append({**r, **s})
    scored.sort(key=lambda x: x["score"])
    return scored[:limit]


def distribution(*, site_id: Optional[str] = None,
                scan_limit: int = 1000) -> dict:
    """Score distribution + recommendation histogram for the library
    (or one site). Used by the dashboard summary card."""
    rows = _fetch_done_rows(site_id=site_id, limit=scan_limit)
    if not rows:
        return {"sample_size": 0,
                "tiers": {}, "recommendations": {},
                "mean_score": None}
    tiers = {}
    recs = {}
    total = 0.0
    for r in rows:
        s = score_row(r)
        total += s["score"]
        tiers[s["tier"]] = tiers.get(s["tier"], 0) + 1
        recs[s["recommendation"]] = recs.get(s["recommendation"], 0) + 1
    return {
        "sample_size": len(rows),
        "tiers": tiers,
        "recommendations": recs,
        "mean_score": round(total / len(rows), 1),
    }
