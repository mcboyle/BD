"""v3.43.44: Heuristic candidate scoring engine.

This module is the canonical implementation of the rule-based scoring
that picks download candidates from a page's elements. It's used by:

  1. `learn.py`'s teach-panel JS (mirrored in JavaScript; see
     `scoreCandidates()` there — kept aligned via the tests)
  2. The upcoming v3.43.45 paste-HTML template extractor which runs
     this scorer directly on a BeautifulSoup tree

## What changed vs. the pre-v3.43.44 scoring

Old:
  - +40 for ANY resolution mention (1080p and 8K tied)
  - +25 for download-flavored text
  - +30 for .mp4/.mkv/.webm in href/data
  - +20 for video/source tags
  - +15 for data-* attrs
  - −50 for ad/share/comment/login
  - −10 for text >80 chars

New (this module):

  **A. Tiered resolution scoring** — instead of a flat +40, each
     resolution maps to a value: 8K→80, 4K→60, 1080→40, 720→25.
     Highest tier wins ties naturally; per-site min_resolution
     preferences now map directly.

  **B. Filesize signal** — parse "5.7 GB" / "230 MB" / "12 KB" from
     surrounding text. GB-scale +30, hundreds-of-MB +20,
     KB-scale −20 (a "Download" button next to "12 KB" is a poster).

  **C. Ancestor context** — walk up 3-4 parents, check class/id for
     'download | mirror | source | quality | versions | get' for +25;
     'related | recommended | sidebar | comments | nav' for −40. The
     negative-ancestor signal is the single biggest win for sites with
     a related-videos rail next to the player.

  **D. URL pattern fingerprinting** — after one successful download,
     record the hostname+path-prefix. Next time, give +30 to any
     candidate whose href matches that prefix. Self-tuning per site.

  **E. Position/shape** — above-the-fold gets +10, large click target
     (≥80px wide, ≥30px tall) gets +15. Useful for sites that hide
     real CTAs in tiny gear-icon dropdowns vs giant "Download" buttons.

  **F. Combinatorial bonuses** — `download` + resolution within 20
     chars of each other gets +20 on top of individual scores
     (catches the strong "Download 4K MP4" pattern).

  **G. Expanded negatives** — `report/flag/abuse` (−60),
     `next episode/prev/related` (−50), `embed/copy link` (−45),
     `trailer/preview/sample` (−40 unless res ≥ 4K), `screenshot/poster`
     (−30). These are HIGH-WEIGHT because they're confusable with real
     CTAs ("Download trailer" looks like "Download" but isn't what we
     want).

## Design

This is a stateless scoring engine: given a serializable "candidate
record" + optional URL fingerprint state, return a score. No DOM
access — that's the caller's job. This means the same engine can
score:
  - Live DOM elements (JS-side, via the existing teach panel)
  - Pasted HTML (Python-side, via BeautifulSoup in v3.43.45)
  - Stored DB rows (for replay/debugging)

## Data shapes

The scorer takes a `Candidate` dict:
  {
    'tag':       'a' | 'button' | 'video' | 'source' | ...
    'text':      str, visible inner text (capped to 200 chars)
    'href':      str, the href attribute
    'data_href': str, data-href
    'data_url':  str, data-url
    'data_src':  str, data-src
    'data_download': str, data-download
    'id':        str, element id
    'class_name': str, space-separated class list
    'ancestor_classes': str, concatenated class/id of 3-4 ancestors
                       (caller's job to walk the tree)
    'surrounding_text': str, ±80 chars around the element (for
                       filesize parsing)
    'rect':      {'top': int, 'width': int, 'height': int}, viewport
                 metrics from getBoundingClientRect() — optional
    'viewport_height': int — used for above-the-fold check
  }

And a `FingerprintState` (optional, per-site):
  {
    'known_hosts': set[str], CDN hostnames we've seen succeed
    'known_path_prefixes': set[str], path prefixes we've seen succeed
  }

Returns a `ScoreResult`:
  {
    'score': int (negative scores possible — caller decides cutoff)
    'reasons': list of (delta, label) pairs explaining the score
    'resolution_tier': int (the resolution VALUE — used for
                       per-site min_resolution comparison;
                       0 if no resolution detected)
    'estimated_size_bytes': int (0 if no size mentioned)
  }
"""
# Load-bearing invariants tagged inline as # INV-<ID>; see DANGER_MAP.md.
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


