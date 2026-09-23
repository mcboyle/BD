"""Row 1010: Segregated Reader/Writer Connection Pools with Non-Blocking Busy Backoff.

Validates:
1. Reader/writer pool segregation with dedicated reader handles and serialized writer handle.
2. Query-only enforcement on reader connections.
3. Busy backoff that holds no pool lock while it waits (other leases proceed); cancellable.
4. Retry execution on SQLite lock contention.
5. Pool lifecycle, metrics introspection, and database integration.

RED on baseline: fails with AssertionError (db module lacks segregated connection pool).
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
from typing import Any

BD_GATE_SCOPE = "repo-wide"


def test_red_baseline_capability_probe():
    """Verify that baseline lacks segregated connection pools and backoff interface.

    RED on baseline (bc1544b751e2): fails with AssertionError: db module lacks db_read_conn.
    """
    from bulk_downloader import db

    assert hasattr(db, "db_read_conn"), "db module lacks db_read_conn"
    assert hasattr(db, "db_write_conn"), "db module lacks db_write_conn"
    assert hasattr(db, "get_segregated_pool"), "db module lacks get_segregated_pool"


def test_segregated_pool_reader_concurrency():
    """Verify multiple readers can acquire distinct handles and read concurrently."""
    from bulk_downloader.connection_pool import SegregatedConnectionPool

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_readers.db")

        # Seed database
        init_conn = sqlite3.connect(db_path)
        init_conn.execute("PRAGMA journal_mode=WAL;")
        init_conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT);")
        init_conn.execute("INSERT INTO items (name) VALUES ('item-1'), ('item-2');")
        init_conn.commit()
        init_conn.close()

        pool = SegregatedConnectionPool(db_path, max_readers=4, max_writers=1)
        try:
            with pool.acquire_reader() as r1:
                with pool.acquire_reader() as r2:
                    assert r1 is not r2
                    rows1 = r1.execute("SELECT COUNT(*) FROM items").fetchone()[0]
                    rows2 = r2.execute("SELECT COUNT(*) FROM items").fetchone()[0]
                    assert rows1 == 2
                    assert rows2 == 2

                    # Readers must be query-only / read-only
                    try:
                        r1.execute("INSERT INTO items (name) VALUES ('item-3')")
                        assert False, "Reader connection must not allow writes"
                    except sqlite3.OperationalError:
                        # Expected: attempt to write a readonly database or query_only
                        pass
        finally:
            pool.close()


def test_segregated_pool_writer_serialization():
    """Verify writer pool enforces serialized access and commits successfully."""
    from bulk_downloader.connection_pool import SegregatedConnectionPool

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_writer.db")

        init_conn = sqlite3.connect(db_path)
        init_conn.execute("PRAGMA journal_mode=WAL;")
        init_conn.execute("CREATE TABLE counter (val INTEGER);")
        init_conn.execute("INSERT INTO counter (val) VALUES (0);")
        init_conn.commit()
        init_conn.close()

        pool = SegregatedConnectionPool(db_path, max_readers=2, max_writers=1)
        try:
            with pool.acquire_writer() as w:
                w.execute("UPDATE counter SET val = val + 1;")
                w.commit()

            with pool.acquire_reader() as r:
                val = r.execute("SELECT val FROM counter;").fetchone()[0]
                assert val == 1
        finally:
            pool.close()


def test_non_blocking_busy_backoff_calculations():
    """Verify NonBlockingBusyBackoff computes exponential intervals with jitter and caps."""
    from bulk_downloader.connection_pool import NonBlockingBusyBackoff

    backoff = NonBlockingBusyBackoff(base_ms=10.0, max_ms=200.0, factor=2.0, jitter=0.1)

    delays = [backoff.compute_delay_ms(attempt=i) for i in range(10)]
    assert len(delays) == 10
    # First delay should be approximately 10ms +/- 10%
    assert 9.0 <= delays[0] <= 11.0
    # Max delay should not exceed max_ms * (1 + jitter)
    assert all(d <= 220.0 for d in delays)
    # Increasing trend over first few attempts
    assert delays[2] > delays[0]


def test_execute_with_busy_backoff_retries_and_succeeds():
    """Verify execute_with_busy_backoff retries on simulated transient locked errors and backs off."""
    import time
    from bulk_downloader.connection_pool import execute_with_busy_backoff

    attempts = 0

    def transient_flaky_operation():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise sqlite3.OperationalError("database is locked")
        return "success"

    t0 = time.monotonic()
    result = execute_with_busy_backoff(
        transient_flaky_operation,
        max_attempts=5,
        base_backoff_ms=15.0,
        max_backoff_ms=100.0,
    )
    t1 = time.monotonic()
    assert result == "success"
    assert attempts == 3
    # Verifies real backoff occurred (2 retries with base 15ms wait > 10ms)
    assert (t1 - t0) >= 0.010


def test_pool_metrics_and_telemetry():
    """Verify pool tracks active handles, read/write counts, and backoff events."""
    from bulk_downloader.connection_pool import SegregatedConnectionPool

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_metrics.db")

        pool = SegregatedConnectionPool(db_path, max_readers=3, max_writers=1)
        try:
            stats_initial = pool.get_stats()
            assert stats_initial["active_readers"] == 0
            assert stats_initial["total_reads"] == 0
            assert stats_initial["total_writes"] == 0

            with pool.acquire_reader() as r:
                assert pool.get_stats()["active_readers"] == 1
                r.execute("SELECT 1;").fetchone()

            with pool.acquire_writer() as w:
                assert pool.get_stats()["active_writers"] == 1
                w.execute("CREATE TABLE dummy (x INT);")
                w.commit()

            stats_final = pool.get_stats()
            assert stats_final["active_readers"] == 0
            assert stats_final["active_writers"] == 0
            assert stats_final["total_reads"] == 1
            assert stats_final["total_writes"] == 1
        finally:
            pool.close()


def test_max_writers_enforced():
    """Verify max_writers strictly limits concurrent writers."""
    from bulk_downloader.connection_pool import SegregatedConnectionPool

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_max_writers.db")

        pool = SegregatedConnectionPool(db_path, max_readers=2, max_writers=1, busy_timeout_s=0.1)
        try:
            with pool.acquire_writer() as w1:
                assert pool.get_stats()["active_writers"] == 1
                # Second concurrent or nested writer acquisition times out because max_writers=1
                try:
                    with pool.acquire_writer():
                        assert False, "Should not acquire second writer when max_writers=1"
                except sqlite3.OperationalError as exc:
                    assert "timed out" in str(exc).lower()
        finally:
            pool.close()


def test_db_integration_read_and_write_conn():
    """Verify db_read_conn and db_write_conn integration in bulk_downloader.db."""
    from bulk_downloader import db

    with tempfile.TemporaryDirectory() as tmpdir:
        test_path = os.path.join(tmpdir, "app_history.db")

        # Write through db_write_conn
        with db.db_write_conn(path=test_path) as w_conn:
            w_conn.execute("CREATE TABLE records (key TEXT PRIMARY KEY, value TEXT);")
            w_conn.execute("INSERT INTO records VALUES ('k1', 'v1');")
            w_conn.commit()

        # Read through db_read_conn
        with db.db_read_conn(path=test_path) as r_conn:
            row = r_conn.execute("SELECT value FROM records WHERE key = 'k1';").fetchone()
            assert row[0] == "v1"

            # F1 verification: db_read_conn MUST reject write attempts!
            try:
                r_conn.execute("INSERT INTO records VALUES ('k2', 'v2');")
                assert False, "db_read_conn must not permit writes"
            except sqlite3.OperationalError:
                pass

        # Pool stats accessible
        pool = db.get_segregated_pool(path=test_path)
        stats = pool.get_stats()
        assert stats["total_reads"] >= 1
        assert stats["total_writes"] >= 1
        pool.close()


def _run_in_thread(fn):
    box: dict[str, Any] = {}

    def _target():
        try:
            box["value"] = fn()
        except BaseException as exc:  # recorded, re-raised by the caller's assert
            box["error"] = exc

    t = threading.Thread(target=_target)
    t.start()
    t.join(timeout=30)
    assert not t.is_alive(), "row1010: pooled lease thread hung"
    return box


def test_pooled_leases_are_usable_from_every_thread():
    """N6-A E2: the one pooled writer (and a recycled reader) must serve a second thread.

    BD writes from Flask request threads and runner threads; a pooled connection bound to
    the thread that opened it raises ProgrammingError everywhere else.
    """
    from bulk_downloader import db

    with tempfile.TemporaryDirectory() as tmpdir:
        test_path = os.path.join(tmpdir, "threads.db")

        def _write(n):
            def _op():
                with db.db_write_conn(path=test_path) as w_conn:
                    w_conn.execute("CREATE TABLE IF NOT EXISTS t (n INTEGER)")
                    w_conn.execute("INSERT INTO t (n) VALUES (?)", (n,))
                    w_conn.commit()
            return _op

        def _read():
            with db.db_read_conn(path=test_path) as r_conn:
                return r_conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]

        try:
            # The first lease opens the pooled connection on THIS (still-live) thread, so the
            # second thread has a distinct ident -- sequential short-lived threads can reuse one.
            _write(1)()
            box = _run_in_thread(_write(2))
            assert "error" not in box, f"row1010: pooled writer failed in a second thread: {box.get('error')!r}"
            assert _read() == 2
            box = _run_in_thread(_read)
            assert "error" not in box, f"row1010: recycled reader failed in a second thread: {box.get('error')!r}"
            assert box["value"] == 2
        finally:
            db.get_segregated_pool(path=test_path).close()


def test_product_paths_lease_from_the_segregated_pool(tmp_path, monkeypatch):
    """N6-A E1: a real reader route leases from the pool, not db_conn(). Writes stay on
    db_conn(), the MOD-3 interception seam (row421 stubs it); the pooled writer is pinned below."""
    from flask import Flask

    from bulk_downloader import app_stats, db

    db_path = str(tmp_path / "history.db")
    seed = sqlite3.connect(db_path)
    seed.execute("PRAGMA journal_mode=WAL")
    seed.execute("CREATE TABLE history (ts TEXT, status TEXT, site_id TEXT, file_size INTEGER)")
    seed.execute("INSERT INTO history VALUES (datetime('now'), 'done', 's1', 10)")
    seed.commit()
    seed.close()
    monkeypatch.setattr(db, "_resolve_db_path", lambda: db_path)
    pool = db.get_segregated_pool()
    try:
        app = Flask(__name__)
        app.register_blueprint(app_stats.stats_bp)
        resp = app.test_client().get("/api/stats/timeline?days=7")
        assert resp.status_code == 200
        assert resp.get_json()["buckets"][0]["done"] == 1
        assert pool.get_stats()["total_reads"] == 1, "row1010: /api/stats/timeline did not lease a pooled reader"

        # The writer keeps db_conn's transaction boundary: committed on release,
        # rolled back when the block raises.
        with db.db_write_conn() as w:
            w.execute("INSERT INTO history VALUES (datetime('now'), 'failed', 's1', 0)")
        try:
            with db.db_write_conn() as w:
                w.execute("INSERT INTO history VALUES (datetime('now'), 'failed', 's2', 0)")
                raise RuntimeError("abort")
        except RuntimeError:
            pass
        check = sqlite3.connect(db_path)
        assert check.execute("SELECT site_id FROM history WHERE status='failed'").fetchall() == [("s1",)]
        check.close()
    finally:
        db.get_segregated_pool().close()


def test_pool_retires_when_the_database_file_is_replaced(tmp_path):
    """A restore swaps the file; pooled handles must not keep serving the old inode."""
    from bulk_downloader import db

    db_path = str(tmp_path / "swap.db")
    for name, marker in ((db_path, "old"), (db_path + ".new", "new")):
        cx = sqlite3.connect(name)
        cx.execute("CREATE TABLE m (v TEXT)")
        cx.execute("INSERT INTO m VALUES (?)", (marker,))
        cx.commit()
        cx.close()
    try:
        with db.db_read_conn(path=db_path) as r:
            assert r.execute("SELECT v FROM m").fetchone()[0] == "old"
        os.replace(db_path + ".new", db_path)
        with db.db_read_conn(path=db_path) as r:
            got = r.execute("SELECT v FROM m").fetchone()[0]
        assert got == "new", f"row1010: pooled reader still served the replaced file ({got!r})"
    finally:
        db.get_segregated_pool(path=db_path).close()


def test_busy_backoff_blocks_no_other_lease_and_is_cancellable(tmp_path):
    """N6-A E3 (rescoped): the retrying thread waits, but it holds no pool lock while it
    does, so a reader lease completes during the wait; a set cancel_event ends each wait early."""
    import time

    from bulk_downloader.connection_pool import SegregatedConnectionPool, execute_with_busy_backoff

    db_path = str(tmp_path / "backoff.db")
    pool = SegregatedConnectionPool(db_path)
    in_backoff = threading.Event()

    def _busy():
        raise sqlite3.OperationalError("database is locked")

    def _retry():
        try:
            execute_with_busy_backoff(
                _busy, max_attempts=3, base_backoff_ms=400.0, max_backoff_ms=400.0,
                backoff_hook=lambda *_: in_backoff.set())
        except sqlite3.OperationalError:
            pass

    try:
        with pool.acquire_writer():
            t = threading.Thread(target=_retry)
            t.start()
            assert in_backoff.wait(5)
            started = time.monotonic()
            with pool.acquire_reader() as r:
                assert r.execute("SELECT 1").fetchone()[0] == 1
            waited = time.monotonic() - started
            assert t.is_alive(), "row1010: retry loop ended before the reader probe"
            assert waited < 0.2, f"row1010: reader lease waited {waited:.3f}s behind a busy backoff"
            t.join(10)
    finally:
        pool.close()

    cancel = threading.Event()
    cancel.set()
    started = time.monotonic()
    try:
        execute_with_busy_backoff(_busy, max_attempts=3, base_backoff_ms=5000.0,
                                  max_backoff_ms=5000.0, cancel_event=cancel)
    except sqlite3.OperationalError:
        pass
    assert time.monotonic() - started < 1.0, "row1010: cancel_event did not end the backoff wait"


def test_t70_close_failures_are_counted_not_swallowed(tmp_path):
    """T70 ratchet (defect_DP_total +9, all DP-13 in connection_pool.py): a handle whose close()
    raises must not abort closing the rest, and the failure must show in get_stats()."""
    from bulk_downloader.connection_pool import SegregatedConnectionPool

    closed = []

    class _CloseRaises(sqlite3.Connection):
        def close(self):
            super().close()
            closed.append(self)
            raise sqlite3.ProgrammingError("t70 close failure")

    db_path = str(tmp_path / "close.db")

    def _connect():
        return sqlite3.connect(db_path, factory=_CloseRaises, check_same_thread=False)

    pool = SegregatedConnectionPool(db_path, connector=_connect, reader_connector=_connect)
    with pool.acquire_reader() as r:
        r.execute("SELECT 1").fetchone()
    with pool.acquire_writer() as w:
        w.execute("CREATE TABLE IF NOT EXISTS t (x)")
    pool.close()
    stats = pool.get_stats()
    assert len(closed) == 2, f"T70: pool.close() stopped after a failing close(): closed {len(closed)} of 2"
    assert stats.get("close_failures") == 2, (
        f"T70: close failures were swallowed, get_stats() reports {stats.get('close_failures')!r}")
