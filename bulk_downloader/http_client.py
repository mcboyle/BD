"""Optional pooled loopback HTTP proxy for guarded egress."""
from __future__ import annotations

import os
import threading
import urllib.error
import urllib.request

_FAILURE_LIMIT = 3
_LOCK = threading.Lock()
_POOLS: dict[str, "_ProxyPool"] = {}


class _ProxyPool:
    def __init__(self, proxy_url: str) -> None:
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
        self.failures = 0

    @property
    def open_circuit(self) -> bool:
        return self.failures >= _FAILURE_LIMIT

    def open(self, request, *, timeout):
        return self.opener.open(request, timeout=timeout)

    def record_failure(self) -> None:
        self.failures += 1


def _proxy_url() -> str | None:
    value = os.environ.get("BD_HTTP_PROXY", "").strip()
    return value if value.startswith(("http://", "https://")) else None


def _pool(proxy_url: str) -> _ProxyPool:
    with _LOCK:
        pool = _POOLS.get(proxy_url)
        if pool is None:
            pool = _ProxyPool(proxy_url)
            _POOLS[proxy_url] = pool
        return pool


def proxy_open(request, *, timeout, direct_open):
    """Use a cached proxy opener when configured; otherwise retain direct egress.

    Proxy transport failures and repeated upstream 5xx responses fail open to the
    caller's direct opener, so an unavailable local proxy cannot stall media work.
    """
    proxy_url = _proxy_url()
    if not proxy_url:
        return direct_open(request, timeout=timeout)
    pool = _pool(proxy_url)
    if pool.open_circuit:
        return direct_open(request, timeout=timeout)
    try:
        return pool.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code >= 500:
            pool.record_failure()
            return direct_open(request, timeout=timeout)
        raise
    except OSError:
        pool.record_failure()
        return direct_open(request, timeout=timeout)
