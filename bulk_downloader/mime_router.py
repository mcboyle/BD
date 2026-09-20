"""Row 932: route an extensionless HTTP media stream by its response headers.

Video platforms serve media from dynamic API routes with no ``.mp4`` in the
path, so ``TransportMixin._direct_media_route`` (which judges the URL alone)
returns ``(None, None)`` and the download falls to the click path. The
response headers are the definitive signal: ``Content-Type: video/*`` or
``audio/*``, or ``application/octet-stream`` served with
``Accept-Ranges: bytes`` (a byte-addressable blob, the shape every media CDN
serves; a non-ranged octet-stream is an opaque blob and is refused).

``route_by_headers`` returns the same ``(media_url, destination_name)``
tuple as ``_direct_media_route`` so the accepted result plugs into the same
handover in runner_transport.py: the ``_durl`` branch assigns
``direct_url, suggested`` and the transfer runs through
``self._http_download(page_url, page, ctx, file_url, final_path)``.
Streaming manifests are ffmpeg's (hls_downloader), never httpx's.
"""

from __future__ import annotations

from email.message import Message
from pathlib import PurePosixPath
from urllib.parse import unquote, urljoin, urlparse

_MEDIA_PREFIXES = ("video/", "audio/")
_OCTET = "application/octet-stream"
_SUBTYPE_EXT = {
    "video/mp4": ".mp4", "video/x-m4v": ".m4v", "video/webm": ".webm",
    "video/x-matroska": ".mkv", "video/quicktime": ".mov",
    "video/x-msvideo": ".avi", "video/x-ms-wmv": ".wmv", "video/x-flv": ".flv",
    "video/ogg": ".ogv", "video/mpeg": ".mpg", "video/3gpp": ".3gp",
    "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/aac": ".aac",
    "audio/ogg": ".ogg", "audio/wav": ".wav", "audio/x-wav": ".wav",
    "audio/flac": ".flac", "audio/webm": ".weba",
}
_DEFAULT_EXT = ".mp4"


def _header(headers, name):
    want = name.lower()
    items = headers.items() if hasattr(headers, "items") else (headers or ())
    for key, value in items:
        if str(key).lower() == want:
            return str(value or "")
    return ""


def _content_type(headers):
    return _header(headers, "Content-Type").split(";", 1)[0].strip().lower()


def _is_streaming(ctype):
    try:
        from . import hls_downloader as _hls
    except Exception:
        return False
    return bool(_hls.is_hls_content_type(ctype) or _hls.is_dash_content_type(ctype))


def media_verdict(headers):
    """``"media"`` | ``"streaming"`` | ``"non_media"`` from response headers alone."""
    ctype = _content_type(headers)
    if not ctype:
        return "non_media"
    if _is_streaming(ctype):
        return "streaming"
    if ctype.startswith(_MEDIA_PREFIXES):
        return "media"
    if ctype == _OCTET and _header(headers, "Accept-Ranges").strip().lower() == "bytes":
        return "media"
    return "non_media"


def _disposition_filename(headers):
    raw = _header(headers, "Content-Disposition")
    if not raw:
        return ""
    msg = Message()
    msg["Content-Disposition"] = raw
    name = msg.get_filename() or ""
    return PurePosixPath(unquote(name.strip())).name


def _destination_name(absolute, headers, ctype):
    name = _disposition_filename(headers)
    if not name:
        segments = [s for s in unquote(urlparse(absolute).path or "").split("/") if s.strip()]
        name = segments[-1].strip() if segments else "download"
    if not PurePosixPath(name).suffix:
        name += _SUBTYPE_EXT.get(ctype, _DEFAULT_EXT)
    return name


def route_by_headers(url, headers, page_url=""):
    """``(media_url, destination_name)`` when the response headers say media, else ``(None, None)``."""
    if not url or not isinstance(url, str):
        return None, None
    raw = url.strip()
    if not raw or raw.startswith(("#", "javascript:", "mailto:", "data:")):
        return None, None
    try:
        absolute = raw if raw.startswith(("http://", "https://")) else urljoin(page_url or "", raw)
        parsed = urlparse(absolute)
    except Exception:
        return None, None
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None, None
    if media_verdict(headers) != "media":
        return None, None
    return absolute, _destination_name(absolute, headers, _content_type(headers))
