"""Row 1025: Database Connection Pool Lease/Release Lifecycle & Thread Cleanup Traps (DBConnectionLifecycleManager).

Provides centralized connection pool lease/release lifecycle management, active lease
stack tracking, thread cleanup traps for worker pools, and cross-thread pooled connection eviction.

RED on baseline:
  - Worker thread exit leaks open SQLite handles (total_changes succeeds instead of ProgrammingError).
  - Nested leases contaminate _DB_CONN_LOCAL.idle while outer lease is still active.
  - Stale / force-closed handles in pool are re-yielded without liveness check and fail on next query.
  - No DBConnectionLifecycleManager or cleanup_thread_connections in bulk_downloader.db.
"""
from __future__ import annotations

import sqlite3
import threading
import pytest

BD_GATE_SCOPE = "repo-wide"


def test_thread_cleanup_trap_closes_handle_on_thread_exit():
    """Verify thread cleanup trap physically closes the SQLite connection handle on thread exit (R1 fix).

    RED on base: Worker thread exits, but captured_cx.total_changes evaluates to 0 (handle leaked).
    GREEN on cut: ThreadCleanupTrap finalizer closes connection on thread exit, raising ProgrammingError.
    """
    from bulk_downloader.db import db_conn

    captured_cx = None

    def worker():
        nonlocal captured_cx
        with db_conn() as cx:
            captured_cx = cx

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=2.0)

    assert captured_cx is not None
    # On base, the handle is leaked and total_changes succeeds.
    # On cut, ThreadCleanupTrap physically closed the handle on thread termination.
    with pytest.raises(sqlite3.ProgrammingError, match="Cannot operate on a closed database"):
        _ = captured_cx.total_changes


def test_nested_db_conn_lease_stack_isolation():
    """Verify nested leases on the same thread maintain stack discipline and do not contaminate idle pool (D2 fix).

    RED on base: Inner lease exit sets _DB_CONN_LOCAL.idle while outer lease is still active.
    GREEN on cut: Lease stack manager keeps _DB_CONN_LOCAL.idle None until outer lease completes.
    """
    from bulk_downloader.db import _DB_CONN_LOCAL, db_conn

    with db_conn() as outer:
        assert outer is not None
        with db_conn() as inner:
            assert inner is not None

        # While outer lease is still active, inner lease must NOT pool connection into idle
        assert getattr(_DB_CONN_LOCAL, "idle", None) is None, (
            "Idle pool was contaminated while outer lease was still active (D2 defect)"
        )

    # Only after outer lease exits is connection pooled as idle
    assert getattr(_DB_CONN_LOCAL, "idle", None) is not None


def test_db_conn_liveness_probe_recovers_from_stale_handle():
    """Verify db_conn liveness probe detects closed idle handle and yields fresh connection (M4 fix).

    RED on base: Yields closed connection cx1 from idle pool, failing on next statement execution.
    GREEN on cut: Liveness probe catches closed state and reopens a fresh connection.
    """
    from bulk_downloader.db import _DB_CONN_LOCAL, db_conn

    with db_conn() as cx1:
        pass

    # Forcibly close the connection behind the pool's back to simulate an unexpected stale handle
    cx1._force_close()
    assert getattr(_DB_CONN_LOCAL, "idle", None) is not None

    # Next db_conn call must probe liveness, discover it closed, and yield a fresh valid connection
    with db_conn() as cx2:
        assert cx2 is not cx1, "Base re-yielded closed connection from idle pool without liveness check (M4 defect)"
        res = cx2.execute("SELECT 1").fetchone()
        assert res[0] == 1


def test_cleanup_thread_connections_evicts_and_closes():
    """Verify cleanup_thread_connections cleans idle connection and physically closes handle."""
    from bulk_downloader.db import _DB_CONN_LOCAL, cleanup_thread_connections, db_conn

    captured_cx = None
    with db_conn() as cx:
        captured_cx = cx

    assert getattr(_DB_CONN_LOCAL, "idle", None) is not None
    count = cleanup_thread_connections()
    assert count >= 1
    assert getattr(_DB_CONN_LOCAL, "idle", None) is None

    with pytest.raises(sqlite3.ProgrammingError, match="Cannot operate on a closed database"):
        _ = captured_cx.total_changes


def test_close_all_pooled_connections_across_threads():
    """Verify close_all_pooled_connections evicts pooled handles from all threads."""
    from bulk_downloader.db import close_all_pooled_connections, db_conn, get_connection_lifecycle_manager

    mgr = get_connection_lifecycle_manager()
    barrier = threading.Barrier(4)
    captured = []

    def run_worker():
        with db_conn() as cx:
            captured.append(cx)
        barrier.wait()
        barrier.wait()

    threads = [threading.Thread(target=run_worker) for _ in range(3)]
    for t in threads:
        t.start()
    barrier.wait()

    assert len(captured) == 3
    assert mgr.get_metrics()["idle_connections"] == 3

    closed = close_all_pooled_connections()
    assert closed == 3
    assert mgr.get_metrics()["idle_connections"] == 0

    barrier.wait()
    for t in threads:
        t.join(timeout=2.0)


def test_db_connection_lifecycle_metrics_tracking():
    """Verify DBConnectionLifecycleManager tracks active leases and cumulative lease counts."""
    from bulk_downloader.db import DBConnectionLifecycleManager, db_conn, get_connection_lifecycle_manager

    mgr = get_connection_lifecycle_manager()
    assert isinstance(mgr, DBConnectionLifecycleManager)
    initial_leases = mgr.get_metrics()["total_leases_acquired"]

    with db_conn() as cx:
        assert cx is not None
        metrics = mgr.get_metrics()
        assert metrics["active_leases"] >= 1
        assert metrics["total_leases_acquired"] > initial_leases

    assert mgr.get_metrics()["active_leases"] == 0


def test_cleanup_rolls_back_uncommitted_work():
    """Verify cleanup rolls back pending transaction before closing handle (M2 guard)."""
    from bulk_downloader.db import _DB_CONN_LOCAL, _open_history_conn, cleanup_thread_connections

    cx = _open_history_conn()
    cx.execute("CREATE TABLE IF NOT EXISTS m2_test (val INT)")
    cx.commit()

    cx.execute("INSERT INTO m2_test VALUES (999)")
    _DB_CONN_LOCAL.idle = (None, cx)
    cleanup_thread_connections()

    cx2 = _open_history_conn()
    res = cx2.execute("SELECT count(*) FROM m2_test WHERE val = 999").fetchone()
    assert res[0] == 0
    cx2.execute("DROP TABLE m2_test")
    cx2.commit()
    cx2.close()
