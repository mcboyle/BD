"""Row 982: Userspace Zero-Copy Reusable Buffer Ring Pool with memoryview Slicing and Object Recycling.

Verifies:
1. Zero-copy buffer slot allocation, memoryview slicing, and in-place mutation.
2. Circular ring pool acquisition, recycling, and re-acquisition without heap reallocations.
3. Thread-safe concurrent acquire/release operations.
4. Context manager auto-release semantics.
5. Pool capacity bounds and exhaustion handling.
6. Memory alignment support for Direct I/O compatibility.
7. Telemetry and pool statistics accounting.
8. adaptive_chunks assembles in one reused slot: exact random payload, retained chunks fail
   loudly (E1), pool sized from the controller (E2), bounded acquires (E4).
"""
from __future__ import annotations

import threading

import pytest

BD_GATE_SCOPE = "module"

try:
    from bulk_downloader.buffer_ring_pool import (
        BufferPoolError,
        BufferPoolExhaustedError,
        BufferPoolStats,
        BufferReleasedError,
        BufferRingPool,
        PooledBuffer,
        zero_copy_slice,
        zero_copy_write,
    )
except ImportError:
    BufferPoolError = None  # type: ignore
    BufferPoolExhaustedError = None  # type: ignore
    BufferPoolStats = None  # type: ignore
    BufferReleasedError = None  # type: ignore
    BufferRingPool = None  # type: ignore
    PooledBuffer = None  # type: ignore
    zero_copy_slice = None  # type: ignore
    zero_copy_write = None  # type: ignore


def _random_source(total: int, seed: int = 982) -> tuple[bytes, list[bytes]]:
    """Distinct bytes everywhere, cut into uneven upstream buffers: aliasing or a
    dropped/duplicated span cannot hide the way it can in a homogeneous payload."""
    import random
    rnd = random.Random(seed)
    data = rnd.randbytes(total)
    parts, offset = [], 0
    while offset < total:
        step = rnd.randint(1, 700)
        parts.append(data[offset:offset + step])
        offset += step
    return data, parts


def _fixed_parts(data: bytes, size: int) -> list[bytes]:
    return [data[start:start + size] for start in range(0, len(data), size)]


def _controller():
    from bulk_downloader.chunked_transfer import AIMDChunkController
    return AIMDChunkController(min_bytes=64, max_bytes=512, initial_bytes=64, additive_step=64)


def test_pooled_stream_preserves_a_random_payload_in_one_reused_slot():
    from bulk_downloader.chunked_transfer import adaptive_chunks
    pool = BufferRingPool(slot_size=512, capacity=1)
    data, parts = _random_source(20_000)
    try:
        chunks = adaptive_chunks(parts, _controller(), pool)
    except TypeError as exc:
        raise AssertionError(f"Row 982 capability missing: adaptive_chunks takes no pool ({exc})")
    out = [bytes(c) for c in chunks]
    assert len(out) > 2
    assert b"".join(out) == data, "ROW982-POOLED-PAYLOAD-CORRUPTED"
    stats = pool.stats()
    assert stats.acquisitions == 1 and stats.in_use == 0, stats


def _jittery(parts, clock):
    """Upstream waits: fast, with a latency spike every 5th buffer so the controller
    backs off and chunks already pending are emitted at the smaller size."""
    for i, part in enumerate(parts):
        clock[0] += 0.080 if i % 5 == 4 else 0.001
        yield part


def test_a_retained_pooled_chunk_fails_loudly_instead_of_aliasing(monkeypatch):
    """E1 (P2-B): a chunk kept past the next iteration must never show another chunk's bytes,
    on the grow path and on the backoff path alike."""
    from bulk_downloader import chunked_transfer
    clock = [0.0]
    monkeypatch.setattr(chunked_transfer, "monotonic", lambda: clock[0])
    pool = BufferRingPool(slot_size=512, capacity=1)
    data, parts = _random_source(20_000)
    controller = _controller()
    sizes = []
    real_observe = controller.observe

    def observe(**sample):
        sizes.append(real_observe(**sample))
        return sizes[-1]

    controller.observe = observe
    kept = list(chunked_transfer.adaptive_chunks(_jittery(parts, clock), controller, pool))
    assert any(b < a for a, b in zip(sizes, sizes[1:])), "no backoff exercised"
    assert len(kept) > 2
    for view in kept:
        with pytest.raises(ValueError, match="released"):
            bytes(view)
    clock[0] = 0.0
    copied = [bytes(c) for c in chunked_transfer.adaptive_chunks(
        _jittery(parts, clock), _controller(), pool)]
    assert b"".join(copied) == data, "ROW982-POOLED-PAYLOAD-CORRUPTED"


