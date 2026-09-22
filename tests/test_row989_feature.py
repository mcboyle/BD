"""Row 989: Inter-Seat IPC Latency Matrix & Clock Skew Monitor.

Validates inter-seat IPC latency tracking, pairwise RTT matrix computation,
percentile aggregation, clock skew measurement via Cristian's 4-timestamp
algorithm, anomaly detection, and cluster-wide divergence monitoring.

RED provenance: at base, _seam() converts the missing module into a pytest.fail
assertion from the test body (not a collection error), stating the capability
that is absent in the row's own terms.
"""
from __future__ import annotations

import json
import time
from typing import Any

import pytest

BD_GATE_SCOPE = "module"


def _seam():
    """The IPC latency module, resolved as an ASSERTION rather than a bare import.

    At base this module does not exist. A bare import would produce
    ModuleNotFoundError at collection time, which is the test failing to CALL
    the subject, not the subject failing. Called from the test body, the
    absence is a FAILURE with the row's own diagnostic.
    """
    try:
        from bulk_downloader import ipc_latency as seam
    except ModuleNotFoundError as exc:
        if exc.name not in (None, "bulk_downloader.ipc_latency"):
            pytest.fail(
                f"row989: the IPC latency module is present but its dependency "
                f"{exc.name!r} is not installed ({exc})")
        pytest.fail(
            f"row989: the inter-seat IPC latency monitoring capability is "
            f"absent ({exc}). bulk_downloader/ipc_latency.py must expose "
            f"InterSeatIPCMonitor, calculate_cristian_skew, and record_ipc_ping.")
    return seam


def test_module_exports():
    """The module must export all named components."""
    ipc_latency = _seam()

    assert hasattr(ipc_latency, "SeatNode")
    assert hasattr(ipc_latency, "IPCPingSample")
    assert hasattr(ipc_latency, "LatencySummary")
    assert hasattr(ipc_latency, "IPCLatencyMatrix")
    assert hasattr(ipc_latency, "ClockSkewSample")
    assert hasattr(ipc_latency, "ClockSkewAnomaly")
    assert hasattr(ipc_latency, "ClockSkewMonitor")
    assert hasattr(ipc_latency, "InterSeatIPCMonitor")
    assert hasattr(ipc_latency, "calculate_cristian_skew")
    assert hasattr(ipc_latency, "record_ipc_ping")


def test_seat_node_registration_and_validation():
    """Verify seat registration, validation, and seat inventory retrieval."""
    ipc = _seam()
    InterSeatIPCMonitor, SeatNode = ipc.InterSeatIPCMonitor, ipc.SeatNode

    monitor = InterSeatIPCMonitor()
    node1 = monitor.register_seat("bd-agy-worker-g16", role="worker", host="hub-mesh01", pid=896526)
    node2 = monitor.register_seat("bd-pm-Opus-B", role="pm", host="hub-mesh01", pid=12345)

    assert isinstance(node1, SeatNode)
    assert node1.seat_id == "bd-agy-worker-g16"
    assert node1.role == "worker"
    assert monitor.has_seat("bd-agy-worker-g16") is True
    assert monitor.has_seat("non-existent") is False
    assert len(monitor.get_seats()) == 2

    # Reject empty seat_id
    try:
        monitor.register_seat("", role="worker")
        assert False, "Expected ValueError for empty seat_id"
    except ValueError:
        pass