# ── Resolution tiers (A) ─────────────────────────────────────────────


# Map a resolution mention to a tier value. Higher tier = more
# desirable. The tier value doubles as a sort key.
#
# We deliberately use rounded integers that map to a "real" height
# in pixels — that way per-site min_resolution=1080 comparisons
# work without any conversion.
RESOLUTION_TIERS: List[Tuple[re.Pattern, int, str]] = [  # INV-005
    # v3.43.54: comprehensive resolution detection. Covers every
    # height a real adult/video site has been seen to serve, plus
    # the standard cinema/broadcast formats. The tier value is a
    # rough pixel-height bucket — higher = better. detect.py's
    # res_score() walks a separate but consistent list.
    #
    # IMPORTANT: patterns are scanned in declaration order; we take
    # the HIGHEST matching tier. Put bigger heights first so a
    # string like "Download 4K (also has 1080p)" picks 4K.
    #
    # v3.43.65: added CDN-path-segment patterns surfaced by the
    # 2026-05-13 recon survey of 20 live-player dumps. These
    # supplement the labelled-tier patterns: many sites embed the
    # height in the URL as `mp4_2160` or `_VIDEO_2160P.mp4` and
    # never use the word "4K" anywhere user-visible.
    #
    # Word-boundary subtlety: `_` is a word character in Python's
    # default \w set, so `\b_2160P\b` doesn't match inside `..._2160P.mp4`
    # (the boundary between `1` and `_` is word→word, not a \b). For
    # the URL-shape patterns we use lookbehind/lookahead instead of
    # \b so they fire inside paths like `mp4_2160/` or `_VIXEN_2160P.mp4`.

    # ── 8K class ──────────────────────────────────────────────────
    # 7680×4320 is the canonical 8K UHD.
    (re.compile(
        r"\b(8\s*[kK]|7680\s*[x×]\s*4320|4320p?)\b"
        r"|(?:^|[/_-])mp4_4320(?:[/_.-]|$)"
        r"|(?:^|[/_-])(?:VIDEO_)?4320[pP](?:[/_.-]|$)"
        r"|(?:^|[/_-])stream_mp4_4320(?:[/_.-]|$)"
    ), 80, "8K"),

    # ── 6K class ──────────────────────────────────────────────────
    # 5760×3240 is the canonical 6K UHD.
    # Wowgirls/VIP4K serve 5568×3132 (an HEVC-friendly mod-16 6K).
    # Some sites also list 6144×3160 (DCI-like 6K) or 6016×3384.
    (re.compile(r"\b(6\s*[kK]|"
                  r"5760\s*[x×]\s*3240|"
                  r"5568\s*[x×]\s*3132|"
                  r"6144\s*[x×]\s*3160|"
                  r"6016\s*[x×]\s*3384|"
                  r"3132p?|3160p?|3240p?|3384p?)\b"), 70, "6K"),

    # ── 5K class ──────────────────────────────────────────────────
    # 5120×2880 is the canonical iMac-style 5K.
    # 5120×2160 is "5K cinema" (DCI 2160 width-stretched).
    (re.compile(r"\b(5\s*[kK]|"
                  r"5120\s*[x×]\s*2880|"
                  r"5120\s*[x×]\s*2160|"
                  r"4096\s*[x×]\s*2160|"  # DCI 4K — close enough to 5K to
                                            # treat as a separate tier from
                                            # UHD 4K (3840×2160). Some sites
                                            # label this as "4K Cinema".
                  r"2880p?)\b"), 65, "5K"),

    # ── 4K class ──────────────────────────────────────────────────
    # 3840×2160 is UHD 4K.
    # v3.43.65: added Vixen-style /mp4_2160/ + _VIDEO_2160P.mp4 + the
    # `_uhd` lowercase label seen on a few Aylo network CDNs.
    (re.compile(
        r"\b(4\s*[kK]|UHD|3840\s*[x×]\s*2160|2160p?)\b"
        r"|(?:^|[/_-])_?uhd(?:[/_.-]|$)"
        r"|(?:^|[/_-])mp4_2160(?:[/_.-]|$)"
        r"|(?:^|[/_-])(?:VIDEO_)?2160[pP](?:[/_.-]|$)"
        r"|(?:^|[/_-])stream_mp4_2160(?:[/_.-]|$)"
    ), 60, "4K"),

    # ── 1440p / 2K class ──────────────────────────────────────────
    # v3.43.65: added _qhd lowercase label
    (re.compile(
        r"\b(2\s*[kK]|QHD|2560\s*[x×]\s*1440|1440p?)\b"
        r"|(?:^|[/_-])_?qhd(?:[/_.-]|$)"
        r"|(?:^|[/_-])mp4_1440(?:[/_.-]|$)"
        r"|(?:^|[/_-])1440[pP](?:[/_.-]|$)"
    ), 50, "1440p"),

    # ── 1200p (uncommon but seen on older surveillance / VR) ──────
    (re.compile(r"\b(1920\s*[x×]\s*1200|1200p?)\b"), 45, "1200p"),

    # ── 1080p / FullHD ────────────────────────────────────────────
    # v3.43.65: added Vixen/VIP4K/Bang patterns
    (re.compile(
        r"\b(1920\s*[x×]\s*1080|1080p?|Full\s*HD|FHD)\b"
        r"|(?:^|[/_-])_?fhd(?:[/_.-]|$)"
        r"|(?:^|[/_-])mp4_1080(?:[/_.-]|$)"
        r"|(?:^|[/_-])1080[pP](?:[/_.-]|$)"
        r"|(?:^|[/_-])stream_mp4_1080(?:[/_.-]|$)"
    ), 40, "1080p"),

    # ── 900p (HD+ — uncommon) ─────────────────────────────────────
    (re.compile(r"\b(1600\s*[x×]\s*900|900p?)\b"), 32, "900p"),

    # ── 720p / HD ─────────────────────────────────────────────────
    # v3.43.65: added Vixen/PornPros patterns
    (re.compile(
        r"\b(1280\s*[x×]\s*720|720p?|HD)\b"
        r"|(?:^|[/_-])mp4_720(?:[/_.-]|$)"
        r"|(?:^|[/_-])720[pP](?:[/_.-]|$)"
        r"|(?:^|[/_-])stream_mp4_720(?:[/_.-]|$)"
    ), 25, "720p"),

    # ── 540p — quarter-HD, common for "preview" streams ───────────
    (re.compile(r"\b(960\s*[x×]\s*540|540p?)\b"), 18, "540p"),

    # ── 480p / SD ─────────────────────────────────────────────────
    # v3.43.65: added Vixen mp4_480 / VIXEN_*_480P shape
    (re.compile(
        r"\b(854\s*[x×]\s*480|640\s*[x×]\s*480|480p?|SD)\b"
        r"|(?:^|[/_-])mp4_480(?:[/_.-]|$)"
        r"|(?:^|[/_-])(?:VIDEO_)?480[pP](?:[/_.-]|$)"
        r"|(?:^|[/_-])stream_mp4_480(?:[/_.-]|$)"
    ), 10, "480p"),

    # ── 360p (mobile-era leftovers) ───────────────────────────────
    (re.compile(r"\b(640\s*[x×]\s*360|360p?)\b"), 6, "360p"),

    # ── 240p (lowest practical) ───────────────────────────────────
    (re.compile(r"\b(426\s*[x×]\s*240|240p?)\b"), 3, "240p"),

    # ── v3.43.65: Vixen "_353P" preview-thumb tier ────────────────
    # Vixen serves a 353-pixel-tall thumbnail tier alongside the real
    # video. It is literally smaller than 480p but the URL contains
    # "_353P" which res_score() would otherwise pick up as 353p. We
    # want this to LOSE every contest against a real variant.
    # Score=2 puts it below 240p but above 0 (so it's still detected,
    # which helps debugging — "why did we pick the 353P file?").
    (re.compile(r"(?:^|[/_-])353[pP](?:[/_.-]|$)"), 2, "353p (preview)"),
]


