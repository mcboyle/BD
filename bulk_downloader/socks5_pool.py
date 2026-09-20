"""bulk_downloader/socks5_pool.py

Row 888: SOCKS5-PROXY-POOL-ROTATION-FOR-LOAD-DISTRIBUTION
Egress routing manager cycling outbound requests across an array of local SOCKS5 proxy ports.

Acceptance:
- Round-robin request distribution across active proxy endpoints
- Automatic health-check removal of dead proxies
- Direct connection fallback if entire pool is exhausted
- Zero site logins touched (Fleet Rule 21)
"""
from __future__ import annotations

import socket
import threading
import time
from typing import Iterable, Optional, Union
from urllib.parse import urlparse


class ProxyPoolExhaustedError(Exception):
    """Raised when all proxies in the pool are exhausted and fallback is disabled."""
    pass


class ProxyEndpoint:
    """Represents a single SOCKS5 proxy endpoint with health and telemetry metrics."""

    def __init__(
        self,
        target: Union[int, str, "ProxyEndpoint"],
        default_host: str = "127.0.0.1",
        default_protocol: str = "socks5",
    ) -> None:
        if isinstance(target, ProxyEndpoint):
            self.host = target.host
            self.port = target.port
            self.protocol = target.protocol
            self.url = target.url
            self.is_healthy = target.is_healthy
            self.failure_count = target.failure_count
            self.success_count = target.success_count
            self.last_checked = target.last_checked
            self.last_used = target.last_used
            return

        self.protocol = default_protocol
        self.host = default_host
        self.port = 1080

        if isinstance(target, int):
            self.port = target
        elif isinstance(target, str):
            clean = target.strip()
            if "://" in clean:
                parsed = urlparse(clean)
                self.protocol = parsed.scheme or default_protocol
                self.host = parsed.hostname or default_host
                self.port = parsed.port or 1080
            elif ":" in clean:
                host_part, port_part = clean.split(":", 1)
                self.host = host_part.strip() or default_host
                self.port = int(port_part.strip())
            else:
                self.port = int(clean)

        self.url = f"{self.protocol}://{self.host}:{self.port}"
        self.is_healthy: bool = True
        self.failure_count: int = 0
        self.success_count: int = 0
        self.last_checked: float = 0.0
        self.last_used: float = 0.0

    def __repr__(self) -> str:
        status = "healthy" if self.is_healthy else "dead"
        return f"<ProxyEndpoint url={self.url} status={status} failures={self.failure_count}>"


