"""Local-only CoreDNS resolution for fleet ``.mesh.local`` service names."""

from __future__ import annotations

import ipaddress
import secrets
import socket
import struct
import threading
import time
from typing import Final


FALLBACK_TTL_SECONDS = 5.0  # row842: negative/fallback cache window
_DEFAULT_NAMESERVER: Final = ("127.0.0.1", 53)
_DEFAULT_TTL_SECONDS: Final = 60.0
_STALE_WINDOW_SECONDS: Final = 300.0  # row1008: serve stale for 5 min past TTL
_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_LOCK = threading.Lock()

# row1008: stale DNS cache for serve-stale with async refresh
_STALE_CACHE: "StaleDNSCache | None" = None
_STALE_CACHE_LOCK = threading.Lock()


def _get_stale_cache() -> "StaleDNSCache":
    """Lazy-init the stale DNS cache singleton."""
    global _STALE_CACHE
    with _STALE_CACHE_LOCK:
        if _STALE_CACHE is None:
            from bulk_downloader.stale_dns_cache import StaleDNSCache
            _STALE_CACHE = StaleDNSCache(
                ttl_seconds=_DEFAULT_TTL_SECONDS,
                stale_window_seconds=_STALE_WINDOW_SECONDS,
                refresh_callback=_refresh_mesh_host,
                client_response_seconds=0.25,  # the CoreDNS query timeout
            )
        return _STALE_CACHE


def _refresh_mesh_host(key: str) -> "str | None":
    """Async refresh callback for stale cache: re-query CoreDNS."""
    try:
        return _query_coredns(key, nameserver=_DEFAULT_NAMESERVER, timeout=0.25)
    except (OSError, ValueError):
        return None


def clear_mesh_cache() -> None:
    """Clear cached mesh addresses; primarily useful for controlled tests."""
    global _STALE_CACHE
    with _CACHE_LOCK:
        _CACHE.clear()
    with _STALE_CACHE_LOCK:
        if _STALE_CACHE is not None:
            _STALE_CACHE.clear()


def resolve_mesh_host(
    hostname: str,
    *,
    nameserver: tuple[str, int] = _DEFAULT_NAMESERVER,
    timeout: float = 0.25,
    ttl_seconds: float = _DEFAULT_TTL_SECONDS,
) -> str:
    """Resolve a mesh service through local CoreDNS, then ``/etc/hosts``.

    Only ``.mesh.local`` names are sent to CoreDNS.  Every CoreDNS datagram is
    restricted to a loopback address; a failed local query never prompts a
    network fallback.

    Row 1008 (RFC 8767): once an answer's TTL lapses, CoreDNS is re-asked
    asynchronously and a timely answer is returned fresh.  The stale answer is
    served only when that refresh fails or is still unanswered, for at most
    5 minutes past the TTL; after a failure it is served without a new query
    for 30 seconds.  An ``/etc/hosts`` fallback is never served stale.
    """
    name = hostname.rstrip(".").lower()
    if not name.endswith(".mesh.local"):
        return _hosts_address(name)

    # row1008: fresh answer, or an async refresh that falls back to stale
    stale_cache = _get_stale_cache()
    stale_result = stale_cache.lookup(name)
    if stale_result is not None:
        return stale_result

    # Legacy path: fresh lookup
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(name)
        if cached and cached[0] > now:
            return cached[1]

    try:
        address = _query_coredns(name, nameserver=nameserver, timeout=timeout)
    except (OSError, ValueError):
        # row842 fixer (2, round 2): a CoreDNS failure must not pin the
        # /etc/hosts fallback for the full TTL (CoreDNS is retried within
        # seconds), but a name CoreDNS never answers must not cost the
        # 250ms timeout on EVERY lookup either (a third of the real catalog
        # does that) -- the fallback is cached for a SHORT negative TTL.
        address = _hosts_address(name)
        with _CACHE_LOCK:
            _CACHE[name] = (time.monotonic() + min(FALLBACK_TTL_SECONDS, max(0.0, ttl_seconds)), address)
        return address

    # Store in both caches
    with _CACHE_LOCK:
        _CACHE[name] = (time.monotonic() + max(0.0, ttl_seconds), address)
    stale_cache.store(name, address, ttl_override=max(0.0, ttl_seconds))
    return address


def _query_coredns(hostname: str, *, nameserver: tuple[str, int], timeout: float) -> str:
    host, port = nameserver
    if not ipaddress.ip_address(host).is_loopback:
        raise ValueError("CoreDNS nameserver must be loopback")
    if not 0 < port < 65536 or timeout <= 0:
        raise ValueError("invalid CoreDNS endpoint")

    transaction_id = secrets.randbits(16)
    labels = hostname.split(".")
    if any(not label or len(label.encode("ascii")) > 63 for label in labels):
        raise ValueError("invalid mesh hostname")
    question = b"".join(bytes((len(label),)) + label.encode("ascii") for label in labels) + b"\0"
    query = struct.pack("!HHHHHH", transaction_id, 0x0100, 1, 0, 0, 0)
    query += question + struct.pack("!HH", 1, 1)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.settimeout(timeout)
        # row842 fixer (1): CONNECT the datagram socket so the kernel only
        # delivers packets from the nameserver's address:port -- an
        # unconnected socket accepts a spoofed answer from any local sender.
        client.connect((host, port))
        client.send(query)
        response = client.recv(512)
    return _first_a_record(response, transaction_id)


def _first_a_record(response: bytes, transaction_id: int) -> str:
    if len(response) < 12:
        raise ValueError("truncated CoreDNS response")
    response_id, flags, questions, answers, _authority, _additional = struct.unpack("!HHHHHH", response[:12])
    if response_id != transaction_id or flags & 0x000F or not flags & 0x8000:
        raise ValueError("invalid CoreDNS response")
    offset = 12
    for _ in range(questions):
        offset = _skip_name(response, offset) + 4
        if offset > len(response):
            raise ValueError("truncated CoreDNS question")
    for _ in range(answers):
        offset = _skip_name(response, offset)
        if offset + 10 > len(response):
            raise ValueError("truncated CoreDNS answer")
        record_type, record_class, _ttl, length = struct.unpack("!HHIH", response[offset:offset + 10])
        offset += 10
        if offset + length > len(response):
            raise ValueError("truncated CoreDNS RDATA")  # row842 fixer (3)
        record = response[offset:offset + length]
        offset += length
        if record_type == 1 and record_class == 1 and len(record) == 4:
            return socket.inet_ntoa(record)
    raise ValueError("CoreDNS returned no A record")


def _skip_name(packet: bytes, offset: int) -> int:
    while True:
        if offset >= len(packet):
            raise ValueError("truncated DNS name")
        size = packet[offset]
        if size == 0:
            return offset + 1
        if size & 0xC0 == 0xC0:
            if offset + 1 >= len(packet):
                raise ValueError("truncated DNS pointer")
            return offset + 2
        if size & 0xC0 or offset + 1 + size > len(packet):
            raise ValueError("invalid DNS name")
        offset += size + 1


def _hosts_address(hostname: str) -> str:
    try:
        with open("/etc/hosts", encoding="utf-8") as hosts:
            for line in hosts:
                fields = line.split("#", 1)[0].split()
                if len(fields) > 1 and hostname in (entry.lower() for entry in fields[1:]):
                    return str(ipaddress.ip_address(fields[0]))
    except OSError as exc:
        raise OSError("cannot read /etc/hosts") from exc
    raise OSError(f"{hostname} is absent from /etc/hosts")
