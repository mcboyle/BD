"""tls_session_cache -- TLS session ticket caching and pre-warmed keepalive pools.

Row 1007.  Every TLS handshake in BD today performs a full negotiation.  For
sites visited repeatedly (the common case in batch downloading), this adds a
full round-trip per connection.  TLS 1.2 session tickets and TLS 1.3 PSK
resumption allow abbreviated handshakes that skip the certificate exchange,
cutting latency and server load.

Additionally, connections are opened on demand and never pre-established.
When a site has predictable concurrency (configured ``max_workers``), the pool
can pre-warm keepalive connections during idle periods so the first real
request avoids both DNS and TLS.

This module provides:

1. ``TLSSessionCache`` -- an LRU cache keyed by ``(host, port)`` that stores
   serialized TLS session data.  It is safe for concurrent access from
   multiple download workers.

2. ``KeepalivePool`` -- a bounded pool of pre-warmed TCP+TLS connections that
   can be checked out by workers and returned.  Idle connections are probed
   for liveness before reuse.

3. ``get_tls_session_cache_info`` -- metadata introspection.

No new ``BD_`` environment keys.  The module is a leaf: it makes no network
calls itself and holds no sockets.  Connection pre-warming is driven by the
caller passing sockets in.
"""
from __future__ import annotations

import collections
import enum
import ssl
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional


class SessionCacheEviction(enum.Enum):
    """Reason a cached TLS session was evicted."""
    LRU = "lru"
    EXPIRED = "expired"
    EXPLICIT = "explicit"


@dataclass(frozen=True)
class CachedSession:
    """An opaque TLS session blob with metadata."""
    host: str
    port: int
    session_data: bytes
    cached_at: float = field(default_factory=time.monotonic)
    tls_version: str = ""
    resumed: bool = False


class TLSSessionCache:
    """Thread-safe LRU cache for TLS session tickets.

    Usage::

        cache = TLSSessionCache(max_entries=256, ttl_seconds=3600)
        cache.store("example.com", 443, session_bytes)
        ticket = cache.lookup("example.com", 443)
        if ticket is not None:
            # Apply ticket to SSLContext for abbreviated handshake
            ...
    """

    def __init__(self, max_entries: int = 256,
                 ttl_seconds: float = 3600.0) -> None:
        if max_entries <= 0:
            raise ValueError(f"max_entries must be positive, got {max_entries}")
        if ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds must be positive, got {ttl_seconds}")
        self._max = max_entries
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._store: collections.OrderedDict[
            tuple[str, int], CachedSession] = collections.OrderedDict()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    @property
    def max_entries(self) -> int:
        return self._max

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    def store(self, host: str, port: int, session_data: bytes,
              tls_version: str = "", resumed: bool = False) -> None:
        """Store a TLS session ticket for ``(host, port)``."""
        if not session_data:
            return
        key = (host, port)
        entry = CachedSession(
            host=host, port=port, session_data=session_data,
            tls_version=tls_version, resumed=resumed)
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = entry
            while len(self._store) > self._max:
                self._store.popitem(last=False)
                self._evictions += 1

    def lookup(self, host: str, port: int) -> Optional[CachedSession]:
        """Return a cached session or None if absent/expired."""
        key = (host, port)
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            if (now - entry.cached_at) > self._ttl:
                del self._store[key]
                self._evictions += 1
                self._misses += 1
                return None
            self._store.move_to_end(key)
            self._hits += 1
            return entry

    def invalidate(self, host: str, port: int) -> bool:
        """Explicitly remove a cached session.  Returns True if found."""
        key = (host, port)
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

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        with self._lock:
            return {
                "size": len(self._store),
                "max_entries": self._max,
                "ttl_seconds": self._ttl,
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "hit_rate": (self._hits / (self._hits + self._misses)
                             if (self._hits + self._misses) > 0 else 0.0),
            }


class PoolSlotState(enum.Enum):
    """State of a keepalive pool slot."""
    IDLE = "idle"
    CHECKED_OUT = "checked_out"
    CLOSED = "closed"


