"""v3.43.75: yt-dlp download_archive interop.

# What this module is

yt-dlp tracks already-downloaded content in a `download_archive` text
file, one entry per line in the format:

    extractor_key video_id

Examples:

    youtube dQw4w9WgXcQ
    pornhub ph12345abcd
    xvideos vid.987654321

If a user runs yt-dlp regularly on the same media library AND uses BD,
they don't want BD to re-download things yt-dlp already grabbed (and
they probably want BD's completions to be visible to yt-dlp too).

This module reads + writes that archive file so the two tools cooperate
instead of duplicating work.

# Operations

  - `read_archive(path)` — load the archive into a set of "key id" entries
  - `is_in_archive(path, extractor, video_id)` — fast membership check
  - `append_entry(path, extractor, video_id)` — append a single line
  - `derive_id(url)` — best-effort URL → (extractor, video_id) mapping
  - `try_skip_for_url(path, url)` — convenience: read + derive + check

# URL-to-ID mapping

yt-dlp has specific URL regex per extractor. We mirror the major ones
for the sites BD handles. When we can't derive an ID confidently, we
return None so the caller treats the URL as unknown (not "already
downloaded").

Per-extractor patterns (subset most relevant to BD):

  - youtube:    /watch?v=ID, /shorts/ID, /v/ID, youtu.be/ID
  - pornhub:    /view_video.php?viewkey=ID, ?viewkey=ID
  - xvideos:    /video.ID/, /prof-video-click/, etc.
  - xhamster:   /videos/ID
  - spankbang:  /ID/video/
  - eporner:    /video-ID/
  - beeg:       /video/ID
  - redtube:    /ID
  - generic:    fall through to None

# Fail-open contract

  - Missing archive file → treat as empty (nothing to skip)
  - Permission denied / IO error on read → empty
  - Append failures logged at INFO, never raise
  - Unknown URL pattern → derive_id returns None; caller doesn't skip
"""
from __future__ import annotations

import logging
import os
import re
import threading
from typing import Optional

log = logging.getLogger(__name__)


# ─── URL-to-(extractor, id) mapping ────────────────────────────────


# Per-extractor regex patterns. Order matters — first match wins.
# Patterns extract the ID into named group "id". The extractor key is
# the dict key.
_EXTRACTOR_PATTERNS: dict = {
    "youtube": [
        re.compile(r"(?:youtube\.com|youtu\.be)/(?:watch\?v=|shorts/|v/|embed/)?(?P<id>[A-Za-z0-9_-]{11})"),
    ],
    "pornhub": [
        re.compile(r"pornhub\.com/view_video\.php\?(?:[^&]+&)*viewkey=(?P<id>[A-Za-z0-9]+)"),
        re.compile(r"pornhubpremium\.com/view_video\.php\?(?:[^&]+&)*viewkey=(?P<id>[A-Za-z0-9]+)"),
    ],
    "xvideos": [
        re.compile(r"xvideos\.com/video\.(?P<id>[A-Za-z0-9]+)/"),
        re.compile(r"xvideos\.com/video(?P<id>\d+)/"),
    ],
    "xhamster": [
        re.compile(r"xhamster\.com/videos/[^/]+-(?P<id>\d+)\b"),
    ],
    "xnxx": [
        re.compile(r"xnxx\.com/video-(?P<id>[A-Za-z0-9]+)/"),
    ],
    "spankbang": [
        re.compile(r"spankbang\.com/(?P<id>[A-Za-z0-9]+)/video/"),
    ],
    "eporner": [
        re.compile(r"eporner\.com/video-(?P<id>[A-Za-z0-9]+)/"),
    ],
    "youporn": [
        re.compile(r"youporn\.com/watch/(?P<id>\d+)"),
    ],
    "beeg": [
        re.compile(r"beeg\.com/(?:video/)?(?P<id>\d+)"),
    ],
    "redtube": [
        re.compile(r"redtube\.com/(?P<id>\d+)"),
    ],
    "tnaflix": [
        re.compile(r"tnaflix\.com/[^/]+/[^/]+-(?P<id>\d+)/"),
    ],
    "hqporner": [
        re.compile(r"hqporner\.com/hdporn/(?P<id>\d+)-"),
    ],
    "tubitv": [
        re.compile(r"tubitv\.com/(?:movies|tv-shows)/(?P<id>\d+)"),
    ],
    "wowgirls": [
        re.compile(r"wowgirls\.com/videos/(?P<id>[A-Za-z0-9_-]+)/"),
    ],
}


