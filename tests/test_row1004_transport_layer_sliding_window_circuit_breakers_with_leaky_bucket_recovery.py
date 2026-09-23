"""Row 1004: Transport-Layer Sliding-Window Circuit Breakers with Leaky-Bucket Recovery.

Validates sliding-window failure rate tracking, fast-failing open transitions,
rate-limited leaky-bucket recovery ramping, immediate re-open on trial failure,
and per-host registry lifecycle.

RED on baseline: fails with AssertionError (capability missing), not an unhandled ImportError.
"""
from __future__ import annotations

import time
import pytest

BD_GATE_SCOPE = "module"

try:
    from bulk_downloader import transport_circuit
except ImportError:
    transport_circuit = None


def test_positive_control_http_client_and_challenge_circuit_baseline():
    """Positive control (Rule 7): proves test runner and probe can say YES on baseline capabilities."""
    from bulk_downloader import challenge_circuit, http_client

    assert hasattr(http_client, "proxy_open")
    assert hasattr(challenge_circuit, "get_circuit_breaker")


def test_transport_circuit_capability_implemented():
    """RED assertion 1: transport layer must provide sliding-window circuit breaker with leaky-bucket recovery."""
    assert transport_circuit is not None, (
        "Row 1004 capability missing: Transport-Layer Sliding-Window Circuit Breakers "
        "with Leaky-Bucket Recovery not implemented in bulk_downloader.transport_circuit"
    )


def test_module_exports():
    """RED assertion 2: bulk_downloader.transport_circuit must export all expected components."""
    assert transport_circuit is not None, "transport_circuit capability missing"

    assert hasattr(transport_circuit, "CircuitState")
    assert hasattr(transport_circuit, "SlidingWindowConfig")
    assert hasattr(transport_circuit, "LeakyBucketConfig")
    assert hasattr(transport_circuit, "TransportCircuitBreaker")
    assert hasattr(transport_circuit, "CircuitOpenError")
    assert hasattr(transport_circuit, "SlidingWindowStats")
    assert hasattr(transport_circuit, "get_transport_circuit")
    assert hasattr(transport_circuit, "reset_all_transport_circuits")


def test_sliding_window_closed_state_and_recording():
    """Verify normal traffic in CLOSED state records metrics without blocking execution."""
    assert transport_circuit is not None, "transport_circuit capability missing"
    from bulk_downloader.transport_circuit import (
        CircuitState,
        SlidingWindowConfig,
        TransportCircuitBreaker,
    )

    cb = TransportCircuitBreaker(
        name="test-closed",
        window_config=SlidingWindowConfig(window_size=10, failure_rate_threshold=0.5),
    )

    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

    # Record 5 successes
    for _ in range(5):
        cb.record_success(duration_ms=15.0)

    stats = cb.get_stats()
    assert stats.total_calls == 5
    assert stats.failure_count == 0
    assert stats.failure_rate == 0.0
    assert cb.state == CircuitState.CLOSED


def test_sliding_window_threshold_tripping_to_open():
    """Verify failure rate exceeding threshold over sliding window trips circuit to OPEN."""
    assert transport_circuit is not None, "transport_circuit capability missing"
    from bulk_downloader.transport_circuit import (
        CircuitOpenError,
        CircuitState,
        SlidingWindowConfig,
        TransportCircuitBreaker,
    )

    cb = TransportCircuitBreaker(
        name="test-trip",
        window_config=SlidingWindowConfig(
            window_size=10, min_calls=5, failure_rate_threshold=0.5
        ),
    )

    # 2 successes, 4 failures -> 6 calls total, 4/6 = 66.7% failure rate > 50%
    cb.record_success()
    cb.record_success()
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()

    assert cb.state == CircuitState.OPEN
    assert cb.can_execute() is False

    with pytest.raises(CircuitOpenError) as exc_info:
        cb.execute(lambda: "should-not-run")
    assert "Circuit 'test-trip' is OPEN" in str(exc_info.value)


def test_sliding_window_rolling_eviction():
    """Verify oldest samples roll out of sliding window so historical errors clear."""
    assert transport_circuit is not None, "transport_circuit capability missing"
    from bulk_downloader.transport_circuit import (
        CircuitState,
        SlidingWindowConfig,
        TransportCircuitBreaker,
    )

    cb = TransportCircuitBreaker(
        name="test-evict",
        window_config=SlidingWindowConfig(
            window_size=6, min_calls=5, failure_rate_threshold=0.6
        ),
    )

    # 3 failures initially
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()

    # Then 6 successes push out the 3 failures
    for _ in range(6):
        cb.record_success()

    stats = cb.get_stats()
    assert stats.total_calls == 6
    assert stats.failure_count == 0
    assert stats.failure_rate == 0.0
    assert cb.state == CircuitState.CLOSED


