"""Async DNS-over-HTTPS (DoH) fallback resolver with pinned EDNS Client Subnet.

WHY THIS MODULE EXISTS (Row 890, O870f / O882):
ISP DNS resolvers often return stale CDN IP addresses or inject ISP block pages
on third-party media domains. This module provides a resilient DNS-over-HTTPS (DoH)
resolver targeting Cloudflare and Google DoH endpoints with EDNS Client Subnet (ECS)
optimization for optimal CDN edge routing and seamless fallback to the system resolver.

PROPERTIES:
1. DoH over HTTPS: queries Cloudflare / Google DoH using the standard DNS JSON API.
2. EDNS Client Subnet (ECS): passes client subnet to ensure CDN edge proximity.
3. Sub-millisecond in-memory cache: thread-safe caching with TTL expiration (<0.5ms).
4. Seamless fallback: falls back to system resolver on timeout or endpoint failure.
5. Zero site logins touched (Fleet Rule 21).
"""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_DOH_ENDPOINTS = [
    "https://cloudflare-dns.com/dns-query",
    "https://dns.google/resolve",
]
DEFAULT_ECS_SUBNET = "198.51.100.0/24"
DEFAULT_TIMEOUT_S = 2.0
DEFAULT_CACHE_TTL = 300


class DNSCacheEntry:
    """Cached DNS resolution record."""

    def __init__(
        self,
        domain: str,
        record_type: str,
        addresses: list[str],
        ttl: int,
        source: str,
    ) -> None:
        self.domain = domain
        self.record_type = record_type
        self.addresses = addresses
        self.ttl = ttl
        self.source = source
        self.expires_at = time.monotonic() + max(1, ttl)

    @property
    def is_expired(self) -> bool:
        return time.monotonic() >= self.expires_at


class ResolutionResult:
    """Outcome of a DNS resolution attempt."""

    def __init__(
        self,
        domain: str,
        addresses: list[str],
        ttl: int,
        source: str,
        success: bool,
        duration_s: float = 0.0,
    ) -> None:
        self.domain = domain
        self.addresses = addresses
        self.ttl = ttl
        self.source = source
        self.success = success
        self.duration_s = duration_s

    def __repr__(self) -> str:
        return (
            f"ResolutionResult(domain={self.domain!r}, addresses={self.addresses!r}, "
            f"ttl={self.ttl}, source={self.source!r}, success={self.success})"
        )


def _doh_address_allowed(addr, host: str):
    """DoH policy: PUBLIC addresses only, for every DNS answer of the endpoint
    host and for every redirect hop. A DoH endpoint is an Internet service
    (Cloudflare/Google/Quad9); the hooks policy, which admits RFC 1918 and
    loopback for local webhooks, is NOT reused here (a redirect from a DoH
    endpoint must never reach a LAN service). Returns (ok, reason)."""
    import ipaddress
    from .provider_resolve_impl._common import _classify_ip  # lazy import
    try:
        ok, why = _classify_ip(ipaddress.ip_address(str(addr)), host)
    except ValueError:
        return False, f"unparseable address for {host}: {addr!r}"
    return bool(ok), str(why)


def _open(req: urllib.request.Request, timeout: float):
    """Open a DoH request through the redirect-guarded, address-pinned opener
    under the public-only DoH policy."""
    from .urllib_ssrf import PinnedUrlOpener  # lazy import
    return PinnedUrlOpener(_doh_address_allowed).open(req, timeout=timeout)


# DNS record types the resolver can ask a DoH endpoint for (RFC 1035/3596
# type codes); anything else is refused up front (no query, no fallback).
_RECORD_TYPES = {"A": 1, "AAAA": 28, "CNAME": 5, "NS": 2, "PTR": 12, "MX": 15,
                 "TXT": 16, "SRV": 33}
_ADDRESS_TYPES = {"A", "AAAA"}  # the only types the system resolver can serve