def detect_resolution_tier(text: str) -> Tuple[int, str]:
    """Return (tier_value, label). Highest-tier match wins when
    multiple appear (e.g. "Download 1080p / 720p" picks 1080)."""
    if not isinstance(text, str):
        return 0, ""
    best_tier = 0
    best_label = ""
    for pattern, tier, label in RESOLUTION_TIERS:
        if pattern.search(text):
            if tier > best_tier:
                best_tier = tier
                best_label = label
    return best_tier, best_label


# ── Codec-quality tiers (F1) ─────────────────────────────────────────
# A SMALL additive bonus that breaks ties BETWEEN encodes at the SAME
# resolution (AV1 1080p vs H.264 1080p), without ever overriding the
# resolution ranking. The max bonus (4) is strictly less than the
# smallest adjacent RESOLUTION_TIERS gap among REAL download tiers
# (720p=25 and up, where the smallest gap is 5), so a better codec at a
# lower real resolution can NEVER outrank a higher one — preserving the
# load-bearing "resolution beats everything else" caveat. (The only
# sub-5 gaps are between junk preview tiers — 353p=2, 240p=3, 360p=6,
# 480p=10 — where codec scoring is irrelevant in practice.)
#
# remux/source encodes tie with AV1 at the top (4): a remux IS the
# source bitstream and AV1 is the best transcode, both "as good as it
# gets" for our tie-break purposes.
#
# Scanned in declaration order; highest matching tier wins.
CODEC_TIERS: List[Tuple[re.Pattern, int, str]] = [
    # Special: remux / source / original encodes — the untranscoded
    # bitstream. Top codec bonus (4), capped at the AV1 level so it
    # stays strictly below a real-resolution step (see below).
    (re.compile(r"\b(remux|prores|source|original|lossless)\b", re.I),
     4, "remux/source"),
    # AV1 — best modern transcode codec at a given bitrate.
    (re.compile(r"\b(av1|av01)\b", re.I), 4, "AV1"),
    # HEVC / H.265.
    (re.compile(r"\b(hevc|h\.?\s*265|x265)\b", re.I), 3, "HEVC"),
    # VP9.
    (re.compile(r"\b(vp9|vp09)\b", re.I), 2, "VP9"),
    # H.264 / AVC — the baseline modern codec.
    (re.compile(r"\b(h\.?\s*264|avc|x264)\b", re.I), 1, "H.264"),
]


