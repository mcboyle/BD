"""Row 1005: Dynamic Path MTU Discovery (PMTU) & TCP MSS Clamping Sentry.

Provides automated Path MTU Discovery (RFC 1191 / RFC 4821), ICMP Packet-Too-Big
feedback processing, TCP MSS clamping for IPv4/IPv6 across physical and tunneled
network links (WireGuard, VXLAN, GRE, IPsec), and black hole router detection.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class IPVersion(str, Enum):
    IPV4 = "IPv4"
    IPV6 = "IPv6"


class PMTUState(str, Enum):
    STABLE = "STABLE"
    PROBING = "PROBING"
    DEGRADED = "DEGRADED"
    BLACK_HOLE_DETECTED = "BLACK_HOLE_DETECTED"


class TunnelType(str, Enum):
    NONE = "NONE"
    WIREGUARD = "WIREGUARD"
    VXLAN = "VXLAN"
    IPSEC = "IPSEC"
    GRE = "GRE"
    PPPOE = "PPPOE"


TUNNEL_OVERHEADS: dict[TunnelType, int] = {
    TunnelType.NONE: 0,
    TunnelType.WIREGUARD: 80,
    TunnelType.VXLAN: 50,
    TunnelType.IPSEC: 56,
    TunnelType.GRE: 24,
    TunnelType.PPPOE: 8,
}


def _normalize_ip_version(ip_v: IPVersion | str) -> IPVersion:
    if isinstance(ip_v, IPVersion):
        return ip_v
    if str(ip_v).lower() in ("ipv6", "6"):
        return IPVersion.IPV6
    return IPVersion.IPV4


def _normalize_tunnel_type(tunnel: TunnelType | str) -> TunnelType:
    if isinstance(tunnel, TunnelType):
        return tunnel
    name = str(tunnel).upper()
    for t in TunnelType:
        if t.value == name:
            return t
    return TunnelType.NONE


def compute_tcp_mss(
    mtu: int,
    ip_version: IPVersion | str = IPVersion.IPV4,
    tcp_options_len: int = 0,
) -> int:
    """Compute maximum segment size (MSS) for a given MTU and IP version.

    Args:
        mtu: Path MTU in bytes.
        ip_version: IPVersion.IPV4 or IPVersion.IPV6.
        tcp_options_len: Extra TCP options overhead in bytes (e.g. 12 for timestamps).

    Returns:
        The calculated TCP MSS in bytes.
    """
    version = _normalize_ip_version(ip_version)
    ip_hdr = 40 if version == IPVersion.IPV6 else 20
    tcp_hdr = 20 + max(0, tcp_options_len)
    return max(88, mtu - ip_hdr - tcp_hdr)


def clamp_syn_mss(
    advertised_mss: int,
    path_mtu: int,
    ip_version: IPVersion | str = IPVersion.IPV4,
) -> int:
    """Clamp the advertised TCP MSS in SYN packets to the path MTU limit.

    If the advertised MSS exceeds the path capacity, it is clamped down.
    If the advertised MSS is already smaller than the path capacity, it is kept untouched.
    """
    max_allowed = compute_tcp_mss(path_mtu, ip_version=ip_version)
    return min(advertised_mss, max_allowed)


@dataclass
class PMTURouteRecord:
    """Maintains PMTU state, clamping values, and probe history for a destination route."""

    destination: str
    interface_mtu: int = 1500
    tunnel_type: TunnelType = TunnelType.NONE
    ip_version: IPVersion = IPVersion.IPV4
    effective_mtu: int = 1500
    clamped_mss: int = 1460
    state: PMTUState = PMTUState.STABLE
    consecutive_drops: int = 0
    highest_confirmed_mtu: int = 1500
    lowest_failed_mtu: int | None = None
    last_probe_at: float = field(default_factory=time.time)
    mtu_history: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "destination": self.destination,
            "interface_mtu": self.interface_mtu,
            "tunnel_type": self.tunnel_type.value,
            "ip_version": self.ip_version.value,
            "effective_mtu": self.effective_mtu,
            "clamped_mss": self.clamped_mss,
            "state": self.state.value,
            "consecutive_drops": self.consecutive_drops,
            "highest_confirmed_mtu": self.highest_confirmed_mtu,
            "lowest_failed_mtu": self.lowest_failed_mtu,
            "last_probe_at": self.last_probe_at,
            "mtu_history": list(self.mtu_history),
        }


class PMTUSentry:
    """Dynamic Path MTU Discovery and TCP MSS Clamping Sentry."""

    def __init__(
        self,
        consecutive_loss_threshold: int = 3,
        min_ipv4_mtu: int = 576,
        min_ipv6_mtu: int = 1280,
    ) -> None:
        self.consecutive_loss_threshold = consecutive_loss_threshold
        self.min_ipv4_mtu = min_ipv4_mtu
        self.min_ipv6_mtu = min_ipv6_mtu
        self._routes: dict[str, PMTURouteRecord] = {}

    def register_route(
        self,
        destination: str,
        interface_mtu: int = 1500,
        tunnel_type: TunnelType | str = TunnelType.NONE,
        ip_version: IPVersion | str = IPVersion.IPV4,
    ) -> PMTURouteRecord:
        if not destination or not isinstance(destination, str):
            raise ValueError("destination must be a non-empty string")

        tunnel = _normalize_tunnel_type(tunnel_type)
        version = _normalize_ip_version(ip_version)
        overhead = TUNNEL_OVERHEADS.get(tunnel, 0)
        effective_mtu = max(
            self.min_ipv6_mtu if version == IPVersion.IPV6 else self.min_ipv4_mtu,
            interface_mtu - overhead,
        )
        clamped_mss = compute_tcp_mss(effective_mtu, ip_version=version)

        record = PMTURouteRecord(
            destination=destination,
            interface_mtu=interface_mtu,
            tunnel_type=tunnel,
            ip_version=version,
            effective_mtu=effective_mtu,
            clamped_mss=clamped_mss,
            state=PMTUState.STABLE,
            consecutive_drops=0,
            highest_confirmed_mtu=effective_mtu,
            lowest_failed_mtu=None,
            mtu_history=[effective_mtu],
        )
        self._routes[destination] = record
        return record

    def has_route(self, destination: str) -> bool:
        return destination in self._routes

    def get_route(self, destination: str) -> PMTURouteRecord | None:
        return self._routes.get(destination)

    def handle_icmp_packet_too_big(
        self,
        destination: str,
        next_hop_mtu: int,
    ) -> PMTURouteRecord | None:
        """Process ICMP Destination Unreachable - Fragmentation Needed (Type 3 Code 4) / PTB."""
        route = self._routes.get(destination)
        if not route:
            route = self.register_route(destination, interface_mtu=1500)

        min_mtu = self.min_ipv6_mtu if route.ip_version == IPVersion.IPV6 else self.min_ipv4_mtu
        safe_mtu = max(min_mtu, int(next_hop_mtu))
        route.effective_mtu = min(route.effective_mtu, safe_mtu)
        route.clamped_mss = compute_tcp_mss(route.effective_mtu, ip_version=route.ip_version)
        route.state = PMTUState.STABLE
        route.consecutive_drops = 0
        route.mtu_history.append(route.effective_mtu)
        route.last_probe_at = time.time()
        return route

    def record_probe_result(
        self,
        destination: str,
        packet_size: int,
        received_ack: bool,
    ) -> PMTURouteRecord | None:
        """Record the outcome of a probing packet of size `packet_size`."""
        route = self._routes.get(destination)
        if not route:
            route = self.register_route(destination, interface_mtu=1500)

        route.last_probe_at = time.time()
        min_mtu = self.min_ipv6_mtu if route.ip_version == IPVersion.IPV6 else self.min_ipv4_mtu

        if received_ack:
            # Successful delivery of probe
            route.consecutive_drops = 0
            if packet_size > route.highest_confirmed_mtu:
                route.highest_confirmed_mtu = packet_size
            route.effective_mtu = max(route.effective_mtu, packet_size)
            route.clamped_mss = compute_tcp_mss(route.effective_mtu, ip_version=route.ip_version)
            if route.state == PMTUState.BLACK_HOLE_DETECTED:
                route.state = PMTUState.DEGRADED
            elif route.state == PMTUState.PROBING:
                route.state = PMTUState.STABLE
        else:
            # Packet dropped without acknowledgement
            route.consecutive_drops += 1
            if route.lowest_failed_mtu is None or packet_size < route.lowest_failed_mtu:
                route.lowest_failed_mtu = packet_size

            # If dropped packet is at or above effective MTU, adjust downward
            if packet_size <= route.effective_mtu:
                if route.highest_confirmed_mtu < packet_size:
                    route.effective_mtu = max(min_mtu, route.highest_confirmed_mtu)
                else:
                    route.effective_mtu = max(min_mtu, packet_size - 50)
                route.clamped_mss = compute_tcp_mss(route.effective_mtu, ip_version=route.ip_version)

            if route.consecutive_drops >= self.consecutive_loss_threshold:
                route.state = PMTUState.BLACK_HOLE_DETECTED
                # Apply emergency black hole clamp to minimum safe MTU
                safe_floor = 1280 if route.ip_version == IPVersion.IPV6 else 1280
                route.effective_mtu = min(route.effective_mtu, safe_floor)
                route.clamped_mss = compute_tcp_mss(route.effective_mtu, ip_version=route.ip_version)
            else:
                route.state = PMTUState.PROBING

        if route.effective_mtu not in route.mtu_history:
            route.mtu_history.append(route.effective_mtu)

        return route

    def is_black_hole(self, destination: str) -> bool:
        route = self._routes.get(destination)
        if not route:
            return False
        return route.state == PMTUState.BLACK_HOLE_DETECTED

    def export_telemetry(self) -> dict[str, Any]:
        black_holes = sum(1 for r in self._routes.values() if r.state == PMTUState.BLACK_HOLE_DETECTED)
        return {
            "total_routes": len(self._routes),
            "black_holes_detected": black_holes,
            "routes": {dst: r.to_dict() for dst, r in self._routes.items()},
            "timestamp": time.time(),
        }

    def export_telemetry_json(self) -> str:
        return json.dumps(self.export_telemetry(), indent=2)

    def reset(self) -> None:
        self._routes.clear()
