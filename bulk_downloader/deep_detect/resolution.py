from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple
import re

from ._common import (RESOLUTION_TIERS, URL_BEARING_ATTRS, _selector_for)
from .urls import (decode_url)


AMBIGUOUS_QUALITY_LABELS = (
    ("source", 9500),
    ("original", 9500),
    ("master", 9500),
    ("remux", 9000),
    ("prores", 9000),
)


CODEC_BONUS = {
    "av1": 35,
    "hevc": 30,
    "vp9": 20,
    "h264": 10,
    "prores": 40,
}


RESOLUTION_RE = re.compile(
    r"(?<!\d)(?P<w>\d{3,5})\s*[x×]\s*(?P<h>\d{3,5})(?!\d)",
    re.I,
)


P_LABEL_RE = re.compile(
    r"(?<!\d)(4320|3240|3160|2880|2160|1440|1080|720|480|360|240)p(?!\d)",
    re.I,
)


QUALITY_LABEL_RE = re.compile(
    r"\b(8k|6k|5k|4k|uhd|fhd|qhd|hd|sd|"
    r"4320p|3240p|3160p|2880p|2160p|1440p|1080p|720p)\b",
    re.I,
)


CODEC_RE = re.compile(
    r"\b(hevc|h\.?265|h265|x265|av1|vp9|h\.?264|h264|x264|prores)\b",
    re.I,
)


FPS_RE = re.compile(r"\b(?P<fps>\d{1,3}(?:\.\d+)?)\s*fps\b", re.I)


SIZE_RE = re.compile(
    r"\b(?P<num>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>kib|mib|gib|tib|kb|mb|gb|tb|b)\b",
    re.I,
)


def parse_size_bytes(text: str) -> Optional[int]:
    """Pull `4.85GB` / `638 MB` / `1.2 TB` / `512 KiB` / `12 B` out of
    a free-text blob and convert to bytes. Returns None if nothing
    matches. Case-insensitive.

    Multiplier convention: 1024-based for ALL prefixes. This matches
    detect.py's parse_size_bytes (the long-standing helper used by
    the legacy detector) and the values used everywhere else in
    BulkDL for size accounting. SI vs IEC distinction is academic
    for this codebase — every consumer of these numbers compares
    them to other 1024-based values, so internal consistency wins.

    The IEC suffixes (KiB/MiB/GiB/TiB) and bare `B` were added so
    pages that use the unambiguous binary forms or report tiny files
    in bare bytes still get a usable value, not None.
    """
    if not text:
        return None
    m = SIZE_RE.search(text)
    if not m:
        return None
    num = float(m.group("num"))
    unit = m.group("unit").lower()
    # Normalize iB forms to their non-iB equivalents — both are 1024.
    base_unit = unit.replace("ib", "b") if unit != "b" else "b"
    mult = {
        "b": 1,
        "kb": 1024,
        "mb": 1024 ** 2,
        "gb": 1024 ** 3,
        "tb": 1024 ** 4,
    }[base_unit]
    return int(num * mult)


def classify_resolution(width: int, height: int) -> Optional[dict]:
    """Map (w, h) → tier dict {label, rank}. Either dimension may be
    the trigger — some cinema masters are wider but shorter than
    1080p (e.g. 4096x1716 is still 4K)."""
    if not (width and height):
        return None
    for label, rank, w_min, h_min, _terms in RESOLUTION_TIERS:
        if width >= w_min or height >= h_min:
            return {"label": label, "rank": rank,
                    "width": width, "height": height}
    return {"label": f"{height}p", "rank": int(height),
            "width": width, "height": height}