def test_ipc_ping_sample_and_rtt_calculation():
    """Verify RTT and Cristian's offset calculation from raw timestamps."""
    ipc = _seam()
    IPCPingSample, calculate_cristian_skew = ipc.IPCPingSample, ipc.calculate_cristian_skew

    # Timestamps in nanoseconds:
    # T0 (client send): 100_000_000 ns (100 ms)
    # T1 (server recv): 112_000_000 ns (server clock has +2ms skew, travel 10ms -> 110ms + 2ms = 112ms)
    # T2 (server send): 115_000_000 ns (processing 3ms)
    # T3 (client recv): 125_000_000 ns (travel 10ms -> 115ms + 10ms - 2ms skew = 125ms on client)
    t0_ns = 100_000_000
    t1_ns = 112_000_000
    t2_ns = 115_000_000
    t3_ns = 125_000_000

    skew_ms, rtt_ms = calculate_cristian_skew(t0_ns, t1_ns, t2_ns, t3_ns)
    # Total round-trip time = (T3 - T0) = 25ms.
    # Server processing = (T2 - T1) = 3ms.
    # Network round-trip delay = (25 - 3) = 22ms.
    # Offset = ((T1 - T0) + (T2 - T3)) / 2 = ((12) + (-10)) / 2 = +1.0 ms.
    assert round(rtt_ms, 2) == 22.0
    assert round(skew_ms, 2) == 1.0

    sample = IPCPingSample(
        sender_seat="bd-agy-worker-g16",
        receiver_seat="bd-pm-Opus-B",
        t0_ns=t0_ns,
        t1_ns=t1_ns,
        t2_ns=t2_ns,
        t3_ns=t3_ns,
    )
    assert round(sample.network_rtt_ms, 2) == 22.0
    assert round(sample.clock_offset_ms, 2) == 1.0
    assert round(sample.total_duration_ms, 2) == 25.0


def test_ipc_latency_matrix_accumulation_and_percentiles():
    """Verify matrix aggregation, percentile statistics (min, max, mean, p50, p95, p99), and jitter."""
    IPCLatencyMatrix = _seam().IPCLatencyMatrix

    matrix = IPCLatencyMatrix()
    # Add samples between g16 and worker-B1: latencies 10ms, 12ms, 14ms, 16ms, 20ms, 100ms
    latencies = [10.0, 12.0, 14.0, 16.0, 20.0, 100.0]
    for lat in latencies:
        # Create ping with network RTT equal to lat
        matrix.record_rtt("bd-agy-worker-g16", "bd-worker-B1", rtt_ms=lat)

    summary = matrix.get_pair_summary("bd-agy-worker-g16", "bd-worker-B1")
    assert summary is not None
    assert summary.sample_count == 6
    assert summary.min_ms == 10.0
    assert summary.max_ms == 100.0
    assert round(summary.mean_ms, 2) == round(sum(latencies) / 6, 2)
    assert summary.p50_ms == 15.0  # Median of sorted [10, 12, 14, 16, 20, 100]
    assert summary.p95_ms > 20.0
    assert summary.jitter_ms > 0.0


def test_highest_latency_and_degraded_links():
    """Verify detection of degraded IPC links and top bottleneck pairs."""
    IPCLatencyMatrix = _seam().IPCLatencyMatrix

    matrix = IPCLatencyMatrix()
    matrix.record_rtt("seat-A", "seat-B", rtt_ms=5.0)
    matrix.record_rtt("seat-A", "seat-C", rtt_ms=150.0)
    matrix.record_rtt("seat-B", "seat-C", rtt_ms=45.0)
    matrix.record_rtt("seat-C", "seat-D", rtt_ms=210.0)

    # Degraded links above 100ms SLA threshold
    degraded = matrix.get_degraded_links(threshold_ms=100.0)
    assert len(degraded) == 2
    degraded_pairs = {(item.sender_seat, item.receiver_seat) for item in degraded}
    assert ("seat-A", "seat-C") in degraded_pairs
    assert ("seat-C", "seat-D") in degraded_pairs

    # Top 2 highest latency pairs
    top2 = matrix.get_highest_latency_pairs(top_n=2)
    assert len(top2) == 2
    assert top2[0].receiver_seat == "seat-D"
    assert top2[0].mean_ms == 210.0
    assert top2[1].receiver_seat == "seat-C"
    assert top2[1].mean_ms == 150.0


def test_cristian_clock_skew_algorithm_edge_cases():
    """Verify clock offset precision with symmetric, forward-skewed, reverse-skewed and zero-latency packets."""
    calculate_cristian_skew = _seam().calculate_cristian_skew

    # Case 1: Zero skew, zero processing
    s1, r1 = calculate_cristian_skew(100_000, 105_000, 105_000, 110_000)
    assert s1 == 0.0
    assert round(r1, 4) == 0.010  # 10,000 ns = 0.01 ms

    # Case 2: Negative skew (server clock behind client clock)
    # T0: 100ms, T1: 85ms (-20ms skew + 5ms travel), T2: 85ms, T3: 110ms
    s2, r2 = calculate_cristian_skew(100_000_000, 85_000_000, 85_000_000, 110_000_000)
    assert round(s2, 2) == -20.0
    assert round(r2, 2) == 10.0


