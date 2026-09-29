"""stale_dns_cache -- RFC 8767 serve-stale DNS cache with asynchronous refresh.

Row 1008.  BD's ``dns_resolver`` module caches DNS answers with a TTL.  When
an entry expires, the next lookup blocks on a fresh DNS query.  Under transient
resolver outages (CoreDNS restarts, upstream failures, network partitions),
every concurrent request stalls until the query times out and falls through to
``/etc/hosts``.

RFC 8767 (Serving Stale Data to Improve DNS Resiliency) specifies that a
resolver answers from stale data only when the refresh FAILS (or is still
unanswered after the client-response timer); a refresh that answers in time
returns the new data.  After a failed refresh, stale data is served without a
new attempt for the failure-recheck window (RFC 8767 section 5 suggests 30 s).

This module provides:

1. ``StaleDNSCache`` -- a thread-safe cache that extends the basic TTL model
   with a *stale window*.  When an entry's TTL has expired but it is still
   within the stale window, ``lookup`` starts an asynchronous refresh via a
   callback and waits up to ``client_response_seconds`` for it: a timely
   answer is returned fresh, otherwise the stale answer is returned while the
   refresh lands in the background.  Only one refresh is in-flight per key at
   a time, and none starts inside the failure-recheck window.

2. ``StaleEntry`` -- the cached record with TTL, stale deadline, and refresh
   state.

3. ``RefreshResult`` -- outcome of an async refresh (success, failure, timeout).

4. ``get_stale_dns_cache_info`` -- metadata introspection.

No new ``BD_`` environment keys.  The module is a leaf: it stores resolved
addresses and drives refresh via a caller-supplied callback.  It does not
perform DNS itself.
"""
from __future__ import annotations

import enum
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


class EntryState(enum.Enum):
    """State of a cache entry."""
    FRESH = "fresh"
    STALE = "stale"
    REFRESHING = "refreshing"
    EXPIRED = "expired"


class RefreshResult(enum.Enum):
    """Outcome of an asynchronous refresh attempt."""
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"  # Another refresh already in-flight


@dataclass
class StaleEntry:
    """A DNS cache entry with TTL and stale-serve metadata."""
    key: str
    value: str
    created_at: float = field(default_factory=time.monotonic)
    ttl_deadline: float = 0.0
    stale_deadline: float = 0.0
    refresh_in_flight: bool = False
    refresh_count: int = 0
    last_refresh_at: float = 0.0
    ttl: float = 0.0  # the answer's own TTL, carried through a refresh
    failure_recheck_until: float = 0.0
    refresh_done: Optional[threading.Event] = None

    def state(self, now: Optional[float] = None) -> EntryState:
        """Current state of this entry."""
        t = now if now is not None else time.monotonic()
        if t < self.ttl_deadline:
            return EntryState.FRESH
        if t >= self.stale_deadline:
            # an unanswered refresh never extends the stale window
            return EntryState.EXPIRED
        if self.refresh_in_flight:
            return EntryState.REFRESHING
        return EntryState.STALE


