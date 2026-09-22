"""Row 1055: Distributed Ingestion Throughput Capacity & Bandwidth Aggregator.

Validates cluster-wide ingestion bandwidth aggregation, sliding-window throughput
calculation, per-node telemetry tracking, capacity headroom computation, three-state
admission control reservation (O1224), and active caller integration in bandwidth_shape.py.

RED on baseline: Base lacks bulk_downloader.bandwidth_aggregator and cluster throughput
accounting in bandwidth_shape.py.
"""
from __future__ import annotations

import io
import threading
import time

import pytest

BD_GATE_SCOPE = "module"


def _get_aggregator_mod():
    try:
        from bulk_downloader import bandwidth_aggregator
        return bandwidth_aggregator
    except ImportError:
        return None


def test_module_exports():
    """RED assertion 1: Base must provide distributed bandwidth aggregator."""
    agg = _get_aggregator_mod()
    assert agg is not None, (
        "Base lacks distributed ingestion throughput capacity and bandwidth aggregator (bulk_downloader.bandwidth_aggregator)"
    )

    assert hasattr(agg, "AdmissionState")
    assert hasattr(agg, "AdmissionResult")
    assert hasattr(agg, "NodeThroughputSample")
    assert hasattr(agg, "IngestionCapacityProfile")
    assert hasattr(agg, "BandwidthAggregator")
    assert hasattr(agg, "create_bandwidth_aggregator")
    assert hasattr(agg, "get_bandwidth_aggregator")


def test_record_throughput_and_rates():
    """Verify single-node sample recording and throughput rate calculation."""
    agg_mod = _get_aggregator_mod()
    assert agg_mod is not None, "Base lacks bulk_downloader.bandwidth_aggregator"

    aggregator = agg_mod.BandwidthAggregator(window_seconds=10.0, cluster_capacity_bps=10_000_000.0)
    aggregator.record_throughput(node_id="node-1", site_id="site-a", bytes_ingested=5_000_000, duration_seconds=2.0)

    stats = aggregator.get_node_stats("node-1")
    assert stats is not None
    assert stats["node_id"] == "node-1"
    assert stats["total_bytes"] == 5_000_000
    assert stats["current_bps"] == 2_500_000.0


def test_sliding_window_aggregation():
    """Verify samples outside the sliding window are pruned from rate calculation."""
    agg_mod = _get_aggregator_mod()
    assert agg_mod is not None, "Base lacks bulk_downloader.bandwidth_aggregator"

    aggregator = agg_mod.BandwidthAggregator(window_seconds=1.0, cluster_capacity_bps=10_000_000.0)
    now = time.monotonic()

    # Record sample at now - 2.0s (outside window)
    aggregator.record_sample_at(node_id="node-1", site_id="site-a", bytes_ingested=1_000_000, timestamp=now - 2.0)
    # Record sample at now (inside window)
    aggregator.record_sample_at(node_id="node-1", site_id="site-a", bytes_ingested=2_000_000, timestamp=now)

    active_bps = aggregator.get_active_throughput_bps("node-1", now=now)
    assert active_bps == 2_000_000.0


def test_multi_node_cluster_aggregation():
    """Verify multi-node throughput aggregation across federated cluster."""
    agg_mod = _get_aggregator_mod()
    assert agg_mod is not None, "Base lacks bulk_downloader.bandwidth_aggregator"

    aggregator = agg_mod.BandwidthAggregator(window_seconds=5.0, cluster_capacity_bps=50_000_000.0)
    aggregator.record_throughput(node_id="node-1", site_id="site-a", bytes_ingested=10_000_000, duration_seconds=2.0)
    aggregator.record_throughput(node_id="node-2", site_id="site-b", bytes_ingested=6_000_000, duration_seconds=2.0)

    cluster_stats = aggregator.get_cluster_stats()
    assert cluster_stats["active_nodes"] == 2
    assert cluster_stats["total_throughput_bps"] == 8_000_000.0
    assert cluster_stats["capacity_bps"] == 50_000_000.0
    assert cluster_stats["utilization_ratio"] == pytest.approx(0.16)
    assert cluster_stats["headroom_bps"] == 42_000_000.0