def derive_id(url: str) -> Optional[tuple]:
    """Best-effort URL → (extractor, video_id).

    Returns None when we can't confidently identify the video. The
    caller treats None as "unknown URL; don't skip".
    """
    if not url or not isinstance(url, str):
        return None
    for extractor, patterns in _EXTRACTOR_PATTERNS.items():
        for pat in patterns:
            m = pat.search(url)
            if m:
                vid = m.group("id")
                if vid:
                    return (extractor, vid)
    return None


# ─── Archive read/write ────────────────────────────────────────────


# Cache of parsed archives keyed by absolute path, with mtime-based
# invalidation. Saves re-parsing the same archive for every URL add.
_archive_cache: dict = {}   # path -> {"mtime": float, "entries": set[str]}
_cache_lock = threading.Lock()


def _archive_path_canonical(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def read_archive(path: str) -> set:
    """Load the archive file into a set of "extractor video_id" lines.

    Cached: if the file hasn't been modified since last read, returns
    the cached set. Cache is per-process (BD's app instance).

    Returns empty set on any failure (file missing, permission denied,
    decode error).
    """
    if not path:
        return set()
    canon = _archive_path_canonical(path)
    try:
        mtime = os.path.getmtime(canon)
    except OSError:
        return set()
    with _cache_lock:
        cached = _archive_cache.get(canon)
        if cached is not None and cached.get("mtime") == mtime:
            return cached["entries"]
    # (Re)load
    try:
        entries: set = set()
        with open(canon, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    entries.add(line)
    except OSError as e:
        log.info("yt_dlp_archive: read failed for %s: %s", canon, e)
        return set()
    except Exception as e:
        log.info("yt_dlp_archive: parse failed for %s: %s", canon, e)
        return set()
    with _cache_lock:
        _archive_cache[canon] = {"mtime": mtime, "entries": entries}
    return entries


def is_in_archive(
    path: str, extractor: str, video_id: str,
) -> bool:
    """True if this (extractor, id) is already in the archive."""
    if not path or not extractor or not video_id:
        return False
    entries = read_archive(path)
    return f"{extractor} {video_id}" in entries


def append_entry(
    path: str, extractor: str, video_id: str,
) -> bool:
    """Append `extractor video_id` to the archive file. Creates the
    file (and parent dirs) if missing.

    Returns True on success; never raises. Concurrent appends are
    safe — each line write is atomic on POSIX/Windows for short
    lines (under PIPE_BUF / 512 bytes).

    Skips appending if the entry is already present (no duplicate
    lines).
    """
    if not path or not extractor or not video_id:
        return False
    canon = _archive_path_canonical(path)
    line = f"{extractor} {video_id}"
    # Quick dedup
    if is_in_archive(path, extractor, video_id):
        return True  # already present; treat as no-op success
    try:
        parent = os.path.dirname(canon)
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)
        with open(canon, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        # Invalidate cache so subsequent reads see the new entry
        with _cache_lock:
            _archive_cache.pop(canon, None)
        return True
    except Exception as e:
        log.info("yt_dlp_archive: append failed for %s: %s", canon, e)
        return False


def try_skip_for_url(path: str, url: str) -> bool:
    """Convenience: read + derive + check, returns True if BD should
    skip downloading this URL (because yt-dlp already has it).

    Fail-open: any error returns False (don't skip).
    """
    if not path or not url:
        return False
    derived = derive_id(url)
    if derived is None:
        return False
    extractor, video_id = derived
    return is_in_archive(path, extractor, video_id)


def stats_for_archive(path: str) -> dict:
    """Return a stats summary for the UI. Counts entries by extractor."""
    if not path:
        return {"total": 0, "by_extractor": {}}
    entries = read_archive(path)
    by_ext: dict = {}
    for line in entries:
        head = line.split(" ", 1)[0]
        by_ext[head] = by_ext.get(head, 0) + 1
    return {
        "total": len(entries),
        "by_extractor": by_ext,
        "path_exists": os.path.exists(_archive_path_canonical(path)),
    }


__all__ = [
    "derive_id",
    "read_archive",
    "is_in_archive",
    "append_entry",
    "try_skip_for_url",
    "stats_for_archive",
]
