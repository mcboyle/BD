"""Strict Lifecycle Asynchronous Testing Suite Standard (Row 1069).

Provides:
- Strict asynchronous test lifecycle standards (function loop scope, strict mode, leak detection).
- Isolated event loop execution per async test suite/case.
- Automated detection, cancellation, and draining of unawaited background tasks.
- Timeout enforcement and resource protection.
- Decorator `@strict_async_test` for hermetic async test execution.
- Operational reporting for test harness and dev suite.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Dict, Optional, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class AsyncTestStandard:
    """Configuration standard for strict asynchronous testing."""

    asyncio_mode: str = "strict"
    loop_scope: str = "function"
    task_timeout_seconds: float = 10.0
    strict_task_drain: bool = True
    leak_detection: bool = True
    mock_restoration_check: bool = True

    def validate(self) -> None:
        if self.asyncio_mode not in ("strict", "auto"):
            raise ValueError(
                f"Unsupported asyncio_mode: {self.asyncio_mode!r}; strict standard requires 'strict'"
            )
        if self.loop_scope != "function":
            raise ValueError(
                f"Unsupported loop_scope: {self.loop_scope!r}; strict standard requires 'function'"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asyncio_mode": self.asyncio_mode,
            "loop_scope": self.loop_scope,
            "task_timeout_seconds": self.task_timeout_seconds,
            "strict_task_drain": self.strict_task_drain,
            "leak_detection": self.leak_detection,
            "mock_restoration_check": self.mock_restoration_check,
        }


@dataclass
class AsyncLifecycleReport:
    """Report detailing execution and leak analysis of an async test."""

    test_name: str
    success: bool
    dangling_tasks_detected: int = 0
    tasks_drained: int = 0
    unrestored_mocks_detected: int = 0
    duration_ms: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "test_name": self.test_name,
            "success": self.success,
            "dangling_tasks_detected": self.dangling_tasks_detected,
            "tasks_drained": self.tasks_drained,
            "unrestored_mocks_detected": self.unrestored_mocks_detected,
            "duration_ms": round(self.duration_ms, 2),
            "error": self.error,
        }


class AsyncTestLifecycleManager:
    """Manages isolated loop lifecycles and background task draining for async tests."""

    def __init__(self, standard: Optional[AsyncTestStandard] = None) -> None:
        self.standard = standard or AsyncTestStandard()

    def run_isolated(
        self,
        coro: Coroutine[Any, Any, Any],
        timeout_seconds: Optional[float] = None,
        test_name: str = "anonymous_test",
    ) -> Tuple[Any, AsyncLifecycleReport]:
        """Execute a coroutine in a fresh, isolated event loop with leak detection and task draining."""
        self.standard.validate()
        effective_timeout = timeout_seconds if timeout_seconds is not None else self.standard.task_timeout_seconds

        # Guard against invocation from an already-running event loop in current thread
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is not None and running_loop.is_running():
            raise RuntimeError(
                "Cannot run isolated async test while another event loop is running in the current thread"
            )

        # Preserve caller's existing event loop
        try:
            policy = asyncio.get_event_loop_policy()
            local_state = getattr(policy, "_local", None)
            prior_loop = getattr(local_state, "_loop", None) if local_state else None
        except Exception:
            prior_loop = None

        # Track active mock patches for mock_restoration_check
        initial_active_mocks: Set[Any] = set()
        if self.standard.mock_restoration_check:
            try:
                from unittest.mock import _patch
                active: Any = getattr(_patch, "_active_patches", set())
                initial_active_mocks = set(active)
            except Exception:
                pass

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        start_time = time.monotonic()
        result: Any = None
        error_msg: Optional[str] = None
        dangling_count = 0
        drained_count = 0
        unrestored_mocks_count = 0
        success = False

        try:
            # Wrap in wait_for for strict timeout enforcement
            wrapped = asyncio.wait_for(coro, timeout=effective_timeout)
            result = loop.run_until_complete(wrapped)
            success = True
        except asyncio.TimeoutError as exc:
            error_msg = f"Async test timed out after {effective_timeout}s"
            raise TimeoutError(error_msg) from exc
        except Exception as exc:
            error_msg = str(exc)
            raise
        finally:
            # Detect unawaited background tasks if leak_detection is enabled
            remaining_tasks: Set[asyncio.Task] = set()
            if self.standard.leak_detection:
                remaining_tasks = {
                    t for t in asyncio.all_tasks(loop) if not t.done()
                }
                dangling_count = len(remaining_tasks)

            if remaining_tasks and self.standard.strict_task_drain:
                for task in remaining_tasks:
                    task.cancel()
                try:
                    # Bounded task draining: wait at most 0.5s for tasks to finish
                    done, _ = loop.run_until_complete(
                        asyncio.wait(remaining_tasks, timeout=0.5)
                    )
                    drained_count = len(done)
                except Exception as drain_exc:
                    logger.warning("Error draining async tasks: %s", drain_exc)
                    drained_count = sum(1 for t in remaining_tasks if t.done())

            # Detect and clean unrestored mock patches
            if self.standard.mock_restoration_check:
                try:
                    from unittest.mock import _patch
                    cleanup_active: Any = getattr(_patch, "_active_patches", set())
                    leaked_mocks = set(cleanup_active) - initial_active_mocks
                    unrestored_mocks_count = len(leaked_mocks)
                    for p in leaked_mocks:
                        try:
                            p.stop()
                        except Exception:
                            pass
                except Exception:
                    pass

            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass

            loop.close()

            # Restore caller's prior event loop
            try:
                asyncio.set_event_loop(prior_loop)
            except Exception:
                asyncio.set_event_loop(None)

            duration_ms = (time.monotonic() - start_time) * 1000.0
            report = AsyncLifecycleReport(
                test_name=test_name,
                success=success,
                dangling_tasks_detected=dangling_count,
                tasks_drained=drained_count,
                unrestored_mocks_detected=unrestored_mocks_count,
                duration_ms=duration_ms,
                error=error_msg,
            )
        return result, report


# Global default standard and manager
_GLOBAL_ASYNC_TEST_STANDARD = AsyncTestStandard()
_GLOBAL_LIFECYCLE_MANAGER = AsyncTestLifecycleManager(_GLOBAL_ASYNC_TEST_STANDARD)


def get_async_test_standard() -> AsyncTestStandard:
    """Retrieve current asynchronous test standard."""
    return _GLOBAL_ASYNC_TEST_STANDARD


def get_lifecycle_manager() -> AsyncTestLifecycleManager:
    """Retrieve global async lifecycle manager."""
    return _GLOBAL_LIFECYCLE_MANAGER


def run_strict_async(
    coro: Coroutine[Any, Any, Any],
    timeout_seconds: Optional[float] = None,
) -> Any:
    """Convenience helper to run a coroutine under strict lifecycle isolation."""
    result, _ = get_lifecycle_manager().run_isolated(coro, timeout_seconds=timeout_seconds)
    return result


def strict_async_test(
    timeout: float = 10.0,
    test_name: Optional[str] = None,
) -> Callable[[Callable[..., Coroutine[Any, Any, Any]]], Callable[..., Any]]:
    """Decorator converting an async test into a hermetic isolated synchronous runner."""
    def decorator(fn: Callable[..., Coroutine[Any, Any, Any]]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            name = test_name or fn.__name__
            coro = fn(*args, **kwargs)
            result, _ = get_lifecycle_manager().run_isolated(
                coro,
                timeout_seconds=timeout,
                test_name=name,
            )
            return result
        return wrapper
    return decorator
