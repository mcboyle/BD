"""Unit tests for Row 1026: Asynchronous Concurrency Unification & Event Loop Consolidation.

Guards:
- AsyncWorkerRuntime lifecycle (IDLE -> STARTING -> RUNNING -> DRAINING -> STOPPED)
- RuntimeConfig defaults and custom values
- TaskGroup add/mark_running/mark_completed/mark_failed/cancel_all
- TaskGroup stats tracking
- Runtime task submission
- Health probe reports correct state
- Graceful vs immediate shutdown (immediate cancels tasks)
- RuntimeState, TaskState, ShutdownMode enums
- Stats reporting
- Metadata introspection
"""
from __future__ import annotations

import pytest

# H622 anti-orphan convention: scoped module test, collected by bd-test-shard
BD_GATE_SCOPE = "module"


def _runtime_module():
    """Import guard: a missing module is an assertion about the capability, not an ImportError."""
    try:
        from bulk_downloader import async_worker_runtime
    except ImportError:
        return None
    return async_worker_runtime


def test_capability_exists():
    """Row 1026 capability presence: async_worker_runtime is importable."""
    async_worker_runtime = _runtime_module()
    assert async_worker_runtime is not None, "Row 1026 capability missing: no async_worker_runtime"
    assert hasattr(
        async_worker_runtime, "AsyncWorkerRuntime"
    ), "Row 1026 capability missing: AsyncWorkerRuntime not exposed"
    assert hasattr(
        async_worker_runtime, "TaskGroup"
    ), "Row 1026 capability missing: TaskGroup not exposed"


def test_runtime_lifecycle():
    """Runtime transitions through lifecycle states."""
    from bulk_downloader.async_worker_runtime import (
        AsyncWorkerRuntime,
        RuntimeState,
    )

    rt = AsyncWorkerRuntime()
    assert rt.state == RuntimeState.IDLE
    assert rt.start() is True
    assert rt.state == RuntimeState.RUNNING
    assert rt.start() is False  # Already running
    assert rt.shutdown() is True
    assert rt.state == RuntimeState.STOPPED
    assert rt.shutdown() is False  # Already stopped


def test_runtime_config_defaults():
    """RuntimeConfig has sensible defaults."""
    from bulk_downloader.async_worker_runtime import RuntimeConfig

    cfg = RuntimeConfig()
    assert cfg.max_concurrent_tasks == 64
    assert cfg.thread_pool_size == 16
    assert cfg.shutdown_timeout_seconds == 30.0


def test_runtime_config_custom():
    """RuntimeConfig accepts custom values."""
    from bulk_downloader.async_worker_runtime import RuntimeConfig

    cfg = RuntimeConfig(max_concurrent_tasks=128, thread_pool_size=32)
    assert cfg.max_concurrent_tasks == 128
    assert cfg.thread_pool_size == 32


def test_task_group_lifecycle():
    """TaskGroup tracks task state transitions."""
    from bulk_downloader.async_worker_runtime import TaskGroup, TaskState

    group = TaskGroup("downloads")
    record = group.add_task("t1", "download-a")
    assert record.state == TaskState.PENDING
    assert group.mark_running("t1") is True
    assert record.state == TaskState.RUNNING
    assert group.mark_completed("t1") is True
    assert record.state == TaskState.COMPLETED


def test_task_group_mark_failed():
    """mark_failed transitions and records error."""
    from bulk_downloader.async_worker_runtime import TaskGroup, TaskState

    group = TaskGroup("batch")
    group.add_task("t1", "job")
    group.mark_running("t1")
    assert group.mark_failed("t1", "connection refused") is True
    s = group.stats()
    assert s["state_counts"].get("failed", 0) == 1


def test_task_group_cancel_all():
    """cancel_all cancels pending and running tasks."""
    from bulk_downloader.async_worker_runtime import TaskGroup, TaskState

    group = TaskGroup("batch")
    group.add_task("t1", "a")
    group.add_task("t2", "b")
    group.mark_running("t1")
    cancelled = group.cancel_all()
    assert cancelled == 2


def test_task_group_stats():
    """Group stats include task counts by state."""
    from bulk_downloader.async_worker_runtime import TaskGroup

    group = TaskGroup("test")
    group.add_task("t1", "a")
    group.add_task("t2", "b")
    group.mark_running("t1")
    s = group.stats()
    assert s["name"] == "test"
    assert s["task_count"] == 2


