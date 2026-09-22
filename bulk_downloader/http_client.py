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
    """Effective outbound proxy: the deploy env var, else the persisted global_config
    value written by the Settings Center runtime control (row 972).

    PRECEDENCE AND THE EMPTY STRING. A deploy that exports ``BD_HTTP_PROXY`` keeps
    winning, and exporting it EMPTY is an explicit "no proxy" that must NOT be
    resurrected by a stale stored value -- so the fallback is taken only when the
    variable is ABSENT, never when it is present-but-empty. The store read is lazy and
    fail-open: a missing or unreadable ``app_config.json`` yields direct egress, exactly
    as before this fallback existed.
    """
    raw = os.environ.get("BD_HTTP_PROXY")
    if raw is None:
        try:
            from . import global_config as _global_config

            raw = _global_config.get("BD_HTTP_PROXY", "")
        except Exception:  # noqa: BLE001 -- unreadable store => direct egress, never a crash
            raw = ""
    value = str(raw or "").strip()
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
