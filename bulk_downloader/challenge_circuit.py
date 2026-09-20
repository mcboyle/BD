"""Verification challenge timeout and fail-soft circuit breaker (Row 940).

Enforces configurable timeout ceiling (default 60s) on verification challenges,
tripping fail-soft to re-queue tasks with exponential backoff and releasing workers
to prevent fleet worker stalls. Zero site logins touched (Fleet Rule 21).
"""
from __future__ import annotations

import importlib
import importlib.util
import math
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

DEFAULT_TIMEOUT_CEILING: float = 60.0
DEFAULT_BASE_BACKOFF: float = 30.0
DEFAULT_BACKOFF_FACTOR: float = 2.0
DEFAULT_MAX_BACKOFF: float = 3600.0
DEFAULT_RECOVERY_TIMEOUT: float = 120.0


class _ChallengeTimeout(Exception):
    """Raised by _run_with_ceiling when the challenge is still running at the ceiling."""


def _run_with_ceiling(fn: Callable[..., Any], args: tuple, kwargs: dict, ceiling: float) -> Any:
    """Run fn on a daemon thread; return its result or raise _ChallengeTimeout at `ceiling`.

    The thread is NOT joined past the ceiling -- a challenge that hangs indefinitely keeps
    its own daemon thread and the calling worker returns after at most `ceiling` seconds.
    Exceptions raised by fn are re-raised in the caller when fn finishes inside the ceiling.
    """
    box: Dict[str, Any] = {}

    def _target() -> None:
        try:
            box["result"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - surfaced to the caller below
            box["error"] = exc

    t = threading.Thread(target=_target, name="challenge-circuit", daemon=True)
    t.start()
    t.join(timeout=ceiling)
    if t.is_alive():
        raise _ChallengeTimeout(f"challenge still running after {ceiling:.3f}s")
    if "error" in box:
        raise box["error"]
    return box["result"]


STATE_CLOSED = "closed"
STATE_OPEN = "open"
STATE_HALF_OPEN = "half_open"


def calculate_backoff(
    attempt: int,
    base_delay: float = DEFAULT_BASE_BACKOFF,
    factor: float = DEFAULT_BACKOFF_FACTOR,
    max_delay: float = DEFAULT_MAX_BACKOFF,
) -> float:
    """Calculate exponential backoff delay in seconds for task re-queueing."""
    if attempt <= 1:
        return min(float(base_delay), float(max_delay))
    delay = float(base_delay) * (float(factor) ** (attempt - 1))
    return min(delay, float(max_delay))


def get_timeout_ceiling(runner: Any = None, default: float = DEFAULT_TIMEOUT_CEILING) -> float:
    """Read configured timeout ceiling from env, config or runner."""
    raw = os.environ.get("CHALLENGE_TIMEOUT_SECONDS", "").strip()
    if raw:
        parsed = _positive_seconds(raw)
        if parsed is not None:
            return parsed
        emit_circuit_event("challenge_circuit_config", f"ignoring CHALLENGE_TIMEOUT_SECONDS={raw!r}: not a positive number", runner=runner)
    _gc = sys.modules.get("bulk_downloader.global_config")
    if _gc is None and importlib.util.find_spec("bulk_downloader.global_config") is not None:
        _gc = importlib.import_module("bulk_downloader.global_config")
    getter = getattr(_gc, "get", None) if _gc is not None else None
    if callable(getter):
        val = getter("challenge_timeout_seconds") or getter("verification_timeout_seconds")
        parsed = _positive_seconds(val)
        if parsed is not None:
            return parsed
        if val is not None:
            emit_circuit_event("challenge_circuit_config", f"ignoring global_config timeout {val!r}: not a positive number", runner=runner)
    if runner is not None:
        cfg = getattr(runner, "config", None)
        if isinstance(cfg, dict):
            val = cfg.get("challenge_timeout_seconds") or cfg.get("verification_timeout_seconds")
            parsed = _positive_seconds(val)
            if parsed is not None:
                return parsed
            if val is not None:
                emit_circuit_event("challenge_circuit_config", f"ignoring runner.config timeout {val!r}: not a positive number", runner=runner)
    return default


_NUMBER_RE = re.compile(r"[0-9]+(?:\.[0-9]*)?|\.[0-9]+")


def _positive_seconds(val: Any) -> Optional[float]:
    """A configured ceiling as float, or None when it is absent or not a positive number."""
    if isinstance(val, bool) or val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val) if math.isfinite(val) and val > 0 else None
    text = str(val).strip()
    if not _NUMBER_RE.fullmatch(text):
        return None
    number = float(text)
    if not math.isfinite(number):
        return None
    return number if number > 0 else None


def emit_circuit_event(
    kind: str,
    message: str,
    extra: Optional[Dict[str, Any]] = None,
    runner: Any = None,
) -> None:
    """Emit structured diagnostic logging via runner.log_event with stderr fallback."""
    suffix = ""
    if runner is not None and hasattr(runner, "log_event"):
        try:
            runner.log_event(kind, message, extra=extra)
            return
        except Exception as exc:  # fail open to stderr, and say why the runner log was skipped
            suffix = f" (runner.log_event failed: {type(exc).__name__}: {str(exc)[:80]})"
    sys.stderr.write(f"  [{kind}] {message}{suffix}\n")


