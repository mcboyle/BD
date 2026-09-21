"""AIMD-driven adaptive chunk sizing for chunked transfer assembly (row854).

Additive-increase/multiplicative-decrease controller: chunk size grows by a
fixed step on each good (high-throughput, low-jitter) observation and halves
immediately on a latency-jitter spike, staying within [MIN_CHUNK_BYTES,
MAX_CHUNK_BYTES]. Assembly is offset-based (``split_into_chunks`` /
``b"".join``), so the assembled bytes are always identical to the source
regardless of how chunk sizes changed mid-transfer.
"""
from __future__ import annotations

from time import monotonic
from typing import Callable, Iterable, Iterator, Optional

MIN_CHUNK_BYTES = 2 * 1024 * 1024   # 2 MiB
MAX_CHUNK_BYTES = 64 * 1024 * 1024  # 64 MiB
_ADDITIVE_STEP_BYTES = 1 * 1024 * 1024  # 1 MiB per good sample
_MULTIPLICATIVE_BACKOFF = 0.5
_JITTER_BACKOFF_MS = 50.0  # latency jitter at/above this triggers backoff


class AIMDChunkController:
    """Adjusts chunk size using additive-increase/multiplicative-decrease.

    A sample lacking both throughput and latency leaves the chunk size
    unchanged, preserving current behavior when metrics are unavailable.
    """

    def __init__(
        self,
        *,
        min_bytes: int = MIN_CHUNK_BYTES,
        max_bytes: int = MAX_CHUNK_BYTES,
        initial_bytes: Optional[int] = None,
        additive_step: int = _ADDITIVE_STEP_BYTES,
        backoff_factor: float = _MULTIPLICATIVE_BACKOFF,
        jitter_threshold_ms: float = _JITTER_BACKOFF_MS,
    ) -> None:
        if min_bytes <= 0 or max_bytes < min_bytes:
            raise ValueError("invalid chunk bounds")
        if not (0.0 < backoff_factor < 1.0):
            raise ValueError("backoff_factor must be in (0, 1)")
        if additive_step <= 0:
            # row854 fixer: a non-positive step could drive chunk_bytes to 0
            # and make split_into_chunks() loop without advancing.
            raise ValueError("additive_step must be positive")
        self.min_bytes = min_bytes
        self.max_bytes = max_bytes
        self.additive_step = additive_step
        self.backoff_factor = backoff_factor
        self.jitter_threshold_ms = jitter_threshold_ms
        initial = initial_bytes if initial_bytes is not None else min_bytes
        self._chunk_bytes = max(self.min_bytes, min(self.max_bytes, initial))
        self._last_latency_ms: Optional[float] = None

    @property
    def chunk_bytes(self) -> int:
        return self._chunk_bytes

    def observe(
        self,
        *,
        throughput_bps: Optional[float] = None,
        latency_ms: Optional[float] = None,
    ) -> int:
        """Record one observation and return the (possibly updated) chunk size."""
        if throughput_bps is None and latency_ms is None:
            return self._chunk_bytes

        jitter_ms = 0.0
        if latency_ms is not None and self._last_latency_ms is not None:
            jitter_ms = abs(latency_ms - self._last_latency_ms)
        if latency_ms is not None:
            self._last_latency_ms = latency_ms

        too_slow = (throughput_bps is not None
                    and 0 <= throughput_bps * 0.5 < self._chunk_bytes)
        if too_slow or (latency_ms is not None and jitter_ms >= self.jitter_threshold_ms):
            self._chunk_bytes = max(
                self.min_bytes, int(self._chunk_bytes * self.backoff_factor)
            )
        elif (throughput_bps is not None
              and throughput_bps * 0.5 >= self._chunk_bytes + self.additive_step):
            # Grow only if the next chunk fits half a second of measured
            # throughput. Positive-but-slow links must not grow to 64 MiB.
            self._chunk_bytes = min(
                self.max_bytes, self._chunk_bytes + self.additive_step
            )
        # The advertised bounds hold on every path out of observe().
        self._chunk_bytes = max(self.min_bytes, min(self.max_bytes, self._chunk_bytes))

        return self._chunk_bytes

    def reset(self) -> None:
        self._chunk_bytes = self.min_bytes
        self._last_latency_ms = None


Telemetry = Callable[[int], Optional[dict]]


def split_into_chunks(
    data: bytes, controller: AIMDChunkController, telemetry: Optional[Telemetry] = None
) -> list[bytes]:
    """Split ``data`` into chunks sized by ``controller``, in order.

    ``telemetry(i)``, if given, returns a dict of ``throughput_bps``/
    ``latency_ms`` (or ``None``) fed to the controller before chunk ``i`` is
    cut, so chunk sizes can change mid-transfer. Because chunks are cut
    contiguously from ``data`` at exact offsets, ``b"".join(result) == data``
    always holds regardless of how sizes changed.
    """
    chunks: list[bytes] = []
    offset = 0
    i = 0
    total = len(data)
    while offset < total:
        if telemetry is not None:
            sample = telemetry(i) or {}
            controller.observe(
                throughput_bps=sample.get("throughput_bps"),
                latency_ms=sample.get("latency_ms"),
            )
        size = controller.chunk_bytes
        chunks.append(data[offset : offset + size])
        offset += size
        i += 1
    return chunks


def adaptive_chunks(
    source: Iterable[bytes], controller: AIMDChunkController
) -> Iterator[bytes]:
    """Batch decoded response bytes using actual upstream wait observations.

    Time only next(source), excluding caller disk writes, pauses and throttles.
    Backoff is applied synchronously on the next received buffer; this cannot
    interrupt a socket read already blocked in the HTTP client. Pending storage
    never exceeds the selected chunk size; even an oversized upstream buffer
    is consumed in contiguous slices. No new HTTP requests are made here.
    """
    iterator = iter(source)
    pending = bytearray()
    while True:
        started = monotonic()
        try:
            buf = next(iterator)
        except StopIteration:
            break
        elapsed = monotonic() - started
        if not buf:
            continue
        target = controller.observe(
            throughput_bps=len(buf) / elapsed if elapsed > 0 else None,
            latency_ms=max(0.0, elapsed) * 1000.0,
        )
        # A decrease can leave several new-sized chunks already pending.
        while len(pending) >= target:
            yield bytes(pending[:target])
            del pending[:target]
        view = memoryview(buf)
        offset = 0
        while offset < len(view):
            take = min(target - len(pending), len(view) - offset)
            pending.extend(view[offset:offset + take])
            offset += take
            if len(pending) == target:
                yield bytes(pending)
                pending.clear()
    if pending:
        yield bytes(pending)
