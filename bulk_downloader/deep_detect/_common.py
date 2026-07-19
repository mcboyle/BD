"""deep_detect._common -- cross-cutting private helpers + shared mutable metrics state.

A true leaf: imports nothing from sibling submodules. _DD_COUNTERS is mutated in place by
the metrics + orchestrate submodules through this one shared object.
"""

from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import (
    parse_qsl, urlencode, urljoin, urlparse, urlunparse,
)


_DD_COUNTERS = {
    "budget_truncated_count": 0,
    "manifests_followed_count": 0,
    "signed_urls_rejected_count": 0,
}


PROGRESSIVE_MEDIA_EXTENSIONS = (
    # Video containers
    ".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".wmv",
    ".flv", ".f4v", ".ogv", ".3gp", ".3g2",
    # Audio
    ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus",
    ".wav", ".flac", ".wma", ".aiff",
    # Documents
    ".pdf", ".epub", ".mobi", ".azw3",
    ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp",
    ".csv", ".tsv", ".rtf", ".txt",
    # Archives
    ".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2",
    # Installers / binaries
    ".exe", ".msi", ".msix", ".dmg", ".pkg",
    ".deb", ".rpm", ".appimage", ".apk", ".ipa",
    ".iso", ".img", ".vhd", ".vhdx", ".ova", ".ovf",
    ".bin", ".dat", ".torrent",
)


STREAM_MANIFEST_EXTENSIONS = (
    ".m3u8", ".mpd", ".ism", ".isml", ".f4m",
    ".smil", ".m3u", ".pls", ".xspf",
)


BINARY_MIME_PREFIXES = (
    "application/octet-stream",
    "application/zip", "application/x-rar", "application/x-7z",
    "application/x-tar", "application/gzip", "application/x-bzip2",
    "video/", "audio/", "image/",
    "application/pdf",
    "application/vnd.openxmlformats-",
    "application/vnd.ms-",
)


RESOLUTION_TIERS = (
    # (label, rank, w_min, h_min, label_terms)
    ("8k", 8000, 7000, 4000,
     ("8k", "4320p", "7680x4320", "8192x4320", "uhd2", "uhd-2")),
    ("6k", 6000, 5500, 3000,
     ("6k", "3160p", "3240p", "5760x3240", "6144x3160", "6144x3456")),
    ("5k", 5000, 4800, 2500,
     ("5k", "2880p", "5120x2880", "5120x2700", "retina 5k", "5k-retina")),
    ("4k", 4000, 3800, 2000,
     ("4k", "2160p", "uhd", "ultra hd", "ultrahd",
      "3840x2160", "4096x2160", "cinema4k")),
    ("1440p", 1440, 2500, 1400,
     ("1440p", "qhd", "quad hd", "2560x1440", "2k", "2048x1080")),
    ("1080p", 1080, 1800, 1000,
     ("1080p", "1920x1080", "full hd", "fhd")),
    ("720p", 720, 1200, 700,
     ("720p", "1280x720", "hd")),
    ("480p", 480, 800, 480,
     ("480p", "sd", "standard")),
)


