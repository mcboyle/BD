"""bulk_downloader.proxy_pool -- F4: rotating proxy pool with health checks.

Egress-resilience infrastructure, the same class as the shipped per-site
egress + VPN kill-switch (NOT anti-bot evasion; the pool does not touch
captcha/flaresolverr/behavior-sim surfaces). Per-site egress historically
offered a single static ``proxy``; this adds an opt-in POOL: round-robin
selection over the currently-healthy members, with health tracked two
ways --

  * passively, via :func:`record_result` (a caller feeds proxy outcomes;
    N consecutive failures put a member in a cooldown), and
  * actively, via :func:`probe_pool` (a health-check sweep using an
    INJECTED probe function so nothing here does I/O in a unit test).

Everything except :func:`default_probe` is pure and network-free. State is
a plain dict the caller owns and persists (e.g. on the SiteRunner
instance)::

    {"health": {url: {"fails": int, "down_until": float}}, "cursor": int}
"""
from __future__ import annotations

import socket
import time
from typing import Callable, List, Optional
from urllib.parse import urlparse

__all__ = [
    "select_proxy", "record_result", "probe_pool", "healthy_urls",
    "default_probe",
]


def _ensure(state: dict) -> dict:
    state.setdefault("health", {})
    state.setdefault("cursor", 0)
    return state


def healthy_urls(pool, state: dict, *, now: Optional[float] = None) -> List[str]:
    """Pool members currently eligible (not in cooldown), in pool order."""
    now = time.time() if now is None else now
    _ensure(state)
    h = state["health"]
    return [u for u in (pool or [])
            if float(h.get(u, {}).get("down_until", 0) or 0) <= now]


def select_proxy(pool, state: dict, *, now: Optional[float] = None) -> Optional[str]:
    """Round-robin pick among the healthy pool members. Advances the
    rotation cursor (mutating ``state``). Returns the chosen url, or
    ``None`` when the pool is empty or every member is in cooldown.
    Network-free."""
    _ensure(state)
    hu = healthy_urls(pool, state, now=now)
    if not hu:
        return None
    idx = state["cursor"] % len(hu)
    state["cursor"] = (state["cursor"] + 1) % len(hu)
    return hu[idx]


def record_result(state: dict, url: str, ok: bool, *,
                  now: Optional[float] = None,
                  max_fails: int = 3, cooldown_s: float = 300.0) -> dict:
    """Feed one proxy outcome. On success: reset the member's fail count
    and clear any cooldown. On failure: increment the fail count and, once
    it reaches ``max_fails``, put the member in cooldown until
    ``now + cooldown_s``. Returns ``state``."""
    now = time.time() if now is None else now
    _ensure(state)
    rec = state["health"].setdefault(url, {"fails": 0, "down_until": 0})
    if ok:
        rec["fails"] = 0
        rec["down_until"] = 0
    else:
        rec["fails"] = int(rec.get("fails", 0)) + 1
        if rec["fails"] >= max_fails:
            rec["down_until"] = now + cooldown_s
    return state


def probe_pool(pool, state: dict, probe_fn: Callable[[str], bool], *,
               now: Optional[float] = None, cooldown_s: float = 300.0) -> dict:
    """Active health sweep. Calls ``probe_fn(url) -> bool`` for each member;
    a truthy result clears the member (healthy), a falsy result (or a
    raising probe) puts it in cooldown until ``now + cooldown_s``.
    ``probe_fn`` is injected so tests never touch the network. Returns
    ``state``."""
    now = time.time() if now is None else now
    _ensure(state)
    for u in (pool or []):
        try:
            ok = bool(probe_fn(u))
        except Exception:
            ok = False
        rec = state["health"].setdefault(u, {"fails": 0, "down_until": 0})
        if ok:
            rec["fails"] = 0
            rec["down_until"] = 0
        else:
            rec["down_until"] = now + cooldown_s
    return state


def default_probe(url: str, *, timeout: float = 5.0) -> bool:
    """Real TCP-connect health probe to a proxy url's host:port. The one
    I/O function in this module -- a production ``probe_fn`` for
    :func:`probe_pool`; not exercised by the unit suite. Returns True iff
    the connect succeeds within ``timeout``."""
    try:
        parsed = urlparse(url if "://" in (url or "") else "//" + (url or ""))
        host = parsed.hostname
        port = parsed.port or (443 if (parsed.scheme or "").endswith("s") else 80)
        if not host:
            return False
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False
