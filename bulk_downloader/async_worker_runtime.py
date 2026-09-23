"""async_worker_runtime -- asynchronous concurrency unification and event loop consolidation.

Row 1026.  BD currently mixes threading, multiprocessing, and ad-hoc asyncio
event loops across different subsystems.  Download workers run in threads,
while HTTP/3 uses asyncio, and the TUI dashboard spins its own loop.  This
creates three problems: (1) no shared cancellation, (2) context variables
don't propagate across boundaries, (3) resource limits are enforced per-pool
rather than globally.

This module provides:

1. ``AsyncWorkerRuntime`` -- a unified runtime that manages a single asyncio
   event loop with a bounded thread executor for sync-to-async bridging.

2. ``RuntimeConfig`` -- configuration for concurrency limits, executor sizing,
   and graceful shutdown timeout.

3. ``TaskGroup`` -- a lightweight structured concurrency group for organizing
   related async tasks with collective cancellation.

4. ``AsyncWorkerRuntime.probe`` -- three-state health check (HEALTHY /
   UNHEALTHY / UNVERIFIABLE) that times a callback through the live loop and
   reads executor occupancy; nothing unmeasured is reported healthy.

No new ``BD_`` environment keys.  First consumer: ``bdctl add
--enable-guardrails`` runs its per-URL safety checks on one runtime loop
instead of one ``asyncio.run`` loop per URL.  Other subsystems migrate in
later rows.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import enum
import threading
import time
from collections.abc import Awaitable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Optional


class RuntimeState(enum.Enum):
    """Lifecycle state of the async worker runtime."""
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    DRAINING = "draining"
    STOPPED = "stopped"


class TaskState(enum.Enum):
    """State of a managed task."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class HealthStatus(enum.Enum):
    """Three-state probe outcome: an unmeasured loop is never reported healthy."""
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNVERIFIABLE = "unverifiable"


class ShutdownMode(enum.Enum):
    """How the runtime shuts down."""
    GRACEFUL = "graceful"
    IMMEDIATE = "immediate"
    FORCED = "forced"


@dataclass(frozen=True)
class RuntimeConfig:
    """Configuration for the async worker runtime."""
    max_concurrent_tasks: int = 64
    thread_pool_size: int = 16
    shutdown_timeout_seconds: float = 30.0
    enable_task_tracking: bool = True
    loop_debug: bool = False


@dataclass
class TaskRecord:
    """Metadata for a tracked task."""
    task_id: str
    name: str
    group: str = ""
    state: TaskState = TaskState.PENDING
    created_at: float = field(default_factory=time.monotonic)
    started_at: float = 0.0
    completed_at: float = 0.0
    error: Optional[str] = None


@dataclass
class ProbeResult:
    """Result of a runtime health probe.  Measurements are None when not taken."""
    status: HealthStatus
    state: str
    loop_latency_ms: Optional[float]
    active_tasks: int
    thread_pool_active: Optional[int]
    thread_pool_size: int
    reason: str = ""
    checked_at: float = field(default_factory=time.monotonic)

    @property
    def healthy(self) -> bool:
        return self.status is HealthStatus.HEALTHY


