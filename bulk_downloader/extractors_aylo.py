"""v3.43.66: Aylo (MindGeek) network flashvars extractor.

# Why this module exists

Aylo (formerly MindGeek) operates the largest premium adult network:
Brazzers, RealityKings, BangBros, Mofos, DigitalPlayground, Babes,
Twistys, PornHubPremium, Tiny4K, plus ~10 other brands. **Every Aylo
scene page exposes its stream URLs in the same global JavaScript
object** — `flashvars_<embedId>` — whose `mediaDefinitions` array
contains every quality variant (MP4 progressive + HLS master playlist).

Before this module, BD treated each Aylo brand as a separate template
with hand-rolled CSS selectors. Workers got whatever tier the player
loaded by default (often 480p or 720p), and the scoring code had to
guess at variant URLs from `<video>` `src` attributes.

After this module, BD:

  1. Recognizes any Aylo brand URL via hostname pattern
  2. Pulls the page HTML once (still needs Cloudflare-cleared cookies)
  3. Regex-extracts the `var flashvars_<id> = {...};` JSON blob
  4. Walks `mediaDefinitions[]` to pick the highest-quality variant
     that matches the user's `quality_preference` cascade
  5. Returns a normalized result that the runner can dispatch to
     HLS (if HLS) or direct HTTP (if MP4)

The pattern has been stable since ~2015 (documented in the Magic-PH
userscript, used by EchterAlsFake/phub, and by every other open-
source MindGeek scraper of note).

# What the flashvars object looks like

```html
<script>
var flashvars_259384716 = {
    "video_url": "...",
    "video_title": "...",
    "video_duration": 1820,
    "video_unavailable_country": false,
    "mediaDefinitions": [
        {
            "defaultQuality": false,
            "format": "hls",
            "videoUrl": "https://ev-h.phncdn.com/.../master.m3u8?validfrom=...&validto=...&hash=...",
            "quality": ["480","720","1080"],
            "remote": true
        },
        {
            "defaultQuality": false,
            "format": "mp4",
            "videoUrl": "https://ev-h.phncdn.com/.../scene_1080p.mp4?validfrom=...",
            "quality": "1080",
            "remote": false
        },
        ...
    ],
    "image_url": "...",
    "actTags": [...]
};
</script>
```

Notes:
  - `quality` is sometimes a string ("1080") and sometimes a list
    (["480","720","1080"]) — HLS variants carry the list, MP4
    variants carry the string.
  - `videoUrl` may be empty for tiers the user doesn't have access
    to (e.g. 4K on a non-premium subscription). Such entries must
    be skipped.
  - `defaultQuality: true` marks the variant the player would auto-
    select if no preference is set. We ignore this — we have our
    own preference cascade.
  - The signed URL TTL is typically 6 hours. Don't cache results
    longer than that.

# Auth + Cloudflare

Aylo is behind Cloudflare with WAF. The page that mints the flashvars
URL needs to be loaded by an authenticated browser with valid session
cookies. Once we have the URL, segment fetches usually work with
`curl_cffi` (TLS impersonation) plus the same cookies — the page-load
challenge is the hardest part. Our existing Playwright path handles
that; this module just runs AFTER the page is loaded.

# Brand hostname matcher

Aylo's CDN domain (`*.phncdn.com`, `ev-h.phncdn.com`, etc.) is shared
across all brands, but the SCENE page lives on the brand's own
hostname. We match scene-page hostnames; the CDN hostname in the
output URL is whatever the flashvars block says.

Known brand hostname patterns (confirmed live as of 2026-05):

    brazzers.com / brazzersnetwork.com / brazzersplus.com
    realitykings.com
    bangbros.com / bangbrosnetwork.com
    mofos.com
    digitalplayground.com
    babes.com
    twistys.com
    pornhubpremium.com / www.pornhubpremium.com
    tiny4k.com (now Aylo-backed after Pure Play Media absorption)
    dorcelclub.com (some scenes)
    wankz.com / wankzvr.com (some scenes)

Plus sister-brand subdomains: `members.<brand>.com`,
`site-ma.<brand>.com`, `site-rk.<brand>.com`, etc. We match
`<aylo-brand>.com` as a suffix on the eTLD+1 so subdomain variants
work automatically.

# Fail-open semantics

Every failure path returns an `AyloResult(ok=False, error=...)`:

  - Hostname doesn't match any Aylo brand
  - Page HTML doesn't contain `var flashvars_`
  - flashvars JSON doesn't parse (sites mutate sometimes)
  - mediaDefinitions array is empty or has no usable variants
  - Every variant's videoUrl is empty (premium-only on free account)
  - quality_pref doesn't match anything

The runner falls through to its existing dispatch chain (qB/JD/teach)
on any failure. No download is ever blocked by this module.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

log = logging.getLogger(__name__)


# ─── Hostname matcher ───────────────────────────────────────────────
#
# We match the eTLD+1 (e.g. `brazzers.com`) so `site-ma.brazzers.com`,
# `members.brazzers.com`, `www.brazzers.com` all hit. The list is
# conservative — we'd rather miss an unknown sister brand than match
# a non-Aylo site that happens to share a name fragment.

AYLO_BRANDS: set = {
    "brazzers.com",
    "brazzersnetwork.com",
    "brazzersplus.com",
    "realitykings.com",
    "bangbros.com",
    "bangbrosnetwork.com",
    "mofos.com",
    "digitalplayground.com",
    "babes.com",
    "twistys.com",
    "pornhubpremium.com",
    "tiny4k.com",
    "dorcelclub.com",
    "wankz.com",
    "wankzvr.com",
    # Aylo also owns these (added based on Aug 2023 brand consolidation
    # — verify per-site since some have changed backends):
    "men.com",
    "milfhunter.com",
    "spyfam.com",
    "mommygotboobs.com",
    "teenslovehugecocks.com",
}


def _etld1(hostname: str) -> str:
    """Crude eTLD+1 — last two dot-segments. Good enough for Aylo
    domains which all use simple TLDs. Not a real PSL implementation."""
    if not hostname:
        return ""
    parts = hostname.lower().strip(".").split(".")
    if len(parts) < 2:
        return ""
    return ".".join(parts[-2:])


def is_aylo_url(url: str) -> bool:
    """Return True if `url`'s hostname looks like an Aylo brand domain.

    Matches the eTLD+1, so subdomains like `site-ma.brazzers.com`,
    `members.brazzers.com`, `www.brazzers.com` all return True.

    Never raises — invalid input returns False."""
    if not url or not isinstance(url, str):
        return False
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    return _etld1(host) in AYLO_BRANDS


# ─── flashvars extraction ───────────────────────────────────────────
#
# The flashvars object can appear with several syntactic shells:
#
#   var flashvars_259384716 = {...};
#   window.flashvars_259384716 = {...};
#   flashvars_259384716 = {...};
#
# And occasionally minified with linefeeds stripped. We use a regex
# that's tolerant of the `var`/`window.`/no-prefix variations and
# uses a non-greedy match up to the SEMICOLON that follows the
# closing brace at the same nesting level.
#
# Single regex isn't quite enough — JSON contains nested `{}` so we
# can't just match `\{[\s\S]+?\}`. Strategy:
#   1. Find the start position of `flashvars_<digits> = {`
#   2. Walk forward, counting brace depth, until depth hits 0
#   3. Slice + JSON.parse
#
# This is the same strategy phub and the Magic-PH userscript use.

_FLASHVARS_RE = re.compile(
    r"(?:var\s+|window\.|self\.)?flashvars_(\d+)\s*=\s*(\{)",
    re.MULTILINE,
)


def _find_matching_brace(text: str, start_idx: int) -> int:
    """Given `text` and an index pointing AT a `{`, return the index
    AFTER its matching `}` (so text[start_idx:returned_idx] yields the
    full JSON object). Returns -1 if no match (malformed).

    Handles string literals (so `{` inside a JSON string doesn't count)
    but does NOT handle JS regex literals or template strings — those
    don't appear in our flashvars JSON so it's safe.
    """
    if start_idx >= len(text) or text[start_idx] != "{":
        return -1
    depth = 0
    i = start_idx
    in_string = False
    string_char = ""
    escape = False
    while i < len(text):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == string_char:
                in_string = False
        else:
            if ch in ('"', "'"):
                in_string = True
                string_char = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i + 1  # AFTER the closing brace
        i += 1
    return -1


def extract_flashvars(html: str) -> Optional[dict]:
    """Find the first `flashvars_<id>` block in `html` and parse it.

    Returns the parsed dict on success, None on:
      - No flashvars block found
      - JSON parse failed (rare; means upstream changed format)
      - Block found but contained no `mediaDefinitions` key (not the
        block we wanted)

    Never raises.

    Some pages contain MULTIPLE flashvars blocks (one per embedded
    video — trailer + main scene). We return the FIRST one that
    contains `mediaDefinitions` and a non-empty list, which is
    almost always the main scene.
    """
    if not html or not isinstance(html, str):
        return None
    for m in _FLASHVARS_RE.finditer(html):
        brace_start = m.start(2)  # position of the `{`
        brace_end = _find_matching_brace(html, brace_start)
        if brace_end == -1:
            log.debug("aylo: flashvars_%s had unbalanced braces", m.group(1))
            continue
        blob = html[brace_start:brace_end]
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError as e:
            log.debug("aylo: flashvars_%s JSON parse failed: %s",
                      m.group(1), e)
            continue
        if not isinstance(obj, dict):
            continue
        # Must have mediaDefinitions to be useful
        mediadefs = obj.get("mediaDefinitions")
        if not mediadefs or not isinstance(mediadefs, list):
            continue
        # Sometimes the FIRST flashvars block is for a trailer (small
        # mediaDefinitions) and the SECOND is for the main scene. Try
        # to detect: prefer blocks where ANY variant has a 1080+ quality.
        # If first one has low qualities only, keep looking and fall
        # back to the first if none better is found.
        max_q = _max_numeric_quality(mediadefs)
        if max_q >= 1080:
            return obj
        # Stash the candidate and keep looking
        # (handled by falling through; we'll return last seen in fallback)
        candidate = obj
    # If we got here we may have had only low-quality blocks. Re-scan
    # and return the FIRST one with any mediaDefinitions.
    for m in _FLASHVARS_RE.finditer(html):
        brace_start = m.start(2)
        brace_end = _find_matching_brace(html, brace_start)
        if brace_end == -1:
            continue
        try:
            obj = json.loads(html[brace_start:brace_end])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("mediaDefinitions"):
            return obj
    return None


def _max_numeric_quality(mediadefs: list) -> int:
    """Return the highest numeric quality across all variants. 0 if
    nothing parseable."""
    best = 0
    for md in mediadefs:
        if not isinstance(md, dict):
            continue
        q = md.get("quality", "")
        if isinstance(q, list):
            for qx in q:
                try:
                    n = int(str(qx).rstrip("p"))
                    if n > best:
                        best = n
                except (ValueError, TypeError):
                    pass
        elif isinstance(q, (str, int)):
            try:
                n = int(str(q).rstrip("p"))
                if n > best:
                    best = n
            except (ValueError, TypeError):
                pass
    return best


# ─── Variant selection ──────────────────────────────────────────────


@dataclass
class AyloVariant:
    """One pickable variant from the mediaDefinitions array."""
    format: str          # "hls" or "mp4"
    url: str             # The signed video URL
    quality: int         # Numeric height (1080, 720, etc.); 0 if HLS multi-tier
    quality_list: list = field(default_factory=list)  # for HLS, the tier list
    is_premium: bool = False  # Some variants are gated
    is_default: bool = False  # Player's default selection


@dataclass
class AyloResult:
    """Final extraction outcome."""
    ok: bool
    variant: Optional[AyloVariant] = None
    # On success:
    title: str = ""
    duration_sec: int = 0
    thumbnail_url: str = ""
    embed_id: str = ""
    available_qualities: list = field(default_factory=list)
    # On failure:
    error: str = ""
    error_detail: str = ""


def _normalize_variant(md: dict) -> Optional[AyloVariant]:
    """Convert one mediaDefinitions entry to AyloVariant. Returns None
    if the entry isn't usable (missing URL, unknown format, etc.)."""
    if not isinstance(md, dict):
        return None
    url = (md.get("videoUrl") or md.get("video_url") or "").strip()
    if not url:
        return None  # Premium-locked or other reason
    fmt = (md.get("format") or "").lower()
    if fmt not in ("hls", "mp4"):
        # Some entries have no format key; infer from URL extension
        if url.endswith(".m3u8") or "/master.m3u8" in url:
            fmt = "hls"
        elif url.endswith(".mp4") or ".mp4?" in url:
            fmt = "mp4"
        else:
            return None
    raw_q = md.get("quality", "")
    quality = 0
    quality_list: list = []
    if isinstance(raw_q, list):
        for qx in raw_q:
            try:
                quality_list.append(int(str(qx).rstrip("p")))
            except (ValueError, TypeError):
                pass
        # For HLS the per-variant `quality` is a LIST of tiers in the
        # manifest; we use max for sorting purposes
        if quality_list:
            quality = max(quality_list)
    elif isinstance(raw_q, (str, int)):
        try:
            quality = int(str(raw_q).rstrip("p"))
        except (ValueError, TypeError):
            quality = 0
    return AyloVariant(
        format=fmt,
        url=url,
        quality=quality,
        quality_list=quality_list,
        is_premium=bool(md.get("premium") or md.get("requirePremium")),
        is_default=bool(md.get("defaultQuality")),
    )


def pick_best_variant(
    mediadefs: list,
    quality_pref: Optional[list] = None,
    force_format: str = "",
) -> Optional[AyloVariant]:
    """Pick the best variant from `mediadefs` honoring `quality_pref`.

    `quality_pref` is a list of integer heights in priority order, e.g.
    `[4320, 2160, 1080, 720]` for 8K-first. The special string "best"
    in the list (or `None`/empty pref) means "take the highest tier".

    `force_format` (optional) restricts to "hls" or "mp4" — useful for
    users who want resume support (MP4 supports Range; HLS doesn't
    natively).

    Returns the best matching variant or None if nothing in the list
    is usable.

    Algorithm:
      1. Normalize every entry to AyloVariant; drop None
      2. Filter by force_format if set
      3. If quality_pref is None/empty/contains "best": return highest
      4. Otherwise: for each pref in order, find the closest match
         (exact > within ±5% > below). First match wins.
    """
    if not mediadefs:
        return None
    variants = [v for v in (_normalize_variant(md) for md in mediadefs)
                if v is not None]
    if force_format:
        variants = [v for v in variants if v.format == force_format.lower()]
    if not variants:
        return None
    # Default: pick the highest-quality variant. Ties broken by
    # preferring MP4 (resume) over HLS (no resume).
    def _sort_key(v: AyloVariant):
        return (v.quality, 1 if v.format == "mp4" else 0)
    variants.sort(key=_sort_key, reverse=True)
    # No preference: take the top sorted entry
    if not quality_pref or any(
        isinstance(p, str) and p.lower() in ("best", "highest", "max")
        for p in quality_pref
    ):
        return variants[0]
    # Otherwise walk the preference cascade
    for pref in quality_pref:
        try:
            target = int(str(pref).rstrip("p"))
        except (ValueError, TypeError):
            continue
        tolerance = max(50, int(target * 0.05))  # Match runner's logic
        # Exact-or-within-tolerance match
        matches = [v for v in variants
                   if abs(v.quality - target) <= tolerance]
        if matches:
            # Among matches, pick the highest (and MP4 over HLS for ties)
            return max(matches, key=_sort_key)
    # No preference matched: return the highest variant we have
    return variants[0]


# ─── End-to-end ─────────────────────────────────────────────────────


def extract_from_html(
    html: str,
    *,
    quality_pref: Optional[list] = None,
    force_format: str = "",
) -> AyloResult:
    """Parse `html` and return the best variant per `quality_pref`.

    Convenience function for callers who already have the page HTML
    (e.g. from `page.content()` in Playwright, or a captured fixture).
    """
    if not html:
        return AyloResult(ok=False, error="empty_html")
    fv = extract_flashvars(html)
    if fv is None:
        return AyloResult(ok=False, error="no_flashvars",
                          error_detail="no flashvars_<id> block found")
    mediadefs = fv.get("mediaDefinitions") or []
    if not mediadefs:
        return AyloResult(ok=False, error="empty_mediadefs")
    variant = pick_best_variant(mediadefs, quality_pref=quality_pref,
                                force_format=force_format)
    if variant is None:
        return AyloResult(ok=False, error="no_usable_variant",
                          error_detail="all variants had empty videoUrl "
                                       "or didn't match preferences")
    # Build the available_qualities list for diagnostics
    seen_q: set = set()
    avail: list = []
    for md in mediadefs:
        v = _normalize_variant(md)
        if v is None:
            continue
        key = (v.quality, v.format)
        if key in seen_q:
            continue
        seen_q.add(key)
        avail.append(f"{v.quality}p/{v.format}")
    return AyloResult(
        ok=True,
        variant=variant,
        title=str(fv.get("video_title", "") or ""),
        duration_sec=int(fv.get("video_duration", 0) or 0),
        thumbnail_url=str(fv.get("image_url", "") or ""),
        embed_id=str(fv.get("video_id", "") or ""),
        available_qualities=avail,
    )


def extract_from_page(
    page,
    *,
    quality_pref: Optional[list] = None,
    force_format: str = "",
) -> AyloResult:
    """Extract from a live Playwright page. Same as `extract_from_html`
    but pulls the HTML via `page.content()`.

    Caller is responsible for ensuring the page is fully loaded and the
    flashvars block has been rendered (some Aylo brands lazy-load it
    on play-button click — caller should click play first, OR pass a
    pre_scrape_action that does so).
    """
    try:
        html = page.content()
    except Exception as e:
        return AyloResult(ok=False, error="page_content_failed",
                          error_detail=f"{type(e).__name__}: {e}")
    return extract_from_html(html, quality_pref=quality_pref,
                             force_format=force_format)


__all__ = [
    "AYLO_BRANDS",
    "AyloResult",
    "AyloVariant",
    "is_aylo_url",
    "extract_flashvars",
    "pick_best_variant",
    "extract_from_html",
    "extract_from_page",
]
