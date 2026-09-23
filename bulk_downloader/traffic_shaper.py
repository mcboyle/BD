"""Bandwidth Budgeting and Traffic Shaping Token Bucket (Row 892).

Provides network throughput pacing and rate control using the token bucket
algorithm. Supports global and per-site bandwidth limits (--max-bandwidth-mbps)
to prevent router saturation and smooth chunk delivery without burst drops.
Incurs zero overhead when bandwidth limits are disabled.
"""

from __future__ import annotations

import io
import math
import threading
import time
from collections.abc import Generator, Iterable
from typing import Any, BinaryIO

# 1 Mbps = 1,000,000 bits/sec = 125,000 bytes/sec
BYTES_PER_MBIT = 125_000


class TokenBucket:
    """Thread-safe token bucket rate limiter for byte streaming."""

    def __init__(
        self,
        rate_bytes_per_sec: float,
        capacity: float | None = None,
        initial_tokens: float = 0.0,
    ) -> None:
        self.rate = float(rate_bytes_per_sec) if rate_bytes_per_sec is not None else 0.0
        # Default burst capacity to 100ms worth of rate, or at least 64 KB
        if capacity is not None:
            self.capacity = float(capacity)
        else:
            self.capacity = max(self.rate * 0.1, 65536.0) if self.rate > 0 else 0.0

        self.tokens = min(float(initial_tokens), self.capacity)
        self.last_update = time.perf_counter()
        self._lock = threading.Lock()

    def consume(self, count: int, block: bool = True) -> float:
        """Consume count bytes from the bucket.

        If block=True, sleeps for the required interval to pace throughput.
        Returns the duration in seconds slept.
        """
        if self.rate <= 0 or count <= 0:
            return 0.0

        with self._lock:
            now = time.perf_counter()
            elapsed = now - self.last_update
            self.last_update = now

            # Refill tokens accumulated during elapsed time
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)

            if self.tokens >= count:
                self.tokens -= count
                return 0.0

            needed = count - self.tokens
            sleep_time = needed / self.rate
            if not block:
                return sleep_time

            # row892 fixer (E1): RESERVE the time slot under the lock by
            # debiting into debt (tokens go negative). A concurrent caller
            # then sees the debt, computes a strictly later slot and sleeps
            # longer, so N threads are serialised at `rate` instead of all
            # sleeping the same interval in parallel and transmitting
            # together. The debt is repaid by the refill on the next call;
            # last_update is therefore NOT reset after the sleep.
            self.tokens -= count

        time.sleep(sleep_time)
        return sleep_time