class Socks5ProxyPool:
    """Thread-safe round-robin SOCKS5 proxy pool manager with health checks and direct fallback."""

    def __init__(
        self,
        endpoints: Optional[Iterable[Union[int, str, ProxyEndpoint]]] = None,
        default_host: str = "127.0.0.1",
        max_failures: int = 3,
        check_timeout: float = 1.0,
        fallback_to_direct: bool = True,
    ) -> None:
        self.default_host = default_host
        self.max_failures = max(1, max_failures)
        self.check_timeout = max(0.05, check_timeout)
        self.fallback_to_direct = fallback_to_direct

        self._lock = threading.Lock()
        self._endpoints: list[ProxyEndpoint] = []
        self._index: int = 0

        if endpoints:
            for ep in endpoints:
                if ep is not None and str(ep).strip():
                    self._endpoints.append(ProxyEndpoint(ep, default_host=self.default_host))

    @property
    def total_count(self) -> int:
        with self._lock:
            return len(self._endpoints)

    @property
    def active_count(self) -> int:
        with self._lock:
            return sum(1 for ep in self._endpoints if ep.is_healthy)

    @property
    def dead_count(self) -> int:
        with self._lock:
            return sum(1 for ep in self._endpoints if not ep.is_healthy)

    @property
    def is_exhausted(self) -> bool:
        return self.active_count == 0

    @property
    def active_proxies(self) -> list[str]:
        with self._lock:
            return [ep.url for ep in self._endpoints if ep.is_healthy]

    @property
    def dead_proxies(self) -> list[str]:
        with self._lock:
            return [ep.url for ep in self._endpoints if not ep.is_healthy]

    def _find_endpoint(self, target: Union[int, str, ProxyEndpoint]) -> Optional[ProxyEndpoint]:
        """Match endpoint by instance, URL, or host:port."""
        if isinstance(target, ProxyEndpoint):
            for ep in self._endpoints:
                if ep is target or ep.url == target.url:
                    return ep
            return None

        norm = ProxyEndpoint(target, default_host=self.default_host).url
        for ep in self._endpoints:
            if ep.url == norm:
                return ep
        return None

    def get_next_proxy(self) -> Optional[str]:
        """Obtain next active proxy URL via round-robin.

        Returns:
            URL string (e.g. 'socks5://127.0.0.1:1080') if an active proxy exists.
            None if all proxies are dead/unreachable and fallback_to_direct is True.

        Raises:
            ProxyPoolExhaustedError if all proxies are dead and fallback_to_direct is False.
        """
        with self._lock:
            active = [ep for ep in self._endpoints if ep.is_healthy]
            if not active:
                if self.fallback_to_direct:
                    return None
                raise ProxyPoolExhaustedError("All SOCKS5 proxies in pool are exhausted")

            selected = active[self._index % len(active)]
            self._index = (self._index + 1) % len(active)
            selected.last_used = time.time()
            return selected.url

    def mark_dead(self, target: Union[int, str, ProxyEndpoint]) -> bool:
        """Mark a proxy endpoint as dead and remove it from active rotation."""
        with self._lock:
            ep = self._find_endpoint(target)
            if ep:
                ep.is_healthy = False
                ep.failure_count = max(ep.failure_count, self.max_failures)
                return True
            return False

    def mark_healthy(self, target: Union[int, str, ProxyEndpoint]) -> bool:
        """Restore a proxy endpoint to healthy active rotation."""
        with self._lock:
            ep = self._find_endpoint(target)
            if ep:
                ep.is_healthy = True
                ep.failure_count = 0
                return True
            return False

    def record_failure(self, target: Union[int, str, ProxyEndpoint]) -> bool:
        """Record a request failure. Marks dead if failure threshold is reached."""
        with self._lock:
            ep = self._find_endpoint(target)
            if ep:
                ep.failure_count += 1
                if ep.failure_count >= self.max_failures:
                    ep.is_healthy = False
                return True
            return False

    def record_success(self, target: Union[int, str, ProxyEndpoint]) -> bool:
        """Record a successful request. Resets failure count."""
        with self._lock:
            ep = self._find_endpoint(target)
            if ep:
                ep.failure_count = 0
                ep.success_count += 1
                return True
            return False

    def check_endpoint_health(self, ep: ProxyEndpoint) -> bool:
        """Attempt TCP connection probe against proxy host:port."""
        try:
            sock = socket.create_connection((ep.host, ep.port), timeout=self.check_timeout)
            sock.close()
            with self._lock:
                ep.is_healthy = True
                ep.failure_count = 0
                ep.last_checked = time.time()
            return True
        except (OSError, socket.error, TimeoutError):
            with self._lock:
                ep.is_healthy = False
                ep.failure_count = max(ep.failure_count, self.max_failures)
                ep.last_checked = time.time()
            return False

    def check_all_health(self) -> dict[str, bool]:
        """Probe all configured endpoints and return {url: is_healthy} map."""
        with self._lock:
            endpoints_copy = list(self._endpoints)

        results: dict[str, bool] = {}
        for ep in endpoints_copy:
            healthy = self.check_endpoint_health(ep)
            results[ep.url] = healthy
        return results

    def add_endpoint(self, target: Union[int, str, ProxyEndpoint]) -> ProxyEndpoint:
        """Add a new proxy endpoint to the pool."""
        new_ep = ProxyEndpoint(target, default_host=self.default_host)
        with self._lock:
            existing = self._find_endpoint(new_ep)
            if existing:
                return existing
            self._endpoints.append(new_ep)
            return new_ep

    def remove_endpoint(self, target: Union[int, str, ProxyEndpoint]) -> bool:
        """Remove a proxy endpoint completely from the pool."""
        with self._lock:
            ep = self._find_endpoint(target)
            if ep and ep in self._endpoints:
                self._endpoints.remove(ep)
                return True
            return False

    def reset(self) -> None:
        """Reset all endpoints to healthy and clear counters."""
        with self._lock:
            for ep in self._endpoints:
                ep.is_healthy = True
                ep.failure_count = 0
                ep.success_count = 0
            self._index = 0


def create_proxy_pool(
    raw_config: Optional[str] = None,
    default_host: str = "127.0.0.1",
    max_failures: int = 3,
    check_timeout: float = 1.0,
    fallback_to_direct: bool = True,
) -> Socks5ProxyPool:
    """Factory helper to parse comma/space separated ports or URLs into a Socks5ProxyPool."""
    endpoints: list[str] = []
    if raw_config:
        # Delimit by comma or whitespace
        parts = raw_config.replace(",", " ").split()
        for part in parts:
            item = part.strip()
            if item:
                endpoints.append(item)

    return Socks5ProxyPool(
        endpoints=endpoints,
        default_host=default_host,
        max_failures=max_failures,
        check_timeout=check_timeout,
        fallback_to_direct=fallback_to_direct,
    )
