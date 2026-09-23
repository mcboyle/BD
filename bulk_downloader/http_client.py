"""Optional pooled loopback HTTP proxy for guarded egress."""
from __future__ import annotations

import os
import threading
from typing import Any, Optional
import urllib.error
import urllib.parse
import urllib.request

_FAILURE_LIMIT = 3
_LOCK = threading.Lock()
_POOLS: dict[str, "_ProxyPool"] = {}


class _ProxyPool:
    def __init__(self, proxy_url: str) -> None:
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
        self.failures = 0
        from .transport_circuit import SlidingWindowConfig, get_transport_circuit
        # R4 fix: the registry key becomes the breaker's public ``name``, and a proxy URL
        # routinely carries credentials (http://user:pass@host:port). Key on host:port only,
        # so no stats, log line or telemetry that enumerates circuit names can leak them.
        # The breaker is the ONLY admission gate (E1). min_calls=_FAILURE_LIMIT keeps row 839's
        # contract -- three consecutive proxy errors stop sending through the proxy -- while
        # the breaker, unlike the old counter, lets the proxy back in once it recovers.
        self.circuit = get_transport_circuit(
            f"proxy:{_circuit_key(proxy_url)}",
            SlidingWindowConfig(min_calls=_FAILURE_LIMIT))

    @property
    def open_circuit(self) -> bool:
        """Whether this pool is refusing traffic. Reading it changes nothing (R1).

        Previously this called ``can_execute()``, which SPENDS a leaky-bucket recovery token;
        three idle reads drained the probe budget and the fourth reported the proxy as open.
        Admission is now taken once per real attempt, in ``proxy_open`` via ``try_acquire``.
        """
        return self.circuit.is_open

    def try_acquire(self) -> bool:
        """Admission for ONE real request attempt: the only call that spends a token.

        Decided by the breaker alone. ``failures`` is telemetry: gating on it latched the pool
        shut for the process lifetime, since only a proxy success -- which that gate made
        unreachable -- ever cleared it (E1, N6-A/P3-A).
        """
        return self.circuit.try_acquire()

    def open(self, request, *, timeout):
        return self.opener.open(request, timeout=timeout)

    def record_failure(self) -> None:
        """Count a proxy error (telemetry) and feed it to the breaker, which decides admission."""
        self.failures += 1
        self.circuit.record_failure()

    def record_success(self) -> None:
        """R2 fix: success is reported from the live path, so the circuit can close.

        The ``failures`` telemetry counter restarts with it (consecutive proxy errors).
        """
        self.failures = 0
        self.circuit.record_success()


def _circuit_key(proxy_url: str) -> str:
    """host:port for *proxy_url*, with any userinfo dropped. Never the raw URL (R4)."""
    try:
        parsed = urllib.parse.urlsplit(proxy_url)
    except ValueError:
        return "unparseable"
    host = parsed.hostname or "unknown-host"
    return f"{host}:{parsed.port}" if parsed.port else host


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
    # One admission per real attempt (R1): in HALF_OPEN_LEAKING this is what spends a
    # recovery token, and it is reached only here, on the path that actually sends a request.
    if not pool.try_acquire():
        return direct_open(request, timeout=timeout)
    try:
        response = pool.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code >= 500:
            pool.record_failure()
            return direct_open(request, timeout=timeout)
        raise
    except OSError:
        pool.record_failure()
        return direct_open(request, timeout=timeout)
    # R2: the success half of the loop. Without it the circuit only ever accumulates
    # failures and can never leave HALF_OPEN_LEAKING for CLOSED.
    pool.record_success()
    return response


def unified_request(url: str, method: str = "GET", headers: Optional[dict] = None, timeout: float = 30.0, data: Any = None):
    """Execute request via the unified egress client."""
    from .egress_transport import get_unified_egress_client
    client = get_unified_egress_client()
    return client.request(method=method, url=url, headers=headers, data=data, timeout=timeout)