def detect_resolution_from_text(text: str) -> Optional[dict]:
    """Parse a free-text blob for resolution info. Tries, in order:
    explicit WIDTHxHEIGHT, then a 'p' label (1080p / 2160p / 4320p),
    then a vocabulary label (8K, FHD, etc.), then ambiguous quality
    words (source/original/master) with a synthetic high rank.

    Returns {label, rank, [width, height], [ambiguous]} or None."""
    if not text:
        return None
    t = " ".join(text.lower().split())

    # 1. explicit dimensions — strongest signal
    m = RESOLUTION_RE.search(t)
    if m:
        w, h = int(m.group("w")), int(m.group("h"))
        cls = classify_resolution(w, h)
        if cls:
            return cls

    # 2. NNNNp label
    pm = P_LABEL_RE.search(t)
    if pm:
        p = int(pm.group(1))
        # Map back through the tier table for consistent ranking.
        for label, rank, _w, h_min, _terms in RESOLUTION_TIERS:
            if p >= h_min:
                return {"label": label, "rank": rank, "height": p}
        return {"label": f"{p}p", "rank": p, "height": p}

    # 3. vocabulary label (handle multi-word terms like "ultra hd").
    # v3.66.10: word-boundary match instead of substring containment.
    # Pre-fix, the 2-letter token 'hd' (a 720p label term) matched
    # inside 'hdr', 'hdmi', 'shadow', etc. — common in unrelated
    # page text. Word boundaries on each side stop that.
    for label, rank, _w, _h, terms in RESOLUTION_TIERS:
        for term in terms:
            # Build a pattern with \b on each side. The term might
            # itself contain spaces ("ultra hd"), so the inner \b
            # only goes on the outer edges.
            pattern = r"\b" + re.escape(term) + r"\b"
            if re.search(pattern, t):
                return {"label": label, "rank": rank}

    # 4. ambiguous "give me the best one" label. Same word-boundary
    # treatment — 'source' should not match 'opensource' or 'resource'.
    for term, rank in AMBIGUOUS_QUALITY_LABELS:
        pattern = r"\b" + re.escape(term) + r"\b"
        if re.search(pattern, t):
            return {"label": term, "rank": rank, "ambiguous": True}

    return None


def parse_codec(text: str) -> Optional[str]:
    """Normalize 'h.264' / 'H264' / 'x264' / 'HEVC' / 'h265' /
    'x265' / 'AV1' / 'VP9' / 'ProRes' to a single canonical form."""
    if not text:
        return None
    m = CODEC_RE.search(text)
    if not m:
        return None
    raw = m.group(1).lower().replace(".", "")
    if raw in ("h265", "x265"):
        return "hevc"
    if raw in ("h264", "x264"):
        return "h264"
    return raw


def parse_fps(text: str) -> Optional[float]:
    """Pull '60fps' / '59.94 fps' / '24fps' out of text."""
    if not text:
        return None
    m = FPS_RE.search(text)
    return float(m.group("fps")) if m else None


CLICKABLE_SELECTOR_TAGS = (
    "a", "button", "[role='button']", "[onclick]",
    "[data-href]", "[data-url]", "[data-src]", "[data-download]",
)


def _candidate_url_from_element(el, base_url: str,
                                base_href: str = "") -> str:
    """Pull the most likely URL out of an element. Tries the
    URL-bearing attributes in order, then sniffs `onclick=` for an
    embedded literal. Resolves to absolute via decode_url."""
    for attr in URL_BEARING_ATTRS:
        val = el.get(attr)
        if val and val.strip() and val.strip() not in ("#", "javascript:;"):
            return decode_url(val, base_url=base_url, base_href=base_href)
    # onclick="window.location='/x.mp4'" etc.
    onclick = el.get("onclick") or ""
    if onclick:
        m = re.search(
            r"""['"]([^'"]+\.(?:mp4|mkv|webm|m4v|mov|zip|m3u8|mpd|pdf|"""
            r"""mp3|m4a|exe|dmg|iso))(?:[^'"]*)?['"]""",
            onclick, re.I,
        )
        if m:
            return decode_url(m.group(1), base_url=base_url,
                              base_href=base_href)
    return ""


def _element_descriptor(el) -> dict:
    """Stable structural fingerprint of an element — used to build
    a Playwright-style selector that survives DOM mutation."""
    desc = {
        "tag": el.name or "",
        "id": el.get("id") or "",
        "classes": [c for c in (el.get("class") or [])
                    if isinstance(c, str)],
        "text": el.get_text(" ", strip=True)[:160],
    }
    return desc