def test_runtime_create_task_group():
    """Runtime creates and retrieves task groups."""
    from bulk_downloader.async_worker_runtime import AsyncWorkerRuntime

    rt = AsyncWorkerRuntime()
    group = rt.create_task_group("batch")
    assert group.name == "batch"
    assert rt.get_task_group("batch") is group
    assert rt.get_task_group("missing") is None


def test_runtime_submit_task():
    """Runtime submits tasks to groups."""
    from bulk_downloader.async_worker_runtime import AsyncWorkerRuntime

    rt = AsyncWorkerRuntime()
    rt.create_task_group("batch")
    record = rt.submit_task("batch", "t1", "download")
    assert record is not None
    assert record.task_id == "t1"
    assert rt.submit_task("missing", "t2", "nope") is None


def test_runtime_probe_running():
    """Health probe reports healthy when running."""
    from bulk_downloader.async_worker_runtime import AsyncWorkerRuntime

    rt = AsyncWorkerRuntime()
    rt.start()
    probe = rt.probe()
    assert probe.healthy is True
    assert probe.state == "running"
    rt.shutdown()


def test_runtime_probe_stopped():
    """Health probe reports unhealthy when stopped."""
    from bulk_downloader.async_worker_runtime import AsyncWorkerRuntime

    rt = AsyncWorkerRuntime()
    probe = rt.probe()
    assert probe.healthy is False


def test_immediate_shutdown_cancels():
    """Immediate shutdown cancels all tasks."""
    from bulk_downloader.async_worker_runtime import (
        AsyncWorkerRuntime,
        ShutdownMode,
        TaskState,
    )

    rt = AsyncWorkerRuntime()
    rt.start()
    group = rt.create_task_group("batch")
    group.add_task("t1", "a")
    group.add_task("t2", "b")
    rt.shutdown(mode=ShutdownMode.IMMEDIATE)
    s = group.stats()
    assert s["state_counts"].get("cancelled", 0) == 2


def test_runtime_stats():
    """Stats include config and group count."""
    from bulk_downloader.async_worker_runtime import AsyncWorkerRuntime

    rt = AsyncWorkerRuntime()
    rt.start()
    rt.create_task_group("g1")
    s = rt.stats()
    assert s["state"] == "running"
    assert s["groups"] == 1
    assert s["config"]["max_concurrent_tasks"] == 64
    rt.shutdown()


def test_runtime_state_enum():
    """RuntimeState has five members."""
    from bulk_downloader.async_worker_runtime import RuntimeState

    expected = {"idle", "starting", "running", "draining", "stopped"}
    assert {s.value for s in RuntimeState} == expected


def test_task_state_enum():
    """TaskState has five members."""
    from bulk_downloader.async_worker_runtime import TaskState

    expected = {"pending", "running", "completed", "cancelled", "failed"}
    assert {s.value for s in TaskState} == expected


def test_shutdown_mode_enum():
    """ShutdownMode has three members."""
    from bulk_downloader.async_worker_runtime import ShutdownMode

    expected = {"graceful", "immediate", "forced"}
    assert {m.value for m in ShutdownMode} == expected


def test_get_async_worker_runtime_info():
    """Metadata introspection returns complete schema."""
    from bulk_downloader.async_worker_runtime import (
        get_async_worker_runtime_info,
    )

    info = get_async_worker_runtime_info()
    assert isinstance(info, dict)
    assert info["version"] >= 1
    assert "AsyncWorkerRuntime" in info["components"]
    assert len(info["runtime_states"]) == 5
    assert len(info["task_states"]) == 5
    assert len(info["shutdown_modes"]) == 3


# ---- rebuild (ORDERS-2307): wiring through bdctl, fail-closed probe, draining shutdown ----

def _guardrail_args(urls):
    class Args:
        site = None
        mode = "append"
        enable_guardrails = True
    Args.urls = list(urls)
    return Args()


