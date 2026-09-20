"""bulk_downloader.login_impl.token_manager -- authorization and clearance token lifecycle cache.

Row 894 (O883 / O961): verification and authorization token lifecycle caching.
Stores and tracks clearance cookies and authorization tokens mapped to client
egress IP addresses and domains, reusing valid clearance tokens on subsequent
requests with non-blocking fallback on cold cache and 0 site logins touched (Rule 21).

Fixer (row894 REFUTE E2/E3): the TTL is elapsed time, so the default clock is
``time.monotonic`` -- a wall-clock correction (NTP step, DST) must neither
extend nor shorten a token's validity. An invalidation that lands while a
fetch for the same key is in flight must survive that fetch: each key
carries a generation, ``invalidate`` bumps it, and a fetch only publishes
its result if the generation it started under is still current.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional


class AuthTokenCache:
    def __init__(self, clock: Optional[Callable[[], float]] = None):
        # Resolved at construction, not at definition, so the provider is
        # the process's current one (test seam; no module-level binding).
        self._clock = clock if clock is not None else time.monotonic
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], tuple[Any, float]] = {}      # key -> (token, expires_at)
        self._clearance: dict[tuple[str, str], tuple[Any, float]] = {}    # key -> (clearance, expires_at)
        self._generation: dict[tuple[str, str], int] = {}                 # key -> int, bumped by invalidate()

    def get(self, egress_ip: str, domain: str, fetch: Optional[Callable[[], tuple[Any, float]]] = None) -> Any:
        """Return a valid token for (egress_ip, domain), fetching if needed.

        ``fetch`` takes no arguments and returns ``(token, ttl_seconds)``.
        A fetch runs outside the lock; its result is published only if no
        invalidation for the key arrived while it ran.
        If ``fetch`` is None and no valid token exists, returns None.
        """
        key = (str(egress_ip), str(domain))
        with self._lock:
            now = self._clock()
            entry = self._entries.get(key)
            if entry is not None and entry[1] > now:
                return entry[0]
            if fetch is None:
                return None
            generation = self._generation.get(key, 0)
        token, ttl = fetch()
        with self._lock:
            if self._generation.get(key, 0) == generation:
                self._entries[key] = (token, self._clock() + ttl)
        return token

    def set(self, egress_ip: str, domain: str, token: Any, ttl_seconds: float = 3600.0) -> None:
        key = (str(egress_ip), str(domain))
        with self._lock:
            self._entries[key] = (token, self._clock() + float(ttl_seconds))

    def get_clearance(self, egress_ip: str, domain: str, fetch: Optional[Callable[[], tuple[Any, float]]] = None) -> Any:
        """Return valid clearance data for (egress_ip, domain), or None (non-blocking fallback)."""
        key = (str(egress_ip), str(domain))
        with self._lock:
            now = self._clock()
            entry = self._clearance.get(key)
            if entry is not None and entry[1] > now:
                return entry[0]
            if fetch is None:
                return None
            generation = self._generation.get(key, 0)
        clearance, ttl = fetch()
        with self._lock:
            if self._generation.get(key, 0) == generation:
                self._clearance[key] = (clearance, self._clock() + float(ttl))
        return clearance

    def set_clearance(self, egress_ip: str, domain: str, clearance: Any, ttl_seconds: float = 7200.0) -> None:
        key = (str(egress_ip), str(domain))
        with self._lock:
            self._clearance[key] = (clearance, self._clock() + float(ttl_seconds))

    def invalidate(self, egress_ip: str, domain: str) -> None:
        key = (str(egress_ip), str(domain))
        with self._lock:
            self._entries.pop(key, None)
            self._clearance.pop(key, None)
            self._generation[key] = self._generation.get(key, 0) + 1

    def invalidate_clearance(self, egress_ip: str, domain: str) -> None:
        key = (str(egress_ip), str(domain))
        with self._lock:
            self._clearance.pop(key, None)
            self._generation[key] = self._generation.get(key, 0) + 1

    def on_response(self, egress_ip: str, domain: str, status_code: int) -> None:
        """Drop cached token and clearance for (egress_ip, domain) on HTTP 401."""
        if status_code == 401:
            self.invalidate(egress_ip, domain)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._clearance.clear()
            self._generation.clear()


TurnstileCache = AuthTokenCache

_default_cache = AuthTokenCache()


def get_default_cache() -> AuthTokenCache:
    return _default_cache


def reset_cache_for_tests() -> None:
    global _default_cache
    _default_cache = AuthTokenCache()