def detect_codec_tier(text: str) -> Tuple[int, str]:
    """Return (bonus, label) for the best codec mentioned in *text*.

    Returns (0, "") if no codec is recognised. The bonus is a SMALL
    additive value (1-5) meant only to rank encodes within the same
    resolution — it can never override the resolution tier (see
    CODEC_TIERS rationale)."""
    if not isinstance(text, str):
        return 0, ""
    best_bonus = 0
    best_label = ""
    for pattern, bonus, label in CODEC_TIERS:
        if pattern.search(text):
            if bonus > best_bonus:
                best_bonus = bonus
                best_label = label
    return best_bonus, best_label


# ── Filesize parsing (B) ─────────────────────────────────────────────


_SIZE_PATTERN = re.compile(
    r"\b(\d+(?:[.,]\d+)?)\s*(B|KB|KiB|MB|MiB|GB|GiB|TB|TiB)\b",
    re.IGNORECASE,
)
_SIZE_MULTIPLIERS = {
    "B": 1,
    "KB": 1024, "KIB": 1024,
    "MB": 1024**2, "MIB": 1024**2,
    "GB": 1024**3, "GIB": 1024**3,
    "TB": 1024**4, "TIB": 1024**4,
}


def parse_filesize_bytes(text: str) -> int:
    """Extract the FIRST filesize mention from text. Returns 0 if
    none found. Handles SI (KB/MB/GB) and IEC (KiB/MiB/GiB)."""
    if not isinstance(text, str) or not text:
        return 0
    m = _SIZE_PATTERN.search(text)
    if not m:
        return 0
    try:
        # Some locales use comma as decimal separator
        num = float(m.group(1).replace(",", "."))
    except ValueError:
        return 0
    unit = m.group(2).upper()
    mult = _SIZE_MULTIPLIERS.get(unit, 0)
    if mult <= 0:
        return 0
    return int(num * mult)


