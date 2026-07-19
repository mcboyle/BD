"""player_recognition.py — Wave 168. Builder-side, family-INDEPENDENT player
recognition + a registry scaffold the brand packs (169+) plug into.

Pure / stdlib-only. extraction_core.py stays byte-identical; this module is
supplemental and consumed by tools/build_template_from_wacz.py. Everything it
emits is review-only selector SHAPES + metadata — never values, never an
enabled template, never a guessed host. Recognition NEVER returns empty: an
unknown player still yields container/media selectors or an explicit review
note, so coverage degrades gracefully.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional
from urllib.parse import urljoin, urlparse, parse_qsl

# ── resolution tokens (generic, brand-independent) ──────────────────────────
_RES_VALUES = (144, 240, 360, 480, 540, 576, 720, 960, 1080, 1280, 1440, 2160, 4320)
_RES_RE = re.compile(
    r"(?<![\d.])(" + "|".join(str(r) for r in sorted(_RES_VALUES, reverse=True)) +
    r")p?(?![\d.])", re.I)


def _has(pattern: str, html: str) -> bool:
    return re.search(pattern, html, re.I | re.S) is not None


def harvest_resolutions(html: str) -> List[int]:
    """Resolution integers mentioned in menu/option/aria text. Generic."""
    found = {int(m.group(1)) for m in _RES_RE.finditer(html or "")}
    return sorted((r for r in found if r in _RES_VALUES), reverse=True)


# ── function-keyed control recognition (not brand-keyed) ────────────────────
def generic_selectors(html: str) -> Dict[str, object]:
    """Recognize a player's controls by FUNCTION — ARIA label / role / generic
    class token — rather than by a brand class. Returns selector SHAPES only."""
    html = html or ""
    sel: Dict[str, object] = {}
    player: Dict[str, str] = {}

    # container: native <video>, <media-controller>, or an explicit player role
    if _has(r"<media-controller", html):
        player["container"] = "media-controller"
    elif _has(r"<video", html):
        player["container"] = "video"
    elif _has(r'aria-label=["\']video player["\']', html):
        player["container"] = '[aria-label="video player"]'

    # play / pause / fullscreen / settings — by aria-label (function), generic
    if _has(r'aria-label=["\'][^"\']*\bplay\b', html):
        player["play_button"] = '[aria-label*="play" i]'
    if _has(r'title=["\']Play Video["\']', html):
        player.setdefault("play_button", 'button[title="Play Video"]')
    if _has(r'aria-label=["\'][^"\']*\bfullscreen\b', html):
        player["fullscreen"] = '[aria-label*="fullscreen" i]'
    if player:
        sel["player"] = player

    # settings / quality menu trigger (gear / settings / quality / HD)
    if _has(r'aria-label=["\'][^"\']*\b(settings|quality|resolution)\b', html):
        sel["settings"] = '[aria-label*="settings" i], [aria-label*="quality" i]'

    # quality: generic resolution tokens (+ a templated option if a repeated
    # "...{N}p..." aria shape exists)
    quality: Dict[str, object] = {}
    res = harvest_resolutions(html)
    if res:
        quality["available_resolutions"] = res
    if _has(r'aria-label=["\'][^"\']*\b\d{3,4}p\b', html):
        quality["resolution_option"] = '[aria-label*="{resolution}" i]'
    if quality:
        sel["quality"] = quality

    # download: a[download], aria-label*=download, or a button labelled download
    download: Dict[str, str] = {}
    if _has(r"<a[^>]+\bdownload\b", html):
        download["trigger"] = "a[download]"
    elif _has(r'aria-label=["\'][^"\']*\bdownload\b', html):
        download["trigger"] = '[aria-label*="download" i]'
    if download:
        sel["download"] = download

    return sel


# ── API classification by RESPONSE content-type (site-agnostic) ─────────────
def _content_type(entry: dict) -> str:
    for h in (entry.get("response_headers") or []):
        if isinstance(h, dict) and str(h.get("name", "")).lower() == "content-type":
            return str(h.get("value", "")).lower()
    return ""


def classify_apis(network: Optional[list]) -> Dict[str, int]:
    """Count delivery/API classes by response content-type — not path regex, so
    it generalizes across sites. Review-only metadata; does not touch
    extraction_core's api_patterns."""
    counts = {"hls": 0, "dash": 0, "progressive": 0, "json_api": 0}
    for e in network or []:
        if not isinstance(e, dict):
            continue
        ct = _content_type(e)
        if "mpegurl" in ct:
            counts["hls"] += 1
        elif "dash+xml" in ct:
            counts["dash"] += 1
        elif ct.startswith("video/"):
            counts["progressive"] += 1
        elif "json" in ct:
            counts["json_api"] += 1
    return {k: v for k, v in counts.items() if v}


# ── config-seam → rendition ladder (D++ Layer A) ────────────────────────────
# Parse a player's CONFIG SEAM to recover the rendition ladder the PAGE carries.
# Inline-source frameworks (jwplayer ``setup({playlist})``, video.js
# ``<source>``/``data-setup``, generic ``<video><source>``) embed the ladder in
# the markup → parse it. Manifest loaders (hls.js ``loadSource``, dash.js
# ``initialize``, shaka ``load``) embed only a manifest POINTER — the ladder
# lives in the .m3u8/.mpd (a network/manifest pass), so we return the seam + a
# manifest ``url_shape`` with NO fabricated rendition rows. Pure / stdlib-only.
# F2: every ``url_shape`` is query-stripped — a signed token never survives.
def _strip_query(url: str) -> str:
    return str(url or "").split("?", 1)[0].split("#", 1)[0]


def _container_from(url: str, mime: str) -> Optional[str]:
    path = _strip_query(url).lower()
    for ext in ("mp4", "m4v", "webm", "mov", "mkv", "ogv"):
        if path.endswith("." + ext):
            return "mp4" if ext == "m4v" else ext
    m = re.search(r"video/([a-z0-9.+-]+)", mime or "", re.I)
    if m:
        sub = m.group(1).lower()
        if sub in ("mp4", "webm"):
            return sub
        if sub in ("ogg",):
            return "webm"
        if sub in ("quicktime",):
            return "mp4"
    return None


def _protocol_from(url: str, mime: str) -> str:
    path = _strip_query(url).lower()
    mime = (mime or "").lower()
    if path.endswith(".m3u8") or "mpegurl" in mime:
        return "hls"
    if path.endswith(".mpd") or "dash+xml" in mime:
        return "dash"
    if path.endswith(".ism") or path.endswith("/manifest"):
        return "mss"
    return "progressive"


def _codec_from(mime: str) -> Optional[str]:
    m = re.search(r'codecs\s*=\s*["\']?([^"\';]+)', mime or "", re.I)
    return m.group(1).strip() if m else None


def _res_from(*candidates) -> Optional[int]:
    for c in candidates:
        if c in (None, ""):
            continue
        m = re.search(r"(\d{3,4})", str(c))
        if m:
            v = int(m.group(1))
            if 100 <= v <= 4320:
                return v
    return None


def _rendition_row(url, *, label=None, height=None, width=None, res=None,
                   bitrate=None, mime="") -> dict:
    br = None
    if bitrate not in (None, "") and str(bitrate).isdigit():
        br = int(bitrate)
    return {
        "resolution": _res_from(res, height, label),
        "bitrate": br,
        "codec": _codec_from(mime),
        "container": _container_from(url, mime),
        "protocol": _protocol_from(url, mime),
        "url_shape": _strip_query(url),
    }


def _iter_brace_objects(text: str):
    """Yield each top-level ``{...}`` chunk in ``text`` (brace-balanced, quote-aware)."""
    depth = 0
    start = -1
    quote = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                yield text[start:i + 1]
                start = -1
        i += 1


def _balanced_array(text: str, from_idx: int) -> str:
    """Return the ``[...]``-balanced slice starting at the first ``[`` >= from_idx."""
    lb = text.find("[", from_idx)
    if lb < 0:
        return ""
    depth = 0
    quote = ""
    i = lb
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[lb:i + 1]
        i += 1
    return text[lb:]


def _field(obj_text: str, name: str):
    m = re.search(r'["\']?' + name + r'["\']?\s*:\s*(?:["\']([^"\']*)["\']|(\d+))',
                  obj_text, re.I)
    if not m:
        return None
    return m.group(1) if m.group(1) is not None else m.group(2)


def _parse_source_objects(arr_text: str) -> list:
    rows = []
    for obj in _iter_brace_objects(arr_text):
        url = _field(obj, "file") or _field(obj, "src")
        if not url:
            continue
        rows.append(_rendition_row(
            url,
            label=_field(obj, "label"),
            height=_field(obj, "height"),
            width=_field(obj, "width"),
            bitrate=_field(obj, "bitrate"),
            mime=_field(obj, "type") or "",
        ))
    return rows


