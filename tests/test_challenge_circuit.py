"""Acceptance and unit tests for Row 940: VERIFICATION-CHALLENGE-TIMEOUT-AND-FAIL-SOFT-CIRCUIT-BREAKER.

Register Row 940:
  SCOPE: enforce configurable timeout ceiling (default 60s) in bulk_downloader/challenge_circuit.py,
  tripping fail-soft to re-queue tasks with exponential backoff and releasing workers;
  0 site logins touched (Rule 21).
  ACCEPTANCE: tests/test_challenge_circuit.py verifying:
    (1) circuit trip upon timeout expiration,
    (2) structured diagnostic logging,
    (3) non-blocking worker continuation.
"""
from __future__ import annotations

import importlib
import time

BD_GATE_SCOPE = "module"


def _get_circuit_module():
    try:
        return importlib.import_module("bulk_downloader.challenge_circuit")
    except ImportError:
        return None


class _BaseFallbackResult:
    """Simulates unmodified base behavior where circuit breaker is absent."""
    def __init__(self, ok=True, result=None):
        self.ok = ok
        self.tripped = False
        self.requeued = False
        self.circuit_state = "closed"
        self.elapsed_seconds = 0.0
        self.timeout_ceiling = 60.0
        self.backoff_seconds = 0.0
        self.attempt = 1
        self.host = "default"
        self.reason = None
        self.result = result


class _BaseFallbackCircuit:
    """Simulates unmodified base behavior: no timeout ceiling, no circuit tripping."""
    def __init__(self, host="default", timeout_ceiling=60.0):
        self.host = host
        self.timeout_ceiling = timeout_ceiling
        self.state = "closed"
        self.failure_count = 0

    def is_available(self):
        return True

    def execute(self, fn, *args, attempt=1, timeout=None, runner=None, **kwargs):
        # On unmodified base, challenges execute without timeout ceiling or circuit breaker
        res = fn(*args, **kwargs)
        return _BaseFallbackResult(ok=True, result=res)

    def calculate_backoff(self, attempt, **kwargs):
        return 0.0


def _get_breaker(host="default", timeout_ceiling=60.0):
    mod = _get_circuit_module()
    if mod is not None and hasattr(mod, "ChallengeCircuitBreaker"):
        return mod.ChallengeCircuitBreaker(host=host, timeout_ceiling=timeout_ceiling)
    return _BaseFallbackCircuit(host=host, timeout_ceiling=timeout_ceiling)


def _calc_backoff(attempt, **kwargs):
    mod = _get_circuit_module()
    if mod is not None and hasattr(mod, "calculate_backoff"):
        return mod.calculate_backoff(attempt, **kwargs)
    return 0.0


class _FakeRunner:
    def __init__(self, config=None):
        self.config = config or {}
        self.events = []

    def log_event(self, kind, message, extra=None):
        self.events.append((kind, message, extra))


# ---------------------------------------------------------------------------
# Acceptance Criterion 1: Circuit Trip Upon Timeout Expiration
# ---------------------------------------------------------------------------

def test_circuit_trip_upon_timeout_expiration():
    """Challenge taking longer than timeout ceiling must trip circuit to OPEN."""
    breaker = _get_breaker(host="test-host.com", timeout_ceiling=0.1)

    def slow_challenge():
        time.sleep(0.3)
        return "solved"

    runner = _FakeRunner()
    result = breaker.execute(slow_challenge, attempt=1, timeout=0.1, runner=runner)

    assert result.tripped is True, "circuit must trip upon timeout expiration"
    assert result.ok is False, "timed out challenge result must not be ok"
    assert result.circuit_state == "open", "circuit state must be open after trip"
    assert breaker.state == "open", "breaker state must transition to open"
    assert "timeout" in str(result.reason).lower()


def test_subsequent_attempts_fast_fail_when_circuit_open():
    """While circuit is OPEN, new challenge attempts must fast-fail without waiting."""
    breaker = _get_breaker(host="fast-fail.com", timeout_ceiling=0.1)

    def slow_challenge():
        time.sleep(0.3)
        return "solved"

    # Trip the circuit
    breaker.execute(slow_challenge, attempt=1, timeout=0.1)

    # Next attempt should fast-fail without sleeping 0.3s
    start_t = time.time()
    result2 = breaker.execute(slow_challenge, attempt=2, timeout=0.1)
    duration = time.time() - start_t

    assert result2.tripped is True
    assert result2.circuit_state == "open"
    assert duration < 0.1, f"fast-fail must return immediately, took {duration:.3f}s"