def score_filesize(size_bytes: int) -> Tuple[int, str]:
    """Return (score_delta, label) for the given filesize. A "Download"
    button next to "12 KB" is a thumbnail, not a video; one next to
    "5.7 GB" is the real thing."""
    if size_bytes <= 0:
        return 0, ""
    # KB scale — almost certainly not a video
    if size_bytes < 5 * 1024 * 1024:  # < 5 MB
        return -20, f"tiny size ({_fmt_bytes(size_bytes)})"
    # Small MB scale — could be a low-res clip but more likely a
    # preview or sample
    if size_bytes < 50 * 1024 * 1024:  # < 50 MB
        return -5, f"small ({_fmt_bytes(size_bytes)})"
    # Medium MB — plausible 720p
    if size_bytes < 500 * 1024 * 1024:  # < 500 MB
        return 10, f"medium ({_fmt_bytes(size_bytes)})"
    # Hundreds of MB — plausible 1080p
    if size_bytes < 2 * 1024**3:
        return 20, f"plausible video ({_fmt_bytes(size_bytes)})"
    # GB scale — definitely a video file
    return 30, f"GB-scale ({_fmt_bytes(size_bytes)})"


def _fmt_bytes(n: int) -> str:
    """Compact human-readable size, for debug labels in reasons."""
    if n < 1024:
        return f"{n}B"
    if n < 1024**2:
        return f"{n/1024:.1f}KB"
    if n < 1024**3:
        return f"{n/1024**2:.1f}MB"
    return f"{n/1024**3:.1f}GB"


# ── Ancestor context scoring (C) ─────────────────────────────────────


# Tokens that, when found in any ancestor's class/id, are strong
# positive context signals: "the parent container is labeled as
# download stuff"
_ANCESTOR_POSITIVE = re.compile(
    r"\b(download|mirror|source|quality|version|versions|"
    r"get|resolutions|file-list|dl-list|stream-list|"
    r"player-controls|video-actions|media-actions)\b",
    re.IGNORECASE,
)

# Tokens that are strong NEGATIVE context. These are the single
# biggest win — a "Download" link inside a "related-videos" rail
# is the false positive that wastes the most time.
_ANCESTOR_NEGATIVE = re.compile(
    r"\b(related|recommended|sidebar|comments?|nav|navigation|"
    r"footer|header|menu|search|breadcrumb|advert|promo|"
    r"social|share-bar|widget|popup|modal-ad)\b",
    re.IGNORECASE,
)


def score_ancestor_context(ancestor_text: str) -> Tuple[int, str]:
    """Score based on the ancestor class/id text. The caller is
    responsible for concatenating 3-4 levels of ancestor identifiers
    into a single string (this keeps the scorer simple)."""
    if not isinstance(ancestor_text, str) or not ancestor_text:
        return 0, ""
    delta = 0
    parts = []
    pos_match = _ANCESTOR_POSITIVE.search(ancestor_text)
    if pos_match:
        delta += 25
        parts.append(f"positive ancestor ({pos_match.group(1)})")
    neg_match = _ANCESTOR_NEGATIVE.search(ancestor_text)
    if neg_match:
        delta -= 40
        parts.append(f"negative ancestor ({neg_match.group(1)})")
    return delta, "; ".join(parts) if parts else ""


# ── URL pattern fingerprinting (D) ───────────────────────────────────


def score_url_fingerprint(href: str, data_attrs: str,
                            fingerprint: Optional[Dict[str, Any]]) -> Tuple[int, str]:
    """+30 if the candidate's href or data-* attrs match a
    previously-successful CDN hostname or path prefix for this
    site. Self-tuning: as the runner accumulates successes, the
    scoring gets sharper without any user input.

    `fingerprint` shape:
      {
        'known_hosts': set/list of CDN hostnames seen succeed
        'known_path_prefixes': set/list of path prefixes seen succeed
      }
    """
    if not fingerprint or not isinstance(fingerprint, dict):
        return 0, ""
    candidate_urls = []
    for v in (href, data_attrs):
        if isinstance(v, str) and v:
            candidate_urls.append(v)
    if not candidate_urls:
        return 0, ""
    known_hosts = fingerprint.get("known_hosts") or set()
    known_prefixes = fingerprint.get("known_path_prefixes") or set()
    # Coerce to sets if the caller passed lists
    if not isinstance(known_hosts, (set, frozenset)):
        known_hosts = set(known_hosts) if known_hosts else set()
    if not isinstance(known_prefixes, (set, frozenset)):
        known_prefixes = set(known_prefixes) if known_prefixes else set()
    if not known_hosts and not known_prefixes:
        return 0, ""
    for url in candidate_urls:
        # Extract host + path quickly without urlparse (handles bare
        # paths too)
        if "://" in url:
            try:
                from urllib.parse import urlparse
                p = urlparse(url)
                host = (p.hostname or "").lower()
                path = p.path or ""
            except Exception:
                continue
        else:
            host = ""
            path = url
        if host and host in known_hosts:
            return 30, f"known CDN host: {host}"
        # Path-prefix match: any known prefix being a prefix of this
        # candidate's path
        for prefix in known_prefixes:
            if isinstance(prefix, str) and prefix and path.startswith(prefix):
                return 30, f"known path prefix: {prefix}"
    return 0, ""


