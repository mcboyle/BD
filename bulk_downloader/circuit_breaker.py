"""Per-host circuit breaker + TCP socket tuning (Phase 99, Block L).

Two concerns that share a module because both are network-side
guardrails the operator usually thinks about together:

1. Circuit breaker — when a single host returns >50% errors over a
   recent window, stop hammering it. Workers parked here move on to
   other URLs. After a cool-down, half-open: one probe at a time
   until success-rate recovers. Pattern from web-clone-master.

2. TCP socket tuning — set TCP_NODELAY (disable Nagle, useful for
   the small request/large response shape of video downloads) and
   bump SO_RCVBUF to 4 MiB so kernel buffering doesn't bottleneck
   on high-bandwidth links. Optional; cheap when ignored.

Design rules:
  • Per-host state lives in a module-level dict, protected by a lock.
    Survives the BD process; resets on restart (intentional — long-
    term host health is the operator's call, not BD's permanent record).
  • Cheap: every observe() is O(1). report() walks at most ~100 entries.
  • Fail-open: a broken circuit breaker must not gate downloads more
    than the natural retry policy already would.
"""
from __future__ import annotations

import socket
import threading
import time
from collections import deque
from typing import Optional
from urllib.parse import urlparse


# Per-host rolling stats: {host: deque of (timestamp, success_bool)}.
# Bounded — only keep observations within _WINDOW seconds. Lock held
# only while mutating the dict + per-host deques.
_observations: dict = {}
# Per-host state machine: "closed" | "open" | "half_open"
# "closed" = normal operation
# "open" = circuit open; workers park here, requests rejected
# "half_open" = one probe in flight; will close on success, open on fail
_states: dict = {}
_lock = threading.Lock()

# Tuning knobs — could become config options later, hardcoded for now.
_WINDOW = 300  # 5 min rolling window
_MIN_SAMPLES = 10  # need this many observations before tripping
_OPEN_THRESHOLD_PCT = 50  # >50% errors over window → open
_OPEN_DURATION = 120  # seconds to stay open before half-open probe
_HALF_OPEN_HEAL_THRESHOLD = 3  # consecutive successes to close again


def _host_of(url: str) -> Optional[str]:
    """Extract scheme+host[+port] as the cache key. Same site on
    different schemes/ports of the same host share one breaker — if
    blacked.com:443 is failing, blacked.com:80 is almost certainly
    failing too (different reverse-proxy hop, same backend)."""
    if not url:
        return None
    try:
        p = urlparse(url)
        if not p.hostname:
            return None
        return p.hostname
    except Exception:
        return None


def observe(url: str, *, success: bool):
    """Record an observation. Cheap O(1). Trims old entries from the
    per-host deque on each call (lazy GC)."""
    host = _host_of(url)
    if not host:
        return
    now = time.time()
    with _lock:
        dq = _observations.get(host)
        if dq is None:
            dq = _observations[host] = deque()
        dq.append((now, bool(success)))
        # Trim old observations
        while dq and (now - dq[0][0]) > _WINDOW:
            dq.popleft()
        state = _states.get(host, "closed")
        if state == "half_open":
            if success:
                # Count consecutive successes since half-open
                successes = 0
                for ts, ok in reversed(dq):
                    if ok:
                        successes += 1
                    else:
                        break
                if successes >= _HALF_OPEN_HEAL_THRESHOLD:
                    _states[host] = "closed"
                    _states.pop(host + "::opened_at", None)
            else:
                # Failed probe; re-open
                _states[host] = "open"
                _states[host + "::opened_at"] = now
        elif state == "closed":
            # Check trip condition
            if len(dq) >= _MIN_SAMPLES:
                errs = sum(1 for _ts, ok in dq if not ok)
                err_pct = (errs / len(dq)) * 100
                if err_pct > _OPEN_THRESHOLD_PCT:
                    _states[host] = "open"
                    _states[host + "::opened_at"] = now


def allow(url: str) -> bool:
    """Should the caller proceed with this URL?
    Returns False when the circuit is open. In half-open, returns True
    only for the probe (one at a time)."""
    host = _host_of(url)
    if not host:
        return True
    now = time.time()
    with _lock:
        state = _states.get(host, "closed")
        if state == "closed":
            return True
        opened_at = _states.get(host + "::opened_at", 0)
        if state == "open":
            if (now - opened_at) >= _OPEN_DURATION:
                # Transition to half-open; allow this caller as probe
                _states[host] = "half_open"
                return True
            return False
        # half_open: caller is the probe; allow but next callers wait
        return False


def state(url: str) -> str:
    """Current state for `url`'s host. closed | open | half_open."""
    host = _host_of(url)
    if not host:
        return "closed"
    with _lock:
        return _states.get(host, "closed")


def report() -> dict:
    """Return per-host stats for /api/status or a debug dashboard.
    {host: {state, samples, error_pct, consecutive_successes,
            consecutive_failures, opened_seconds_ago}}"""
    now = time.time()
    out = {}
    with _lock:
        for host, dq in _observations.items():
            if not dq:
                continue
            n = len(dq)
            errs = sum(1 for _ts, ok in dq if not ok)
            st = _states.get(host, "closed")
            opened_at = _states.get(host + "::opened_at", 0)
            # Count streaks from the most recent observation backward.
            # `consecutive_successes` = length of the trailing run of
            # ok=True; `consecutive_failures` = length of trailing run
            # of ok=False. At most one is nonzero (or both zero if
            # the deque is empty, which we've already filtered).
            cs, cf = 0, 0
            for _ts, ok in reversed(dq):
                if ok:
                    if cf > 0:
                        break
                    cs += 1
                else:
                    if cs > 0:
                        break
                    cf += 1
            out[host] = {
                "state": st,
                "samples": n,
                "error_pct": round((errs / n) * 100, 1) if n else 0.0,
                "consecutive_successes": cs,
                "consecutive_failures": cf,
                "opened_seconds_ago": int(now - opened_at) if opened_at else None,
            }
    return out


def reset(host: Optional[str] = None):
    """Wipe state. If `host` is None, wipe all (use after a network
    blip recovers system-wide). Operator-callable from /api/circuit/reset."""
    with _lock:
        if host is None:
            _observations.clear()
            _states.clear()
        else:
            _observations.pop(host, None)
            _states.pop(host, None)
            _states.pop(host + "::opened_at", None)


# ─── TCP socket tuning ────────────────────────────────────────────────

def apply_tcp_tuning(sock: socket.socket, *,
                     nodelay: bool = True,
                     rcvbuf_kb: int = 4096) -> None:
    """Apply common tunings to a connected TCP socket.

      • TCP_NODELAY disables Nagle's algorithm. For video downloads
        (small request, large streaming response), Nagle adds tens of
        ms of latency per chunk without saving bandwidth. Turning it
        off is a free win when each request gets its own connection.

      • SO_RCVBUF expands the receive buffer. The kernel auto-tunes
        these on Linux, but on Windows and BSD the default (~64 KiB)
        is the bandwidth-delay product bottleneck on >100 Mbps links.
        4 MiB is enough for ~1 Gbps over a 35 ms RTT path.

    Fail-open: any setsockopt failure is silently ignored; the
    download still works on the default tunings."""
    if nodelay:
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except (OSError, AttributeError):
            pass
    if rcvbuf_kb and rcvbuf_kb > 0:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf_kb * 1024)
        except (OSError, AttributeError):
            pass