def _seam_jwplayer(html: str):
    if not re.search(r"\.setup\s*\(|jwplayer\s*\(", html, re.I):
        return None
    m = re.search(r"sources\s*:", html, re.I)
    if m:
        rows = _parse_source_objects(_balanced_array(html, m.end()))
        if rows:
            return {"seam": "jwplayer_playlist", "renditions": rows}
    return None


def _seam_html5_sources(html: str):
    rows = []
    # data-setup='{"sources":[...]}'
    for m in re.finditer(r"data-setup\s*=\s*(['\"])(.*?)\1", html, re.I | re.S):
        blob = m.group(2)
        sm = re.search(r"sources\s*:", blob, re.I) or re.search(r'"sources"\s*:', blob, re.I)
        if sm:
            rows += _parse_source_objects(_balanced_array(blob, sm.end()))
    # <source src=... type=... data-res=...> (quote-aware: type values can hold ")
    for m in re.finditer(r"<source\b[^>]*>", html, re.I):
        tag = m.group(0)
        src = re.search(r'\bsrc\s*=\s*(["\'])(.*?)\1', tag, re.I)
        if not src:
            continue
        mime = re.search(r'\btype\s*=\s*(["\'])(.*?)\1', tag, re.I)
        dres = re.search(r'\bdata-res\s*=\s*["\']?(\d{3,4})', tag, re.I)
        rows.append(_rendition_row(
            src.group(2),
            res=(dres.group(1) if dres else None),
            mime=(mime.group(2) if mime else ""),
        ))
    if not rows:
        return None
    seam = "videojs_source" if re.search(r"video-js|vjs-", html, re.I) else "html5_source"
    return {"seam": seam, "renditions": rows}


def _seam_manifest_loader(html: str):
    m = re.search(r"loadSource\s*\(\s*['\"]([^'\"]+)['\"]", html, re.I)
    if m:
        return {"seam": "hlsjs_loadsource", "renditions": [],
                "manifest_url_shape": _strip_query(m.group(1)),
                "manifest_protocol": "hls"}
    m = re.search(r"\.initialize\s*\([^;]*?['\"]([^'\"]+\.mpd[^'\"]*)['\"]", html, re.I | re.S)
    if m:
        return {"seam": "dashjs_initialize", "renditions": [],
                "manifest_url_shape": _strip_query(m.group(1)),
                "manifest_protocol": "dash"}
    m = re.search(r"\.load\s*\(\s*['\"]([^'\"]+\.(?:m3u8|mpd)[^'\"]*)['\"]", html, re.I)
    if m:
        proto = "hls" if ".m3u8" in m.group(1).lower() else "dash"
        return {"seam": "shaka_load", "renditions": [],
                "manifest_url_shape": _strip_query(m.group(1)),
                "manifest_protocol": proto}
    return None


_SEAM_PARSERS = (
    ("jwplayer", _seam_jwplayer),
    ("videojs", _seam_html5_sources),
    ("manifest", _seam_manifest_loader),
)
_VJS_LIKE = {"videojs", "plyr", "clappr", "mediaelement", "wordpress_mejs",
             "html5", "native_custom"}
_MANIFEST_LIKE = {"hlsjs", "dashjs", "shaka", "shakaplayer"}


def extract_config_seam(html: str, *, family: Optional[str] = None) -> Dict[str, object]:
    """Parse the player config seam → normalized rendition ladder (page-only).

    Returns ``{"seam": str|None, "renditions": [row, ...]}`` (+ optional
    ``manifest_url_shape`` / ``manifest_protocol`` for manifest-loader seams).
    Pure / stdlib-only; F2 (every ``url_shape`` query-stripped). ``family`` is an
    optional hint that only REORDERS the parsers — each is still tried, so
    detection never depends on a correct hint.

    Rendition row schema (uniform across frameworks):
      ``{resolution:int|None, bitrate:int|None, codec:str|None,
         container:str|None, protocol:str, url_shape:str}``
    """
    html = html or ""
    fam = (family or "").lower()

    def _rank(pid: str) -> int:
        if pid == fam:
            return 0
        if pid == "videojs" and fam in _VJS_LIKE:
            return 0
        if pid == "manifest" and fam in _MANIFEST_LIKE:
            return 0
        return 1

    for _pid, fn in sorted(_SEAM_PARSERS, key=lambda p: _rank(p[0])):
        try:
            res = fn(html)
        except Exception:
            res = None
        if res and (res.get("renditions") or res.get("manifest_url_shape")):
            res.setdefault("renditions", [])
            return res
    return {"seam": None, "renditions": []}


# ── protocol + framework-independent rendition ladder (D++ Layer B) ─────────
# Recognize protocol(s) + a normalized rendition ladder from the NETWORK log
# (independent of the player framework), plus poster/MSE disambiguation. Reuses
# the FROZEN extraction_core.manifest_resolutions as a fallback (called, never
# edited). Pure/stdlib; F2 — every url_shape query-stripped.
_MIN_MEDIA_BYTES = 1_000_000  # a real progressive body; below this = poster/preview/init fragment
_HLS_RES_WH = re.compile(r"RESOLUTION=(\d+)x(\d+)", re.I)
_MPD_REP = re.compile(r"<Representation\b[^>]*>", re.I)


def _har_value(headers, name: str) -> str:
    for h in headers or []:
        if isinstance(h, dict) and str(h.get("name", "")).lower() == name.lower():
            return str(h.get("value", ""))
    return ""


def _parse_hls_master(body: str, base_url: str):
    rows = []
    ll = "#EXT-X-PART" in (body or "")
    lines = (body or "").splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        if ln.upper().startswith("#EXT-X-STREAM-INF:"):
            attrs = ln.split(":", 1)[1]
            bw = re.search(r"BANDWIDTH=(\d+)", attrs, re.I)
            wh = _HLS_RES_WH.search(attrs)
            cod = re.search(r'CODECS="([^"]*)"', attrs, re.I)
            uri = None
            j = i + 1
            while j < len(lines):
                cand = lines[j].strip()
                if cand and not cand.startswith("#"):
                    uri = cand
                    break
                j += 1
            url_shape = _strip_query(urljoin(base_url, uri)) if uri else _strip_query(base_url)
            rows.append({
                "resolution": int(wh.group(2)) if wh else None,
                "bitrate": int(bw.group(1)) if bw else None,
                "codec": cod.group(1) if cod else None,
                "container": None,
                "protocol": "hls",
                "url_shape": url_shape,
            })
            i = (j + 1) if uri else (i + 1)
            continue
        i += 1
    return rows, ll


def _parse_dash(body: str, base_url: str):
    rows = []
    man = _strip_query(base_url)
    for m in _MPD_REP.finditer(body or ""):
        tag = m.group(0)
        h = re.search(r'\bheight\s*=\s*"(\d+)"', tag, re.I)
        bw = re.search(r'\bbandwidth\s*=\s*"(\d+)"', tag, re.I)
        cod = re.search(r'\bcodecs\s*=\s*"([^"]*)"', tag, re.I)
        rows.append({
            "resolution": int(h.group(1)) if h else None,
            "bitrate": int(bw.group(1)) if bw else None,
            "codec": cod.group(1) if cod else None,
            "container": None,
            "protocol": "dash",
            "url_shape": man,
        })
    return rows


def _res_from_path(low: str) -> Optional[int]:
    m = re.search(r"[._x-](\d{3,4})[pP]?(?:[._-]|\.mp4|\.webm|\.mov|\.mkv|$)", low)
    if m:
        v = int(m.group(1))
        if 100 <= v <= 4320:
            return v
    return None


