"""Multi-Homed Physical Egress Routing & Autonomous Interface Failover (Row 1065).

Provides:
- Multi-homed physical interface inventory and metrics (IP, gateway, priority, weight, link carrier).
- Link health from real connect outcomes, failure detection, and automatic failover.
- Anti-flapping damping (``flapping_damping_seconds``) and recovery hysteresis.
- Fail-closed security posture when all egress links are degraded or down.
- Socket-level interface/IP egress binding with a reported outcome (bound / ip-only / refused).
- ``connect_via_egress``: the socket factory the download path connects through.

With no interface registered the router is inert and ``connect_via_egress`` is exactly
``socket.create_connection``; once any interface is registered every egress socket is
bound to the active interface, and no healthy interface means the connect is refused.
"""

from __future__ import annotations

import errno
import ipaddress
import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

BIND_BOUND = "bound"  # IP bind + SO_BINDTODEVICE pin both took effect
BIND_IP_ONLY = "ip-only"  # IP bind only; the device pin was unavailable or refused

# Connect errors that indict the local link rather than the remote endpoint.
_LINK_FAILURE_ERRNOS = frozenset({
    errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN, errno.EADDRNOTAVAIL,
})


class EgressBindRefused(OSError):
    """No healthy egress interface to bind to, or the named one is not healthy."""


@dataclass
class InterfaceState:
    """Physical network interface state and health metrics."""

    name: str
    ip_address: str
    gateway: str
    priority: int = 100  # Lower value = higher priority
    weight: int = 1
    is_carrier_up: bool = True
    is_healthy: bool = True
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    latency_ms: float = 0.0
    last_probed_at: float = 0.0
    last_state_change: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "ip_address": self.ip_address,
            "gateway": self.gateway,
            "priority": self.priority,
            "weight": self.weight,
            "is_carrier_up": self.is_carrier_up,
            "is_healthy": self.is_healthy,
            "consecutive_failures": self.consecutive_failures,
            "consecutive_successes": self.consecutive_successes,
            "latency_ms": round(self.latency_ms, 2),
            "last_probed_at": self.last_probed_at,
            "last_state_change": self.last_state_change,
        }


@dataclass
class FailoverPolicy:
    """Thresholds and timing parameters governing autonomous interface failover."""

    max_consecutive_failures: int = 3
    recovery_success_threshold: int = 2
    # A tripped interface may not recover, and a recovered one may not take the
    # route back (failback), until it has held its state this long.
    flapping_damping_seconds: float = 5.0
    auto_failback: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_consecutive_failures": self.max_consecutive_failures,
            "recovery_success_threshold": self.recovery_success_threshold,
            "flapping_damping_seconds": self.flapping_damping_seconds,
            "auto_failback": self.auto_failback,
        }


