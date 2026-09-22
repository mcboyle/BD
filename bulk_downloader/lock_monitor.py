"""Distributed Mutex Contention & Lock Queue Monitor (Row 1059).

Provides lock contention detection, waiter queue depth tracking, hold duration
latency telemetry, and registry aggregation for multithreaded synchronization.
"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class LockMetrics:
    """Telemetry metrics captured for a monitored synchronization lock."""
    name: str
    acquire_count: int = 0
    contention_count: int = 0
    wait_time_total: float = 0.0
    wait_time_max: float = 0.0
    hold_time_total: float = 0.0
    hold_time_max: float = 0.0
    current_waiters: int = 0


class MonitoredLock:
    """Wraps threading.Lock or threading.RLock to monitor contention and queue depth."""

    def __init__(self, name: str, raw_lock: Any | None = None) -> None:
        self.name = name
        self.raw_lock = raw_lock if raw_lock is not None else threading.Lock()
        self._meta_lock = threading.Lock()
        self._acquire_count = 0
        self._contention_count = 0
        self._wait_time_total = 0.0
        self._wait_time_max = 0.0
        self._hold_time_total = 0.0
        self._hold_time_max = 0.0
        self._current_waiters = 0
        self._thread_depth: dict[int, int] = {}
        self._thread_hold_start: dict[int, float] = {}

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        """Acquires underlying lock while recording waiter queue depth and contention."""
        with self._meta_lock:
            self._current_waiters += 1

        start_wait = time.monotonic()
        contended = False

        # Attempt immediate non-blocking acquisition if blocking requested without timeout
        if blocking and timeout < 0:
            acquired = self.raw_lock.acquire(blocking=False)
            if not acquired:
                contended = True
                acquired = self.raw_lock.acquire(blocking=True)
        else:
            acquired = self.raw_lock.acquire(blocking=blocking, timeout=timeout)
            if not acquired:
                # Any failed acquire (try-lock or timeout) indicates an existing holder -> contention
                contended = True

        elapsed_wait = max(0.0, time.monotonic() - start_wait)

        with self._meta_lock:
            self._current_waiters -= 1
            if contended or elapsed_wait > 1e-4 or not acquired:
                self._contention_count += 1
            if acquired:
                self._acquire_count += 1
                self._wait_time_total += elapsed_wait
                self._wait_time_max = max(self._wait_time_max, elapsed_wait)
                tid = threading.get_ident()
                cur_depth = self._thread_depth.get(tid, 0)
                if cur_depth == 0:
                    self._thread_hold_start[tid] = time.monotonic()
                self._thread_depth[tid] = cur_depth + 1
            else:
                self._wait_time_total += elapsed_wait
                self._wait_time_max = max(self._wait_time_max, elapsed_wait)

        return acquired

    def release(self) -> None:
        """Releases underlying lock and records hold duration metrics."""
        tid = threading.get_ident()

        with self._meta_lock:
            cur_depth = self._thread_depth.get(tid, 0)
            if cur_depth <= 0:
                raise RuntimeError(f"cannot release un-acquired lock {self.name!r} from thread {tid}")

            self._thread_depth[tid] = cur_depth - 1
            if self._thread_depth[tid] == 0:
                del self._thread_depth[tid]
                hold_start = self._thread_hold_start.pop(tid, None)
                if hold_start is not None:
                    elapsed_hold = max(0.0, time.monotonic() - hold_start)
                    self._hold_time_total += elapsed_hold
                    self._hold_time_max = max(self._hold_time_max, elapsed_hold)

        self.raw_lock.release()

    def locked(self) -> bool:
        """Returns True if the underlying lock is currently locked."""
        if hasattr(self.raw_lock, "locked"):
            return self.raw_lock.locked()
        with self._meta_lock:
            return bool(self._thread_depth)

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.release()

    def __getattr__(self, name: str) -> Any:
        try:
            raw = self.__dict__["raw_lock"]
        except KeyError:
            raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}") from None
        return getattr(raw, name)

    def __dir__(self) -> list[str]:
        raw_attrs = dir(self.raw_lock) if "raw_lock" in self.__dict__ else []
        return sorted(set(dir(type(self)) + list(self.__dict__.keys()) + raw_attrs))

    def get_metrics(self) -> LockMetrics:
        """Returns snapshot of current lock contention metrics."""
        with self._meta_lock:
            return LockMetrics(
                name=self.name,
                acquire_count=self._acquire_count,
                contention_count=self._contention_count,
                wait_time_total=self._wait_time_total,
                wait_time_max=self._wait_time_max,
                hold_time_total=self._hold_time_total,
                hold_time_max=self._hold_time_max,
                current_waiters=self._current_waiters,
            )


class LockMonitorRegistry:
    """Registry maintaining active monitored locks and generating system-wide reports."""

    def __init__(self) -> None:
        self._locks: dict[str, MonitoredLock] = {}
        self._registry_lock = threading.Lock()

    def register(self, name: str, raw_lock: Any | None = None) -> MonitoredLock:
        """Registers a named lock under monitoring (idempotent across multiple calls)."""
        with self._registry_lock:
            if name not in self._locks:
                self._locks[name] = MonitoredLock(name, raw_lock)
            return self._locks[name]

    def get(self, name: str) -> MonitoredLock | None:
        """Retrieves registered monitored lock by name."""
        with self._registry_lock:
            return self._locks.get(name)

    def generate_report(self) -> dict[str, dict[str, Any]]:
        """Generates comprehensive report of all registered locks."""
        with self._registry_lock:
            locks = list(self._locks.values())

        report: dict[str, dict[str, Any]] = {}
        for lock in locks:
            report[lock.name] = asdict(lock.get_metrics())
        return report

    def reset(self) -> None:
        """Clears all registered locks in registry."""
        with self._registry_lock:
            self._locks.clear()


_GLOBAL_REGISTRY = LockMonitorRegistry()


def get_lock_monitor_registry() -> LockMonitorRegistry:
    """Returns the global lock monitor registry."""
    return _GLOBAL_REGISTRY


def register_monitored_lock(name: str, raw_lock: Any | None = None) -> MonitoredLock:
    """Convenience helper to register and monitor a named lock."""
    return _GLOBAL_REGISTRY.register(name, raw_lock)


def reset_lock_monitor_registry() -> None:
    """Resets the global lock monitor registry."""
    _GLOBAL_REGISTRY.reset()


_PRODUCTION_LOCK_MODULES = (
    "bulk_downloader.app_state",
    "bulk_downloader.auth_throttle",
    "bulk_downloader.events",
    "bulk_downloader.circuit_breaker",
    "bulk_downloader.bg_scheduler",
    "bulk_downloader.cookie_relogin",
    "bulk_downloader.bandwidth_shape",
)

_discovered = False


def _discover_production_locks() -> None:
    global _discovered
    if _discovered:
        return
    import importlib
    for mod in _PRODUCTION_LOCK_MODULES:
        try:
            importlib.import_module(mod)
        except (ImportError, AttributeError):
            pass
    _discovered = True


def get_global_contention_report() -> dict[str, Any]:
    """Returns aggregated global report of lock contentions and queues."""
    _discover_production_locks()
    report = _GLOBAL_REGISTRY.generate_report()
    total_contentions = sum(m.get("contention_count", 0) for m in report.values())
    total_acquires = sum(m.get("acquire_count", 0) for m in report.values())
    return {
        "locks": report,
        "total_locks_monitored": len(report),
        "total_contentions": total_contentions,
        "total_acquires": total_acquires,
    }
