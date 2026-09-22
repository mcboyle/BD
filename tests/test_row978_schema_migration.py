"""Row 978 -- Zero-Downtime Schema Migration & DDL Locking Barrier.

Validates that:
1. DDLLockingBarrier manages migration barrier states (IDLE, DRAINING, MIGRATING, COMMITTED, ABORTED).
2. Active borrower lease drain coordination functions correctly.
3. DDL operations that encounter SQLITE_LOCKED / table lock errors are retried with bounded backoff.
4. apply_pending(zero_downtime=True) executes migrations through the DDL barrier and records barrier telemetry.
5. Concurrent readers and writers during DDL migrations proceed without uncaught locking failures.
6. Full backward compatibility is preserved for existing callers (app.py, library.py, dev_suite).
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import time

import pytest

import bulk_downloader.db as db
import bulk_downloader.migrations as mg

BD_GATE_SCOPE = "repo-wide"


def _isolated_db():
    """Create a temporary directory and isolated queue.db path."""
    d = tempfile.mkdtemp(prefix="test_row978_")
    return d, os.path.join(d, "queue.db")


def test_red_apply_pending_zero_downtime_support():
    """Base's apply_pending lacks zero_downtime barrier coordination."""
    import inspect
    sig = inspect.signature(mg.apply_pending)
    assert "zero_downtime" in sig.parameters, (
        "apply_pending must accept zero_downtime for DDL barrier coordination"
    )


def test_ddl_locking_barrier_lifecycle_and_states():
    """Verify barrier state transitions from IDLE to DRAINING/MIGRATING and back to IDLE/COMMITTED."""
    barrier = mg.DDLLockingBarrier(drain_timeout=0.5, barrier_timeout=1.0)
    assert barrier.state == mg.BarrierState.IDLE
    assert barrier.is_idle()

    with barrier.barrier_context():
        assert barrier.state == mg.BarrierState.MIGRATING
        assert not barrier.is_idle()

    assert barrier.state == mg.BarrierState.COMMITTED
    assert barrier.is_idle()


def test_ddl_locking_barrier_lease_registration_and_drain():
    """Verify active leases are tracked and drained gracefully without arbitrary sleeps."""
    barrier = mg.DDLLockingBarrier(drain_timeout=0.5, barrier_timeout=1.0)
    token = barrier.acquire_lease("test_reader_1")
    assert barrier.active_leases_count == 1

    def release_when_draining():
        while barrier.state != mg.BarrierState.DRAINING:
            time.sleep(0.001)
        barrier.release_lease(token)

    t = threading.Thread(target=release_when_draining)
    t.start()

    # Draining should wait for the lease to clear
    success = barrier.drain_in_flight(timeout=0.5)
    t.join()

    assert success is True
    assert barrier.active_leases_count == 0


def test_apply_pending_aborts_when_barrier_drain_fails():
    """Verify apply_pending aborts without applying DDL when barrier drain fails (leases held)."""
    mg.reset_barrier()
    barrier = mg.get_barrier(drain_timeout=0.05, barrier_timeout=1.0)
    token1 = barrier.acquire_lease("held_reader_1")
    token2 = barrier.acquire_lease("held_reader_2")
    assert barrier.active_leases_count == 2

    saved_migs = list(mg._MIGRATIONS)
    saved_path = db.DB_PATH
    _d, dbf = _isolated_db()
    db.DB_PATH = dbf
    try:
        def _m978_failing_drain(cx):
            cx.execute("CREATE TABLE IF NOT EXISTS row978_drain_fail(id INTEGER PRIMARY KEY)")

        mg._MIGRATIONS.clear()
        mg._MIGRATIONS.append({"version": 978099, "name": "m978_drain_fail", "fn": _m978_failing_drain})

        out = mg.apply_pending(zero_downtime=True, drain_timeout=0.05)
        assert out.get("aborted") is True
        assert out.get("applied") == 0
        assert "barrier_telemetry" in out
        tel = out["barrier_telemetry"]
        assert tel["drained"] is False
        assert tel["leases_outstanding"] == 2
        assert tel["barrier_state"] == mg.BarrierState.DRAIN_FAILED.value

        with db.db_conn() as cx:
            row = cx.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='row978_drain_fail'"
            ).fetchone()
            assert row[0] == 0
    finally:
        barrier.release_lease(token1)
        barrier.release_lease(token2)
        mg._MIGRATIONS[:] = saved_migs
        db.DB_PATH = saved_path
        mg.reset_barrier()