def recognize_protocol(network_log: Optional[list]) -> Dict[str, object]:
    """Protocol(s) + normalized rendition ladder + poster/MSE disambiguation,
    recognized from the network log. See module header. Pure/stdlib; F2.

    Returns ``{protocols, primary, renditions, ll_hls, resumable, segments,
    media_candidates, rejected}``.
    """
    net = network_log or []
    protocols = set()
    renditions = []
    ll_hls = False
    resumable = False
    seg_fmp4 = 0
    seg_ts = 0
    media_candidates = []
    rejected = []
    seen = set()

    def _add_candidate(path):
        if path not in seen:
            media_candidates.append(path)
            seen.add(path)

    for e in net:
        if not isinstance(e, dict):
            continue
        url = str(e.get("url") or "")
        if url.startswith("blob:"):
            protocols.add("mse_blob")
            continue
        if not url.startswith(("http://", "https://")):
            continue
        path = _strip_query(url)
        low = path.lower()
        leaf = low.rsplit("/", 1)[-1]
        rh = e.get("response_headers") or []
        ct = _har_value(rh, "content-type").lower()
        status = str(e.get("response_status") or "")
        cl = _har_value(rh, "content-length")
        clen = int(cl) if cl.isdigit() else None
        ranges = ("bytes" in _har_value(rh, "accept-ranges").lower()
                  or status == "206" or bool(_har_value(rh, "content-range")))
        body = e.get("response_body")

        # segments (fMP4/CMAF, TS) — counted, never a direct-download candidate
        if low.endswith(".m4s") or leaf.startswith("init") and leaf.endswith(".mp4"):
            seg_fmp4 += 1
            continue
        if low.endswith(".ts"):
            seg_ts += 1
            continue

        # HLS manifest
        if low.endswith(".m3u8") or "mpegurl" in ct:
            protocols.add("hls")
            if body:
                rows, ll = _parse_hls_master(body, url)
                if ll:
                    ll_hls = True
                renditions += rows
            _add_candidate(path)
            continue

        # DASH manifest
        if low.endswith(".mpd") or "dash+xml" in ct:
            protocols.add("dash")
            if body:
                rows = _parse_dash(body, url)
                renditions += rows
            _add_candidate(path)
            continue

        # MSS
        if low.endswith(".ism") or low.endswith("/manifest"):
            protocols.add("mss")
            _add_candidate(path)
            continue

        # poster / image — never the media
        if ct.startswith("image/"):
            rejected.append({"url_shape": path, "reason": "image-content-type"})
            continue

        # progressive direct media (full file)
        if ct.startswith("video/") or low.endswith((".mp4", ".webm", ".mov", ".mkv")):
            if clen is not None and clen < _MIN_MEDIA_BYTES:
                rejected.append({"url_shape": path, "reason": "sub-threshold-body"})
                continue
            if any(t in low for t in ("/poster", "poster.", "/thumb", "thumbnail")):
                rejected.append({"url_shape": path, "reason": "poster-url"})
                continue
            protocols.add("progressive")
            if ranges:
                resumable = True
            renditions.append({
                "resolution": _res_from_path(low),
                "bitrate": None,
                "codec": None,
                "container": _container_from(path, ct),
                "protocol": "progressive",
                "url_shape": path,
            })
            _add_candidate(path)
            continue

    order = ["hls", "dash", "mss", "progressive", "mse_blob"]
    present = [p for p in order if p in protocols]
    return {
        "protocols": sorted(protocols),
        "primary": present[0] if present else None,
        "renditions": renditions,
        "ll_hls": ll_hls,
        "resumable": resumable,
        "segments": {"fmp4": seg_fmp4, "ts": seg_ts},
        "media_candidates": media_candidates,
        "rejected": rejected,
    }


# ── Layer C: protection recognition (D++ cut 3) ─────────────────────────────
# DETECTION ONLY (F2): tags / param NAMES / shapes — never a token, signed-URL,
# or cookie VALUE. The redactor keeps query param NAMES (values -> PLACEHOLDER),
# header NAMES (credential values dropped), and query-stripped script hosts, so
# everything below works on a fully-redacted capture without ever touching a
# secret. The cookie jar itself is PLACEHOLDER under redaction; cookie-name
# detection is therefore best-effort (live dev/synthetic only) and degrades to
# empty gracefully.

# signing-scheme fingerprints by query-param NAME (lower-cased)
_SIGN_AKAMAI = {"hdnts", "hdnea", "__token__", "__hdnea__", "__hdnts__"}
_SIGN_CF_TRIPLET = ({"policy", "signature", "key-pair-id"},
                    {"expires", "signature", "key-pair-id"})
_SIGN_GENERIC = {"token", "expires", "expire", "expiry", "signature", "sig",
                 "hash", "st", "e", "ip"}
# too-generic single/ambiguous names excluded from the generic set on their own
_SIGN_GENERIC_STRONG = {"token", "expires", "expire", "expiry", "signature",
                        "sig", "hash"}

_TOKEN_REFRESH_RE = re.compile(
    r"(token[/_-]?(refresh|renew|sign)|refresh[/_-]?token|get[_-]?signed[_-]?url|"
    r"re[_-]?sign|/sign(ed)?[_/]|/playback[_-]?token|/auth/token|license[_-]?token)",
    re.I)

# anti-bot vendor signatures: cookie names, request/response header names, and
# script/host markers (all NAMES — never values).
_ANTIBOT = {
    "akamai":     {"cookies": {"_abck", "bm_sz", "ak_bmsc", "bm_mi", "bm_sv"},
                   "headers": {"akamai-grn"},
                   "markers": ("akam.net/akam", "/akam/")},
    "cloudflare": {"cookies": {"cf_clearance", "__cf_bm", "__cfduid"},
                   "headers": {"cf-ray", "cf-mitigated", "cf-chl-bypass"},
                   "markers": ("challenges.cloudflare.com",)},
    "datadome":   {"cookies": {"datadome"},
                   "headers": {"x-datadome", "x-dd-b", "x-datadome-cid"},
                   "markers": ("js.datadome.co", "datadome.co", "ct.captcha-delivery.com")},
    "perimeterx": {"cookies": {"_px", "_pxhd", "_pxvid", "_pxff", "_px3"},
                   "headers": {"x-px", "x-px-authorization"},
                   "markers": ("perimeterx.net", "px-cdn.net", "/px/main", "human-challenge")},
    "kasada":     {"cookies": {"kpsdk-ct", "kpsdk-cd"},
                   "headers": {"x-kpsdk-ct", "x-kpsdk-cd", "x-kpsdk-v"},
                   "markers": ("kasada", "/kpsdk")},
    "queue_it":   {"cookies": {"queue-it", "queueitaccepted", "queue-it_"},
                   "headers": {"x-queueit-ai"},
                   "markers": ("queue-it.net",)},
}

_CAPTCHA = {
    "turnstile": ("cf-turnstile", "challenges.cloudflare.com/turnstile",
                  "data-sitekey", "turnstile"),
    "hcaptcha":  ("hcaptcha.com", "h-captcha", "js.hcaptcha.com"),
    "recaptcha": ("g-recaptcha", "recaptcha/api.js", "gstatic.com/recaptcha",
                  "www.google.com/recaptcha", "grecaptcha"),
}
# turnstile and recaptcha both carry data-sitekey; require a vendor-specific
# marker so a bare data-sitekey doesn't tag both.
_CAPTCHA_REQUIRED = {
    "turnstile": ("cf-turnstile", "challenges.cloudflare.com"),
    "recaptcha": ("g-recaptcha", "recaptcha", "grecaptcha"),
    "hcaptcha":  ("hcaptcha", "h-captcha"),
}

_LICENSE_HOST_RE = re.compile(
    r"(license|getlicense|licence|/wv\b|/widevine|/playready|/fairplay|"
    r"acquirelicense|drmtoday|/ls\b|keydelivery)", re.I)

# precondition headers a gated media request typically requires
_PRECOND_HEADERS = ("referer", "origin", "range")


def _header_names(headers) -> set:
    out = set()
    for h in headers or []:
        if isinstance(h, dict) and h.get("name"):
            out.add(str(h["name"]).lower())
    return out


def _cookie_name_list(cookies) -> list:
    """Cookie NAMES only, from a name-bearing list. A redacted capture sets the
    jar to a PLACEHOLDER string -> treated as no cookies. Never reads values."""
    out = []
    if isinstance(cookies, list):
        for c in cookies:
            if isinstance(c, dict) and c.get("name"):
                out.append(str(c["name"]))
            elif isinstance(c, str) and c:
                out.append(c)
    return out


def _is_media_entry(url_low: str, ct: str) -> bool:
    return (ct.startswith("video/") or ct.startswith("audio/")
            or "mpegurl" in ct or "dash+xml" in ct
            or url_low.endswith((".mp4", ".m3u8", ".mpd", ".webm", ".ts", ".m4s")))


