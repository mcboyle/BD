"""Plex deepening via plexapi (Phase 181).

Additive companion to `plex_deep.py`. The existing module is a
hand-rolled HTTP client that does refresh / match-check / addedAt
stamping / collection routing — those keep working unchanged.

This module ADDS richer read operations using the official
`plexapi` library when it's available:

  • server_info(cfg) — server identity, version, transcoder state
  • library_stats(cfg) — per-section item counts + sizes
  • recently_added(cfg, *, count) — newest items across all sections
  • on_deck(cfg, *, count) — partially-watched items
  • search(cfg, query, *, count) — full-text search
  • mark_watched(cfg, rating_key) / mark_unwatched(cfg, rating_key)

Fail-open everywhere: if `plexapi` isn't installed, every function
returns `{"available": False, "install_with": "pip install plexapi"}`.
Callers don't need to feature-detect — they can just call and check
the response.

This is intentionally read-heavy. Write paths (refresh, match) stay
in plex_deep.py so a regression here can't break the existing
download → refresh pipeline.
"""
from __future__ import annotations

from typing import Optional

from . import error_reply as _er


# Lazy import — module import must succeed even when plexapi isn't installed
def _try_plexapi():
    """Return (PlexServer_class, import_error_str_or_None)."""
    try:
        from plexapi.server import PlexServer  # type: ignore
        return PlexServer, None
    except ImportError as e:
        return None, str(e)


def is_available() -> bool:
    cls, _ = _try_plexapi()
    return cls is not None


def import_error() -> Optional[str]:
    _, err = _try_plexapi()
    return err


class PlexConnectError(RuntimeError):
    """A failure _connect words itself (plexapi absent, config missing, the
    guard's refusal): its text names a setting or a host, never a credential,
    so it may reach the client. Any other exception is foreign and does not."""


def _error(where: str, cfg: dict, e: Exception, **extra) -> dict:
    """The error answer for one call: _connect's own message (scrubbed), or a
    generic message + request id with the detail in the server log (F3/F4)."""
    token = (cfg or {}).get("plex_token")
    if isinstance(e, PlexConnectError):
        return {"available": True, **extra, "error": _er.scrub(e, [token])[:200]}
    return _er.failure_reply(f"plex_advanced.{where}", e, secrets=[token],
                             available=True, **extra)


def _connect(cfg: dict):
    """Build a PlexServer from BD's site config. Returns the server
    or raises an Exception with a user-readable message.

    Connects through plex_deep_plexapi.connect_or_refusal, the one guarded path:
    a private/loopback plex_url is refused unless the site sets
    plex_allow_private_host, and the session never carries the token
    across a cross-origin redirect (row 713)."""
    PlexServer, err = _try_plexapi()
    if PlexServer is None:
        raise PlexConnectError(
            f"plexapi not installed: {err}. "
            f"pip install plexapi")
    base_url = (cfg or {}).get("plex_url") or ""
    token = ((cfg or {}).get("plex_token") or "").strip()
    if not base_url or not token:
        raise PlexConnectError(
            "site config missing plex_url and/or plex_token")
    from . import plex_deep_plexapi as _pdp
    # The refusal comes back with this call, never from the module's shared
    # last-refusal state, which another site's concurrent call can overwrite.
    srv, refusal = _pdp.connect_or_refusal(cfg, timeout=10)
    if srv is None:
        raise PlexConnectError(refusal
                               or "could not connect to Plex (details in the server log)")
    return srv


def _fail_open(extra=None) -> dict:
    """Standard 'available: False' return shape so callers can branch
    on `r.get('available') is False` without further care."""
    out = {"available": False,
           "install_with": "pip install plexapi"}
    err = import_error()
    if err:
        out["import_error"] = err
    if extra:
        out.update(extra)
    return out


def server_info(cfg: dict) -> dict:
    """Server identity: name, version, platform, friendly URL."""
    if not is_available():
        return _fail_open()
    try:
        srv = _connect(cfg)
        return {
            "available": True,
            "friendly_name": srv.friendlyName,
            "version": srv.version,
            "platform": srv.platform,
            "machine_identifier": srv.machineIdentifier,
            "myplex_username": srv.myPlexUsername,
            "transcoder_active_sessions": len(srv.sessions() or []),
        }
    except Exception as e:
        return _error("server_info", cfg, e)


