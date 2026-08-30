"""Resolution scoring, download-link detection, file utilities."""
# Load-bearing invariants tagged inline as # INV-<ID>; see DANGER_MAP.md.
import math, os, re, shutil, sys, uuid
from pathlib import Path
from .constants import NON_VIDEO_RE, SIZE_RE


# P5-3 DOM honeypot filter — opt-in via BD_DOM_HONEYPOT_FILTER env var.
# Three modes:
#   - "off" / unset / unrecognized  → no filtering (default; v3.66.27 behavior)
#   - "cheap"                       → Playwright is_visible() + URL/text checks
#                                     (the locator path; layout-aware)
#   - "strict"                      → cheap path PLUS a computed-style
#                                     probe (locator.evaluate) that
#                                     catches post-hydration hidden
#                                     states is_visible() reports as
#                                     visible: opacity:0, off-screen
#                                     transform, clip-path, pointer-
#                                     events:none. (F3, v3.66.50)
#
# Read at call time (mirrors _honeypot_drop_threshold and _yt_cipher_backend
# conventions) so tests can flip it via monkeypatch.setenv.
def _dom_honeypot_mode() -> str:
    raw = os.environ.get("BD_DOM_HONEYPOT_FILTER", "").strip().lower()
    # v3.66.308 (CLI→GUI parity): global_config store key `dom_honeypot_filter`
    # overrides the env seed when set; read at call time, lazy import, fail-safe.
    try:
        from bulk_downloader import global_config as _gc
        _sv = _gc.get("dom_honeypot_filter", None)
        if _sv:
            raw = str(_sv).strip().lower()
    except Exception:
        pass
    if raw in ("cheap", "strict"):
        return raw
    return "off"

# Phase 17.17: hash hint extraction. Sites occasionally publish file hashes
# in data-* attributes or in the URL fragment so clients can verify integrity.
# We scan for the standard names. If found, the runner verifies after
# download and reports a mismatch as a failure (corrupted CDN, redirected
# to error page that happens to be the right size, etc.).
_HASH_ATTRS = (
    ("md5",    "data-md5",    32),
    ("sha1",   "data-sha1",   40),
    ("sha256", "data-sha256", 64),
)
_HEX_RE = re.compile(r"^[a-fA-F0-9]+$")
def _extract_hash_hint(element, text):
    """Look for a hash on this element. Returns (algo, hexstring) or None.
    Handles three patterns:
      • data-md5 / data-sha1 / data-sha256 attributes
      • #md5=abc... / #sha1=abc... in the data-href/data-url URL fragment
      • adjacent text matching exact hash format (rare; we don't do this
        because false positives are common in noisy text)"""
    # Data attribute path
    for algo, attr, expected_len in _HASH_ATTRS:
        try:
            v = element.get_attribute(attr)
        except Exception:
            v = None
        if v and len(v) == expected_len and _HEX_RE.match(v):
            return (algo, v.lower())
    # URL fragment path
    if isinstance(text, str):
        for algo, _, expected_len in _HASH_ATTRS:
            m = re.search(rf"\b{algo}=([a-fA-F0-9]{{{expected_len}}})\b",
                          text, re.IGNORECASE)
            if m: return (algo, m.group(1).lower())
    return None

# ─── RESOLUTION + SIZE PARSING ────────────────────────────────────────────────
def parse_size_bytes(text):
    """Return file size in bytes from text like '5 GB', '447.26MB', '(118 MB)'.
    Returns 0 if no size found. Used as a tiebreaker between candidates with
    the same resolution score: bigger file ≈ better encode at the same res."""
    if not text: return 0
    m=SIZE_RE.search(text)
    if not m: return 0
    try: n=float(m.group(1))
    except Exception: return 0
    u=m.group(2).lower()
    return int(n*{"kb":1024,"mb":1048576,"gb":1073741824,"tb":1099511627776}.get(u,0))

# Resolution-label patterns checked AFTER explicit pixel heights. We take the
# MAX score across all matches, so 'Full HD' (1080) correctly beats 'HD' (720)
# even though both patterns will match inside 'Full HD'.
_RES_LABEL_PATTERNS=[  # INV-005
    (re.compile(r"\b8k\b",re.I),                       4320),
    (re.compile(r"\b6k\b",re.I),                       3160),  # 6144×3160 / 5568×3132 / 5760×3240
    (re.compile(r"\b5k\b",re.I),                       2880),  # 5120×2880
    (re.compile(r"\b(?:uhd|4k|ultra)\b",re.I),         2160),
    (re.compile(r"\b(?:qhd|2k)\b",re.I),               1440),
    (re.compile(r"\b(?:fhd|full[\s_-]?hd)\b",re.I),    1080),
    (re.compile(r"\bweb[\s_-]?hd\b",re.I),              540),
    (re.compile(r"\bhd\b",re.I),                        720),  # bare HD
    (re.compile(r"\b(?:sd|standard)\b",re.I),           480),
    (re.compile(r"\bmedium\b",re.I),                    360),
    (re.compile(r"\b(?:lq|low|small)\b",re.I),          360),
    (re.compile(r"\b(?:mobile|tiny)\b",re.I),           240),
]