class TaskGroup:
    """Structured concurrency group for related tasks.

    Usage::

        group = TaskGroup("downloads")
        group.add_task("task-1", "download-file-a")
        group.mark_running("task-1")
        group.mark_completed("task-1")
        group.cancel_all()  # Cancels remaining tasks
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._lock = threading.Lock()
        self._tasks: dict[str, TaskRecord] = {}
        self._next_id = 0

    @property
    def name(self) -> str:
        return self._name

    def add_task(self, task_id: str, name: str) -> TaskRecord:
        """Add a task to this group."""
        record = TaskRecord(task_id=task_id, name=name, group=self._name)
        with self._lock:
            self._tasks[task_id] = record
        return record

    def mark_running(self, task_id: str) -> bool:
        """Mark a task as running.  Returns True if found and pending."""
        with self._lock:
            record = self._tasks.get(task_id)
            if record and record.state == TaskState.PENDING:
                record.state = TaskState.RUNNING
                record.started_at = time.monotonic()
                return True
            return False

    def mark_completed(self, task_id: str) -> bool:
        """Mark a task as completed."""
        with self._lock:
            record = self._tasks.get(task_id)
            if record and record.state == TaskState.RUNNING:
                record.state = TaskState.COMPLETED
                record.completed_at = time.monotonic()
                return True
            return False

    def mark_failed(self, task_id: str, error: str) -> bool:
        """Mark a task as failed."""
        with self._lock:
            record = self._tasks.get(task_id)
            if record and record.state in (TaskState.PENDING, TaskState.RUNNING):
                record.state = TaskState.FAILED
                record.error = error
                record.completed_at = time.monotonic()
                return True
            return False

    def mark_cancelled(self, task_id: str) -> bool:
        """Mark a pending/running task as cancelled."""
        with self._lock:
            record = self._tasks.get(task_id)
            if record and record.state in (TaskState.PENDING, TaskState.RUNNING):
                record.state = TaskState.CANCELLED
                record.completed_at = time.monotonic()
                return True
            return False

    def cancel_all(self) -> int:
        """Cancel all pending/running tasks.  Returns count cancelled."""
        count = 0
        with self._lock:
            for record in self._tasks.values():
                if record.state in (TaskState.PENDING, TaskState.RUNNING):
                    record.state = TaskState.CANCELLED
                    count += 1
        return count

    def stats(self) -> dict[str, Any]:
        """Return group statistics."""
        with self._lock:
            state_counts: dict[str, int] = {}
            for r in self._tasks.values():
                s = r.state.value
                state_counts[s] = state_counts.get(s, 0) + 1
            return {
                "name": self._name,
                "task_count": len(self._tasks),
                "state_counts": state_counts,
            }


class _CountingExecutor(ThreadPoolExecutor):
    """ThreadPoolExecutor that counts the jobs currently executing on its threads."""

    def __init__(self, max_workers: int) -> None:
        super().__init__(max_workers=max_workers, thread_name_prefix="bd-async-pool")
        self._active = 0
        self._active_lock = threading.Lock()

    @property
    def active(self) -> int:
        with self._active_lock:
            return self._active

    def submit(self, fn, /, *args, **kwargs):  # type: ignore[override]
        def counted():
            with self._active_lock:
                self._active += 1
            try:
                return fn(*args, **kwargs)
            finally:
                with self._active_lock:
                    self._active -= 1
        return super().submit(counted)


class AsyncWorkerRuntime:
    """Unified async worker runtime: one event loop on its own thread plus a bounded executor.

    Usage::

        runtime = AsyncWorkerRuntime(config=RuntimeConfig())
        runtime.start()
        results = runtime.run_all([check(u) for u in urls])  # one loop, bounded concurrency
        runtime.shutdown(mode=ShutdownMode.GRACEFUL)
    """

    def __init__(self, config: Optional[RuntimeConfig] = None) -> None:
        self._config = config or RuntimeConfig()
        self._state = RuntimeState.IDLE
        self._lock = threading.Lock()
        self._groups: dict[str, TaskGroup] = {}
        self._started_at: float = 0.0
        self._total_tasks_submitted = 0
        self._total_tasks_completed = 0
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._executor: Optional[_CountingExecutor] = None
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._inflight: set[concurrent.futures.Future] = set()

    @property
    def config(self) -> RuntimeConfig:
        return self._config

    @property
    def state(self) -> RuntimeState:
        with self._lock:
            return self._state

    def start(self) -> bool:
        """Start the loop thread and executor.  Returns True if newly started."""
        with self._lock:
            if self._state in (RuntimeState.RUNNING, RuntimeState.STARTING, RuntimeState.DRAINING):
                return False
            self._state = RuntimeState.STARTING
            loop = asyncio.new_event_loop()
            loop.set_debug(self._config.loop_debug)
            executor = _CountingExecutor(self._config.thread_pool_size)
            loop.set_default_executor(executor)
            ready = threading.Event()

            def _serve() -> None:
                asyncio.set_event_loop(loop)
                self._semaphore = asyncio.Semaphore(self._config.max_concurrent_tasks)
                loop.call_soon(ready.set)
                try:
                    loop.run_forever()
                finally:
                    leftover = asyncio.all_tasks(loop)
                    for task in leftover:
                        task.cancel()
                    if leftover:
                        loop.run_until_complete(asyncio.gather(*leftover, return_exceptions=True))
                    loop.run_until_complete(loop.shutdown_asyncgens())
                    loop.close()

            thread = threading.Thread(target=_serve, name="bd-async-runtime", daemon=True)
            thread.start()
            if not ready.wait(5.0):
                loop.call_soon_threadsafe(loop.stop)
                executor.shutdown(wait=False, cancel_futures=True)
                self._state = RuntimeState.STOPPED
                raise RuntimeError("async runtime loop thread did not start within 5s")
            self._loop, self._thread, self._executor = loop, thread, executor
            self._started_at = time.monotonic()
            self._state = RuntimeState.RUNNING
            return True

    def _require_running(self, coros: list, blocking: bool = True) -> asyncio.AbstractEventLoop:
        with self._lock:
            loop = self._loop
            if self._state != RuntimeState.RUNNING or loop is None:
                for c in coros:
                    c.close()
                raise RuntimeError(f"async runtime is not running (state={self._state.value})")
        if blocking and threading.current_thread() is self._thread:
            for c in coros:
                c.close()
            raise RuntimeError("blocking on the runtime from its own loop thread would deadlock")
        return loop

    async def _bounded(self, coro: Awaitable[Any]) -> Any:
        assert self._semaphore is not None
        async with self._semaphore:
            return await coro

    def run(self, coro: Awaitable[Any], timeout: Optional[float] = None) -> Any:
        """Run one coroutine on the runtime loop and block for its result."""
        return self.run_all([coro], timeout=timeout)[0]

    def run_all(self, coros: list, timeout: Optional[float] = None) -> list:
        """Run coroutines concurrently on the runtime loop (at most max_concurrent_tasks at once).

        Results come back in input order; the first exception propagates.
        """
        coros = list(coros)
        loop = self._require_running(coros)

        async def _gather() -> list:
            return list(await asyncio.gather(*(self._bounded(c) for c in coros)))

        fut = asyncio.run_coroutine_threadsafe(_gather(), loop)
        try:
            return fut.result(timeout)
        except concurrent.futures.TimeoutError:
            fut.cancel()
            raise

    def spawn(self, group_name: str, task_id: str, coro: Awaitable[Any],
              name: str = "") -> concurrent.futures.Future:
        """Schedule a tracked coroutine in a task group; returns its future.

        The TaskRecord follows the real task: running when it starts, then
        completed / failed / cancelled by what the coroutine actually did.
        """
        group = self.get_task_group(group_name)
        if group is None:
            coro.close()  # type: ignore[attr-defined]
            raise KeyError(f"no task group {group_name!r}")
        loop = self._require_running([coro], blocking=False)
        group.add_task(task_id, name or task_id)

        async def _tracked() -> Any:
            group.mark_running(task_id)
            try:
                result = await self._bounded(coro)
            except asyncio.CancelledError:
                group.mark_cancelled(task_id)
                raise
            except BaseException as exc:
                group.mark_failed(task_id, repr(exc))
                raise
            group.mark_completed(task_id)
            with self._lock:
                self._total_tasks_completed += 1
            return result

        fut = asyncio.run_coroutine_threadsafe(_tracked(), loop)
        with self._lock:
            self._total_tasks_submitted += 1
            self._inflight.add(fut)
        fut.add_done_callback(self._discard_inflight)
        return fut

    def _discard_inflight(self, fut: concurrent.futures.Future) -> None:
        with self._lock:
            self._inflight.discard(fut)

    def shutdown(self, mode: ShutdownMode = ShutdownMode.GRACEFUL) -> bool:
        """Shut down the runtime.  Returns True if it was running.

        GRACEFUL waits up to shutdown_timeout_seconds for spawned tasks, then
        cancels what is left.  IMMEDIATE and FORCED cancel at once (FORCED does
        not wait for executor threads).  Afterwards no task record is left
        pending or running.
        """
        if threading.current_thread() is self._thread:
            raise RuntimeError("shutdown() from the runtime loop thread would deadlock")
        with self._lock:
            if self._state != RuntimeState.RUNNING:
                return False
            self._state = RuntimeState.DRAINING
            loop, thread, executor = self._loop, self._thread, self._executor
            inflight = set(self._inflight)
        if mode == ShutdownMode.GRACEFUL and inflight:
            concurrent.futures.wait(inflight, timeout=self._config.shutdown_timeout_seconds)
        for fut in inflight:
            fut.cancel()
        if inflight:
            concurrent.futures.wait(inflight, timeout=1.0)
        with self._lock:
            groups = list(self._groups.values())
        for group in groups:
            group.cancel_all()
        assert loop is not None and thread is not None and executor is not None
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5.0)
        executor.shutdown(wait=mode != ShutdownMode.FORCED, cancel_futures=True)
        with self._lock:
            self._loop = self._thread = self._executor = None
            self._state = RuntimeState.STOPPED
        return True

    def create_task_group(self, name: str) -> TaskGroup:
        """Create a new task group."""
        group = TaskGroup(name)
        with self._lock:
            self._groups[name] = group
        return group

    def get_task_group(self, name: str) -> Optional[TaskGroup]:
        """Look up a task group."""
        with self._lock:
            return self._groups.get(name)

    def submit_task(self, group_name: str, task_id: str,
                    name: str) -> Optional[TaskRecord]:
        """Register a bookkeeping-only task record in a group (no coroutine; see spawn)."""
        with self._lock:
            group = self._groups.get(group_name)
            if group is None:
                return None
            self._total_tasks_submitted += 1
        return group.add_task(task_id, name)

    def probe(self, timeout: float = 1.0) -> ProbeResult:
        """Health probe: time a callback through the live loop.

        HEALTHY only when the loop serviced the callback within ``timeout``.
        UNHEALTHY when the runtime is not running, its loop thread is dead, or
        the loop is stalled.  UNVERIFIABLE when the latency cannot be measured
        (probing from the loop's own thread, or the loop is closing).
        """
        with self._lock:
            state = self._state
            loop, thread, executor = self._loop, self._thread, self._executor
            active = sum(
                sum(1 for r in g._tasks.values() if r.state == TaskState.RUNNING)
                for g in self._groups.values())
        size = self._config.thread_pool_size

        def result(status: HealthStatus, reason: str, latency: Optional[float] = None) -> ProbeResult:
            return ProbeResult(
                status=status, state=state.value, loop_latency_ms=latency,
                active_tasks=active,
                thread_pool_active=executor.active if executor is not None else None,
                thread_pool_size=size, reason=reason)

        if state != RuntimeState.RUNNING or loop is None or thread is None:
            return result(HealthStatus.UNHEALTHY, f"runtime not running (state={state.value})")
        if not thread.is_alive():
            return result(HealthStatus.UNHEALTHY, "event loop thread is dead")
        if threading.current_thread() is thread:
            return result(HealthStatus.UNVERIFIABLE, "probe called from the loop thread; latency not measurable")
        serviced = threading.Event()
        t0 = time.monotonic()
        try:
            loop.call_soon_threadsafe(serviced.set)
        except RuntimeError as exc:
            return result(HealthStatus.UNVERIFIABLE, f"loop not accepting callbacks: {exc}")
        if not serviced.wait(timeout):
            return result(HealthStatus.UNHEALTHY, f"event loop stalled: callback not serviced within {timeout}s")
        return result(HealthStatus.HEALTHY, "loop serviced probe callback",
                      (time.monotonic() - t0) * 1000.0)

    def stats(self) -> dict[str, Any]:
        """Return runtime statistics."""
        with self._lock:
            return {
                "state": self._state.value,
                "config": {
                    "max_concurrent_tasks": self._config.max_concurrent_tasks,
                    "thread_pool_size": self._config.thread_pool_size,
                    "shutdown_timeout": self._config.shutdown_timeout_seconds,
                },
                "groups": len(self._groups),
                "total_submitted": self._total_tasks_submitted,
                "total_completed": self._total_tasks_completed,
                "uptime_seconds": (
                    time.monotonic() - self._started_at
                    if self._started_at > 0 else 0.0),
            }


def get_async_worker_runtime_info() -> dict:
    """Return metadata about the async worker runtime subsystem."""
    return {
        "version": 1,
        "components": ["AsyncWorkerRuntime", "TaskGroup", "RuntimeConfig"],
        "health_statuses": [h.value for h in HealthStatus],
        "runtime_states": [s.value for s in RuntimeState],
        "task_states": [s.value for s in TaskState],
        "shutdown_modes": [m.value for m in ShutdownMode],
    }
