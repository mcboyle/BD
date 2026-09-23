"""v3.66.1627: Connection Liveness Monitoring & Health Probing (Row 1077).

Autonomous active connection health probing, liveness state management,
and telemetry latency accounting for network endpoints.

Provides:
  - Active TCP socket probe with high-resolution latency measurement
  - Configurable failure and recovery hysteresis thresholds
  - Liveness state transitions: HEALTHY, DEGRADED, DOWN, UNKNOWN
  - Event notifications for state transitions
  - Thread-safe background monitoring daemon
  - Telemetry summary and health rollup
"""

from __future__ import annotations

import collections
import enum
import ipaddress
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit


class LivenessState(str, enum.Enum):
    """Connection liveness state."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"


@dataclass
class EndpointTarget:
    """Monitored target endpoint specification."""
    host: str
    port: int
    key: str
    protocol: str = "tcp"
    timeout_s: float = 2.0
    failure_threshold: int = 3
    recovery_threshold: int = 2
    latency_threshold_ms: float = 500.0

    @classmethod
    def from_target(
        cls,
        target: str,
        default_port: int = 80,
        failure_threshold: int = 3,
        recovery_threshold: int = 2,
        latency_threshold_ms: float = 500.0,
    ) -> EndpointTarget:
        """Parse target string (URL, host:port, or bare host) into EndpointTarget.

        A bare IPv6 literal ('::1', '2001:db8::7') is one host, bracketed before
        parsing so its colons are not read as a port. A target with no host
        ('http:///path') raises ValueError: there is nothing to probe, and it
        must not register as ':<port>'.
        """
        cleaned = target.strip()
        if "://" not in cleaned:
            try:
                bare_ipv6 = ipaddress.ip_address(cleaned).version == 6
            except ValueError:
                bare_ipv6 = False
            cleaned = "//" + (f"[{cleaned}]" if bare_ipv6 else cleaned)
        parsed = urlsplit(cleaned)
        host = parsed.hostname or ""
        if not host:
            raise ValueError(f"no host in liveness target {target!r}")
        try:
            port = parsed.port
        except ValueError:
            port = None
        if port is None:
            if parsed.scheme == "https":
                port = 443
            elif parsed.scheme == "http":
                port = 80
            else:
                port = default_port

        host = host.lower()
        key = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        return cls(
            host=host,
            port=port,
            key=key,
            failure_threshold=failure_threshold,
            recovery_threshold=recovery_threshold,
            latency_threshold_ms=latency_threshold_ms,
        )


@dataclass
class ProbeResult:
    """Outcome of an active connection probe."""
    target: str
    state: LivenessState
    latency_ms: float
    timestamp: float
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "target": self.target,
            "state": self.state.value if isinstance(self.state, LivenessState) else str(self.state),
            "latency_ms": round(self.latency_ms, 2),
            "timestamp": self.timestamp,
            "consecutive_failures": self.consecutive_failures,
            "consecutive_successes": self.consecutive_successes,
            "error": self.error,
        }


def _refuse_non_public(host: str, addr_info) -> Optional[str]:
    """Reason to refuse if ANY resolved address is non-public, else None.

    Same predicate as the multi_conn/provider SSRF guard; judged on the
    addresses actually about to be connected, so there is no re-resolution gap.
    """
    from .provider_resolve_impl._common import _classify_ip

    for _family, _type, _proto, _canon, sockaddr in addr_info:
        try:
            addr = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            return f"non-public address refused: {sockaddr[0]!r} is not an IP"
        ok, reason = _classify_ip(addr, host)
        if not ok:
            return f"non-public address refused: {reason}"
    return None


def probe_socket(
    host: str,
    port: int,
    timeout_s: float = 2.0,
    allow_private: bool = False,
) -> Tuple[bool, float, Optional[str]]:
    """Execute active TCP socket connection probe to host:port.

    Fail-closed SSRF guard: unless ``allow_private``, a host resolving to any
    loopback/private/link-local/reserved address is refused without connecting.
    Every vetted address is tried in resolver order, as socket.create_connection
    does: a dual-stack host whose first answer is unreachable is up when a later
    one connects, and down only when all failed (each failure is reported).
    Latency runs from the first connect attempt to the one that succeeded; name
    resolution is not timed, so a slow resolver is not a slow endpoint.
    """
    if not host:
        return False, 0.0, "no host to probe"
    try:
        addr_info = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
        if not addr_info:
            return False, 0.0, "getaddrinfo returned no addresses"
        if not allow_private:
            refusal = _refuse_non_public(host, addr_info)
            if refusal:
                return False, 0.0, refusal
    except Exception as exc:
        return False, 0.0, str(exc)

    errors: List[str] = []
    t0 = time.perf_counter()
    for family, socktype, proto, _, sockaddr in addr_info:
        try:
            # The with-block closes the socket; a close that fails is a failed
            # attempt (reported below like any connect error), not dropped.
            with socket.socket(family, socktype, proto) as sock:
                sock.settimeout(timeout_s)
                sock.connect(sockaddr)
                latency_ms = (time.perf_counter() - t0) * 1000.0
            return True, latency_ms, None
        except Exception as exc:
            errors.append(str(exc))
    return False, 0.0, "; ".join(errors)


StateListener = Callable[[str, LivenessState, LivenessState, ProbeResult], None]


class ConnectionLivenessMonitor:
    """Thread-safe connection liveness monitoring and health probing orchestrator."""

    def __init__(self, poll_interval_s: float = 30.0, allow_private: bool = False) -> None:
        self._lock = threading.RLock()
        self._allow_private = bool(allow_private)
        self._targets: Dict[str, EndpointTarget] = {}
        self._results: Dict[str, ProbeResult] = {}
        self._latency_history: Dict[str, collections.deque[float]] = {}
        # Endpoints that went DOWN and have not yet had recovery_threshold
        # consecutive successful probes: DEGRADED, never HEALTHY, until they have.
        self._recovering: set[str] = set()
        self._listeners: List[StateListener] = []
        self._poll_interval_s = max(0.01, poll_interval_s)
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        # Failures that must not stop monitoring (a raising state listener, a
        # background poll that raised); summary() reports them.
        self._error_count = 0
        self._last_error: Optional[str] = None

    @property
    def is_running(self) -> bool:
        """Check if background monitoring daemon is active."""
        with self._lock:
            return self._worker_thread is not None and self._worker_thread.is_alive()

    def register_target(
        self,
        target: str,
        default_port: int = 80,
        failure_threshold: int = 3,
        recovery_threshold: int = 2,
        latency_threshold_ms: float = 500.0,
    ) -> EndpointTarget:
        """Register an endpoint target for monitoring.

        One registration per endpoint key: if any spelling of the endpoint (URL,
        host:port, mixed case) is already registered, that registration --
        thresholds included -- is kept and returned; unregister_target() first
        to change it.
        """
        tgt = EndpointTarget.from_target(
            target,
            default_port=default_port,
            failure_threshold=failure_threshold,
            recovery_threshold=recovery_threshold,
            latency_threshold_ms=latency_threshold_ms,
        )
        with self._lock:
            registered = self._targets.get(tgt.key)
            if registered is not None:
                return registered
            self._targets[tgt.key] = tgt
            if tgt.key not in self._results:
                self._results[tgt.key] = ProbeResult(
                    target=tgt.key,
                    state=LivenessState.UNKNOWN,
                    latency_ms=0.0,
                    timestamp=time.time(),
                    consecutive_failures=0,
                    consecutive_successes=0,
                    error=None,
                )
            if tgt.key not in self._latency_history:
                self._latency_history[tgt.key] = collections.deque(maxlen=20)
        return tgt

    def unregister_target(self, target: str) -> bool:
        """Unregister an endpoint target."""
        tgt = EndpointTarget.from_target(target)
        with self._lock:
            removed = self._targets.pop(tgt.key, None) is not None
            self._results.pop(tgt.key, None)
            self._latency_history.pop(tgt.key, None)
            self._recovering.discard(tgt.key)
            return removed

    def add_state_listener(self, listener: StateListener) -> None:
        """Add a state transition callback listener."""
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_state_listener(self, listener: StateListener) -> None:
        """Remove a state transition callback listener."""
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def get_target_status(self, target: str) -> ProbeResult:
        """Retrieve current liveness status for target."""
        tgt = EndpointTarget.from_target(target)
        with self._lock:
            res = self._results.get(tgt.key)
            if res is None:
                return ProbeResult(
                    target=tgt.key,
                    state=LivenessState.UNKNOWN,
                    latency_ms=0.0,
                    timestamp=time.time(),
                    consecutive_failures=0,
                    consecutive_successes=0,
                    error=None,
                )
            return res

    def get_all_statuses(self) -> Dict[str, Dict[str, object]]:
        """Retrieve status dictionary for all registered targets."""
        with self._lock:
            return {k: v.to_dict() for k, v in self._results.items()}

    def probe_target(
        self,
        target: str,
        timeout_s: Optional[float] = None,
        default_port: int = 80,
    ) -> ProbeResult:
        """Actively probe target and update state.

        The target is resolved to its endpoint key, so any spelling of a
        registered endpoint is probed under that registration's thresholds; an
        unregistered endpoint is registered with the defaults.
        """
        tgt = self.register_target(target, default_port=default_port)

        effective_timeout = timeout_s if timeout_s is not None else tgt.timeout_s
        ok, latency_ms, error = probe_socket(
            tgt.host, tgt.port, timeout_s=effective_timeout, allow_private=self._allow_private)

        res = ProbeResult(
            target=tgt.key,
            state=LivenessState.HEALTHY if ok else LivenessState.DOWN,
            latency_ms=latency_ms,
            timestamp=time.time(),
            error=error,
        )
        return self._apply_probe_result(res)

    def _apply_probe_result(self, raw_result: ProbeResult) -> ProbeResult:
        """Update internal state machine using hysteresis thresholds."""
        with self._lock:
            tgt = self.register_target(raw_result.target)

            prev = self._results.get(tgt.key)
            old_state = prev.state if prev is not None else LivenessState.UNKNOWN
            prev_fails = prev.consecutive_failures if prev is not None else 0
            prev_succs = prev.consecutive_successes if prev is not None else 0

            probe_ok = raw_result.state in (LivenessState.HEALTHY, LivenessState.DEGRADED) and raw_result.error is None

            if probe_ok:
                new_fails = 0
                new_succs = prev_succs + 1
                history = self._latency_history.setdefault(tgt.key, collections.deque(maxlen=20))
                history.append(raw_result.latency_ms)

                if tgt.key in self._recovering and new_succs < tgt.recovery_threshold:
                    new_state = LivenessState.DEGRADED
                elif raw_result.latency_ms > tgt.latency_threshold_ms:
                    new_state = LivenessState.DEGRADED
                else:
                    new_state = LivenessState.HEALTHY
                if new_succs >= tgt.recovery_threshold:
                    self._recovering.discard(tgt.key)
            else:
                new_fails = prev_fails + 1
                new_succs = 0
                if new_fails >= tgt.failure_threshold:
                    new_state = LivenessState.DOWN
                    self._recovering.add(tgt.key)
                else:
                    new_state = LivenessState.DEGRADED

            final_result = ProbeResult(
                target=tgt.key,
                state=new_state,
                latency_ms=raw_result.latency_ms,
                timestamp=raw_result.timestamp,
                consecutive_failures=new_fails,
                consecutive_successes=new_succs,
                error=raw_result.error,
            )
            self._results[tgt.key] = final_result
            listeners = list(self._listeners)

        if old_state != new_state:
            for listener in listeners:
                try:
                    listener(tgt.key, old_state, new_state, final_result)
                except Exception as exc:
                    self._record_error("state listener", exc)

        return final_result

    def probe_all(self) -> Dict[str, ProbeResult]:
        """Probe all registered targets."""
        with self._lock:
            target_keys = list(self._targets.keys())

        results: Dict[str, ProbeResult] = {}
        for key in target_keys:
            results[key] = self.probe_target(key)
        return results

    def start_background(self) -> None:
        """Start the background probe thread."""
        with self._lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                return
            self._stop_event.clear()
            self._worker_thread = threading.Thread(
                target=self._background_loop,
                name="ConnectionLivenessMonitor",
                daemon=True,
            )
            self._worker_thread.start()

    def stop_background(self, timeout: float = 2.0) -> None:
        """Stop background monitoring daemon."""
        with self._lock:
            self._stop_event.set()
            thread = self._worker_thread
        if thread is not None:
            thread.join(timeout=timeout)
        with self._lock:
            self._worker_thread = None

    def _background_loop(self) -> None:
        """Periodic background probe loop."""
        while not self._stop_event.is_set():
            try:
                self.probe_all()
            except Exception as exc:
                self._record_error("background probe", exc)
            self._stop_event.wait(self._poll_interval_s)

    def _record_error(self, where: str, exc: Exception) -> None:
        """Count a failure that must not stop monitoring; summary() reports it."""
        with self._lock:
            self._error_count += 1
            self._last_error = f"{where}: {type(exc).__name__}: {exc}"

    def summary(self) -> Dict[str, object]:
        """Aggregate telemetry summary across all monitored endpoints."""
        with self._lock:
            total = len(self._targets)
            healthy = sum(1 for r in self._results.values() if r.state == LivenessState.HEALTHY)
            degraded = sum(1 for r in self._results.values() if r.state == LivenessState.DEGRADED)
            down = sum(1 for r in self._results.values() if r.state == LivenessState.DOWN)
            unknown = sum(1 for r in self._results.values() if r.state == LivenessState.UNKNOWN)

            latencies = [
                r.latency_ms for r in self._results.values()
                if r.state in (LivenessState.HEALTHY, LivenessState.DEGRADED) and r.latency_ms > 0
            ]
            avg_latency = (sum(latencies) / len(latencies)) if latencies else 0.0
            uptime_ratio = (healthy + degraded) / total if total > 0 else 1.0

            return {
                "total_targets": total,
                "healthy": healthy,
                "degraded": degraded,
                "down": down,
                "unknown": unknown,
                "avg_latency_ms": round(avg_latency, 2),
                "uptime_ratio": round(uptime_ratio, 4),
                "error_count": self._error_count,
                "last_error": self._last_error,
            }


_GLOBAL_MONITOR: Optional[ConnectionLivenessMonitor] = None
_GLOBAL_LOCK = threading.Lock()


def get_connection_liveness_monitor() -> ConnectionLivenessMonitor:
    """Return process-wide singleton ConnectionLivenessMonitor."""
    global _GLOBAL_MONITOR
    with _GLOBAL_LOCK:
        if _GLOBAL_MONITOR is None:
            _GLOBAL_MONITOR = ConnectionLivenessMonitor()
        return _GLOBAL_MONITOR


def reset_connection_liveness_monitor() -> None:
    """Reset process-wide singleton (used by tests)."""
    global _GLOBAL_MONITOR
    with _GLOBAL_LOCK:
        if _GLOBAL_MONITOR is not None:
            _GLOBAL_MONITOR.stop_background(timeout=1.0)
            _GLOBAL_MONITOR = None