PROVIDERS = (
    ("kaltura",
     ("kaltura.com", "cdnapi.kaltura.com"),
     ("kaltura", "kWidget", "entry_id", "partner_id", "uiconf_id",
      "KalturaPlayer", "kalturaIframePackageData")),
    ("brightcove",
     ("players.brightcove.net", "edge.api.brightcove.com",
      "bcvideo", "brightcove.net"),
     ("brightcove", "videoId", "video-id", "data-account",
      "policyKey", "catalog.getVideo")),
    ("wistia",
     ("wistia.com", "fast.wistia.com", "wistia.net"),
     ("wistia", "wistia_async_", "hashedId", "embedType")),
    ("vimeo",
     ("vimeo.com", "player.vimeo.com"),
     ("vimeo", "clip_id", "config_url")),
    ("youtube",
     ("youtube.com", "youtu.be", "youtube-nocookie.com"),
     ("ytInitialPlayerResponse", "streamingData", "adaptiveFormats",
      "hlsManifestUrl")),
    ("mux",
     ("mux.com", "stream.mux.com"),
     ("mux", "playbackId", "playback_id", "mux-player", "mux-video")),
    ("cloudflare_stream",
     ("cloudflarestream.com", "videodelivery.net",
      "iframe.videodelivery.net", "watch.cloudflarestream.com"),
     ("cloudflarestream", "videodelivery")),
    ("bunny_stream",
     ("bunnycdn.com", "b-cdn.net", "iframe.mediadelivery.net",
      "mediadelivery.net"),
     ("bunny", "mediadelivery")),
    ("panopto",
     ("panopto", ".hosted.panopto.com"),
     ("Panopto", "SessionId", "Viewer.aspx", "Embed.aspx")),
    ("vidyard",
     ("vidyard.com", "play.vidyard.com"),
     ("vidyard", "player_uuid")),
    ("dailymotion",
     ("dailymotion.com", "dai.ly"),
     ("dailymotion",)),
    ("sproutvideo",
     ("sproutvideo.com", "videos.sproutvideo.com"),
     ("sproutvideo",)),
    ("jwplayer",
     # cdn.jwplayer.com hosts the cloud media JSON; content.jwplatform.com
     # is the legacy cloud host; feeds.jwplayer.com is the playlist /
     # feed endpoint. jwpsrv.com appears on signed analytics/CDN URLs.
     ("cdn.jwplayer.com", "content.jwplatform.com",
      "feeds.jwplayer.com", "jwpsrv.com"),
     ("jwplayer", "jwplatform", "playlist", "mediaid")),
)


def _url_path(url: str) -> str:
    """Lowercased path component of a URL, without query or fragment.
    Used for extension matching — `?download=1` doesn't change the
    path's extension."""
    try:
        return (urlparse(url).path or "").lower()
    except Exception:
        return url.lower()


def _url_host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


_SIGNED_URL_SHORT_TTL_THRESHOLD = 300


URL_BEARING_ATTRS = (
    "href", "src", "data-href", "data-url", "data-src",
    "data-download", "data-file", "data-video", "data-audio",
    "data-mp4", "data-hls", "data-dash", "data-stream",
    "data-media", "data-asset-url", "data-playback-url",
    "data-fileurl", "data-downloadurl",
)


def _selector_for(el) -> str:
    """Cheap CSS selector. Prefer #id, then tag.class.class
    (max 3 classes), else tag."""
    if el.get("id"):
        return f"#{el['id']}"
    classes = [c for c in (el.get("class") or [])
               if isinstance(c, str) and c]
    if classes:
        return f"{el.name}." + ".".join(classes[:3])
    return el.name or "*"


_CONFIDENCE_BREAKPOINTS = (
    # (score_threshold, confidence_at_that_score)
    (0,    0.00),
    (50,   0.25),
    (80,   0.50),
    (100,  0.70),
    (165,  0.85),
    (250,  0.92),
    (400,  0.98),
)


def _score_to_confidence(score: int,
                         source_type: str | None = None) -> float:
    """Map an integer score onto a [0.0, 1.0] confidence via the
    piecewise-linear curve defined in `_CONFIDENCE_BREAKPOINTS`.

    `source_type` is accepted now but not yet used; v3.66.14 ships a
    source-type-agnostic curve. Future calibration may diverge per
    source_type once we have enough corpus signal to justify that
    (e.g. `direct_file` may deserve a lower asymptote than
    `hls_manifest` because the latter has structural corroboration).
    """
    s = int(score or 0)
    if s <= _CONFIDENCE_BREAKPOINTS[0][0]:
        return _CONFIDENCE_BREAKPOINTS[0][1]
    if s >= _CONFIDENCE_BREAKPOINTS[-1][0]:
        return _CONFIDENCE_BREAKPOINTS[-1][1]
    # Walk breakpoints and linearly interpolate within the segment
    # that contains `s`.
    for (s_lo, c_lo), (s_hi, c_hi) in zip(_CONFIDENCE_BREAKPOINTS,
                                           _CONFIDENCE_BREAKPOINTS[1:]):
        if s_lo <= s <= s_hi:
            t = (s - s_lo) / (s_hi - s_lo)  # in [0, 1]
            return round(c_lo + t * (c_hi - c_lo), 4)
    # Unreachable; the asymptote checks above are exhaustive.
    return 0.0


