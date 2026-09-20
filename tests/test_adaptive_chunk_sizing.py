"""Tests for the AIMD adaptive chunk-size controller (row854)."""
import os

import pytest

BD_GATE_SCOPE = "module"

from bulk_downloader.chunked_transfer import (
    AIMDChunkController,
    MAX_CHUNK_BYTES,
    MIN_CHUNK_BYTES,
    split_into_chunks,
)


def test_scales_up_under_sustained_high_throughput():
    c = AIMDChunkController()
    start = c.chunk_bytes
    for _ in range(200):
        c.observe(throughput_bps=500_000_000, latency_ms=5.0)
    assert c.chunk_bytes > start
    assert c.chunk_bytes <= MAX_CHUNK_BYTES


def test_backs_off_within_one_observation_on_latency_jitter():
    c = AIMDChunkController()
    for _ in range(20):
        c.observe(throughput_bps=500_000_000, latency_ms=5.0)
    scaled = c.chunk_bytes
    assert scaled > MIN_CHUNK_BYTES

    # A single delayed-packet sample with >=50ms jitter must back off
    # immediately -- not after further samples.
    after = c.observe(throughput_bps=500_000_000, latency_ms=5.0 + 60.0)
    assert after < scaled


def test_bounds_are_enforced():
    c = AIMDChunkController()
    for _ in range(2000):
        c.observe(throughput_bps=500_000_000, latency_ms=5.0)
    assert c.chunk_bytes <= MAX_CHUNK_BYTES

    for _ in range(2000):
        c.observe(throughput_bps=500_000_000, latency_ms=5.0 + 60.0)
    assert c.chunk_bytes >= MIN_CHUNK_BYTES


def test_missing_metrics_preserve_current_behavior():
    c = AIMDChunkController()
    before = c.chunk_bytes
    result = c.observe(throughput_bps=None, latency_ms=None)
    assert result == before
    assert c.chunk_bytes == before


def test_assembled_bytes_identical_to_source_under_varying_chunk_sizes():
    data = os.urandom(5 * MIN_CHUNK_BYTES + 1234)
    c = AIMDChunkController()
    latencies = [5.0, 5.0, 70.0, 5.0, 5.0, 90.0, 5.0]

    def telemetry(i):
        return {"throughput_bps": 500_000_000, "latency_ms": latencies[i % len(latencies)]}

    chunks = split_into_chunks(data, c, telemetry)
    assert b"".join(chunks) == data


def test_split_into_chunks_feeds_telemetry_and_grows_chunk_sizes():
    data = os.urandom(8 * MIN_CHUNK_BYTES)
    c = AIMDChunkController()

    def telemetry(_i):
        return {"throughput_bps": 500_000_000, "latency_ms": 5.0}

    chunks = split_into_chunks(data, c, telemetry)
    assert len(chunks) > 1
    assert len(chunks[-2]) > len(chunks[0])


def test_split_into_chunks_respects_bounds_per_chunk():
    data = os.urandom(3 * MAX_CHUNK_BYTES)
    c = AIMDChunkController()

    def telemetry(_i):
        return {"throughput_bps": 500_000_000, "latency_ms": 5.0}

    chunks = split_into_chunks(data, c, telemetry)
    for chunk in chunks[:-1]:
        assert MIN_CHUNK_BYTES <= len(chunk) <= MAX_CHUNK_BYTES


# ── FIXER (row854-A6 REFUTE E1) ──────────────────────────────────────────


@pytest.mark.parametrize("step", [0, -1, -2 * 1024 * 1024])
def test_non_positive_additive_step_is_rejected(step):
    """E1: a non-positive step could drive chunk_bytes below min_bytes (to 0)
    and make split_into_chunks() append empty chunks forever."""
    with pytest.raises(ValueError, match="additive_step"):
        AIMDChunkController(additive_step=step)


def test_chunk_bytes_never_leaves_bounds_and_split_always_advances():
    """Every observe() path clamps to [min_bytes, max_bytes]; with the bounds
    honoured, split_into_chunks() consumes its input in a bounded number of
    non-empty chunks (the E1 probe: b'abc' must yield exactly [b'abc'])."""
    c = AIMDChunkController(min_bytes=4, max_bytes=8, initial_bytes=4, additive_step=1)
    for i in range(50):
        size = c.observe(throughput_bps=1.0, latency_ms=float(i % 2) * 1000.0)
        assert 4 <= size <= 8
    chunks = split_into_chunks(b"abc", c, telemetry=lambda i: {"throughput_bps": 1.0})
    assert chunks == [b"abc"]
    assert all(chunks)