def library_stats(cfg: dict) -> dict:
    """Per-section item count + size estimate. Read-only.
    Each section adds one HTTP round-trip, so cost scales with N
    sections (typically 3-8, not a concern)."""
    if not is_available():
        return _fail_open()
    try:
        srv = _connect(cfg)
        sections = []
        for s in srv.library.sections():
            total = 0
            try:
                total = s.totalSize  # plexapi getter
            except Exception:
                # Some section types don't expose totalSize cheaply
                total = len(s.all() or [])
            sections.append({
                "key": s.key,
                "title": s.title,
                "type": s.type,
                "agent": getattr(s, "agent", ""),
                "language": getattr(s, "language", ""),
                "total_items": total,
                "uuid": getattr(s, "uuid", ""),
            })
        return {"available": True, "sections": sections}
    except Exception as e:
        return _error("library_stats", cfg, e)


def recently_added(cfg: dict, *, count: int = 20) -> dict:
    """Newest items across all sections (server-wide hub query).
    Returns just the metadata BD cares about, not full plexapi
    objects."""
    if not is_available():
        return _fail_open()
    try:
        srv = _connect(cfg)
        items = []
        # Each section has its own `recentlyAdded()`; pull from all
        for s in srv.library.sections():
            try:
                for it in (s.recentlyAdded(maxresults=count) or []):
                    items.append(_item_summary(it))
            except Exception:
                continue
        # Sort overall by addedAt desc
        items.sort(key=lambda x: x.get("added_at", 0), reverse=True)
        return {"available": True,
                "items": items[:count],
                "scanned_sections": len(srv.library.sections())}
    except Exception as e:
        return _error("recently_added", cfg, e)


def on_deck(cfg: dict, *, count: int = 20) -> dict:
    """Items partially watched (Plex's "Continue Watching" row)."""
    if not is_available():
        return _fail_open()
    try:
        srv = _connect(cfg)
        # onDeck() is on the library, not the server
        items = []
        for it in (srv.library.onDeck() or [])[:count]:
            items.append(_item_summary(it))
        return {"available": True, "items": items}
    except Exception as e:
        return _error("on_deck", cfg, e)


def search(cfg: dict, query: str, *, count: int = 25) -> dict:
    """Full-text search across all sections."""
    if not is_available():
        return _fail_open()
    if not query:
        return {"available": True, "items": []}
    try:
        srv = _connect(cfg)
        results = srv.search(query, limit=count)
        items = [_item_summary(it) for it in (results or [])]
        return {"available": True, "items": items, "query": query}
    except Exception as e:
        return _error("search", cfg, e)


def mark_watched(cfg: dict, rating_key) -> dict:
    """Mark item as watched. Caller passes the rating_key returned
    from any of the read calls above."""
    if not is_available():
        return _fail_open()
    try:
        srv = _connect(cfg)
        item = srv.fetchItem(int(rating_key))
        item.markWatched()
        return {"available": True, "ok": True}
    except Exception as e:
        return _error("mark_watched", cfg, e, ok=False)


def mark_unwatched(cfg: dict, rating_key) -> dict:
    if not is_available():
        return _fail_open()
    try:
        srv = _connect(cfg)
        item = srv.fetchItem(int(rating_key))
        item.markUnwatched()
        return {"available": True, "ok": True}
    except Exception as e:
        return _error("mark_unwatched", cfg, e, ok=False)


def _item_summary(item) -> dict:
    """Reduce a plexapi item to a JSON-friendly dict. Defensive on
    every attribute — older plexapi versions name some fields
    differently."""
    out = {
        "rating_key": getattr(item, "ratingKey", None),
        "title": getattr(item, "title", None),
        "type": getattr(item, "type", None),
        "summary": (getattr(item, "summary", "") or "")[:300],
        "year": getattr(item, "year", None),
        "duration_ms": getattr(item, "duration", None),
        "section_id": (getattr(item, "librarySectionID", None)
                       or getattr(item, "sectionID", None)),
    }
    # addedAt is a datetime in plexapi — convert to unix ts
    try:
        added = getattr(item, "addedAt", None)
        if added is not None:
            out["added_at"] = added.timestamp()
    except Exception:
        out["added_at"] = None
    try:
        viewed = getattr(item, "lastViewedAt", None)
        if viewed is not None:
            out["last_viewed_at"] = viewed.timestamp()
    except Exception:
        pass
    # Files (only for Video/Episode items)
    try:
        out["file"] = (item.media[0].parts[0].file
                       if item.media and item.media[0].parts else None)
    except Exception:
        pass
    return out


def status_dict() -> dict:
    """Health summary for the diagnostics bundle / settings UI."""
    return {
        "available": is_available(),
        "import_error": import_error(),
        "operations": [
            "server_info", "library_stats", "recently_added",
            "on_deck", "search", "mark_watched", "mark_unwatched",
        ],
    }