def recognize_protection(network_log, *, cookies=None, html="",
                         script_srcs=None):
    """Protection posture — signing scheme · token-refresh · anti-bot vendor ·
    captcha · DRM/EME · header preconditions · cookie names — recognized from the
    network log + cookies (names) + page markup. DETECTION ONLY / F2: tags,
    NAMES and SHAPES only, never a token / signed-URL / cookie value.

    Returns ``{signing, token_refresh, anti_bot, anti_bot_signals, captcha,
    drm, drm_reasons, drm_license_hosts, header_preconditions, cookie_names}``.
    """
    net = network_log or []
    html = html or ""
    cookie_names = _cookie_name_list(cookies)
    cookie_low = {c.lower() for c in cookie_names}

    # blob over html + script srcs + network urls/hosts for marker scans
    marker_parts = [html.lower()]
    marker_parts += [str(s).lower() for s in (script_srcs or [])]
    for e in net:
        if isinstance(e, dict) and e.get("url"):
            marker_parts.append(str(e["url"]).lower())
    marker_blob = " ".join(marker_parts)

    # ── signing scheme by query-param NAME ──────────────────────────────────
    sign_schemes = set()
    sign_param_names = set()
    sign_hosts = set()
    token_refresh = []
    seen_refresh = set()
    header_pre = []

    for e in net:
        if not isinstance(e, dict):
            continue
        url = str(e.get("url") or "")
        if not url.startswith(("http://", "https://")):
            continue
        parsed = urlparse(url)
        host = parsed.netloc
        names = {k.lower() for k, _ in parse_qsl(parsed.query, keep_blank_values=True)}
        url_shape = _strip_query(url)
        low = url_shape.lower()
        rh = e.get("response_headers") or []
        ct = _har_value(rh, "content-type").lower()

        if names:
            matched_here = set()
            if names & _SIGN_AKAMAI:
                sign_schemes.add("akamai_token")
                matched_here |= (names & _SIGN_AKAMAI)
            if any(trip <= names for trip in _SIGN_CF_TRIPLET):
                sign_schemes.add("cloudfront")
                matched_here |= {"policy", "signature", "key-pair-id", "expires"} & names
            if "x-amz-signature" in names:
                sign_schemes.add("aws_sigv4")
                matched_here |= {n for n in names if n.startswith("x-amz-")}
            if "jwt" in names or ({"exp"} <= names and ({"sig"} <= names or {"signature"} <= names)):
                sign_schemes.add("jwt")
                matched_here |= names & {"jwt", "exp", "sig", "signature"}
            generic = names & _SIGN_GENERIC_STRONG
            # only call it a *signing* generic when it's on a media-ish URL or
            # paired (token+expires/sig) — avoids tagging a plain ?token= search.
            if generic and (_is_media_entry(low, ct) or len(generic) >= 2):
                sign_schemes.add("generic_token")
                matched_here |= generic
            if matched_here:
                sign_param_names |= matched_here
                if host:
                    sign_hosts.add(host)

        # token-refresh endpoint by path NAME
        if _TOKEN_REFRESH_RE.search(parsed.path):
            if url_shape not in seen_refresh:
                token_refresh.append(url_shape)
                seen_refresh.add(url_shape)

        # header preconditions: gated media (403/401) missing Referer/Origin/Range
        status = str(e.get("response_status") or "")
        if status in ("401", "403") and _is_media_entry(low, ct):
            req_names = _header_names(e.get("request_headers"))
            missing = [h for h in _PRECOND_HEADERS if h not in req_names]
            if missing:
                header_pre.append({"url_shape": url_shape, "status": status,
                                   "missing": missing})

    # ── anti-bot vendor by cookie/header/script NAME ────────────────────────
    anti_bot = set()
    anti_bot_signals = {}
    all_header_names = set()
    for e in net:
        if isinstance(e, dict):
            all_header_names |= _header_names(e.get("response_headers"))
            all_header_names |= _header_names(e.get("request_headers"))
    for vendor, sig in _ANTIBOT.items():
        signals = []
        signals += sorted(n for n in sig["cookies"] if n.lower() in cookie_low)
        signals += sorted(n for n in sig["headers"] if n in all_header_names)
        signals += sorted(m for m in sig["markers"] if m in marker_blob)
        if signals:
            anti_bot.add(vendor)
            anti_bot_signals[vendor] = signals

    # ── captcha widget by markup/script ─────────────────────────────────────
    captcha = set()
    for name, needles in _CAPTCHA.items():
        if any(n in marker_blob for n in needles):
            req = _CAPTCHA_REQUIRED.get(name, ())
            if not req or any(r in marker_blob for r in req):
                captcha.add(name)

    # ── DRM / EME (reuse detect_drm) + license-server host shape ────────────
    drm, drm_reasons = detect_drm(html, scripts=script_srcs, network=net)
    drm_license_hosts = []
    seen_lic = set()
    for e in net:
        if not isinstance(e, dict):
            continue
        url = str(e.get("url") or "")
        if not url.startswith(("http://", "https://")):
            continue
        if _LICENSE_HOST_RE.search(urlparse(url).path):
            shape = _strip_query(url)
            if shape not in seen_lic:
                drm_license_hosts.append(shape)
                seen_lic.add(shape)

    return {
        "signing": {
            "schemes": sorted(sign_schemes),
            "param_names": sorted(sign_param_names),
            "hosts": sorted(sign_hosts),
        },
        "token_refresh": token_refresh,
        "anti_bot": sorted(anti_bot),
        "anti_bot_signals": anti_bot_signals,
        "captcha": sorted(captcha),
        "drm": bool(drm),
        "drm_reasons": list(drm_reasons),
        "drm_license_hosts": drm_license_hosts,
        "header_preconditions": header_pre,
        "cookie_names": sorted(set(cookie_names)),
        # Gap 2 (redaction ceiling): a TRUE media-vs-page scope map needs
        # per-request cookie attribution, which the redacted jar (PLACEHOLDER)
        # destroys. Best honest F2 approximation: tag cookie NAMES that are
        # known session/anti-bot gates, never claim a per-request map.
        "gating_cookie_names": sorted(
            n for n in set(cookie_names)
            if n.lower() in _GATING_COOKIE_NAMES
            or any(n.lower() in sig["cookies"] for sig in _ANTIBOT.values())),
    }


_GATING_COOKIE_NAMES = {
    "sessionid", "session", "sess", "sid", "phpsessid", "jsessionid",
    "auth", "auth_token", "access_token", "token", "jwt", "bearer",
    "remember_token", "_session", "connect.sid", "laravel_session",
    "asp.net_sessionid", "csrftoken", "xsrf-token",
}


# ── Layer D: auxiliary content recognition (D++ cut 4) ──────────────────────
# Caption/subtitle + multi-audio + storyboard + chapter tracks, and SSAI / ad
# markers, recognized from the network log + page markup + the config seam.
# Pure/stdlib, F2 (every url_shape query-stripped). NAMES/SHAPES only.

def _track_row(url, *, kind=None, lang=None, label=None, source=None):
    return {k: v for k, v in (
        ("url_shape", _strip_query(url) if url else None),
        ("kind", kind), ("lang", lang), ("label", label), ("source", source),
    ) if v is not None}


