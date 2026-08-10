"""v3.43.67: Vixen Media Group extractor.

# Why this module exists

Vixen Media Group operates ten sister sites that all share infrastructure
— Vixen, Blacked, Tushy, Deeper, Slayed, Wifey, MilfyMD, VixenPlus,
BlackedRaw, TushyRaw. They run a Next.js SPA with a GraphQL backend
served from `cdn.vixen.com`, `cdn.blacked.com`, `cdn.tushy.com`, etc.
The CDN URL pattern is uniform across the network:

    https://cdn.<brand>.com/video/mp4_<tier>/<scene-id>/<ts>/<NAME>_<TIER>P.mp4
                                  ^^^^^^^^^^                ^^^^^^^^^
                                  swappable                 redundant tier marker

The recon survey (2026-05-14) showed:
  - The `<video src>` element loaded by the player already contains
    one tier's signed CDN URL (typically `mp4_480` — the player picks
    a conservative default).
  - Next.js server-renders the initial state into a
    `<script id="__NEXT_DATA__" type="application/json">` tag, which
    on some pages carries the full manifest URL with the highest tier
    pre-selected.
  - The GraphQL endpoint at `/api/graphql` returns the full per-scene
    record including a `videoManifest` field with all tier URLs, but
    is gated by aggressive Cloudflare WAF.

# Strategy: three paths, fail-open

This module implements three extraction paths and falls through in
order:

  Path A — `__NEXT_DATA__` parse: Find the script tag, JSON.parse the
    content, walk `props.pageProps.video.videoSrc` (path varies — we
    try several known shapes). When this works it gives us the title
    + duration + thumbnail + manifest URL in ONE shot, no extra
    requests, no Cloudflare drama.

  Path B — `<video src>` scrape with metadata: Walk the rendered DOM
    for a `<video>` element whose src matches a Vixen CDN host.
    Combine with `og:title` / `og:image` / page title for the
    metadata bits.

  Path C — GraphQL POST (v3.66.680): Last resort. POST the `findOneVideo`
    query to `/api/graphql` through the live page's request context
    (`page.request`), so the page's cookies + Origin + any Cloudflare
    clearance ride along. The response record is walked by the same
    find_video_record machinery as Path A. Fail-open: any error returns
    ok=False and the runner falls through to the teach-based path.

# Brand hostnames

Confirmed live: members.vixen.com, members.blacked.com,
members.tushy.com, members.deeper.com, members.slayed.com,
members.wifey.com, members.milfymd.com, members.vixenplus.com,
members.blackedraw.com, members.tushyraw.com.

The CDN hostnames (cdn.vixen.com etc.) appear in the video URLs but
not as the page hostname. We match on the PAGE hostname.

# Integration with tier-probe (v3.43.65)

Once we have a URL with a `mp4_<N>/` path segment, we can hand it to
tier_probe.probe_higher_tiers() with the `vixen_network` preset
pattern to upgrade `mp4_480` → `mp4_2160` if the signed URL allows.
The runner already does this — this module just produces the initial
URL.

# Fail-open

If all three paths fail, the runner falls through to the standard
teach-based find_best_download path. Vixen sites already work today
via that path (just at whatever tier the player loaded — typically
480p). This module is a quality upgrade, not a correctness
requirement.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

log = logging.getLogger(__name__)


# ─── Brand list ─────────────────────────────────────────────────────
#
# All ten share infrastructure. We match the eTLD+1.

VIXEN_BRANDS: set = {
    "vixen.com",
    "blacked.com",
    "tushy.com",
    "deeper.com",
    "slayed.com",
    "wifey.com",
    "milfymd.com",
    "vixenplus.com",
    "blackedraw.com",
    "tushyraw.com",
}


def _etld1(hostname: str) -> str:
    """The registrable domain of `hostname`, or "" when there is none.

    v3.66.1018: delegates to bulk_downloader.registrable_domain. The previous
    last-two-labels join returned the PUBLIC SUFFIX for any multi-part one --
    co.uk for www.bbc.co.uk -- so two unrelated registrants read as one domain.

    THE EMPTY RETURN FOR A SINGLE-LABEL HOST IS PRESERVED DELIBERATELY. The
    canonical rule returns the host itself there; this function has always
    returned "", and that is a different contract, not a bug this cut owns.
    Collapsing the two would make `localhost` a registrable domain for every
    caller that tests the result for truth.
    """
    if not hostname:
        return ""
    from .registrable_domain import registrable_domain
    rd = registrable_domain(hostname)
    return rd if "." in rd else ""


def is_vixen_url(url: str) -> bool:
    """True if `url` is a Vixen Media Group brand domain (including
    members. and www. subdomains). Never raises."""
    if not url or not isinstance(url, str):
        return False
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    return _etld1(host) in VIXEN_BRANDS


# ─── __NEXT_DATA__ extraction ───────────────────────────────────────


# The script tag has a fixed id/type but the JSON content is huge — we
# want to slice it out cheaply without parsing the whole HTML.
_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def extract_next_data(html: str) -> Optional[dict]:
    """Find <script id="__NEXT_DATA__"> and JSON-parse its content.

    Returns the parsed dict on success, None on:
      - Script tag not present
      - JSON parse failed

    Never raises.
    """
    if not html or not isinstance(html, str):
        return None
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return None
    blob = m.group(1).strip()
    if not blob:
        return None
    try:
        return json.loads(blob)
    except json.JSONDecodeError as e:
        log.debug("vixen: __NEXT_DATA__ JSON parse failed: %s", e)
        return None


# ─── Walking __NEXT_DATA__ for the video record ─────────────────────
#
# The path to the video record varies across Vixen brands and across
# page types. Known shapes (from XBVR scrapers + CommunityScrapers):
#
#   props.pageProps.video                  — most direct
#   props.pageProps.videoData              — some pages
#   props.pageProps.findOneVideo           — GraphQL passthrough
#   props.pageProps.scene                  — older naming
#   props.pageProps.data.video             — variant
#   props.pageProps.initialApolloState.<key>  — Apollo Client cache
#
# Rather than hard-coding all shapes, we walk the tree looking for an
# object that has the fields we need: a title + a video URL (key may
# be `videoSrc`, `videoUrl`, `videoManifestUrl`, `manifestUrl`,
# `streamUrl`, or a nested `sources` / `videoSources` / `media[].sources`
# array).


_VIDEO_URL_KEYS = (
    "videoSrc", "videoUrl", "video_url",
    "videoManifestUrl", "manifestUrl", "manifest_url",
    "streamUrl", "stream_url",
    "hlsManifestUrl", "hls_manifest_url",
    # Plain `src` and `url` — common in Apollo cache shapes and in
    # `sources` arrays. Both narrowed by the Vixen-CDN host check so
    # adding them doesn't broaden false positives.
    "src", "url",
)


def _looks_like_vixen_cdn(url: str) -> bool:
    """True if URL points to a Vixen-network CDN host."""
    if not url or not isinstance(url, str):
        return False
    return bool(re.search(
        r"https?://(?:[a-z0-9-]+\.)?(?:vixen|blacked|tushy|deeper|"
        r"slayed|wifey|milfymd|vixenplus|blackedraw|tushyraw|vixencdn)\.com",
        url, re.IGNORECASE,
    ))


def find_video_record(node, depth=0, max_depth=12) -> Optional[dict]:
    """Walk a parsed __NEXT_DATA__ tree looking for an object that
    looks like the video record. Returns the first matching dict.

    Heuristic: a dict that contains at least one of:
      - A direct URL key (videoSrc, manifestUrl, etc.) pointing at a
        Vixen CDN
      - A `sources` / `videoSources` list with URL entries
      - A `media` list with nested sources

    AND at least one of (title, name, videoTitle).
    """
    if depth > max_depth:
        return None
    if isinstance(node, dict):
        # Check this node first
        if _is_video_record(node):
            return node
        # Recurse into values
        for v in node.values():
            r = find_video_record(v, depth + 1, max_depth)
            if r is not None:
                return r
    elif isinstance(node, list):
        for item in node:
            r = find_video_record(item, depth + 1, max_depth)
            if r is not None:
                return r
    return None


def _is_video_record(d: dict) -> bool:
    """True if dict looks like a video record."""
    if not isinstance(d, dict):
        return False
    has_title = any(k in d for k in ("title", "videoTitle", "name"))
    if not has_title:
        return False
    # Direct URL hit
    for k in _VIDEO_URL_KEYS:
        v = d.get(k)
        if isinstance(v, str) and _looks_like_vixen_cdn(v):
            return True
    # Nested sources array
    for sk in ("sources", "videoSources", "encodings", "media"):
        arr = d.get(sk)
        if isinstance(arr, list) and arr:
            for entry in arr:
                if isinstance(entry, dict):
                    for k in _VIDEO_URL_KEYS:
                        v = entry.get(k)
                        if isinstance(v, str) and _looks_like_vixen_cdn(v):
                            return True
    return False


def _collect_urls_from_record(record: dict) -> list:
    """Pull every Vixen-CDN URL from a video record. Returns a list of
    (url, tier_hint) tuples; tier_hint is the integer height if known
    (from a `resolution`/`height`/`quality` sibling field) or 0."""
    urls: list = []
    # Top-level URL keys
    for k in _VIDEO_URL_KEYS:
        v = record.get(k)
        if isinstance(v, str) and _looks_like_vixen_cdn(v):
            urls.append((v, _tier_from_url(v) or 0))
    # Nested arrays
    for sk in ("sources", "videoSources", "encodings", "media"):
        arr = record.get(sk)
        if not isinstance(arr, list):
            continue
        for entry in arr:
            if not isinstance(entry, dict):
                continue
            # The entry MAY have nested sources too (HereSphere/DeoVR
            # style: media[].sources[]).
            sub = entry.get("sources") or entry.get("videoSources") or []
            if isinstance(sub, list):
                for s in sub:
                    if not isinstance(s, dict):
                        continue
                    for k in _VIDEO_URL_KEYS:
                        v = s.get(k)
                        if isinstance(v, str) and _looks_like_vixen_cdn(v):
                            tier = (_int_or_zero(s.get("resolution"))
                                    or _int_or_zero(s.get("height"))
                                    or _int_or_zero(s.get("quality"))
                                    or _tier_from_url(v) or 0)
                            urls.append((v, tier))
            # The entry itself may carry a URL
            for k in _VIDEO_URL_KEYS:
                v = entry.get(k)
                if isinstance(v, str) and _looks_like_vixen_cdn(v):
                    tier = (_int_or_zero(entry.get("resolution"))
                            or _int_or_zero(entry.get("height"))
                            or _int_or_zero(entry.get("quality"))
                            or _tier_from_url(v) or 0)
                    urls.append((v, tier))
    return urls


def _int_or_zero(v) -> int:
    if v is None:
        return 0
    try:
        return int(str(v).rstrip("p"))
    except (ValueError, TypeError):
        return 0


_TIER_FROM_URL_RE = re.compile(r"mp4_(\d{3,4})|/(\d{3,4})[pP]\.mp4|_(\d{3,4})[pP]\.mp4")


def _tier_from_url(url: str) -> int:
    """Extract a tier value from a Vixen CDN URL path segment."""
    if not url:
        return 0
    m = _TIER_FROM_URL_RE.search(url)
    if not m:
        return 0
    for g in m.groups():
        if g:
            try:
                return int(g)
            except ValueError:
                pass
    return 0


# ─── Video src fallback ─────────────────────────────────────────────


_VIDEO_TAG_RE = re.compile(
    r'<video[^>]*\bsrc=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


def find_video_src_in_html(html: str) -> Optional[str]:
    """Look for a <video src="..."> with a Vixen CDN URL.

    Returns the URL on success, None otherwise. Doesn't parse the
    full DOM — a regex on the rendered HTML is enough for this
    fallback path. Returns the FIRST matching URL.
    """
    if not html:
        return None
    for m in _VIDEO_TAG_RE.finditer(html):
        url = m.group(1)
        # HTML entities — &amp; → &
        url = url.replace("&amp;", "&")
        if _looks_like_vixen_cdn(url):
            return url
    return None


_TITLE_TAG_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.IGNORECASE)
_OG_RE = re.compile(
    r'<meta[^>]+property=["\']og:([^"\']+)["\'][^>]*content=["\']([^"\']*)["\']',
    re.IGNORECASE,
)


def extract_metadata_from_html(html: str) -> dict:
    """Pull title, thumbnail, description from `<meta property="og:*">`
    and `<title>` as a fallback when __NEXT_DATA__ isn't available.

    Returns a dict with whatever keys we found: title, image, description.
    Never raises."""
    out: dict = {}
    if not html:
        return out
    m = _TITLE_TAG_RE.search(html)
    if m:
        out["title"] = m.group(1).strip()
    for m in _OG_RE.finditer(html):
        prop = m.group(1).lower()
        content = m.group(2)
        if prop == "title" and not out.get("title"):
            out["title"] = content
        elif prop == "image":
            out["image"] = content
        elif prop == "description":
            out["description"] = content
        elif prop == "video:duration":
            try:
                out["duration"] = int(content)
            except ValueError:
                pass
    return out


# ─── Result type + end-to-end ───────────────────────────────────────


@dataclass
class VixenResult:
    """Final extraction outcome."""
    ok: bool
    url: str = ""              # The chosen video URL (may need tier-probe upgrade)
    is_hls: bool = False       # True if url is an .m3u8
    title: str = ""
    duration_sec: int = 0
    thumbnail_url: str = ""
    description: str = ""
    tier: int = 0              # Best tier discovered (0 if unknown)
    available_tiers: list = field(default_factory=list)
    via: str = ""              # "next_data" / "video_src" / ""
    error: str = ""
    error_detail: str = ""


def _build_result_from_record(
    record: dict, via: str, quality_pref: Optional[list] = None,
) -> Optional[VixenResult]:
    """Turn a matched video record into a VixenResult. Shared by the
    __NEXT_DATA__ path (via='next_data') and the GraphQL path
    (via='graphql'). Returns None if the record yields no Vixen CDN URL."""
    urls = _collect_urls_from_record(record)
    if not urls:
        return None
    seen_tiers: set = set()
    avail: list = []
    for _u, t in urls:
        if t > 0 and t not in seen_tiers:
            seen_tiers.add(t)
            avail.append(t)
    avail.sort(reverse=True)
    picked_url, picked_tier = _pick_url_by_preference(urls, quality_pref)
    title = (record.get("title")
             or record.get("videoTitle")
             or record.get("name") or "")
    duration = (_int_or_zero(record.get("duration"))
                or _int_or_zero(record.get("videoLength"))
                or _int_or_zero(record.get("durationSec"))
                or 0)
    thumb = (record.get("poster")
             or record.get("thumbnail")
             or record.get("thumbnailUrl")
             or record.get("posterUrl") or "")
    if isinstance(thumb, dict):
        thumb = thumb.get("url", "") or ""
    description = (record.get("description")
                  or record.get("synopsis") or "")
    return VixenResult(
        ok=True,
        url=picked_url,
        is_hls=picked_url.endswith(".m3u8") or "/master.m3u8" in picked_url,
        title=str(title)[:255],
        duration_sec=duration,
        thumbnail_url=str(thumb)[:1024],
        description=str(description)[:1024],
        tier=picked_tier,
        available_tiers=avail,
        via=via,
    )


def extract_from_html(
    html: str,
    *,
    quality_pref: Optional[list] = None,
) -> VixenResult:
    """Run the three-path extraction strategy against `html`.

    Path A: __NEXT_DATA__ parse
    Path B: <video src> fallback
    (Path C: GraphQL POST — not implemented in v3.43.67; needs live page)

    Returns VixenResult(ok=False) if nothing yielded a Vixen CDN URL.

    `quality_pref` is a list of integer heights in priority order.
    If provided, we pick the URL whose embedded tier matches the
    preference cascade. Otherwise: highest available.
    """
    if not html or not isinstance(html, str):
        return VixenResult(ok=False, error="empty_html")

    # ── Path A: __NEXT_DATA__ ──────────────────────────────────────
    nd = extract_next_data(html)
    if nd is not None:
        record = find_video_record(nd)
        if record is not None:
            res = _build_result_from_record(record, "next_data", quality_pref)
            if res is not None:
                return res

    # ── Path B: <video src> fallback ───────────────────────────────
    video_src = find_video_src_in_html(html)
    if video_src:
        meta = extract_metadata_from_html(html)
        tier = _tier_from_url(video_src)
        return VixenResult(
            ok=True,
            url=video_src,
            is_hls=video_src.endswith(".m3u8") or "/master.m3u8" in video_src,
            title=meta.get("title", "")[:255],
            duration_sec=int(meta.get("duration", 0) or 0),
            thumbnail_url=meta.get("image", "")[:1024],
            description=meta.get("description", "")[:1024],
            tier=tier,
            available_tiers=[tier] if tier > 0 else [],
            via="video_src",
        )

    # ── Path C: not implemented ───────────────────────────────────
    return VixenResult(ok=False, error="no_extractor_path",
                       error_detail="no __NEXT_DATA__ and no <video src> "
                                    "matching Vixen CDN found")


def _pick_url_by_preference(
    urls: list, quality_pref: Optional[list],
) -> tuple:
    """Pick (url, tier) from a list of (url, tier) tuples honoring the
    preference cascade. Same logic as runner._apply_quality_preference
    but standalone so we can use it without a SiteRunner instance.

    Tolerance: max(50, target * 0.05). When no preference matches or
    quality_pref is empty/None: return the highest-tier entry.
    """
    if not urls:
        return "", 0
    # Dedup on URL (some records list the same URL twice)
    seen: set = set()
    unique: list = []
    for u, t in urls:
        if u in seen:
            continue
        seen.add(u)
        unique.append((u, t))
    # Sort by tier descending for fallback
    unique.sort(key=lambda ut: ut[1], reverse=True)
    if not quality_pref:
        return unique[0]
    for pref in quality_pref:
        if isinstance(pref, str) and pref.lower() in ("best", "highest", "max"):
            return unique[0]
        try:
            target = int(str(pref).rstrip("p"))
        except (ValueError, TypeError):
            continue
        tolerance = max(50, int(target * 0.05))
        matches = [ut for ut in unique
                   if abs(ut[1] - target) <= tolerance]
        if matches:
            # Highest within tolerance wins
            return max(matches, key=lambda ut: ut[1])
    # No pref matched — return highest
    return unique[0]


# ─── Path C: GraphQL POST (v3.66.680) ───────────────────────────────
#
# Last-resort path when neither __NEXT_DATA__ nor a <video src> yields a
# CDN URL. We POST the findOneVideo query to /api/graphql through the live
# page's request context (page.request) so the page's cookies, origin, and
# any Cloudflare clearance ride along -- no separate HTTP client, no new
# module-level import edge. The response record is walked by the same
# find_video_record / _collect_urls machinery as Path A.

_GRAPHQL_FIND_ONE_VIDEO = (
    "query findOneVideo($site: Site!, $slug: String!) {"
    " findOneVideo(input: {site: $site, slug: $slug}) {"
    " title runtime releaseDate"
    " videoManifestUrl"
    " sources { src height width quality }"
    " } }"
)


def build_graphql_query(url: str) -> dict:
    """Build the GraphQL POST body for a Vixen scene URL. The scene slug is
    the last path segment; `site` is left generic (the server resolves it
    from the Origin header)."""
    slug = ""
    try:
        path = (urlparse(url).path or "").rstrip("/")
        slug = path.rsplit("/", 1)[-1] if path else ""
    except Exception:
        slug = ""
    return {
        "operationName": "findOneVideo",
        "query": _GRAPHQL_FIND_ONE_VIDEO,
        "variables": {"slug": slug, "site": ""},
    }


def extract_from_graphql_response(
    payload: dict, *, quality_pref: Optional[list] = None,
) -> VixenResult:
    """Extract a VixenResult from an already-fetched GraphQL response dict.
    Pure (no network) -- walks the payload for the video record. Returns
    VixenResult(ok=False) when no Vixen CDN URL is present."""
    if not isinstance(payload, dict) or not payload:
        return VixenResult(ok=False, error="graphql_empty")
    record = find_video_record(payload)
    if record is None:
        return VixenResult(ok=False, error="graphql_no_record")
    res = _build_result_from_record(record, "graphql", quality_pref)
    if res is None:
        return VixenResult(ok=False, error="graphql_no_url")
    return res


def extract_via_graphql(
    page, *, quality_pref: Optional[list] = None,
) -> VixenResult:
    """POST the findOneVideo query via the live page's request context and
    extract from the response. Fail-open: any error returns ok=False."""
    try:
        page_url = getattr(page, "url", "") or ""
        body = build_graphql_query(page_url)
        origin = ""
        try:
            p = urlparse(page_url)
            if p.hostname:
                origin = f"{p.scheme or 'https'}://{p.hostname}"
        except Exception:
            origin = ""
        endpoint = (origin + "/api/graphql") if origin else "/api/graphql"
        headers = {"content-type": "application/json",
                   "x-requested-with": "XMLHttpRequest"}
        if origin:
            headers["origin"] = origin
        resp = page.request.post(endpoint, data=json.dumps(body), headers=headers)
        payload = resp.json()
    except Exception as e:
        return VixenResult(ok=False, error="graphql_request_failed",
                           error_detail=f"{type(e).__name__}: {e}")
    return extract_from_graphql_response(payload, quality_pref=quality_pref)


def extract_from_page(
    page,
    *,
    quality_pref: Optional[list] = None,
) -> VixenResult:
    """Run the full three-path strategy against a live Playwright page:
    __NEXT_DATA__ / <video src> from page HTML, then GraphQL POST fallback."""
    html = None
    try:
        html = page.content()
    except Exception:
        html = None
    if html:
        res = extract_from_html(html, quality_pref=quality_pref)
        if res.ok:
            return res
    # Path C: GraphQL POST fallback (uses page.request context)
    return extract_via_graphql(page, quality_pref=quality_pref)


__all__ = [
    "VIXEN_BRANDS",
    "VixenResult",
    "is_vixen_url",
    "extract_next_data",
    "find_video_record",
    "find_video_src_in_html",
    "extract_metadata_from_html",
    "extract_from_html",
    "extract_from_page",
    "build_graphql_query",
    "extract_from_graphql_response",
    "extract_via_graphql",
]
