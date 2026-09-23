"""Userspace Zero-Copy Reusable Buffer Ring Pool with memoryview Slicing and Object Recycling (Row 982).

Provides high-performance, userspace circular buffer allocation and zero-copy slicing
without per-chunk heap allocations and garbage collection overhead.
"""
from __future__ import annotations

import collections
import mmap
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Self


class BufferPoolError(Exception):
    """Base exception for buffer pool operations."""


class BufferPoolExhaustedError(BufferPoolError):
    """Raised when no free buffer slots are available and wait timed out or non-blocking."""


class BufferReleasedError(BufferPoolError):
    """Raised when accessing a buffer that has already been released back to the pool."""


@dataclass(frozen=True)
class BufferPoolStats:
    """Runtime telemetry snapshot for a BufferRingPool."""
    capacity: int
    slot_size: int
    in_use: int
    acquisitions: int
    releases: int
    recycled_count: int
    peak_in_use: int
    total_allocated_bytes: int


class PooledBuffer:
    """A single reusable memory slot acquired from a BufferRingPool."""

    __slots__ = (
        "_generation",
        "_is_released",
        "_length",
        "_mv",
        "_pool",
        "_raw",
        "capacity",
        "slot_id",
    )

    def __init__(
        self,
        slot_id: int,
        capacity: int,
        raw_storage: bytearray | mmap.mmap,
        pool: BufferRingPool | None = None,
    ) -> None:
        self.slot_id = slot_id
        self.capacity = capacity
        self._raw = raw_storage
        self._mv = memoryview(self._raw)
        self._length = 0
        self._is_released = False
        self._pool = pool
        self._generation = 0

    @property
    def length(self) -> int:
        return self._length

    @property
    def is_released(self) -> bool:
        return self._is_released

    @property
    def generation(self) -> int:
        return self._generation

    def _check_alive(self) -> None:
        if self._is_released:
            raise BufferReleasedError(f"Buffer slot {self.slot_id} has already been released.")

    def write(
        self,
        data: bytes | bytearray | memoryview,
        offset: int | None = None,
    ) -> int:
        """Write source data into this buffer slot using zero-copy memoryview assignment."""
        self._check_alive()
        target_offset = self._length if offset is None else offset
        data_len = len(data)

        if target_offset + data_len > self.capacity:
            raise ValueError(
                f"Write of {data_len} bytes at offset {target_offset} exceeds slot capacity {self.capacity}."
            )

        # In-place memoryview write (zero intermediate allocations)
        self._mv[target_offset : target_offset + data_len] = data
        if offset is None:
            self._length += data_len
        else:
            self._length = max(self._length, target_offset + data_len)
        return data_len

    def get_slice(self, start: int = 0, length: int | None = None) -> memoryview:
        """Return a zero-copy memoryview slice of the active buffer contents."""
        self._check_alive()
        if start < 0 or start > self._length:
            raise ValueError(f"Invalid start offset {start} for buffer length {self._length}.")

        max_len = self._length - start
        slice_len = max_len if length is None else length
        if slice_len < 0 or start + slice_len > self._length:
            raise ValueError(f"Requested slice length {slice_len} exceeds active length {self._length}.")

        return self._mv[start : start + slice_len]

    def region(self) -> memoryview:
        """Writable view over the whole slot, for callers that assemble in place."""
        self._check_alive()
        return self._mv[: self.capacity]

    def memoryview(self) -> memoryview:
        """Convenience method returning a memoryview over the full active payload."""
        return self.get_slice(0, self._length)

    def tobytes(self) -> bytes:
        """Export the active payload as bytes."""
        return bytes(self.get_slice())

    def recycle(self) -> None:
        """Reset slot state for reuse without reallocating underlying memory."""
        self._length = 0
        self._is_released = False
        self._generation += 1

    def release(self) -> None:
        """Release this buffer back to its originating pool."""
        if self._is_released:
            return
        if self._pool is not None:
            self._pool.release(self)
        else:
            self._is_released = True

    def __enter__(self) -> Self:
        self._check_alive()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()


