"""Bandwidth shaping (Phase 150, Block Q / Row 1055).

Per-site bytes-per-second cap and distributed cluster-wide bandwidth aggregation.
Useful when:
  • BD shares bandwidth with other household/business uses
  • Operator wants to avoid burning a metered VPN allowance in one
    burst
  • Site detects "anomalously fast" downloads as abuse
  • Multi-node federation needs global capacity accounting and admission control

Implementation: a token-bucket per site. Workers call `acquire(sid, n)`
before reading/writing `n` bytes; if the bucket is empty, they wait
until enough tokens have been refilled. Refill rate is the configured
max bytes/sec. Throughput samples are transparently recorded into the
distributed bandwidth aggregator (RFC/Phase 150, Row 1055).

This module is the bucket machinery — the actual "wrap the download
stream in a throttle" code lives in runner.py / runner_transport.py.
"""
from __future__ import annotations

import logging
import threading
import time

from .bandwidth_aggregator import (
    AdmissionResult,
    AdmissionState,
    BandwidthAggregator,
    get_bandwidth_aggregator,
)

log = logging.getLogger(__name__)

# Per-site bucket state: site_id → TokenBucket instance
_buckets: dict[str, TokenBucket] = {}
from .lock_monitor import register_monitored_lock

_buckets_lock = register_monitored_lock("bandwidth_shape._buckets_lock", threading.Lock())


class TokenBucket:
    """Classic token-bucket rate limiter with cluster bandwidth aggregation.

    `capacity` is the maximum burst (bytes).
    `refill_rate` is bytes-per-second of steady-state.

    `acquire(n)` blocks until n tokens are available. Thread-safe.
    """

    def __init__(
        self,
        *,
        capacity: int,
        refill_rate: float,
        site_id: str = "default",
        node_id: str = "local",
        aggregator: BandwidthAggregator | None = None,
    ):
        self.capacity = int(capacity)
        self.refill_rate = float(refill_rate)
        self.site_id = str(site_id)
        self.node_id = str(node_id)
        self._aggregator = aggregator
        self.tokens = float(capacity)
        self.last_refill = time.time()
        self._lock = threading.Lock()

    @property
    def aggregator(self) -> BandwidthAggregator:
        if self._aggregator is None:
            self._aggregator = get_bandwidth_aggregator()
        return self._aggregator

    def _refill(self):
        now = time.time()
        elapsed = now - self.last_refill
        if elapsed > 0:
            self.tokens = min(
                self.capacity,
                self.tokens + elapsed * self.refill_rate,
            )
            self.last_refill = now

    def acquire(self, n: int, *, timeout: float | None = None) -> bool:
        """Block until `n` tokens are available. Returns True on
        success, False if `timeout` expired first."""
        if n <= 0:
            return True
        n = float(n)
        deadline = (time.time() + timeout) if timeout else None
        while True:
            with self._lock:
                self._refill()
                if self.tokens >= n:
                    self.tokens -= n
                    # Row 1055: Record acquired throughput into cluster aggregator
                    try:
                        self.aggregator.record_throughput(
                            node_id=self.node_id,
                            site_id=self.site_id,
                            bytes_ingested=int(n),
                        )
                    except (TypeError, ValueError, RuntimeError, AttributeError) as exc:
                        log.debug("Throughput recording non-fatal error: %s", exc)
                    return True
                # How long do we wait?
                needed = n - self.tokens
                wait = needed / self.refill_rate if self.refill_rate > 0 else 60
            if deadline is not None:
                wait = min(wait, deadline - time.time())
                if wait <= 0:
                    return False
            time.sleep(min(wait, 1.0))

    def try_acquire(self, n: int) -> bool:
        """Non-blocking variant. Returns True if `n` tokens were
        consumed, False if not enough were available."""
        with self._lock:
            self._refill()
            if self.tokens >= n:
                self.tokens -= n
                # Row 1055: Record acquired throughput into cluster aggregator
                try:
                    self.aggregator.record_throughput(
                        node_id=self.node_id,
                        site_id=self.site_id,
                        bytes_ingested=int(n),
                    )
                except (TypeError, ValueError, RuntimeError, AttributeError) as exc:
                    log.debug("Throughput recording non-fatal error: %s", exc)
                return True
            return False

    def snapshot(self) -> dict:
        with self._lock:
            self._refill()
            return {
                "tokens_available": int(self.tokens),
                "capacity": self.capacity,
                "refill_rate_bps": self.refill_rate,
                "saturated_pct": round(100 * self.tokens / max(1, self.capacity), 1),
                "site_id": self.site_id,
                "node_id": self.node_id,
            }

    def update_rate(self, refill_rate: float, *, capacity: int | None = None):
        """Adjust the bucket rate / capacity at runtime."""
        with self._lock:
            self._refill()
            self.refill_rate = float(refill_rate)
            if capacity is not None:
                self.capacity = int(capacity)
                self.tokens = min(self.tokens, self.capacity)


