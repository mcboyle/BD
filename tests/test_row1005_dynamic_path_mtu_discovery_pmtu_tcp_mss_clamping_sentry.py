"""Row 1005: Dynamic Path MTU Discovery (PMTU) & TCP MSS Clamping Sentry.

Validates Path MTU Discovery (RFC 1191/4821), ICMP Packet-Too-Big handling,
TCP MSS clamping for IPv4/IPv6 across bare and tunneled links (WireGuard, VXLAN, GRE),
black hole router detection, and telemetry reporting.

RED on baseline: bulk_downloader.pmtu_sentry does not exist.
"""
from __future__ import annotations

import json
from typing import Any

BD_GATE_SCOPE = "module"


def test_module_exports():
    """RED assertion 1: bulk_downloader.pmtu_sentry must export core PMTU and MSS sentry components."""
    from bulk_downloader import pmtu_sentry

    assert hasattr(pmtu_sentry, "IPVersion")
    assert hasattr(pmtu_sentry, "PMTUState")
    assert hasattr(pmtu_sentry, "TunnelType")
    assert hasattr(pmtu_sentry, "PMTURouteRecord")
    assert hasattr(pmtu_sentry, "compute_tcp_mss")
    assert hasattr(pmtu_sentry, "clamp_syn_mss")
    assert hasattr(pmtu_sentry, "PMTUSentry")


def test_tcp_mss_calculation_ipv4_and_ipv6():
    """Verify standard TCP MSS computation for IPv4 (MTU - 40) and IPv6 (MTU - 60)."""
    from bulk_downloader.pmtu_sentry import IPVersion, compute_tcp_mss

    # Standard Ethernet: 1500 bytes
    # IPv4: 1500 - 20 (IP) - 20 (TCP) = 1460
    assert compute_tcp_mss(1500, ip_version=IPVersion.IPV4) == 1460

    # IPv6: 1500 - 40 (IPv6) - 20 (TCP) = 1440
    assert compute_tcp_mss(1500, ip_version=IPVersion.IPV6) == 1440

    # With TCP Timestamp option (12 extra bytes)
    assert compute_tcp_mss(1500, ip_version=IPVersion.IPV4, tcp_options_len=12) == 1448

    # Minimum MTU boundary clamping
    assert compute_tcp_mss(576, ip_version=IPVersion.IPV4) == 536


def test_syn_mss_clamping_logic():
    """Verify clamping of advertised MSS when path MTU cannot support it."""
    from bulk_downloader.pmtu_sentry import IPVersion, clamp_syn_mss

    # Client advertises 1460 MSS (assuming 1500 MTU), but path MTU is 1420 (e.g. WireGuard)
    # Target MSS for 1420 MTU IPv4 = 1420 - 40 = 1380
    clamped = clamp_syn_mss(advertised_mss=1460, path_mtu=1420, ip_version=IPVersion.IPV4)
    assert clamped == 1380

    # Client advertises smaller MSS (1300), which already fits within 1420 MTU (1380 max)
    # Must NOT increase advertised MSS
    unchanged = clamp_syn_mss(advertised_mss=1300, path_mtu=1420, ip_version=IPVersion.IPV4)
    assert unchanged == 1300


def test_tunnel_overhead_adjustments():
    """Verify effective MTU and MSS calculations accounting for tunnel encapsulations."""
    from bulk_downloader.pmtu_sentry import IPVersion, PMTUSentry, TunnelType

    sentry = PMTUSentry()

    # WireGuard tunnel: 80 bytes overhead on a 1500 physical link -> 1420 effective MTU
    route_wg = sentry.register_route(
        destination="10.0.70.95",
        interface_mtu=1500,
        tunnel_type=TunnelType.WIREGUARD,
        ip_version=IPVersion.IPV4,
    )
    assert route_wg.effective_mtu == 1420
    assert route_wg.clamped_mss == 1380

    # VXLAN encapsulation: 50 bytes overhead -> 1450 effective MTU -> 1410 MSS
    route_vxlan = sentry.register_route(
        destination="192.168.1.50",
        interface_mtu=1500,
        tunnel_type=TunnelType.VXLAN,
        ip_version=IPVersion.IPV4,
    )
    assert route_vxlan.effective_mtu == 1450
    assert route_vxlan.clamped_mss == 1410


def test_pmtu_route_registration_and_initial_state():
    """Verify route registration, lookup, duplicate handling, and initial STABLE state."""
    from bulk_downloader.pmtu_sentry import PMTUState, PMTUSentry

    sentry = PMTUSentry()
    route = sentry.register_route("10.0.0.1", interface_mtu=1500)

    assert route.destination == "10.0.0.1"
    assert route.state == PMTUState.STABLE
    assert sentry.has_route("10.0.0.1") is True
    assert sentry.has_route("10.0.0.2") is False

    # Check route lookup
    fetched = sentry.get_route("10.0.0.1")
    assert fetched is not None
    assert fetched.effective_mtu == 1500