class MultiHomedEgressRouter:
    """Autonomous routing engine managing multiple physical egress interfaces and failover."""

    def __init__(
        self,
        failover_policy: Optional[FailoverPolicy] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.failover_policy = failover_policy or FailoverPolicy()
        self.interfaces: Dict[str, InterfaceState] = {}
        self.active_interface_name: Optional[str] = None
        self._clock = clock
        self._lock = threading.Lock()
        self._event_log: List[Dict[str, Any]] = []

    def is_configured(self) -> bool:
        """True once any interface is registered: egress is then router-bound."""
        with self._lock:
            return bool(self.interfaces)

    def register_interface(
        self,
        name: str,
        ip_address: str,
        gateway: str,
        priority: int = 100,
        weight: int = 1,
        is_carrier_up: bool = True,
    ) -> InterfaceState:
        """Register a physical egress interface, or update an existing one in place.

        Re-registering keeps the health state and probe history: a tripped
        interface stays tripped until it recovers through probes.
        """
        ipaddress.ip_address(ip_address)  # reject a non-literal before it reaches bind()
        with self._lock:
            iface = self.interfaces.get(name)
            if iface is None:
                iface = InterfaceState(
                    name=name,
                    ip_address=ip_address,
                    gateway=gateway,
                    priority=priority,
                    weight=weight,
                    is_carrier_up=is_carrier_up,
                    is_healthy=True,
                    last_state_change=self._clock(),
                )
                self.interfaces[name] = iface
            else:
                iface.ip_address = ip_address
                iface.gateway = gateway
                iface.priority = priority
                iface.weight = weight
                iface.is_carrier_up = is_carrier_up
            self._recalculate_active_interface_locked()
            return iface

    def unregister_interface(self, name: str) -> None:
        """Remove an interface from active management."""
        with self._lock:
            self.interfaces.pop(name, None)
            if self.active_interface_name == name:
                self.active_interface_name = None
            self._recalculate_active_interface_locked()

    def get_active_interface(self) -> Optional[InterfaceState]:
        """Return the currently selected active egress interface, or None if all are down."""
        with self._lock:
            return self._active_locked()

    def _active_locked(self) -> Optional[InterfaceState]:
        iface = self.interfaces.get(self.active_interface_name or "")
        if iface is not None and iface.is_healthy and iface.is_carrier_up:
            return iface
        return None

    def select_egress_route(self, destination: Optional[str] = None) -> Optional[InterfaceState]:
        """Select the best healthy egress interface for outbound traffic."""
        return self.get_active_interface()

    def record_probe_result(
        self,
        name: str,
        success: bool,
        latency_ms: float = 0.0,
    ) -> bool:
        """Record link health probe outcome. Returns True if autonomous failover occurred."""
        now = self._clock()
        with self._lock:
            iface = self.interfaces.get(name)
            if not iface:
                return False

            iface.last_probed_at = now
            iface.latency_ms = latency_ms

            old_active = self.active_interface_name

            if success:
                iface.consecutive_successes += 1
                iface.consecutive_failures = 0
                # Recovery: success threshold AND the damping window since the trip.
                if (
                    not iface.is_healthy
                    and iface.consecutive_successes >= self.failover_policy.recovery_success_threshold
                    and now - iface.last_state_change >= self.failover_policy.flapping_damping_seconds
                ):
                    iface.is_healthy = True
                    iface.last_state_change = now
                    self._log_event_locked("interface_recovered", name)
            else:
                iface.consecutive_failures += 1
                iface.consecutive_successes = 0
                # Failure check: must meet consecutive failure threshold
                if iface.is_healthy:
                    if iface.consecutive_failures >= self.failover_policy.max_consecutive_failures:
                        iface.is_healthy = False
                        iface.last_state_change = now
                        self._log_event_locked("interface_failed", name)

            self._recalculate_active_interface_locked()
            return self.active_interface_name != old_active

    def trigger_failover(self, reason: str = "manual") -> Optional[str]:
        """Force immediate failover to the next best healthy interface."""
        with self._lock:
            current = self.active_interface_name
            healthy_candidates = [
                iface for iface in self.interfaces.values()
                if iface.name != current and iface.is_healthy and iface.is_carrier_up
            ]
            if not healthy_candidates:
                return None
            healthy_candidates.sort(key=lambda x: (x.priority, -x.weight, x.name))
            new_active = healthy_candidates[0].name
            self.active_interface_name = new_active
            self._log_event_locked(f"failover_{reason}", new_active)
            return new_active

    def bind_socket_egress(
        self,
        sock: socket.socket,
        interface_name: Optional[str] = None,
        require_device: bool = False,
    ) -> str:
        """Bind an outbound socket to a healthy physical interface.

        Returns ``BIND_BOUND`` (IP bind + SO_BINDTODEVICE) or ``BIND_IP_ONLY``
        (the device pin was unavailable or refused -- logged, recorded in the
        event log, and raised instead when ``require_device``). Raises
        ``EgressBindRefused`` when there is no healthy interface, including an
        explicitly named one that is tripped, carrier-down or unknown.
        """
        with self._lock:
            if interface_name:
                target_iface = self.interfaces.get(interface_name)
                if target_iface is None:
                    raise EgressBindRefused(f"egress interface {interface_name!r} is not registered")
                if not (target_iface.is_healthy and target_iface.is_carrier_up):
                    raise EgressBindRefused(f"egress interface {interface_name!r} is not healthy")
            else:
                target_iface = self._active_locked()
            if target_iface is None:
                raise EgressBindRefused("No healthy egress interface available to bind socket")
            name, ip_address = target_iface.name, target_iface.ip_address

        sock.bind((ip_address, 0))

        pin_error: Optional[str] = None
        if not hasattr(socket, "SO_BINDTODEVICE"):
            pin_error = "SO_BINDTODEVICE unsupported on this platform"
        else:
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, name.encode("utf-8"))
            except OSError as exc:
                pin_error = f"SO_BINDTODEVICE {name!r} refused: {exc}"
        if pin_error is None:
            return BIND_BOUND
        if require_device:
            raise EgressBindRefused(pin_error)
        logger.warning("row1065 egress: %s; socket bound to %s by IP only", pin_error, ip_address)
        with self._lock:
            self._log_event_locked("bind_ip_only", name)
        return BIND_IP_ONLY

    def connect(self, address: Tuple[str, int], timeout: Optional[float] = None) -> socket.socket:
        """Open a TCP connection to ``address`` from the active egress interface.

        Like ``socket.create_connection``, every resolved address is tried in
        order, each on a fresh socket bound to the same interface. The peer's
        outcome is ONE health input for the link: a connect, or any refusal
        from a far end (the link carried packets), counts for it; only when
        every attempt failed at the link (unreachable network or timeout) does
        it count against the interface.
        """
        with self._lock:
            iface = self._active_locked()
            if iface is None:
                raise EgressBindRefused("No healthy egress interface available to connect")
            name, ip_address = iface.name, iface.ip_address
        family = socket.AF_INET6 if ":" in ip_address else socket.AF_INET
        infos = socket.getaddrinfo(address[0], address[1], family, socket.SOCK_STREAM)
        if not infos:
            raise OSError(f"no {family.name} address for {address[0]!r} via {name}")
        last_error: Optional[OSError] = None
        link_carried = False
        for _family, _type, _proto, _canon, sockaddr in infos:
            sock = socket.socket(family, socket.SOCK_STREAM)
            t0 = time.perf_counter()
            try:
                self.bind_socket_egress(sock, name)
                sock.settimeout(timeout)
                sock.connect(sockaddr)
            except EgressBindRefused:
                sock.close()
                raise
            except OSError as exc:
                sock.close()
                last_error = exc
                if not (isinstance(exc, socket.timeout) or exc.errno in _LINK_FAILURE_ERRNOS):
                    link_carried = True
                continue
            self.record_probe_result(name, success=True, latency_ms=(time.perf_counter() - t0) * 1000.0)
            return sock
        self.record_probe_result(name, success=link_carried)
        raise last_error or OSError(f"no address for {address[0]!r} via {name}")

    def _recalculate_active_interface_locked(self) -> None:
        """Evaluate interface pool and assign the optimal active egress route."""
        healthy = [
            iface for iface in self.interfaces.values()
            if iface.is_healthy and iface.is_carrier_up
        ]
        if not healthy:
            self.active_interface_name = None
            return

        # Sort by priority ascending (10 before 20), weight descending, name
        healthy.sort(key=lambda x: (x.priority, -x.weight, x.name))
        best = healthy[0]

        current = self.interfaces.get(self.active_interface_name or "")
        if not current or not current.is_healthy or not current.is_carrier_up:
            # No usable current route; switch immediately
            self.active_interface_name = best.name
            return

        # Current is healthy: fail back only to a better interface that has held
        # its healthy state for the damping window.
        if (
            self.failover_policy.auto_failback
            and best.priority < current.priority
            and self._clock() - best.last_state_change >= self.failover_policy.flapping_damping_seconds
        ):
            self.active_interface_name = best.name

    def _log_event_locked(self, event_type: str, interface_name: str) -> None:
        self._event_log.append({
            "timestamp": self._clock(),
            "event": event_type,
            "interface": interface_name,
        })
        if len(self._event_log) > 100:
            self._event_log.pop(0)

    def get_egress_status(self) -> Dict[str, Any]:
        """Return operational telemetry of interfaces, failover policy, and active route."""
        with self._lock:
            return {
                "active_interface": self.active_interface_name,
                "failover_policy": self.failover_policy.to_dict(),
                "interfaces": {k: v.to_dict() for k, v in self.interfaces.items()},
                "recent_events": list(self._event_log[-10:]),
            }

    def to_dict(self) -> Dict[str, Any]:
        return self.get_egress_status()


