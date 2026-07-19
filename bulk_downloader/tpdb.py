"""ThePornDatabase (TPDB) metadata lookup client.

Phase 88 / 5-star repo integration (ThePornDatabase/namer).

TPDB (api.theporndb.net) is the canonical metadata source for adult
content. Given a URL, title, or filename, it returns structured data:

  • Title (canonical)
  • Studio + parent network
  • Performers (with TPDB identity IDs for cross-site dedup)
  • Tags / categories
  • Release date
  • Cover image URL
  • Description

BD uses this opt-in as an enrichment step in the post-download pipeline:

  1. Download finishes → BD has a filename and source URL
  2. If tpdb_api_key is configured, BD looks up the URL or filename
  3. Lookup result is merged into the MetadataContext that mp4_metadata.py
     uses to write MP4 atoms (title, artist, comment, cover)
  4. NFO sidecar (future Phase 91) gets the full structured data

Auth: TPDB uses a Bearer token (per-user API key, freely issued at
theporndb.net). Stored in BD config as `tpdb_api_key`. We never log it.

This module is fail-open: every TPDB call wraps in try/except and
returns None on any failure. A broken TPDB call must NEVER block the
download pipeline — the file is already on disk; metadata is gravy.

Rate limiting: TPDB has a documented 60 requests/minute limit. We
self-impose a 1-call-per-second floor via a simple module-level
timestamp lock. Bursts of post-download enrichment will queue up at
that rate without overrunning.
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
from typing import Optional
from urllib.parse import quote

# Optional import — TPDB needs requests but we don't want a hard dep.
# Fall back gracefully if not present (the rest of BD already has
# requests installed in practice, so this is belt-and-suspenders).
try:
    import requests  # type: ignore
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

_API_BASE = "https://api.theporndb.net"
_TIMEOUT = 15  # seconds; TPDB usually responds in <2s

# Rate-limit: at least 1 second between calls. TPDB allows 60/min;
# this gives a safety margin and lets us be a good citizen without
# adding a token bucket.
_MIN_INTERVAL = 1.0
_rate_lock = threading.Lock()
_last_call_ts = 0.0

# Tiny in-memory cache so re-lookups of the same URL within a session
# don't re-hit TPDB. Bounded to prevent memory growth on long-running
# instances.
_CACHE_MAX = 500
_cache: dict = {}
_cache_order: list = []


def is_available() -> bool:
    """True if this module can make TPDB calls (requests installed)."""
    return _HAS_REQUESTS


def is_configured(api_key: Optional[str]) -> bool:
    """True if both module is available AND an API key was provided."""
    return _HAS_REQUESTS and bool(api_key and api_key.strip())


def _rate_limit_wait():
    """Block until at least _MIN_INTERVAL has passed since the last call."""
    global _last_call_ts
    with _rate_lock:
        elapsed = time.time() - _last_call_ts
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        _last_call_ts = time.time()


def _cache_get(key: str):
    return _cache.get(key)


def _cache_put(key: str, value):
    if key in _cache:
        return
    _cache[key] = value
    _cache_order.append(key)
    while len(_cache_order) > _CACHE_MAX:
        oldest = _cache_order.pop(0)
        _cache.pop(oldest, None)


def _headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key.strip()}",
        "Accept": "application/json",
        "User-Agent": "BulkDownloader-TPDB-Client/1.0",
    }


def _request(path: str, *, api_key: str, params: Optional[dict] = None) -> Optional[dict]:
    """Internal: make an authenticated GET, return parsed JSON or None.
    Never raises — TPDB failures must not break the pipeline."""
    if not is_configured(api_key):
        return None
    _rate_limit_wait()
    try:
        r = requests.get(_API_BASE + path,
                        headers=_headers(api_key),
                        params=params or {},
                        timeout=_TIMEOUT)
        if r.status_code == 401:
            sys.stderr.write("[tpdb] 401 unauthorized — check tpdb_api_key\n")
            return None
        if r.status_code == 404:
            return None  # not in TPDB; common, not an error
        if r.status_code == 429:
            sys.stderr.write("[tpdb] 429 rate limited; will back off\n")
            global _last_call_ts
            _last_call_ts = time.time() + 60  # 1-minute cool-down
            return None
        if not r.ok:
            sys.stderr.write(f"[tpdb] HTTP {r.status_code} for {path}\n")
            return None
        return r.json()
    except (requests.Timeout, requests.ConnectionError) as e:
        sys.stderr.write(f"[tpdb] network error: {e}\n")
        return None
    except Exception as e:
        sys.stderr.write(f"[tpdb] unexpected error: {e}\n")
        return None


def lookup_url(url: str, *, api_key: str) -> Optional[dict]:
    """Look up TPDB metadata for a source URL. Returns the raw scene
    dict or None. Cached per URL for the process lifetime."""
    if not url:
        return None
    cached = _cache_get("url:" + url)
    if cached is not None:
        return cached or None  # empty-dict sentinel for known-missing
    result = _request("/scenes/url", api_key=api_key, params={"url": url})
    scene = (result or {}).get("data") if isinstance(result, dict) else None
    _cache_put("url:" + url, scene or {})
    return scene


def lookup_filename(filename: str, *, api_key: str) -> Optional[dict]:
    """Look up TPDB metadata using a filename match (fuzzy). Returns
    the highest-confidence scene dict or None."""
    if not filename:
        return None
    # Strip extension + clean noise for better fuzzy match
    name = os.path.splitext(os.path.basename(filename))[0]
    name = re.sub(r"[._\-]+", " ", name).strip()
    if not name or len(name) < 4:
        return None
    cached = _cache_get("name:" + name)
    if cached is not None:
        return cached or None
    result = _request("/scenes", api_key=api_key, params={"parse": name, "limit": 1})
    data = (result or {}).get("data") if isinstance(result, dict) else None
    scene = data[0] if isinstance(data, list) and data else None
    _cache_put("name:" + name, scene or {})
    return scene


def to_metadata_dict(scene: Optional[dict]) -> dict:
    """Convert a TPDB scene dict into BD's MetadataContext-compatible
    shape. Empty dict on missing/invalid input — caller can merge."""
    if not scene or not isinstance(scene, dict):
        return {}
    out = {}
    if scene.get("title"):
        out["title"] = scene["title"]
    if scene.get("description"):
        out["description"] = scene["description"][:1000]
    if scene.get("date"):
        out["date"] = scene["date"]
    if scene.get("duration"):
        try:
            out["duration_seconds"] = int(scene["duration"])
        except (TypeError, ValueError):
            pass
    # Studio + parent
    site = scene.get("site") or {}
    if site.get("name"):
        out["studio"] = site["name"]
    parent = site.get("parent") or {}
    if parent.get("name"):
        out["network"] = parent["name"]
    # Performers — list of names; identity IDs preserved for future cross-site dedup
    performers = scene.get("performers") or []
    if isinstance(performers, list):
        names = [p.get("name") for p in performers if isinstance(p, dict) and p.get("name")]
        ids = [p.get("id") for p in performers if isinstance(p, dict) and p.get("id")]
        if names:
            out["performers"] = names
        if ids:
            out["performer_ids"] = ids
    # Tags
    tags = scene.get("tags") or []
    if isinstance(tags, list):
        tag_names = [t.get("name") for t in tags if isinstance(t, dict) and t.get("name")]
        if tag_names:
            out["tags"] = tag_names
    # Cover
    if scene.get("poster"):
        out["cover_url"] = scene["poster"]
    elif scene.get("background", {}).get("large"):
        out["cover_url"] = scene["background"]["large"]
    # TPDB scene ID — preserved for future re-lookup / sync
    if scene.get("id"):
        out["tpdb_id"] = scene["id"]
    return out


def enrich(url: Optional[str], filename: Optional[str], *,
          api_key: str) -> dict:
    """High-level: look up by URL first (most reliable), then fall back
    to filename. Returns BD-shaped metadata dict (possibly empty).
    Designed to be the one call sites make from the post-download
    pipeline."""
    if not is_configured(api_key):
        return {}
    scene = lookup_url(url, api_key=api_key) if url else None
    if not scene and filename:
        scene = lookup_filename(filename, api_key=api_key)
    return to_metadata_dict(scene)
