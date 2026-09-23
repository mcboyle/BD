"""Row 1004: Transport-Layer Sliding-Window Circuit Breakers with Leaky-Bucket Recovery.

Provides a robust sliding-window circuit breaker implementation tailored for network
transport pools, HTTP connections, and RPC invocations. Features:
- Sliding-window error rate accounting with sample capacity and temporal eviction.
- Controlled leaky-bucket recovery in HALF_OPEN_LEAKING state to prevent thundering herds.
- Instant backoff and re-tripping on trial failure.
- Thread-safe operations, per-host registry, and context manager / decorator interfaces.
"""
from __future__ import annotations

import functools
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Deque, Dict, Optional, Tuple


class CircuitState(str, Enum):
    """Lifecycle states for transport circuit breaker."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN_LEAKING = "HALF_OPEN_LEAKING"


class CircuitOpenError(Exception):
    """Raised when an operation is attempted while the circuit is OPEN or recovery rate-limited."""


@dataclass
class SlidingWindowConfig:
    """Configuration for sliding-window error rate evaluation."""

    window_size: int = 20
    window_duration_sec: float = 60.0
    min_calls: int = 5
    failure_rate_threshold: float = 0.5  # 50% failures trigger OPEN


@dataclass
class LeakyBucketConfig:
    """Configuration for leaky-bucket recovery traffic shaping."""

    recovery_timeout_sec: float = 5.0
    bucket_capacity: int = 3
    leak_rate_per_sec: float = 1.0
    success_threshold: int = 3
    backoff_multiplier: float = 2.0
    max_recovery_timeout_sec: float = 60.0


@dataclass
class SlidingWindowStats:
    """Snapshot metrics of circuit breaker activity."""

    total_calls: int
    success_count: int
    failure_count: int
    failure_rate: float
    avg_duration_ms: float
    state: CircuitState


class TransportCircuitBreaker:
    """Sliding-window circuit breaker with leaky-bucket recovery."""

    def __init__(
        self,
        name: str,
        window_config: Optional[SlidingWindowConfig] = None,
        recovery_config: Optional[LeakyBucketConfig] = None,
    ):
        self.name = name
        self.window_config = window_config or SlidingWindowConfig()
        self.recovery_config = recovery_config or LeakyBucketConfig()

        self._lock = threading.RLock()
        self._state = CircuitState.CLOSED
        self._last_state_change = time.monotonic()
        self._current_recovery_timeout = self.recovery_config.recovery_timeout_sec

        # Sliding window buffer: deque of (timestamp, is_success, duration_ms)
        self._window: Deque[Tuple[float, bool, float]] = deque(
            maxlen=self.window_config.window_size
        )

        # Leaky bucket recovery state
        self._tokens: float = float(self.recovery_config.bucket_capacity)
        self._last_leak_time: float = time.monotonic()
        self._recovery_successes: int = 0

    @property
    def state(self) -> CircuitState:
        """Get current circuit state, updating transitions if timeout elapsed."""
        with self._lock:
            self._evaluate_state_transition()
            return self._state

    @property
    def is_open(self) -> bool:
        """Whether the circuit is currently refusing traffic. A QUERY, with no side effects.

        R1 fix (correctness lens B3, 2026-09-22T06:55Z): ``can_execute()`` is an ADMISSION
        decision -- in HALF_OPEN_LEAKING it spends one leaky-bucket token. Reading it from an
        attribute-shaped property meant that a log line, a retry loop or a debugger burned the
        recovery budget meant for real requests, and the fourth idle read reported the transport
        as open. Anything that only wants to KNOW the state asks here; only a real request
        attempt calls ``can_execute()``.

        HALF_OPEN_LEAKING is deliberately reported as NOT open: the circuit is admitting trial
        traffic, and whether a particular attempt gets a token is ``can_execute()``'s answer.
        """
        with self._lock:
            self._evaluate_state_transition()
            return self._state == CircuitState.OPEN

    @property
    def current_recovery_timeout(self) -> float:
        """Current recovery timeout incorporating any applied backoff."""
        with self._lock:
            return self._current_recovery_timeout

    def _evict_old_samples(self, now: float) -> None:
        """Evict samples older than window_duration_sec."""
        cutoff = now - self.window_config.window_duration_sec
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()

    def _evaluate_state_transition(self) -> None:
        """Check whether OPEN circuit has timed out and can begin leaky-bucket trial."""
        now = time.monotonic()
        if self._state == CircuitState.OPEN:
            if now - self._last_state_change >= self._current_recovery_timeout:
                self._state = CircuitState.HALF_OPEN_LEAKING
                self._last_state_change = now
                self._tokens = float(self.recovery_config.bucket_capacity)
                self._last_leak_time = now
                self._recovery_successes = 0

    def can_execute(self) -> bool:
        """Check if an outbound request is permitted to proceed."""
        with self._lock:
            self._evaluate_state_transition()

            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.OPEN:
                return False

            if self._state == CircuitState.HALF_OPEN_LEAKING:
                now = time.monotonic()
                # Leak tokens over elapsed time
                elapsed = now - self._last_leak_time
                self._tokens = min(
                    float(self.recovery_config.bucket_capacity),
                    self._tokens + elapsed * self.recovery_config.leak_rate_per_sec,
                )
                self._last_leak_time = now

                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
                return False

            return False

    def try_acquire(self) -> bool:
        """Spend one admission against this circuit; the verb says what ``can_execute`` does.

        Same call, named so a reader cannot mistake it for a query (see ``is_open``). Call it
        exactly once per real request attempt.
        """
        return self.can_execute()

    def record_success(self, duration_ms: float = 0.0) -> None:
        """Record a successful execution."""
        with self._lock:
            now = time.monotonic()

            if self._state == CircuitState.HALF_OPEN_LEAKING:
                self._recovery_successes += 1
                if self._recovery_successes >= self.recovery_config.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._last_state_change = now
                    self._current_recovery_timeout = self.recovery_config.recovery_timeout_sec
                    self._window.clear()
                    self._recovery_successes = 0
                return

            if self._state == CircuitState.CLOSED:
                self._evict_old_samples(now)
                self._window.append((now, True, duration_ms))

    def record_failure(self, duration_ms: float = 0.0) -> None:
        """Record a failed execution."""
        with self._lock:
            now = time.monotonic()

            if self._state == CircuitState.HALF_OPEN_LEAKING:
                # Immediate re-trip with exponential backoff
                self._state = CircuitState.OPEN
                self._last_state_change = now
                self._current_recovery_timeout = min(
                    self.recovery_config.max_recovery_timeout_sec,
                    self._current_recovery_timeout * self.recovery_config.backoff_multiplier,
                )
                self._recovery_successes = 0
                return

            if self._state == CircuitState.CLOSED:
                self._evict_old_samples(now)
                self._window.append((now, False, duration_ms))

                if len(self._window) >= self.window_config.min_calls:
                    failures = sum(1 for _, ok, _ in self._window if not ok)
                    rate = failures / len(self._window)
                    if rate >= self.window_config.failure_rate_threshold:
                        self._state = CircuitState.OPEN
                        self._last_state_change = now

    def execute(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute a callable protected by the circuit breaker."""
        if not self.can_execute():
            raise CircuitOpenError(f"Circuit '{self.name}' is OPEN or rate-limited in recovery")

        t0 = time.monotonic()
        try:
            res = func(*args, **kwargs)
            self.record_success(duration_ms=(time.monotonic() - t0) * 1000.0)
            return res
        except Exception:
            self.record_failure(duration_ms=(time.monotonic() - t0) * 1000.0)
            raise

    def get_stats(self) -> SlidingWindowStats:
        """Compute current metrics across the sliding window."""
        with self._lock:
            now = time.monotonic()
            self._evict_old_samples(now)
            total = len(self._window)
            successes = sum(1 for _, ok, _ in self._window if ok)
            failures = total - successes
            rate = (failures / total) if total > 0 else 0.0
            avg_dur = (
                (sum(dur for _, _, dur in self._window) / total) if total > 0 else 0.0
            )
            return SlidingWindowStats(
                total_calls=total,
                success_count=successes,
                failure_count=failures,
                failure_rate=rate,
                avg_duration_ms=avg_dur,
                state=self._state,
            )

    def reset(self) -> None:
        """Reset the circuit breaker to clean CLOSED state."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._last_state_change = time.monotonic()
            self._current_recovery_timeout = self.recovery_config.recovery_timeout_sec
            self._window.clear()
            self._tokens = float(self.recovery_config.bucket_capacity)
            self._recovery_successes = 0

    def protect(self, func: Callable[..., Any]) -> Callable[..., Any]:
        """Decorator to guard function calls with the circuit breaker."""
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return self.execute(func, *args, **kwargs)

        return wrapper

    def __enter__(self) -> "TransportCircuitBreaker":
        if not self.can_execute():
            raise CircuitOpenError(f"Circuit '{self.name}' is OPEN")
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        if exc_type is not None:
            self.record_failure()
            return False
        self.record_success()
        return False


_REGISTRY_LOCK = threading.Lock()
_CIRCUIT_REGISTRY: Dict[str, TransportCircuitBreaker] = {}


def get_transport_circuit(
    name: str, window_config: Optional[SlidingWindowConfig] = None
) -> TransportCircuitBreaker:
    """Retrieve or register a singleton TransportCircuitBreaker instance.

    ``window_config`` applies only when this call registers the breaker.
    """
    with _REGISTRY_LOCK:
        if name not in _CIRCUIT_REGISTRY:
            _CIRCUIT_REGISTRY[name] = TransportCircuitBreaker(
                name=name, window_config=window_config
            )
        return _CIRCUIT_REGISTRY[name]


def reset_all_transport_circuits() -> None:
    """Reset all registered circuit breakers to closed state."""
    with _REGISTRY_LOCK:
        for cb in _CIRCUIT_REGISTRY.values():
            cb.reset()
