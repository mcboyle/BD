"""Row 989: Inter-Seat IPC Latency Matrix Monitor.

Validates inter-seat IPC latency tracking, pairwise RTT matrix computation,
percentile aggregation, LRU pair eviction, degraded link detection, and
explicit NOT-MEASURED state for unmeasurable clock skew per ORDERS-0745.

RED provenance: at base, _seam() converts the missing module into a pytest.fail
assertion from the test body (not a collection error), stating the capability
that is absent in the row's own terms.
"""
from __future__ import annotations

import json

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
                f"{exc.name!r} is not installed ({exc})"
            )
        pytest.fail(
            f"row989: the inter-seat IPC latency monitoring capability is "
            f"absent ({exc}). bulk_downloader/ipc_latency.py must expose "
            f"InterSeatIPCMonitor, IPCLatencyMatrix, and record_ipc_ping."
        )
    return seam


def test_module_exports():
    """The module must export all named latency components."""
    ipc_latency = _seam()

    assert hasattr(ipc_latency, "SeatNode")
    assert hasattr(ipc_latency, "IPCPingSample")
    assert hasattr(ipc_latency, "LatencySummary")
    assert hasattr(ipc_latency, "IPCLatencyMatrix")
    assert hasattr(ipc_latency, "ClockSkewMonitor")
    assert hasattr(ipc_latency, "InterSeatIPCMonitor")
    assert hasattr(ipc_latency, "record_ipc_ping")
    assert hasattr(ipc_latency, "MAX_TRACKED_PAIRS")


def test_seat_node_registration_and_validation():
    """Verify seat registration, validation, and seat inventory retrieval."""
    ipc = _seam()
    InterSeatIPCMonitor, SeatNode = ipc.InterSeatIPCMonitor, ipc.SeatNode

    monitor = InterSeatIPCMonitor()
    node1 = monitor.register_seat("bd-agy-worker-g16", role="worker", host="hub-mesh01", pid=896526)
    node2 = monitor.register_seat("bd-pm-Opus-B", role="pm", host="hub-mesh01", pid=12345)

    assert isinstance(node1, SeatNode)
    assert isinstance(node2, SeatNode)
    assert node1.seat_id == "bd-agy-worker-g16"
    assert node2.seat_id == "bd-pm-Opus-B"
    assert node1.role == "worker"
    assert node2.role == "pm"
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
    """Verify RTT calculation from raw client timestamps and explicit None for skew."""
    ipc = _seam()
    IPCPingSample = ipc.IPCPingSample

    t0_ns = 100_000_000  # 100 ms
    t3_ns = 125_000_000  # 125 ms (25 ms round-trip duration)

    sample = IPCPingSample(
        sender_seat="bd-agy-worker-g16",
        receiver_seat="bd-pm-Opus-B",
        t0_ns=t0_ns,
        t3_ns=t3_ns,
    )
    assert round(sample.rtt_ms, 2) == 25.0
    assert round(sample.total_duration_ms, 2) == 25.0

    # ORDERS-0745: skew and server processing are unmeasured, returning None (never 0.0)
    assert sample.clock_offset_ms is None
    assert sample.server_processing_ms is None

    # Serialization omits unmeasured Cristian fields
    d = sample.to_dict()
    assert d["rtt_ms"] == 25.0
    assert "clock_offset_ms" not in d
    assert "server_processing_ms" not in d


def test_ipc_latency_matrix_accumulation_and_percentiles():
    """Verify matrix aggregation, percentile statistics (min, max, mean, p50, p95, p99), and jitter."""
    IPCLatencyMatrix = _seam().IPCLatencyMatrix

    matrix = IPCLatencyMatrix()
    latencies = [10.0, 12.0, 14.0, 16.0, 20.0, 100.0]
    for lat in latencies:
        matrix.record_rtt("bd-agy-worker-g16", "bd-worker-B1", rtt_ms=lat)

    summary = matrix.get_pair_summary("bd-agy-worker-g16", "bd-worker-B1")
    assert summary is not None
    assert summary.sample_count == 6
    assert summary.min_ms == 10.0
    assert summary.max_ms == 100.0
    assert round(summary.mean_ms, 2) == round(sum(latencies) / 6, 2)
    assert summary.p50_ms == 15.0
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


def test_clock_skew_explicit_not_measured():
    """ORDERS-0745: ClockSkewMonitor must report explicit NOT-MEASURED state (never 0.0)."""
    ClockSkewMonitor = _seam().ClockSkewMonitor

    skew_monitor = ClockSkewMonitor()
    skew_monitor.record_skew_sample("seat-1", "seat-2", 0.0, 10.0)

    # Must report None for divergence (unknown, not 0.0)
    assert skew_monitor.get_max_cluster_clock_divergence() is None
    # No false anomalies
    assert skew_monitor.detect_anomalies() == []


def test_unified_ipc_monitor_telemetry_export():
    """Verify InterSeatIPCMonitor full lifecycle, telemetry export, and reset."""
    InterSeatIPCMonitor = _seam().InterSeatIPCMonitor

    monitor = InterSeatIPCMonitor()
    monitor.register_seat("g16", role="worker", host="hub-mesh01", pid=101)
    monitor.register_seat("pm-B", role="pm", host="hub-mesh01", pid=202)

    monitor.record_ping(
        sender_seat="g16",
        receiver_seat="pm-B",
        t0_ns=1_000_000,
        t3_ns=3_200_000,
    )

    telemetry = monitor.export_telemetry()
    assert "seats" in telemetry
    assert "latency_matrix" in telemetry
    assert "highest_latency_pairs" in telemetry
    assert "degraded_links" in telemetry
    assert "clock_skew" in telemetry
    assert telemetry["clock_skew"]["max_cluster_divergence_ms"] is None
    assert telemetry["clock_skew"]["status"] == "NOT_MEASURED"
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
    """The wired path: service_mesh.record_service_mesh_ipc_sample -> ipc_latency."""
    _seam()
    from bulk_downloader import service_mesh

    res = service_mesh.record_service_mesh_ipc_sample(
        "g16", "pm-B", 100_000_000, 125_000_000
    )
    assert isinstance(res, dict), f"expected dict, got {type(res)}"
    assert round(res["rtt_ms"], 2) == 25.0, res
    assert round(res["total_duration_ms"], 2) == 25.0, res
    # Skew fields must NOT be advertised
    assert "clock_offset_ms" not in res, res
    assert "server_processing_ms" not in res, res


class TestPairCountBounding:
    """Distinct pair count must be capped with LRU eviction."""

    def test_latency_matrix_lru_eviction(self):
        from bulk_downloader.ipc_latency import IPCLatencyMatrix

        matrix = IPCLatencyMatrix(max_pairs=4)
        for i in range(4):
            matrix.record_rtt(f"s{i}", f"r{i}", 1.0)
        matrix.record_rtt("s0", "r0", 2.0)
        matrix.record_rtt("s4", "r4", 1.0)
        assert len(matrix.get_matrix()) == 4
        assert matrix.get_pair_summary("s0", "r0") is not None, (
            "s0/r0 was recently touched — LRU should keep it"
        )
        assert matrix.get_pair_summary("s1", "r1") is None, (
            "s1/r1 was the least recently used — LRU should evict it"
        )

    def test_max_tracked_pairs_is_1024(self):
        """ORDERS-0745: MAX_TRACKED_PAIRS stays 1024 for gateway star topology."""
        from bulk_downloader.ipc_latency import MAX_TRACKED_PAIRS

        assert MAX_TRACKED_PAIRS == 1024
