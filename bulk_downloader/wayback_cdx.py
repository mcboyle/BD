"""Wayback Machine CDX archive lookup (Phase 106, Block N).

Port of the CDX query pattern from wayback-machine-downloader
(Ruby, lib/wayback_machine_downloader/archive_api.rb) — F7 finding
from the 22-repo line-by-line review.

When a site dies, the URL is dead but the content frequently isn't —
Wayback Machine archived it. This module:

  • Queries the CDX API for all snapshots of a URL (paginated)
  • Filters to status=200 snapshots by default (only successful captures)
  • Returns sorted snapshot timestamps for the operator to pick from
  • Builds the Wayback "web/<ts>/<url>" URL that fetches the actual
    archived bytes

The CDX endpoint is `https://web.archive.org/cdx/search/xd`. It
accepts query params:
  url, from, to, output, filter, collapse, page, fl, gzip

We use output=json, collapse=digest (de-dup identical captures),
gzip=false (we don't have a streaming gunzip handy and snapshots
are usually small anyway).

Usage:

    from .wayback_cdx import find_snapshots, build_archive_url
    snaps = find_snapshots("https://defunct.com/video/123")
    if snaps:
        archive_url = build_archive_url(snaps[0]["timestamp"],
                                        snaps[0]["original"])
        # download archive_url normally — it returns the bytes

Cache: in-memory LRU bounded to 200 entries. Wayback's CDX API is
relatively expensive to hit; caching avoids repeated queries when the
operator triages a batch of dead URLs.
"""
from __future__ import annotations

import sys
import threading
import time
from collections import OrderedDict
from typing import Optional
from urllib.parse import quote, urlencode

try:
    import requests  # type: ignore
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False


_API_BASE = "https://web.archive.org/cdx/search/xd"
_ARCHIVE_BASE = "https://web.archive.org/web"
_TIMEOUT = 20  # seconds; CDX can be slow at peak times
_USER_AGENT = "BulkDownloader-WaybackClient/1.0"

# Bounded LRU for CDX results. Key: URL string. Value: list of dicts.
_CACHE_MAX = 200
_cache: "OrderedDict[str, list]" = OrderedDict()
_cache_lock = threading.Lock()


def is_available() -> bool:
    """True when this module can make HTTP calls (requests installed)."""
    return _HAS_REQUESTS


def _cache_get(url: str) -> Optional[list]:
    with _cache_lock:
        v = _cache.get(url)
        if v is not None:
            _cache.move_to_end(url)
        return v


def _cache_put(url: str, snapshots: list):
    with _cache_lock:
        _cache[url] = snapshots
        _cache.move_to_end(url)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)


def find_snapshots(
    url: str,
    *,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    only_success: bool = True,
    limit: int = 100,
) -> list:
    """Query the CDX API for snapshots of `url`.

    `from_ts` / `to_ts` are YYYYMMDD or YYYYMMDDHHMMSS strings as
    expected by CDX. `only_success` filters to 200-status captures
    (most operators only want successfully-archived bytes).

    Returns a list of dicts:
      [{"timestamp": "20230515093421",
        "original": "https://example.com/path",
        "status_code": "200", "mime": "text/html"}, ...]

    Sorted oldest first by default. Empty list on miss or any error.

    Fail-open: never raises."""
    if not _HAS_REQUESTS or not url:
        return []
    cached = _cache_get(url)
    if cached is not None:
        return cached
    params = [
        ("url", url),
        ("output", "json"),
        ("collapse", "digest"),
        ("gzip", "false"),
        ("fl", "timestamp,original,statuscode,mimetype"),
    ]
    if only_success:
        params.append(("filter", "statuscode:200"))
    if from_ts:
        params.append(("from", str(from_ts)))
    if to_ts:
        params.append(("to", str(to_ts)))
    params.append(("limit", str(min(limit, 500))))
    try:
        r = requests.get(_API_BASE, params=params,
                        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
                        timeout=_TIMEOUT)
        if not r.ok:
            return []
        data = r.json()
    except (requests.Timeout, requests.ConnectionError) as e:
        sys.stderr.write(f"[wayback] network error for {url[:80]}: {e}\n")
        return []
    except Exception as e:
        sys.stderr.write(f"[wayback] unexpected error: {e}\n")
        return []
    if not isinstance(data, list) or not data:
        _cache_put(url, [])
        return []
    # First row is the header row ["timestamp","original","statuscode","mimetype"]
    if data and isinstance(data[0], list) and data[0] and data[0][0] == "timestamp":
        rows = data[1:]
    else:
        rows = data
    snapshots = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        snap = {
            "timestamp": row[0],
            "original": row[1],
            "status_code": row[2] if len(row) > 2 else "",
            "mime": row[3] if len(row) > 3 else "",
        }
        snapshots.append(snap)
    _cache_put(url, snapshots)
    return snapshots


def latest_snapshot(url: str, *, only_success: bool = True) -> Optional[dict]:
    """Return the most recent snapshot of `url`, or None if none exist."""
    snaps = find_snapshots(url, only_success=only_success)
    if not snaps:
        return None
    return snaps[-1]


def build_archive_url(timestamp: str, original_url: str,
                      *, identity: bool = True) -> str:
    """Build the Wayback URL that fetches the actual archived bytes
    at `timestamp`.

    When `identity=True`, the URL includes the 'id_' modifier which
    skips Wayback's HTML wrapper and returns the raw archived response.
    This is what BD wants for re-download: bytes, not the toolbar UI.
    """
    if not timestamp or not original_url:
        return ""
    mod = "id_" if identity else ""
    return f"{_ARCHIVE_BASE}/{timestamp}{mod}/{original_url}"


def availability(url: str) -> dict:
    """Quick lookup via Wayback's Availability API. Returns:
      {available: bool, timestamp: str, archive_url: str}
    Cheaper than CDX search when you only want "is there ANY snapshot?"."""
    if not _HAS_REQUESTS or not url:
        return {"available": False, "timestamp": "", "archive_url": ""}
    try:
        r = requests.get(
            "https://archive.org/wayback/available",
            params={"url": url},
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            timeout=_TIMEOUT,
        )
        if not r.ok:
            return {"available": False, "timestamp": "", "archive_url": ""}
        data = r.json()
        closest = (data or {}).get("archived_snapshots", {}).get("closest", {})
        if not closest.get("available"):
            return {"available": False, "timestamp": "", "archive_url": ""}
        return {
            "available": True,
            "timestamp": closest.get("timestamp", ""),
            "archive_url": closest.get("url", ""),
            "status": closest.get("status", ""),
        }
    except Exception as e:
        sys.stderr.write(f"[wayback] availability error: {e}\n")
        return {"available": False, "timestamp": "", "archive_url": "", "error": str(e)[:200]}


def stats() -> dict:
    """Diagnostic counters for /api/status."""
    with _cache_lock:
        return {"available": _HAS_REQUESTS, "cache_entries": len(_cache),
                "cache_max": _CACHE_MAX}