def res_score(text):
    """Estimate the pixel height of the resolution mentioned in *text*.

    Returns the height as an integer (1080, 2160, etc.) or -1 if nothing
    resolution-shaped is found. The score is the actual pixel height so
    downstream code can compare against thresholds like 1080 directly.

    Strategy:
      1) Look for explicit pixel heights — '1080p', '2160p', '1920x1080'.
         If ANY explicit height is found, use the max and STOP. Explicit
         numbers are the most reliable signal — labels like 'low' or 'high'
         vary widely across sites.
      2) v3.43.65: look for CDN-path-segment tiers (e.g. Vixen's
         `/mp4_2160/`, VIP4K's `/1080p.mp4`). These are reliable because
         the site put the tier directly in the URL.
      3) If no explicit height was found, fall back to named tier labels —
         '4K', 'UHD', 'FHD', 'HD', 'SD', etc. Take the MAX label score so
         'Full HD' (1080) beats embedded 'HD' (720) in the same string.

    The digit pattern requires a 'p' suffix or 'x' separator, so file sizes
    like '448.26MB' cannot be misread as 448p resolution.

    v3.43.65 also boosts +5 for `_60fps` modifiers — 60fps variants are
    usually the highest-bitrate encode at any given resolution.
    """
    if not text: return -1
    t=text.lower()
    digit_best=-1
    for m in re.finditer(r"(\d{3,4})\s*p\b",t):
        try: n=int(m.group(1))
        except Exception: continue
        if 100<=n<=9999: digit_best=max(digit_best,n)
    # v3.66.1342: a PHOTO-SET dimension is not a video resolution.
    # nubilefilms captions its stills "Large 6000x4000px" / "Large
    # 8192x5464px"; group 2 was read as a pixel HEIGHT, scoring 4000 and
    # 5464, so a 3:2 photo outranked the real 2160p anchor and the wide
    # sweep clicked the photo control -- history rows 111 and 112.
    # The (?!\d) is load-bearing and \b is NOT a substitute for it. A bare
    # (?!\s*px) BACKTRACKS to a shorter first alternative and matches
    # ("6000", "400") -- a height absent from the text. \b blocks that too,
    # but "_" is a WORD character, so \b also killed "1280x720_60FPS.mp4"
    # and broke the 60fps tiebreaker in test_v3_43_65_cascade.py. Measured
    # all three ways; both regressions pinned in test_row381_*.
    for m in re.finditer(r"(\d{3,4})\s*[x×]\s*(\d{3,4})(?!\d)(?!\s*px)",t):
        try: h=int(m.group(2))
        except Exception: continue
        if 100<=h<=9999: digit_best=max(digit_best,h)
    # v3.43.65: CDN-path-segment tier patterns from the recon survey.
    # These supplement (don't replace) the explicit-height path above —
    # we take the MAX across both. Patterns and the height they imply:
    #   /mp4_2160/   /mp4_1080/   /mp4_720/   /mp4_480/   (Vixen/blacked/tushy)
    #   /mp4_4320/                                          (8K, hypothetical)
    #   _VIDEO_2160P.mp4   _480P.mp4                       (Vixen-style ALL CAPS)
    #   /1080p.mp4   /720p.mp4                             (VIP4K/Bang)
    #   stream_mp4_1080.mp4                                 (PornPros/Tiny4K)
    for m in re.finditer(r"mp4_(\d{3,4})\b", t):
        try: n=int(m.group(1))
        except Exception: continue
        if 100<=n<=9999: digit_best=max(digit_best,n)
    for m in re.finditer(r"_(\d{3,4})p\.mp4\b", t):
        try: n=int(m.group(1))
        except Exception: continue
        if 100<=n<=9999: digit_best=max(digit_best,n)
    for m in re.finditer(r"_(\d{3,4})p_60fps\.mp4\b", t):
        try: n=int(m.group(1))
        except Exception: continue
        if 100<=n<=9999: digit_best=max(digit_best,n)
    # +5 tiebreaker for 60fps variants — these are usually the
    # highest-bitrate encode the site offers at a given height.
    if digit_best > 0 and re.search(r"\b60\s*fps\b|_60fps\b", t):
        digit_best += 5
    if digit_best>0: return digit_best
    label_best=-1
    for pat,sc in _RES_LABEL_PATTERNS:
        if pat.search(t): label_best=max(label_best,sc)
    return label_best

def res_label(score):  # INV-005
    """Human-readable label for a res_score result.

    v3.43.78 (F5): keep this in sync with
    heuristic_scoring.RESOLUTION_TIERS labels. The three non-power-
    of-two heights below (1200, 900, 353) are real tiers that
    occur in the wild and heuristic_scoring labels them distinctly
    — without these intermediates, BD displays "720p" for an 900p
    file and "1080p" for a 1200p file, which is wrong-labeled (the
    score comparisons still work — only the displayed label was off).
    Static-analysis F5 flagged this.
    """
    if score>=4320: return "8K"
    if score>=3160: return "6K"
    if score>=2880: return "5K"
    if score>=2160: return "4K"
    if score>=1440: return "1440p"
    if score>=1200: return "1200p"
    if score>=1080: return "1080p"
    if score>=900:  return "900p"
    if score>=720:  return "720p"
    if score>=540:  return "540p"
    if score>=480:  return "480p"
    if score>=360:  return "360p"
    if score>=353:  return "353p (preview)"
    if score>=240:  return "240p"
    if score>0:     return f"{score}p"
    return "auto"

# ─── CODEC QUALITY (F1) ───────────────────────────────────────────────
# A lightweight codec-quality ranking, parallel to res_score but a
# SEPARATE signal. Higher = better codec at a given bitrate. This is a
# tie-breaker only — the heuristic_scoring side caps the codec bonus
# below a resolution step so codec never overrides verified resolution.
# Kept here so callers that only import detect.py can rank codecs too.
_CODEC_PATTERNS=[
    (re.compile(r"\b(remux|prores|source|original|lossless)\b",re.I), 4, "remux/source"),
    (re.compile(r"\b(av1|av01)\b",re.I),                              4, "AV1"),
    (re.compile(r"\b(hevc|h\.?\s*265|x265)\b",re.I),                  3, "HEVC"),
    (re.compile(r"\b(vp9|vp09)\b",re.I),                              2, "VP9"),
    (re.compile(r"\b(h\.?\s*264|avc|x264)\b",re.I),                   1, "H.264"),
]

def codec_score(text):
    """Estimate codec quality from *text*. Returns an int 0-5 (0 = no
    codec recognised, higher = better). Tie-breaker only — never large
    enough to override a resolution difference."""
    if not text: return 0
    best=0
    for pat,sc,_ in _CODEC_PATTERNS:
        if pat.search(text): best=max(best,sc)
    return best

def codec_label(score):
    """Human-readable label for a codec_score result."""
    for _,sc,lbl in _CODEC_PATTERNS:
        if score==sc: return lbl
    return "" if score<=0 else "unknown"

# ─── SAME-WORK IDENTITY ───────────────────────────────────────────────────────
# v3.66.x row 388 -- THE THIRD ROUTING DECISION, AS A PURE FUNCTION, in the
# shape of runner_transport's _stream_route / _direct_media_route: two strings
# in, a verdict out, nothing browser-coupled, so it can be checked directly.
#
# MEASURED on test6 2026-08-29 at v3.66.1346, live and read-only, on
# https://members.nubilefilms.com/video/watch/254796/seeing-red-s50e30 . The
# page carries 159 media links, SIX of which are the requested work: below the
# scene's own six download tiers sit a Related Videos grid of ~25 scenes, then
# Related Photos and Related Shorts, and EVERY related card publishes its own
# full tier menu with the identical label '3840x2160 4K MP4 (5 GB)'.
# find_best_download's top ten candidates were all related scenes at
# score=2160 size=5368709120; the requested scene's own 4K tier scored the
# same 2160 with size=3221225472 and did not make the top ten. So the score
# TIED and `size` resolved the tie toward whichever scene on the page happened
# to have the biggest file. History row 121 read `done`, library row 103 read
# the requested title, and 5,102,802,950 bytes of a different scene were on
# disk. Nothing in the ranking ever asked whether the candidate and the page
# name the same work.
#
# RANK, DO NOT DELETE. This returns 1 or 0 and is used as the LEADING sort
# key, never as a filter. 0 means "no identity could be derived", not "wrong":
# sites legitimately name files unlike their page (teenmegaworld's
# after-shower-satisfaction correctly saves TeenSexMania_Adell_3840x2160.mp4 --
# studio_performer_resolution). When nothing on a page derives an identity every
# candidate scores 0 and the ordering is byte-identical to the old
# (score, size). Refusing every candidate would turn one wrong file into a
# total outage; an audit that assumed otherwise called three innocent rows
# damage.
#
# KNOWN RESIDUAL, deliberately not solved: a page slug that is a PERFORMER
# name rather than a scene title (e.g. .../octavia-red-returns) would mark
# every card featuring that performer as same-work, and run length does not
# break that tie. Unobserved on either measured page -- the incident slug's
# only overlap with the related cards is the single token 'red', which is
# below the threshold. Stemming or fuzzy matching would add false-judgment
# surface no measurement asks for.
_CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_WORK_SPLIT_RE = re.compile(r"[^a-z0-9]+")
_PAGE_EXT_RE = re.compile(r"\.(?:html?|php|aspx?|jsp|jspx)$", re.I)
# A run of ONE token is coincidence: both related hrefs above carry
# 'octavia_red' and would match the page's 'red'. Two tokens and six joined
# characters is what separates 'seeing red' (9) from 'red' (3), and admits
# wowgirls' 'dreamingofjapan' (15) via the camelCase split.
_WORK_MIN_TOKENS = 2
_WORK_MIN_CHARS = 6
# Bounds, so a hostile URL cannot turn the O(n*m) scan below into a stall.
_WORK_MAX_PAGE_TOKENS = 40
_WORK_MAX_CAND_TOKENS = 120