def get_bucket(
    site_id: str,
    *,
    max_bytes_per_sec: float,
    burst_seconds: float = 5.0,
    node_id: str = "local",
) -> TokenBucket:
    """Get or create a bucket for `site_id`. Capacity defaults to
    `burst_seconds × max_bytes_per_sec` so short bursts are allowed."""
    capacity = int(burst_seconds * max_bytes_per_sec)
    with _buckets_lock:
        bucket = _buckets.get(site_id)
        if bucket is None:
            bucket = TokenBucket(
                capacity=capacity,
                refill_rate=max_bytes_per_sec,
                site_id=site_id,
                node_id=node_id,
            )
            _buckets[site_id] = bucket
        else:
            # Update rate if changed
            if bucket.refill_rate != max_bytes_per_sec:
                bucket.update_rate(max_bytes_per_sec, capacity=capacity)
        return bucket


def remove_bucket(site_id: str):
    with _buckets_lock:
        _buckets.pop(site_id, None)


def all_buckets_snapshot() -> dict:
    """Diagnostic view of all active buckets."""
    with _buckets_lock:
        return {sid: bucket.snapshot() for sid, bucket in _buckets.items()}


def cluster_bandwidth_snapshot() -> dict:
    """Row 1055: Diagnostic view of cluster-wide bandwidth aggregation."""
    return get_bandwidth_aggregator().get_cluster_stats()


def check_admission(requested_bps: float) -> AdmissionResult:
    """Row 1055: Check cluster ingestion headroom with three-state fail-closed verdict."""
    return get_bandwidth_aggregator().check_admission(requested_bps)


def can_admit(requested_bps: float) -> bool:
    """Row 1055: Boolean admission gate backed by cluster bandwidth aggregator."""
    return get_bandwidth_aggregator().can_admit(requested_bps)


def reserve_bandwidth(
    requested_bps: float,
    ttl_seconds: float = 10.0,
    node_id: str = "local",
) -> str | None:
    """Row 1055: Reserve a bandwidth slice across the cluster aggregator."""
    return get_bandwidth_aggregator().reserve_capacity(
        node_id=node_id,
        requested_bps=requested_bps,
        ttl_seconds=ttl_seconds,
    )


def release_bandwidth(lease_id: str) -> bool:
    """Row 1055: Release a bandwidth reservation."""
    return get_bandwidth_aggregator().release_reservation(lease_id)


def get_cluster_bandwidth_aggregator() -> BandwidthAggregator:
    """Row 1055: Return the process-wide distributed bandwidth aggregator."""
    return get_bandwidth_aggregator()


class ThrottledReader:
    """Wraps a file-like or generator, applies bandwidth shaping
    as data is read.

    Usage:
        with open(path, "rb") as src:
            reader = ThrottledReader(src, bucket)
            while True:
                chunk = reader.read(65536)
                if not chunk: break
                ...
    """

    def __init__(self, source, bucket: TokenBucket):
        self.source = source
        self.bucket = bucket
        self._bytes_read = 0

    def read(self, n: int = -1) -> bytes:
        # Cap n at the bucket's capacity so we don't ask for more than
        # the bucket can give in a reasonable wait
        if n < 0:
            # full read — split into capacity-sized chunks
            out = []
            while True:
                chunk = self.read(self.bucket.capacity)
                if not chunk:
                    break
                out.append(chunk)
            return b"".join(out)
        n = min(n, self.bucket.capacity)
        self.bucket.acquire(n)
        data = self.source.read(n)
        self._bytes_read += len(data)
        return data

    @property
    def bytes_read(self) -> int:
        return self._bytes_read

    def close(self):
        if hasattr(self.source, "close"):
            self.source.close()


__all__ = [
    "AdmissionResult",
    "AdmissionState",
    "ThrottledReader",
    "TokenBucket",
    "all_buckets_snapshot",
    "can_admit",
    "check_admission",
    "cluster_bandwidth_snapshot",
    "get_bandwidth_aggregator",
    "get_bucket",
    "get_cluster_bandwidth_aggregator",
    "release_bandwidth",
    "remove_bucket",
    "reserve_bandwidth",
]
