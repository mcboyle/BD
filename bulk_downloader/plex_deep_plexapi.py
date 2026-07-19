"""Plex deepening via `plexapi` library (Phase 181, alternate backend).

This module is an OPT-IN alternative to plex_deep.py. plex_deep.py uses
raw HTTP + XML parsing (no external deps), works well, and is the
default. This module uses the `plexapi` library which handles XML
parsing, retries, connection pooling, and edge cases that we'd
otherwise have to hand-roll.

When to choose plexapi over the raw HTTP path:
  • Large libraries (10k+ items) where plexapi's better connection
    pooling reduces refresh latency
  • Sites that hit edge cases in our XML parser (rare Plex Pass
    features that emit non-standard XML)
  • Operators who already use plexapi elsewhere and want one stack

When to keep the raw HTTP path:
  • Don't want the extra dependency (~700 KB install)
  • Have working plex_deep.py in production — switching to plexapi
    means re-testing every workflow
  • Want minimal-dep BD distribution

Fail-open: if `plexapi` isn't installed, every call returns the
"not available" marker and the caller is expected to fall through
to plex_deep.py. The runner consults the `use_plexapi` config flag
to decide which backend to use; if plexapi is requested but not
installed, the raw HTTP path takes over silently.

Public surface mirrors plex_deep.py:

  • is_available()                     → bool: plexapi importable
  • connect(cfg)                        → server obj or None
  • path_scoped_refresh(server, ...)   → bool
  • find_unmatched(server, ...)        → list[dict]
  • restamp_added_at(server, ...)      → bool
  • ensure_collection(server, ...)     → collection_id or None
  • add_to_collection(server, ...)     → bool

Each function handles its own exceptions and returns False / None /
empty list on any error — never raises. Same contract as plex_deep.
"""
from __future__ import annotations

import sys
from typing import Optional


_plexapi = None
_import_attempted = False
_import_error = None


def _try_import():
    """Lazy import plexapi. Returns the module or None on failure.
    Caches the result so we don't pay the import cost per-call."""
    global _plexapi, _import_attempted, _import_error
    if _import_attempted:
        return _plexapi
    _import_attempted = True
    try:
        from plexapi.server import PlexServer
        _plexapi = PlexServer
        return _plexapi
    except ImportError as e:
        _import_error = str(e)
        _plexapi = None
        return None
    except Exception as e:
        _import_error = f"{type(e).__name__}: {e}"
        _plexapi = None
        return None


def is_available() -> bool:
    """True if plexapi is installed and importable."""
    return _try_import() is not None


def import_error() -> Optional[str]:
    """Last import error, if any. None when import succeeded or
    hasn't been attempted yet."""
    return _import_error


def connect(cfg: dict, *, timeout: float = 10.0):
    """Return a connected PlexServer instance, or None on failure.
    Caller is responsible for caching — connect() is moderately
    expensive (handshake + capabilities probe)."""
    PlexServer = _try_import()
    if PlexServer is None:
        return None
    url = (cfg.get("plex_url") or "").strip()
    token = (cfg.get("plex_token") or "").strip()
    if not url or not token:
        return None
    try:
        # plexapi.PlexServer accepts timeout via Session config.
        # For simplicity we pass it through; if a future plexapi
        # version drops the kwarg, fall back to default timeout.
        try:
            return PlexServer(url, token, timeout=timeout)
        except TypeError:
            return PlexServer(url, token)
    except Exception as e:
        sys.stderr.write(f"[plex_deep_plexapi] connect failed: {e}\n")
        return None


def path_scoped_refresh(server, section_id: int,
                       absolute_path: str, *,
                       force: bool = False) -> bool:
    """Refresh a Plex library section limited to one path. Returns
    True on success, False on any error (caller falls back to a
    full-library refresh in that case)."""
    if server is None or not absolute_path:
        return False
    try:
        section = server.library.sectionByID(section_id)
        # plexapi's update() supports a `path` kwarg for path-scoped
        section.update(path=absolute_path, force=force)
        return True
    except Exception as e:
        sys.stderr.write(
            f"[plex_deep_plexapi] path_scoped_refresh failed: {e}\n")
        return False


def find_unmatched(server, section_id: int,
                  *, limit: int = 50) -> list:
    """Return a list of {ratingKey, title, file} dicts for items in
    the given section that Plex marked as unmatched. Empty on
    error."""
    if server is None:
        return []
    try:
        section = server.library.sectionByID(section_id)
        items = section.search(unmatched=True)
        out = []
        for it in items[:limit]:
            try:
                first_media = (it.media or [None])[0]
                first_part = first_media.parts[0] if first_media else None
                out.append({
                    "ratingKey": str(it.ratingKey),
                    "title": getattr(it, "title", ""),
                    "file": getattr(first_part, "file", "") if first_part else "",
                })
            except Exception:
                continue
        return out
    except Exception as e:
        sys.stderr.write(
            f"[plex_deep_plexapi] find_unmatched failed: {e}\n")
        return []


def restamp_added_at(server, rating_key: str,
                    *, new_ts: Optional[int] = None) -> bool:
    """Re-stamp a Plex item's `addedAt` so the recently-added widget
    surfaces it. If new_ts is None, uses time.time()."""
    if server is None or not rating_key:
        return False
    try:
        import time as _t
        item = server.fetchItem(int(rating_key))
        ts = int(new_ts) if new_ts else int(_t.time())
        # plexapi exposes `addedAt` as a datetime, but the underlying
        # PUT call accepts the unix timestamp via the legacy field name.
        item.edit(**{"addedAt.value": ts})
        return True
    except Exception as e:
        sys.stderr.write(
            f"[plex_deep_plexapi] restamp_added_at failed: {e}\n")
        return False


def ensure_collection(server, section_id: int,
                     collection_name: str) -> Optional[str]:
    """Get-or-create a collection in the given section. Returns the
    collection's ratingKey as a string, or None on error."""
    if server is None or not collection_name:
        return None
    try:
        section = server.library.sectionByID(section_id)
        for col in section.collections():
            if col.title == collection_name:
                return str(col.ratingKey)
        # plexapi creates collections by adding an item; we can't
        # create an empty collection. Caller should use
        # add_to_collection instead — it auto-creates on first add.
        return None
    except Exception as e:
        sys.stderr.write(
            f"[plex_deep_plexapi] ensure_collection failed: {e}\n")
        return None


def add_to_collection(server, rating_key: str,
                     collection_name: str) -> bool:
    """Add an item to a named collection. Creates the collection if
    it doesn't exist. Returns True on success."""
    if server is None or not rating_key or not collection_name:
        return False
    try:
        item = server.fetchItem(int(rating_key))
        item.addCollection([collection_name])
        return True
    except Exception as e:
        sys.stderr.write(
            f"[plex_deep_plexapi] add_to_collection failed: {e}\n")
        return False


def status_dict() -> dict:
    """Snapshot for /api/plex_deep/status — reports availability
    + import error context."""
    return {
        "backend": "plexapi",
        "available": is_available(),
        "import_error": import_error(),
        "module": "bulk_downloader.plex_deep_plexapi",
    }