# Global singleton instance
_GLOBAL_MULTI_HOMED_ROUTER: Optional[MultiHomedEgressRouter] = None
_GLOBAL_LOCK = threading.Lock()


def get_multi_homed_router() -> MultiHomedEgressRouter:
    """Retrieve or initialize the global MultiHomedEgressRouter singleton."""
    global _GLOBAL_MULTI_HOMED_ROUTER
    with _GLOBAL_LOCK:
        if _GLOBAL_MULTI_HOMED_ROUTER is None:
            _GLOBAL_MULTI_HOMED_ROUTER = MultiHomedEgressRouter()
        return _GLOBAL_MULTI_HOMED_ROUTER


def _is_loopback(host: str) -> bool:
    if host.lower() in ("localhost", "localhost."):
        return True
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    # ::ffff:127.0.0.1 is the same local carrier; the stdlib (< 3.13) does not call it loopback.
    return (getattr(ip, "ipv4_mapped", None) or ip).is_loopback


def connect_via_egress(address: Tuple[str, int], timeout: Optional[float] = None) -> socket.socket:
    """Egress socket factory for the download path.

    Unconfigured router or loopback peer: plain ``socket.create_connection``.
    Configured: bound to the active interface, fail-closed when none is healthy.
    """
    router = get_multi_homed_router()
    if not router.is_configured() or _is_loopback(address[0]):
        # A loopback peer (a local SOCKS/VPN carrier) is not reached over a
        # physical interface; binding it to one would only break it.
        return socket.create_connection(address, timeout=timeout)
    return router.connect(address, timeout=timeout)


def resolve_multi_homed_egress_ip(destination: Optional[str] = None) -> Optional[str]:
    """Convenience helper to resolve currently active egress IP address."""
    iface = get_multi_homed_router().select_egress_route(destination=destination)
    return iface.ip_address if iface else None


def record_interface_health(name: str, success: bool, latency_ms: float = 0.0) -> bool:
    """Convenience helper to record interface health probe results."""
    return get_multi_homed_router().record_probe_result(
        name=name,
        success=success,
        latency_ms=latency_ms,
    )