def _nearby_text(el, *, lookbehind: int = 2, lookahead: int = 2) -> str:
    """Free-text content drawn from the element itself, its parent,
    and a small window of sibling elements. Real-world resolution
    cards put the dimension on the button and the codec/fps/size on
    a sibling, so we have to merge both into one blob.

    Tradeoff: pulling sibling text helps the screenshot pattern but
    HURTS pages where multiple peer anchors sit at the same DOM
    level (each anchor's "nearby" then contains every other anchor's
    text — every link looks like "the 8K one"). We resolve that by
    only pulling sibling text when the immediate element's own text
    is THIN — a button containing just `7680 x 4320` needs help; an
    anchor containing the full phrase `4K Download · H264 · 60fps`
    does not.
    """
    own = el.get_text(" ", strip=True)
    if not own:
        own = ""
    # If the element already carries enough information by itself,
    # don't reach for siblings — that's where peer pollution comes
    # from (every anchor at the same DOM level sees every other
    # anchor's text and ranks against the loudest label). Any
    # resolution-shaped marker in own text is "enough":
    #   • explicit width × height,
    #   • a 'p' label (720p / 1080p / 2160p / 4320p),
    #   • a vocabulary label (4K, 8K, FHD, UHD).
    # Siblings still get pulled when own text is bare (a button
    # whose only content is an arrow icon and a dimension number
    # while the codec/fps/size lives one node over).
    own_self_sufficient = bool(
        RESOLUTION_RE.search(own)
        or P_LABEL_RE.search(own)
        or QUALITY_LABEL_RE.search(own)
    )
    if own_self_sufficient and (
        # but DO still pull siblings if own has only a dimension and
        # the metadata (codec/fps/size) is clearly elsewhere — this
        # is the screenshot pattern
        not (RESOLUTION_RE.search(own)
             and not (CODEC_RE.search(own) or SIZE_RE.search(own)
                      or FPS_RE.search(own)))
    ):
        return own

    parts: List[str] = [own]
    parent = el.parent
    if parent and hasattr(parent, "get_text"):
        parent_text = parent.get_text(" ", strip=True)
        # If the parent text is dominated by siblings (very long
        # relative to own_text), don't drag it in — that's the peer
        # pollution case.
        if parent_text and len(parent_text) <= max(len(own) * 4, 160):
            parts.append(parent_text)
        try:
            for sib in list(parent.find_next_siblings())[:lookahead]:
                t = sib.get_text(" ", strip=True)
                if t and len(t) <= 240:
                    parts.append(t)
            for sib in list(parent.find_previous_siblings())[:lookbehind]:
                t = sib.get_text(" ", strip=True)
                if t and len(t) <= 240:
                    parts.append(t)
        except Exception:
            pass
    return " ".join(p for p in parts if p)