def test_three_state_admission_control_and_reservation():
    """Verify admission check against cluster bandwidth headroom with O1224 three states."""
    agg_mod = _get_aggregator_mod()
    assert agg_mod is not None, "Base lacks bulk_downloader.bandwidth_aggregator"

    aggregator = agg_mod.BandwidthAggregator(window_seconds=5.0, cluster_capacity_bps=10_000_000.0)
    aggregator.record_throughput(node_id="node-1", site_id="site-a", bytes_ingested=16_000_000, duration_seconds=2.0)

    # 1.5 MB/s should be admitted
    res = aggregator.check_admission(requested_bps=1_500_000.0)
    assert res.state == agg_mod.AdmissionState.ADMITTED
    assert res.is_admitted is True
    assert aggregator.can_admit(requested_bps=1_500_000.0) is True

    # 3.0 MB/s should be refused
    res_refused = aggregator.check_admission(requested_bps=3_000_000.0)
    assert res_refused.state == agg_mod.AdmissionState.REJECTED
    assert res_refused.is_admitted is False
    assert aggregator.can_admit(requested_bps=3_000_000.0) is False

    # Reserve 1.5 MB/s
    lease_id = aggregator.reserve_capacity(node_id="node-2", requested_bps=1_500_000.0, ttl_seconds=5.0)
    assert lease_id is not None

    # After reservation, only 0.5 MB/s left -> 1.0 MB/s refused
    assert aggregator.can_admit(requested_bps=1_000_000.0) is False

    # Release reservation
    aggregator.release_reservation(lease_id)
    assert aggregator.can_admit(requested_bps=1_000_000.0) is True


def test_unverifiable_admission_fails_closed_per_o1224():
    """Verify unmeasurable / invalid capacity fails closed to UNVERIFIABLE (never fails open)."""
    agg_mod = _get_aggregator_mod()
    assert agg_mod is not None, "Base lacks bulk_downloader.bandwidth_aggregator"

    # Unconfigured / non-positive cluster capacity
    aggregator = agg_mod.BandwidthAggregator(window_seconds=5.0, cluster_capacity_bps=0.0)
    res = aggregator.check_admission(requested_bps=1000.0)
    assert res.state == agg_mod.AdmissionState.UNVERIFIABLE
    assert res.is_admitted is False

    # Negative request
    res_neg = aggregator.check_admission(requested_bps=-500.0)
    assert res_neg.state == agg_mod.AdmissionState.UNVERIFIABLE
    assert res_neg.is_admitted is False

    # Reservation refuses when unverifiable
    lease = aggregator.reserve_capacity(node_id="node-x", requested_bps=1000.0)
    assert lease is None


def test_stale_node_pruning():
    """Verify inactive nodes are detected and pruned from active cluster capacity."""
    agg_mod = _get_aggregator_mod()
    assert agg_mod is not None, "Base lacks bulk_downloader.bandwidth_aggregator"

    aggregator = agg_mod.BandwidthAggregator(window_seconds=2.0, cluster_capacity_bps=10_000_000.0, node_ttl_seconds=3.0)
    now = time.monotonic()

    aggregator.record_sample_at(node_id="node-1", site_id="site-a", bytes_ingested=1_000_000, timestamp=now)
    aggregator.record_sample_at(node_id="node-2", site_id="site-b", bytes_ingested=1_000_000, timestamp=now - 5.0)

    aggregator.prune_stale_nodes(now=now)
    cluster_stats = aggregator.get_cluster_stats(now=now)
    assert cluster_stats["active_nodes"] == 1
    assert "node-1" in cluster_stats["nodes"]
    assert "node-2" not in cluster_stats["nodes"]


def test_thread_safety():
    """Verify concurrent throughput recording is thread-safe without race conditions."""
    agg_mod = _get_aggregator_mod()
    assert agg_mod is not None, "Base lacks bulk_downloader.bandwidth_aggregator"

    aggregator = agg_mod.BandwidthAggregator(window_seconds=5.0, cluster_capacity_bps=100_000_000.0)
    num_threads = 8
    samples_per_thread = 50

    def worker(worker_id: int):
        for _ in range(samples_per_thread):
            aggregator.record_throughput(
                node_id=f"node-{worker_id % 3}",
                site_id="site-x",
                bytes_ingested=10_000,
                duration_seconds=0.1,
            )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    cluster_stats = aggregator.get_cluster_stats()
    assert cluster_stats["total_samples"] == num_threads * samples_per_thread


