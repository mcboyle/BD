"""Row 1014: Asynchronous Non-Blocking Event-Driven WAL Flusher Pipeline.

Provides non-blocking event-driven WAL checkpointing, event coalescing,
asynchronous background pipeline execution, metric accounting, and integration with
bulk_downloader.db_maintenance.
"""
from __future__ import annotations

import logging
import os
import queue
import sqlite3
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

log = logging.getLogger("bulk_downloader.async_wal_flusher")


class FlushPriority(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


@dataclass
class FlushEvent:
    event_id: str
    db_path: str
    checkpoint_mode: str = "PASSIVE"
    timestamp: float = field(default_factory=time.time)


@dataclass
class FlushResult:
    event_id: str
    db_path: str
    checkpoint_mode: str
    success: bool
    busy: int = 0
    log_frames: int = 0
    checkpointed_frames: int = 0
    duration_ms: float = 0.0
    error: Optional[str] = None


@dataclass
class FlusherConfig:
    flush_interval_ms: float = 50.0
    batch_size: int = 10
    max_queue_size: int = 100
    default_checkpoint_mode: str = "PASSIVE"
    auto_start: bool = True
    max_results: int = 1000


@dataclass
class FlusherMetrics:
    total_enqueued: int = 0
    pending_events: int = 0
    total_flushed: int = 0
    coalesced_events: int = 0
    errors_count: int = 0


class AsyncWalFlusherPipeline:
    """Asynchronous non-blocking pipeline executing SQLite WAL checkpoints."""

    def __init__(self, config: Optional[FlusherConfig] = None) -> None:
        self.config = config or FlusherConfig()
        self._queue: queue.Queue[FlushEvent] = queue.Queue(maxsize=self.config.max_queue_size)
        # Bounded: oldest results are evicted so a long-running process cannot grow it.
        self._results: "OrderedDict[str, FlushResult]" = OrderedDict()
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        self._total_enqueued = 0
        self._total_flushed = 0
        self._coalesced_events = 0
        self._errors_count = 0

        if self.config.auto_start:
            self.start()

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._worker_loop, daemon=True, name="AsyncWalFlusher")
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

    def enqueue_flush(self, db_path: str, checkpoint_mode: Optional[str] = None) -> str:
        mode = checkpoint_mode or self.config.default_checkpoint_mode
        ev_id = f"ev_{uuid.uuid4().hex[:12]}"
        event = FlushEvent(event_id=ev_id, db_path=db_path, checkpoint_mode=mode)

        try:
            self._queue.put_nowait(event)
            with self._lock:
                self._total_enqueued += 1
        except queue.Full:
            with self._lock:
                self._errors_count += 1
                self._store_result(FlushResult(
                    event_id=ev_id,
                    db_path=db_path,
                    checkpoint_mode=mode,
                    success=False,
                    error="queue full; event dropped",
                ))
            log.warning("AsyncWalFlusher queue full; dropping event %s for %s", ev_id, db_path)

        return ev_id

    def submit_flush_event(
        self,
        event_type: str = "MAINTENANCE",
        priority: FlushPriority = FlushPriority.NORMAL,
        db_path: Optional[str] = None,
    ) -> bool:
        if not db_path:
            log.warning("AsyncWalFlusher: %s flush event names no database; nothing flushed", event_type)
            return False
        ev_id = self.enqueue_flush(db_path=db_path, checkpoint_mode="PASSIVE")
        res = self.get_event_result(ev_id)
        return res is None or res.success

    def get_event_result(self, event_id: str) -> Optional[FlushResult]:
        with self._lock:
            return self._results.get(event_id)

    def _store_result(self, result: FlushResult) -> None:
        # Caller holds self._lock.
        self._results[result.event_id] = result
        while len(self._results) > max(1, self.config.max_results):
            self._results.popitem(last=False)

    def get_metrics(self) -> FlusherMetrics:
        with self._lock:
            pending = self._queue.qsize()
            return FlusherMetrics(
                total_enqueued=self._total_enqueued,
                pending_events=pending,
                total_flushed=self._total_flushed,
                coalesced_events=self._coalesced_events,
                errors_count=self._errors_count,
            )

    def process_pending_batch(self) -> int:
        """Drain and process up to batch_size pending events, coalescing identical targets."""
        batch: List[FlushEvent] = []
        for _ in range(self.config.batch_size):
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break

        if not batch:
            return 0

        # Group by db_path and take the strictest checkpoint mode
        by_path: Dict[str, List[FlushEvent]] = {}
        for ev in batch:
            by_path.setdefault(ev.db_path, []).append(ev)

        flushed_count = 0
        for db_path, events in by_path.items():
            if len(events) > 1:
                with self._lock:
                    self._coalesced_events += len(events) - 1

            # Mode selection: TRUNCATE > RESTART > FULL > PASSIVE
            mode = "PASSIVE"
            modes = {e.checkpoint_mode.upper() for e in events}
            if "TRUNCATE" in modes:
                mode = "TRUNCATE"
            elif "RESTART" in modes:
                mode = "RESTART"
            elif "FULL" in modes:
                mode = "FULL"

            res = self._execute_checkpoint(db_path, mode, events[0].event_id)
            with self._lock:
                for ev in events:
                    # Map result across all coalesced events
                    self._store_result(FlushResult(
                        event_id=ev.event_id,
                        db_path=db_path,
                        checkpoint_mode=mode,
                        success=res.success,
                        busy=res.busy,
                        log_frames=res.log_frames,
                        checkpointed_frames=res.checkpointed_frames,
                        duration_ms=res.duration_ms,
                        error=res.error,
                    ))
                # A checkpoint that did not complete (missing file, busy, error) is
                # an error, never a flushed event.
                if res.success:
                    self._total_flushed += 1
                else:
                    self._errors_count += 1
            flushed_count += 1

        return flushed_count

    def _execute_checkpoint(self, db_path: str, mode: str, event_id: str) -> FlushResult:
        if not os.path.exists(db_path):
            return FlushResult(
                event_id=event_id,
                db_path=db_path,
                checkpoint_mode=mode,
                success=False,
                error="database file does not exist",
            )

        t0 = time.time()
        try:
            conn = sqlite3.connect(db_path, timeout=5.0)
            cur = conn.cursor()
            row = cur.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
            conn.close()

            busy, log_frames, checkpointed = 0, 0, 0
            if row:
                busy, log_frames, checkpointed = row

            dur_ms = (time.time() - t0) * 1000.0
            return FlushResult(
                event_id=event_id,
                db_path=db_path,
                checkpoint_mode=mode,
                success=(busy == 0),
                busy=busy,
                log_frames=log_frames,
                checkpointed_frames=checkpointed,
                duration_ms=dur_ms,
            )
        except Exception as exc:
            dur_ms = (time.time() - t0) * 1000.0
            log.warning("Checkpoint failed on %s: %s", db_path, exc)
            return FlushResult(
                event_id=event_id,
                db_path=db_path,
                checkpoint_mode=mode,
                success=False,
                duration_ms=dur_ms,
                error=str(exc),
            )

    def _worker_loop(self) -> None:
        interval_sec = max(0.005, self.config.flush_interval_ms / 1000.0)
        while True:
            with self._lock:
                if not self._running:
                    break
            self.process_pending_batch()
            time.sleep(interval_sec)


_GLOBAL_FLUSHER: Optional[AsyncWalFlusherPipeline] = None
_GLOBAL_LOCK = threading.Lock()


def get_async_wal_flusher() -> AsyncWalFlusherPipeline:
    global _GLOBAL_FLUSHER
    if _GLOBAL_FLUSHER is None:
        with _GLOBAL_LOCK:
            if _GLOBAL_FLUSHER is None:
                _GLOBAL_FLUSHER = AsyncWalFlusherPipeline(FlusherConfig(auto_start=True))
    return _GLOBAL_FLUSHER


def reset_async_wal_flusher() -> None:
    global _GLOBAL_FLUSHER
    with _GLOBAL_LOCK:
        if _GLOBAL_FLUSHER is not None:
            _GLOBAL_FLUSHER.stop()
            _GLOBAL_FLUSHER = None


def create_wal_flusher_pipeline(config: Optional[FlusherConfig] = None) -> AsyncWalFlusherPipeline:
    return AsyncWalFlusherPipeline(config=config)