def test_pool_slot_must_hold_the_largest_chunk_and_the_default_pool_does():
    """E2 (P2-B): a pool too small for the controller is refused up front; the default is sized from it."""
    from bulk_downloader.chunked_transfer import AIMDChunkController, adaptive_chunks, stream_buffer_pool
    with pytest.raises(ValueError, match="smaller than the controller"):
        next(adaptive_chunks([b"x" * 600], _controller(), BufferRingPool(slot_size=256, capacity=1)))
    pool = stream_buffer_pool()
    before = pool.stats().acquisitions
    data, _ = _random_source(5 * 1024 * 1024)
    parts = _fixed_parts(data, 64 * 1024)
    out = b"".join(bytes(c) for c in adaptive_chunks(parts, AIMDChunkController(), pool))
    assert out == data
    assert pool.stats().acquisitions == before + 1


def test_exhausted_pool_falls_back_to_a_private_buffer():
    from bulk_downloader.chunked_transfer import adaptive_chunks
    pool = BufferRingPool(slot_size=512, capacity=1)
    held = pool.acquire(timeout=1.0)
    data, parts = _random_source(8_000)
    assert b"".join(bytes(c) for c in adaptive_chunks(parts, _controller(), pool)) == data
    assert pool.in_use == 1 and not held.is_released
    pool.release(held)


def test_abandoning_a_pooled_stream_returns_its_slot():
    from bulk_downloader.chunked_transfer import adaptive_chunks
    pool = BufferRingPool(slot_size=512, capacity=1)
    _, parts = _random_source(8_000)
    stream = adaptive_chunks(parts, _controller(), pool)
    next(stream)
    assert pool.in_use == 1
    stream.close()
    assert pool.in_use == 0


def test_buffer_slot_lifecycle():
    pool = BufferRingPool(slot_size=4096, capacity=4)
    buf = pool.acquire(timeout=1.0)
    assert isinstance(buf, PooledBuffer)
    assert buf.capacity == 4096
    assert buf.length == 0
    assert not buf.is_released

    # Write payload into buffer
    payload = b"Hello, Zero-Copy World!"
    written = buf.write(payload)
    assert written == len(payload)
    assert buf.length == len(payload)

    # memoryview slicing
    mv = buf.get_slice()
    assert isinstance(mv, memoryview)
    assert bytes(mv) == payload
    assert mv.tobytes() == payload

    # Sub-slice without copy
    sub_mv = buf.get_slice(start=7, length=9)
    assert bytes(sub_mv) == b"Zero-Copy"

    # Release back to pool
    pool.release(buf)
    assert buf.is_released
    with pytest.raises(BufferReleasedError):
        buf.write(b"fail")
    with pytest.raises(BufferReleasedError):
        buf.get_slice()


def test_object_recycling_and_ring_order():
    capacity = 3
    slot_size = 1024
    pool = BufferRingPool(slot_size=slot_size, capacity=capacity)

    # Acquire all slots
    b0 = pool.acquire(timeout=1.0)
    b1 = pool.acquire(timeout=1.0)
    b2 = pool.acquire(timeout=1.0)
    assert pool.in_use == 3

    b0.write(b"data_0")
    b1.write(b"data_1")
    b2.write(b"data_2")

    # Release first slot
    pool.release(b0)
    assert pool.in_use == 2

    # Next acquire in ring order should recycle the released slot
    reacquired = pool.acquire(timeout=1.0)
    assert reacquired.slot_id == b0.slot_id
    assert reacquired.length == 0  # reset on recycle
    assert not reacquired.is_released
    assert pool.stats().recycled_count >= 1

    # Cleanup
    pool.release(b1)
    pool.release(b2)
    pool.release(reacquired)
    assert pool.in_use == 0


