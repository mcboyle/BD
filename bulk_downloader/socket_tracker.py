"""High-resolution socket I/O accounting and microsecond latency tracker (Row 992).

Tracks socket operation latencies at nanosecond precision, computes microsecond-level
statistics (min, max, average, p95, p99), monitors send/receive byte throughput,
provides transparent TrackedSocket wrappers, and integrates with AIMDChunkController.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import socket
import threading
import time
from typing import Any, Generator, List, Optional


@dataclass(frozen=True)
class SocketIOStats:
    """Snapshot of socket I/O throughput and latency metrics."""

    bytes_sent: int
    bytes_received: int
    ops_send: int
    ops_recv: int
    total_ops: int
    min_latency_us: float
    max_latency_us: float
    total_latency_us: float
    avg_latency_us: float
    p95_latency_us: float
    p99_latency_us: float
    window_duration_seconds: float
    throughput_bytes_per_sec: float


class _TimingOperation:
    """Helper for timing context manager."""

    def __init__(self, op_type: str, initial_bytes: int = 0) -> None:
        self.op_type = op_type
        self.bytes_count = initial_bytes

    def set_bytes(self, count: int) -> None:
        self.bytes_count = max(0, int(count))


class SocketIOTracker:
    """Thread-safe socket I/O accounting and microsecond latency tracker."""

    def __init__(self, max_samples: int = 10000) -> None:
        self._max_samples = max_samples
        self._lock = threading.Lock()
        self._bytes_sent = 0
        self._bytes_recv = 0
        self._ops_send = 0
        self._ops_recv = 0
        self._latencies_us: List[float] = []
        self._min_latency_us = 0.0
        self._max_latency_us = 0.0
        self._total_latency_us = 0.0
        self._start_time = time.monotonic()

    def record_send(self, bytes_count: int, duration_ns: int) -> None:
        """Record a socket send operation with duration in nanoseconds."""
        lat_us = max(0.0, duration_ns / 1000.0)
        b = max(0, int(bytes_count))
        with self._lock:
            self._bytes_sent += b
            self._ops_send += 1
            self._record_latency_locked(lat_us)

    def record_recv(self, bytes_count: int, duration_ns: int) -> None:
        """Record a socket receive operation with duration in nanoseconds."""
        lat_us = max(0.0, duration_ns / 1000.0)
        b = max(0, int(bytes_count))
        with self._lock:
            self._bytes_recv += b
            self._ops_recv += 1
            self._record_latency_locked(lat_us)

    def _record_latency_locked(self, lat_us: float) -> None:
        if not self._latencies_us:
            self._min_latency_us = lat_us
            self._max_latency_us = lat_us
        else:
            if lat_us < self._min_latency_us:
                self._min_latency_us = lat_us
            if lat_us > self._max_latency_us:
                self._max_latency_us = lat_us
        self._total_latency_us += lat_us
        if len(self._latencies_us) < self._max_samples:
            self._latencies_us.append(lat_us)
        else:
            idx = (self._ops_send + self._ops_recv) % self._max_samples
            self._latencies_us[idx] = lat_us

    @contextmanager
    def time_operation(self, op_type: str, bytes_count: int = 0) -> Generator[_TimingOperation, None, None]:
        """Context manager to measure operation latency in microseconds."""
        op = _TimingOperation(op_type, bytes_count)
        start_ns = time.perf_counter_ns()
        try:
            yield op
        finally:
            end_ns = time.perf_counter_ns()
            duration_ns = max(0, end_ns - start_ns)
            if op.op_type == "send":
                self.record_send(op.bytes_count, duration_ns)
            elif op.op_type == "recv":
                self.record_recv(op.bytes_count, duration_ns)
            else:
                lat_us = duration_ns / 1000.0
                with self._lock:
                    self._record_latency_locked(lat_us)

    def get_stats(self) -> SocketIOStats:
        """Calculate and return a snapshot of socket stats."""
        with self._lock:
            total_ops = self._ops_send + self._ops_recv
            window = max(1e-6, time.monotonic() - self._start_time)
            total_bytes = self._bytes_sent + self._bytes_recv
            throughput = total_bytes / window
            avg_us = (self._total_latency_us / total_ops) if total_ops > 0 else 0.0

            if self._latencies_us:
                sorted_lats = sorted(self._latencies_us)
                p95_idx = min(len(sorted_lats) - 1, int(len(sorted_lats) * 0.95))
                p99_idx = min(len(sorted_lats) - 1, int(len(sorted_lats) * 0.99))
                p95_val = sorted_lats[p95_idx]
                p99_val = sorted_lats[p99_idx]
            else:
                p95_val = 0.0
                p99_val = 0.0

            return SocketIOStats(
                bytes_sent=self._bytes_sent,
                bytes_received=self._bytes_recv,
                ops_send=self._ops_send,
                ops_recv=self._ops_recv,
                total_ops=total_ops,
                min_latency_us=self._min_latency_us,
                max_latency_us=self._max_latency_us,
                total_latency_us=self._total_latency_us,
                avg_latency_us=avg_us,
                p95_latency_us=p95_val,
                p99_latency_us=p99_val,
                window_duration_seconds=window,
                throughput_bytes_per_sec=throughput,
            )

    def reset(self) -> None:
        """Reset all counters and latency buffers."""
        with self._lock:
            self._bytes_sent = 0
            self._bytes_recv = 0
            self._ops_send = 0
            self._ops_recv = 0
            self._latencies_us.clear()
            self._min_latency_us = 0.0
            self._max_latency_us = 0.0
            self._total_latency_us = 0.0
            self._start_time = time.monotonic()

    def feed_aimd_controller(self, controller: Any) -> int:
        """Feed current socket stats into an AIMDChunkController and return updated chunk size."""
        stats = self.get_stats()
        throughput_bps = stats.throughput_bytes_per_sec * 8.0
        latency_ms = stats.avg_latency_us / 1000.0 if stats.total_ops > 0 else None
        return controller.observe(throughput_bps=throughput_bps, latency_ms=latency_ms)


class TrackedSocket:
    """Transparent socket proxy that records I/O accounting and latency."""

    def __init__(self, sock: socket.socket, tracker: Optional[SocketIOTracker] = None) -> None:
        self._sock = sock
        self._tracker = tracker or SocketIOTracker()

    @property
    def tracker(self) -> SocketIOTracker:
        return self._tracker

    def send(self, data: bytes | bytearray | memoryview, flags: int = 0) -> int:
        start_ns = time.perf_counter_ns()
        try:
            sent = self._sock.send(data, flags)
            return sent
        finally:
            end_ns = time.perf_counter_ns()
            self._tracker.record_send(sent if "sent" in locals() else 0, end_ns - start_ns)

    def sendall(self, data: bytes | bytearray | memoryview, flags: int = 0) -> None:
        start_ns = time.perf_counter_ns()
        try:
            return self._sock.sendall(data, flags)
        finally:
            end_ns = time.perf_counter_ns()
            self._tracker.record_send(len(data), end_ns - start_ns)

    def recv(self, bufsize: int, flags: int = 0) -> bytes:
        start_ns = time.perf_counter_ns()
        try:
            data = self._sock.recv(bufsize, flags)
            return data
        finally:
            end_ns = time.perf_counter_ns()
            self._tracker.record_recv(len(data) if "data" in locals() else 0, end_ns - start_ns)

    def recv_into(self, buffer: Any, nbytes: int = 0, flags: int = 0) -> int:
        start_ns = time.perf_counter_ns()
        try:
            n = self._sock.recv_into(buffer, nbytes, flags)
            return n
        finally:
            end_ns = time.perf_counter_ns()
            self._tracker.record_recv(n if "n" in locals() else 0, end_ns - start_ns)

    def connect(self, address: Any) -> None:
        start_ns = time.perf_counter_ns()
        try:
            return self._sock.connect(address)
        finally:
            end_ns = time.perf_counter_ns()
            self._tracker.record_send(0, end_ns - start_ns)

    def close(self) -> None:
        return self._sock.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._sock, name)


def wrap_socket(sock: socket.socket, tracker: Optional[SocketIOTracker] = None) -> TrackedSocket:
    """Wrap a socket with high-resolution I/O accounting and latency tracking."""
    return TrackedSocket(sock, tracker)


def get_socket_tracker_metadata() -> dict[str, Any]:
    """Introspection metadata describing high-resolution socket tracking."""
    return {
        "high_resolution_timer": True,
        "time_unit": "microsecond",
        "precision": "nanosecond",
        "percentiles_supported": True,
        "aimd_compatible": True,
    }
