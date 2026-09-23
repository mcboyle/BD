"""Row 1010: Segregated Reader/Writer Connection Pools with Non-Blocking Busy Backoff.

Provides segregated connection pooling for SQLite databases:
- Multiple concurrent read-only connection handles with query_only enforcement.
- Serialized transaction-safe writer connection handle.
- Busy backoff that never blocks other leases: the retrying thread waits without
  holding the pool lock (readers keep running), and the wait ends early on a
  cancel_event. sqlite3 is a synchronous API, so the retrying thread itself waits.
- Granular pool metrics and lifecycle telemetry.
"""
from __future__ import annotations

import contextlib
import logging
import os
import random
import sqlite3
import threading
import time
from collections.abc import Callable, Generator
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class NonBlockingBusyBackoff:
    """Cooperative exponential backoff calculator with jitter."""

    def __init__(
        self,
        base_ms: float = 5.0,
        max_ms: float = 250.0,
        factor: float = 2.0,
        jitter: float = 0.1,
    ) -> None:
        self.base_ms = max(0.1, float(base_ms))
        self.max_ms = max(self.base_ms, float(max_ms))
        self.factor = max(1.0, float(factor))
        self.jitter = max(0.0, min(1.0, float(jitter)))

    def compute_delay_ms(self, attempt: int) -> float:
        """Calculate exponential delay in milliseconds for a retry attempt."""
        raw_delay = self.base_ms * (self.factor ** attempt)
        capped_delay = min(raw_delay, self.max_ms)
        if self.jitter > 0.0:
            jitter_range = capped_delay * self.jitter
            delta = random.uniform(-jitter_range, jitter_range)
            capped_delay = max(0.1, capped_delay + delta)
        return capped_delay

    def yield_cooperative(self, attempt: int, cancel_event: threading.Event | None = None) -> float:
        """Wait out the backoff interval on the calling thread; returns early if cancel_event is set."""
        delay_ms = self.compute_delay_ms(attempt)
        delay_sec = delay_ms / 1000.0
        if cancel_event is not None:
            cancel_event.wait(timeout=delay_sec)
        else:
            threading.Event().wait(timeout=delay_sec)
        return delay_ms


