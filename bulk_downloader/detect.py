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
    for m in re.finditer(r"(\d{3,4})\s*[x×]\s*(\d{3,4})",t):
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

# ─── DOWNLOAD HELPERS ─────────────────────────────────────────────────────────
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
    # locate any matching elements and pick the one with the highest
    # resolution score among them. Skip the full sweep when we hit.
    #
    # v3.65.2: preserve ALL scored matches in _all_candidates, not just
    # the winner. The downstream _apply_quality_preference (Phase 67)
    # picks from this list — without alternatives present, "prefer
    # 1080p" silently degrades to "take whatever scored highest"
    # because there's nothing in the candidate list to pick from.
    # Effect was that on every site where teaching had successfully
    # produced row_selectors (i.e. the cache was actually working),
    # the user's quality_preference setting was silently ignored.
    if learned and isinstance(learned, dict):
        row_sels = learned.get("row_selectors") or []
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
                    txt_parts = [el.inner_text() or ""]
                    for attr in ("data-href","data-url","data-src","title","aria-label"):
                        try:
                            v = el.get_attribute(attr)
                            if v: txt_parts.append(v)
                        except Exception: pass
                    txt = " ".join(txt_parts)
                    if NON_VIDEO_RE.search(txt): continue
                    score = res_score(txt)
                    size = parse_size_bytes(txt)
                    # Phase 17.17: opportunistically extract hash hints from
                    # standard data-* attributes. These rarely appear, but
                    # when they do, they let us verify the download was
                    # bit-exact instead of just "looked plausible".
                    hash_info = _extract_hash_hint(el, txt)
                    entry = {"locator": el, "text": txt[:160].strip(),
                             "score": max(0, score), "size": size}
                    if hash_info:
                        entry["expected_hash_algo"] = hash_info[0]
                        entry["expected_hash_value"] = hash_info[1]
                    scored.append(entry)
                except Exception: continue
            if scored:
                scored.sort(key=lambda c: (c["score"], c["size"]),
                            reverse=True)
                best_match = dict(scored[0])  # copy so _all_candidates assign doesn't recurse
                best_match["_via_learned"] = True
                best_match["_learned_sel"] = sel
                best_match["_all_candidates"] = [
                    {"text": c["text"], "score": c["score"], "size": c["size"],
                     "locator": c["locator"]}
                    for c in scored[:10]
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
    dl_re=re.compile(r"download|\bdl\b|save|get\s*it|grab|\.mp4|\.mkv|\.mov|\.webm|\.m4v|\.ts",re.I)
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

    def add(el,text):
        t=(text or "").strip()
        if not t or t in seen: return
        if NON_VIDEO_RE.search(t): return  # skip Zip/Photos/Trailer/etc.
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
        s=res_score(t)
        # Include if it scored OR it has a download-shaped word.
        # If only a download word matches (e.g. bare 'Download' button with
        # no quality info), keep it with score=0 — the file-size tiebreaker
        # will at least pick the largest one.
        if s<0 and not dl_re.search(t): return
        seen.add(t)
        candidates.append({"locator":el,"text":t[:160],
                           "score":max(0,s),"size":parse_size_bytes(t)})

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
    candidates.sort(key=lambda c:(c["score"],c["size"]),reverse=True)
    winner=candidates[0]
    # v3.65.2: include `locator` so _apply_quality_preference can return
    # a candidate other than `best`. Without it, the guard at the end of
    # that function (`if chosen and chosen.get("locator")`) always fails
    # and the user's quality_preference setting is silently ignored on
    # this path too. Same bug class as the learned-fast-path fix above.
    winner["_all_candidates"]=[
        {"text":c["text"],"score":c["score"],"size":c["size"],
         "locator":c["locator"]}
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
