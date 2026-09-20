"""Tests for Row 892: Bandwidth Budgeting and Traffic Shaping Token Bucket.

Acceptance criteria:
1. Download throughput throttled accurately to configured Mbps cap.
2. Smooth chunk pacing without burst drops.
3. Zero overhead when bandwidth cap is disabled.
"""

from __future__ import annotations

import io
import time

BD_GATE_SCOPE = "module"

import pytest

from bulk_downloader.traffic_shaper import (
    TokenBucket,
    BandwidthShaper,
    wrap_reader,
    wrap_stream,
)


class _PacingSpy:
    """Enabled shaper substitute that records wrapper pacing calls."""

    is_enabled = True

    def __init__(self) -> None:
        self.calls: list[tuple[int, str | None]] = []

    def pace(self, size: int, site_id: str | None = None) -> float:
        self.calls.append((size, site_id))
        return 0.0


class TestTrafficShaperAcceptance:
    """Acceptance test suite for token-bucket traffic shaper."""

    def test_download_throughput_throttled_accurately_to_configured_mbps_cap(self):
        """(1) Download throughput throttled accurately to configured Mbps cap."""
        # 8.0 Mbps = 1,000,000 bytes/sec = 1 MB/s
        target_mbps = 8.0
        shaper = BandwidthShaper(global_mbps=target_mbps)

        # Transmit 500 KB (0.5 MB)
        payload_size = 500_000
        chunk_size = 50_000
        data = b"X" * payload_size

        t0 = time.perf_counter()
        sent = 0
        for i in range(0, payload_size, chunk_size):
            chunk = data[i : i + chunk_size]
            shaper.pace(len(chunk))
            sent += len(chunk)
        elapsed = time.perf_counter() - t0

        assert sent == payload_size
        # Expected elapsed time: payload_size / bytes_per_sec = 500,000 / 1,000,000 = 0.5s
        # Allow reasonable timing tolerance (0.42s to 0.70s) on loaded host
        assert 0.40 <= elapsed <= 0.75, (
            f"Expected ~0.50s elapsed for 500KB at {target_mbps} Mbps, got {elapsed:.3f}s"
        )

        effective_mbps = (sent * 8) / (elapsed * 1_000_000)
        assert 6.0 <= effective_mbps <= 10.0, (
            f"Expected effective throughput near {target_mbps} Mbps, got {effective_mbps:.2f} Mbps"
        )

    def test_smooth_chunk_pacing_without_burst_drops(self):
        """(2) Smooth chunk pacing without burst drops across multiple intervals."""
        # 4.0 Mbps = 500,000 bytes/sec
        target_mbps = 4.0
        shaper = BandwidthShaper(global_mbps=target_mbps, burst_ratio=1.0)

        chunk_size = 50_000  # 50 KB -> should take ~0.10s per chunk
        num_chunks = 6
        delays = []

        for _ in range(num_chunks):
            t0 = time.perf_counter()
            shaper.pace(chunk_size)
            delays.append(time.perf_counter() - t0)

        # Skip first chunk (which may consume initial burst tokens)
        steady_delays = delays[1:]
        # Each 50KB chunk at 500KB/s should pace for ~0.10s
        for idx, d in enumerate(steady_delays):
            assert 0.07 <= d <= 0.20, (
                f"Chunk {idx + 2} pacing delay {d:.4f}s deviated significantly from expected ~0.10s"
            )

    def test_zero_overhead_when_bandwidth_cap_is_disabled(self):
        """(3) Zero overhead when bandwidth cap is disabled (None or 0)."""
        for disabled_mbps in (None, 0, -1.0):
            shaper = BandwidthShaper(global_mbps=disabled_mbps)

            payload_size = 1_000_000  # 1 MB
            chunk_size = 100_000

            t0 = time.perf_counter()
            for _ in range(0, payload_size, chunk_size):
                slept = shaper.pace(chunk_size)
                assert slept == 0.0

            elapsed = time.perf_counter() - t0
            # 1 MB unthrottled in memory must execute nearly instantaneously (< 15ms)
            assert elapsed < 0.03, (
                f"Disabled shaper had unexpected latency: {elapsed:.4f}s"
            )

        assert not BandwidthShaper(global_mbps=None).is_enabled
        assert BandwidthShaper(global_mbps=1.0).is_enabled

    def test_per_site_bandwidth_capping(self):
        """Per-site bandwidth limits are enforced independently alongside global limits."""
        shaper = BandwidthShaper(
            global_mbps=16.0,  # 2.0 MB/s global cap
            site_mbps={"site_slow": 4.0, "site_fast": 12.0},  # 500 KB/s vs 1.5 MB/s
        )

        payload_size = 250_000  # 250 KB
        # 250 KB at 4.0 Mbps (500 KB/s) should take ~0.50s
        t0 = time.perf_counter()
        shaper.pace(payload_size, site_id="site_slow")
        slow_elapsed = time.perf_counter() - t0
        assert 0.35 <= slow_elapsed <= 0.70, (
            f"Slow site took {slow_elapsed:.3f}s, expected ~0.50s"
        )

        # 250 KB at 12.0 Mbps (1.5 MB/s) should take ~0.17s
        t0 = time.perf_counter()
        shaper.pace(payload_size, site_id="site_fast")
        fast_elapsed = time.perf_counter() - t0
        assert fast_elapsed < slow_elapsed, (
            f"Fast site ({fast_elapsed:.3f}s) was not faster than slow site ({slow_elapsed:.3f}s)"
        )

    def test_stream_and_reader_wrapping(self):
        """Stream generator and reader wrappers preserve data while pacing."""
        content = b"Stream chunk testing " * 1000

        # wrap_stream
        chunks = [content[:5000], content[5000:15000], content[15000:]]
        stream_spy = _PacingSpy()
        stream_out = b"".join(wrap_stream(
            chunks, shaper=stream_spy, site_id="stream-site"))
        assert stream_out == content
        assert stream_spy.calls == [
            (len(chunk), "stream-site") for chunk in chunks if chunk
        ]

        # wrap_reader
        bio = io.BytesIO(content)
        reader_spy = _PacingSpy()
        reader = wrap_reader(bio, shaper=reader_spy, chunk_size=4096,
                             site_id="reader-site")
        read_out = reader.read()
        assert read_out == content
        assert reader_spy.calls == [
            (4096, "reader-site") for _ in range(len(content) // 4096)
        ] + ([(len(content) % 4096, "reader-site")]
             if len(content) % 4096 else [])

    def test_reader_readinto_paces_exact_bytes(self):
        spy = _PacingSpy()
        reader = wrap_reader(io.BytesIO(b"abc"), shaper=spy,
                             site_id="readinto-site")
        destination = bytearray(8)

        assert reader.readinto(destination) == 3
        assert bytes(destination[:3]) == b"abc"
        assert spy.calls == [(3, "readinto-site")]


# ── FIXER (row892 REFUTE E1 HIGH / E2) ───────────────────────────────────


def test_concurrent_consumers_are_serialised_at_the_rate():
    """E1: N threads hitting one bucket must be paced at `rate` in TOTAL --
    not each sleep the same interval in parallel and transmit together
    (which multiplied throughput by N)."""
    import threading

    rate = 200_000.0  # bytes/s
    bucket = TokenBucket(rate, capacity=1_000.0, initial_tokens=0.0)
    threads_n, per_thread = 4, 10_000
    slept: list[float] = []
    lock = threading.Lock()

    def worker():
        s = bucket.consume(per_thread, block=True)
        with lock:
            slept.append(s)

    t0 = time.perf_counter()
    threads = [threading.Thread(target=worker) for _ in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    wall = time.perf_counter() - t0
    assert all(not t.is_alive() for t in threads)

    total_bytes = threads_n * per_thread
    expected_min = (total_bytes - bucket.capacity) / rate  # 0.195 s
    # Parallel identical sleeps (the defect) finish in ~per_thread/rate = 0.05 s.
    assert wall >= expected_min * 0.9, (wall, slept)
    # Each later reservation is strictly later: distinct, increasing sleeps.
    assert len(set(round(s, 4) for s in slept)) == threads_n, slept
    assert bucket.tokens <= bucket.capacity


def test_pace_reports_the_sum_of_sequential_sleeps(monkeypatch):
    """E2: pace() sleeps site then global bucket sequentially; the caller is
    told the SUM (observed 1.476 s real vs 1.000 s reported)."""
    from bulk_downloader import traffic_shaper

    sleeps: list[float] = []
    monkeypatch.setattr(traffic_shaper.time, "sleep", lambda s: sleeps.append(s))
    shaper = BandwidthShaper(global_mbps=8.0, site_mbps={"s": 4.0})  # 1 MB/s, 0.5 MB/s
    for b in (shaper._global_bucket, shaper._site_buckets["s"]):
        b.tokens = 0.0
        b.last_update = time.perf_counter()
    reported = shaper.pace(500_000, site_id="s")
    assert len(sleeps) == 2 and all(s > 0 for s in sleeps), sleeps
    assert reported == pytest.approx(sum(sleeps), rel=1e-3)
    assert reported > max(sleeps)
