"""bulk_downloader.db_lifecycle -- Row 1025: Database Connection Pool Lease/Release Lifecycle & Thread Cleanup Traps.

Provides centralized tracking for SQLite connection pool leases, thread-affine idle handles,
thread cleanup traps, and bulk connection eviction across threads.
Follows Fleet Rule 21 (zero site logins touched).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)


class ThreadCleanupTrap:
    """Trap attached to thread-local storage that executes connection cleanup on thread exit."""

    def __init__(self, conn: Any, manager: DBConnectionLifecycleManager) -> None:
        self.conn = conn
        self.manager = manager

    def __del__(self) -> None:
        if self.conn is not None:
            try:
                self.manager.cleanup_connection(self.conn)
            except Exception as exc:
                log.debug("ThreadCleanupTrap finalizer failed: %s", exc)


class DBConnectionLifecycleManager:
    """Manages database connection lease/release lifecycle, thread cleanup traps, and pooling telemetry."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # Per-thread lease stack to handle re-entrant / nested leases safely (D2 fix)
        self._active_leases: dict[int, list[dict[str, Any]]] = {}
        self._thread_connections: dict[int, tuple[Any, Any]] = {}  # tid -> (cache_key, conn)
        self._total_leases_acquired = 0
        self._total_thread_cleanups = 0
        self._close_fn: Optional[Callable[[Any], None]] = None

    def set_close_callback(self, close_fn: Callable[[Any], None]) -> None:
        """Register the underlying physical connection close callback."""
        self._close_fn = close_fn

    def install_thread_trap(self, thread_local: Any, conn: Any) -> None:
        """Ensure thread cleanup trap is attached for the connection on the thread."""
        traps = getattr(thread_local, "_cleanup_traps", None)
        if traps is None:
            traps = []
            thread_local._cleanup_traps = traps
        for trap in traps:
            if trap.conn is conn:
                return
        traps.append(ThreadCleanupTrap(conn, self))

    def is_leased(self, conn: Any) -> bool:
        """Check if connection is currently in an active lease."""
        with self._lock:
            for stack in self._active_leases.values():
                for entry in stack:
                    if entry["conn"] is conn:
                        return True
            return False

    def acquire_lease(self, thread_id: int, conn: Any, cache_key: Any) -> None:
        """Record acquisition of a connection lease by a thread."""
        with self._lock:
            self._total_leases_acquired += 1
            stack = self._active_leases.setdefault(thread_id, [])
            stack.append({
                "conn": conn,
                "cache_key": cache_key,
                "started_at": time.time(),
            })
            # Remove from idle pool while leased
            self._thread_connections.pop(thread_id, None)

    def release_lease(self, thread_id: int, conn: Any, reusable: bool, cache_key: Any) -> bool:
        """Record release of a connection lease. Returns True if pooled as idle."""
        with self._lock:
            stack = self._active_leases.get(thread_id)
            if stack:
                for i in range(len(stack) - 1, -1, -1):
                    if stack[i]["conn"] is conn:
                        stack.pop(i)
                        break
                else:
                    stack.pop()
                if not stack:
                    self._active_leases.pop(thread_id, None)

            # Only pool as idle if no outer leases remain active on this thread (D2 fix)
            if reusable and cache_key is not None and thread_id not in self._active_leases:
                self._thread_connections[thread_id] = (cache_key, conn)
                return True
            return False

    def has_thread_connection(self, thread_id: int) -> bool:
        """Check if an idle connection is registered for the specified thread."""
        with self._lock:
            return thread_id in self._thread_connections

    def get_thread_connection(self, thread_id: int) -> Optional[tuple[Any, Any]]:
        """Retrieve and claim the idle connection for the specified thread."""
        with self._lock:
            return self._thread_connections.pop(thread_id, None)

    def cleanup_connection(self, cx: Any) -> bool:
        """Evict, rollback, and physically close a specific connection."""
        with self._lock:
            if self.is_leased(cx):
                return False
            # Remove from idle tracking if present
            owner_tid = None
            for tid, entry in list(self._thread_connections.items()):
                if entry[1] is cx:
                    owner_tid = tid
                    self._thread_connections.pop(tid, None)
                    break

            try:
                setattr(cx, "_pending_close", True)
            except Exception:
                pass

            current_tid = threading.get_ident()
            # Under SQLite's check_same_thread=True, physical close must occur on the creator thread.
            # If called from owner thread (e.g. ThreadCleanupTrap on thread exit, or cleanup_thread_connections),
            # close physically now. If called cross-thread, mark _pending_close for deferred close.
            if owner_tid is None or owner_tid == current_tid:
                try:
                    import sqlite3
                    sqlite3.Connection.rollback(cx)
                except Exception:
                    try:
                        cx.rollback()
                    except Exception:
                        pass

                closed = False
                if self._close_fn is not None:
                    try:
                        self._close_fn(cx)
                        closed = True
                    except Exception as exc:
                        log.debug("Close callback failed during connection cleanup: %s", exc)
                elif hasattr(cx, "_force_close"):
                    try:
                        cx._force_close()
                        closed = True
                    except Exception:
                        pass
                elif hasattr(cx, "close"):
                    try:
                        cx.close()
                        closed = True
                    except Exception:
                        pass

                if closed:
                    self._total_thread_cleanups += 1
                return closed
            else:
                # Deferred cross-thread close: evicted from pool and flagged _pending_close.
                self._total_thread_cleanups += 1
                return True

    def cleanup_thread_connections(self, thread_id: Optional[int] = None) -> int:
        """Evict, rollback, and close idle connection for a thread (or calling thread)."""
        tid = thread_id if thread_id is not None else threading.get_ident()
        with self._lock:
            entry = self._thread_connections.pop(tid, None)
            if entry is not None:
                _, cx = entry
                return 1 if self.cleanup_connection(cx) else 0
            return 0

    def close_all_pooled(self) -> int:
        """Close all idle pooled connections across all threads."""
        count = 0
        with self._lock:
            for entry in list(self._thread_connections.values()):
                _, cx = entry
                if self.cleanup_connection(cx):
                    count += 1
            self._thread_connections.clear()
        return count

    def get_metrics(self) -> dict[str, Any]:
        """Return snapshot of connection lifecycle metrics."""
        with self._lock:
            total_active = sum(len(stack) for stack in self._active_leases.values())
            return {
                "active_leases": total_active,
                "idle_connections": len(self._thread_connections),
                "total_leases_acquired": self._total_leases_acquired,
                "total_thread_cleanups": self._total_thread_cleanups,
                "timestamp": time.time(),
            }


_GLOBAL_LIFECYCLE_MANAGER: Optional[DBConnectionLifecycleManager] = None
_INIT_LOCK = threading.Lock()


def get_connection_lifecycle_manager() -> DBConnectionLifecycleManager:
    """Return the global DBConnectionLifecycleManager instance."""
    global _GLOBAL_LIFECYCLE_MANAGER
    if _GLOBAL_LIFECYCLE_MANAGER is None:
        with _INIT_LOCK:
            if _GLOBAL_LIFECYCLE_MANAGER is None:
                _GLOBAL_LIFECYCLE_MANAGER = DBConnectionLifecycleManager()
    return _GLOBAL_LIFECYCLE_MANAGER
