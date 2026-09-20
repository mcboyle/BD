"""Streaming-safe HAR entry reduction for diagnostic archives."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

_KEEP_SUFFIXES = (".m3u8", ".mpd", ".m4s", ".ts")
_KEEP_TYPES = ("text/html", "application/json")
_DROP_TYPES = ("image/", "font/", "text/css")


def should_keep_entry(entry: Mapping[str, Any]) -> bool:
    request = entry.get("request") if isinstance(entry.get("request"), Mapping) else {}
    response = entry.get("response") if isinstance(entry.get("response"), Mapping) else {}
    url = str(request.get("url", "")).lower()
    # row902 fixer (E2): the suffix is judged on the PATH -- a signed or
    # query-bearing manifest (`master.m3u8?token=...`) is still a manifest.
    path = urlsplit(url).path
    # row902 fixer (E1): real captures carry parameters
    # (`text/html; charset=utf-8`); the keep rule matches the media type only.
    mime = str((response.get("content") or {}).get("mimeType", "")).lower()
    media_type = mime.split(";", 1)[0].strip()
    if any(token in url for token in ("/api/", "graphql", "manifest")) or path.endswith(_KEEP_SUFFIXES):
        return True
    if media_type in _KEEP_TYPES:
        return True
    return not (mime.startswith(_DROP_TYPES) or any(token in url for token in ("analytics", "tracking", "pixel")))


def compact_har(har: Mapping[str, Any]) -> dict[str, Any]:
    """Return a schema-preserving HAR containing only diagnostic entries."""
    log = har.get("log") if isinstance(har.get("log"), Mapping) else {}
    entries = log.get("entries") if isinstance(log.get("entries"), list) else []
    kept = [dict(entry) for entry in entries if isinstance(entry, Mapping) and should_keep_entry(entry)]
    return {"log": {"version": str(log.get("version", "1.2")), "creator": dict(log.get("creator") or {}), "entries": kept}}