class BandwidthShaper:
    """Multi-tier traffic shaper enforcing global and per-site bandwidth quotas."""

    def __init__(
        self,
        global_mbps: float | None = None,
        site_mbps: dict[str, float] | None = None,
        burst_ratio: float = 1.0,
    ) -> None:
        self.burst_ratio = max(0.1, float(burst_ratio))
        self._global_bucket: TokenBucket | None = None
        self._site_buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

        if global_mbps is not None and global_mbps > 0:
            self.set_global_cap(global_mbps)

        if site_mbps:
            for site_id, mbps in site_mbps.items():
                self.set_site_cap(site_id, mbps)

    @property
    def is_enabled(self) -> bool:
        if self._global_bucket is not None and self._global_bucket.rate > 0:
            return True
        return any(b.rate > 0 for b in self._site_buckets.values())

    def set_global_cap(self, mbps: float | None) -> None:
        """Configure global bandwidth cap in Mbps."""
        with self._lock:
            if mbps is None or mbps <= 0:
                self._global_bucket = None
            else:
                rate = float(mbps) * BYTES_PER_MBIT
                cap = max(rate * self.burst_ratio * 0.1, 65536.0)
                self._global_bucket = TokenBucket(rate_bytes_per_sec=rate, capacity=cap)

    def set_site_cap(self, site_id: str, mbps: float | None) -> None:
        """Configure per-site bandwidth cap in Mbps."""
        if not site_id:
            return
        with self._lock:
            if mbps is None or mbps <= 0:
                self._site_buckets.pop(site_id, None)
            else:
                rate = float(mbps) * BYTES_PER_MBIT
                cap = max(rate * self.burst_ratio * 0.1, 65536.0)
                self._site_buckets[site_id] = TokenBucket(
                    rate_bytes_per_sec=rate, capacity=cap
                )

    def apply_to_socket(self, sock: Any, site_id: str | None = None) -> dict:
        """Ask the kernel to pace *sock* at this shaper's cap, and report what took.

        Row 1066. ``pace()`` above shapes the READ loop, which reaches the peer only
        indirectly through the receive window and only after the bytes are already here.
        This hands the same ceiling to the kernel's fair-queue scheduler, where it shapes
        egress at transmit time. The two are complements, not alternatives.

        The socket gets the TIGHTER of the site cap for *site_id* and the global cap:
        ``pace()`` waits on both buckets, so a transfer never runs faster than the smaller.
        A shaper with no cap applies nothing: pacing at some default would be a limit
        nobody asked for, and the report would claim a ceiling the caller never chose.
        A cap that is not a finite rate is no cap either: the setters accept inf (which
        ``pace()`` never waits on) and NaN, and int() of either would raise here. So a
        site whose cap is not finite leaves the global cap in force, exactly as ``pace()``
        still waits on the global bucket for it.
        """
        from . import socket_pacing

        with self._lock:
            site = self._site_buckets.get(site_id) if site_id else None
            candidates = (site, self._global_bucket)
        rates = [b.rate for b in candidates
                 if b is not None and b.rate > 0 and math.isfinite(b.rate)]
        if not rates:
            return {"applied": False, "rate_bytes_per_s": 0, "verified_bytes_per_s": None,
                    "reason": "no finite bandwidth cap configured; nothing to ask the kernel for"}
        return socket_pacing.set_pacing_rate(sock, int(min(rates)))

    def pace(self, bytes_count: int, site_id: str | None = None) -> float:
        """Pace a chunk of bytes_count through global and site-specific token buckets.

        Incurs zero overhead when no caps are active.
        Returns the duration slept in seconds.
        """
        if bytes_count <= 0:
            return 0.0

        # Fast path: zero overhead when no limits configured
        if not self._global_bucket and not self._site_buckets:
            return 0.0

        with self._lock:
            gb = self._global_bucket
            sb = self._site_buckets.get(site_id) if site_id else None

        if gb is None and sb is None:
            return 0.0

        # row892 fixer (E2): the two sleeps are sequential, so the caller is
        # told their SUM, not the larger one.
        slept = 0.0
        if sb is not None:
            slept += sb.consume(bytes_count, block=True)
        if gb is not None:
            slept += gb.consume(bytes_count, block=True)

        return slept


def wrap_stream(
    stream: Iterable[bytes],
    shaper: BandwidthShaper | None = None,
    site_id: str | None = None,
) -> Generator[bytes, None, None]:
    """Wrap a generator or iterable of byte chunks with bandwidth shaping."""
    if shaper is None or not shaper.is_enabled:
        yield from stream
        return

    for chunk in stream:
        if chunk:
            shaper.pace(len(chunk), site_id=site_id)
            yield chunk


class ShapedReader(io.RawIOBase):
    """File-like wrapper pacing reads through a BandwidthShaper."""

    def __init__(
        self,
        raw: BinaryIO | io.RawIOBase,
        shaper: BandwidthShaper | None = None,
        chunk_size: int = 65536,
        site_id: str | None = None,
    ) -> None:
        self.raw = raw
        self.shaper = shaper
        self.chunk_size = chunk_size
        self.site_id = site_id

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        if size == 0:
            return b""
        if size < 0:
            # Read all remaining data
            chunks = []
            while True:
                part = self.read(self.chunk_size)
                if not part:
                    break
                chunks.append(part)
            return b"".join(chunks)

        chunk = self.raw.read(size)
        if chunk and self.shaper is not None:
            self.shaper.pace(len(chunk), site_id=self.site_id)
        return chunk

    def readinto(self, b: bytearray) -> int:  # type: ignore[override]
        data = self.read(len(b))
        n = len(data)
        b[:n] = data
        return n


def wrap_reader(
    fileobj: BinaryIO,
    shaper: BandwidthShaper | None = None,
    chunk_size: int = 65536,
    site_id: str | None = None,
) -> ShapedReader:
    """Wrap a file-like stream object with bandwidth shaping on read operations."""
    return ShapedReader(
        raw=fileobj,
        shaper=shaper,
        chunk_size=chunk_size,
        site_id=site_id,
    )