class StaleDNSCache:
    """Thread-safe DNS cache implementing RFC 8767 serve-stale semantics.

    Usage::

        def refresh_callback(key: str) -> Optional[str]:
            # Perform actual DNS lookup
            return resolved_address_or_none

        cache = StaleDNSCache(
            ttl_seconds=60.0,
            stale_window_seconds=300.0,
            refresh_callback=refresh_callback,
        )

        # Store a fresh answer
        cache.store("api.example.com", "93.184.216.34")

        # Lookup -- returns stale answer + triggers async refresh if expired
        result = cache.lookup("api.example.com")
    """

    def __init__(self, ttl_seconds: float = 60.0,
                 stale_window_seconds: float = 300.0,
                 max_entries: int = 1024,
                 refresh_callback: Optional[
                     Callable[[str], Optional[str]]] = None,
                 failure_recheck_seconds: float = 30.0,
                 client_response_seconds: float = 0.0) -> None:
        if ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds must be positive, got {ttl_seconds}")
        if stale_window_seconds < 0:
            raise ValueError(
                f"stale_window_seconds must be non-negative, got "
                f"{stale_window_seconds}")
        if max_entries <= 0:
            raise ValueError(
                f"max_entries must be positive, got {max_entries}")
        if failure_recheck_seconds < 0 or client_response_seconds < 0:
            raise ValueError(
                "failure_recheck_seconds and client_response_seconds must be "
                "non-negative")
        self._ttl = ttl_seconds
        self._stale_window = stale_window_seconds
        self._max = max_entries
        self._refresh_cb = refresh_callback
        self._failure_recheck = failure_recheck_seconds
        self._client_response = client_response_seconds
        self._lock = threading.Lock()
        self._store: dict[str, StaleEntry] = {}
        self._hits_fresh = 0
        self._hits_stale = 0
        self._misses = 0
        self._refreshes_started = 0
        self._refreshes_completed = 0
        self._refreshes_failed = 0

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    @property
    def stale_window_seconds(self) -> float:
        return self._stale_window

    def store(self, key: str, value: str,
              ttl_override: Optional[float] = None) -> None:
        """Store or update an entry with fresh TTL."""
        now = time.monotonic()
        ttl = ttl_override if ttl_override is not None else self._ttl
        with self._lock:
            entry = self._store.get(key)
            if entry is not None:
                entry.value = value
                entry.ttl_deadline = now + ttl
                entry.stale_deadline = now + ttl + self._stale_window
                entry.ttl = ttl
                entry.refresh_in_flight = False
                entry.failure_recheck_until = 0.0
                entry.refresh_done = None  # an in-flight refresh no longer applies
            else:
                entry = StaleEntry(
                    key=key, value=value, created_at=now, ttl=ttl,
                    ttl_deadline=now + ttl,
                    stale_deadline=now + ttl + self._stale_window)
                self._store[key] = entry
                self._evict_if_needed()

    def lookup(self, key: str) -> Optional[str]:
        """Look up a key.  Returns the value (fresh or stale) or None.

        A stale entry starts one asynchronous refresh (unless one is in
        flight or the last one failed inside the failure-recheck window) and
        waits up to ``client_response_seconds`` for it; the refreshed value
        is returned when it lands in time, the stale value otherwise.
        """
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None

            state = entry.state(now)

            if state == EntryState.FRESH:
                self._hits_fresh += 1
                return entry.value

            if state == EntryState.EXPIRED:
                del self._store[key]
                self._misses += 1
                return None

            # STALE or REFRESHING, inside the stale window
            stale_value = entry.value
            if (not entry.refresh_in_flight and self._refresh_cb is not None
                    and now >= entry.failure_recheck_until):
                entry.refresh_in_flight = True
                entry.refresh_done = threading.Event()
                self._refreshes_started += 1
                threading.Thread(
                    target=self._do_refresh,
                    args=(key, entry, entry.refresh_done),
                    daemon=True).start()
            done = entry.refresh_done if entry.refresh_in_flight else None
            if done is None or self._client_response <= 0:
                self._hits_stale += 1
                return stale_value

        # client-response timer: wait for the refresh outside the lock
        done.wait(self._client_response)
        with self._lock:
            current = self._store.get(key)
            if (current is not None
                    and current.state(time.monotonic()) == EntryState.FRESH):
                self._hits_fresh += 1
                return current.value
            self._hits_stale += 1
            return stale_value

    def _do_refresh(self, key: str, entry: StaleEntry,
                    done: threading.Event) -> None:
        """Background refresh for a stale entry."""
        try:
            new_value = self._refresh_cb(key) if self._refresh_cb else None
        except Exception:
            new_value = None
        with self._lock:
            now = time.monotonic()
            current = (self._store.get(key) is entry
                       and entry.refresh_done is done)
            if new_value is not None:
                self._refreshes_completed += 1
                if current:
                    entry.value = new_value
                    entry.ttl_deadline = now + entry.ttl
                    entry.stale_deadline = (now + entry.ttl
                                            + self._stale_window)
                    entry.refresh_count += 1
                    entry.last_refresh_at = now
            else:
                self._refreshes_failed += 1
                if current:
                    entry.failure_recheck_until = now + self._failure_recheck
            if current:
                entry.refresh_in_flight = False
        done.set()

    def invalidate(self, key: str) -> bool:
        """Remove an entry.  Returns True if found."""
        with self._lock:
            if key in self._store:
                del self._store[key]
                return True
            return False

    def clear(self) -> int:
        """Remove all entries.  Returns count removed."""
        with self._lock:
            count = len(self._store)
            self._store.clear()
            return count

    def _evict_if_needed(self) -> None:
        """Evict expired entries, then oldest stale, then oldest fresh,
        if over capacity. Caller holds self._lock."""
        if len(self._store) <= self._max:
            return
        now = time.monotonic()
        # First pass: remove truly expired
        expired_keys = [
            k for k, e in self._store.items()
            if e.state(now) == EntryState.EXPIRED
        ]
        for k in expired_keys:
            del self._store[k]
        if len(self._store) <= self._max:
            return
        # Second pass: remove oldest stale
        stale = sorted(
            ((k, e) for k, e in self._store.items()
             if e.state(now) in (EntryState.STALE, EntryState.REFRESHING)),
            key=lambda x: x[1].ttl_deadline)
        for k, _ in stale:
            del self._store[k]
            if len(self._store) <= self._max:
                return
        # Third pass: remove oldest fresh (LRU by created_at)
        fresh = sorted(
            ((k, e) for k, e in self._store.items()
             if e.state(now) == EntryState.FRESH),
            key=lambda x: x[1].created_at)
        for k, _ in fresh:
            del self._store[k]
            if len(self._store) <= self._max:
                return

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        with self._lock:
            now = time.monotonic()
            states: dict[str, int] = {}
            for e in self._store.values():
                s = e.state(now).value
                states[s] = states.get(s, 0) + 1
            return {
                "size": len(self._store),
                "max_entries": self._max,
                "ttl_seconds": self._ttl,
                "stale_window_seconds": self._stale_window,
                "hits_fresh": self._hits_fresh,
                "hits_stale": self._hits_stale,
                "misses": self._misses,
                "refreshes_started": self._refreshes_started,
                "refreshes_completed": self._refreshes_completed,
                "refreshes_failed": self._refreshes_failed,
                "entry_states": states,
            }


def get_stale_dns_cache_info() -> dict:
    """Return metadata about the stale DNS cache subsystem."""
    return {
        "version": 1,
        "rfc": "RFC 8767",
        "components": ["StaleDNSCache", "StaleEntry"],
        "entry_states": [s.value for s in EntryState],
        "refresh_results": [r.value for r in RefreshResult],
    }
