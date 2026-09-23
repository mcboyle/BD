"""Row 994: SQLite lock contention profiler (RESCOPED per RULING-2338-row994-RESCOPE-a).

Query-plan classification is NOT here: dev_suite.db_tools._explain_query_plan
and index_advisor (/api/dev/index_advisor) already own it.  This module only
records lock stalls the product actually observes, so an operator can tell a
quiet database from a contended one.

A report is ``unobserved`` until a feed attaches (``attach_feed``): a zero
count from a profiler nothing reports into says nothing about contention, and
must not read as "no contention".  The one feed today is db.db_init's
locked-retry loop, the product's application-level lock retry.
"""
from __future__ import annotations

import collections
import threading
import time
from typing import Any, Dict, Optional

SQLITE_BUSY = "SQLITE_BUSY"
SQLITE_LOCKED = "SQLITE_LOCKED"


def classify_lock_error(error: object) -> Optional[str]:
    """SQLITE_BUSY / SQLITE_LOCKED for a lock error's message, None otherwise.

    SQLite words the two differently: SQLITE_LOCKED (a shared-cache table
    lock) reads "database table is locked"; SQLITE_BUSY (another connection
    holds the file lock) reads "database is locked".  Matching "locked" alone
    labels every busy stall as LOCKED.
    """
    msg = str(error).lower()
    if "table is locked" in msg:
        return SQLITE_LOCKED
    if "database is locked" in msg or "busy" in msg:
        return SQLITE_BUSY
    return None


class SQLiteLockContentionProfiler:
    """Thread-safe recorder and aggregator for SQLite lock contention events."""

    def __init__(self, max_history: int = 100) -> None:
        self._lock = threading.Lock()
        self._feeds: set = set()
        self._total_events = 0
        self._total_stall_ms = 0.0
        self._max_stall_ms = 0.0
        self._contended_tables: Dict[str, Dict[str, Any]] = collections.defaultdict(
            lambda: {"events": 0, "total_stall_ms": 0.0, "max_stall_ms": 0.0, "last_error": ""}
        )
        self._recent_events: collections.deque = collections.deque(maxlen=max_history)

    def attach_feed(self, name: str) -> None:
        """Declare that ``name`` reports its stalls here; the report is observed from now on."""
        with self._lock:
            self._feeds.add(str(name))

    def record_contention(
        self,
        table: str,
        duration_ms: float,
        error_type: str = SQLITE_BUSY,
        statement: str = "",
    ) -> None:
        """Record a lock contention stall event."""
        dur = max(0.0, float(duration_ms))
        tbl = table or "unknown"
        now = time.time()

        with self._lock:
            self._total_events += 1
            self._total_stall_ms += dur
            if dur > self._max_stall_ms:
                self._max_stall_ms = dur

            t_stats = self._contended_tables[tbl]
            t_stats["events"] += 1
            t_stats["total_stall_ms"] += dur
            if dur > t_stats["max_stall_ms"]:
                t_stats["max_stall_ms"] = dur
            t_stats["last_error"] = str(error_type)

            self._recent_events.append({
                "ts": now,
                "table": tbl,
                "duration_ms": dur,
                "error_type": error_type,
                "statement": statement[:200] if statement else "",
            })

    def get_contention_report(self) -> Dict[str, Any]:
        """Return a snapshot of lock contention metrics."""
        with self._lock:
            avg_stall = (
                self._total_stall_ms / self._total_events
                if self._total_events > 0
                else 0.0
            )
            return {
                "status": "observed" if self._feeds else "unobserved",
                "feeds": sorted(self._feeds),
                "total_contention_events": self._total_events,
                "total_stall_ms": round(self._total_stall_ms, 3),
                "max_stall_ms": round(self._max_stall_ms, 3),
                "avg_stall_ms": round(avg_stall, 3),
                "contended_tables": {k: dict(v) for k, v in self._contended_tables.items()},
                "recent_events_count": len(self._recent_events),
            }

    def reset(self) -> None:
        """Reset all recorded contention statistics (feeds stay attached)."""
        with self._lock:
            self._total_events = 0
            self._total_stall_ms = 0.0
            self._max_stall_ms = 0.0
            self._contended_tables.clear()
            self._recent_events.clear()


def record_lock_stall(
    table: str,
    stall_ms: float,
    error: object = "",
    statement: str = "",
) -> None:
    """Record one stall on the process profiler, typed from the error's message."""
    get_contention_profiler().record_contention(
        table=table,
        duration_ms=stall_ms,
        error_type=classify_lock_error(error) or SQLITE_BUSY,
        statement=statement,
    )


_GLOBAL_PROFILER: Optional[SQLiteLockContentionProfiler] = None
_PROFILER_LOCK = threading.Lock()


def get_contention_profiler() -> SQLiteLockContentionProfiler:
    """Return the process-wide contention profiler."""
    global _GLOBAL_PROFILER
    with _PROFILER_LOCK:
        if _GLOBAL_PROFILER is None:
            _GLOBAL_PROFILER = SQLiteLockContentionProfiler()
        return _GLOBAL_PROFILER


def reset_contention_profiler() -> None:
    """Test helper. Drop the process-wide profiler."""
    global _GLOBAL_PROFILER
    with _PROFILER_LOCK:
        _GLOBAL_PROFILER = None