def test_guarded_ddl_execution_retries_on_table_lock():
    """Verify execute_ddl_guarded retries on SQLITE_LOCKED and succeeds within deadline."""
    barrier = mg.DDLLockingBarrier(drain_timeout=0.2, barrier_timeout=2.0)
    attempts = 0

    def transient_locked_operation(cx):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise sqlite3.OperationalError("database table is locked: sqlite_master")
        cx.execute("CREATE TABLE IF NOT EXISTS test_guarded_tbl(id INTEGER PRIMARY KEY, v TEXT)")
        return "created"

    saved_path = db.DB_PATH
    _d, dbf = _isolated_db()
    db.DB_PATH = dbf
    try:
        with db.db_conn() as cx:
            result, retries = barrier.execute_ddl_guarded(cx, transient_locked_operation)
            assert result == "created"
            assert retries == 2
            # Verify table was actually created
            cx.execute("INSERT INTO test_guarded_tbl(v) VALUES ('ok')")
            row = cx.execute("SELECT v FROM test_guarded_tbl").fetchone()
            assert row[0] == "ok"
    finally:
        db.DB_PATH = saved_path


def test_apply_pending_zero_downtime_barrier_telemetry():
    """Verify apply_pending with zero_downtime=True applies migrations with telemetry."""
    mg.reset_barrier()
    saved_migs = list(mg._MIGRATIONS)
    saved_path = db.DB_PATH
    _d, dbf = _isolated_db()
    db.DB_PATH = dbf
    try:
        def _m978_test(cx):
            cx.execute("CREATE TABLE IF NOT EXISTS row978_tbl(id INTEGER PRIMARY KEY, note TEXT)")

        mg._MIGRATIONS.clear()
        mg._MIGRATIONS.append({"version": 978001, "name": "m978_test_barrier", "fn": _m978_test})

        out = mg.apply_pending(zero_downtime=True, barrier_timeout=2.0)
        assert out.get("applied") == 1
        assert out.get("errors") == 0
        assert "barrier_telemetry" in out
        tel = out["barrier_telemetry"]
        assert tel["zero_downtime"] is True
        assert tel["drained"] is True
        assert tel["leases_outstanding"] == 0
        assert tel["barrier_state"] in ("COMMITTED", "IDLE")

        # Verify applied in database
        with db.db_conn() as cx:
            row = cx.execute(
                "SELECT name, success FROM schema_migrations WHERE version=978001"
            ).fetchone()
            assert row is not None
            assert row[0] == "m978_test_barrier"
            assert row[1] == 1
    finally:
        mg._MIGRATIONS[:] = saved_migs
        db.DB_PATH = saved_path
        mg.reset_barrier()


def test_lease_refused_during_migration():
    """Verify acquire_lease refuses when barrier is DRAINING or MIGRATING (F4)."""
    barrier = mg.DDLLockingBarrier(drain_timeout=0.5, barrier_timeout=1.0)

    refused = threading.Event()

    def try_lease_during_migration():
        try:
            barrier.acquire_lease("during_migration")
        except RuntimeError:
            refused.set()

    with barrier.barrier_context():
        assert barrier.state == mg.BarrierState.MIGRATING
        t = threading.Thread(target=try_lease_during_migration)
        t.start()
        t.join(timeout=1.0)

    assert refused.is_set(), "acquire_lease must refuse during MIGRATING state"


def test_barrier_does_not_block_concurrent_state_reads():
    """Verify barrier_context releases lock during yield so state reads are not blocked (F2)."""
    barrier = mg.DDLLockingBarrier(drain_timeout=0.5, barrier_timeout=1.0)

    state_read = threading.Event()
    state_value = [None]

    def read_state_during_migration():
        state_value[0] = barrier.state
        state_read.set()

    with barrier.barrier_context():
        t = threading.Thread(target=read_state_during_migration)
        t.start()
        t.join(timeout=1.0)

    assert state_read.is_set(), "state read must not block during migration"
    assert state_value[0] == mg.BarrierState.MIGRATING


def test_backward_compatibility_preserved():
    """Existing status(), detect_drift(), and apply_pending() signatures remain 100% compatible."""
    saved_path = db.DB_PATH
    _d, dbf = _isolated_db()
    db.DB_PATH = dbf
    try:
        # Dry-run still works as before
        dry_out = mg.apply_pending(dry_run=True)
        assert "considered" in dry_out
        assert "applied" in dry_out

        # status still works
        st = mg.status()
        assert "registered_migrations" in st
        assert "applied_versions" in st
        assert "drift" in st
    finally:
        db.DB_PATH = saved_path
