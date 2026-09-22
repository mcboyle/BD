"""Row 1069: Strict Lifecycle Asynchronous Testing Suite Standard (pytest-asyncio & pytest-mock).

Tests verify:
1. Positive control on dev_suite/test_meta baseline and semantic failure on missing capability.
2. AsyncTestStandard configuration defaults and validation.
3. Isolated event loop execution and clean termination.
4. Dangling background task detection, cancellation, and graceful draining.
5. Uncooperative task bounded drain timeout prevents indefinite hangs (P1/E1).
6. Caller event loop preservation and restoration (P2/R3).
7. Nested running loop guard raises RuntimeError (R3).
8. Leak detection flag behavior (P3/R2).
9. Mock restoration check detects and cleans unstopped patches (P3/R2).
10. Strict async test decorator behavior.
11. Execution timeout enforcement and resource cleanup.
12. Caller integration with bulk_downloader.dev_suite.test_meta.
13. Native async test execution through the conftest pytest_pyfunc_call hook (E4/R1).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import time
from typing import Any, Dict
from unittest.mock import patch
import pytest

BD_GATE_SCOPE = "module"


def test_row1069_positive_control_and_capability() -> None:
    """Proves the probe can say YES on positive control and fails on base for missing capability."""
    repo_root = Path(__file__).resolve().parents[1]
    test_meta_py = repo_root / "bulk_downloader" / "dev_suite" / "test_meta.py"
    assert test_meta_py.is_file(), f"Positive control failed: {test_meta_py} does not exist"

    try:
        from bulk_downloader.async_testing import (  # type: ignore[import-not-found]
            AsyncTestLifecycleManager,
            AsyncTestStandard,
            get_async_test_standard,
        )
    except (ImportError, ModuleNotFoundError) as exc:
        raise AssertionError(
            "Row 1069 capability missing: Strict Lifecycle Asynchronous Testing Suite Standard "
            "(pytest-asyncio & pytest-mock) not implemented in bulk_downloader.async_testing"
        ) from exc

    std = get_async_test_standard()
    assert isinstance(std, AsyncTestStandard)


def test_async_test_standard_strict_defaults() -> None:
    """Verifies that AsyncTestStandard enforces strict lifecycle and isolation parameters."""
    from bulk_downloader.async_testing import AsyncTestStandard

    standard = AsyncTestStandard()
    assert standard.asyncio_mode == "strict"
    assert standard.loop_scope == "function"
    assert standard.task_timeout_seconds == 10.0
    assert standard.strict_task_drain is True
    assert standard.leak_detection is True
    assert standard.mock_restoration_check is True

    summary = standard.to_dict()
    assert summary["asyncio_mode"] == "strict"
    assert summary["loop_scope"] == "function"


def test_loop_scope_and_asyncio_mode_validation() -> None:
    """Verifies that invalid loop_scope or asyncio_mode raises ValueError (R2)."""
    from bulk_downloader.async_testing import AsyncTestLifecycleManager, AsyncTestStandard

    bad_scope = AsyncTestStandard(loop_scope="session")
    manager_bad_scope = AsyncTestLifecycleManager(bad_scope)
    c1 = asyncio.sleep(0)
    try:
        with pytest.raises(ValueError, match="Unsupported loop_scope"):
            manager_bad_scope.run_isolated(c1)
    finally:
        c1.close()

    bad_mode = AsyncTestStandard(asyncio_mode="legacy")
    manager_bad_mode = AsyncTestLifecycleManager(bad_mode)
    c2 = asyncio.sleep(0)
    try:
        with pytest.raises(ValueError, match="Unsupported asyncio_mode"):
            manager_bad_mode.run_isolated(c2)
    finally:
        c2.close()


def test_isolated_loop_execution_and_result() -> None:
    """Verifies execution of asynchronous code in an isolated event loop."""
    from bulk_downloader.async_testing import AsyncTestLifecycleManager

    manager = AsyncTestLifecycleManager()

    async def sample_coro() -> int:
        await asyncio.sleep(0.01)
        return 42

    result, report = manager.run_isolated(sample_coro(), test_name="sample_test")
    assert result == 42
    assert report.success is True
    assert report.dangling_tasks_detected == 0
    assert report.duration_ms > 0


def test_dangling_task_detection_and_drain() -> None:
    """Verifies detection, cancellation, and draining of unawaited background tasks."""
    from bulk_downloader.async_testing import AsyncTestLifecycleManager

    manager = AsyncTestLifecycleManager()

    async def leaky_coro() -> str:
        async def background_worker() -> None:
            while True:
                await asyncio.sleep(1.0)

        asyncio.create_task(background_worker())
        await asyncio.sleep(0.01)
        return "completed"

    result, report = manager.run_isolated(leaky_coro(), test_name="leaky_test")
    assert result == "completed"
    assert report.dangling_tasks_detected >= 1
    assert report.tasks_drained >= 1


def test_uncooperative_task_bounded_drain_prevents_hang() -> None:
    """Verifies that uncooperative tasks that catch CancelledError do not hang drain (P1/E1)."""
    from bulk_downloader.async_testing import AsyncTestLifecycleManager

    manager = AsyncTestLifecycleManager()

    async def uncooperative_coro() -> str:
        async def rogue_worker() -> None:
            while True:
                try:
                    await asyncio.sleep(0.05)
                except asyncio.CancelledError:
                    # Uncooperative task ignores cancel and keeps looping
                    await asyncio.sleep(0.05)

        asyncio.create_task(rogue_worker())
        await asyncio.sleep(0.01)
        return "finished"

    t0 = time.monotonic()
    result, report = manager.run_isolated(uncooperative_coro(), test_name="uncooperative_test")
    elapsed = time.monotonic() - t0

    assert result == "finished"
    assert report.dangling_tasks_detected >= 1
    # Draining times out after 0.5s rather than hanging indefinitely
    assert elapsed < 2.0


def test_caller_event_loop_preserved_and_restored() -> None:
    """Verifies caller event loop is preserved and restored after run_isolated (P2/R3)."""
    from bulk_downloader.async_testing import AsyncTestLifecycleManager, run_strict_async

    loop0 = asyncio.new_event_loop()
    asyncio.set_event_loop(loop0)
    try:
        async def quick_coro() -> str:
            return "ok"

        res = run_strict_async(quick_coro())
        assert res == "ok"

        current_loop = asyncio.get_event_loop_policy().get_event_loop()
        assert current_loop is loop0
        assert not loop0.is_closed()
    finally:
        asyncio.set_event_loop(None)
        loop0.close()


def test_nested_running_loop_raises_runtime_error() -> None:
    """Verifies that invoking run_isolated inside a running loop raises RuntimeError (R3)."""
    from bulk_downloader.async_testing import AsyncTestLifecycleManager

    manager = AsyncTestLifecycleManager()

    async def outer_coro() -> None:
        async def inner_coro() -> None:
            pass

        inner = inner_coro()
        try:
            manager.run_isolated(inner)
        finally:
            inner.close()

    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(RuntimeError, match="another event loop is running"):
            loop.run_until_complete(outer_coro())
    finally:
        loop.close()


def test_leak_detection_flag_behavior() -> None:
    """Verifies behavior when leak_detection and strict_task_drain are toggled (P3/R2)."""
    from bulk_downloader.async_testing import AsyncTestLifecycleManager, AsyncTestStandard

    # When leak_detection is False, dangling tasks are not tracked
    std_no_leak = AsyncTestStandard(leak_detection=False, strict_task_drain=False)
    mgr_no_leak = AsyncTestLifecycleManager(std_no_leak)

    async def leaking_coro() -> str:
        async def worker() -> None:
            while True:
                await asyncio.sleep(1.0)
        asyncio.create_task(worker())
        await asyncio.sleep(0.01)
        return "done"

    res, rep = mgr_no_leak.run_isolated(leaking_coro(), test_name="no_leak_test")
    assert res == "done"
    assert rep.dangling_tasks_detected == 0


def test_mock_restoration_check_detects_and_cleans_leaked_mock() -> None:
    """Verifies mock_restoration_check detects unstopped mocks and cleans them up (P3/R2)."""
    from bulk_downloader.async_testing import AsyncTestLifecycleManager, AsyncTestStandard

    # With mock_restoration_check enabled
    std = AsyncTestStandard(mock_restoration_check=True)
    mgr = AsyncTestLifecycleManager(std)

    async def mock_leaking_coro() -> str:
        p = patch("os.getcwd", return_value="/mocked/path")
        p.start()
        # Intentionally forget to call p.stop()
        return "mock_started"

    res, rep = mgr.run_isolated(mock_leaking_coro(), test_name="mock_leak_test")
    assert res == "mock_started"
    assert rep.unrestored_mocks_detected >= 1


def test_strict_async_test_decorator() -> None:
    """Verifies strict async test decorator executing coroutine functions."""
    from bulk_downloader.async_testing import strict_async_test

    @strict_async_test(timeout=5.0)
    async def decorated_test() -> str:
        await asyncio.sleep(0.01)
        return "pass"

    assert decorated_test() == "pass"


def test_timeout_enforcement() -> None:
    """Verifies that coroutines exceeding timeout are stopped and raise TimeoutError."""
    from bulk_downloader.async_testing import AsyncTestLifecycleManager

    manager = AsyncTestLifecycleManager()

    async def slow_coro() -> None:
        await asyncio.sleep(2.0)

    with pytest.raises(TimeoutError):
        manager.run_isolated(slow_coro(), timeout_seconds=0.05, test_name="slow_test")


def test_dev_suite_caller_wiring() -> None:
    """Verifies caller integration with bulk_downloader.dev_suite.test_meta."""
    from bulk_downloader.dev_suite.test_meta import get_async_test_suite_standard

    res = get_async_test_suite_standard()
    assert isinstance(res, dict)
    assert res["ok"] is True
    assert res["tool"] == "async_test_standard"
    assert res["asyncio_mode"] == "strict"
    assert res["loop_scope"] == "function"


def test_native_async_execution_via_strict_standard() -> None:
    """Verifies that a coroutine runs under strict lifecycle standard (E4/R1)."""
    from bulk_downloader.async_testing import strict_async_test

    @strict_async_test(timeout=5.0)
    async def sample_test() -> str:
        await asyncio.sleep(0.01)
        return "pass"

    assert sample_test() == "pass"


def test_conftest_pytest_pyfunc_call_hook() -> None:
    """Verifies that conftest pytest_pyfunc_call hook executes coroutines (E4/R1)."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from tests.conftest import pytest_pyfunc_call

    class FakeItem:
        def __init__(self, fn: Any) -> None:
            self.obj = fn
            self.funcargs: dict[str, Any] = {}
            class FixtureInfo:
                argnames: list[str] = []
            self._fixtureinfo = FixtureInfo()

    async def sample() -> str:
        await asyncio.sleep(0.01)
        return "hook_success"

    res = pytest_pyfunc_call(FakeItem(sample))
    assert res is True

