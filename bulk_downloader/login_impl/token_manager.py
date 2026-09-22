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

Row 1039 (RULING-2138): the lifecycle must also end. An expired entry is
evicted when read, and every write prunes the expired entries nobody reads
again. A key's generation is kept only while a fetch for it is in flight --
with no fetch to outrun, an invalidation has nothing to guard -- so neither
map grows with the number of (egress_ip, domain) pairs ever seen.
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
        self._inflight: dict[tuple[str, str], int] = {}                   # key -> fetches running for it

    def _prune_locked(self, now: float) -> None:
        for store in (self._entries, self._clearance):
            for key in [k for k, (_, expires_at) in store.items() if expires_at <= now]:
                del store[key]

    def _fetch_and_publish(self, key: tuple[str, str], store: dict, fetch: Callable[[], tuple[Any, float]]) -> Any:
        # Called with the lock NOT held; the fetch itself runs unlocked.
        with self._lock:
            generation = self._generation.get(key, 0)
            self._inflight[key] = self._inflight.get(key, 0) + 1
        try:
            value, ttl = fetch()
            with self._lock:
                if self._generation.get(key, 0) == generation:
                    now = self._clock()
                    self._prune_locked(now)
                    store[key] = (value, now + float(ttl))
            return value
        finally:
            with self._lock:
                remaining = self._inflight[key] - 1
                if remaining:
                    self._inflight[key] = remaining
                else:
                    del self._inflight[key]
                    self._generation.pop(key, None)

    def _bump_locked(self, key: tuple[str, str]) -> None:
        if key in self._inflight:
            self._generation[key] = self._generation.get(key, 0) + 1

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
            if entry is not None:
                if entry[1] > now:
                    return entry[0]
                del self._entries[key]
            if fetch is None:
                return None
        return self._fetch_and_publish(key, self._entries, fetch)

    def set(self, egress_ip: str, domain: str, token: Any, ttl_seconds: float = 3600.0) -> None:
        key = (str(egress_ip), str(domain))
        with self._lock:
            now = self._clock()
            self._prune_locked(now)
            self._entries[key] = (token, now + float(ttl_seconds))

    def get_clearance(self, egress_ip: str, domain: str, fetch: Optional[Callable[[], tuple[Any, float]]] = None) -> Any:
        """Return valid clearance data for (egress_ip, domain), or None (non-blocking fallback)."""
        key = (str(egress_ip), str(domain))
        with self._lock:
            now = self._clock()
            entry = self._clearance.get(key)
            if entry is not None:
                if entry[1] > now:
                    return entry[0]
                del self._clearance[key]
            if fetch is None:
                return None
        return self._fetch_and_publish(key, self._clearance, fetch)

    def set_clearance(self, egress_ip: str, domain: str, clearance: Any, ttl_seconds: float = 7200.0) -> None:
        key = (str(egress_ip), str(domain))
        with self._lock:
            now = self._clock()
            self._prune_locked(now)
            self._clearance[key] = (clearance, now + float(ttl_seconds))

    def invalidate(self, egress_ip: str, domain: str) -> None:
        key = (str(egress_ip), str(domain))
        with self._lock:
            self._entries.pop(key, None)
            self._clearance.pop(key, None)
            self._bump_locked(key)

    def invalidate_clearance(self, egress_ip: str, domain: str) -> None:
        key = (str(egress_ip), str(domain))
        with self._lock:
            self._clearance.pop(key, None)
            self._bump_locked(key)

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