def test_context_manager_auto_release():
    pool = BufferRingPool(slot_size=512, capacity=2)
    assert pool.in_use == 0

    with pool.acquire(timeout=1.0) as buf:
        assert pool.in_use == 1
        buf.write(b"ephemeral payload")
        assert bytes(buf.get_slice()) == b"ephemeral payload"

    assert pool.in_use == 0
    assert buf.is_released

    # Verify release on exception
    try:
        with pool.acquire(timeout=1.0) as buf2:
            assert pool.in_use == 1
            raise RuntimeError("simulated error during transfer")
    except RuntimeError:
        pass

    assert pool.in_use == 0
    assert buf2.is_released


def test_pool_exhaustion_and_non_blocking():
    pool = BufferRingPool(slot_size=256, capacity=2)
    b1 = pool.acquire(timeout=1.0)
    b2 = pool.acquire(timeout=1.0)
    assert pool.in_use == 2

    # Non-blocking acquisition when full should raise BufferPoolExhaustedError
    with pytest.raises(BufferPoolExhaustedError):
        pool.acquire(non_blocking=True)

    # Timeout acquisition should raise BufferPoolExhaustedError
    with pytest.raises(BufferPoolExhaustedError):
        pool.acquire(timeout=0.05)

    pool.release(b1)
    # Now acquire succeeds
    b3 = pool.acquire(non_blocking=True)
    assert b3 is not None
    pool.release(b2)
    pool.release(b3)


def test_zero_copy_helpers():
    data = bytearray(b"0123456789ABCDEF")
    sliced = zero_copy_slice(data, start=4, length=6)
    assert isinstance(sliced, memoryview)
    assert bytes(sliced) == b"456789"

    pool = BufferRingPool(slot_size=128, capacity=1)
    with pool.acquire(timeout=1.0) as buf:
        n = zero_copy_write(buf, data, offset=0)
        assert n == len(data)
        assert bytes(buf.get_slice()) == bytes(data)


def test_aligned_buffer_pool():
    # 4096-byte alignment
    pool = BufferRingPool(slot_size=8192, capacity=2, aligned=True, alignment=4096)
    with pool.acquire(timeout=1.0) as buf:
        assert buf.capacity == 8192
        buf.write(b"X" * 1024)
        assert buf.length == 1024


def test_concurrent_acquire_release():
    slot_count = 8
    iterations = 200
    pool = BufferRingPool(slot_size=512, capacity=slot_count)
    errors = []

    def worker(worker_id: int):
        for i in range(iterations):
            try:
                with pool.acquire(timeout=2.0) as buf:
                    payload = f"worker-{worker_id}-iter-{i}".encode()
                    buf.write(payload)
                    read_back = bytes(buf.get_slice())
                    if read_back != payload:
                        errors.append(f"Mismatch: {read_back!r} != {payload!r}")
            except (BufferPoolError, RuntimeError, OSError) as exc:
                errors.append(str(exc))

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Errors in concurrent worker threads: {errors}"
    assert pool.in_use == 0
    stats = pool.stats()
    assert stats.acquisitions == 4 * iterations
    assert stats.releases == 4 * iterations


def test_pool_stats_accounting():
    pool = BufferRingPool(slot_size=1024, capacity=4)
    initial_stats = pool.stats()
    assert isinstance(initial_stats, BufferPoolStats)
    assert initial_stats.capacity == 4
    assert initial_stats.slot_size == 1024
    assert initial_stats.in_use == 0
    assert initial_stats.acquisitions == 0

    with pool.acquire(timeout=1.0):
        assert pool.stats().in_use == 1

    final_stats = pool.stats()
    assert final_stats.in_use == 0
    assert final_stats.acquisitions == 1
    assert final_stats.releases == 1
