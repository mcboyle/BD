"""Row 1075 -- Transactional Task State Pause, Drain, and Resumption Engine.

Provides thread-safe coordination for task pausing, in-flight operation draining to
clean transactional boundaries with timeout enforcement, and state resumption with
preserved checkpoints.
"""
from __future__ import annotations

import copy
import enum
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


class TaskState(str, enum.Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    DRAINING = "draining"
    DRAINED = "drained"
    TIMED_OUT = "timed_out"
    RESUMING = "resuming"
    FAILED = "failed"


class DrainStatus(str, enum.Enum):
    DRAINED = "drained"
    TIMED_OUT = "timed_out"
    ERROR = "error"


@dataclass
class DrainResult:
    status: str
    task_id: str
    in_flight_remaining: int
    elapsed_seconds: float
    error: Optional[str] = None

    @property
    def is_drained(self) -> bool:
        return self.status == DrainStatus.DRAINED.value

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TaskRecord:
    task_id: str
    state: str
    checkpoint_data: Dict[str, Any] = field(default_factory=dict)
    in_flight_ops: Set[str] = field(default_factory=set)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        res["in_flight_ops"] = sorted(self.in_flight_ops)
        return res


class TaskStatePauseDrainEngine:
    """Thread-safe coordinator for task pausing, draining, and resumption."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._tasks: Dict[str, TaskRecord] = {}

    def register_task(
        self,
        task_id: str,
        checkpoint_data: Optional[Dict[str, Any]] = None,
    ) -> TaskRecord:
        with self._lock:
            now = time.time()
            task = TaskRecord(
                task_id=str(task_id),
                state=TaskState.RUNNING.value,
                checkpoint_data=copy.deepcopy(dict(checkpoint_data or {})),
                in_flight_ops=set(),
                created_at=now,
                updated_at=now,
            )
            self._tasks[task_id] = task
            return task

    def acquire_operation(self, task_id: str, op_id: Optional[str] = None) -> str:
        """Register an active in-flight operation for a task."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                task = self.register_task(task_id)
            op = str(op_id) if op_id else f"op-{uuid.uuid4().hex[:8]}"
            task.in_flight_ops.add(op)
            task.updated_at = time.time()
            return op

    def release_operation(self, task_id: str, op_id: str) -> bool:
        """Release an active in-flight operation and signal waiting drainers."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or op_id not in task.in_flight_ops:
                return False
            task.in_flight_ops.remove(op_id)
            task.updated_at = time.time()
            self._condition.notify_all()
            return True

    def in_flight_count(self, task_id: str) -> int:
        with self._lock:
            task = self._tasks.get(task_id)
            return len(task.in_flight_ops) if task else 0

    def pause_task(
        self,
        task_id: str,
        checkpoint_data: Optional[Dict[str, Any]] = None,
    ) -> TaskRecord:
        with self._lock:
            task = self._tasks.get(task_id)
            now = time.time()
            if not task:
                task = TaskRecord(
                    task_id=str(task_id),
                    state=TaskState.PAUSED.value,
                    checkpoint_data=copy.deepcopy(dict(checkpoint_data or {})),
                    in_flight_ops=set(),
                    created_at=now,
                    updated_at=now,
                )
                self._tasks[task_id] = task
                return task

            task.state = TaskState.PAUSED.value
            task.updated_at = now
            if checkpoint_data:
                task.checkpoint_data.update(copy.deepcopy(checkpoint_data))
            return task

    def drain_task(
        self,
        task_id: str,
        timeout_seconds: float = 5.0,
    ) -> DrainResult:
        """Wait for in-flight operations on task_id to settle. Returns three-state DrainResult."""
        started = time.time()
        timeout = max(0.0, float(timeout_seconds))

        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return DrainResult(
                    status=DrainStatus.ERROR.value,
                    task_id=task_id,
                    in_flight_remaining=0,
                    elapsed_seconds=0.0,
                    error=f"Task '{task_id}' not found",
                )

            task.state = TaskState.DRAINING.value
            task.updated_at = started

        deadline = started + timeout
        with self._lock:
            while task.in_flight_ops:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=min(remaining, 0.05))

            elapsed = time.time() - started
            remaining_ops = len(task.in_flight_ops)

            if remaining_ops == 0:
                task.state = TaskState.DRAINED.value
                task.updated_at = time.time()
                return DrainResult(
                    status=DrainStatus.DRAINED.value,
                    task_id=task_id,
                    in_flight_remaining=0,
                    elapsed_seconds=elapsed,
                )
            else:
                task.state = TaskState.TIMED_OUT.value
                task.updated_at = time.time()
                return DrainResult(
                    status=DrainStatus.TIMED_OUT.value,
                    task_id=task_id,
                    in_flight_remaining=remaining_ops,
                    elapsed_seconds=elapsed,
                )

    def drain_all(self, timeout_seconds: float = 5.0) -> List[DrainResult]:
        """Drain all registered tasks."""
        with self._lock:
            tids = list(self._tasks.keys())
        return [self.drain_task(tid, timeout_seconds=timeout_seconds) for tid in tids]

    def resume_task(
        self,
        task_id: str,
    ) -> Tuple[TaskRecord, Dict[str, Any]]:
        """Transition task from PAUSED/DRAINED/TIMED_OUT to RUNNING and return restored checkpoint."""
        with self._lock:
            task = self._tasks.get(task_id)
            now = time.time()
            if not task:
                task = self.register_task(task_id)
                return task, {}

            task.state = TaskState.RUNNING.value
            task.updated_at = now
            return task, copy.deepcopy(task.checkpoint_data)

    def resume_all(self) -> List[Tuple[TaskRecord, Dict[str, Any]]]:
        """Resume all paused, drained, or timed_out tasks."""
        with self._lock:
            targets = [
                tid for tid, t in self._tasks.items()
                if t.state in (TaskState.PAUSED.value, TaskState.DRAINED.value, TaskState.TIMED_OUT.value)
            ]
        return [self.resume_task(tid) for tid in targets]

    def get_task_status(self, task_id: str) -> Optional[TaskRecord]:
        with self._lock:
            return self._tasks.get(task_id)

    def list_active_tasks(self) -> List[TaskRecord]:
        with self._lock:
            return [
                t for t in self._tasks.values()
                if t.state in (TaskState.RUNNING.value, TaskState.DRAINING.value)
            ]

    def export_telemetry(self) -> Dict[str, Any]:
        with self._lock:
            counts: Dict[str, int] = {}
            for t in self._tasks.values():
                counts[t.state] = counts.get(t.state, 0) + 1
            return {
                "total_tasks": len(self._tasks),
                "state_counts": counts,
                "active_tasks": len(self.list_active_tasks()),
            }


_GLOBAL_ENGINE: Optional[TaskStatePauseDrainEngine] = None
_GLOBAL_ENGINE_LOCK = threading.Lock()


def get_task_drain_engine() -> TaskStatePauseDrainEngine:
    global _GLOBAL_ENGINE
    if _GLOBAL_ENGINE is None:
        with _GLOBAL_ENGINE_LOCK:
            if _GLOBAL_ENGINE is None:
                _GLOBAL_ENGINE = TaskStatePauseDrainEngine()
    return _GLOBAL_ENGINE