def recognize_aux(network_log, *, html="", config_seam=None):
    """Auxiliary content: caption/subtitle tracks, multi-audio, storyboard/
    sprite, chapters, and SSAI/ad markers. Pure/stdlib, F2.

    Returns ``{captions, audio, storyboard, chapters, ssai}``.
    """
    net = network_log or []
    html = html or ""
    captions, audio, storyboard, chapters, ssai = [], [], [], [], []

    # ── <track> elements in markup ──────────────────────────────────────────
    for m in re.finditer(r"<track\b[^>]*>", html, re.I):
        tag = m.group(0)
        kind = (re.search(r'kind=["\']([^"\']+)', tag, re.I) or [None, ""])[1].lower()
        src = (re.search(r'src=["\']([^"\']+)', tag, re.I) or [None, None])[1]
        lang = (re.search(r'srclang=["\']([^"\']+)', tag, re.I) or [None, None])[1]
        label = (re.search(r'label=["\']([^"\']+)', tag, re.I) or [None, None])[1]
        row = _track_row(src, kind=kind or "subtitles", lang=lang, label=label,
                         source="html_track")
        if kind == "chapters":
            chapters.append(row)
        elif kind == "metadata" and (label and "thumb" in label.lower()
                                     or (src and ("storyboard" in src.lower()
                                                  or "sprite" in src.lower()
                                                  or "thumb" in src.lower()))):
            storyboard.append(row)
        elif kind in ("captions", "subtitles", "descriptions", ""):
            captions.append(row)

    # ── manifest bodies: HLS EXT-X-MEDIA + DASH AdaptationSet ───────────────
    for e in net:
        if not isinstance(e, dict):
            continue
        url = str(e.get("url") or "")
        body = e.get("response_body") or ""
        low = _strip_query(url).lower()
        ct = _har_value(e.get("response_headers"), "content-type").lower()

        # HLS media playlists
        if body and (low.endswith(".m3u8") or "mpegurl" in ct):
            for ml in re.finditer(r"#EXT-X-MEDIA:([^\n\r]+)", body, re.I):
                attrs = ml.group(1)
                typ = (re.search(r'TYPE=([A-Z-]+)', attrs, re.I) or [None, ""])[1].upper()
                lang = (re.search(r'LANGUAGE="([^"]+)"', attrs, re.I) or [None, None])[1]
                uri = (re.search(r'URI="([^"]+)"', attrs, re.I) or [None, None])[1]
                name = (re.search(r'NAME="([^"]+)"', attrs, re.I) or [None, None])[1]
                row = _track_row(urljoin(url, uri) if uri else None,
                                 kind=typ.lower(), lang=lang, label=name,
                                 source="hls_ext_x_media")
                if typ == "SUBTITLES" or typ == "CLOSED-CAPTIONS":
                    captions.append(row)
                elif typ == "AUDIO":
                    audio.append(row)
            # HLS image/storyboard
            if re.search(r"#EXT-X-IMAGE-STREAM-INF", body, re.I):
                storyboard.append(_track_row(url, kind="storyboard",
                                             source="hls_image_stream"))
            # SSAI / ad markers (SCTE-35)
            if re.search(r"SCTE35|#EXT-X-CUE-OUT|#EXT-X-DATERANGE[^\n]*SCTE",
                         body, re.I):
                ssai.append({"kind": "scte35", "source": "hls_daterange",
                             "url_shape": _strip_query(url)})

        # DASH AdaptationSet
        if body and (low.endswith(".mpd") or "dash+xml" in ct):
            for asm in re.finditer(r"<AdaptationSet\b([^>]*)>", body, re.I):
                a = asm.group(1)
                cont = (re.search(r'contentType="([^"]+)"', a, re.I) or [None, ""])[1].lower()
                mime = (re.search(r'mimeType="([^"]+)"', a, re.I) or [None, ""])[1].lower()
                lang = (re.search(r'lang="([^"]+)"', a, re.I) or [None, None])[1]
                if cont == "text" or "text/" in mime or "vtt" in mime:
                    captions.append(_track_row(url, kind="subtitles", lang=lang,
                                               source="dash_text_adaptationset"))
                elif cont == "audio" or mime.startswith("audio/"):
                    audio.append(_track_row(url, kind="audio", lang=lang,
                                            source="dash_audio_adaptationset"))

    # ── network-level VTT/SRT + ad servers ──────────────────────────────────
    for e in net:
        if not isinstance(e, dict):
            continue
        url = str(e.get("url") or "")
        low = _strip_query(url).lower()
        if low.endswith((".vtt", ".srt")):
            tgt = storyboard if ("storyboard" in low or "sprite" in low or "thumb" in low) else captions
            tgt.append(_track_row(url, kind="webvtt", source="network_vtt"))
        if re.search(r"/(vmap|vast)\b|[?&](vmap|vast)=|\.vast\b|googleads|imasdk|/ad(s)?/",
                     low):
            ssai.append({"kind": "vmap_vast", "source": "ad_server",
                         "url_shape": _strip_query(url)})

    # ── config seam tracks (jwplayer playlist[].tracks) ─────────────────────
    if isinstance(config_seam, dict):
        for t in config_seam.get("tracks") or []:
            if isinstance(t, dict) and t.get("url_shape"):
                captions.append(_track_row(t["url_shape"], kind=t.get("kind", "subtitles"),
                                           lang=t.get("lang"), source="config_seam"))

    def _dedup(rows):
        seen, out = set(), []
        for r in rows:
            key = (r.get("url_shape"), r.get("kind"), r.get("lang"))
            if key not in seen:
                seen.add(key)
                out.append(r)
        return out

    return {
        "captions": _dedup(captions),
        "audio": _dedup(audio),
        "storyboard": _dedup(storyboard),
        "chapters": _dedup(chapters),
        "ssai": _dedup(ssai),
    }


# ── family registry scaffold (brand packs register here in 169+) ────────────
class PlayerFamily:
    def __init__(self, fid: str, label: str,
                 detect: Callable[[str, list, list, list], float],
                 selectors: Callable[[str], dict] = None,
                 delivery: str = "unknown", embed: str = "inline",
                 policy: str = "normal", live: bool = False):
        self.id = fid
        self.label = label
        self._detect = detect
        self._selectors = selectors or (lambda html: {})
        self.delivery = delivery
        self.embed = embed
        self.policy = policy
        self.live = live


FAMILIES: List[PlayerFamily] = []  # 169+ brand recognizers append here.


def register_family(family: PlayerFamily) -> None:
    FAMILIES.append(family)


# ── top-level detection (generic foundation) ────────────────────────────────
_IFRAME_EMBED_HOSTS = re.compile(
    r"player\.vimeo\.com|youtube\.com/embed|youtube-nocookie\.com|player\.twitch\.tv|"
    r"dailymotion\.com/embed|facebook\.com/plugins/video|iframe\.cloudflarestream\.com|"
    r"videodelivery\.net|fast\.wistia\.(?:com|net)", re.I)


_DRM_NEEDLES = (
    "widevine", "playready", "fairplay", "drmtoday", "ezdrm", "axinom", "keyos",
    "com.widevine", "com.microsoft.playready", "com.apple.fps",
    "requestmediakeysystemaccess", "clearkey", ".wvm",
)  # NB: bare "/license"/"licenseserver" intentionally excluded — too generic to
#     justify escalating to drm_never; require a real EME/DRM-vendor signal.
_AD_NEEDLES = (
    "imasdk.googleapis.com", "securepubads", "doubleclick.net", "googlesyndication",
    "adsbygoogle", "ima3", "vpaid", "vmap", "video-ads", "ima-ad-container",
)  # NB: bare "ad-container" excluded — too generic; rely on ad-network/standard tells.


def _scan_blob(html, scripts, network) -> str:
    parts = [html or ""]
    parts += [str(s) for s in (scripts or [])]
    parts += [str(e.get("url")) for e in (network or []) if isinstance(e, dict) and e.get("url")]
    return " ".join(parts).lower()


def detect_drm(html, scripts=None, network=None):
    """(bool, reasons) — DRM/EME present. A flag, not a family: a DRM-protected
    videojs is still videojs. Recognition only; NEVER aids bypass.

    Cut 4 (Gap 1): in addition to the vendor needles, recognize the STRUCTURAL
    EME/DASH signals the plan lists — a bare ``encrypted`` media event, a
    ``keySystems`` config block, and a DASH ``<ContentProtection>`` element —
    so a generic-EME or DASH-CENC site is not mis-read as not-DRM."""
    blob = _scan_blob(html, scripts, network)
    reasons = sorted({n for n in _DRM_NEEDLES if n in blob})
    # structural signals (no vendor name required)
    if re.search(r"""addeventlistener\(\s*['"]encrypted['"]|['"]onencrypted['"]|\bonencrypted\b""", blob):
        reasons.append("eme:encrypted-event")
    if "keysystems" in blob:
        reasons.append("eme:keysystems")
    if "contentprotection" in blob or "content-protection" in blob:
        reasons.append("dash:contentprotection")
    return bool(reasons), sorted(set(reasons))


def detect_ads(html, scripts=None, network=None):
    """(bool, reasons) — ad/VAST/IMA wrapper present. A flag: the content player
    underneath is kept; ad media must be excluded, not mined as content."""
    blob = _scan_blob(html, scripts, network)
    reasons = sorted({n for n in _AD_NEEDLES if n in blob})
    return bool(reasons), reasons


def _compute_delivery(html: str, api_classes: dict, family_hint: str = "") -> str:
    """Delivery is orthogonal to player family: derive it from the network's
    response content-types first, then media URLs in the DOM, then the matched
    family's hint, then blob. Returns a '+'-joined class string."""
    classes = []
    if api_classes.get("hls"):
        classes.append("hls")
    if api_classes.get("dash"):
        classes.append("dash")
    if api_classes.get("progressive"):
        classes.append("progressive")
    if not classes:
        if _has(r"\.m3u8", html):
            classes.append("hls")
        if _has(r"\.mpd", html):
            classes.append("dash")
        if _has(r"\.mp4", html):
            classes.append("progressive")
    if not classes and family_hint in ("hls", "dash", "progressive"):
        classes.append(family_hint)
    blob = _has(r'<video[^>]+src=["\']blob:', html)
    if not classes:
        return "mse_blob" if blob else "unknown"
    # blob is just MSE plumbing when concrete delivery is observed — don't tag it.
    return "+".join(classes)


