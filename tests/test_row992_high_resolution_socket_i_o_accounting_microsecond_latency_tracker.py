"""Row 992: High-Resolution Socket I/O Accounting & Microsecond Latency Tracker.

Provides microsecond-precision socket latency tracking, socket I/O throughput accounting,
transparent socket wrappers, and dynamic transport integration.

RED on baseline: bulk_downloader.socket_tracker does not exist.
"""
from __future__ import annotations

import socket
import threading
import time
from typing import Any, Dict
import pytest

BD_GATE_SCOPE = "repo-wide"


def test_socket_io_accounting_capability_contract():
    """Verify transport module exposes high-resolution socket I/O accounting."""
    from bulk_downloader import chunked_transfer

    assert hasattr(chunked_transfer, "SocketIOTracker") and hasattr(chunked_transfer, "create_tracked_socket"), (
        "Baseline lacks high-resolution socket I/O accounting and microsecond latency tracker"
    )


def test_socket_tracker_module_exports_and_metadata():
    """Verify socket tracker exports and runtime metadata."""
    from bulk_downloader.socket_tracker import (
        SocketIOStats,
        SocketIOTracker,
        TrackedSocket,
        get_socket_tracker_metadata,
        wrap_socket,
    )

    meta = get_socket_tracker_metadata()
    assert isinstance(meta, dict)
    assert meta.get("high_resolution_timer") is True
    assert meta.get("time_unit") == "microsecond"
    assert meta.get("percentiles_supported") is True
    assert callable(SocketIOTracker)
    assert callable(TrackedSocket)
    assert callable(wrap_socket)


def test_socket_tracker_microsecond_accounting():
    """Verify microsecond latency calculation and I/O byte accounting."""
    from bulk_downloader.socket_tracker import SocketIOTracker

    tracker = SocketIOTracker()

    # Record operations with explicit nanosecond durations
    # 1,000,000 ns = 1,000 us; 5,000,000 ns = 5,000 us; 10,000,000 ns = 10,000 us
    tracker.record_send(bytes_count=1024, duration_ns=1_000_000)
    tracker.record_send(bytes_count=2048, duration_ns=5_000_000)
    tracker.record_recv(bytes_count=4096, duration_ns=10_000_000)

    stats = tracker.get_stats()
    assert stats.bytes_sent == 3072
    assert stats.bytes_received == 4096
    assert stats.ops_send == 2
    assert stats.ops_recv == 1
    assert stats.total_ops == 3

    assert stats.min_latency_us == pytest.approx(1000.0, rel=1e-3)
    assert stats.max_latency_us == pytest.approx(10000.0, rel=1e-3)
    # Average: (1000 + 5000 + 10000) / 3 = 5333.33 us
    assert stats.avg_latency_us == pytest.approx(5333.33, rel=1e-2)
    assert stats.p95_latency_us >= 5000.0


def test_socket_tracker_context_manager():
    """Verify time_operation context manager records execution duration and bytes."""
    from bulk_downloader.socket_tracker import SocketIOTracker

    tracker = SocketIOTracker()

    with tracker.time_operation("send") as op:
        time.sleep(0.005)  # ~5,000 microseconds
        op.set_bytes(512)

    stats = tracker.get_stats()
    assert stats.ops_send == 1
    assert stats.bytes_sent == 512
    assert stats.min_latency_us >= 4000.0  # at least ~4ms in microseconds


def test_tracked_socket_wrapper_transparent_io():
    """Verify TrackedSocket intercepts socket operations and records metrics."""
    from bulk_downloader.socket_tracker import SocketIOTracker, wrap_socket

    tracker = SocketIOTracker()
    s1, s2 = socket.socketpair()

    try:
        ts1 = wrap_socket(s1, tracker)
        ts2 = wrap_socket(s2, tracker)

        message = b"bulk_downloader_microsecond_test_payload"
        sent_bytes = ts1.send(message)
        assert sent_bytes == len(message)

        received_bytes = ts2.recv(1024)
        assert received_bytes == message

        stats = tracker.get_stats()
        assert stats.bytes_sent == len(message)
        assert stats.bytes_received == len(message)
        assert stats.ops_send >= 1
        assert stats.ops_recv >= 1
        assert stats.min_latency_us > 0.0
    finally:
        s1.close()
        s2.close()


def test_socket_tracker_aimd_chunk_integration():
    """Verify tracker feeds metrics into AIMDChunkController."""
    from bulk_downloader.chunked_transfer import AIMDChunkController
    from bulk_downloader.socket_tracker import SocketIOTracker

    tracker = SocketIOTracker()
    controller = AIMDChunkController(initial_bytes=4 * 1024 * 1024)
    initial_chunk = controller.chunk_bytes

    # Simulate high throughput, low latency transfer:
    # 10 MiB in 50ms = 200 MiB/s, low latency (1000 us = 1ms)
    tracker.record_recv(bytes_count=10 * 1024 * 1024, duration_ns=50_000_000)

    updated_chunk = tracker.feed_aimd_controller(controller)
    # AIMD additive increase should have increased chunk size
    assert updated_chunk > initial_chunk


def test_socket_tracker_concurrency():
    """Verify thread safety of high-resolution tracking across parallel threads."""
    from bulk_downloader.socket_tracker import SocketIOTracker

    tracker = SocketIOTracker()
    num_threads = 8
    ops_per_thread = 50

    def worker():
        for _ in range(ops_per_thread):
            tracker.record_send(bytes_count=100, duration_ns=500_000)
            tracker.record_recv(bytes_count=200, duration_ns=800_000)

    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    stats = tracker.get_stats()
    expected_sends = num_threads * ops_per_thread
    expected_recvs = num_threads * ops_per_thread
    assert stats.ops_send == expected_sends
    assert stats.ops_recv == expected_recvs
    assert stats.bytes_sent == expected_sends * 100
    assert stats.bytes_received == expected_recvs * 200


def test_socket_tracker_reset():
    """Verify reset clears all accumulated statistics."""
    from bulk_downloader.socket_tracker import SocketIOTracker

    tracker = SocketIOTracker()
    tracker.record_send(1000, 2_000_000)
    assert tracker.get_stats().total_ops == 1

    tracker.reset()
    stats = tracker.get_stats()
    assert stats.total_ops == 0
    assert stats.bytes_sent == 0
    assert stats.bytes_received == 0
    assert stats.min_latency_us == 0.0
    assert stats.max_latency_us == 0.0