def work_tokens(text):
    """Normalize *text* into identity tokens: lowercase, camelCase split, then
    split on every non-alphanumeric. 'DreamingOfJapan' and 'dreaming-of-japan'
    both become ('dreaming','of','japan'); 'seeing_red_with_octavia_red'
    becomes ('seeing','red','with','octavia','red')."""
    if not text or not isinstance(text, str): return ()
    try:
        s = _CAMEL_SPLIT_RE.sub(" ", text)
        return tuple(t for t in _WORK_SPLIT_RE.split(s.lower()) if t)
    except Exception:
        return ()

def page_work_tokens(page_url):
    """Identity tokens for the work named by *page_url*, or () when none.

    Only http(s) URLs carry a path slug worth reading. about:blank, data: and
    file: return () -- which is what every `set_content` fixture in this repo
    produces, so those pages provably keep the old ordering.

    The identifying segment is the last path segment that is not purely
    numeric: `/video/watch/254796/seeing-red-s50e30` -> the slug, and
    `/scene/seeing-red/254796` -> the slug one step back past the id.
    """
    if not page_url or not isinstance(page_url, str): return ()
    if not page_url.lower().startswith(("http://", "https://")): return ()
    try:
        from urllib.parse import urlparse, unquote
        path = unquote(urlparse(page_url).path or "")
    except Exception:
        return ()
    for seg in reversed([s for s in path.split("/") if s]):
        toks = work_tokens(_PAGE_EXT_RE.sub("", seg))
        if any(not t.isdigit() for t in toks):
            return toks
    return ()

def _longest_common_run(a, b):
    """(token_count, joined_char_count) of the longest CONTIGUOUS token run
    common to sequences *a* and *b*.

    Contiguity is the whole point. A bag-of-words overlap would score the
    related card `new_years_with_my_ex_with_octavia_red` a match against the
    page slug `seeing-red-s50e30` on the shared token 'red'."""
    if not a or not b: return (0, 0)
    best_n = best_c = 0
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                n = prev[j - 1] + 1
                cur[j] = n
                if n >= best_n:
                    c = sum(len(t) for t in a[i - n:i])
                    if n > best_n or c > best_c:
                        best_n, best_c = n, c
        prev = cur
    return (best_n, best_c)

def work_affinity(page_url, candidate_url):
    """1 when *candidate_url* provably names the same work as *page_url*, else 0.

    0 is UNKNOWN, never a refusal -- see the section comment above. Used as the
    leading key of the candidate sort so a candidate that cannot be shown to
    belong to this page can never outrank one that can.

    Deliberately reads the candidate's URL and not its harvested text: an
    ancestor-walk candidate inherits its descendants' inner_text, which on a
    scene page includes the page's own title, and that would manufacture an
    identity for a control that has none.
    """
    page = page_work_tokens(page_url)
    if not page: return 0
    cand = work_tokens(candidate_url if isinstance(candidate_url, str) else "")
    if not cand: return 0
    n, c = _longest_common_run(page[:_WORK_MAX_PAGE_TOKENS],
                               cand[:_WORK_MAX_CAND_TOKENS])
    return 1 if (n >= _WORK_MIN_TOKENS and c >= _WORK_MIN_CHARS) else 0

# ─── DOWNLOAD HELPERS ─────────────────────────────────────────────────────────
# Shared candidate admission. Learned selectors used to return before the wide
# sweep's admission logic, so a broad learned ``a`` scored arbitrary anchors --
# including the row-399 /films-6K/ site-navigation link. Keep this vocabulary at
# module scope so both populations make the same basic score/download decision.
_DL_WORD_RE = re.compile(
    r"download|\bdl\b|save|get\s*it|grab|\.mp4|\.mkv|\.mov|\.webm|\.m4v|\.ts",
    re.I)
_EXPLICIT_VIDEO_HEIGHT_RE = re.compile(
    r"\d{3,4}\s*p\b|\d{3,4}\s*[x×]\s*\d{3,4}(?!\d)(?!\s*px)|"
    r"mp4_\d{3,4}", re.I)
_CANDIDATE_URL_ATTRS = (
    "href", "data-href", "data-url", "data-src", "data-download",
    "data-signed-url-key")
_NAV_DOWNLOAD_AUTHORITY_ATTRS = (
    "download", "data-href", "data-url", "data-src",
    "data-download", "data-signed-url-key", "data-link")
_OWN_CONTROL_AFFORDANCE_ATTRS = (
    "href", "download", "onclick", "data-href", "data-url", "data-src",
    "data-download", "data-signed-url-key", "data-link")
_CONTROL_DESCENDANT_SEL = (
    "a[href],button,[onclick],[data-href],[data-url],[data-src],"
    "[data-download],[data-signed-url-key],[role='button'],[role='link']")
_SIGNED_DOWNLOAD_QUERY_KEY_RE = re.compile(
    r"(?:^|[_-])(?:download|dl|file|media|video|stream|token|sig|signature|"
    r"signed|expires?|key)(?:$|[_-])", re.I)
_SELECTOR_CLASS_OR_ID_RE = re.compile(r"(?<!\\)[.#][A-Za-z_-]")
_SELECTOR_AUTHORITY_WORD_RE = re.compile(
    r"download|quality|resolution|media|stream|player|video|source|"
    r"\.m(?:p4|4v|kv)|\.mov|\.webm|\.ts\b", re.I)
