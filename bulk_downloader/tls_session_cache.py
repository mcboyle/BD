"""tls_session_cache -- TLS session ticket caching for BD's pinned HTTPS seam.

Row 1007.  Every TLS handshake in BD today performs a full negotiation.  For
sites visited repeatedly (the common case in batch downloading), this adds a
full round-trip per connection.  TLS 1.2 session tickets and TLS 1.3 PSK
resumption allow abbreviated handshakes that skip the certificate exchange,
cutting latency and server load.

This module provides:

1. ``TLSSessionCache`` -- an LRU cache keyed by ``(host, port)`` that stores
   TLS sessions.  It is safe for concurrent access from multiple download workers.

2. ``remember_session`` / ``resume_session_for`` -- the calls the pinned HTTPS
   seam (``urllib_ssrf``) makes around every handshake.

3. ``get_tls_session_cache_info`` -- metadata introspection.

A pre-warmed keepalive pool is deliberately NOT here (row 1007 re-emit, N6-A E3):
the only TLS seam BD owns is urllib, which sends ``Connection: close`` on every
request, so there is no idle connection a pool could hold. A pool with no
sockets and no caller was metadata only and has been removed.

No new ``BD_`` environment keys.  The module is a leaf: it makes no network
calls itself and holds no sockets.
"""
from __future__ import annotations

import collections
import enum
import itertools
import ssl
import threading
import time
import weakref
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


def get_tls_session_cache_info() -> dict:
    """Return metadata about the TLS session caching subsystem."""
    return {
        "version": 1,
        "components": ["TLSSessionCache"],
        "eviction_strategies": [e.value for e in SessionCacheEviction],
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


#: Per-context cache tokens. Weak keys, so a context's entry dies with it; tokens come from a
#: counter, so a token is never handed out twice -- unlike ``id()``, which CPython gives the next
#: context allocated at the same address (N6-A E2: a new context was offered a dead one's session).
_CONTEXT_TOKENS: weakref.WeakKeyDictionary[Any, int] = weakref.WeakKeyDictionary()
_CONTEXT_SEQ = itertools.count(1)
_CONTEXT_LOCK = threading.Lock()


def _context_key(context: Any) -> int:
    """Identity of the SSLContext a session belongs to.

    OpenSSL binds a session to the context that negotiated it: handing it to a different one
    raises ``ValueError: Session refers to a different SSLContext``. So the cache key is
    (host, port, context) -- caching by host alone is what made a second caller, with its own
    context, crash on a host the first caller had warmed. The token is unique for the life of
    the process, so a context allocated where a freed one lived does not inherit its sessions.
    """
    if context is None:
        return 0
    with _CONTEXT_LOCK:
        token = _CONTEXT_TOKENS.get(context)
        if token is None:
            token = next(_CONTEXT_SEQ)
            _CONTEXT_TOKENS[context] = token
        return token


def remember_session(host: str, port: int, session: Any, context: Any = None) -> bool:
    """Keep *session* for the next connection to ``(host, port)`` on *context*.

    A session that cannot resume is NOT stored: cached, every later lookup would count a hit
    while the handshake stays full (G1), and it would overwrite a resumable session another
    connection just stored (G2). That is a falsy session (a handshake that did not complete), and
    an ``ssl.SSLSession`` with neither a ticket nor a session id -- what a TLS 1.3 handshake holds
    until the server's post-handshake NewSessionTicket has been read (rc5 E1), and all a TLS 1.3
    server that issues no tickets ever hands out. A resumable TLS 1.2 session carries one or the
    other (a server without tickets resumes by session id). OpenSSL sets the id to the ticket's
    SHA-256 when a ticket arrives, so in practice the id alone decides; both are named for intent.
    """
    if not host or not session:
        return False
    if isinstance(session, ssl.SSLSession) and not (session.has_ticket or session.id):
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
