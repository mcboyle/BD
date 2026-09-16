"""Row657: real bucket progress for transfer chunks larger than burst capacity."""
from types import SimpleNamespace

import pytest

from bulk_downloader import download_supervisor as ds

BD_GATE_SCOPE = "repo-wide"


class Clock:
    def __init__(self, deadline=20):
        self.now = 0.0
        self.deadline = deadline
        self.sleeps = []
        self.after_sleep = None

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        assert self.now <= self.deadline, (
            "acquire made no bounded progress after "
            f"{self.now}s of simulated refill; sleeps={len(self.sleeps)}"
        )
        if self.after_sleep:
            callback, self.after_sleep = self.after_sleep, None
            callback()


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(ds, "time", SimpleNamespace(
        monotonic=clock.monotonic, sleep=clock.sleep))
    return clock


@pytest.mark.parametrize("rate,factor,size,wait", [
    (100, 1.0, 50, 0.0),
    (100, 1.0, 100, 0.0),
    (100, 1.0, 101, 0.01),
    (100, 1.0, 200, 1.0),
    (100, 1.0, 450, 3.5),
    (10, 0.5, 100, 9.5),
    (8192, 0.5, 8192, 0.5),
])
def test_every_positive_chunk_finishes_at_its_configured_rate(clock, rate, factor, size, wait):
    bucket = ds.TokenBucket("row657", rate, capacity_factor=factor)
    assert bucket.acquire(size) == pytest.approx(wait)
    assert clock.now == pytest.approx(wait)
    stats = bucket.stats()
    assert stats["acquires"] == 1
    assert stats["bytes_passed"] == size
    assert stats["total_wait_s"] == pytest.approx(wait, abs=0.001)
    assert 0 <= stats["tokens_available"] <= stats["capacity"]


def test_hot_unlimited_rate_releases_an_inflight_acquire(clock):
    bucket = ds.TokenBucket("row657", 100)
    bucket.acquire(100)
    clock.after_sleep = lambda: bucket.set_rate(0)
    assert bucket.acquire(200) == 0.5
    assert bucket.stats()["bytes_passed"] == 300
    assert bucket.stats()["acquires"] == 2


def test_hot_lower_rate_reprices_only_the_unpaid_bytes(clock):
    bucket = ds.TokenBucket("row657", 100)
    clock.after_sleep = lambda: bucket.set_rate(50)
    waited = bucket.acquire(200)
    assert waited == pytest.approx(2.5)
    assert bucket.stats()["bytes_passed"] == 200
    assert bucket.stats()["acquires"] == 1


def test_consecutive_chunks_cannot_reuse_consumed_tokens(clock):
    bucket = ds.TokenBucket("row657", 100)
    assert bucket.acquire(200) == 1.0
    assert bucket.acquire(100) == 1.0
    assert bucket.stats()["bytes_passed"] == 300
    assert clock.now == 2.0


@pytest.mark.parametrize("rate,size", [(0, 8192), (100, 0), (100, -1)])
def test_unlimited_and_empty_transfers_never_sleep(clock, rate, size):
    assert ds.TokenBucket("row657", rate).acquire(size) == 0.0
    assert clock.sleeps == []


@pytest.mark.parametrize("global_rate,site_rate", [(100, 0), (0, 100), (100, 100)])
def test_global_and_site_supervisor_paths_make_progress(clock, global_rate, site_rate):
    state = ds._SupervisorState()
    state.configure(enabled=True, global_bps=global_rate, per_site_bps={"site": site_rate})
    waited = state.acquire("site", 200)
    assert 1 <= waited <= 2
    assert state.stats()["global"]["bytes_passed"] == 200
    assert state.stats()["per_site"]["site"]["bytes_passed"] == 200