def _count_by_type(cands: List[dict]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for c in cands:
        t = c.get("source_type", "unknown")
        counts[t] = counts.get(t, 0) + 1
    return counts


def _parse_content_disposition(cd: str) -> dict:
    """Pull filename and disposition type out of a Content-Disposition
    header. Returns {"type": "attachment"|"inline"|"", "filename": "..."}.

    Handles:
      • quoted filenames (`filename="x.zip"`) including those containing
        a `;` inside the quotes
      • RFC 6266 quoted-string backslash escapes (`filename="a\\"b.txt"`)
      • unbalanced/unterminated quotes (extracts the content verbatim
        instead of silently dropping a character with `.strip('"')`)
      • RFC 5987 / 8187 extended encoding (`filename*=UTF-8''...`)
      • spec preference: `filename*=` wins over `filename=` when both
        are present (RFC 6266 §4.3 — extended form preferred for i18n)
    """
    out = {"type": "", "filename": ""}
    if not cd or not isinstance(cd, str):
        return out

    # Stateful tokenizer: split on `;` only outside double-quoted
    # regions. The original implementation used cd.split(";"), which
    # corrupted any filename that contained a semicolon (rare but legal
    # in quoted form: `filename="a;b.txt"`).
    parts: List[str] = []
    buf: List[str] = []
    in_quotes = False
    escape_next = False
    for ch in cd:
        if escape_next:
            buf.append(ch)
            escape_next = False
            continue
        if in_quotes:
            if ch == "\\":
                # Preserve the backslash in the raw token; we strip
                # it in the unquote helper below. Doing it this way
                # means an `escape_next` next-char is still treated
                # as literal (so `\\"` produces a `"` in the value).
                buf.append(ch)
                escape_next = True
                continue
            if ch == '"':
                in_quotes = False
            buf.append(ch)
            continue
        if ch == '"':
            in_quotes = True
            buf.append(ch)
            continue
        if ch == ";":
            parts.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())
    if parts:
        out["type"] = parts[0].lower()

    def _unquote_token(v: str) -> str:
        """Strip exactly one pair of surrounding double quotes (only
        if both ends are quoted) and process backslash escapes inside.
        The original code's `.strip('"').strip("'")` chain stripped
        unbalanced quotes and could collapse multiple layers."""
        v = v.strip()
        if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
            v = v[1:-1]
            # RFC 6266 quoted-string: \X → X for any X.
            result = []
            i = 0
            while i < len(v):
                if v[i] == "\\" and i + 1 < len(v):
                    result.append(v[i + 1])
                    i += 2
                else:
                    result.append(v[i])
                    i += 1
            return "".join(result)
        return v

    filename_legacy = ""
    filename_extended = ""
    for p in parts[1:]:
        if "=" not in p:
            continue
        k, _, v = p.partition("=")
        k = k.strip().lower()
        v = _unquote_token(v)
        if k == "filename" and v and not filename_legacy:
            filename_legacy = v
        elif k == "filename*" and v:
            # RFC 5987 extended: charset'lang'percent-encoded-name
            if "''" in v:
                _, _, encoded = v.partition("''")
                try:
                    from urllib.parse import unquote
                    filename_extended = unquote(encoded)
                except Exception:
                    pass
            else:
                # Malformed but present — fall back to the raw value.
                filename_extended = v

    # RFC 6266 §4.3: filename*= is preferred when both are present
    # (it carries explicit charset/i18n information).
    out["filename"] = filename_extended or filename_legacy
    return out