def execute_with_busy_backoff(
    operation: Callable[[], T],
    max_attempts: int = 5,
    base_backoff_ms: float = 5.0,
    max_backoff_ms: float = 250.0,
    backoff_hook: Callable[[int, float, Exception], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> T:
    """Execute a database operation, retrying on transient lock/busy errors with backoff."""
    backoff = NonBlockingBusyBackoff(base_ms=base_backoff_ms, max_ms=max_backoff_ms)
    last_exc: Exception | None = None

    for attempt in range(max_attempts):
        try:
            return operation()
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "locked" in msg or "busy" in msg:
                last_exc = exc
                delay = backoff.yield_cooperative(attempt, cancel_event=cancel_event)
                if backoff_hook is not None:
                    backoff_hook(attempt, delay, exc)
                continue
            raise

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("execute_with_busy_backoff exhausted attempts without an exception")


class SegregatedConnectionPool:
    """Segregated reader/writer connection pool for SQLite databases."""

    def __init__(
        self,
        db_path: str,
        max_readers: int = 4,
        max_writers: int = 1,
        busy_timeout_s: float = 10.0,
        connector: Callable[[], sqlite3.Connection] | None = None,
        reader_connector: Callable[[], sqlite3.Connection] | None = None,
    ) -> None:
        self.db_path = db_path
        self.max_readers = max(1, max_readers)
        self.max_writers = max(1, max_writers)
        self.busy_timeout_s = busy_timeout_s
        self._connector = connector
        self._reader_connector = reader_connector

        self._lock = threading.RLock()
        self._writer_semaphore = threading.Semaphore(self.max_writers)
        self._closed = False

        self._idle_readers: list[sqlite3.Connection] = []
        self._active_readers: set[sqlite3.Connection] = set()

        self._writer_conn: sqlite3.Connection | None = None
        self._active_writers = 0

        # Metrics telemetry
        self._total_reads = 0
        self._total_writes = 0
        self._backoff_events = 0
        self._busy_retry_events = 0
        self._close_failures = 0

    def _discard(self, conn: sqlite3.Connection) -> None:
        """Close one handle; a failing close() is counted in get_stats() and never stops the caller."""
        try:
            conn.close()
        except Exception as e:  # why: one bad handle must not leave the rest of a close loop open
            self._close_failures += 1
            logger.warning("SegregatedConnectionPool: close() failed: %s", e)

    def _open_raw_conn(self, read_only: bool = False) -> sqlite3.Connection:
        if read_only and self._reader_connector is not None:
            conn = self._reader_connector()
        elif self._connector is not None:
            conn = self._connector()
        else:
            conn = sqlite3.connect(
                self.db_path, timeout=self.busy_timeout_s, check_same_thread=False
            )
            conn.row_factory = sqlite3.Row
            # A handle without busy_timeout defeats the pool's backoff: a PRAGMA failure raises.
            conn.isolation_level = None
            cur = conn.execute("PRAGMA journal_mode=WAL;")
            _ = cur.fetchone()
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_s * 1000)};")
            conn.isolation_level = ""

        if read_only:
            conn.execute("PRAGMA query_only = ON;")

        return conn

    @contextlib.contextmanager
    def acquire_reader(self) -> Generator[sqlite3.Connection, None, None]:
        """Acquire a dedicated read-only connection from the reader pool."""
        with self._lock:
            if self._closed:
                raise RuntimeError("Cannot acquire reader from a closed SegregatedConnectionPool")

            conn: sqlite3.Connection | None = None
            while self._idle_readers:
                candidate = self._idle_readers.pop()
                try:
                    candidate.execute("SELECT 1;").fetchone()
                    conn = candidate
                    break
                except Exception:
                    self._discard(candidate)

            if conn is None:
                conn = self._open_raw_conn(read_only=True)

            self._active_readers.add(conn)
            self._total_reads += 1

        try:
            yield conn
        finally:
            with self._lock:
                self._active_readers.discard(conn)
                if not self._closed and len(self._idle_readers) < self.max_readers:
                    self._idle_readers.append(conn)
                else:
                    self._discard(conn)

    @contextlib.contextmanager
    def acquire_writer(self) -> Generator[sqlite3.Connection, None, None]:
        """Acquire the serialized writer; waits up to busy_timeout_s for the slot."""
        acquired = self._writer_semaphore.acquire(blocking=True, timeout=self.busy_timeout_s)
        if not acquired:
            with self._lock:
                self._backoff_events += 1
            raise sqlite3.OperationalError(
                f"Writer connection acquisition timed out after {self.busy_timeout_s}s"
            )

        try:
            with self._lock:
                if self._closed:
                    raise RuntimeError("Cannot acquire writer from a closed SegregatedConnectionPool")

                if self._writer_conn is None:
                    self._writer_conn = self._open_raw_conn(read_only=False)

                self._active_writers += 1
                self._total_writes += 1
                conn = self._writer_conn

            yield conn
        finally:
            with self._lock:
                self._active_writers -= 1
                if self._closed and self._active_writers == 0 and self._writer_conn is not None:
                    self._discard(self._writer_conn)
                    self._writer_conn = None
            self._writer_semaphore.release()

    def get_stats(self) -> dict[str, Any]:
        """Return pool status and metric counters."""
        with self._lock:
            return {
                "active_readers": len(self._active_readers),
                "idle_readers": len(self._idle_readers),
                "max_readers": self.max_readers,
                "active_writers": self._active_writers,
                "max_writers": self.max_writers,
                "total_reads": self._total_reads,
                "total_writes": self._total_writes,
                "backoff_count": self._backoff_events,
                "busy_retries": self._busy_retry_events,
                "close_failures": self._close_failures,
                "closed": self._closed,
            }

    def retire(self) -> None:
        """Refuse new leases and close idle handles; leases still out close on release."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for r in tuple(self._idle_readers):
                self._discard(r)
            self._idle_readers.clear()
            if self._active_writers == 0 and self._writer_conn is not None:
                self._discard(self._writer_conn)
                self._writer_conn = None

    def close(self) -> None:
        """Physically close all pooled reader and writer connections."""
        with self._lock:
            if self._closed:
                return
            self._closed = True

            for r in tuple(self._idle_readers):
                self._discard(r)
            self._idle_readers.clear()

            for r in tuple(self._active_readers):
                self._discard(r)
            self._active_readers.clear()

            if self._writer_conn is not None:
                self._discard(self._writer_conn)
                self._writer_conn = None