def _requeue(queue: Any, task: Any, runner: Any) -> bool:
    """Re-queue task for its backoff retry. True = re-queued (or no queue: the caller re-queues from
    the result); False only when queue.put raised, with a diagnostic event saying why."""
    if queue is None or not hasattr(queue, "put"):
        return True
    try:
        queue.put(task)
    except Exception as exc:
        emit_circuit_event("challenge_circuit_requeue_failed", f"requeue failed: {type(exc).__name__}: {str(exc)[:100]}", extra={"task": repr(task)[:200]}, runner=runner)
        return False
    return True


@dataclass
class ChallengeCircuitResult:
    """Outcome of a challenge execution through the circuit breaker."""
    ok: bool
    tripped: bool
    requeued: bool
    circuit_state: str
    elapsed_seconds: float
    timeout_ceiling: float
    backoff_seconds: float
    attempt: int
    host: str
    reason: Optional[str] = None
    result: Any = None
    extra: Dict[str, Any] = field(default_factory=dict)


class ChallengeCircuitBreaker:
    """Per-host circuit breaker for verification challenge worker stall prevention."""

    def __init__(
        self,
        host: str = "default",
        timeout_ceiling: float = DEFAULT_TIMEOUT_CEILING,
        recovery_timeout: float = DEFAULT_RECOVERY_TIMEOUT,
        base_backoff: float = DEFAULT_BASE_BACKOFF,
        backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
        max_backoff: float = DEFAULT_MAX_BACKOFF,
    ):
        self.host = host
        self.timeout_ceiling = timeout_ceiling
        self.recovery_timeout = recovery_timeout
        self.base_backoff = base_backoff
        self.backoff_factor = backoff_factor
        self.max_backoff = max_backoff

        self.state = STATE_CLOSED
        self.failure_count = 0
        self.opened_at: Optional[float] = None
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        """True if circuit is closed or ready for half-open probe."""
        with self._lock:
            if self.state == STATE_CLOSED:
                return True
            if self.state == STATE_OPEN:
                now = time.time()
                if self.opened_at and (now - self.opened_at) >= self.recovery_timeout:
                    self.state = STATE_HALF_OPEN
                    return True
                return False
            return True  # half_open

    def trip(
        self,
        reason: str,
        elapsed: float,
        attempt: int,
        runner: Any = None,
        task: Any = None,
    ) -> float:
        """Trip circuit to OPEN, compute backoff, and emit diagnostic event."""
        with self._lock:
            self.state = STATE_OPEN
            self.opened_at = time.time()
            self.failure_count += 1
            backoff = calculate_backoff(
                attempt,
                base_delay=self.base_backoff,
                factor=self.backoff_factor,
                max_delay=self.max_backoff,
            )

        msg = (
            f"Verification challenge circuit tripped to OPEN on {self.host}: "
            f"reason={reason} elapsed={elapsed:.2f}s attempt={attempt} "
            f"requeue_backoff={backoff:.1f}s"
        )
        extra = {
            "host": self.host,
            "circuit_state": STATE_OPEN,
            "reason": reason,
            "elapsed_seconds": elapsed,
            "timeout_ceiling": self.timeout_ceiling,
            "attempt": attempt,
            "backoff_seconds": backoff,
            "requeued": True,
            "task_id": getattr(task, "id", None) if task else None,
        }
        emit_circuit_event("challenge_circuit_trip", msg, extra=extra, runner=runner)
        return backoff

    def record_success(self, runner: Any = None) -> None:
        """Record successful challenge resolution, closing circuit if half-open."""
        with self._lock:
            was_half_open = (self.state == STATE_HALF_OPEN)
            self.state = STATE_CLOSED
            self.failure_count = 0
            self.opened_at = None

        if was_half_open:
            msg = f"Verification challenge circuit on {self.host} recovered to CLOSED"
            extra = {"host": self.host, "circuit_state": STATE_CLOSED}
            emit_circuit_event("challenge_circuit_close", msg, extra=extra, runner=runner)

    def execute(
        self,
        fn: Callable[..., Any],
        *args: Any,
        attempt: int = 1,
        timeout: Optional[float] = None,
        runner: Any = None,
        queue: Any = None,
        task: Any = None,
        **kwargs: Any,
    ) -> ChallengeCircuitResult:
        """Execute a challenge operation under timeout ceiling and fail-soft circuit breaker.

        Non-blocking: if circuit is open or timeout expires, trips fail-soft,
        calculates exponential backoff, optionally re-queues task, and releases worker.
        """
        ceiling = timeout if timeout is not None else get_timeout_ceiling(runner, self.timeout_ceiling)

        # 1. Fast-fail check if circuit is OPEN
        with self._lock:
            if self.state == STATE_OPEN:
                now = time.time()
                if self.opened_at and (now - self.opened_at) >= self.recovery_timeout:
                    self.state = STATE_HALF_OPEN
                else:
                    backoff = calculate_backoff(
                        attempt,
                        base_delay=self.base_backoff,
                        factor=self.backoff_factor,
                        max_delay=self.max_backoff,
                    )
                    extra = {
                        "host": self.host,
                        "circuit_state": STATE_OPEN,
                        "reason": "circuit_open_fast_fail",
                        "attempt": attempt,
                        "backoff_seconds": backoff,
                        "requeued": True,
                    }
                    msg = (
                        f"Challenge rejected on {self.host}: circuit is OPEN, "
                        f"fast-failing attempt={attempt} (backoff={backoff:.1f}s)"
                    )
                    emit_circuit_event("challenge_circuit_reject", msg, extra=extra, runner=runner)
                    requeued = _requeue(queue, task, runner)
                    extra["requeued"] = requeued
                    return ChallengeCircuitResult(
                        ok=False,
                        tripped=True,
                        requeued=requeued,
                        circuit_state=STATE_OPEN,
                        elapsed_seconds=0.0,
                        timeout_ceiling=ceiling,
                        backoff_seconds=backoff,
                        attempt=attempt,
                        host=self.host,
                        reason="circuit_open_fast_fail",
                        extra=extra,
                    )

        # 2. Execute under timeout ceiling. The challenge runs on a daemon thread and the
        # caller waits at most `ceiling`: a ThreadPoolExecutor with-block would
        # shutdown(wait=True) on exit and join the hung challenge, so execute() could
        # not release the worker at the ceiling (correctness REFUTE E1).
        start_time = time.time()
        try:
            res = _run_with_ceiling(fn, args, kwargs, ceiling)

            elapsed = time.time() - start_time
            self.record_success(runner=runner)
            return ChallengeCircuitResult(
                ok=True,
                tripped=False,
                requeued=False,
                circuit_state=STATE_CLOSED,
                elapsed_seconds=elapsed,
                timeout_ceiling=ceiling,
                backoff_seconds=0.0,
                attempt=attempt,
                host=self.host,
                result=res,
            )

        except (_ChallengeTimeout, TimeoutError):
            elapsed = time.time() - start_time
            backoff = self.trip(
                reason="timeout_expired",
                elapsed=elapsed,
                attempt=attempt,
                runner=runner,
                task=task,
            )
            requeued = _requeue(queue, task, runner)
            return ChallengeCircuitResult(
                ok=False,
                tripped=True,
                requeued=requeued,
                circuit_state=STATE_OPEN,
                elapsed_seconds=elapsed,
                timeout_ceiling=ceiling,
                backoff_seconds=backoff,
                attempt=attempt,
                host=self.host,
                reason="timeout_expired",
            )
        except Exception as exc:
            elapsed = time.time() - start_time
            with self._lock:
                if self.state == STATE_HALF_OPEN:
                    self.state = STATE_OPEN
                    self.opened_at = time.time()
            return ChallengeCircuitResult(
                ok=False,
                tripped=False,
                requeued=False,
                circuit_state=self.state,
                elapsed_seconds=elapsed,
                timeout_ceiling=ceiling,
                backoff_seconds=0.0,
                attempt=attempt,
                host=self.host,
                reason=f"execution_error:{type(exc).__name__}:{str(exc)[:100]}",
            )