def extract_resolution_cards(html: str, *, base_url: str = "") -> List[dict]:
    """Find every clickable element on the page whose text or
    sibling text contains an explicit width×height OR a quality
    label (8K / 4K / 2160p / FHD / etc.). Each becomes a candidate.

    Returns the list sorted highest-quality first. Caller decides
    whether to keep all of them (for a UI picker) or just the top.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    if not html or not html.strip():
        return []
    soup = BeautifulSoup(html, "html.parser")

    base_tag = soup.find("base", href=True)
    base_href = (base_tag.get("href") if base_tag else "") or ""

    selector = ",".join(CLICKABLE_SELECTOR_TAGS)
    candidates: List[dict] = []
    # Track each element's ancestors so we can drop duplicate
    # candidates that arise when a clickable wraps another clickable.
    # The OUTER candidate (the one with the resolved URL) is the keeper.
    visited_elements = []

    for el in soup.select(selector):
        blob = _nearby_text(el)
        # Quick reject: no resolution-shaped text anywhere nearby.
        if not (RESOLUTION_RE.search(blob)
                or QUALITY_LABEL_RE.search(blob)
                or P_LABEL_RE.search(blob)):
            continue
        res = detect_resolution_from_text(blob)
        if not res:
            continue
        url = _candidate_url_from_element(el, base_url, base_href)
        codec = parse_codec(blob)
        fps = parse_fps(blob)
        size = parse_size_bytes(blob)

        # Deduplicate against already-emitted candidates:
        # • Same URL → keep the first one (the outer/wrapping element
        #   is visited first; the inner button is a duplicate trigger
        #   for the same destination).
        # • Element is a descendant of an already-emitted element AND
        #   shares the same resolution label → drop (the parent's
        #   click takes us to the same destination).
        #
        # v3.66.10 fix: when the descendant DOES have a URL but the
        # parent was emitted as a click-only candidate (no URL on the
        # outer element), promote the inner's URL up to the parent.
        # Pre-fix the URL was lost — the parent's `requires_click`
        # path would then need a real click, but its `_selector_for(el)`
        # might point at a div that doesn't actually trigger download.
        is_dup = False
        for prev_el, prev_cand in visited_elements:
            if url and prev_cand.get("url") == url:
                is_dup = True
                break
            # Descendant check
            try:
                if prev_el in el.parents and \
                        prev_cand["resolution"]["label"] == res["label"]:
                    # Inner has a URL the parent lacked? Lift it.
                    if url and not prev_cand.get("url"):
                        prev_cand["url"] = url
                        prev_cand["requires_click"] = False
                        prev_cand.setdefault("reasons", []).append(
                            f"URL promoted from descendant "
                            f"<{el.name}>")
                        # Refresh the score so the URL bonus applies.
                        prev_cand["reasons"] = [
                            r for r in prev_cand["reasons"]
                            if r != "URL not in attributes — needs click"]
                        prev_cand["warnings"] = [
                            w for w in prev_cand.get("warnings", [])
                            if w != "URL not in attributes — needs click"]
                        prev_cand["score"] = prev_cand.get("score", 0) + 25
                    is_dup = True
                    break
            except Exception:
                pass
        if is_dup:
            continue

        cand = {
            "source_type": "resolution_download_card",
            "url": url or None,
            "click_selector": _selector_for(el),
            "width": res.get("width"),
            "height": res.get("height"),
            "resolution": {"label": res["label"], "rank": res["rank"]},
            "quality_label": res["label"],
            "codec": codec,
            "fps": fps,
            "size_bytes": size,
            "requires_click": not bool(url),
            "text": blob[:240],
            "reasons": [],
            "warnings": [],
            "score": 0,
        }
        _score_resolution_card(cand)
        candidates.append(cand)
        visited_elements.append((el, cand))

    # Highest-quality first. Tiebreak chain: resolution rank →
    # codec bonus → fps → bytes → has a real URL.
    candidates.sort(
        key=lambda c: (
            c["resolution"]["rank"],
            CODEC_BONUS.get(c.get("codec") or "", 0),
            c.get("fps") or 0,
            c.get("size_bytes") or 0,
            1 if c.get("url") else 0,
        ),
        reverse=True,
    )
    return candidates


def _score_resolution_card(c: dict) -> None:
    """In-place: fill c['score'] and append to c['reasons'] /
    c['warnings'] based on the parsed attributes. Used by
    extract_resolution_cards and by the master deep_detect scorer."""
    score = 0
    rank = c["resolution"]["rank"]
    label = c["resolution"]["label"]

    # Resolution — the dominant factor.
    if rank >= 8000:
        score += 140
        c["reasons"].append(f"{label} resolution detected")
    elif rank >= 6000:
        score += 120
        c["reasons"].append(f"{label} resolution detected")
    elif rank >= 5000:
        score += 110
        c["reasons"].append(f"{label} resolution detected")
    elif rank >= 4000:
        score += 100
        c["reasons"].append(f"{label} resolution detected")
    elif rank >= 1440:
        score += 55
        c["reasons"].append(f"{label} resolution detected")
    elif rank >= 1080:
        score += 35
        c["reasons"].append(f"{label} resolution detected")
    elif rank >= 720:
        score += 20
        c["reasons"].append(f"{label} resolution detected")

    codec = c.get("codec")
    if codec:
        bonus = CODEC_BONUS.get(codec, 0)
        if bonus:
            score += bonus
            c["reasons"].append(f"codec: {codec} (+{bonus})")

    fps = c.get("fps") or 0
    if fps >= 60:
        score += 20
        c["reasons"].append(f"{int(fps)}fps")
    elif fps >= 30:
        score += 10

    if c.get("size_bytes"):
        score += 5  # informative only; we don't bias toward bigger

    if c.get("url"):
        score += 25
        c["reasons"].append("direct URL on element")
    else:
        c["warnings"].append("URL not in attributes — needs click")

    c["score"] = score