# ---------------------------------------------------------------------------
# Acceptance Criterion 2: Structured Diagnostic Logging
# ---------------------------------------------------------------------------

def test_structured_diagnostic_logging_on_circuit_trip():
    """Timeout trip must emit structured diagnostic logs with required telemetry fields."""
    breaker = _get_breaker(host="telemetry-site.com", timeout_ceiling=0.1)
    runner = _FakeRunner()

    def slow_challenge():
        time.sleep(0.25)
        return "challenge_result"

    result = breaker.execute(slow_challenge, attempt=1, timeout=0.1, runner=runner)

    assert result.tripped is True
    assert len(runner.events) >= 1, "must emit structured diagnostic log event"

    trip_events = [e for e in runner.events if e[0] in ("challenge_circuit_trip", "challenge_circuit_timeout")]
    assert len(trip_events) >= 1, f"expected challenge_circuit_trip event, got: {runner.events}"

    kind, message, extra = trip_events[0]
    assert extra is not None, "event must carry structured extra metadata"
    assert extra.get("host") == "telemetry-site.com"
    assert extra.get("circuit_state") == "open"
    assert extra.get("requeued") is True
    assert extra.get("backoff_seconds", 0) > 0
    assert "telemetry-site.com" in message


def test_structured_logging_fails_open_to_stderr_when_runner_raises(capsys):
    """If runner.log_event raises, logging must fail open to stderr without crashing."""
    class _BrokenRunner:
        def log_event(self, *a, **kw):
            raise RuntimeError("telemetry connection dropped")

    breaker = _get_breaker(host="broken-log.com", timeout_ceiling=0.1)

    def slow_challenge():
        time.sleep(0.25)
        return "challenge_result"

    result = breaker.execute(slow_challenge, attempt=1, timeout=0.1, runner=_BrokenRunner())
    assert result.tripped is True

    captured = capsys.readouterr()
    assert "challenge_circuit" in captured.err or "broken-log.com" in captured.err


# ---------------------------------------------------------------------------
# Acceptance Criterion 3: Non-blocking Worker Continuation & Task Re-queue
# ---------------------------------------------------------------------------

def test_non_blocking_worker_continuation():
    """Worker processing queue tasks must not hang when one task times out."""
    breaker = _get_breaker(host="queue-worker.com", timeout_ceiling=0.1)
    runner = _FakeRunner()

    queue_tasks = [
        {"id": "task-1-stuck", "type": "challenge", "sleep": 3.0},
        {"id": "task-2-fast", "type": "normal", "sleep": 0.01},
        {"id": "task-3-fast", "type": "normal", "sleep": 0.01},
    ]

    requeued_tasks = []
    completed_tasks = []
    stuck_elapsed = []

    start_total = time.time()
    for task in queue_tasks:
        if task["type"] == "challenge":
            def challenge_action():
                time.sleep(task["sleep"])
                return "done"

            res = breaker.execute(challenge_action, attempt=1, timeout=0.1, runner=runner)
            stuck_elapsed.append(res.elapsed_seconds)
            if res.requeued:
                requeued_tasks.append((task["id"], res.backoff_seconds))
            else:
                completed_tasks.append(task["id"])
        else:
            time.sleep(task["sleep"])
            completed_tasks.append(task["id"])

    total_time = time.time() - start_total

    # Worker must complete all tasks non-blocking: task-1 hangs for 3.0s but the ceiling is
    # 0.1s, so the whole loop finishes far below the hang (an execute() that joins the stuck
    # challenge takes >= 3.0s here and is RED; correctness REFUTE E2).
    assert total_time < 0.35, f"worker stalled! Total time took {total_time:.3f}s"
    assert stuck_elapsed[0] <= 0.1 + 0.15, f"execute() did not return at the ceiling: {stuck_elapsed[0]:.3f}s"
    assert len(requeued_tasks) == 1, "stuck task must be re-queued"
    assert requeued_tasks[0][0] == "task-1-stuck"
    assert requeued_tasks[0][1] > 0, "must assign positive exponential backoff"
    assert completed_tasks == ["task-2-fast", "task-3-fast"], "remaining tasks must complete"