@dataclass
class PoolSlot:
    """A slot in the keepalive pool tracking a pre-warmed connection."""
    host: str
    port: int
    state: PoolSlotState = PoolSlotState.IDLE
    warmed_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)
    use_count: int = 0


class KeepalivePool:
    """Bounded pool of pre-warmed connection slots.

    This pool manages *metadata* about pre-warmed connections.  The actual
    sockets are owned by the caller; this pool tracks which (host, port)
    slots are available, checked out, or stale.

    Usage::

        pool = KeepalivePool(max_slots=16, idle_timeout=60.0)
        slot = pool.register("example.com", 443)
        checkout = pool.checkout("example.com", 443)
        if checkout is not None:
            # Use the pre-warmed connection
            pool.checkin(checkout)
    """

    def __init__(self, max_slots: int = 16,
                 idle_timeout: float = 60.0) -> None:
        if max_slots <= 0:
            raise ValueError(f"max_slots must be positive, got {max_slots}")
        if idle_timeout <= 0:
            raise ValueError(
                f"idle_timeout must be positive, got {idle_timeout}")
        self._max = max_slots
        self._idle_timeout = idle_timeout
        self._lock = threading.Lock()
        self._slots: dict[tuple[str, int], PoolSlot] = {}
        self._checkouts = 0
        self._returns = 0

    @property
    def max_slots(self) -> int:
        return self._max

    @property
    def idle_timeout(self) -> float:
        return self._idle_timeout

    def register(self, host: str, port: int) -> Optional[PoolSlot]:
        """Register a pre-warmed connection slot.  Returns the slot or None
        if pool is full."""
        key = (host, port)
        with self._lock:
            if key in self._slots:
                slot = self._slots[key]
                slot.warmed_at = time.monotonic()
                slot.state = PoolSlotState.IDLE
                return slot
            if len(self._slots) >= self._max:
                # Evict oldest idle
                evict_key = None
                oldest = float("inf")
                for k, s in self._slots.items():
                    if s.state == PoolSlotState.IDLE and s.last_used < oldest:
                        oldest = s.last_used
                        evict_key = k
                if evict_key is not None:
                    del self._slots[evict_key]
                else:
                    return None  # All checked out, cannot evict
            slot = PoolSlot(host=host, port=port)
            self._slots[key] = slot
            return slot

    def checkout(self, host: str, port: int) -> Optional[PoolSlot]:
        """Check out an idle slot for ``(host, port)``.  Returns None if
        unavailable or stale."""
        key = (host, port)
        now = time.monotonic()
        with self._lock:
            slot = self._slots.get(key)
            if slot is None or slot.state != PoolSlotState.IDLE:
                return None
            if (now - slot.last_used) > self._idle_timeout:
                slot.state = PoolSlotState.CLOSED
                return None
            slot.state = PoolSlotState.CHECKED_OUT
            slot.use_count += 1
            self._checkouts += 1
            return slot

    def checkin(self, slot: PoolSlot) -> None:
        """Return a checked-out slot to idle."""
        with self._lock:
            if slot.state == PoolSlotState.CHECKED_OUT:
                slot.state = PoolSlotState.IDLE
                slot.last_used = time.monotonic()
                self._returns += 1

    def remove(self, host: str, port: int) -> bool:
        """Remove a slot.  Returns True if found."""
        key = (host, port)
        with self._lock:
            if key in self._slots:
                self._slots[key].state = PoolSlotState.CLOSED
                del self._slots[key]
                return True
            return False

    def prune_stale(self) -> int:
        """Remove idle slots past their timeout.  Returns count pruned."""
        now = time.monotonic()
        pruned = 0
        with self._lock:
            stale_keys = [
                k for k, s in self._slots.items()
                if s.state == PoolSlotState.IDLE
                and (now - s.last_used) > self._idle_timeout
            ]
            for k in stale_keys:
                del self._slots[k]
                pruned += 1
        return pruned

    def stats(self) -> dict[str, Any]:
        """Return pool statistics."""
        with self._lock:
            idle = sum(1 for s in self._slots.values()
                       if s.state == PoolSlotState.IDLE)
            out = sum(1 for s in self._slots.values()
                      if s.state == PoolSlotState.CHECKED_OUT)
            return {
                "size": len(self._slots),
                "max_slots": self._max,
                "idle": idle,
                "checked_out": out,
                "idle_timeout": self._idle_timeout,
                "total_checkouts": self._checkouts,
                "total_returns": self._returns,
            }


