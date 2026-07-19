"""v3.43.69: dl8-video VR extractor + Badoink filename prediction.

# Why this module exists

`<dl8-video>` is a web component (originally from Doppio Labs) that
several adult-VR studios adopted as their player. The element family
has the SAME DOM structure across every site that uses it:

    <dl8-video title="..." poster="..." projection="equirectangular"
               stereo="sbs">
      <source src=".../SCENE_8k_180_180x180_3dh.mp4"
              quality="8K" type="video/mp4"/>
      <source src=".../SCENE_5k_180_180x180_3dh.mp4"
              quality="5K" type="video/mp4"/>
      ...
    </dl8-video>

So one extractor handles all of them. Confirmed sites (per XBVR's
open-source scrapers at github.com/xbapps/xbvr/blob/master/pkg/scrape/):

  - tmwvrnet.com   (TeenMegaWorld VR -- TMW Tour Pass family)
  - badoinkvr.com  (Badoink VR)
  - babevr.com
  - vrcosplayx.com
  - 18vr.com
  - realvr.com

The Badoink-family subset (the five non-TMW sites) has a SECOND useful
property: member-area URLs are PREDICTABLE from the public trailer URL.
You can strip the `_trailer` suffix and append a known list of quality
markers; HEAD-probe each candidate with your auth cookies and the
highest 200-OK wins. This means:

  1. We can extract from PUBLIC trailer pages (no login needed) to
     get the basename
  2. We can predict every member-area filename without scraping any
     member-area HTML
  3. Empty signed-URL games -- these URLs are NOT signed, just
     auth-gated

# Strategies

  - parse_dl8_video(html): pull `<dl8-video>` element + all `<source>`
    children with their `quality` attribute. Returns title, poster,
    and a list of (url, quality_int).
  - predict_badoink_filenames(trailer_url, max_tier): given any URL
    with a `_trailer` (or `_1m`/`_2m`/`_3m`) suffix, generate every
    member-area candidate.
  - extract_from_html(html, quality_pref): end-to-end dl8 walk +
    optional Badoink prediction handoff.

# Quality string mapping

The `quality` attribute is a string like `"8K"`, `"5K"`, `"4K"`,
`"HEVC"`, `"1080p"`. We map these to numeric pixel heights so the
existing quality_preference cascade can apply.

# Fail-open

Every failure mode returns either an empty list or
`Dl8Result(ok=False, error=...)`. Caller falls through to the
runner's standard teach path.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse, urlunparse

log = logging.getLogger(__name__)


# ─── Brand catalogs ─────────────────────────────────────────────────
#
# DL8_BRANDS = every site known to use <dl8-video>.
# BADOINK_BRANDS = subset where the filename-prediction trick works.
#
# Notes on TMW Tour Pass: wowgirls.com, vip4k.com, ultrafilms.com, and
# teenmegaworld.net all share the TMW Tour Pass backend. WowGirls/VIP4K/
# Ultrafilms use a non-dl8 player (the `WxH_NNFPS.mp4` pattern that
# tier_probe already handles via wowgirls_wxh). Only tmwvrnet.com (the
# VR-specific brand) uses dl8-video. We include only tmwvrnet here.

DL8_BRANDS: set = {
    "tmwvrnet.com",
    "badoinkvr.com",
    "babevr.com",
    "vrcosplayx.com",
    "18vr.com",
    "realvr.com",
}

BADOINK_BRANDS: set = {
    "badoinkvr.com",
    "babevr.com",
    "vrcosplayx.com",
    "18vr.com",
    "realvr.com",
}


def _etld1(hostname: str) -> str:
    """Last two dot-segments. All dl8 brand domains use simple .com TLDs."""
    if not hostname:
        return ""
    parts = hostname.lower().strip(".").split(".")
    if len(parts) < 2:
        return ""
    return ".".join(parts[-2:])


def is_dl8_url(url: str) -> bool:
    """True if URL host is in DL8_BRANDS. Never raises."""
    if not url or not isinstance(url, str):
        return False
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    return _etld1(host) in DL8_BRANDS


def is_badoink_url(url: str) -> bool:
    """True if URL host is in BADOINK_BRANDS. Never raises.

    Badoink hosts are a STRICT subset of DL8_BRANDS — tmwvrnet uses
    dl8-video but doesn't support the filename-prediction trick.
    """
    if not url or not isinstance(url, str):
        return False
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    return _etld1(host) in BADOINK_BRANDS


# ─── Quality string → numeric tier mapping ──────────────────────────


_QUALITY_LABEL_RE = re.compile(
    r"(?i)\b(\d+)\s*k\b|(\d{3,4})\s*p\b|\bHEVC\b|\b4K\s*HEVC\b",
)


def parse_quality_label(label: str) -> int:
    """Convert a quality label string ('8K', '5K', '1080p', 'HEVC') to
    pixel height. Returns 0 on unrecognized input.

    Mapping (approximate, matches industry convention):
        8K  → 4320
        7K  → 3160 (rare; some studios use this)
        6K  → 3160 (often interchangeable with 7K)
        5K  → 2880
        4K  → 2160
        4K HEVC → 2160 (HEVC encoding at 4K)
        3K  → 1620
        2K  → 1440
        1080p → 1080
        720p → 720
        ...

    The label may be embedded in junk text. We extract the first
    numeric+K or numeric+P pattern we find.
    """
    if not label or not isinstance(label, str):
        return 0
    # Direct numeric-K pattern
    m = re.search(r"(?i)\b(\d+)\s*k\b", label)
    if m:
        n = int(m.group(1))
        # Standard mapping
        if n == 8: return 4320
        if n == 7: return 3160
        if n == 6: return 3160
        if n == 5: return 2880
        if n == 4: return 2160
        if n == 3: return 1620
        if n == 2: return 1440
        # Unknown N-K: fall through
    # Numeric-P pattern
    m = re.search(r"(\d{3,4})\s*p\b", label, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    # HEVC alone (without resolution) — assume 4K which is the most
    # common HEVC encoding for VR adult content
    if re.search(r"(?i)\bHEVC\b", label):
        return 2160
    return 0


# ─── DOM parse: <dl8-video> ─────────────────────────────────────────


_DL8_TAG_RE = re.compile(
    r"<dl8-video\b([^>]*)>(.*?)</dl8-video\s*>",
    re.IGNORECASE | re.DOTALL,
)

_ATTR_RE = re.compile(
    r"""(\w[\w-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')""",
)

_SOURCE_TAG_RE = re.compile(
    # `[^>]*?` (not `[^/>]*?`) — URLs in src= attributes contain `/`.
    # The trailing `/?>` covers both `<source ... >` and `<source ... />`.
    r"<source\b([^>]*?)/?>", re.IGNORECASE,
)


def _parse_attrs(attr_str: str) -> dict:
    """Parse `name="value"` and `name='value'` pairs from an attribute
    string into a dict. Tolerant of weird whitespace."""
    out: dict = {}
    for m in _ATTR_RE.finditer(attr_str):
        key = m.group(1).lower()
        val = m.group(2) if m.group(2) is not None else m.group(3)
        # Decode common HTML entities — fine for our use case
        val = val.replace("&amp;", "&").replace("&quot;", '"')
        out[key] = val
    return out


@dataclass
class Dl8Source:
    """One <source> entry from inside <dl8-video>."""
    url: str
    quality_label: str
    quality_tier: int  # Numeric pixel height (0 if unparseable)
    mime_type: str = ""


@dataclass
class Dl8Parse:
    """Result of parse_dl8_video — what's IN the page DOM."""
    title: str = ""
    poster: str = ""
    projection: str = ""  # "equirectangular" / "fisheye" / etc.
    stereo: str = ""      # "sbs" / "tb" / "mono"
    sources: list = field(default_factory=list)  # list[Dl8Source]

    @property
    def best_tier(self) -> int:
        if not self.sources:
            return 0
        return max(s.quality_tier for s in self.sources)


def parse_dl8_video(html: str) -> Optional[Dl8Parse]:
    """Locate the first `<dl8-video>` element and parse its attributes
    + every `<source>` child.

    Returns Dl8Parse on success (even with empty sources), None if no
    `<dl8-video>` element is present.

    Multiple `<dl8-video>` elements: we return the FIRST. Pages usually
    have only one; pages with a trailer + main player can re-run this
    after navigating to the main one.
    """
    if not html or not isinstance(html, str):
        return None
    m = _DL8_TAG_RE.search(html)
    if not m:
        return None
    attrs = _parse_attrs(m.group(1))
    body = m.group(2)
    sources: list = []
    for sm in _SOURCE_TAG_RE.finditer(body):
        sattrs = _parse_attrs(sm.group(1))
        url = sattrs.get("src", "").strip()
        if not url:
            continue
        qlabel = sattrs.get("quality", "").strip()
        sources.append(Dl8Source(
            url=url,
            quality_label=qlabel,
            quality_tier=parse_quality_label(qlabel),
            mime_type=sattrs.get("type", ""),
        ))
    return Dl8Parse(
        title=attrs.get("title", "").strip(),
        poster=attrs.get("poster", "").strip(),
        projection=attrs.get("projection", "").strip(),
        stereo=attrs.get("stereo", "").strip(),
        sources=sources,
    )


# ─── Badoink filename prediction ────────────────────────────────────


# Strip these suffixes from a trailer URL's filename to get the basename.
# Order matters — longest first so `_trailer` is tried before `_1m`.
_TRAILER_SUFFIX_RE = re.compile(
    r"(_trailer|_3m|_2m|_1m)\.mp4(?:\?.*)?$",
    re.IGNORECASE,
)


# Per-tier suffix templates. Ordered DESCENDING by tier — first match
# wins. Each template gets formatted with the basename.
#
# Source: github.com/xbapps/xbvr/blob/master/pkg/scrape/badoink.go
# (the `filenames := []string{...}` declaration around line 178)

_BADOINK_TEMPLATES_8K = [
    # 8K (4320p)
    ("_8k_180_180x180_3dh.mp4", 4320),
    ("_8k_180_180x180_3dh_LR.mp4", 4320),
    # 7K (3160p — Badoink uses this for some scenes)
    ("_7k_180_180x180_3dh.mp4", 3160),
    ("_7k_180_180x180_3dh_LR.mp4", 3160),
    # 6K (~3160p — interchangeable with 7K mapping)
    ("_6k_180_180x180_3dh.mp4", 3160),
    ("_6k_180_180x180_3dh_LR.mp4", 3160),
    # 5K (2880p) — default for older Badoink scenes
    ("_5k_180_180x180_3dh.mp4", 2880),
    ("_5k_180_180x180_3dh_LR.mp4", 2880),
    # 4K HEVC (2160p)
    ("_4k_HEVC_180_180x180_3dh.mp4", 2160),
    ("_4k_HEVC_180_180x180_3dh_LR.mp4", 2160),
    # Device-specific encodings — these are typically smaller-screen
    # downsampled variants. Useful fallbacks.
    ("_samsung_180_180x180_3dh.mp4", 1440),
    ("_oculus_180_180x180_3dh.mp4", 1440),
    ("_samsung_180_180x180_3dh_LR.mp4", 1440),
    ("_oculus_180_180x180_3dh_LR.mp4", 1440),
    ("_ps4_pro_180_sbs.mp4", 1080),
    ("_ps4_180_sbs.mp4", 720),
    ("_mobile_180_180x180_3dh.mp4", 720),
    ("_mobile_180_180x180_3dh_LR.mp4", 720),
]


@dataclass
class BadoinkCandidate:
    """One predicted member-area URL with its expected tier."""
    url: str
    tier: int
    suffix: str  # The template used (for debugging)


def predict_badoink_filenames(
    trailer_url: str,
    max_tier_hint: int = 0,
) -> list:
    """Given a trailer URL, return a list of BadoinkCandidate in
    DESCENDING tier order — every plausible member-area filename.

    `max_tier_hint`: if > 0, candidates ABOVE this tier are skipped.
    Use this when the public page advertised "5K" (don't bother
    probing 8K — it won't exist).

    Returns empty list if the URL doesn't have a stripable trailer
    suffix.

    The caller is responsible for HEAD-probing each candidate with
    auth cookies and picking the first 200 OK.
    """
    if not trailer_url or not isinstance(trailer_url, str):
        return []
    try:
        parsed = urlparse(trailer_url)
    except Exception:
        return []
    path = parsed.path or ""
    # Find and strip a trailer suffix
    m = _TRAILER_SUFFIX_RE.search(path)
    if not m:
        return []
    base_path = path[:m.start()]
    candidates: list = []
    for suffix, tier in _BADOINK_TEMPLATES_8K:
        if max_tier_hint > 0 and tier > max_tier_hint:
            continue
        new_path = base_path + suffix
        cand_url = urlunparse((
            parsed.scheme, parsed.netloc, new_path,
            parsed.params, "", parsed.fragment,  # drop query
        ))
        candidates.append(BadoinkCandidate(
            url=cand_url, tier=tier, suffix=suffix,
        ))
    return candidates


# ─── Quality-tag detection (for max_tier_hint) ──────────────────────


_QUALITY_TAG_RE = re.compile(
    # Captures the class attribute value AND the tag text. We split the
    # class value on whitespace and check membership rather than using
    # \b — `\b` treats `-` as a word boundary, so `\bvideo-tag\b` would
    # match `not-video-tag` (matching `video-tag` as a sub-token at the
    # `-` boundary), which is wrong.
    r'<[^>]*class=["\']([^"\']*)["\'][^>]*>([^<]+)<',
    re.IGNORECASE,
)


def detect_max_quality_tag(html: str) -> int:
    """Look for `<a class="video-tag">8K</a>` etc. on the page. Returns
    the highest numeric tier mentioned across all matching tags, or 0
    if no matches found.

    Used as `max_tier_hint` for Badoink prediction — if the page says
    "5K" we don't bother HEAD-probing 8K (will 404 and waste a request).
    """
    if not html:
        return 0
    best = 0
    for m in _QUALITY_TAG_RE.finditer(html):
        # Strict class-list check: `video-tag` must be a whole entry
        # in the space-separated class attribute, not a substring.
        classes = m.group(1).split()
        if "video-tag" not in classes:
            continue
        tier = parse_quality_label(m.group(2))
        if tier > best:
            best = tier
    return best


# ─── End-to-end ─────────────────────────────────────────────────────


@dataclass
class Dl8Result:
    """Final extraction outcome."""
    ok: bool
    url: str = ""
    tier: int = 0
    title: str = ""
    poster: str = ""
    via: str = ""              # "dl8_source" / "badoink_predict"
    badoink_candidates: list = field(default_factory=list)  # for caller HEAD-probing
    available_tiers: list = field(default_factory=list)
    error: str = ""
    error_detail: str = ""


def _pick_source(
    sources: list, quality_pref: Optional[list],
) -> Optional[Dl8Source]:
    """Pick a Dl8Source from `sources` honoring `quality_pref` cascade.
    Same proportional tolerance as the runner."""
    if not sources:
        return None
    # Sort descending tier
    sorted_sources = sorted(sources, key=lambda s: s.quality_tier, reverse=True)
    if not quality_pref:
        return sorted_sources[0]
    for pref in quality_pref:
        if isinstance(pref, str) and pref.lower() in ("best", "highest", "max"):
            return sorted_sources[0]
        try:
            target = int(str(pref).rstrip("p"))
        except (ValueError, TypeError):
            continue
        tolerance = max(50, int(target * 0.05))
        matches = [s for s in sorted_sources
                   if abs(s.quality_tier - target) <= tolerance]
        if matches:
            # Highest within tolerance
            return max(matches, key=lambda s: s.quality_tier)
    # No pref matched → highest
    return sorted_sources[0]


def extract_from_html(
    html: str,
    *,
    page_url: str = "",
    quality_pref: Optional[list] = None,
    predict_badoink: bool = True,
) -> Dl8Result:
    """End-to-end dl8 extraction.

    `page_url` is the URL the HTML came from (used to detect whether
    Badoink filename prediction applies).

    `predict_badoink`: when True AND the page_url is a Badoink-network
    host AND the dl8 sources look like trailers, return the list of
    member-area candidates in `badoink_candidates`. Caller HEAD-probes
    them. When False or hostname doesn't match, just return the best
    dl8 source URL directly.

    On any failure returns Dl8Result(ok=False, error=...). Never raises.
    """
    if not html or not isinstance(html, str):
        return Dl8Result(ok=False, error="empty_html")
    parsed = parse_dl8_video(html)
    if parsed is None:
        return Dl8Result(ok=False, error="no_dl8_element")
    if not parsed.sources:
        return Dl8Result(ok=False, error="no_sources_in_dl8",
                         title=parsed.title, poster=parsed.poster)

    available_tiers = sorted({s.quality_tier for s in parsed.sources
                              if s.quality_tier > 0}, reverse=True)

    # Path B: Badoink filename prediction.
    if predict_badoink and page_url and is_badoink_url(page_url):
        # The "is this a trailer URL?" check: any source URL with a
        # _trailer / _Nm suffix indicates we're on a public page.
        any_trailer = any(
            _TRAILER_SUFFIX_RE.search(s.url) for s in parsed.sources
        )
        if any_trailer:
            # Take the first trailer URL as the basename source
            trailer_url = next(
                (s.url for s in parsed.sources
                 if _TRAILER_SUFFIX_RE.search(s.url)),
                None,
            )
            if trailer_url:
                max_hint = detect_max_quality_tag(html)
                candidates = predict_badoink_filenames(
                    trailer_url, max_tier_hint=max_hint,
                )
                if candidates:
                    return Dl8Result(
                        ok=True,
                        url="",  # caller HEAD-probes the candidates
                        tier=candidates[0].tier,  # best HOPED-FOR tier
                        title=parsed.title,
                        poster=parsed.poster,
                        via="badoink_predict",
                        badoink_candidates=candidates,
                        available_tiers=[c.tier for c in candidates],
                    )

    # Path A: pick the best <source>
    src = _pick_source(parsed.sources, quality_pref)
    if src is None or not src.url:
        return Dl8Result(ok=False, error="no_pickable_source")

    return Dl8Result(
        ok=True,
        url=src.url,
        tier=src.quality_tier,
        title=parsed.title,
        poster=parsed.poster,
        via="dl8_source",
        available_tiers=available_tiers,
    )


def extract_from_page(
    page,
    *,
    quality_pref: Optional[list] = None,
    predict_badoink: bool = True,
) -> Dl8Result:
    """Pull HTML from a live Playwright page and run extract_from_html.

    The page_url is sourced from `page.url` so caller doesn't need to
    pass it separately.
    """
    try:
        html = page.content()
        page_url = page.url
    except Exception as e:
        return Dl8Result(ok=False, error="page_content_failed",
                         error_detail=f"{type(e).__name__}: {e}")
    return extract_from_html(
        html, page_url=page_url, quality_pref=quality_pref,
        predict_badoink=predict_badoink,
    )


# ─── HEAD-probe helper for Badoink candidates ───────────────────────


def probe_badoink_candidates(
    candidates: list,
    *,
    user_agent: str = "",
    referer: str = "",
    cookies: Optional[dict] = None,
    timeout_s: float = 6.0,
    max_attempts: int = 8,
) -> Optional[BadoinkCandidate]:
    """HEAD-probe each candidate in descending tier order; return the
    first one that 200s (or 206 via Range GET if HEAD is 405).

    Fail-open: returns None if every probe fails or `httpx` is
    unavailable. Caller falls through to the runner's standard path.

    `max_attempts` caps total HEAD requests. Some Badoink CDNs charge
    for excessive requests; default of 8 is enough to cover the
    main 8K/6K/5K/4K variants (8 templates) without wasting on the
    mobile/console fallbacks.
    """
    if not candidates:
        return None
    try:
        import httpx
    except ImportError:
        log.info("dl8: httpx not installed; can't HEAD-probe Badoink")
        return None
    headers: dict = {}
    if user_agent:
        headers["User-Agent"] = user_agent
    if referer:
        headers["Referer"] = referer
    try:
        with httpx.Client(timeout=timeout_s, follow_redirects=True,
                          cookies=cookies or None) as client:
            for i, cand in enumerate(candidates):
                if i >= max_attempts:
                    break
                try:
                    r = client.head(cand.url, headers=headers)
                    status = r.status_code
                except httpx.RequestError:
                    continue
                except Exception:
                    continue
                if status == 200:
                    return cand
                if status == 405:
                    # HEAD not allowed; try Range GET
                    try:
                        r2 = client.get(cand.url, headers={
                            **headers, "Range": "bytes=0-0",
                        })
                        if r2.status_code in (200, 206):
                            return cand
                    except Exception:
                        continue
                # 401 / 403: cookies don't authorize this URL;
                # don't keep probing higher tiers either (same auth
                # error will repeat). Bail.
                if status in (401, 403):
                    log.info(
                        "dl8: badoink probe got %s on %s -- "
                        "auth issue; aborting cascade",
                        status, cand.suffix,
                    )
                    return None
                # 404 / 5xx / other: continue
    except Exception as e:
        log.info("dl8: probe_badoink_candidates raised %s", e)
        return None
    return None


__all__ = [
    "DL8_BRANDS",
    "BADOINK_BRANDS",
    "Dl8Source",
    "Dl8Parse",
    "Dl8Result",
    "BadoinkCandidate",
    "is_dl8_url",
    "is_badoink_url",
    "parse_quality_label",
    "parse_dl8_video",
    "predict_badoink_filenames",
    "detect_max_quality_tag",
    "extract_from_html",
    "extract_from_page",
    "probe_badoink_candidates",
]