# ---------------------------------------------------------------------------
# Global Registry & Helper Functions
# ---------------------------------------------------------------------------

_REGISTRY: Dict[str, ChallengeCircuitBreaker] = {}
_REGISTRY_LOCK = threading.Lock()


def _normalize_host(url_or_host: str) -> str:
    if not url_or_host:
        return "default"
    if "://" in url_or_host:
        try:
            hostname = urlparse(url_or_host).hostname
        except ValueError:  # malformed netloc (e.g. unbalanced IPv6 brackets): key on the raw string
            hostname = None
        if hostname:
            return hostname.lower()
    return url_or_host.strip().lower() or "default"


def get_circuit_breaker(
    host_or_url: str = "default",
    timeout_ceiling: float = DEFAULT_TIMEOUT_CEILING,
) -> ChallengeCircuitBreaker:
    """Retrieve or create a ChallengeCircuitBreaker instance for the given host."""
    norm = _normalize_host(host_or_url)
    with _REGISTRY_LOCK:
        if norm not in _REGISTRY:
            _REGISTRY[norm] = ChallengeCircuitBreaker(
                host=norm,
                timeout_ceiling=timeout_ceiling,
            )
        return _REGISTRY[norm]


def reset_all_circuits() -> None:
    """Reset all circuit breakers in the registry to closed."""
    with _REGISTRY_LOCK:
        for breaker in _REGISTRY.values():
            with breaker._lock:
                breaker.state = STATE_CLOSED
                breaker.failure_count = 0
                breaker.opened_at = None
        _REGISTRY.clear()