def test_indefinite_hang_releases_worker_at_ceiling():
    """A challenge that never returns (register: 'hang indefinitely') must not hold the worker."""
    import threading

    breaker = _get_breaker(host="hang-forever.com", timeout_ceiling=0.1)
    never = threading.Event()
    start = time.time()
    res = breaker.execute(never.wait, attempt=1, timeout=0.1, runner=_FakeRunner())
    elapsed = time.time() - start
    never.set()  # release the daemon thread after the assertion window

    assert res.tripped is True and res.requeued is True
    assert res.reason == "timeout_expired"
    assert elapsed < 0.5, f"execute() blocked on an indefinite challenge for {elapsed:.3f}s"
    assert res.elapsed_seconds <= 0.1 + 0.15, f"elapsed_seconds reports the hang, not the ceiling: {res.elapsed_seconds:.3f}"


# ---------------------------------------------------------------------------
# Exponential Backoff & Configurable Timeout
# ---------------------------------------------------------------------------

def test_exponential_backoff_calculation():
    """Exponential backoff doubles per attempt and respects ceiling."""
    b1 = _calc_backoff(1, base_delay=30.0, factor=2.0, max_delay=300.0)
    b2 = _calc_backoff(2, base_delay=30.0, factor=2.0, max_delay=300.0)
    b3 = _calc_backoff(3, base_delay=30.0, factor=2.0, max_delay=300.0)
    b4 = _calc_backoff(4, base_delay=30.0, factor=2.0, max_delay=300.0)
    b5 = _calc_backoff(10, base_delay=30.0, factor=2.0, max_delay=300.0)

    assert b1 == 30.0
    assert b2 == 60.0
    assert b3 == 120.0
    assert b4 == 240.0
    assert b5 == 300.0, "must cap at max_delay"


def test_configurable_timeout_ceiling_via_env(monkeypatch):
    """Timeout ceiling can be configured via CHALLENGE_TIMEOUT_SECONDS."""
    monkeypatch.setenv("CHALLENGE_TIMEOUT_SECONDS", "45.0")
    mod = _get_circuit_module()
    if mod is not None and hasattr(mod, "get_timeout_ceiling"):
        assert mod.get_timeout_ceiling() == 45.0
    else:
        assert False, "get_timeout_ceiling must be implemented"


# ---------------------------------------------------------------------------
# Negative Control: Fast Challenge Completes Normally Without Tripping
# ---------------------------------------------------------------------------

def test_negative_control_fast_success_keeps_circuit_closed():
    """Negative control: task completing within timeout ceiling must NOT trip circuit."""
    breaker = _get_breaker(host="healthy-site.com", timeout_ceiling=1.0)

    def fast_challenge():
        return "immediate_success"

    runner = _FakeRunner()
    result = breaker.execute(fast_challenge, attempt=1, timeout=1.0, runner=runner)

    assert result.ok is True, "fast challenge must succeed"
    assert result.tripped is False, "circuit must NOT trip on success"
    assert result.requeued is False, "successful task must NOT be re-queued"
    assert result.circuit_state == "closed", "circuit state must remain closed"
    assert breaker.state == "closed"
    assert breaker.failure_count == 0, "failure count must remain 0"
    assert result.result == "immediate_success"


def test_negative_control_recovery_after_cooldown():
    """After cooldown duration, circuit transitions to half-open and closes on success."""
    breaker = _get_breaker(host="recovery-site.com", timeout_ceiling=0.1)
    if hasattr(breaker, "recovery_timeout"):
        breaker.recovery_timeout = 0.1  # short cooldown for testing

    def slow():
        time.sleep(0.2)
        return "slow"

    def fast():
        return "recovered"

    # Trip
    breaker.execute(slow, attempt=1, timeout=0.1)
    assert breaker.state == "open"

    # Wait for cooldown
    time.sleep(0.15)

    # Recovery attempt
    result = breaker.execute(fast, attempt=2, timeout=0.1)
    assert result.ok is True
    assert result.tripped is False
    assert breaker.state == "closed", "circuit must close after successful probe"