# ── v3.66.171 storage-tell signal channel ────────────────────────────────────
# A runtime storage marker (a key the player actually wrote) is stronger
# evidence of the ACTIVE engine than static HTML/script markers, which can be a
# leftover skin or a shared shell. Patterns are matched case-insensitively
# against each storage key NAME (never values). Additive; review-only.
_STORAGE_CONFIRMS = {
    "theoplayer": (r"^THEOplayer\b", r"theoplayer[-_.]", r"\btheo[-_]?session"),
    "jwplayer": (r"^jwplayer\b", r"jwplayer[-_.]", r"\bjwplayer-video"),
    "bitmovin": (r"^bitmovin\b", r"bitmovin[-_]"),
    "shaka": (r"shaka[-_]?(player|store|offline)",),
    "videojs": (r"^vjs[-_]", r"videojs[-_]"),
    # AI-3 (v300): flowplayer stores keys like ``flowplayer/flowplayer/uuid``,
    # ``flowplayer/--fp-sub-*`` and ``flowplayerTestStorage`` — the old
    # ``^flowplayer[-_.]`` matched none. ``flowplayer`` is an exclusive brand
    # prefix, so any key starting with it confirms the engine.
    "flowplayer": (r"^flowplayer",),
    "clappr": (r"^clappr[-_.]",),
    # AI-2 fix: web-component players' exclusive storage markers (promoter).
    "media_chrome": (r"^media-chrome[-_]", r"media-chrome-pref"),
    # v3.66.318: real vidstack runtime storage uses the ``vds-player:`` key
    # prefix (display-bg, font-family, font-size, text-color, ...) — the original
    # ``^vidstack[:_-]`` / ``vidstack::`` markers matched none of those, so a
    # genuine vidstack-over-hls site never confirmed via the storage channel.
    # ``vds-player:`` is exclusive to the vidstack player.
    "vidstack": (r"^vidstack[:_-]", r"vidstack::", r"^vds-player[:_-]"),
}
# Families that are commonly a SHARED shell or a leftover skin and so should be
# demoted in favour of a storage-confirmed engine that co-fires.
_STATIC_SHELL = {"videojs", "hlsjs", "dashjs", "cloudflare_stream", "mux",
                 "media_chrome", "react_player"}


def _storage_confirmed(storage_keys) -> set:
    keys = [str(k) for k in (storage_keys or [])]
    hits = set()
    for fid, pats in _STORAGE_CONFIRMS.items():
        if any(re.search(p, k, re.I) for k in keys for p in pats):
            hits.add(fid)
    return hits


# AI-2 fix: families whose recognizers can fire on a generic class fragment and
# so must show an EXCLUSIVE tell (custom element / lib script / storage marker)
# before they outrank the generic native fallback.
_WEAK_BRAND = {"vidstack", "media_chrome", "wowza_player"}


def _streaming_present(html, network) -> bool:
    """True if the NETWORK log shows real HLS/DASH activity (a manifest or segment
    URL) — distinguishes a real streaming engine from a leftover progressive-only
    skin. Network-only and extension-anchored on purpose: a `.webmanifest` (PWA) or
    a stray `.m3u8` substring in lib/ad text must NOT count."""
    for e in (network or []):
        if not isinstance(e, dict):
            continue
        u = str(e.get("url") or "").split("?", 1)[0]
        if re.search(r"\.(m3u8|mpd|m4s|ts)$", u, re.I):
            return True
    return False


def _weak_brand_has_tell(fid, html, script_srcs, iframe_hosts, storage_keys, storage_confirmed) -> bool:
    """Exclusive evidence for a weak-brand family: its custom element, lib script,
    iframe/script host, or a confirmed storage marker."""
    srcs = " ".join(str(s).lower() for s in (script_srcs or []))
    if fid == "vidstack":
        return ("<media-player" in html) or ("vidstack" in srcs) or ("vidstack" in storage_confirmed)
    if fid == "media_chrome":
        return ("<media-controller" in html) or ("media-chrome" in srcs) or ("media_chrome" in storage_confirmed)
    if fid == "wowza_player":
        hosts = " ".join(str(h).lower() for h in (iframe_hosts or []))
        return ("wowza" in srcs) or ("wowza" in hosts) or any(re.search(r"wowza", str(k), re.I) for k in (storage_keys or []))
    return True


# v3.66.318 — vidstack-over-hls promotion. Real-world vidstack overwhelmingly
# ships the DEFAULT layout (no literal ``<media-player>`` element in the
# snapshot), persists UI under the ``vds-player:`` storage prefix, and sits on
# hls.js as its HLS engine. That exact shape was being systematically
# misattributed to hlsjs (the engine underneath, a static-shell co-firer) or to
# plyr — vidstack's default layout reuses plyr-ish skin classes
# (``plyr__controls``/``data-plyr``), so the plyr recognizer co-fires and can
# outscore vidstack's weak-brand match. When vidstack's evidence is STRONG (a
# high vds- class density AND either its own ``vidstack-*.js`` script or a
# confirmed ``vds-player:`` storage marker) it is unmistakably the active
# engine, so the confusable co-firers demote below it. Density-gated so a faint
# incidental vds- trace with no script and no ``vds-player:`` storage does NOT
# promote (e.g. a page that merely references a vds- class fragment).
_VIDSTACK_VDS_DENSITY_MIN = 100
_VIDSTACK_CONFUSABLE = {"hlsjs", "plyr"}


def _vidstack_strong_evidence(html, script_srcs, storage_confirmed) -> bool:
    """Strong vidstack evidence: dense ``vds-`` markup AND (its own script OR a
    confirmed ``vds-player:`` storage marker). A literal ``<media-player>``
    element is NOT required — genuine vidstack default layouts omit it; a faint
    ``vds-`` trace alone is NOT enough (it must co-occur with the script or the
    storage tell, so an incidental class fragment cannot promote)."""
    if len(re.findall(r"vds-", html or "")) < _VIDSTACK_VDS_DENSITY_MIN:
        return False
    srcs = " ".join(str(s).lower() for s in (script_srcs or []))
    return ("vidstack" in srcs) or ("vidstack" in (storage_confirmed or set()))


# v3.66.261 — broader markup-brand floor (generalizes the deferred one-family
# videojs demoter). These families' recognizers can fire on a generic CSS-class
# fragment (a leftover skin) yet expose a SEPARABLE exclusive tell: a lib script,
# an exclusive custom element/id/data-attr, or a storage marker. When EVERY
# eligible candidate is one of these AND none carries a tell AND the page is a
# fundamentally native <video> with OBSERVED progressive delivery (not adaptive
# HLS/DASH, not MSE, and not a media-less `unknown`), they all demote to the
# generic native_custom fallback (a leftover skin is not the active engine).
_MARKUP_BRANDS = {"videojs", "jwplayer", "brid_tv", "plyr",
                  "flowplayer", "clappr", "mediaelement"}
# Lib-script needles per family (a present script is an exclusive tell). Kept
# slightly broader than the recognizer's own script check (e.g. "video.min.js")
# so a real minified dist counts as a tell even where the recognizer scored on
# class alone — errs toward KEEPING a brand, never toward over-demoting.
_MARKUP_BRAND_SCRIPTS = {
    "videojs": ("video.js", "videojs", "video-js", "video.min.js"),
    "jwplayer": ("jwplayer",),
    "brid_tv": ("brid",),
    "plyr": ("plyr",),
    "flowplayer": ("flowplayer",),
    "clappr": ("clappr",),
    "mediaelement": ("mediaelement",),
}
# Exclusive NON-class markup tells (a custom element / id / data-attr unique to
# the family). brid_tv / flowplayer / clappr / mediaelement have none that is
# exclusive enough — they rely on the script/storage channel (`data-player` is
# too generic to be a clappr tell).
_MARKUP_BRAND_HTML_TELLS = {
    "videojs": (r"data-vjs-player",),
    "jwplayer": (r'id=["\']jwplayer',),
    "plyr": (r"data-plyr",),
}


def _markup_brand_has_tell(fid, html, script_srcs, storage_confirmed) -> bool:
    """A markup-brand is tell-backed (NOT a leftover skin) when its engine is
    storage-confirmed, its lib script is present, or an exclusive non-class
    element/id/data-attr appears. Bare CSS-class markup is NOT a tell. A family
    outside _MARKUP_BRANDS is never subject to this floor (returns True)."""
    if fid not in _MARKUP_BRANDS:
        return True
    if fid in (storage_confirmed or set()):
        return True
    srcs = " ".join(str(s).lower() for s in (script_srcs or []))
    if any(n in srcs for n in _MARKUP_BRAND_SCRIPTS.get(fid, ())):
        return True
    return any(re.search(p, html or "", re.I) for p in _MARKUP_BRAND_HTML_TELLS.get(fid, ()))