# ── Position + shape (E) ─────────────────────────────────────────────


def score_position_shape(rect: Optional[Dict[str, Any]],
                            viewport_height: int = 0) -> Tuple[int, str]:
    """Score the candidate's location and size on the page. Real
    download CTAs are clickable rectangles above the fold; tiny
    text links deep in the footer are not.

    `rect` shape: {top: int, width: int, height: int}
    Missing rect → no contribution (server-side scoring of pasted
    HTML can't know layout).
    """
    if not rect or not isinstance(rect, dict):
        return 0, ""
    delta = 0
    parts = []
    try:
        top = int(rect.get("top", 99999))
        width = int(rect.get("width", 0))
        height = int(rect.get("height", 0))
    except (TypeError, ValueError):
        return 0, ""
    # Above the fold — strong signal
    if viewport_height > 0 and top >= 0 and top < viewport_height:
        delta += 10
        parts.append("above-fold")
    # Click-target shape
    if width >= 80 and height >= 30:
        delta += 15
        parts.append("button-sized")
    # Deep in the footer — likely nav noise
    if viewport_height > 0 and top > viewport_height * 5:
        delta -= 10
        parts.append("deep-in-page")
    return delta, "; ".join(parts) if parts else ""


# ── Text signals (F + G) ─────────────────────────────────────────────


_DOWNLOAD_RE = re.compile(
    r"\b(download|save|mp4|video|stream|full\s*movie|"
    r"get|grab|export|direct\s*link)\b",
    re.IGNORECASE,
)

# Stronger combinatorial signal: "download" within 20 chars of a
# resolution mention. Catches "Download 4K MP4", "1080p Download",
# etc. — the most common explicit CTA pattern.
_DOWNLOAD_RES_PAIR = re.compile(
    r"(?:(download|save|get).{0,20}"
    r"(8k|6k|4k|2k|1440p?|1080p?|720p?|2160p?)|"
    r"(8k|6k|4k|2k|1440p?|1080p?|720p?|2160p?).{0,20}"
    r"(download|save|get))",
    re.IGNORECASE,
)

# Expanded + weighted negatives. Each entry is (pattern, weight,
# label). Heavy weights here because these are CONFUSABLE with the
# real download text — "Download trailer" looks like the real CTA
# but isn't.
_NEGATIVES: List[Tuple[re.Pattern, int, str]] = [
    (re.compile(r"\b(report|flag|abuse|dmca)\b", re.I), -60, "report/flag"),
    (re.compile(r"\b(next\s*episode|prev|previous|related|recommended|"
                  r"up\s*next)\b", re.I), -50, "navigation"),
    (re.compile(r"\b(embed|copy\s*link|short\s*url|share\s*url)\b", re.I),
     -45, "share/embed"),
    # trailer/preview/sample suppressed UNLESS the surrounding text
    # also mentions a high resolution — caller handles the conditional
    (re.compile(r"\b(trailer|preview|sample|teaser|clip)\b", re.I),
     -40, "preview"),
    (re.compile(r"\b(screenshot|thumbnail|poster|cover|still)\b", re.I),
     -30, "thumbnail"),
    # Original NEG_RE keywords kept
    (re.compile(r"\b(advertisement|sponsor|ad-banner)\b", re.I),
     -50, "advertisement"),
    (re.compile(r"\b(share|tweet|facebook|whatsapp|telegram)\b", re.I),
     -25, "social"),
    (re.compile(r"\b(comment|reply|like|favourite|favorite)\b", re.I),
     -30, "comment/social"),
    (re.compile(r"\b(sign\s*up|register|create\s*account)\b", re.I),
     -40, "signup"),
    (re.compile(r"\b(login|sign\s*in|log\s*in)\b", re.I),
     -40, "login"),
    (re.compile(r"\b(subscribe|membership|premium\s*only|join\s*now)\b", re.I),
     -35, "subscribe"),
]


