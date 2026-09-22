"""token_bucket_backpressure -- Per-Domain Token Bucket Rate-Limit & Backpressure Telemetry.

Row 996 (v3.66.1620): continuous token bucket rate-limiting with fractional token refill,
burst capacity limits, wait queue tracking, backpressure coefficient calculation,
and granular per-domain telemetry export.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlparse


class BackpressureState(str, Enum):
    """Backpressure congestion states for domain rate-limiting."""
    NORMAL = "normal"
    CONGESTED = "congested"
    SHEDDING = "shedding"


@dataclass
class TokenBucket:
    """Thread-safe continuous token bucket with temporal refill."""
    capacity: float
    refill_rate: float
    _tokens: float = field(init=False)
    last_refill: float = field(init=False)

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError(f"capacity must be positive, got {self.capacity}")
        if self.refill_rate <= 0:
            raise ValueError(f"refill_rate must be positive, got {self.refill_rate}")
        self._tokens = float(self.capacity)
        self.last_refill = time.monotonic()
        self._lock = threading.Lock()

    @property
    def tokens(self) -> float:
        """Current tokens available after dynamic temporal refill."""
        return self.refill()

    @tokens.setter
    def tokens(self, val: float) -> None:
        with self._lock:
            self._tokens = val

    def refill(self) -> float:
        """Accumulate tokens based on elapsed wall time since last refill."""
        with self._lock:
            now = time.monotonic()
            elapsed = max(0.0, now - self.last_refill)
            self.last_refill = now
            self._tokens = min(self.capacity, self._tokens + (elapsed * self.refill_rate))
            return self._tokens

    def try_consume(self, amount: float = 1.0) -> bool:
        """Attempt to consume tokens immediately without waiting."""
        with self._lock:
            now = time.monotonic()
            elapsed = max(0.0, now - self.last_refill)
            self.last_refill = now
            self._tokens = min(self.capacity, self._tokens + (elapsed * self.refill_rate))

            if self._tokens >= amount:
                self._tokens -= amount
                return True
            return False


class TokenBucketSlot:
    """Acquired rate-limit lease holding concurrency and token quota."""
    __slots__ = ("_domain", "_limiter", "_released")

    def __init__(self, limiter: DomainTokenBucketLimiter, domain: str) -> None:
        self._limiter = limiter
        self._domain = domain
        self._released = False

    @property
    def domain(self) -> str:
        return self._domain

    @property
    def is_acquired(self) -> bool:
        return not self._released

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._limiter._release_domain(self._domain)


class _DomainBackpressureState:
    """Internal coordination and metric store for one registrable domain."""

    def __init__(
        self,
        domain: str,
        capacity: float = 10.0,
        refill_rate: float = 10.0,
        max_concurrent: int = 5,
    ) -> None:
        self.domain = domain
        self.bucket = TokenBucket(capacity=capacity, refill_rate=refill_rate)
        self.max_concurrent = max(0, max_concurrent)
        self.active_requests = 0
        self.waiting_requests = 0
        self.acquired_count = 0
        self.rejected_count = 0
        self.recent_wait_times: deque[float] = deque(maxlen=256)
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)

    def calculate_backpressure_score(self) -> float:
        """Compute normalized congestion coefficient [0.0, 1.0]."""
        with self.lock:
            # Concurrency pressure
            c_ratio = 0.0
            if self.max_concurrent > 0:
                c_ratio = min(1.0, float(self.active_requests) / float(self.max_concurrent))

            # Token depletion pressure
            t_avail = self.bucket.refill()
            t_ratio = 1.0 - min(1.0, max(0.0, t_avail / self.bucket.capacity))

            # Wait queue pressure
            w_ratio = min(1.0, float(self.waiting_requests) / 5.0)

            # Combined weighted score
            score = (0.45 * c_ratio) + (0.35 * t_ratio) + (0.20 * w_ratio)
            return round(min(1.0, max(0.0, score)), 4)

    def get_state_classification(self, score: float) -> BackpressureState:
        if score >= 0.85:
            return BackpressureState.SHEDDING
        if score >= 0.50:
            return BackpressureState.CONGESTED
        return BackpressureState.NORMAL


class DomainTokenBucketLimiter:
    """Enterprise rate-limiting engine managing per-domain token buckets and telemetry."""

    def __init__(self) -> None:
        self._domains: dict[str, _DomainBackpressureState] = {}
        self._domains_lock = threading.Lock()
        self._default_capacity = 10.0
        self._default_refill_rate = 10.0
        self._default_max_concurrent = 5

    def configure_domain(
        self,
        domain: str,
        capacity: float = 10.0,
        refill_rate: float = 10.0,
        max_concurrent: int = 5,
    ) -> None:
        """Set or update limits for a specific registrable domain."""
        norm_domain = domain.lower().strip()
        with self._domains_lock:
            state = self._domains.get(norm_domain)
            if state is None:
                self._domains[norm_domain] = _DomainBackpressureState(
                    norm_domain,
                    capacity=capacity,
                    refill_rate=refill_rate,
                    max_concurrent=max_concurrent,
                )
            else:
                with state.lock:
                    state.bucket = TokenBucket(capacity=capacity, refill_rate=refill_rate)
                    state.max_concurrent = max_concurrent
                    state.condition.notify_all()

    def _get_or_create_state(self, domain: str) -> _DomainBackpressureState:
        norm_domain = domain.lower().strip()
        with self._domains_lock:
            state = self._domains.get(norm_domain)
            if state is None:
                state = _DomainBackpressureState(
                    norm_domain,
                    capacity=self._default_capacity,
                    refill_rate=self._default_refill_rate,
                    max_concurrent=self._default_max_concurrent,
                )
                self._domains[norm_domain] = state
            return state

    @staticmethod
    def _extract_domain(url_or_domain: str) -> str:
        if not url_or_domain:
            return ""
        if "://" in url_or_domain:
            try:
                parsed = urlparse(url_or_domain)
                host = (parsed.hostname or "").lower()
                return host
            except (ValueError, AttributeError):
                return ""
        return url_or_domain.lower().split("/")[0].split(":")[0]

    def acquire(
        self,
        url_or_domain: str,
        timeout: float = 30.0,
        tokens: float = 1.0,
    ) -> TokenBucketSlot:
        """Block until tokens and concurrent slots are available, respecting timeout."""
        domain = self._extract_domain(url_or_domain)
        if not domain:
            return TokenBucketSlot(self, "")

        state = self._get_or_create_state(domain)
        start_time = time.monotonic()
        deadline = start_time + timeout

        with state.lock:
            state.waiting_requests += 1
            try:
                while True:
                    # Concurrency check
                    concurrency_ok = (
                        state.max_concurrent <= 0
                        or state.active_requests < state.max_concurrent
                    )
                    # Token bucket consumption check
                    token_ok = state.bucket.try_consume(tokens)

                    if concurrency_ok and token_ok:
                        state.active_requests += 1
                        state.acquired_count += 1
                        wait_duration = time.monotonic() - start_time
                        state.recent_wait_times.append(wait_duration)
                        return TokenBucketSlot(self, domain)

                    # If token was consumed but concurrency failed, refund token
                    if token_ok and not concurrency_ok:
                        state.bucket.tokens = min(
                            state.bucket.capacity,
                            state.bucket.tokens + tokens,
                        )

                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        state.rejected_count += 1
                        raise TimeoutError(
                            f"backpressure timeout on domain '{domain}': "
                            f"active={state.active_requests}/{state.max_concurrent}, "
                            f"tokens={state.bucket.tokens:.2f}/{state.bucket.capacity:.2f}"
                        )

                    # Adaptive sleep interval based on refill requirement
                    needed = max(0.0, tokens - state.bucket.tokens)
                    wait_hint = (needed / state.bucket.refill_rate) if state.bucket.refill_rate > 0 else 0.1
                    sleep_time = max(0.01, min(remaining, wait_hint, 1.0))
                    state.condition.wait(timeout=sleep_time)
            finally:
                state.waiting_requests -= 1

    def _release_domain(self, domain: str) -> None:
        if not domain:
            return
        norm_domain = domain.lower().strip()
        with self._domains_lock:
            state = self._domains.get(norm_domain)
        if state is None:
            return

        with state.lock:
            if state.active_requests > 0:
                state.active_requests -= 1
            state.condition.notify()

    def get_backpressure_telemetry(self, domain: str) -> dict:
        """Export granular telemetry metrics and backpressure score for domain."""
        norm_domain = domain.lower().strip()
        state = self._get_or_create_state(norm_domain)

        score = state.calculate_backpressure_score()
        classification = state.get_state_classification(score)

        with state.lock:
            tokens_avail = state.bucket.refill()
            waits = list(state.recent_wait_times)
            mean_wait_ms = (sum(waits) / len(waits) * 1000.0) if waits else 0.0

            return {
                "domain": norm_domain,
                "tokens_available": round(tokens_avail, 4),
                "capacity": state.bucket.capacity,
                "refill_rate": state.bucket.refill_rate,
                "active_requests": state.active_requests,
                "max_concurrent": state.max_concurrent,
                "wait_queue_depth": state.waiting_requests,
                "mean_wait_time_ms": round(mean_wait_ms, 2),
                "backpressure_score": score,
                "state": classification.value,
                "acquired_count": state.acquired_count,
                "rejected_count": state.rejected_count,
            }


_GLOBAL_TOKEN_BUCKET_LIMITER = DomainTokenBucketLimiter()


def get_token_bucket_limiter() -> DomainTokenBucketLimiter:
    """Return singleton DomainTokenBucketLimiter instance."""
    return _GLOBAL_TOKEN_BUCKET_LIMITER