def detect(html: str, *, script_srcs: Optional[list] = None,
           iframe_hosts: Optional[list] = None,
           network: Optional[list] = None,
           storage_keys: Optional[list] = None,
           struct_tiebreak: bool = False) -> Dict[str, object]:
    """Top-level recognition. Runs every registered family recognizer (pack A
    auto-registers), then the generic structural pass, and ALWAYS returns a
    non-empty result. Family and delivery are computed independently. Output is
    review-only metadata + selector shapes; nothing is enabled.

    ``storage_keys`` (v3.66.171, optional) are localStorage/sessionStorage key
    NAMES from the capture; a key matching a family's exclusive storage marker
    confirms that engine and arbitrates against a co-firing static shell.
    """
    html = html or ""
    script_srcs = script_srcs or []
    iframe_hosts = iframe_hosts or []
    network = network or []

    # Lazy-load the brand pack (no-op if absent or already registered).
    try:
        import player_families
        player_families.ensure_registered()
    except Exception:
        pass

    candidates = []
    for fam in FAMILIES:
        try:
            score = float(fam._detect(html, script_srcs, iframe_hosts, network))
        except Exception:
            score = 0.0
        if score > 0:
            candidates.append({"family": fam.id, "score": round(score, 3)})
    candidates.sort(key=lambda c: -c["score"])

    notes: List[str] = []
    sel = generic_selectors(html)
    api_classes = classify_apis(network)

    iframe_hit = bool(iframe_hosts and any(_IFRAME_EMBED_HOSTS.search(str(h)) for h in iframe_hosts)) \
        or bool(_has(r"<iframe[^>]+src=[\"\'][^\"\']*(" + _IFRAME_EMBED_HOSTS.pattern + r")", html))

    family = "unknown"
    policy = "normal"
    family_hint = ""

    _THRESHOLD = 0.3
    # AI-6 (v3.66.321) — opt-in struct_embed tie-breaker. Two eligible candidates
    # whose scores differ by <= this are a "genuine tie" the structural-embedding
    # verdict may break (same-tier, neither storage-confirmed). Scores are rounded
    # to 3dp; 0.05 keeps it to near-equal pairs only.
    _TIEBREAK_EPS = 0.05
    _TIER = {"normal": 0, "third_party_review_only": 1, "review_only": 2}
    # v3.66.170 edge arbitration: WordPress video PLUGINS vs WP-core MediaElement.js.
    _WP_PLUGINS = {"presto_player", "fv_player"}
    _WP_CORE = {"wordpress_mejs", "mediaelement"}

    def _famobj(fid):
        return next((f for f in FAMILIES if f.id == fid), None)

    # Precedence: a concrete inline player (normal) beats a hosted embed
    # (third_party) beats a live/RTC platform (review_only); score breaks ties
    # within a tier. So jwplatform_hosted never outranks inline jwplayer and a
    # live platform never overwrites an inline VOD player.
    _elig = [c for c in candidates if c["score"] >= _THRESHOLD]
    # A WordPress video plugin (Presto/FV) renders on a page that ALSO carries
    # WP-core MediaElement.js markers, so wordpress_mejs co-fires. The plugin is
    # the real player — it replaces core playback and its delivery differs (hls
    # vs progressive) — so demote WP-core deterministically whenever a plugin is
    # eligible, rather than letting a score quirk (plugin class-only 0.6 vs mejs
    # 0.7) pick the core fallback. Core stays visible in `candidates` + a note.
    _wp_core_demoted: List[str] = []
    if _WP_PLUGINS & {c["family"] for c in _elig}:
        _wp_core_demoted = [c["family"] for c in _elig if c["family"] in _WP_CORE]
        _elig = [c for c in _elig if c["family"] not in _WP_CORE]
    # v3.66.171 storage-tell arbitration: a runtime storage marker is stronger
    # evidence of the ACTIVE engine than static markup. When a storage-confirmed
    # engine is eligible, demote co-firing static-shell families that are NOT
    # themselves storage-confirmed (e.g. a video.js skin left in the DOM) so the
    # real engine wins — same shape as the WP plugin-vs-core demotion. Demoted
    # families stay visible in `candidates` + a note; nothing is fabricated.
    storage_confirmed = _storage_confirmed(storage_keys)
    _storage_demoted: List[str] = []
    if storage_confirmed and any(c["family"] in storage_confirmed for c in _elig):
        _storage_demoted = [c["family"] for c in _elig
                            if c["family"] in _STATIC_SHELL
                            and c["family"] not in storage_confirmed]
        _elig = [c for c in _elig if c["family"] not in _storage_demoted]
    # AI-2 fix — weak-brand evidence floor: a web-component/niche player (vidstack,
    # media_chrome, wowza_player) that fired on a generic class fragment without its
    # exclusive element/script/host/storage tell is demoted below the native
    # fallback (it stays in `candidates` + a note; nothing fabricated).
    _weak_floored: List[str] = []
    if any(c["family"] in _WEAK_BRAND for c in _elig):
        _weak_floored = [c["family"] for c in _elig if c["family"] in _WEAK_BRAND
                         and not _weak_brand_has_tell(c["family"], html, script_srcs,
                                                      iframe_hosts, storage_keys, storage_confirmed)]
        _elig = [c for c in _elig if c["family"] not in _weak_floored]
    # v3.66.318 — vidstack-over-hls promotion. When vidstack is eligible WITH
    # strong evidence (dense vds- markup + its script and/or ``vds-player:``
    # storage), it is the active engine; demote the confusable co-firers (the
    # underlying hls.js engine and a plyr skin-class match) below it so vidstack
    # wins. Storage-tell arbitration above already demotes hls.js when vidstack
    # is ``vds-player:``-confirmed (hls.js is a static shell); this additionally
    # covers plyr, whose confusable skin classes are not a static shell, and the
    # script-only case (no storage). Demoted families stay visible in
    # ``candidates`` + a note; nothing is fabricated.
    _vidstack_promoted: List[str] = []
    if (any(c["family"] == "vidstack" for c in _elig)
            and _vidstack_strong_evidence(html, script_srcs, storage_confirmed)):
        _vidstack_promoted = [c["family"] for c in _elig
                              if c["family"] in _VIDSTACK_CONFUSABLE]
        _elig = [c for c in _elig if c["family"] not in _vidstack_promoted]
    # v3.66.261 — no-tell weak-markup -> native_custom. Generalizes the deferred
    # one-family videojs demoter (which whack-a-moled: demoting the top markup-only
    # match just promoted the next stacked weak brand — jwplayer on newsensations,
    # brid_tv on w3schools). When EVERY eligible candidate is a markup-class brand
    # carrying NO lib-script / exclusive element/id/data-attr / storage tell, AND
    # the page is fundamentally a native <video> with no adaptive (HLS/DASH) or MSE
    # delivery, demote them ALL so the generic native_custom fallback wins. Demoted
    # families stay visible in `candidates` + a note; nothing is fabricated.
    _markup_demoted: List[str] = []
    if _elig and all(c["family"] in _MARKUP_BRANDS for c in _elig) and not any(
            _markup_brand_has_tell(c["family"], html, script_srcs, storage_confirmed)
            for c in _elig):
        _delivery_pre = _compute_delivery(html, api_classes, "")
        _adaptive_or_mse = any(t in _delivery_pre for t in ("hls", "dash", "mse_blob"))
        # Require POSITIVE progressive delivery (observed native media) — not merely
        # the ABSENCE of adaptive. A thin/synthetic page whose delivery is `unknown`
        # (no media seen) is not enough evidence to override a brand class; only a
        # confirmed native progressive <video> demotes (newsensations: progressive
        # .mp4). This keeps a brand class on a media-less page (e.g. a membership
        # gate that never loaded the player) labelled by brand for review.
        _native = _has(r"<video", html) or _has(r"<media-controller", html)
        if _native and ("progressive" in _delivery_pre) and not _adaptive_or_mse:
            _markup_demoted = [c["family"] for c in _elig]
            _elig = []
    _elig.sort(key=lambda c: (_TIER.get((_famobj(c["family"]).policy if _famobj(c["family"]) else "normal"), 0), -c["score"]))

    # AI-6 (v3.66.321) — structural-embedding verdict. Computed unconditionally as
    # the advisory ``struct_embed`` field (the 320 contract). It is ALSO consulted,
    # and ONLY when ``struct_tiebreak`` is opted in, to break a genuine rule-level
    # tie below. classify() returns None when there is no player-namespace structure
    # or the baked model is absent; never raises into recognition.
    struct_embed = None
    try:
        import player_struct_embed as _pse
        struct_embed = _pse.classify(html, script_srcs=script_srcs,
                                     storage_keys=storage_keys, network=network)
    except Exception:
        struct_embed = None

    # Opt-in tie-break (default OFF -> byte-identical to 320). Fires ONLY when the
    # rules are left with a genuine 2-way tie: the top two eligible candidates are
    # in the SAME policy tier, their scores are within _TIEBREAK_EPS, and NEITHER is
    # storage-confirmed (a storage tell is the strong signal and is never
    # overridden). The structural verdict must be high-confidence and name one of
    # those two candidates. It can ONLY swap the two tied candidates -- it never
    # invents a family the rules didn't surface and never reaches past the top-2.
    if struct_tiebreak and struct_embed and len(_elig) >= 2:
        _a, _b = _elig[0], _elig[1]
        _fa, _fb = _famobj(_a["family"]), _famobj(_b["family"])
        _tier_a = _TIER.get(_fa.policy if _fa else "normal", 0)
        _tier_b = _TIER.get(_fb.policy if _fb else "normal", 0)
        _is_tie = (_tier_a == _tier_b
                   and abs(_a["score"] - _b["score"]) <= _TIEBREAK_EPS
                   and _a["family"] not in storage_confirmed
                   and _b["family"] not in storage_confirmed)
        if _is_tie and struct_embed.get("confidence") == "high":
            _pick = struct_embed.get("family")
            if _pick == _b["family"] and _pick != _a["family"]:
                _elig[0], _elig[1] = _elig[1], _elig[0]
                notes.append(
                    "Rule recognizer left a tie between %s and %s (equal score, "
                    "same tier, neither storage-confirmed); the structural-embedding "
                    "tie-break selected %s (high confidence)."
                    % (_a["family"], _b["family"], _pick))

    _top = _elig[0] if _elig else None

    if _top is not None:
        family = _top["family"]
        fam = _famobj(family)
        if _wp_core_demoted:
            notes.append("WordPress core MediaElement.js present as fallback under the "
                         "%s plugin (core: %s) — the plugin is the active player."
                         % (family, ", ".join(sorted(_wp_core_demoted))))
        if _storage_demoted:
            notes.append("Storage tells confirm %s as the active engine (exclusive "
                         "storage key present); static-shell co-firer(s) demoted: %s."
                         % (family, ", ".join(sorted(_storage_demoted))))
        if _weak_floored:
            notes.append("Weak-brand match(es) without an exclusive element/script/"
                         "storage tell demoted below the native fallback: %s."
                         % ", ".join(sorted(_weak_floored)))
        if _vidstack_promoted:
            notes.append("Strong vidstack evidence (dense vds- markup + vidstack "
                         "script and/or vds-player: storage); confusable co-firer(s) "
                         "demoted below vidstack: %s."
                         % ", ".join(sorted(_vidstack_promoted)))
        # note any higher-scoring live/hosted families that were demoted
        _demoted = [c["family"] for c in candidates
                    if c["score"] >= _THRESHOLD and c["family"] != family]
        if fam:
            policy = fam.policy
            family_hint = fam.delivery
            if policy == "third_party_review_only":
                # cross-origin / third-party embed — recognize it, never claim
                # internal controls (not in the capture).
                sel.pop("quality", None)
                sel.pop("settings", None)
                notes.append("Third-party embedded/hosted player; internals are not in the "
                             "capture and must not be fabricated. ToS/review-only.")
            elif policy == "review_only":
                notes.append("Live/P2P transport — generally not a standard downloadable file; "
                             "recognition/review only.")
            else:
                for k, v in (fam._selectors(html) or {}).items():
                    if k not in sel:
                        sel[k] = v
                    elif isinstance(sel.get(k), dict) and isinstance(v, dict):
                        for sk, sv in v.items():
                            sel[k].setdefault(sk, sv)
                notes.append("Brand player matched — confirm selectors during review.")
    elif iframe_hit:
        family = "iframe_embed"
        policy = "third_party_review_only"
        notes.append("Cross-origin embedded player; internals are not in the capture "
                     "and must not be fabricated. Recognition is the embed + ToS/review policy.")
        sel.pop("quality", None)
        sel.pop("settings", None)

    delivery = _compute_delivery(html, api_classes, family_hint)

    if family == "unknown":
        if _has(r'<video[^>]+src=["\']blob:', html) and "mse_blob" in delivery and \
           "hls" not in delivery and "dash" not in delivery and "progressive" not in delivery:
            family = "mse_blob_custom"
            notes.append("MSE/blob source with no brand match — recover media segments "
                         "from the network log; no progressive file URL on the page.")
        elif _has(r"<video", html) or _has(r"<media-controller", html):
            family = "native_custom"
            notes.append("Generic recognition only — confirm selectors during review; "
                         "no brand-specific player matched.")

    if _markup_demoted:
        notes.append("Weak markup-only brand match(es) with no lib-script / exclusive "
                     "element / storage tell on a native <video> page demoted to the "
                     "generic native fallback (leftover skin, not the active engine): %s."
                     % ", ".join(sorted(_markup_demoted)))

    if family == "unknown" and not sel:
        notes.append("No recognizable player markup; review the capture manually.")

    # Guardrail flags (orthogonal to family — a DRM-protected / ad-wrapped page
    # still has a real content player). DRM escalates policy to drm_never.
    drm, drm_reasons = detect_drm(html, script_srcs, network)
    ad, ad_reasons = detect_ads(html, script_srcs, network)
    concerns: List[str] = []
    if drm:
        concerns.append("drm_eme_review_only")
        policy = "drm_never"
        notes.append("DRM/EME present (%s) — recognition ONLY; never attempt to bypass DRM/"
                     "Widevine/PlayReady/FairPlay or acquire/decrypt licenses." %
                     ", ".join(drm_reasons[:4]))
    if ad:
        concerns.append("ad_wrapper")
        notes.append("Ad/VAST/IMA wrapper present (%s) — exclude ad media from content "
                     "recognition; do not treat ad creative as the target video." %
                     ", ".join(ad_reasons[:4]))

    # Platform / workflow hints (Packs H + I) — separate channel; never a family,
    # never selectors, structure/label only.
    workflow_hints: List[dict] = []
    platform_hints: List[dict] = []
    try:
        import player_platform_hints as _ph
        _hosts = list(iframe_hosts) + [
            (re.findall(r"https?://([^/]+)", str(e.get("url")))[:1] or [""])[0]
            for e in network if isinstance(e, dict) and e.get("url")]
        _h = _ph.detect_platform_hints(html, script_srcs, _hosts)
        workflow_hints = _h.get("wrappers", [])
        platform_hints = _h.get("shells", [])
        if _h.get("has_membership_workflow"):
            notes.append("Membership/entitlement workflow present — hint only; "
                         "no entitlement or download availability is inferred.")
    except Exception:
        pass

    # AI-2/AI-7 fix — capture quality + confidence. Zero media/segment activity
    # means the player never initialized (so an engine storage tell could not
    # materialize) — a static-shell family pick from such a capture is low-confidence.
    _media_n = 0
    for e in network:
        if not isinstance(e, dict):
            continue
        u = str(e.get("url") or "").split("?", 1)[0]
        if str(e.get("type")) in ("media", "manifest") or re.search(r"\.(m3u8|mpd|m4s|ts|mp4|webm|m4a)$", u, re.I):
            _media_n += 1
    capture_quality = "ok"
    if _media_n == 0 and family in _STATIC_SHELL and family not in storage_confirmed:
        capture_quality = "thin_no_media"
        notes.append("No media/segment activity in this capture — the player did not "
                     "initialize, so an engine storage tell could not appear; the family "
                     "is a static-shell guess only. Low confidence — route to review.")
    # AI-7: a storage tell is the reliable precision signal; markup-only is weaker.
    if capture_quality == "thin_no_media":
        confidence = "low_review"
    elif family in storage_confirmed:
        confidence = "high"
    else:
        confidence = "medium"

    _seam = extract_config_seam(html, family=family)

    return {
        "player_family": family,
        "confidence": confidence,
        "capture_quality": capture_quality,
        "candidates": candidates,
        "delivery": delivery,
        "policy": policy,
        "selectors": sel,
        "config_seam": _seam.get("seam"),
        "renditions": _seam.get("renditions", []),
        "api_classes": api_classes,
        "flags": {"drm": drm, "ad_overlay": ad},
        "concerns": concerns,
        "workflow_hints": workflow_hints,
        "platform_hints": platform_hints,
        "signals": [],
        "storage_confirmed": sorted(storage_confirmed),
        "struct_embed": struct_embed,
        "notes": notes,
    }