class BufferRingPool:
    """Thread-safe userspace circular buffer pool.

    Maintains a ring of pre-allocated memory slots recycled on release.
    """

    def __init__(
        self,
        slot_size: int = 64 * 1024,
        capacity: int = 16,
        aligned: bool = False,
        alignment: int = 4096,
    ) -> None:
        if slot_size <= 0:
            raise ValueError("slot_size must be positive.")
        if capacity <= 0:
            raise ValueError("capacity must be positive.")

        self._slot_size = slot_size
        self._capacity = capacity
        self._aligned = aligned
        self._alignment = alignment

        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

        # Allocate reusable buffer slots
        self._slots: list[PooledBuffer] = []
        self._available_indices = collections.deque(range(capacity))
        self._in_use_count = 0

        # Telemetry metrics
        self._acquisitions = 0
        self._releases = 0
        self._recycled_count = 0
        self._peak_in_use = 0

        for slot_id in range(capacity):
            raw: bytearray | mmap.mmap
            if aligned:
                # Direct I/O block-aligned buffer allocation via anonymous mmap
                raw = mmap.mmap(-1, slot_size)
            else:
                raw = bytearray(slot_size)
            buf = PooledBuffer(slot_id=slot_id, capacity=slot_size, raw_storage=raw, pool=self)
            self._slots.append(buf)

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def slot_size(self) -> int:
        return self._slot_size

    @property
    def in_use(self) -> int:
        with self._lock:
            return self._in_use_count

    @property
    def available(self) -> int:
        with self._lock:
            return len(self._available_indices)

    def acquire(
        self,
        timeout: float | None = None,
        non_blocking: bool = False,
    ) -> PooledBuffer:
        """Acquire a buffer slot from the ring pool.

        Args:
            timeout: Maximum seconds to wait if pool is exhausted. None waits indefinitely.
            non_blocking: If True, raise BufferPoolExhaustedError immediately when empty.
        """
        with self._cond:
            if non_blocking:
                if not self._available_indices:
                    raise BufferPoolExhaustedError(
                        f"Buffer pool exhausted: all {self._capacity} slots currently in use."
                    )
            else:
                if not self._available_indices:
                    if timeout is not None:
                        not_empty = self._cond.wait_for(
                            lambda: len(self._available_indices) > 0,
                            timeout=timeout,
                        )
                        if not not_empty:
                            raise BufferPoolExhaustedError(
                                f"Timed out after {timeout}s waiting for an available buffer slot."
                            )
                    else:
                        self._cond.wait_for(lambda: len(self._available_indices) > 0)

            slot_idx = self._available_indices.popleft()
            buf = self._slots[slot_idx]
            is_recycled = buf.generation > 0 or self._acquisitions >= self._capacity
            buf.recycle()

            self._in_use_count += 1
            self._acquisitions += 1
            if is_recycled:
                self._recycled_count += 1
            self._peak_in_use = max(self._peak_in_use, self._in_use_count)

            return buf

    def release(self, buf: PooledBuffer) -> None:
        """Release an acquired buffer slot back to the ring pool."""
        with self._cond:
            if buf.slot_id < 0 or buf.slot_id >= self._capacity:
                raise BufferPoolError(f"Unknown slot ID {buf.slot_id} for this pool.")
            if self._slots[buf.slot_id] is not buf:
                raise BufferPoolError("Buffer slot object mismatch.")
            if buf._is_released:
                return

            buf._is_released = True
            self._available_indices.append(buf.slot_id)
            self._in_use_count = max(0, self._in_use_count - 1)
            self._releases += 1
            self._cond.notify()

    @contextmanager
    def acquire_context(
        self,
        timeout: float | None = None,
        non_blocking: bool = False,
    ) -> Iterator[PooledBuffer]:
        """Context manager for acquiring and safely auto-releasing a buffer slot."""
        buf = self.acquire(timeout=timeout, non_blocking=non_blocking)
        try:
            yield buf
        finally:
            self.release(buf)

    def stats(self) -> BufferPoolStats:
        """Return a point-in-time telemetry snapshot of pool usage."""
        with self._lock:
            return BufferPoolStats(
                capacity=self._capacity,
                slot_size=self._slot_size,
                in_use=self._in_use_count,
                acquisitions=self._acquisitions,
                releases=self._releases,
                recycled_count=self._recycled_count,
                peak_in_use=self._peak_in_use,
                total_allocated_bytes=self._capacity * self._slot_size,
            )

    def get_stats(self) -> BufferPoolStats:
        """Alias for stats()."""
        return self.stats()

    def close(self) -> None:
        """Close aligned mmaps if allocated."""
        with self._lock:
            if self._aligned:
                for slot in self._slots:
                    if isinstance(slot._raw, mmap.mmap) and not slot._raw.closed:
                        slot._raw.close()


def zero_copy_slice(
    data: bytes | bytearray | memoryview,
    start: int = 0,
    length: int | None = None,
) -> memoryview:
    """Return a memoryview slice of arbitrary buffer-protocol data without heap copies."""
    mv = memoryview(data)
    max_len = len(mv) - start
    slice_len = max_len if length is None else length
    return mv[start : start + slice_len]


def zero_copy_write(
    target: PooledBuffer,
    src: bytes | bytearray | memoryview,
    offset: int = 0,
) -> int:
    """Write source buffer data into a PooledBuffer using zero-copy memoryview assignment."""
    return target.write(src, offset=offset)
