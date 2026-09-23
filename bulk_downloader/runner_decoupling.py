"""bulk_downloader.runner_decoupling -- Row 1021: Decoupling Monolithic SiteRunner into Subsystems.

Decomposes monolithic SiteRunner state, coordination, and lifecycle into modular,
single-responsibility subsystems:
- LifecycleSubsystem: runner state machine, stop/pause synchronization, worker thread lifecycle.
- QueueSubsystem: URL queue, job records, priority management, and item tracking.
- TransportSubsystem: rate limit auto-resume state, byte accumulators, and transport policy.
- TelemetrySubsystem: transfer byte accounting, error frequency tracking, and metrics snapshots.
- RunnerSubsystemManager: registry and coordinator managing subsystem instances per runner.

Follows Fleet Rule 21 (zero site logins touched).
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Optional

log = logging.getLogger(__name__)


class BaseRunnerSubsystem:
    """Abstract base class representing a decoupled runner subsystem."""

    def __init__(self, runner: Any, name: str) -> None:
        self.runner = runner
        self.name = name

    def initialize(self) -> None:
        """Initialize subsystem resources."""
        pass

    def is_active(self) -> bool:
        """Check if subsystem is actively operating."""
        return True

    def status(self) -> dict[str, Any]:
        """Return runtime status dictionary for the subsystem."""
        return {"name": self.name, "active": self.is_active()}


class LifecycleSubsystem(BaseRunnerSubsystem):
    """Manages runner state machine, pause/stop events, and worker thread lifecycle."""

    def __init__(self, runner: Any) -> None:
        super().__init__(runner, "lifecycle")
        self.state = "idle"
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.worker_threads: list[threading.Thread] = []
        self._state_lock = threading.Lock()

    def set_state(self, new_state: str) -> None:
        with self._state_lock:
            self.state = new_state
            if new_state == "running":
                self.stop_event.clear()
                self.pause_event.set()
            elif new_state == "paused":
                self.pause_event.clear()
            elif new_state == "stopped":
                self.stop_event.set()
                self.pause_event.set()

    def pause(self) -> None:
        with self._state_lock:
            self.state = "paused"
            self.pause_event.clear()

    def resume(self) -> None:
        with self._state_lock:
            self.state = "running"
            self.stop_event.clear()
            self.pause_event.set()

    def stop(self) -> None:
        with self._state_lock:
            self.state = "stopped"
            self.stop_event.set()
            self.pause_event.set()

    def is_active(self) -> bool:
        return self.state in ("running", "paused") and not self.stop_event.is_set()

    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "is_stopped": self.stop_event.is_set(),
            "is_paused": not self.pause_event.is_set(),
            "active_workers": len([t for t in self.worker_threads if t.is_alive()]),
        }


class QueueSubsystem(BaseRunnerSubsystem):
    """Manages URL queue, job registry, and item dispatch."""

    def __init__(self, runner: Any) -> None:
        super().__init__(runner, "queue")
        self.url_queue: queue.Queue = queue.Queue()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.urls: list[str] = []
        self._lock = threading.Lock()

    def add_url(self, url: str) -> None:
        with self._lock:
            self.urls.append(url)
            self.url_queue.put(url)

    def record_job(self, job_id: str, job_info: dict[str, Any]) -> None:
        with self._lock:
            self.jobs[job_id] = dict(job_info)

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            return self.jobs.get(job_id)

    def update_job_status(self, job_id: str, status: str, **kwargs: Any) -> None:
        with self._lock:
            if job_id in self.jobs:
                self.jobs[job_id]["status"] = status
                self.jobs[job_id].update(kwargs)
            else:
                entry = {"status": status}
                entry.update(kwargs)
                self.jobs[job_id] = entry

    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "queue_size": self.url_queue.qsize(),
            "total_urls": len(self.urls),
            "total_jobs": len(self.jobs),
        }


class TransportSubsystem(BaseRunnerSubsystem):
    """Manages rate limiting, daily byte accumulators, and transport policy."""

    def __init__(self, runner: Any) -> None:
        super().__init__(runner, "transport")
        self.rl_autostart = False
        self.accumulators: set[Any] = set()
        self.flush_failures = 0
        self.lock = threading.Lock()

    def set_rate_limit_autostart(self, enabled: bool) -> None:
        self.rl_autostart = bool(enabled)

    def register_accumulator(self, acc: Any) -> None:
        if acc is None:
            return
        with self.lock:
            self.accumulators.add(acc)

    def unregister_accumulator(self, acc: Any) -> None:
        if acc is None:
            return
        with self.lock:
            self.accumulators.discard(acc)

    def contains_accumulator(self, acc: Any) -> bool:
        with self.lock:
            return acc in self.accumulators

    def flush_accumulators(self) -> int:
        flushed_bytes = 0
        with self.lock:
            accumulators = tuple(self.accumulators)
        for acc in accumulators:
            try:
                if hasattr(acc, "flush"):
                    res = acc.flush()
                    if isinstance(res, (int, float)):
                        flushed_bytes += int(res)
                elif isinstance(acc, dict) and "bytes" in acc:
                    flushed_bytes += int(acc.get("bytes", 0))
            except Exception:
                # One broken accumulator must not stop the rest from flushing, but its
                # bytes are unaccounted: count it where status() reports it.
                with self.lock:
                    self.flush_failures += 1
                log.warning("byte accumulator %r failed to flush", acc, exc_info=True)
        return flushed_bytes

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "name": self.name,
                "rl_autostart": self.rl_autostart,
                "active_accumulators": len(self.accumulators),
                "flush_failures": self.flush_failures,
            }


class TelemetrySubsystem(BaseRunnerSubsystem):
    """Manages transfer byte accounting, error frequency tracking, and metrics snapshots."""

    def __init__(self, runner: Any) -> None:
        super().__init__(runner, "telemetry")
        self.total_bytes = 0
        self.error_counts: dict[str, int] = {}
        self.start_time = time.monotonic()
        self._lock = threading.Lock()

    def record_bytes(self, byte_count: int) -> None:
        with self._lock:
            self.total_bytes += max(0, int(byte_count))

    def record_error(self, error_type: str) -> None:
        with self._lock:
            self.error_counts[error_type] = self.error_counts.get(error_type, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            elapsed = max(0.001, time.monotonic() - self.start_time)
            speed_bps = self.total_bytes / elapsed
            return {
                "total_bytes": self.total_bytes,
                "elapsed_seconds": round(elapsed, 2),
                "speed_bps": round(speed_bps, 2),
                "errors": dict(self.error_counts),
                "timestamp": time.time(),
            }

    def status(self) -> dict[str, Any]:
        return self.snapshot()


class RunnerSubsystemManager:
    """Central registry and coordinator managing decoupled subsystems for a runner."""

    def __init__(self, runner: Any) -> None:
        self.runner = runner
        self._subsystems: dict[str, BaseRunnerSubsystem] = {}
        self._lock = threading.Lock()

    def register(self, subsystem: BaseRunnerSubsystem) -> None:
        with self._lock:
            self._subsystems[subsystem.name] = subsystem

    def get(self, name: str) -> Optional[BaseRunnerSubsystem]:
        with self._lock:
            return self._subsystems.get(name)

    def list_subsystems(self) -> list[str]:
        with self._lock:
            return list(self._subsystems.keys())

    def status_summary(self) -> dict[str, Any]:
        with self._lock:
            return {name: sub.status() for name, sub in self._subsystems.items()}
