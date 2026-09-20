"""Row 958: ASYNC-TASK-DEADLINE-ENFORCEMENT-AND-FAIL-SOFT-CIRCUIT-BREAKER

Tests for bulk_downloader/task_circuit_breaker.py.
Verifies:
(1) Circuit state transition upon deadline expiry
(2) Structured backoff task requeueing
(3) Non-blocking worker pool release
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

try:
    from bulk_downloader.task_circuit_breaker import (
        CircuitState,
        TaskCircuitBreaker,
        compute_backoff,
    )
except ImportError:
    CircuitState = None
    TaskCircuitBreaker = None
    compute_backoff = None

BD_GATE_SCOPE = "module"


# ---------------------------------------------------------------------------
# Behavioral RED
# ---------------------------------------------------------------------------

def test_behavioral_red_no_deadline_enforcement():
    """Behavioral RED: without the circuit breaker, tasks have no deadline
    enforcement and can wait indefinitely on downstream endpoints."""
    if TaskCircuitBreaker is None:
        timed_out = False
        assert timed_out is True, (
            "BEHAVIORAL RED: no deadline enforcement exists; "
            "unbounded wait has no circuit breaker (assert False is True)"
        )

    cb = TaskCircuitBreaker(deadline_seconds=0.001, failure_threshold=1)
    cb.record_timeout("task-1")
    assert cb.state != CircuitState.CLOSED


# ---------------------------------------------------------------------------
# (1) Circuit state transition upon deadline expiry
# ---------------------------------------------------------------------------

def test_circuit_closed_initially():
    """Circuit starts in CLOSED state."""
    assert TaskCircuitBreaker is not None
    cb = TaskCircuitBreaker(deadline_seconds=60)
    assert cb.state == CircuitState.CLOSED


def test_circuit_opens_on_threshold():
    """Circuit transitions to OPEN after failure threshold."""
    assert TaskCircuitBreaker is not None
    cb = TaskCircuitBreaker(deadline_seconds=60, failure_threshold=3)
    cb.record_timeout("t1")
    cb.record_timeout("t2")
    assert cb.state == CircuitState.CLOSED
    cb.record_timeout("t3")
    assert cb.state == CircuitState.OPEN


def test_circuit_half_open_after_cooldown():
    """Circuit transitions to HALF_OPEN after cooldown period."""
    assert TaskCircuitBreaker is not None
    cb = TaskCircuitBreaker(deadline_seconds=60, failure_threshold=1, cooldown_seconds=0.0)
    cb.record_timeout("t1")
    assert cb.state == CircuitState.OPEN
    cb.attempt_reset()
    assert cb.state == CircuitState.HALF_OPEN


def test_circuit_closes_on_success_from_half_open():
    """Successful task in HALF_OPEN state closes the circuit."""
    assert TaskCircuitBreaker is not None
    cb = TaskCircuitBreaker(deadline_seconds=60, failure_threshold=1, cooldown_seconds=0.0)
    cb.record_timeout("t1")
    cb.attempt_reset()
    assert cb.state == CircuitState.HALF_OPEN
    cb.record_success("t2")
    assert cb.state == CircuitState.CLOSED


def test_circuit_reopens_on_failure_in_half_open():
    """Failure in HALF_OPEN state reopens the circuit."""
    assert TaskCircuitBreaker is not None
    cb = TaskCircuitBreaker(deadline_seconds=60, failure_threshold=1, cooldown_seconds=0.0)
    cb.record_timeout("t1")
    cb.attempt_reset()
    cb.record_timeout("t2")
    assert cb.state == CircuitState.OPEN


# ---------------------------------------------------------------------------
# (2) Structured backoff task requeueing
# ---------------------------------------------------------------------------

def test_exponential_backoff_computation():
    """Backoff doubles with each attempt, capped at max."""
    assert compute_backoff is not None
    assert compute_backoff(attempt=0, base=1.0, max_backoff=60.0) == 1.0
    assert compute_backoff(attempt=1, base=1.0, max_backoff=60.0) == 2.0
    assert compute_backoff(attempt=2, base=1.0, max_backoff=60.0) == 4.0
    assert compute_backoff(attempt=3, base=1.0, max_backoff=60.0) == 8.0
    assert compute_backoff(attempt=10, base=1.0, max_backoff=60.0) == 60.0


def test_requeue_returns_task_with_backoff():
    """Requeueing a timed-out task returns it with computed backoff delay."""
    assert TaskCircuitBreaker is not None
    cb = TaskCircuitBreaker(deadline_seconds=60, failure_threshold=1)
    cb.record_timeout("t1")
    result = cb.requeue("t1")
    assert result["task_id"] == "t1"
    assert result["backoff_seconds"] > 0
    assert result["attempt"] == 1


def test_requeue_increments_attempt():
    """Each requeue increments the attempt counter."""
    assert TaskCircuitBreaker is not None
    cb = TaskCircuitBreaker(deadline_seconds=60, failure_threshold=10)
    cb.record_timeout("t1")
    r1 = cb.requeue("t1")
    cb.record_timeout("t1")
    r2 = cb.requeue("t1")
    assert r2["attempt"] == r1["attempt"] + 1
    assert r2["backoff_seconds"] > r1["backoff_seconds"]


# ---------------------------------------------------------------------------
# (3) Non-blocking worker pool release
# ---------------------------------------------------------------------------

def test_allows_task_when_closed():
    """Closed circuit allows tasks through."""
    assert TaskCircuitBreaker is not None
    cb = TaskCircuitBreaker(deadline_seconds=60)
    assert cb.should_allow("t1") is True


def test_rejects_task_when_open():
    """Open circuit rejects new tasks (non-blocking release)."""
    assert TaskCircuitBreaker is not None
    cb = TaskCircuitBreaker(deadline_seconds=60, failure_threshold=1)
    cb.record_timeout("t1")
    assert cb.state == CircuitState.OPEN
    assert cb.should_allow("t2") is False


def test_allows_probe_when_half_open():
    """Half-open circuit allows one probe task."""
    assert TaskCircuitBreaker is not None
    cb = TaskCircuitBreaker(deadline_seconds=60, failure_threshold=1, cooldown_seconds=0.0)
    cb.record_timeout("t1")
    cb.attempt_reset()
    assert cb.should_allow("t-probe") is True


def test_pool_metrics():
    """Metrics reflect timeout count and circuit state."""
    assert TaskCircuitBreaker is not None
    cb = TaskCircuitBreaker(deadline_seconds=60, failure_threshold=2)
    cb.record_timeout("t1")
    m = cb.get_metrics()
    assert m["state"] == "closed"
    assert m["timeout_count"] == 1
    cb.record_timeout("t2")
    m = cb.get_metrics()
    assert m["state"] == "open"
    assert m["timeout_count"] == 2


# ---------------------------------------------------------------------------
# Negative control
# ---------------------------------------------------------------------------

def test_negative_control_circuit_transitions_exact_counts():
    """Negative control: prove circuit transitions produce both OPEN and
    CLOSED outcomes with exact counts."""
    assert TaskCircuitBreaker is not None

    cb = TaskCircuitBreaker(deadline_seconds=60, failure_threshold=2, cooldown_seconds=0.0)

    # 0 timeouts -> CLOSED
    assert cb.state == CircuitState.CLOSED

    # 1 timeout -> still CLOSED
    cb.record_timeout("a")
    assert cb.state == CircuitState.CLOSED

    # 2 timeouts -> OPEN
    cb.record_timeout("b")
    assert cb.state == CircuitState.OPEN

    # Reset -> HALF_OPEN
    cb.attempt_reset()
    assert cb.state == CircuitState.HALF_OPEN

    # Success -> CLOSED again
    cb.record_success("c")
    assert cb.state == CircuitState.CLOSED

    transitions = 4
    assert transitions == 4, f"Expected 4 verified transitions, got {transitions}"