def test_bdctl_guardrail_checks_run_on_one_runtime_loop(monkeypatch, capsys):
    """bdctl add --enable-guardrails runs every check on ONE runtime loop, concurrently.

    RED on base: cmd_add calls asyncio.run() once per URL -- one throwaway event loop per
    check, run strictly one after another on the caller's thread.
    """
    import asyncio
    import threading

    import bdctl
    from bulk_downloader import guardrails

    loops, threads, inflight, peak = [], [], [0], [0]

    async def fake_safety(metadata, *, enabled=False, request=None):
        loops.append(asyncio.get_running_loop())
        threads.append(threading.current_thread().name)
        inflight[0] += 1
        peak[0] = max(peak[0], inflight[0])
        await asyncio.sleep(0.05)
        inflight[0] -= 1
        return "blocked" not in metadata["url"]

    posted = []
    monkeypatch.setattr(bdctl, "_request", lambda m, path, body=None, query=None: posted.append(body) or {"ok": True})
    monkeypatch.setattr(guardrails, "pre_download_safety_check", fake_safety)

    urls = ["https://a.example/1", "https://a.example/blocked", "https://a.example/3"]
    bdctl.cmd_add(_guardrail_args(urls))

    assert len(loops) == 3
    assert len({id(lp) for lp in loops}) == 1, f"guardrail checks ran on {len({id(lp) for lp in loops})} event loops"
    assert set(threads) == {"bd-async-runtime"}, threads
    assert peak[0] == 3, f"checks ran one at a time (peak in flight {peak[0]})"
    assert posted == [{"url": "https://a.example/1"}, {"url": "https://a.example/3"}]
    assert "Blocked unsafe download: https://a.example/blocked" in capsys.readouterr().err


