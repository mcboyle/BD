"""Bandwidth shaping (Phase 150, Block Q).

Per-site bytes-per-second cap. Useful when:
  • BD shares bandwidth with other household/business uses
  • Operator wants to avoid burning a metered VPN allowance in one
    burst
  • Site detects "anomalously fast" downloads as abuse

Implementation: a token-bucket per site. Workers call `acquire(sid, n)`
before reading/writing `n` bytes; if the bucket is empty, they wait
until enough tokens have been refilled. Refill rate is the configured
max bytes/sec.

This module is the bucket machinery — the actual "wrap the download
stream in a throttle" code lives in runner.py and uses these primitives.
"""
from __future__ import annotations

import threading
import time
from typing import Optional


# Per-site bucket state: site_id → TokenBucket instance
_buckets: dict = {}
_buckets_lock = threading.Lock()


class TokenBucket:
    """Classic token-bucket rate limiter.

    `capacity` is the maximum burst (bytes).
    `refill_rate` is bytes-per-second of steady-state.

    `acquire(n)` blocks until n tokens are available. Thread-safe.
    """

    def __init__(self, *, capacity: int, refill_rate: float):
        self.capacity = int(capacity)
        self.refill_rate = float(refill_rate)
        self.tokens = float(capacity)
        self.last_refill = time.time()
        self._lock = threading.Lock()

    def _refill(self):
        now = time.time()
        elapsed = now - self.last_refill
        if elapsed > 0:
            self.tokens = min(self.capacity,
                              self.tokens + elapsed * self.refill_rate)
            self.last_refill = now

    def acquire(self, n: int, *,
                timeout: Optional[float] = None) -> bool:
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
            }

    def update_rate(self, refill_rate: float, *,
                   capacity: Optional[int] = None):
        """Adjust the bucket rate / capacity at runtime."""
        with self._lock:
            self._refill()
            self.refill_rate = float(refill_rate)
            if capacity is not None:
                self.capacity = int(capacity)
                self.tokens = min(self.tokens, self.capacity)


def get_bucket(site_id: str, *,
              max_bytes_per_sec: float,
              burst_seconds: float = 5.0) -> TokenBucket:
    """Get or create a bucket for `site_id`. Capacity defaults to
    `burst_seconds × max_bytes_per_sec` so short bursts are allowed."""
    capacity = int(burst_seconds * max_bytes_per_sec)
    with _buckets_lock:
        bucket = _buckets.get(site_id)
        if bucket is None:
            bucket = TokenBucket(capacity=capacity,
                                 refill_rate=max_bytes_per_sec)
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
        return {sid: bucket.snapshot()
                for sid, bucket in _buckets.items()}


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