def test_caller_token_bucket_acquire_records_throughput():
    """Verify real caller TokenBucket.acquire in bandwidth_shape records into aggregator."""
    from bulk_downloader import bandwidth_shape

    site = f"site-acquire-{int(time.time() * 1000)}"
    # Call get_bucket with base signature (site_id, max_bytes_per_sec)
    bucket = bandwidth_shape.get_bucket(site, max_bytes_per_sec=1_000_000)
    assert bucket.acquire(42_000) is True

    # Base lacks cluster aggregator and throughput accounting
    assert hasattr(bandwidth_shape, "get_cluster_bandwidth_aggregator"), (
        "Base bandwidth_shape lacks cluster bandwidth aggregator integration"
    )
    agg = bandwidth_shape.get_cluster_bandwidth_aggregator()
    node_stats = agg.get_node_stats("local")
    assert node_stats is not None
    assert node_stats["total_bytes"] >= 42_000

    snap = bucket.snapshot()
    assert "site_id" in snap and snap["site_id"] == site, "Base TokenBucket lacks site telemetry"


def test_caller_token_bucket_try_acquire_records_throughput():
    """Verify real caller TokenBucket.try_acquire records into aggregator."""
    from bulk_downloader import bandwidth_shape

    site = f"site-try-{int(time.time() * 1000)}"
    # Call get_bucket with base signature (site_id, max_bytes_per_sec)
    bucket = bandwidth_shape.get_bucket(site, max_bytes_per_sec=2_000_000)
    assert bucket.try_acquire(15_000) is True

    snap = bucket.snapshot()
    assert "node_id" in snap, "Base TokenBucket.snapshot lacks cluster node telemetry"
    assert hasattr(bandwidth_shape, "get_cluster_bandwidth_aggregator"), (
        "Base bandwidth_shape lacks get_cluster_bandwidth_aggregator"
    )
    agg = bandwidth_shape.get_cluster_bandwidth_aggregator()
    node_stats = agg.get_node_stats(snap["node_id"])
    assert node_stats is not None
    assert node_stats["total_bytes"] >= 15_000


def test_caller_throttled_reader_throughput_accounting():
    """Verify ThrottledReader stream consumption propagates to cluster aggregator."""
    from bulk_downloader import bandwidth_shape

    site = f"site-reader-{int(time.time() * 1000)}"
    bucket = bandwidth_shape.get_bucket(site, max_bytes_per_sec=100_000)
    stream_data = b"A" * 8000
    reader = bandwidth_shape.ThrottledReader(io.BytesIO(stream_data), bucket)

    data = reader.read(8000)
    assert len(data) == 8000
    assert reader.bytes_read == 8000

    assert hasattr(bandwidth_shape, "cluster_bandwidth_snapshot"), (
        "Base bandwidth_shape lacks cluster_bandwidth_snapshot telemetry"
    )
    cluster_stats = bandwidth_shape.cluster_bandwidth_snapshot()
    assert "nodes" in cluster_stats
    assert cluster_stats["nodes"]["local"]["total_bytes"] >= 8000


def test_caller_bandwidth_shape_admission_and_cluster_snapshot():
    """Verify admission check and cluster snapshot functions on bandwidth_shape module."""
    from bulk_downloader import bandwidth_shape

    assert hasattr(bandwidth_shape, "check_admission"), (
        "Base bandwidth_shape lacks three-state check_admission gate"
    )
    res = bandwidth_shape.check_admission(5000.0)
    assert res.is_admitted is True
    assert hasattr(bandwidth_shape, "can_admit")
    assert isinstance(bandwidth_shape.can_admit(5000.0), bool)

    snap = bandwidth_shape.cluster_bandwidth_snapshot()
    assert "active_nodes" in snap
    assert "capacity_bps" in snap

    lease = bandwidth_shape.reserve_bandwidth(10_000.0, ttl_seconds=2.0)
    if lease is not None:
        assert bandwidth_shape.release_bandwidth(lease) is True