def get_tls_session_cache_info() -> dict:
    """Return metadata about the TLS session caching subsystem."""
    return {
        "version": 1,
        "components": ["TLSSessionCache", "KeepalivePool"],
        "eviction_strategies": [e.value for e in SessionCacheEviction],
        "pool_slot_states": [s.value for s in PoolSlotState],
    }


# ---------------------------------------------------------------------------------------------
# The live seam (row 1007 re-emit, answering REFUTE F1 -- bd-review-correctness-B3 09:25Z).
#
# The first cut of this row shipped the cache with no caller: nothing in the product opened a
# TLS connection through it, so no handshake was ever abbreviated. The one place BD performs its
# own TLS handshake is ``urllib_ssrf._PinnedHTTPSConnection.connect`` (row 728), the pinned
# opener behind hooks.py, doh_resolver.py, app_template.py and dev_suite/capture_diag.py. These
# three functions are what that seam calls; everything above stays a plain data structure.
#
# What is cached here is an ``ssl.SSLSession`` OBJECT, not bytes. CPython does not expose a
# serialisable ticket -- ``SSLSocket.session`` hands back an opaque object that is only valid in
# the process that negotiated it -- so the cache is per-process and deliberately not persisted.
# ---------------------------------------------------------------------------------------------

_PROCESS_CACHE: Optional[TLSSessionCache] = None
_PROCESS_CACHE_LOCK = threading.Lock()

#: Resumption attempts the kernel/OpenSSL refused, by reason. Counted rather than swallowed: a
#: session cache that quietly stops resuming looks exactly like one that is working.
REUSE_FAILURES: dict[str, int] = collections.defaultdict(int)


def get_session_cache() -> TLSSessionCache:
    """The process-wide session cache used by the pinned HTTPS seam."""
    global _PROCESS_CACHE
    with _PROCESS_CACHE_LOCK:
        if _PROCESS_CACHE is None:
            _PROCESS_CACHE = TLSSessionCache()
        return _PROCESS_CACHE


def _context_key(context: Any) -> int:
    """Identity of the SSLContext a session belongs to.

    OpenSSL binds a session to the context that negotiated it: handing it to a different one
    raises ``ValueError: Session refers to a different SSLContext``. So the cache key is
    (host, port, context) -- caching by host alone is what made a second caller, with its own
    context, crash on a host the first caller had warmed.
    """
    return 0 if context is None else id(context)


def remember_session(host: str, port: int, session: Any, context: Any = None) -> bool:
    """Keep *session* for the next connection to ``(host, port)`` on *context*.

    A falsy session (no ticket offered, or a handshake that did not complete) is NOT stored:
    caching "nothing" would turn every later lookup into a hit that resumes nothing.
    """
    if not host or not session:
        return False
    try:
        get_session_cache().store(_scoped_host(host, context), int(port), session)
    except Exception as exc:                      # noqa: BLE001 -- never break a live request
        REUSE_FAILURES[f"store:{type(exc).__name__}"] += 1
        return False
    return True


def resume_session_for(host: str, port: int, context: Any = None) -> Any:
    """An ``ssl.SSLSession`` to hand to ``wrap_socket(session=...)``, or None for a full handshake.

    None is the ordinary answer on a first connection, and on any connection whose SSLContext is
    not the one that negotiated the cached session. Nothing here raises: a cache that could take
    down egress would be worse than the full handshake it is trying to avoid.
    """
    try:
        entry = get_session_cache().lookup(_scoped_host(host, context), int(port))
    except Exception as exc:                      # noqa: BLE001
        REUSE_FAILURES[f"lookup:{type(exc).__name__}"] += 1
        return None
    return entry.session_data if entry is not None else None


def _scoped_host(host: str, context: Any) -> str:
    """The cache key host component, scoped to the owning SSLContext."""
    return f"{host}#ctx{_context_key(context)}"
