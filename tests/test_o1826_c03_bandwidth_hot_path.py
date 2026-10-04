"""O1826 C03: bandwidth aggregator hot path (H05) and bps helper (M032).

RED on base: every record_throughput rebuilds the node's whole sample list in
_prune_samples_locked, so one record against 10k in-window samples reads 10k
timestamps. GREEN: pruning pops expired samples from the left of a time-ordered
deque and reads only expired + 1. The value tests pin base bps semantics and
must pass on base and cut alike (negative control for the refactor).
"""
from __future__ import annotations

import pytest

from bulk_downloader import bandwidth_aggregator as ba
from bulk_downloader.bandwidth_shape import TokenBucket

BD_GATE_SCOPE = "module"

N_IN_WINDOW = 10_000
N_EXPIRED = 5


class _CountingSample(ba.NodeThroughputSample):
    """Sample whose timestamp reads are counted (one read = one sample examined)."""

    reads = 0

    @property
    def timestamp(self) -> float:
        _CountingSample.reads += 1
        return self._ts

    @timestamp.setter
    def timestamp(self, value: float) -> None:
        self._ts = value


@pytest.fixture
def counting(monkeypatch):
    monkeypatch.setattr(ba, "NodeThroughputSample", _CountingSample)
    _CountingSample.reads = 0
    return _CountingSample


def _fill(agg: ba.BandwidthAggregator) -> None:
    for i in range(N_IN_WINDOW):
        agg.record_throughput("node-1", "site-a", 100, now=i * 0.001)


def test_record_examines_expired_plus_one_not_whole_window(counting):
    agg = ba.BandwidthAggregator(window_seconds=100.0)
    _fill(agg)
    counting.reads = 0
    # cutoff = 0.0045 -> samples at 0.000..0.004 expire
    agg.record_throughput("node-1", "site-a", 100, now=100.0045)
    assert counting.reads <= N_EXPIRED + 4, (
        f"H05: one record_throughput examined {counting.reads} samples "
        f"(window holds {N_IN_WINDOW}); expected expired+1 ({N_EXPIRED + 1}) plus O(1)"
    )
    assert agg.get_node_stats("node-1", now=100.0045)["samples_in_window"] == N_IN_WINDOW - N_EXPIRED + 1


def test_token_bucket_hot_path_reaches_amortised_prune(counting, monkeypatch):
    agg = ba.BandwidthAggregator(window_seconds=100.0)
    _fill(agg)
    counting.reads = 0
    monkeypatch.setattr(ba.time, "monotonic", lambda: 100.0045)
    bucket = TokenBucket(capacity=1000, refill_rate=1000.0, node_id="node-1", aggregator=agg)
    assert bucket.try_acquire(100) is True
    assert counting.reads <= N_EXPIRED + 4, (
        f"H05: TokenBucket.try_acquire examined {counting.reads} samples on the hot path"
    )


def _three_samples(agg: ba.BandwidthAggregator, node: str) -> None:
    agg.record_throughput(node, "site-a", 1000, duration_seconds=1.0, now=0.0)
    agg.record_throughput(node, "site-a", 3000, duration_seconds=2.0, now=2.0)
    agg.record_throughput(node, "site-a", 6000, duration_seconds=1.0, now=5.0)


def test_bps_values_match_base_semantics():
    agg = ba.BandwidthAggregator(window_seconds=10.0, cluster_capacity_bps=10_000.0)
    _three_samples(agg, "node-1")
    # all in window: 10000 B / max(2, min(10, 8 - 0)) = 1250
    assert agg.get_active_throughput_bps("node-1", now=8.0) == 1250.0
    # unpruned admission/reserve paths at t=12 still see t=0: 10000 / 10
    assert agg.check_admission(0.0, now=12.0).headroom_bps == 9000.0
    assert agg.reserve_capacity("node-1", 9000.0, now=12.0) is not None
    assert agg.reserve_capacity("node-1", 1.0, now=12.0) is None
    # pruned getters at t=12 drop t=0: 9000 / max(2, min(10, 12 - 2)) = 900
    stats = agg.get_node_stats("node-1", now=12.0)
    assert stats["current_bps"] == 900.0
    assert stats["samples_in_window"] == 2
    assert stats["total_bytes"] == 10000
    assert agg.get_active_throughput_bps("node-1", now=12.0) == 900.0
    cluster = agg.get_cluster_stats(now=12.0)
    assert cluster["nodes"]["node-1"]["current_bps"] == 900.0
    assert cluster["total_throughput_bps"] == 900.0


def test_out_of_order_sample_is_pruned_like_base():
    agg = ba.BandwidthAggregator(window_seconds=10.0)
    agg.record_sample_at("node-1", "site-a", 500, timestamp=5.0)
    agg.record_sample_at("node-1", "site-a", 700, timestamp=1.0)
    agg.record_sample_at("node-1", "site-a", 300, timestamp=6.0)
    # cutoff 2.0: the late-arriving t=1 sample expires, t=5 and t=6 stay
    stats = agg.get_node_stats("node-1", now=12.0)
    assert stats["samples_in_window"] == 2
    assert stats["current_bps"] == 800.0 / 7.0
    assert agg.get_node_stats("ghost") is None
    assert agg.get_active_throughput_bps("ghost", now=12.0) == 0.0