def score_text_signals(text: str, href: str, data_attrs: str,
                          tag: str) -> Tuple[int, List[Tuple[int, str]]]:
    """Score by text content. Returns (total_delta, reasons_list).

    text: the visible inner text of the candidate
    href: the href attribute
    data_attrs: concatenation of data-* attribute values
    tag: lowercase tag name
    """
    if not isinstance(text, str):
        text = ""
    if not isinstance(href, str):
        href = ""
    if not isinstance(data_attrs, str):
        data_attrs = ""
    if not isinstance(tag, str):
        tag = ""
    all_text = (text + " " + href + " " + data_attrs)[:400]
    reasons = []
    total = 0
    # Resolution tier (A)
    res_tier, res_label = detect_resolution_tier(all_text)
    if res_tier > 0:
        total += res_tier
        reasons.append((res_tier, f"resolution {res_label}"))
    # Codec-quality tie-breaker (F1) — small additive bonus that ranks
    # encodes within the same resolution; capped below a resolution
    # step so it never overrides the resolution ranking.
    codec_bonus, codec_label = detect_codec_tier(all_text)
    if codec_bonus > 0:
        total += codec_bonus
        reasons.append((codec_bonus, f"codec {codec_label}"))
    # Download keyword
    if _DOWNLOAD_RE.search(all_text):
        total += 25
        reasons.append((25, "download keyword"))
    # Combinatorial: download + resolution within 20 chars (F)
    if _DOWNLOAD_RES_PAIR.search(all_text):
        total += 20
        reasons.append((20, "download+resolution pair"))
    # File-extension signal in href/data
    url_text = (href + " " + data_attrs)
    if re.search(r"\.(mp4|mkv|webm|mov|m4v)(\?|#|$)", url_text, re.I):
        total += 30
        reasons.append((30, "video file extension"))
    # <video> / <source> tags
    if tag in ("video", "source"):
        total += 20
        reasons.append((20, "video element"))
    # data-* attribute presence (regardless of value pattern)
    if data_attrs.strip():
        total += 15
        reasons.append((15, "data-* attribute"))
    # Negative signals (G). trailer/preview is conditionally
    # demoted by 40 ONLY if the resolution isn't 4K+ (a "4K Trailer"
    # IS still a 4K video).
    for pattern, weight, label in _NEGATIVES:
        if pattern.search(all_text):
            if "preview" in label and res_tier >= 60:
                # 4K+ trailer/sample — still good content
                continue
            total += weight
            reasons.append((weight, label))
    # Long text → probably a paragraph, not a button
    if len(text) > 80:
        total -= 10
        reasons.append((-10, "long text"))
    return total, reasons


# ── Top-level scorer ─────────────────────────────────────────────────