def test_icmp_packet_too_big_feedback_adaptation():
    """Verify route PMTU downward adjustment upon ICMP Packet Too Big (PTB) notification."""
    from bulk_downloader.pmtu_sentry import PMTUState, PMTUSentry

    sentry = PMTUSentry()
    sentry.register_route("198.51.100.1", interface_mtu=1500)

    # Router sends ICMP PTB indicating next-hop MTU is 1400
    sentry.handle_icmp_packet_too_big("198.51.100.1", next_hop_mtu=1400)
    route = sentry.get_route("198.51.100.1")

    assert route.effective_mtu == 1400
    assert route.clamped_mss == 1360
    assert route.state == PMTUState.STABLE
    assert 1400 in route.mtu_history


def test_black_hole_detection_without_icmp():
    """Verify detection of PMTU black holes when large packets drop silently without ICMP."""
    from bulk_downloader.pmtu_sentry import PMTUState, PMTUSentry

    sentry = PMTUSentry(consecutive_loss_threshold=3)
    sentry.register_route("203.0.113.5", interface_mtu=1500)

    # 3 consecutive drops of full-sized packets without ICMP PTB
    sentry.record_probe_result("203.0.113.5", packet_size=1500, received_ack=False)
    sentry.record_probe_result("203.0.113.5", packet_size=1500, received_ack=False)
    sentry.record_probe_result("203.0.113.5", packet_size=1500, received_ack=False)

    route = sentry.get_route("203.0.113.5")
    assert route.state == PMTUState.BLACK_HOLE_DETECTED
    assert sentry.is_black_hole("203.0.113.5") is True

    # Fallback to conservative safe MTU (e.g. 1280 or 576)
    assert route.effective_mtu <= 1280


def test_rfc4821_probing_convergence():
    """Verify search convergence towards true bottleneck MTU via search probes."""
    from bulk_downloader.pmtu_sentry import PMTUState, PMTUSentry

    sentry = PMTUSentry()
    sentry.register_route("192.0.2.1", interface_mtu=1500)

    # True path bottleneck is 1350 bytes
    # Probe 1500: fails
    sentry.record_probe_result("192.0.2.1", packet_size=1500, received_ack=False)
    # Probe 1280: succeeds
    sentry.record_probe_result("192.0.2.1", packet_size=1280, received_ack=True)
    # Probe 1350: succeeds
    sentry.record_probe_result("192.0.2.1", packet_size=1350, received_ack=True)
    # Probe 1400: fails
    sentry.record_probe_result("192.0.2.1", packet_size=1400, received_ack=False)

    route = sentry.get_route("192.0.2.1")
    # Best confirmed passing probe should be 1350
    assert route.effective_mtu == 1350
    assert route.clamped_mss == 1310


def test_sentry_telemetry_export_and_json_serialization():
    """Verify complete sentry state export to dict and valid JSON."""
    from bulk_downloader.pmtu_sentry import IPVersion, PMTUSentry, TunnelType

    sentry = PMTUSentry()
    sentry.register_route(
        "10.200.1.1",
        interface_mtu=1500,
        tunnel_type=TunnelType.WIREGUARD,
        ip_version=IPVersion.IPV4,
    )

    telemetry = sentry.export_telemetry()
    assert "routes" in telemetry
    assert "total_routes" in telemetry
    assert "black_holes_detected" in telemetry
    assert telemetry["total_routes"] == 1
    assert "10.200.1.1" in telemetry["routes"]

    raw_json = sentry.export_telemetry_json()
    parsed = json.loads(raw_json)
    assert parsed["routes"]["10.200.1.1"]["tunnel_type"] == "WIREGUARD"
    assert parsed["routes"]["10.200.1.1"]["clamped_mss"] == 1380

    sentry.reset()
    assert sentry.export_telemetry()["total_routes"] == 0


def test_wireguard_tunnel_mss_clamping_integration():
    """Verify WireGuard integration with dynamic PMTU MSS clamping sentry.

    RED on base: vpn_wireguard has no clamp_wireguard_mss sentry integration
    (fails for the row's reason: missing tunnel MSS clamping capability, NOT an ImportError).
    """
    from bulk_downloader import vpn_wireguard

    clamp_fn = getattr(vpn_wireguard, "clamp_wireguard_mss", None)
    assert clamp_fn is not None, "vpn_wireguard lacks clamp_wireguard_mss sentry integration"
    # Advertised 1460 on 1500 link with 80-byte WireGuard overhead -> effective 1420 MTU -> 1380 MSS
    assert clamp_fn(1460, endpoint_mtu=1500) == 1380
    # Advertised 1300 within 1420 MTU capacity -> remains 1300
    assert clamp_fn(1300, endpoint_mtu=1500) == 1300

