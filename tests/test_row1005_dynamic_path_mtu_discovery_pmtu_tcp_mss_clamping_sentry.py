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

    # Sub-128 MTU boundary calculation without artificial upward floor (F2 fix)
    assert compute_tcp_mss(120, ip_version=IPVersion.IPV4) == 80
    assert compute_tcp_mss(100, ip_version=IPVersion.IPV4) == 60
    assert compute_tcp_mss(30, ip_version=IPVersion.IPV4) == 0


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


_WG_CFG = {
    "private_key": "FAKE-WG-PRIVATE-KEY-NOT-A-REAL-KEY-000000001",
    "address": "10.66.0.2/32",
    "peer_public_key": "FAKE-WG-PUBLIC-KEY-NOT-A-REAL-KEY-0000000001",
    "endpoint": "198.51.100.7:51820",
}


def _mtu_lines(conf_text: str) -> list[str]:
    return [ln for ln in conf_text.splitlines() if ln.startswith("MTU = ")]


def test_wireguard_render_conf_derives_tunnel_mtu_from_endpoint_path():
    """render_conf (the WireGuard .conf writer start() calls) consults the PMTU sentry.

    RED on base: render_conf ignores endpoint_mtu, so a 1340-byte underlay gets no MTU
    line and wg's default 1420 interface MTU black-holes full-size segments.
    """
    from bulk_downloader.pmtu_sentry import compute_tcp_mss
    from bulk_downloader.vpn_wireguard import render_conf

    # Sub-1360 underlay: 1340 - 80 = 1260; the kernel-derived MSS fits the wire
    lines = _mtu_lines(render_conf({**_WG_CFG, "endpoint_mtu": 1340}))
    assert lines == ["MTU = 1260"], (
        f"render_conf did not derive the tunnel MTU from endpoint_mtu: {lines}")
    mss = compute_tcp_mss(1260)
    assert mss == 1220
    assert mss + 40 + 80 <= 1340

    # 1500 underlay - 80 WireGuard overhead -> 1420; endpoint 1280 -> 1200
    assert _mtu_lines(render_conf({**_WG_CFG, "endpoint_mtu": 1500})) == ["MTU = 1420"]
    assert _mtu_lines(render_conf({**_WG_CFG, "endpoint_mtu": 1280})) == ["MTU = 1200"]

    # An explicit mtu above the path is clamped down; one already below it is kept
    assert _mtu_lines(render_conf({**_WG_CFG, "mtu": 1500, "endpoint_mtu": 1500})) == ["MTU = 1420"]
    assert _mtu_lines(render_conf({**_WG_CFG, "mtu": 1300, "endpoint_mtu": 1500})) == ["MTU = 1300"]


def test_wireguard_render_conf_unchanged_without_endpoint_mtu():
    """No endpoint_mtu -> the rendered .conf keeps the pre-row MTU behaviour."""
    from bulk_downloader.vpn_wireguard import render_conf

    assert _mtu_lines(render_conf(dict(_WG_CFG))) == []
    assert _mtu_lines(render_conf({**_WG_CFG, "mtu": 1500})) == ["MTU = 1500"]


def test_wireguard_render_conf_rejects_endpoint_mtu_below_ipv4_minimum():
    """An underlay too small to carry a 576-byte inner packet is an invalid config."""
    import pytest

    from bulk_downloader.vpn_wireguard import render_conf

    with pytest.raises(ValueError, match="endpoint_mtu"):
        render_conf({**_WG_CFG, "endpoint_mtu": 655})
    # 656 - 80 = 576 is the smallest accepted underlay
    assert _mtu_lines(render_conf({**_WG_CFG, "endpoint_mtu": 656})) == ["MTU = 576"]


def test_clamp_syn_mss_rejects_negative_advertised_mss():
    """A negative advertised MSS is malformed input, not a value to pass through."""
    import pytest

    from bulk_downloader.pmtu_sentry import clamp_syn_mss

    with pytest.raises(ValueError, match="advertised_mss"):
        clamp_syn_mss(-5, path_mtu=1500)
    assert clamp_syn_mss(0, path_mtu=1500) == 0


def test_single_loss_below_confirmed_size_does_not_shrink_path_mtu():
    """RFC 4821: one lost packet at or below an ACKed size is ordinary loss, not a PMTU change."""
    from bulk_downloader.pmtu_sentry import PMTUSentry, PMTUState

    sentry = PMTUSentry(consecutive_loss_threshold=3)
    sentry.register_route("198.51.100.9", interface_mtu=1500)
    sentry.record_probe_result("198.51.100.9", packet_size=1500, received_ack=True)
    route = sentry.record_probe_result("198.51.100.9", packet_size=1400, received_ack=False)

    assert route.effective_mtu == 1500, f"single sub-confirmed drop shrank MTU to {route.effective_mtu}"
    assert route.clamped_mss == 1460
    assert route.consecutive_drops == 1
    assert route.state == PMTUState.PROBING

    # Loss below an ACKed size also leaves an unconfirmed, higher estimate alone
    sentry.register_route("198.51.100.11", interface_mtu=1500)
    sentry.record_probe_result("198.51.100.11", packet_size=1500, received_ack=False)
    sentry.record_probe_result("198.51.100.11", packet_size=1300, received_ack=True)
    other = sentry.record_probe_result("198.51.100.11", packet_size=1200, received_ack=False)
    assert other.effective_mtu == 1450, f"sub-confirmed drop moved the estimate to {other.effective_mtu}"

    # Repeated loss still reaches the black-hole clamp
    sentry.record_probe_result("198.51.100.9", packet_size=1400, received_ack=False)
    route = sentry.record_probe_result("198.51.100.9", packet_size=1400, received_ack=False)
    assert route.state == PMTUState.BLACK_HOLE_DETECTED
    assert route.effective_mtu == 1280


def test_loss_above_confirmed_size_narrows_path_mtu():
    """A loss above every ACKed size narrows the search: -50 with no ACK yet, else the ACKed size."""
    from bulk_downloader.pmtu_sentry import PMTUSentry, PMTUState

    sentry = PMTUSentry(consecutive_loss_threshold=3)
    sentry.register_route("198.51.100.10", interface_mtu=1500)
    route = sentry.record_probe_result("198.51.100.10", packet_size=1500, received_ack=False)
    assert route.effective_mtu == 1450
    assert route.state == PMTUState.PROBING

    sentry.record_probe_result("198.51.100.10", packet_size=1300, received_ack=True)
    route = sentry.record_probe_result("198.51.100.10", packet_size=1400, received_ack=False)
    assert route.effective_mtu == 1300
    assert route.clamped_mss == 1260
