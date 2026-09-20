"""task_circuit_breaker -- async task deadline enforcement with fail-soft circuit breaker.

Row 958 (v3.66.1585): enforce timeout deadlines on downstream endpoint
calls, transitioning to fail-soft circuit state on repeated timeouts
and rescheduling tasks via exponential backoff.
"""
from __future__ import annotations

from collections import defaultdict
from enum import Enum
from typing import Dict


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


def compute_backoff(
    *, attempt: int, base: float = 1.0, max_backoff: float = 60.0,
) -> float:
    return min(base * (2 ** attempt), max_backoff)


class TaskCircuitBreaker:
    def __init__(
        self,
        *,
        deadline_seconds: float = 60.0,
        failure_threshold: int = 5,
        cooldown_seconds: float = 30.0,
        base_backoff: float = 1.0,
        max_backoff: float = 60.0,
    ) -> None:
        self.deadline_seconds = deadline_seconds
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._base_backoff = base_backoff
        self._max_backoff = max_backoff
        self._state = CircuitState.CLOSED
        self._timeout_count = 0
        self._task_attempts: Dict[str, int] = defaultdict(int)

    @property
    def state(self) -> CircuitState:
        return self._state

    def record_timeout(self, task_id: str) -> None:
        self._timeout_count += 1
        self._task_attempts[task_id] += 1
        if self._timeout_count >= self._failure_threshold:
            self._state = CircuitState.OPEN

    def record_success(self, task_id: str) -> None:
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.CLOSED
            self._timeout_count = 0

    def attempt_reset(self) -> None:
        if self._state == CircuitState.OPEN:
            self._state = CircuitState.HALF_OPEN

    def should_allow(self, task_id: str) -> bool:
        if self._state == CircuitState.CLOSED:
            return True
        if self._state == CircuitState.HALF_OPEN:
            return True
        return False

    def requeue(self, task_id: str) -> dict:
        attempt = self._task_attempts.get(task_id, 1)
        delay = compute_backoff(
            attempt=attempt,
            base=self._base_backoff,
            max_backoff=self._max_backoff,
        )
        return {
            "task_id": task_id,
            "attempt": attempt,
            "backoff_seconds": delay,
        }

    def get_metrics(self) -> dict:
        return {
            "state": self._state.value,
            "timeout_count": self._timeout_count,
            "deadline_seconds": self.deadline_seconds,
        }