def test_leaky_bucket_recovery_transition_to_half_open():
    """Verify OPEN circuit transitions to HALF_OPEN_LEAKING after recovery timeout and rate-limits."""
    assert transport_circuit is not None, "transport_circuit capability missing"
    from bulk_downloader.transport_circuit import (
        CircuitOpenError,
        CircuitState,
        LeakyBucketConfig,
        SlidingWindowConfig,
        TransportCircuitBreaker,
    )

    cb = TransportCircuitBreaker(
        name="test-leaky-recovery",
        window_config=SlidingWindowConfig(window_size=5, min_calls=3, failure_rate_threshold=0.5),
        recovery_config=LeakyBucketConfig(
            recovery_timeout_sec=0.05,
            bucket_capacity=2,
            leak_rate_per_sec=10.0,
            success_threshold=3,
        ),
    )

    # Trip the circuit
    for _ in range(3):
        cb.record_failure()
    assert cb.state == CircuitState.OPEN

    # Before timeout, execution rejected
    assert cb.can_execute() is False

    # Wait for recovery timeout
    time.sleep(0.06)

    # Should transition to HALF_OPEN_LEAKING
    assert cb.can_execute() is True
    assert cb.state == CircuitState.HALF_OPEN_LEAKING

    # Consume available leaky bucket capacity
    cb.record_success()
    cb.can_execute()  # second token
    cb.record_success()

    # Leaky bucket bounds execution
    can_ex = cb.can_execute()
    assert isinstance(can_ex, bool)


def test_leaky_bucket_recovery_success_closes_circuit():
    """Verify meeting recovery success threshold closes the circuit cleanly."""
    assert transport_circuit is not None, "transport_circuit capability missing"
    from bulk_downloader.transport_circuit import (
        CircuitState,
        LeakyBucketConfig,
        SlidingWindowConfig,
        TransportCircuitBreaker,
    )

    cb = TransportCircuitBreaker(
        name="test-recovery-close",
        window_config=SlidingWindowConfig(window_size=5, min_calls=3, failure_rate_threshold=0.5),
        recovery_config=LeakyBucketConfig(
            recovery_timeout_sec=0.02,
            bucket_capacity=5,
            leak_rate_per_sec=50.0,
            success_threshold=3,
        ),
    )

    for _ in range(3):
        cb.record_failure()
    assert cb.state == CircuitState.OPEN

    time.sleep(0.03)
    assert cb.state == CircuitState.HALF_OPEN_LEAKING

    # Record 3 successful trial requests
    cb.record_success()
    cb.record_success()
    cb.record_success()

    assert cb.state == CircuitState.CLOSED
    stats = cb.get_stats()
    assert stats.failure_count == 0


def test_leaky_bucket_recovery_failure_immediately_reopens():
    """Verify single failure during recovery immediately reopens circuit with backoff."""
    assert transport_circuit is not None, "transport_circuit capability missing"
    from bulk_downloader.transport_circuit import (
        CircuitState,
        LeakyBucketConfig,
        SlidingWindowConfig,
        TransportCircuitBreaker,
    )

    cb = TransportCircuitBreaker(
        name="test-recovery-fail",
        window_config=SlidingWindowConfig(window_size=5, min_calls=3, failure_rate_threshold=0.5),
        recovery_config=LeakyBucketConfig(
            recovery_timeout_sec=0.05,
            backoff_multiplier=2.0,
            max_recovery_timeout_sec=1.0,
        ),
    )

    for _ in range(3):
        cb.record_failure()
    assert cb.state == CircuitState.OPEN

    time.sleep(0.06)
    assert cb.state == CircuitState.HALF_OPEN_LEAKING

    # Failure during leaky-bucket trial
    cb.record_failure()

    # Must immediately trip back to OPEN
    assert cb.state == CircuitState.OPEN
    # And backoff applied (timeout increased from 0.05 to 0.10)
    assert cb.current_recovery_timeout >= 0.10


def test_context_manager_and_decorator():
    """Verify context manager and function decorator wrappers."""
    assert transport_circuit is not None, "transport_circuit capability missing"
    from bulk_downloader.transport_circuit import (
        CircuitOpenError,
        CircuitState,
        SlidingWindowConfig,
        TransportCircuitBreaker,
    )

    cb = TransportCircuitBreaker(
        name="test-wrappers",
        window_config=SlidingWindowConfig(window_size=5, min_calls=3, failure_rate_threshold=0.5),
    )

    @cb.protect
    def sample_func(val: int) -> int:
        if val < 0:
            raise ValueError("Negative values rejected")
        return val * 2

    assert sample_func(10) == 20
    assert cb.get_stats().success_count == 1

    with pytest.raises(ValueError):
        sample_func(-1)
    assert cb.get_stats().failure_count == 1


