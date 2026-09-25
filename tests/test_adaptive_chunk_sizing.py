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


# Row 966: exercise the actual HTTP transfer, not only in-memory splitting.
class _SocketClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _adaptive_transfer(monkeypatch, tmp_path, *, adaptive=True, resume=False, cffi=False):
    import hashlib
    import threading
    from contextlib import contextmanager
    from types import SimpleNamespace
    from bulk_downloader import chunked_transfer, ssrf_transport, staging_claim
    from bulk_downloader.runner_transport import TransportMixin

    monkeypatch.delenv("BD_INSTALL_DIR", raising=False)
    monkeypatch.setenv("BD_DISABLE_KEEPALIVE", "1")
    monkeypatch.chdir(tmp_path)
    clock = _SocketClock()
    # Different bytes on either side of every chunk boundary expose duplication
    # and omissions even if total length happens to remain unchanged.
    block = bytes(range(251)) * 8356
    payload = b"".join(bytes([i]) + block for i in range(20)) + b"tail966"
    prefix = payload[:12345] if resume else b""
    remaining = payload[len(prefix):]
    requests, decisions, emitted = [], [], []
    real_chunks = getattr(chunked_transfer, "adaptive_chunks", None)
    if real_chunks is not None:
        def measured_chunks(*args):
            for buf in real_chunks(*args):
                emitted.append(len(buf))
                yield buf

        monkeypatch.setattr(chunked_transfer, "adaptive_chunks", measured_chunks)
    real_observe = chunked_transfer.AIMDChunkController.observe

    def measured_observe(controller, **sample):
        before = controller.chunk_bytes
        value = real_observe(controller, **sample)
        decisions.append((before, value, sample))
        return value

    monkeypatch.setattr(chunked_transfer.AIMDChunkController, "observe", measured_observe)
    # Clock is a module seam, not a replacement for the process-wide clock.
    monkeypatch.setattr(chunked_transfer, "monotonic", clock, raising=False)

    class Response:
        status_code = 206 if resume else 200
        headers = {"Content-Length": str(len(remaining)), "ETag": '"row966"'}
        if resume:  # RFC 9110: a 206 names the range it carries
            headers["Content-Range"] = (
                f"bytes {len(prefix)}-{len(payload) - 1}/{len(payload)}")

        def close(self):
            pass

        def iter_content(self, chunk_size=None):
            return self.iter_bytes(chunk_size)

        def iter_bytes(self, chunk_size=None):
            requests.append(chunk_size)
            for i, offset in enumerate(range(0, len(remaining), MIN_CHUNK_BYTES)):
                clock.now += 0.080 if i == 8 else 0.001
                yield remaining[offset:offset + MIN_CHUNK_BYTES]

    @contextmanager
    def stream(client, *args, **kwargs):
        try:
            yield Response()
        finally:
            client.close()

    monkeypatch.setattr(ssrf_transport, "owning_stream", stream)
    if cffi:
        import sys
        fake_requests = SimpleNamespace(request=lambda *args, **kwargs: Response())
        monkeypatch.setitem(sys.modules, "curl_cffi", SimpleNamespace(requests=fake_requests))

    class Runner(TransportMixin):
        def __init__(self):
            self.site_id = "row966"
            self.config = {"auto_chunk_size": adaptive, "use_curl_cffi": cffi,
                           "parallel_chunks": 1, "chunk_size_mb": 4}
            self._stop = threading.Event()
            self._pause = threading.Event()
            self._pause.set()
            self._throughput_samples = 0
            self._throughput_ewma_bps = 0.0
            self.log = SimpleNamespace(warning=lambda *a, **kw: None,
                                       info=lambda *a, **kw: None)

        def log_event(self, *args, **kwargs):
            pass

        def _update_job(self, *args, **kwargs):
            pass

    runner = Runner()
    dest = tmp_path / "row966.bin"
    url = "https://cdn.example.invalid/row966.bin"
    if prefix:
        part = staging_claim.claim(dest, staging_claim.job_identity(url))
        part.write_bytes(prefix)
    ctx = SimpleNamespace(cookies=lambda *args: [])
    size, fetched = runner._http_download(url, None, ctx, url, dest)
    assert size == len(payload) and fetched == len(remaining), "ADAPTIVE-BYTE-ACCOUNTING"
    assert hashlib.sha256(dest.read_bytes()).digest() == hashlib.sha256(payload).digest(), "ADAPTIVE-CHECKSUM"
    assert requests, "ADAPTIVE-NO-STREAM"
    return decisions, requests, emitted


@pytest.mark.parametrize("cffi", [False, True])
@pytest.mark.parametrize("resume", [False, True])
def test_core_transfer_adapts_and_preserves_checksum(monkeypatch, tmp_path, resume, cffi):
    decisions, requests, emitted = _adaptive_transfer(monkeypatch, tmp_path, resume=resume, cffi=cffi)
    assert decisions, "ADAPTIVE-CORE-NOT-WIRED"
    assert any(after > before for before, after, _ in decisions), "ADAPTIVE-NO-GROWTH"
    assert any(after < before for before, after, _ in decisions), "ADAPTIVE-NO-BACKOFF"
    assert all(MIN_CHUNK_BYTES <= after <= MAX_CHUNK_BYTES for _, after, _ in decisions)
    assert len(set(emitted[:-1])) > 1, "ADAPTIVE-OUTPUT-SIZES-STATIC"
    assert all(MIN_CHUNK_BYTES <= size <= MAX_CHUNK_BYTES for size in emitted[:-1])