def score_candidate(candidate: Dict[str, Any],
                       fingerprint: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Score a single candidate. Returns a result dict:

    {
      'score': int,
      'reasons': list of (delta, label),
      'resolution_tier': int,
      'estimated_size_bytes': int,
    }
    """
    if not isinstance(candidate, dict):
        return {"score": 0, "reasons": [],
                "resolution_tier": 0, "estimated_size_bytes": 0}
    # Defensive: coerce every string field. Recorder always emits
    # strings, but corrupted candidate dicts (from paste-HTML
    # extractor with malformed input, or third-party callers)
    # shouldn't crash on string concat.
    def _s(v):
        return v if isinstance(v, str) else ""
    text = _s(candidate.get("text"))
    href = _s(candidate.get("href"))
    data_href = _s(candidate.get("data_href"))
    data_url = _s(candidate.get("data_url"))
    data_src = _s(candidate.get("data_src"))
    data_dl = _s(candidate.get("data_download"))
    tag = _s(candidate.get("tag")).lower()
    ancestor_text = _s(candidate.get("ancestor_classes"))
    surrounding = _s(candidate.get("surrounding_text"))
    rect = candidate.get("rect")
    try:
        viewport_h = int(candidate.get("viewport_height", 0) or 0)
    except (TypeError, ValueError):
        viewport_h = 0
    data_attrs_concat = (data_href + " " + data_url + " "
                          + data_src + " " + data_dl)
    # Text signals (returns its own reasons list)
    text_score, text_reasons = score_text_signals(
        text, href, data_attrs_concat, tag)
    score = text_score
    reasons = list(text_reasons)
    # Filesize from surrounding text + the visible candidate text
    size_text = (text + " " + surrounding)
    size_bytes = parse_filesize_bytes(size_text)
    size_delta, size_label = score_filesize(size_bytes)
    if size_delta != 0:
        score += size_delta
        reasons.append((size_delta, f"filesize: {size_label}"))
    # Ancestor context
    anc_delta, anc_label = score_ancestor_context(ancestor_text)
    if anc_delta != 0:
        score += anc_delta
        reasons.append((anc_delta, anc_label))
    # URL fingerprint
    fp_delta, fp_label = score_url_fingerprint(
        href, data_attrs_concat, fingerprint)
    if fp_delta != 0:
        score += fp_delta
        reasons.append((fp_delta, fp_label))
    # Position + shape
    pos_delta, pos_label = score_position_shape(rect, viewport_h)
    if pos_delta != 0:
        score += pos_delta
        reasons.append((pos_delta, pos_label))
    # Resolution tier (extracted from text signals — pull it back
    # out for downstream comparison)
    res_tier, _ = detect_resolution_tier(
        text + " " + href + " " + data_attrs_concat)
    return {
        "score": score,
        "reasons": reasons,
        "resolution_tier": res_tier,
        "estimated_size_bytes": size_bytes,
    }


def rank_candidates(candidates: List[Dict[str, Any]],
                       fingerprint: Optional[Dict[str, Any]] = None,
                       min_score: int = 25) -> List[Dict[str, Any]]:
    """Score every candidate, filter to those with score >= min_score,
    and sort: score DESC, then resolution_tier DESC, then size DESC.
    Returns each candidate annotated with its score result fields."""
    if not isinstance(candidates, list):
        return []
    out = []
    for c in candidates:
        result = score_candidate(c, fingerprint=fingerprint)
        if result["score"] < min_score:
            continue
        # Annotate the candidate with the score result so the caller
        # gets all info in one pass
        annotated = dict(c) if isinstance(c, dict) else {}
        annotated.update({
            "score": result["score"],
            "score_reasons": result["reasons"],
            "resolution_tier": result["resolution_tier"],
            "estimated_size_bytes": result["estimated_size_bytes"],
        })
        out.append(annotated)
    # Sort: score DESC, then resolution_tier DESC, then size DESC
    out.sort(key=lambda c: (
        -c.get("score", 0),
        -c.get("resolution_tier", 0),
        -c.get("estimated_size_bytes", 0),
    ))
    return out


# ── Fingerprint state management ────────────────────────────────────


def update_fingerprint_from_success(fingerprint: Dict[str, Any],
                                       successful_url: str) -> Dict[str, Any]:
    """After a successful download, fold the URL's host + path-prefix
    into the fingerprint state. The prefix is everything up to the
    last `/` so we generalize from `/v/123/8k.mp4` to `/v/123/`.

    Caller is responsible for persisting the returned fingerprint
    (typically to the per-site cfg). Bounded growth: caps each set
    at 20 entries (oldest dropped) so a misclassified download can't
    poison scoring forever.
    """
    if not isinstance(fingerprint, dict):
        fingerprint = {}
    if not isinstance(successful_url, str) or not successful_url:
        return fingerprint
    # Decompose
    try:
        from urllib.parse import urlparse
        p = urlparse(successful_url)
        host = (p.hostname or "").lower()
        path = p.path or ""
    except Exception:
        return fingerprint
    if not host and not path:
        return fingerprint
    hosts = fingerprint.get("known_hosts")
    if not isinstance(hosts, list):
        hosts = list(hosts) if hosts else []
    prefixes = fingerprint.get("known_path_prefixes")
    if not isinstance(prefixes, list):
        prefixes = list(prefixes) if prefixes else []
    # Add host (dedupe; cap at 20)
    if host and host not in hosts:
        hosts.append(host)
        if len(hosts) > 20:
            hosts = hosts[-20:]  # keep most recent
    # Path prefix — strip the filename component
    if path:
        last_slash = path.rfind("/")
        if last_slash > 0:
            prefix = path[:last_slash + 1]
            if prefix and prefix not in prefixes:
                prefixes.append(prefix)
                if len(prefixes) > 20:
                    prefixes = prefixes[-20:]
    fingerprint["known_hosts"] = hosts
    fingerprint["known_path_prefixes"] = prefixes
    return fingerprint
