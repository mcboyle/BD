"""v3.43.76: per-download bandwidth supervisor + throttling.

# What this module is

A token-bucket rate limiter for download bytes-per-second. Workers
call `acquire(site_id, n_bytes)` before writing chunks; the supervisor
blocks until tokens are available, enforcing both a global cap and
per-site caps.

# Why this exists

At 8K HEVC with v3.43.74 multi-connection (4-8 parallel chunks), a
single download can saturate Matt's network. With multiple sites
running concurrent workers, the aggregate can hit ISP rate limits
or starve other services on the LAN.

The supervisor gives Matt deterministic, configurable bandwidth caps:

  - Global cap: total across ALL active workers + sites
  - Per-site cap: per-site override (e.g. let Aylo have 200 MB/s but
    cap Vixen at 50 MB/s)
  - Hot-reload: configure() updates limits on the fly without
    restarting workers
  - Fail-open: when disabled, acquire() returns immediately

# Token bucket implementation

Each bucket has:
  - capacity: max tokens the bucket can hold (= ~1 sec of throughput)
  - rate: tokens added per second
  - tokens: current balance (float for sub-second accuracy)
  - last_refill: monotonic timestamp

`acquire(n)` blocks until n <= tokens, then deducts n. Refill happens
lazily on each call: tokens += (now - last_refill) * rate, clamped
to capacity.

When BOTH global and per-site buckets are active, acquire() must wait
on BOTH (the slower one wins). We do this with two-phase: deduct from
the more-constrained one first, then the other.

# Caveats

  - This is a SOFT cap: a single huge chunk write can momentarily
    exceed the cap by ~chunk_size (since acquire() is per-chunk, not
    per-byte).
  - Hot-reload is best-effort: in-flight acquire() calls finish at
    the old rate; new calls use the new rate.
  - Zero or negative rate = "unlimited" (acquire returns instantly).

# API

  - `configure(global_bps=0, per_site_bps={})` — set rates
  - `acquire(site_id, n_bytes)` — blocking call, returns after wait
  - `try_acquire(site_id, n_bytes, max_wait_s=None)` — non-blocking
    variant; returns (acquired: bool, waited_s: float)
  - `stats()` — current bucket state + acquisition counters
  - `reset()` — clear all buckets (testing only)
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# Sentinel: "no limit" rate. Set to 0 explicitly OR pass negative.
UNLIMITED = 0


# ─── Token bucket ─────────────────────────────────────────────────


class TokenBucket:
    """One token bucket. Thread-safe via internal lock."""

    def __init__(self, name: str, rate_bps: int = UNLIMITED,
                 capacity_factor: float = 1.0):
        """
        rate_bps: bytes per second. <=0 means unlimited.
        capacity_factor: bucket capacity = rate * factor seconds.
        """
        self.name = name
        self._lock = threading.Lock()
        self._rate_bps: float = float(max(0, rate_bps))
        self._capacity: float = self._rate_bps * capacity_factor
        self._tokens: float = self._capacity
        self._last_refill: float = time.monotonic()
        self._capacity_factor: float = capacity_factor

        # Stats
        self._acquires: int = 0
        self._bytes_passed: int = 0
        self._total_wait_s: float = 0.0

    def _refill_locked(self) -> None:
        """Refill tokens based on elapsed time. Caller holds _lock."""
        if self._rate_bps <= 0:
            return  # unlimited — no need to track tokens
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(
                self._capacity, self._tokens + elapsed * self._rate_bps)
            self._last_refill = now

    def set_rate(self, rate_bps: int) -> None:
        """Hot-update the rate. Adjusts capacity proportionally and
        refills the bucket to the new capacity (gives one full
        burst-second of headroom after a rate change)."""
        with self._lock:
            prev_rate = self._rate_bps
            self._rate_bps = float(max(0, rate_bps))
            self._capacity = self._rate_bps * self._capacity_factor
            # On a meaningful rate change (especially from UNLIMITED →
            # limited), give the bucket a full capacity. Standard
            # token-bucket convention is to start full so the first
            # second of usage doesn't have to wait for refill.
            if prev_rate <= 0 and self._rate_bps > 0:
                # transitioning from unlimited → limited; start full
                self._tokens = self._capacity
            elif self._tokens > self._capacity:
                # rate decreased — cap tokens at new capacity
                self._tokens = self._capacity
            # If rate increased while limited, leave tokens alone
            # (they'll refill at the new rate)
            self._last_refill = time.monotonic()

    def acquire(self, n_bytes: int) -> float:
        """Block until n_bytes tokens are available, then deduct them.
        Returns the time spent waiting (0.0 if no wait was needed).

        "Wait" here means actual time.sleep() calls — not the few
        microseconds of lock acquisition + refill arithmetic. A call
        that finds tokens already available on the first iteration
        returns exactly 0.0 even though the function itself took
        nonzero wall time to execute. This keeps the semantics clean
        for callers (and tests) that key behavior off "did we throttle?"
        """
        if n_bytes <= 0:
            return 0.0
        # Unlimited fast path: no need to track or wait
        with self._lock:
            if self._rate_bps <= 0:
                self._acquires += 1
                self._bytes_passed += n_bytes
                return 0.0

        slept_s = 0.0
        while True:
            with self._lock:
                self._refill_locked()
                if self._tokens >= n_bytes:
                    self._tokens -= n_bytes
                    self._acquires += 1
                    self._bytes_passed += n_bytes
                    if slept_s > 0.0:
                        self._total_wait_s += slept_s
                    return slept_s
                # How long until enough tokens accrue?
                deficit = n_bytes - self._tokens
                rate = self._rate_bps
                wait_for = deficit / rate if rate > 0 else 1.0
            # Sleep outside the lock so other workers can refill check
            # against the same bucket
            # Cap each sleep at 0.5s so set_rate updates take effect
            # within half a second.
            sleep_chunk = min(0.5, max(0.01, wait_for))
            time.sleep(sleep_chunk)
            slept_s += sleep_chunk

    def try_acquire(
        self, n_bytes: int,
        max_wait_s: Optional[float] = None,
    ) -> tuple:
        """Non-blocking variant. If max_wait_s is None, attempts an
        immediate acquire; returns (False, 0.0) if not enough tokens.
        If max_wait_s is set, waits up to that many seconds before
        giving up.

        Returns (acquired: bool, waited_s: float).
        """
        if n_bytes <= 0:
            return (True, 0.0)
        with self._lock:
            if self._rate_bps <= 0:
                self._acquires += 1
                self._bytes_passed += n_bytes
                return (True, 0.0)
            self._refill_locked()
            if self._tokens >= n_bytes:
                self._tokens -= n_bytes
                self._acquires += 1
                self._bytes_passed += n_bytes
                return (True, 0.0)
            if max_wait_s is None or max_wait_s <= 0:
                return (False, 0.0)
            deficit = n_bytes - self._tokens
            rate = self._rate_bps
            wait_for = deficit / rate if rate > 0 else 1.0

        if wait_for > max_wait_s:
            return (False, 0.0)

        # Wait outside lock + retry once
        time.sleep(wait_for)
        with self._lock:
            self._refill_locked()
            if self._tokens >= n_bytes:
                self._tokens -= n_bytes
                self._acquires += 1
                self._bytes_passed += n_bytes
                self._total_wait_s += wait_for
                return (True, wait_for)
            return (False, wait_for)

    def stats(self) -> dict:
        with self._lock:
            self._refill_locked()
            return {
                "name": self.name,
                "rate_bps": int(self._rate_bps),
                "capacity": int(self._capacity),
                "tokens_available": int(self._tokens),
                "acquires": self._acquires,
                "bytes_passed": self._bytes_passed,
                "total_wait_s": round(self._total_wait_s, 3),
            }


# ─── Supervisor ───────────────────────────────────────────────────


@dataclass
class SupervisorConfig:
    """Current rate limits."""
    enabled: bool = False
    global_bps: int = UNLIMITED                  # 0 = unlimited
    per_site_bps: dict = field(default_factory=dict)  # site_id → bps


class _SupervisorState:
    """Module-level singleton state. Use the module-level API instead
    of instantiating this directly."""

    def __init__(self):
        self._lock = threading.RLock()
        self._cfg = SupervisorConfig()
        self._global_bucket = TokenBucket("global", UNLIMITED)
        self._site_buckets: dict = {}    # site_id → TokenBucket

    def configure(
        self, *,
        enabled: bool = False,
        global_bps: int = UNLIMITED,
        per_site_bps: Optional[dict] = None,
    ) -> None:
        """Hot-reload rate limits."""
        with self._lock:
            self._cfg.enabled = bool(enabled)
            self._cfg.global_bps = int(max(0, global_bps))
            self._cfg.per_site_bps = dict(per_site_bps or {})
            # Apply to existing buckets
            self._global_bucket.set_rate(self._cfg.global_bps)
            # Update or create per-site buckets
            for site_id, bps in self._cfg.per_site_bps.items():
                bucket = self._site_buckets.get(site_id)
                if bucket is None:
                    bucket = TokenBucket(f"site:{site_id}",
                                          int(max(0, bps)))
                    self._site_buckets[site_id] = bucket
                else:
                    bucket.set_rate(int(max(0, bps)))
            # Sites no longer in config: set unlimited (don't delete —
            # in-flight workers may still reference them)
            for site_id, bucket in self._site_buckets.items():
                if site_id not in self._cfg.per_site_bps:
                    bucket.set_rate(UNLIMITED)

    def acquire(self, site_id: str, n_bytes: int) -> float:
        """Block until n_bytes can be passed under both the global +
        per-site caps. Returns total wait time."""
        if n_bytes <= 0:
            return 0.0
        with self._lock:
            if not self._cfg.enabled:
                return 0.0
            global_bucket = self._global_bucket
            site_bucket = self._site_buckets.get(site_id)
        total_wait = 0.0
        # Acquire from the global bucket first (most-constrained
        # usually). Then site bucket.
        total_wait += global_bucket.acquire(n_bytes)
        if site_bucket is not None:
            total_wait += site_bucket.acquire(n_bytes)
        return total_wait

    def stats(self) -> dict:
        with self._lock:
            return {
                "enabled": self._cfg.enabled,
                "global": self._global_bucket.stats(),
                "per_site": {
                    site_id: bucket.stats()
                    for site_id, bucket in self._site_buckets.items()
                },
                "config": {
                    "global_bps": self._cfg.global_bps,
                    "per_site_bps": dict(self._cfg.per_site_bps),
                },
            }

    def reset(self) -> None:
        """Testing helper: clear all state."""
        with self._lock:
            self._cfg = SupervisorConfig()
            self._global_bucket = TokenBucket("global", UNLIMITED)
            self._site_buckets = {}


_state = _SupervisorState()


# ─── Module-level API ─────────────────────────────────────────────


def configure(
    *,
    enabled: bool = False,
    global_bps: int = UNLIMITED,
    per_site_bps: Optional[dict] = None,
) -> None:
    """Hot-reload rate limits.

    `enabled`: master switch. When False, acquire() returns instantly.
    `global_bps`: cap across ALL workers. 0 = unlimited.
    `per_site_bps`: dict of site_id → bytes_per_second. 0 = unlimited
        for that site.

    Example:
        configure(enabled=True, global_bps=200_000_000,
                  per_site_bps={"vixen": 50_000_000, "aylo": 0})
    """
    _state.configure(
        enabled=enabled,
        global_bps=global_bps,
        per_site_bps=per_site_bps,
    )


def acquire(site_id: str, n_bytes: int) -> float:
    """Block until n_bytes can be passed under both global + per-site
    caps. Returns the total time spent waiting (0.0 if no wait).

    Safe to call even when supervisor is disabled — returns 0.0
    instantly in that case.

    Safe to call with site_id=None or empty string — only the global
    bucket applies in that case.
    """
    if not site_id:
        site_id = ""
    return _state.acquire(site_id, n_bytes)


def stats() -> dict:
    """Snapshot of current bucket state + acquisition counters."""
    return _state.stats()


def is_enabled() -> bool:
    """Quick check without locking the whole state."""
    return _state._cfg.enabled


def reset() -> None:
    """Reset all state. Testing only — production code should call
    configure() to update rates."""
    _state.reset()


__all__ = [
    "UNLIMITED",
    "TokenBucket",
    "SupervisorConfig",
    "configure",
    "acquire",
    "stats",
    "is_enabled",
    "reset",
]