def test_clock_skew_monitor_and_anomaly_detection():
    """Verify pairwise skew tracking, anomaly detection, and severity levels."""
    ClockSkewMonitor = _seam().ClockSkewMonitor

    skew_monitor = ClockSkewMonitor(warn_skew_threshold_ms=25.0, crit_skew_threshold_ms=75.0)

    # Record normal skew (5ms)
    skew_monitor.record_skew_sample("seat-1", "seat-2", offset_ms=5.0, rtt_ms=10.0)
    anomalies = skew_monitor.detect_anomalies()
    assert len(anomalies) == 0

    # Record warning-level skew (35ms)
    skew_monitor.record_skew_sample("seat-1", "seat-3", offset_ms=35.0, rtt_ms=12.0)
    anomalies = skew_monitor.detect_anomalies()
    assert len(anomalies) == 1
    assert anomalies[0].severity == "WARNING"
    assert anomalies[0].seat_b == "seat-3"
    assert anomalies[0].skew_ms == 35.0

    # Record critical-level skew (90ms)
    skew_monitor.record_skew_sample("seat-1", "seat-4", offset_ms=-90.0, rtt_ms=15.0)
    anomalies = skew_monitor.detect_anomalies()
    assert len(anomalies) == 2
    crit = [a for a in anomalies if a.severity == "CRITICAL"]
    assert len(crit) == 1
    assert crit[0].seat_b == "seat-4"
    assert crit[0].skew_ms == -90.0


def test_cluster_max_clock_divergence():
    """Verify calculation of max clock divergence across the entire seat cluster."""
    ClockSkewMonitor = _seam().ClockSkewMonitor

    skew_monitor = ClockSkewMonitor()
    skew_monitor.record_skew_sample("ref-seat", "seat-fast", offset_ms=40.0, rtt_ms=5.0)
    skew_monitor.record_skew_sample("ref-seat", "seat-slow", offset_ms=-30.0, rtt_ms=5.0)

    # Max divergence between fastest (+40) and slowest (-30) relative to ref-seat is 70ms
    divergence = skew_monitor.get_max_cluster_clock_divergence()
    assert round(divergence, 2) == 70.0


def test_unified_ipc_monitor_telemetry_export():
    """Verify the unified InterSeatIPCMonitor full lifecycle, telemetry export, and reset."""
    InterSeatIPCMonitor = _seam().InterSeatIPCMonitor

    monitor = InterSeatIPCMonitor()
    monitor.register_seat("g16", role="worker", host="hub-mesh01", pid=101)
    monitor.register_seat("pm-B", role="pm", host="hub-mesh01", pid=202)

    monitor.record_ping(
        sender_seat="g16",
        receiver_seat="pm-B",
        t0_ns=1_000_000,
        t1_ns=2_000_000,
        t2_ns=2_100_000,
        t3_ns=3_200_000,
    )

    telemetry = monitor.export_telemetry()
    assert "seats" in telemetry
    assert "latency_matrix" in telemetry
    assert "clock_skew" in telemetry
    assert "anomalies" in telemetry
    assert len(telemetry["seats"]) == 2

    # Export to valid JSON
    json_output = monitor.export_telemetry_json()
    parsed = json.loads(json_output)
    assert parsed["seats"]["g16"]["role"] == "worker"

    # Reset
    monitor.reset()
    assert len(monitor.get_seats()) == 0
    assert len(monitor.export_telemetry()["seats"]) == 0


def test_service_mesh_ipc_sample_integration():
    """The wired path: service_mesh.record_service_mesh_ipc_sample -> ipc_latency.

    At base, service_mesh exists but the IPC recording function does not.
    _seam() ensures the module is present first; then the call through
    service_mesh exercises the integration.
    """
    _seam()
    from bulk_downloader import service_mesh

    res = service_mesh.record_service_mesh_ipc_sample(
        "g16", "pm-B", 100_000_000, 112_000_000, 115_000_000, 125_000_000)
    assert isinstance(res, dict), f"expected dict, got {type(res)}"
    assert round(res["network_rtt_ms"], 2) == 22.0, res
    assert round(res["clock_offset_ms"], 2) == 1.0, res