def test_core_transfer_assembles_chunks_in_the_stream_pool(monkeypatch, tmp_path):
    """Row 982: the download loop assembles its chunks in a reused pool slot and gives the
    slot back; the harness above still proves the file's bytes are exact."""
    from bulk_downloader import chunked_transfer
    from bulk_downloader.buffer_ring_pool import BufferRingPool
    pool = BufferRingPool(slot_size=MAX_CHUNK_BYTES, capacity=1, aligned=True)
    monkeypatch.setattr(chunked_transfer, "stream_buffer_pool", lambda: pool)
    _adaptive_transfer(monkeypatch, tmp_path, resume=False, cffi=False)
    stats = pool.stats()
    assert stats.acquisitions == 1, "Row 982 capability missing: the download never used the stream pool"
    assert stats.in_use == 0, "ROW982-SLOT-NOT-RETURNED"


def test_static_core_transfer_does_not_adapt(monkeypatch, tmp_path):
    decisions, requests, emitted = _adaptive_transfer(monkeypatch, tmp_path, adaptive=False)
    assert decisions == []
    assert requests == [4 * 1024 * 1024]


def test_slow_socket_does_not_increase_request_cost():
    c = AIMDChunkController(initial_bytes=8 * MIN_CHUNK_BYTES)
    before = c.chunk_bytes
    c.observe(throughput_bps=1024, latency_ms=5.0)
    assert c.chunk_bytes < before, "ADAPTIVE-SLOW-LINK-DID-NOT-BACKOFF"


def test_jitter_backoff_computation_takes_less_than_50ms():
    from time import perf_counter
    c = AIMDChunkController(initial_bytes=8 * MIN_CHUNK_BYTES)
    c.observe(throughput_bps=500_000_000, latency_ms=1.0)
    before = c.chunk_bytes
    started = perf_counter()
    after = c.observe(throughput_bps=500_000_000, latency_ms=81.0)
    elapsed = perf_counter() - started
    assert after < before, "ADAPTIVE-NO-BACKOFF"
    assert elapsed < 0.050, "ADAPTIVE-BACKOFF-TOO-LATE"


@pytest.mark.parametrize("mutation,diagnostic", [
    ("growth", "ADAPTIVE-NO-GROWTH"),
    ("backoff", "ADAPTIVE-NO-BACKOFF"),
    ("corruption", "ADAPTIVE-CHECKSUM"),
])
def test_core_transfer_negative_controls(monkeypatch, tmp_path, mutation, diagnostic):
    from bulk_downloader import chunked_transfer
    original = chunked_transfer.AIMDChunkController.observe
    if mutation == "corruption":
        original_chunks = chunked_transfer.adaptive_chunks

        def corrupt(*args):
            for buf in original_chunks(*args):
                yield bytes(buf)[::-1]  # same size, incorrect data: size gates cannot catch it

        monkeypatch.setattr(chunked_transfer, "adaptive_chunks", corrupt)
    else:
        def broken(controller, **sample):
            before = controller.chunk_bytes
            after = original(controller, **sample)
            if ((mutation == "growth" and after > before)
                    or (mutation == "backoff" and after < before)):
                controller._chunk_bytes = before
            return controller.chunk_bytes

        monkeypatch.setattr(chunked_transfer.AIMDChunkController, "observe", broken)
    with pytest.raises(AssertionError, match=diagnostic):
        test_core_transfer_adapts_and_preserves_checksum(monkeypatch, tmp_path, False, False)


def test_adaptive_stream_handles_empty_and_oversized_buffers(monkeypatch):
    from bulk_downloader import chunked_transfer
    clock = _SocketClock()
    monkeypatch.setattr(chunked_transfer, "monotonic", clock)
    source = [b"", bytes(range(250)) * 100, b"tail", b""]
    controller = AIMDChunkController(min_bytes=4, max_bytes=8, initial_bytes=4)
    chunks = list(chunked_transfer.adaptive_chunks(source, controller))
    assert b"".join(chunks) == b"".join(source)
    assert all(len(buf) == 4 for buf in chunks)
    assert list(chunked_transfer.adaptive_chunks([], controller)) == []


def test_downstream_delays_do_not_become_socket_jitter(monkeypatch):
    from bulk_downloader import chunked_transfer
    clock = _SocketClock()
    monkeypatch.setattr(chunked_transfer, "monotonic", clock)
    controller = AIMDChunkController(min_bytes=4, max_bytes=8, additive_step=1)

    def source():
        for _ in range(10):
            clock.now += 0.001
            yield b"abcdefgh"

    chunks = chunked_transfer.adaptive_chunks(source(), controller)
    values = []
    for buf in chunks:
        values.append(buf)
        clock.now += 100.0  # downstream disk/throttle delay, outside next()
    assert b"".join(values) == b"abcdefgh" * 10
    assert controller.chunk_bytes == 8, "ADAPTIVE-MEASURED-CONSUMER-DELAY"