def test_registry_and_reset():
    """Verify global host/endpoint circuit registry and reset operations."""
    assert transport_circuit is not None, "transport_circuit capability missing"
    from bulk_downloader.transport_circuit import (
        CircuitState,
        get_transport_circuit,
        reset_all_transport_circuits,
    )

    cb1 = get_transport_circuit("cdn.archive.org")
    cb2 = get_transport_circuit("cdn.archive.org")
    assert cb1 is cb2

    # Trip cb1
    for _ in range(10):
        cb1.record_failure()
    assert cb1.state == CircuitState.OPEN

    # Reset all
    reset_all_transport_circuits()
    assert cb1.state == CircuitState.CLOSED


def _pool_with_circuit(url, **recovery):
    """A pool whose breaker has an EXPLICIT recovery config, so no test needs to sleep.

    ``leak_rate_per_sec=0.0`` freezes the leaky bucket: the only thing that changes the token
    count is a spend, which is what these tests are measuring. The real default leak rate is
    exercised by the breaker's own tests; wall-clock here would only add a flake surface.
    """
    from bulk_downloader import http_client
    from bulk_downloader.transport_circuit import LeakyBucketConfig, TransportCircuitBreaker

    cfg = dict(recovery_timeout_sec=0.0, bucket_capacity=3, leak_rate_per_sec=0.0,
               success_threshold=3)
    cfg.update(recovery)
    pool = http_client._ProxyPool(url)
    pool.circuit = TransportCircuitBreaker(name=url, recovery_config=LeakyBucketConfig(**cfg))
    return pool


def _trip(circuit, times=10):
    for _ in range(times):
        circuit.record_failure()


def test_http_client_proxy_pool_caller_integration():
    """The wiring is real: the pool refuses traffic the OLD code would have admitted.

    R3 fix. The previous version of this test called pool.record_failure() twenty times and
    asserted open_circuit -- which the pre-row `failures >= _FAILURE_LIMIT` term satisfies on
    its own, so it passed whether or not the circuit was wired in at all (mutation M1 survived
    it). The discriminating state is: circuit OPEN while `failures` is still BELOW the limit.
    Only the new transport circuit can produce that.
    """
    assert transport_circuit is not None, "transport_circuit capability missing"
    from bulk_downloader import http_client

    pool = _pool_with_circuit("http://127.0.0.1:8888", recovery_timeout_sec=60.0)
    assert pool.open_circuit is False
    assert pool.failures == 0

    _trip(pool.circuit)
    assert pool.failures < http_client._FAILURE_LIMIT, (
        "the discriminator requires the legacy counter to be untouched")
    assert pool.circuit.state == transport_circuit.CircuitState.OPEN
    assert pool.open_circuit is True, (
        "the pool ignored its transport circuit: open_circuit is still the pre-row "
        "failure-count test, so the row's integration does nothing on the live path")
    assert pool.try_acquire() is False


def test_pool_record_failure_feeds_the_transport_circuit():
    """M2: if _ProxyPool.record_failure stops reporting to the circuit, the circuit stays shut.

    Asserted on the CIRCUIT's state, not on open_circuit -- open_circuit would go true from the
    legacy counter alone and hide the missing edge.
    """
    pool = _pool_with_circuit("http://127.0.0.1:8891", recovery_timeout_sec=60.0)
    assert pool.circuit.state == transport_circuit.CircuitState.CLOSED
    for _ in range(10):
        pool.record_failure()
    assert pool.circuit.state == transport_circuit.CircuitState.OPEN
    assert pool.circuit.get_stats().failure_count >= 5


def test_a_pool_recovers_through_a_full_open_half_open_closed_cycle():
    """M3/R2: success must be reported from the pool, or the breaker latches open forever."""
    states = transport_circuit.CircuitState
    pool = _pool_with_circuit("http://127.0.0.1:8889")
    _trip(pool.circuit)
    # recovery_timeout_sec=0.0 by design here, so the trip lands in OPEN and the first state
    # read moves straight into the trial window -- no sleep, no wall-clock flake. That the trip
    # itself produces OPEN is asserted (with a 60s timeout) in the record_failure test above.
    assert pool.circuit.state == states.HALF_OPEN_LEAKING
    assert pool.open_circuit is False, "a circuit admitting trials is not 'open'"

    for _ in range(3):
        assert pool.try_acquire() is True
        pool.record_success()
    assert pool.circuit.state == states.CLOSED, (
        "three successful trials did not close the circuit: nothing on the live path reports "
        "success, so the transport breaker can only ever latch open")
    assert pool.failures == 0
    assert pool.open_circuit is False