_SELECTOR_AUTHORITY_ATTRS = {
    "download", "data-href", "data-url", "data-src",
    "data-download", "data-signed-url-key", "data-link", "data-video",
    "data-stream", "data-player", "data-quality", "data-resolution",
    "data-framerate",
}
_SELECTOR_CONTROL_ROLES = {
    "button", "link", "menuitem", "option", "tab", "radio", "checkbox",
}
_SELECTOR_ALWAYS_CONTROL_ROLES = {"button", "link"}
_SELECTOR_SCOPED_CONTROL_ROLES = {
    "menuitem", "menuitemradio", "option", "tab", "radio", "checkbox",
    "listitem", "row",
}
_SELECTOR_SCOPE_ROLES = {"dialog", "listbox", "menu", "toolbar"}
_SELECTOR_POSITIVE_PSEUDOS = {
    "eq", "first-child", "first-of-type", "has", "has-text", "last-child",
    "last-of-type", "nth", "nth-child", "nth-of-type", "only-child", "text",
    "text-is", "visible",
}


def _candidate_work_affinity(el, page_url):
    """Return work affinity from URL-bearing attributes, or 0 if unknown."""
    if not page_url:
        return 0
    for attr in _CANDIDATE_URL_ATTRS:
        try:
            value = el.get_attribute(attr)
        except Exception:
            continue
        try:
            if value and work_affinity(page_url, value):
                return 1
        except Exception:
            continue
    return 0


def _split_selector_list(selector):
    """Split top-level CSS alternatives, returning [] when syntax is unclear."""
    parts, start = [], 0
    parens = brackets = 0
    quote = None
    escaped = False
    for index, char in enumerate(selector):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
        elif char == "[":
            brackets += 1
        elif char == "]":
            brackets -= 1
        elif not brackets and char == "(":
            parens += 1
        elif not brackets and char == ")":
            parens -= 1
        elif char == "," and not brackets and not parens:
            part = selector[start:index].strip()
            if not part:
                return []
            parts.append(part)
            start = index + 1
        if parens < 0 or brackets < 0:
            return []
    if quote or parens or brackets:
        return []
    final = selector[start:].strip()
    if not final:
        return []
    parts.append(final)
    return parts