def test_bdctl_guardrail_runtime_is_shut_down_after_add(monkeypatch):
    """The runtime cmd_add starts is stopped again: its loop thread does not outlive the command."""
    import threading

    import bdctl
    from bulk_downloader import guardrails

    async def fake_safety(metadata, *, enabled=False, request=None):
        return True

    monkeypatch.setattr(bdctl, "_request", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(guardrails, "pre_download_safety_check", fake_safety)
    bdctl.cmd_add(_guardrail_args(["https://a.example/1"]))
    assert not [t for t in threading.enumerate() if t.name == "bd-async-runtime"]


def test_probe_reports_unhealthy_when_loop_is_stalled():
    """A loop that cannot service a callback within the probe budget is UNHEALTHY, never healthy."""
    import time

    mod = _runtime_module()
    assert mod is not None, "Row 1026 capability missing: no async_worker_runtime"
    rt = mod.AsyncWorkerRuntime()
    rt.start()
    try:
        async def block_loop():
            time.sleep(0.6)  # deliberately blocks the event loop thread

        rt.create_task_group("stall")
        fut = rt.spawn("stall", "s1", block_loop())
        time.sleep(0.05)
        probe = rt.probe(timeout=0.1)
        assert probe.healthy is False, "probe reported healthy while the event loop was stalled"
        assert probe.status == mod.HealthStatus.UNHEALTHY
        assert probe.loop_latency_ms is None
        assert "stall" in probe.reason
        fut.result(timeout=5)
        healthy = rt.probe(timeout=1.0)
        assert healthy.status == mod.HealthStatus.HEALTHY
        assert healthy.loop_latency_ms is not None and 0 <= healthy.loop_latency_ms < 1000
    finally:
        rt.shutdown()


def test_probe_from_loop_thread_is_unverifiable():
    """The loop cannot time itself from inside itself: that is UNVERIFIABLE, not healthy."""
    mod = _runtime_module()
    assert mod is not None, "Row 1026 capability missing: no async_worker_runtime"
    rt = mod.AsyncWorkerRuntime()
    rt.start()
    try:
        async def probe_inside():
            return rt.probe(timeout=0.1)

        probe = rt.run(probe_inside())
        assert probe.status == mod.HealthStatus.UNVERIFIABLE
        assert probe.healthy is False
        assert probe.loop_latency_ms is None
    finally:
        rt.shutdown()


def test_probe_stopped_runtime_is_unhealthy_with_no_measurement():
    mod = _runtime_module()
    assert mod is not None, "Row 1026 capability missing: no async_worker_runtime"
    rt = mod.AsyncWorkerRuntime()
    probe = rt.probe()
    assert probe.status == mod.HealthStatus.UNHEALTHY
    assert probe.loop_latency_ms is None
    assert probe.thread_pool_active is None


def test_probe_counts_busy_executor_threads():
    """thread_pool_active is measured from the executor, not a literal."""
    import asyncio
    import threading

    mod = _runtime_module()
    assert mod is not None, "Row 1026 capability missing: no async_worker_runtime"
    rt = mod.AsyncWorkerRuntime(mod.RuntimeConfig(thread_pool_size=4))
    rt.start()
    release, entered = threading.Event(), threading.Event()

    def blocking():
        entered.set()
        release.wait(5)

    try:
        rt.create_task_group("io")
        fut = rt.spawn("io", "b1", asyncio.to_thread(blocking))
        assert entered.wait(5)
        probe = rt.probe(timeout=1.0)
        assert probe.thread_pool_active == 1
        assert probe.thread_pool_size == 4
        release.set()
        fut.result(timeout=5)
        assert rt.probe(timeout=1.0).thread_pool_active == 0
    finally:
        release.set()
        rt.shutdown()


def test_graceful_shutdown_waits_for_running_task():
    """GRACEFUL drains: an in-flight task finishes before shutdown returns."""
    import asyncio

    mod = _runtime_module()
    assert mod is not None, "Row 1026 capability missing: no async_worker_runtime"
    rt = mod.AsyncWorkerRuntime()
    rt.start()
    group = rt.create_task_group("g")

    async def work():
        await asyncio.sleep(0.2)
        return "done"

    fut = rt.spawn("g", "t1", work())
    assert rt.shutdown(mode=mod.ShutdownMode.GRACEFUL) is True
    assert fut.done() and fut.result() == "done"
    assert group.stats()["state_counts"] == {"completed": 1}


def test_graceful_shutdown_cancels_what_outlives_the_timeout():
    """Past shutdown_timeout the stragglers are cancelled; nothing is left 'running'."""
    import asyncio
    import time

    mod = _runtime_module()
    assert mod is not None, "Row 1026 capability missing: no async_worker_runtime"
    rt = mod.AsyncWorkerRuntime(mod.RuntimeConfig(shutdown_timeout_seconds=0.1))
    rt.start()
    group = rt.create_task_group("g")
    fut = rt.spawn("g", "slow", asyncio.sleep(30))
    group.add_task("ledger", "bookkeeping-only")
    group.mark_running("ledger")
    t0 = time.monotonic()
    assert rt.shutdown(mode=mod.ShutdownMode.GRACEFUL) is True
    assert time.monotonic() - t0 < 5
    assert fut.cancelled()
    assert group.stats()["state_counts"] == {"cancelled": 2}


def test_run_all_bounds_concurrency_and_rejects_when_stopped():
    import asyncio

    mod = _runtime_module()
    assert mod is not None, "Row 1026 capability missing: no async_worker_runtime"
    rt = mod.AsyncWorkerRuntime(mod.RuntimeConfig(max_concurrent_tasks=2))
    inflight, peak = [0], [0]

    async def job(i):
        inflight[0] += 1
        peak[0] = max(peak[0], inflight[0])
        await asyncio.sleep(0.02)
        inflight[0] -= 1
        return i

    with pytest.raises(RuntimeError, match="not running"):
        rt.run_all([job(0)])
    rt.start()
    try:
        assert rt.run_all([job(i) for i in range(5)]) == [0, 1, 2, 3, 4]
        assert peak[0] == 2
    finally:
        rt.shutdown()


def test_healthy_probe_is_backed_by_a_live_event_loop():
    """P1: a probe may only say healthy when a real loop thread exists to have been measured."""
    import threading

    mod = _runtime_module()
    assert mod is not None, "Row 1026 capability missing: no async_worker_runtime"
    rt = mod.AsyncWorkerRuntime()
    rt.start()
    try:
        probe = rt.probe()
        loop_threads = [t for t in threading.enumerate() if t.name == "bd-async-runtime" and t.is_alive()]
        if probe.healthy:
            assert loop_threads, "probe reported healthy with no event loop running"
    finally:
        rt.shutdown()


def test_graceful_shutdown_leaves_no_task_running():
    """P2: after GRACEFUL shutdown returns, no record may still claim to be running."""
    mod = _runtime_module()
    assert mod is not None, "Row 1026 capability missing: no async_worker_runtime"
    rt = mod.AsyncWorkerRuntime(mod.RuntimeConfig(shutdown_timeout_seconds=0.1))
    rt.start()
    group = rt.create_task_group("g")
    group.add_task("t1", "a")
    group.mark_running("t1")
    assert rt.shutdown(mode=mod.ShutdownMode.GRACEFUL) is True
    counts = group.stats()["state_counts"]
    assert "running" not in counts, f"graceful shutdown returned with tasks still running: {counts}"