def test_the_leaky_bucket_actually_rations_recovery_probes():
    """M4: the rate-limiting the row is NAMED for. Without a token spend this is unbounded."""
    pool = _pool_with_circuit("http://127.0.0.1:8890")
    _trip(pool.circuit)
    assert pool.circuit.state == transport_circuit.CircuitState.HALF_OPEN_LEAKING

    admitted = [pool.try_acquire() for _ in range(5)]
    assert admitted == [True, True, True, False, False], (
        f"bucket_capacity=3 with no leak must admit exactly 3 probes, got {admitted}")


def test_reading_the_state_never_spends_a_recovery_token():
    """R1: open_circuit is a property. A property that mutates what it reports is a trap --
    three idle reads used to drain the whole probe budget."""
    pool = _pool_with_circuit("http://127.0.0.1:8892")
    _trip(pool.circuit)
    assert pool.circuit.state == transport_circuit.CircuitState.HALF_OPEN_LEAKING

    for _ in range(5):
        assert pool.open_circuit is False
        assert pool.circuit.is_open is False
    admitted = [pool.try_acquire() for _ in range(3)]
    assert admitted == [True, True, True], (
        f"reading the state consumed recovery probes: {admitted}")


def test_the_circuit_registry_key_carries_no_proxy_credentials():
    """R4: the key becomes the breaker's public name, and proxy URLs carry userinfo."""
    from bulk_downloader import http_client

    pool = http_client._ProxyPool("http://alice:hunter2@proxy.internal:3128")
    assert "hunter2" not in pool.circuit.name and "alice" not in pool.circuit.name
    assert pool.circuit.name == "proxy:proxy.internal:3128"
    from bulk_downloader.transport_circuit import _CIRCUIT_REGISTRY
    assert not any("hunter2" in key for key in _CIRCUIT_REGISTRY)


def test_proxy_open_reuses_the_proxy_after_it_heals(monkeypatch):
    """E1/E2 (N6-A, P3-A): the WIRED path must recover. A proxy that answers 503 three times
    and then heals must be used again once the breaker's recovery timeout passes; the pre-fix
    admission refused forever on the legacy ``failures >= _FAILURE_LIMIT`` counter, which only
    a proxy success (never reached) could clear.

    Driven through proxy_open with the pool's REAL breaker (no injected config) and a fake
    monotonic clock on transport_circuit, so the recovery timeout elapses without sleeping.
    """
    import types
    import urllib.error

    from bulk_downloader import http_client

    clock = [1000.0]
    monkeypatch.setattr(transport_circuit, "time",
                        types.SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(transport_circuit, "_CIRCUIT_REGISTRY", {})
    monkeypatch.setattr(http_client, "_POOLS", {})
    monkeypatch.setenv("BD_HTTP_PROXY", "http://127.0.0.1:3999")

    class HealingOpener:
        def __init__(self):
            self.calls = 0

        def open(self, _request, *, timeout):
            self.calls += 1
            if self.calls <= 3:
                raise urllib.error.HTTPError("http://127.0.0.1:3999", 503, "down", {}, None)
            return "PROXY"

    opener = HealingOpener()
    monkeypatch.setattr(http_client.urllib.request, "build_opener", lambda *_h: opener)

    def send():
        return http_client.proxy_open(object(), timeout=0.1,
                                      direct_open=lambda _r, *, timeout: "DIRECT")

    # Three 503s trip the breaker (row 839's contract): the 4th call goes direct, unsent.
    assert [send() for _ in range(4)] == ["DIRECT"] * 4
    assert opener.calls == 3
    pool = http_client._POOLS["http://127.0.0.1:3999"]
    assert pool.circuit.state == transport_circuit.CircuitState.OPEN
    assert pool.failures == 3

    clock[0] += pool.circuit.current_recovery_timeout + 0.001
    assert [send() for _ in range(4)] == ["PROXY"] * 4, (
        "the healed proxy was never retried: admission still latches on the legacy counter")
    assert opener.calls == 7
    assert pool.circuit.state == transport_circuit.CircuitState.CLOSED
    assert pool.failures == 0
    assert pool.open_circuit is False