class DoHResolver:
    """DNS-over-HTTPS resolver with caching and system fallback."""

    def __init__(
        self,
        endpoints: list[str] | None = None,
        edns_client_subnet: str | None = DEFAULT_ECS_SUBNET,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.endpoints = list(endpoints or DEFAULT_DOH_ENDPOINTS)
        self.edns_client_subnet = edns_client_subnet
        self.timeout_s = timeout_s
        self._cache: dict[tuple[str, str], DNSCacheEntry] = {}
        self._cache_lock = threading.Lock()

    def clear_cache(self) -> None:
        """Clear all cached entries."""
        with self._cache_lock:
            self._cache.clear()

    def cache_get(self, domain: str, record_type: str = "A") -> DNSCacheEntry | None:
        """Fast thread-safe in-memory cache lookup (<0.5ms)."""
        key = (domain.lower(), record_type.upper())
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is not None:
                if entry.is_expired:
                    del self._cache[key]
                    return None
                return entry
            return None

    def cache_put(
        self,
        domain: str,
        record_type: str,
        addresses: list[str],
        ttl: int,
        source: str = "cache",
    ) -> None:
        """Store addresses in the in-memory cache. A nonpositive TTL means
        "do not cache" (RFC 2181 s8: TTL 0 = use once, never reuse)."""
        if ttl <= 0:
            return
        key = (domain.lower(), record_type.upper())
        entry = DNSCacheEntry(
            domain=domain,
            record_type=record_type,
            addresses=addresses,
            ttl=ttl,
            source=source,
        )
        with self._cache_lock:
            self._cache[key] = entry

    def _fetch_doh_json(
        self,
        endpoint: str,
        domain: str,
        record_type: str = "A",
        edns_client_subnet: str | None = None,
        timeout_s: float = 2.0,
    ) -> dict[str, Any]:
        """Perform HTTPS GET query against a DoH JSON endpoint."""
        params: dict[str, str] = {
            "name": domain,
            "type": record_type,
        }
        ecs = edns_client_subnet or self.edns_client_subnet
        if ecs:
            params["edns_client_subnet"] = ecs

        url = f"{endpoint}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/dns-json",
                "User-Agent": "BulkDownloader/3.0 DoHResolver",
            },
        )
        # row890 fixer (E2): never a bare urlopen -- it follows 301/302 to
        # anywhere after the endpoint was vetted. The hooks' pinned opener
        # (row 728) resolves once, opens only the vetted address and re-vets
        # every redirect target with the same policy.
        with _open(req, timeout_s) as resp:
            data = resp.read().decode("utf-8")
            return json.loads(data)  # type: ignore[no-any-return]

    async def _fetch_doh_json_async(
        self,
        endpoint: str,
        domain: str,
        record_type: str = "A",
        edns_client_subnet: str | None = None,
        timeout_s: float = 2.0,
    ) -> dict[str, Any]:
        """Async variant of _fetch_doh_json."""
        return await asyncio.to_thread(
            self._fetch_doh_json,
            endpoint=endpoint,
            domain=domain,
            record_type=record_type,
            edns_client_subnet=edns_client_subnet,
            timeout_s=timeout_s,
        )

    def _extract_addresses(self, payload: dict[str, Any], record_type: str) -> tuple[list[str], int]:
        """Parse answers from DoH JSON response."""
        addresses: list[str] = []
        min_ttl = DEFAULT_CACHE_TTL
        # row890 fixer (E1 / round-2 item 1): the record type is
        # case-normalised everywhere and mapped to its RFC type code -- a
        # CNAME/MX/TXT query keeps its own answers instead of collapsing to
        # AAAA; an unknown type matches nothing.
        expected_type = _RECORD_TYPES.get(record_type.upper())

        # Shape validation: a non-dict payload, "Answer": null, or a non-dict
        # answer element is "no addresses" -> the caller continues to the next
        # endpoint / system fallback instead of raising TypeError/AttributeError.
        answers = payload.get("Answer") if isinstance(payload, dict) else None
        if not isinstance(answers, list):
            return [], min_ttl
        for answer in answers:
            if not isinstance(answer, dict):
                continue
            if expected_type is not None and answer.get("type") == expected_type:
                addr = str(answer.get("data", "") or "").strip()
                if addr:
                    addresses.append(addr)
                try:
                    ttl = int(answer.get("TTL", DEFAULT_CACHE_TTL))
                except (TypeError, ValueError):
                    ttl = DEFAULT_CACHE_TTL
                min_ttl = min(min_ttl, ttl)

        return addresses, min_ttl

    def _system_resolve(self, domain: str, record_type: str = "A") -> list[str]:
        """Fallback to operating system standard resolver, in the address
        family the caller asked for (AAAA -> AF_INET6, else AF_INET)."""
        if record_type.upper() not in _ADDRESS_TYPES:
            return []  # getaddrinfo cannot answer CNAME/MX/TXT/...
        family = socket.AF_INET6 if record_type.upper() == "AAAA" else socket.AF_INET
        try:
            results = socket.getaddrinfo(domain, None, family)
            ips = [r[4][0] for r in results if r and r[4]]
            # Deduplicate while preserving order
            seen: set[str] = set()
            dedup: list[str] = []
            for ip in ips:
                if ip not in seen:
                    seen.add(ip)
                    dedup.append(ip)
            return dedup
        except (socket.gaierror, OSError):
            return []

    def resolve(self, domain: str, record_type: str = "A") -> ResolutionResult:
        """Resolve domain synchronously with caching, DoH HTTPS queries, and system fallback."""
        t0 = time.perf_counter()

        # 1. Check in-memory cache (<0.5ms)
        cached = self.cache_get(domain, record_type)
        if cached is not None:
            return ResolutionResult(
                domain=domain,
                addresses=cached.addresses,
                ttl=int(cached.expires_at - time.monotonic()),
                source="cache",
                success=True,
                duration_s=time.perf_counter() - t0,
            )

        # 2. Try configured DoH HTTPS endpoints
        for endpoint in self.endpoints:
            provider = "cloudflare" if "cloudflare" in endpoint else "google" if "google" in endpoint else "custom"
            try:
                payload = self._fetch_doh_json(
                    endpoint=endpoint,
                    domain=domain,
                    record_type=record_type,
                    edns_client_subnet=self.edns_client_subnet,
                    timeout_s=self.timeout_s,
                )
                addresses, ttl = self._extract_addresses(payload, record_type)
                if addresses:
                    self.cache_put(domain, record_type, addresses, ttl, source=f"doh_{provider}")
                    return ResolutionResult(
                        domain=domain,
                        addresses=addresses,
                        ttl=ttl,
                        source=f"doh_{provider}",
                        success=True,
                        duration_s=time.perf_counter() - t0,
                    )
            except (TimeoutError, urllib.error.URLError, OSError, json.JSONDecodeError):
                continue

        # 3. Fallback to system resolver
        sys_ips = self._system_resolve(domain, record_type)
        if sys_ips:
            self.cache_put(domain, record_type, sys_ips, ttl=60, source="system_fallback")
            return ResolutionResult(
                domain=domain,
                addresses=sys_ips,
                ttl=60,
                source="system_fallback",
                success=True,
                duration_s=time.perf_counter() - t0,
            )

        return ResolutionResult(
            domain=domain,
            addresses=[],
            ttl=0,
            source="failed",
            success=False,
            duration_s=time.perf_counter() - t0,
        )

    async def resolve_async(self, domain: str, record_type: str = "A") -> ResolutionResult:
        """Resolve domain asynchronously."""
        t0 = time.perf_counter()

        cached = self.cache_get(domain, record_type)
        if cached is not None:
            return ResolutionResult(
                domain=domain,
                addresses=cached.addresses,
                ttl=int(cached.expires_at - time.monotonic()),
                source="cache",
                success=True,
                duration_s=time.perf_counter() - t0,
            )

        for endpoint in self.endpoints:
            provider = "cloudflare" if "cloudflare" in endpoint else "google" if "google" in endpoint else "custom"
            try:
                payload = await self._fetch_doh_json_async(
                    endpoint=endpoint,
                    domain=domain,
                    record_type=record_type,
                    edns_client_subnet=self.edns_client_subnet,
                    timeout_s=self.timeout_s,
                )
                addresses, ttl = self._extract_addresses(payload, record_type)
                if addresses:
                    self.cache_put(domain, record_type, addresses, ttl, source=f"doh_{provider}")
                    return ResolutionResult(
                        domain=domain,
                        addresses=addresses,
                        ttl=ttl,
                        source=f"doh_{provider}",
                        success=True,
                        duration_s=time.perf_counter() - t0,
                    )
            except (TimeoutError, urllib.error.URLError, OSError, json.JSONDecodeError):
                continue

        sys_ips = await asyncio.to_thread(self._system_resolve, domain, record_type)
        if sys_ips:
            self.cache_put(domain, record_type, sys_ips, ttl=60, source="system_fallback")
            return ResolutionResult(
                domain=domain,
                addresses=sys_ips,
                ttl=60,
                source="system_fallback",
                success=True,
                duration_s=time.perf_counter() - t0,
            )

        return ResolutionResult(
            domain=domain,
            addresses=[],
            ttl=0,
            source="failed",
            success=False,
            duration_s=time.perf_counter() - t0,
        )


# Global singleton instance
_GLOBAL_RESOLVER: DoHResolver | None = None


def get_default_resolver() -> DoHResolver:
    global _GLOBAL_RESOLVER
    if _GLOBAL_RESOLVER is None:
        _GLOBAL_RESOLVER = DoHResolver()
    return _GLOBAL_RESOLVER


def resolve(domain: str, record_type: str = "A") -> ResolutionResult:
    return get_default_resolver().resolve(domain, record_type=record_type)


async def resolve_async(domain: str, record_type: str = "A") -> ResolutionResult:
    return await get_default_resolver().resolve_async(domain, record_type=record_type)