def _matching_selector_paren(selector, opening):
    depth = 0
    quote = None
    escaped = False
    for index in range(opening, len(selector)):
        char = selector[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _outer_selector_functions(selector):
    """Return outer ``:name(...)`` spans, or None for an unbalanced form."""
    functions = []
    index = 0
    quote = None
    escaped = False
    brackets = 0
    while index < len(selector):
        char = selector[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\":
            escaped = True
            index += 1
            continue
        if quote:
            if char == quote:
                quote = None
            index += 1
            continue
        if char in ("'", '"'):
            quote = char
            index += 1
            continue
        if char == "[":
            brackets += 1
            index += 1
            continue
        if char == "]":
            brackets -= 1
            if brackets < 0:
                return None
            index += 1
            continue
        if char == ":" and not brackets:
            match = re.match(r":([A-Za-z][\w-]*)\s*\(", selector[index:])
            if match:
                opening = index + match.end() - 1
                closing = _matching_selector_paren(selector, opening)
                if closing is None:
                    return None
                functions.append((
                    match.group(1).lower(), index, closing + 1,
                    selector[opening + 1:closing]))
                index = closing + 1
                continue
        index += 1
    if quote or brackets:
        return None
    return functions


def _without_selector_functions(selector, names=None):
    functions = _outer_selector_functions(selector)
    if functions is None:
        return None
    chunks = []
    cursor = 0
    for name, start, end, _argument in functions:
        chunks.append(selector[cursor:start])
        if names is not None and name not in names:
            chunks.append(selector[start:end])
        cursor = end
    chunks.append(selector[cursor:])
    return "".join(chunks)


def _selector_attributes_and_mask(selector):
    attributes = []
    masked = list(selector)
    index = 0
    while index < len(selector):
        if selector[index] != "[":
            index += 1
            continue
        start = index
        index += 1
        quote = None
        escaped = False
        while index < len(selector):
            char = selector[index]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif quote:
                if char == quote:
                    quote = None
            elif char in ("'", '"'):
                quote = char
            elif char == "]":
                break
            index += 1
        if index >= len(selector) or quote:
            return None
        attributes.append(selector[start + 1:index])
        for masked_index in range(start, index + 1):
            masked[masked_index] = " "
        index += 1
    return attributes, "".join(masked)


def _selector_direct_authority(selector):
    functions = _outer_selector_functions(selector)
    if functions is None:
        return False
    if any(name in _SELECTOR_POSITIVE_PSEUDOS
           for name, _start, _end, _argument in functions):
        return True
    base = _without_selector_functions(selector)
    if base is None:
        return False
    parsed = _selector_attributes_and_mask(base)
    if parsed is None:
        return False
    attributes, masked = parsed
    roles = set()
    has_modal_scope = False
    for attribute in attributes:
        content = attribute.strip().lower()
        name_match = re.match(r"([\w:-]+)", content)
        name = name_match.group(1) if name_match else ""
        if name in _SELECTOR_AUTHORITY_ATTRS:
            return True
        if name == "role":
            for role in (_SELECTOR_CONTROL_ROLES
                         | _SELECTOR_SCOPED_CONTROL_ROLES
                         | _SELECTOR_SCOPE_ROLES):
                if re.search(
                        rf"(?:^|[^\w-]){role}(?:$|[^\w-])", content):
                    roles.add(role)
        if name == "aria-modal" and re.search(
                r"(?:^|[^\w-])true(?:$|[^\w-])", content):
            has_modal_scope = True
        if _SELECTOR_AUTHORITY_WORD_RE.search(content):
            return True
    if roles.intersection(_SELECTOR_ALWAYS_CONTROL_ROLES):
        return True
    if (roles.intersection(_SELECTOR_SCOPED_CONTROL_ROLES)
            and (roles.intersection(_SELECTOR_SCOPE_ROLES)
                 or has_modal_scope)):
        return True
    return bool(_SELECTOR_CLASS_OR_ID_RE.search(masked)
                or _SELECTOR_AUTHORITY_WORD_RE.search(masked))


def _selector_branch_has_authority(selector):
    positive = _without_selector_functions(selector, {"not"})
    if positive is None:
        return False
    if _selector_direct_authority(positive):
        return True
    functions = _outer_selector_functions(positive)
    if functions is None:
        return False
    alternatives = [item for item in functions if item[0] in {"is", "where"}]
    if not alternatives:
        return False
    for _name, _start, _end, argument in alternatives:
        branches = _split_selector_list(argument)
        if not branches or not all(
                _selector_branch_has_authority(branch) for branch in branches):
            return False
    return True


def _learned_selector_requires_signal(selector):
    """Whether every match needs its own media/download evidence.

    Authority is evaluated per top-level branch and positive pseudo context;
    one precise alternative cannot lend authority to a generic ``a`` branch.
    Negations and quoted attribute values never manufacture positive intent.
    Unparseable selector syntax stays conservative.
    """
    if not isinstance(selector, str):
        return True
    branches = _split_selector_list(selector.strip())
    return not branches or not all(
        _selector_branch_has_authority(branch) for branch in branches)


def _selector_branch_authority_probe(selector):
    """Return a CSS branch matching only its positive-authority alternatives."""
    positive = _without_selector_functions(selector, {"not"})
    if positive is None:
        return None
    if _selector_direct_authority(positive):
        return selector
    functions = _outer_selector_functions(selector)
    if functions is None:
        return None
    replacements = []
    for name, start, end, argument in functions:
        if name not in {"is", "where"}:
            continue
        alternatives = _split_selector_list(argument)
        if not alternatives:
            return None
        authoritative = [
            branch for branch in alternatives
            if _selector_branch_has_authority(branch)
        ]
        if not authoritative:
            return None
        replacements.append((
            start, end, f":{name}({','.join(authoritative)})"))
    if not replacements:
        return None
    probe = selector
    for start, end, replacement in reversed(replacements):
        probe = probe[:start] + replacement + probe[end:]
    return probe


def _learned_candidate_requires_signal(el, selector):
    """Bind mixed-selector authority to the branch the live element matches."""
    if not isinstance(selector, str):
        return True
    branches = _split_selector_list(selector.strip())
    if not branches:
        return True
    if all(_selector_branch_has_authority(branch) for branch in branches):
        return False
    probes = [
        probe for probe in (
            _selector_branch_authority_probe(branch) for branch in branches)
        if probe
    ]
    if not probes:
        return True
    try:
        matches_authority = el.evaluate(
            "(e, probes) => probes.some(probe => {"
            "try { return e.matches(probe); } catch (_) { return false; }"
            "})",
            probes)
        return not bool(matches_authority)
    except Exception:
        return True


def _is_wrapper_not_control(el):
    """True only for a measured wrapper with no affordance of its own."""
    if _candidate_has_own_affordance(el):
        return False
    try:
        return el.locator(_CONTROL_DESCENDANT_SEL).count() > 0
    except Exception:
        # Locator stubs and detached elements cannot prove wrapper status.
        return False


def _candidate_has_own_affordance(el):
    """Whether a broad learned match is itself an interactive control.

    Unknown locator implementations fail open so reviewed/stub paths are not
    deleted merely because they cannot expose live DOM semantics.
    """
    try:
        return bool(el.evaluate(
            "(e, args) => args.tags.includes(e.tagName.toLowerCase()) || "
            "args.attrs.some(attr => e.hasAttribute(attr)) || "
            "args.roles.includes((e.getAttribute('role') || '').toLowerCase())",
            {"tags": ["a", "button", "input", "option", "select"],
             "attrs": list(_OWN_CONTROL_AFFORDANCE_ATTRS),
             "roles": sorted(_SELECTOR_CONTROL_ROLES)}))
    except Exception:
        return True


def _has_navigation_ancestor(el):
    """True only when the live DOM proves semantic site-chrome ancestry.

    A locator stub or detached element cannot prove context and therefore keeps
    the candidate. This is intentionally narrower than guessing from URL shape.
    """
    try:
        return bool(el.evaluate(
            "e => Boolean(e.closest("
            "'nav,header,footer,[role=\"navigation\"],"
            "[class~=\"navigation\"],[class~=\"main_menu\"],"
            "[class~=\"ps_main_menu\"]'))"))
    except Exception:
        return False


def _is_navigation_resolution_ghost(el, text, page_url=""):
    """Whether ``el`` is a resolution-only plain link in proven site chrome.

    Every ambiguous or download-shaped case fails open (keeps the control).
    In particular, opaque/signed data controls outside navigation are never
    judged by URL shape alone -- the flaw in the historical row-399 candidate.
    """
    t = text or ""
    # Prove that this is a weak resolution-only href before asking the live DOM
    # about ancestry.  Besides keeping the common media path cheap, ordering
    # the checks this way preserves cheap honeypot mode's contract: an obvious
    # ``Download 1080p ...mp4`` candidate must not acquire an unrelated
    # computed-style/evaluate probe merely because chrome filtering is enabled.
    try:
        href = (el.get_attribute("href") or "").strip()
    except Exception:
        return False
    if not href or res_score(t) < 0:
        return False
    # The harvested candidate text includes href. Query decoration such as
    # ``utm_campaign=download`` is not visible download intent, so judge the
    # label separately and let URL-specific rules below inspect href.
    visible_text = t.replace(href, " ")
    if parse_size_bytes(visible_text) > 0 or _DL_WORD_RE.search(visible_text):
        return False
    if _EXPLICIT_VIDEO_HEIGHT_RE.search(visible_text):
        return False

    try:
        from . import candidate_filter as _candidate_filter
        strong = {
            "media_extension", "manifest_url", "download_path", "api_pattern"
        }
        if strong.intersection(
                _candidate_filter.positive_signals(href, t, "")):
            return False
    except Exception:
        return False

    try:
        from urllib.parse import parse_qsl, urlsplit
        href_parts = urlsplit(href)
        if any(_SIGNED_DOWNLOAD_QUERY_KEY_RE.search(key or "")
               for key, _value in parse_qsl(
                   href_parts.query, keep_blank_values=True)):
            return False
        if _DL_WORD_RE.search(href_parts.fragment or ""):
            return False
        if href_parts.netloc:
            page_host = urlsplit(page_url or "").netloc.lower()
            if not page_host or href_parts.netloc.lower() != page_host:
                return False
        if work_affinity(page_url, href):
            return False
    except Exception:
        return False

    if not _has_navigation_ancestor(el):
        return False
    try:
        context = el.evaluate(
            "(e, attrs) => ({"
            "tag: e.tagName.toLowerCase(), "
            "hasOwnAuthority: attrs.some(attr => e.hasAttribute(attr)), "
            "onclick: e.getAttribute('onclick') || '', "
            "href: e.getAttribute('href') || ''"
            "})",
            list(_NAV_DOWNLOAD_AUTHORITY_ATTRS))
        if not isinstance(context, dict) or context.get("tag") != "a":
            return False
        if context.get("hasOwnAuthority"):
            return False
        if _DL_WORD_RE.search(context.get("onclick") or ""):
            return False
    except Exception:
        return False
    return True


def _candidate_is_rankable(el, text, page_url="", require_signal=True):
    """Shared learned/wide admission without overriding explicit selectors."""
    t = (text or "").strip()
    if NON_VIDEO_RE.search(t):
        return False
    if require_signal and (
            not t or (res_score(t) < 0 and not _DL_WORD_RE.search(t))):
        return False
    return not _is_navigation_resolution_ghost(el, t, page_url)


def find_best_download(page,custom="",learned=None,runner=None):
    """Locate the best download candidate on the page — defensively.

    Phase 5.5: if `learned` is a dict with row_selectors, try those first.
    On hit, returns immediately with a flag indicating the learned path
    succeeded (the caller uses this to update hit/miss counters for
    drift detection in Phase 5.8). On miss, falls through to the wide
    14-element-type scan unchanged.

    `learned` schema:
      {
        "trigger_selectors": [...],     # for opening modals; used by caller
        "row_selectors": [...],         # tried first here
        "url_attribute": "data-href",   # caller uses for direct fetch
      }

    P5-3b (v3.66.29): `runner` is an optional Runner-like object exposing
    a `log_event(kind, message, ..., extra=None)` method. When set, the
    P5-3 DOM-honeypot filter summary is emitted via that channel (carries
    site_id, persists in the event log, mirrored to stderr automatically
    by log_event itself). When unset, falls back to direct stderr.write
    — keeps the v3.66.28 contract for callers that have no runner handle
    (auto_detect.py) and preserves fail-open semantics if log_event raises.

    Original selection rules unchanged (custom > direct media > general
    sweep > ancestor walk > resolution scoring + size tiebreaker)."""
    # Phase 5.5: learned-pattern fast path. If we have row_selectors,
    # locate any matching elements and pick the strongest same-work row, then
    # resolution/size. Skip the full sweep when we hit.
    #
    # v3.65.2: preserve ALL scored matches in _all_candidates, not just
    # the winner. The downstream _apply_quality_preference (Phase 67)
    # picks from this list — without alternatives present, "prefer
    # 1080p" silently degrades to "take whatever scored highest"
    # because there's nothing in the candidate list to pick from.
    # Effect was that on every site where teaching had successfully
    # produced row_selectors (i.e. the cache was actually working),
    # the user's quality_preference setting was silently ignored.
    try:
        _page_url = page.url or ""
    except Exception:
        _page_url = ""
    if not isinstance(_page_url, str):
        _page_url = ""

    if learned and isinstance(learned, dict):
        row_sels = learned.get("row_selectors") or []
        fallback_group = None
        winning_group = None
        for sel in row_sels:
            try:
                loc_all = page.locator(sel)
                count = loc_all.count()
            except Exception: continue
            if count == 0: continue
            # Score every match. v3.66.276: the cap is now on VISIBLE rows
            # scored, not raw iterations. Responsive lg+md+mob duplication can
            # put the hidden block FIRST in DOM order; a raw min(count,30) would
            # scan only hidden rows, score nothing, and miss the back-loaded
            # visible set entirely (silent fall-through to the wide sweep). Scan
            # up to _RAW_SCAN_CAP raw matches but stop after _VISIBLE_CAP visible
            # rows are scored.
            scored = []
            _VISIBLE_CAP = 30
            _RAW_SCAN_CAP = 200
            _seen_visible = 0
            for i in range(min(count, _RAW_SCAN_CAP)):
                if _seen_visible >= _VISIBLE_CAP:
                    break
                try:
                    el = loc_all.nth(i)
                    # v3.66.247: a learned row that is not visible cannot be
                    # clicked. Returning it as a _via_learned hit produces a
                    # false drift "hit", wastes the full expect_download timeout
                    # on an unclickable element, and is misdiagnosable (looks
                    # matched; never downloads) — the canonical case is a
                    # modal-scoped discriminating row while the modal is closed.
                    # Skip it so the selector falls through to a clean
                    # no-learned-hit + wide sweep instead. A raising
                    # is_visible() is caught by the enclosing except below
                    # (treated as a skip), consistent with per-element error
                    # handling on this path.
                    if not el.is_visible():
                        continue
                    _seen_visible += 1
                    require_signal = _learned_candidate_requires_signal(
                        el, sel)
                    txt_parts = [el.inner_text() or ""]
                    for attr in _CANDIDATE_URL_ATTRS + (
                            "title", "aria-label"):
                        try:
                            v = el.get_attribute(attr)
                            if v: txt_parts.append(v)
                        except Exception: pass
                    txt = " ".join(txt_parts)
                    if _is_wrapper_not_control(el):
                        continue
                    if (require_signal
                            and not _candidate_has_own_affordance(el)):
                        continue
                    if not _candidate_is_rankable(
                            el, txt, _page_url,
                            require_signal=require_signal):
                        continue
                    score = res_score(txt)
                    size = parse_size_bytes(txt)
                    # Phase 17.17: opportunistically extract hash hints from
                    # standard data-* attributes. These rarely appear, but
                    # when they do, they let us verify the download was
                    # bit-exact instead of just "looked plausible".
                    hash_info = _extract_hash_hint(el, txt)
                    entry = {"locator": el, "text": txt[:160].strip(),
                             "score": max(0, score), "size": size,
                             "work": _candidate_work_affinity(
                                 el, _page_url)}
                    if hash_info:
                        entry["expected_hash_algo"] = hash_info[0]
                        entry["expected_hash_value"] = hash_info[1]
                    scored.append(entry)
                except Exception: continue
            if scored:
                scored.sort(key=lambda c: (
                    c["work"], c["score"], c["size"]),
                            reverse=True)
                if scored[0]["work"]:
                    winning_group = (sel, scored)
                    break
                if fallback_group is None:
                    fallback_group = (sel, scored)
        selected_group = winning_group or fallback_group
        if selected_group:
            # Work identity is global across the learned chain. The first group
            # with same-work evidence is already the maximum (work is binary);
            # absent that, retain the first reviewed selector as before.
            # Resolution/size and quality alternatives stay within one selector
            # so _learned_sel and _all_candidates describe the same route.
            winning_sel, winning = selected_group
            best_match = dict(winning[0])
            best_match["_via_learned"] = True
            best_match["_learned_sel"] = winning_sel
            best_match["_all_candidates"] = [
                {"text": c["text"], "score": c["score"], "size": c["size"],
                 "work": c["work"], "locator": c["locator"]}
                for c in winning[:10]
            ]
            return best_match

    if custom:
        loc=page.locator(custom).first
        if loc.count()>0: return {"locator":loc,"text":custom,"score":9999,"size":0,
                                  "_all_candidates":[{"text":f"custom: {custom}","score":9999,"size":0}]}
    candidates,seen=[],set()
    # P5-3: read DOM-honeypot mode once per call. Cheap when off
    # (single env lookup); avoids per-candidate overhead in the hot loop.
    _dom_hp_mode=_dom_honeypot_mode()
    _dom_hp_filtered=[]   # accumulates dropped (locator, reason) for log emission
    dl_re=_DL_WORD_RE
    res_re=re.compile(r"\d{3,4}\s*p|\d{3,4}\s*[x×]\s*\d{3,4}"
                      r"|\b(?:4k|2k|8k|hd|fhd|uhd|qhd|sd|lq|ultra|standard|"
                      r"mobile|low|medium|tiny|web\s*hd|full\s*hd)\b",re.I)
    # Collect text from every attribute that might carry user-visible labels
    # OR machine-readable resolution/format data. Wide net is correct here:
    # we filter aggressively on the score side, so harvesting noise is fine.
    _attrs_to_scan=("title","aria-label","alt","value","placeholder",
                    "href","src","data-href","data-url","data-src",
                    "data-quality","data-res","data-resolution","data-size",
                    "data-format","data-bitrate","data-label","data-name",
                    "data-title","data-tooltip","data-original-title",
                    "data-signed-url-key","data-download")

    # Only URL-bearing attributes decide the work. NOT the harvested text: an
    # ancestor-walk candidate inherits its descendants' inner_text, which on a
    # scene page includes the page's own title, and that would manufacture an
    # identity for a control that has none.
    def gather_work(el):
        """1 when any URL this element carries names the page's work, else 0.

        Fails to 0 (unknown, rank unchanged) on any error -- never to a
        refusal, and never to a claim of identity it could not measure."""
        return _candidate_work_affinity(el, _page_url)

    def gather_text(el):
        parts=[]
        try: parts.append(el.inner_text() or "")
        except Exception: pass
        for a in _attrs_to_scan:
            try:
                v=el.get_attribute(a)
                if v: parts.append(v)
            except Exception: pass
        return " ".join(parts)

    # v3.66.1340: a LAYOUT WRAPPER is not a download control.
    # gather_text reads inner_text, so an ancestor inherits every
    # descendant's label. Measured on the live wowgirls scene page
    # (test6, 2026-08-29): div.content_download.video_downloads -- 88
    # descendants, 9 child a.ct_dl_button -- scores 4325 from its
    # "7680 x 4320" child (TYING the real 8K anchor) and parses a size
    # of 1.99GB from the 1080p child's caption, while the real 8K anchor
    # keeps its size in a SIBLING caption and parses 0. The
    # (score,size) sort then ranks the wrapper first, and clicking a
    # <div> fires no Playwright download event -- five wowgirls history
    # rows read "no dl event; scored ok but no download fired".
    # Drop a candidate ONLY when it carries no affordance of its own AND
    # contains one that does. Every element the ancestor walk below is
    # willing to promote satisfies the own-affordance test, so this can
    # never delete a real control -- including wowgirls' own learned
    # `div.download-button[data-href]` shape (negative control in
    # tests/test_row380_wrapper_never_outranks_leaf.py).
    def add(el,text):
        t=(text or "").strip()
        if not t or t in seen: return
        # v3.66.1340: a pure layout wrapper is not clickable-as-a-download.
        if _is_wrapper_not_control(el): return
        # P5-3 DOM-honeypot filter at candidate-construction time.
        # Filter here (not at scoring) so invisible candidates don't
        # pollute the scoring list. Off by default — env var unset →
        # this block is a single mode check and a no-op.
        if _dom_hp_mode!="off":
            try:
                from .dom_honeypot import is_link_decoy_playwright
                is_decoy,reason=is_link_decoy_playwright(el,strict=(_dom_hp_mode=="strict"))
                if is_decoy:
                    _dom_hp_filtered.append((t[:120],reason))
                    return
            except Exception:
                # Fail-open: a bug in dom_honeypot must not cost
                # the operator a real download (mirrors R-P5-2's
                # _apply_honeypot_filter exception handling).
                pass
        # Shared with the learned fast path: include only a scored/download-
        # shaped control, and drop a weak resolution link only when its DOM
        # ancestry positively proves site chrome.
        if not _candidate_is_rankable(el, t, _page_url): return
        s=res_score(t)
        seen.add(t)
        candidates.append({"locator":el,"text":t[:160],
                           "score":max(0,s),"size":parse_size_bytes(t),
                           "work":gather_work(el)})

    # ── 1. Direct download links / explicit media extensions ──────────────
    for sel in ["a[download]",
                "a[href*='.mp4']","a[href*='.mkv']","a[href*='.mov']",
                "a[href*='.webm']","a[href*='.m4v']","a[href*='.ts']",
                "a[href*='download']","a[href*='/dl/']",
                "[data-href*='.mp4']","[data-href*='.mkv']",
                "[data-url*='.mp4']","[data-src*='.mp4']"]:
        try:
            for el in page.locator(sel).all():
                try: add(el,gather_text(el))
                except Exception: pass
        except Exception: pass

    # ── 2. General clickable elements that might be download triggers ─────
    # Cast a wide net. The scoring/filtering decides what's actually relevant.
    general_selectors=[
        # Standard interactive elements
        "a","button",
        "[role='button']","[role='link']","[role='menuitem']","[role='option']",
        # Click-handler-bearing elements
        "[onclick]","li[onclick]","tr[onclick]","td[onclick]","div[onclick]","span[onclick]",
        # data-* signals (data-href is the wowgirls pattern; covers many CDN setups)
        "[data-href]","[data-url]","[data-src]","[data-download]",
        "[data-signed-url-key]",
        # Class-name hints that survive most styled-components hashes
        "[class*='download' i]","[class*='clickable' i]",
        # ARIA-tabbable elements (modal items often use this)
        "[tabindex='0']",
    ]
    for sel in general_selectors:
        try:
            for el in page.locator(sel).all():
                try:
                    t=gather_text(el)
                    if dl_re.search(t) or res_re.search(t):
                        add(el,t)
                except Exception: pass
        except Exception: pass

    # ── 3. Data-attribute markers (resolution declared explicitly) ────────
    for attr in ["[data-quality]","[data-res]","[data-resolution]","[data-format]"]:
        try:
            for el in page.locator(attr).all():
                try: add(el,gather_text(el))
                except Exception: pass
        except Exception: pass

    # ── 4. Ancestor walk fallback ─────────────────────────────────────────
    # Find any element whose visible text matches a pixel-height pattern
    # (e.g. "8K (HEVC)", "1080p", "2160p"); if its closest 3 ancestors include
    # a clickable element, treat that ancestor as the candidate. This catches
    # the styled-components case where text lives in <span>s nested inside
    # a clickable <div> with a hash classname we can't predict.
    try:
        text_holders=page.locator(
            ":text-matches('\\\\b(?:[1-9]\\\\d{2,3}\\\\s*p|[1-9]\\\\d{3}\\\\s*[x×]\\\\s*\\\\d{3,4}|"
            "[24568]K|HD|FHD|UHD|QHD)\\\\b','i')"
        ).all()
    except Exception: text_holders=[]
    for el in text_holders:
        try:
            ancestor=el.locator(
                "xpath=ancestor-or-self::*[self::a or self::button "
                "or @role='button' or @onclick or @data-href or @data-url "
                "or @data-src or contains(@class,'download') "
                "or contains(@class,'clickable')][1]"
            )
            if ancestor.count()>0:
                a=ancestor.first
                add(a,gather_text(a))
        except Exception: pass

    # P5-3b (v3.66.29): emission helper. Prefer runner.log_event when
    # available so the summary carries site_id, persists in the event
    # log, and broadcasts via SSE. Fall back to stderr when runner is
    # None (auto_detect.py path) or when log_event raises (fail-open —
    # never let a logging bug suppress the operator signal).
    def _emit_filter_summary(all_dropped):
        if not _dom_hp_filtered:
            return
        reasons=[r for _,r in _dom_hp_filtered[:10]]
        msg=(f"honeypot_filtered: count={len(_dom_hp_filtered)} "
             f"mode={_dom_hp_mode} reasons={reasons}")
        if all_dropped:
            msg+=" (all candidates dropped)"
        if runner is not None:
            try:
                runner.log_event(
                    "honeypot_filtered",
                    msg,
                    extra={
                        "count": len(_dom_hp_filtered),
                        "mode": _dom_hp_mode,
                        "reasons": reasons,
                        "all_dropped": bool(all_dropped),
                    },
                )
                return
            except Exception:
                # Fall through to stderr — a broken log_event must
                # not silence the operator-visible signal.
                pass
        sys.stderr.write(f"  {msg}\n")

    if not candidates:
        # P5-3: surface the filter-summary even when every
        # candidate was dropped — operator wants to know the
        # filter is the reason for no result.
        _emit_filter_summary(all_dropped=True)
        return None
    # P5-3 operator log — one event summarizing dropped candidates.
    _emit_filter_summary(all_dropped=False)
    # v3.66.x row 388: SAME WORK FIRST, then score, then size. `work` is the
    # LEADING key and only ever 1 or 0, so this reorders exactly one thing --
    # a candidate that provably belongs to this page now outranks one that
    # cannot be shown to. Nothing is dropped, and on a page where no identity
    # is derivable every work is 0 and this is the old (score,size) sort.
    candidates.sort(key=lambda c:(c.get("work",0),c["score"],c["size"]),
                    reverse=True)
    winner=candidates[0]
    # v3.65.2: include `locator` so _apply_quality_preference can return
    # a candidate other than `best`. Without it, the guard at the end of
    # that function (`if chosen and chosen.get("locator")`) always fails
    # and the user's quality_preference setting is silently ignored on
    # this path too. Same bug class as the learned-fast-path fix above.
    winner["_all_candidates"]=[
        {"text":c["text"],"score":c["score"],"size":c["size"],
         "locator":c["locator"],"work":c.get("work",0)}
        for c in candidates[:10]
    ]
    return winner

def disk_free_gb(path):
    try:
        p=Path(path) if path else Path.home()
        while not p.exists(): p=p.parent
        return shutil.disk_usage(p).free/1_073_741_824
    except Exception: return None

def safe_dest(path):
    if not path.exists(): return path
    stem,suffix=path.stem,path.suffix
    for i in range(1,1000):
        p=path.parent/f"{stem}_{i}{suffix}"
        if not p.exists(): return p
    return path.parent/f"{stem}_{uuid.uuid4().hex[:6]}{suffix}"
def fmt_bytes(b):
    """Compact human-readable size string. Mirrors the JS fmtB() helper."""
    if not b or b<=0: return ""
    if b>1073741824: return f"{b/1073741824:.1f} GB"
    if b>1048576:    return f"{b/1048576:.1f} MB"
    if b>1024:       return f"{b//1024:d} KB"
    return f"{b} B"

# ── K1: recognizer-plugin merge layer (v3.66.488) ─────────────────────────────
# Review-only fold of plugin recognizer verdicts into a builtin recognition
# scorecard. This rides detect.py's recognition layer WITHOUT touching the
# extraction_core guard (the same "extend recognition, don't touch the guarded
# core" trick as the builder-side _merge_supplemental_api). The fold is purely
# ADVISORY: a plugin verdict can NEVER auto-enable a template (posture
# invariant), low-confidence verdicts are demoted, and the builtin verdicts are
# preserved byte-for-byte (corpus-regression invariant).

# Keys a plugin might (accidentally or maliciously) use to smuggle a
# template-enable side-effect through the advisory channel. Stripped on fold.
_RECOGNIZER_ENABLE_KEYS = (
    "enabled", "enable", "auto_enable", "enable_template", "auto_enabled",
    "promote", "action",
)
_RECOGNIZER_CONFIDENCE_FLOOR = 0.5


def merge_plugin_recognitions(builtin, dom_excerpt="", network_summary=None,
                              ctx=None, confidence_floor=None):
    """Fold review-only plugin recognizer verdicts into ``builtin`` scorecard.

    ``builtin`` is the list of verdicts produced by BD's own recognition pass
    (each a dict, typically ``{player_family, confidence, source: "builtin",
    ...}``). It is returned UNCHANGED and UNMUTATED -- adding a recognizer
    plugin must never perturb an existing verdict.

    Each plugin verdict from :func:`bulk_downloader.plugins.run_recognizers` is
    normalized and appended, tagged ``source="plugin"`` + ``review_only=True``:
      * confidence is coerced to a float and clamped to ``[0.0, 1.0]``;
      * a verdict below ``confidence_floor`` (default
        ``_RECOGNIZER_CONFIDENCE_FLOOR``) is marked ``demoted=True`` (advisory,
        not trusted) -- otherwise ``demoted=False``;
      * every enable/promote/action key is STRIPPED (posture invariant -- a
        recognizer can never flip a template to enabled);
      * a plugin that raised / returned no opinion contributes nothing.

    Returns a new list: ``list(builtin) + [<plugin verdicts>]``.
    """
    floor = _RECOGNIZER_CONFIDENCE_FLOOR if confidence_floor is None else confidence_floor
    out = list(builtin or [])

    try:
        from . import plugins as _plugins
    except Exception:
        return out

    try:
        raw = _plugins.run_recognizers(dom_excerpt, network_summary, ctx)
    except Exception:
        # The runner is already exception-isolated per-plugin; a failure of the
        # whole pass must still fail-open to the builtin scorecard.
        return out

    for row in raw:
        if not row.get("ok"):
            continue
        verdict = row.get("verdict")
        if not isinstance(verdict, dict) or not verdict:
            continue
        fam = verdict.get("player_family")
        if not fam:
            continue
        # Copy + strip any enable side-channel BEFORE it reaches a caller.
        folded = {k: v for k, v in verdict.items()
                  if k not in _RECOGNIZER_ENABLE_KEYS}
        try:
            conf = float(folded.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        # A NaN/inf plugin confidence passes float() but evades the range clamp
        # below (nan < 0.0 and nan > 1.0 are each False), so it would fold in
        # stored non-finite and NON-demoted -- a low-confidence-demotion bypass.
        # Reject non-finite first so a nonsense confidence becomes 0.0 (demoted).
        if not math.isfinite(conf):
            conf = 0.0
        conf = 0.0 if conf < 0.0 else (1.0 if conf > 1.0 else conf)
        folded["confidence"] = conf
        folded["demoted"] = conf < floor
        folded["source"] = "plugin"
        folded["review_only"] = True
        folded["name"] = row.get("name")
        out.append(folded)

    return out
