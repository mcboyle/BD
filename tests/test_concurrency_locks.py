from __future__ import annotations

import pathlib
import queue
import sqlite3
import threading
import time

import pytest

from tests.conftest import find_free_port, wait_until


def _execute_with_retry(conn: sqlite3.Connection, sql: str, params: tuple[object, ...]=(), timeout: float=10.0) -> sqlite3.Cursor:
    deadline = time.monotonic() + timeout
    delay = 0.002
    while True:
        try:
            return conn.execute(sql, params)
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if ("locked" in msg or "busy" in msg) and time.monotonic() < deadline:
                time.sleep(delay)
                delay = min(delay * 1.5, 0.05)
            else:
                raise

def test_sqlite_wal_mode_concurrent_writes(sqlite_wal_db: tuple[sqlite3.Connection, pathlib.Path]) -> None:
    conn, db_path = sqlite_wal_db
    conn.execute("CREATE TABLE test_records (id INTEGER PRIMARY KEY, worker_id INT, val TEXT)")
    conn.commit()
    conn.close()
    num_threads = 8
    writes_per_thread = 10
    barrier = threading.Barrier(num_threads)
    errors: list[Exception] = []

    def worker_task(worker_id: int) -> None:
        try:
            thread_conn = sqlite3.connect(str(db_path), timeout=10.0, isolation_level=None)
            thread_conn.execute("PRAGMA synchronous=OFF;")
            barrier.wait(timeout=5.0)
            for i in range(writes_per_thread):
                _execute_with_retry(thread_conn, "INSERT INTO test_records (worker_id, val) VALUES (?, ?)", (worker_id, f"row-{worker_id}-{i}"))
            thread_conn.close()
        except (sqlite3.Error, OSError, threading.BrokenBarrierError) as exc:
            errors.append(exc)
    threads = [threading.Thread(target=worker_task, args=(tid,), daemon=True) for tid in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)
    assert not errors, f"Concurrent writes failed with errors: {errors}"
    with sqlite3.connect(str(db_path), timeout=10.0) as verify_conn:
        cursor = verify_conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM test_records")
        count = cursor.fetchone()[0]
        assert count == num_threads * writes_per_thread

def test_sqlite_wal_mode_concurrent_readers_and_writers(sqlite_wal_db: tuple[sqlite3.Connection, pathlib.Path]) -> None:
    conn, db_path = sqlite_wal_db
    conn.execute("CREATE TABLE counter (id INTEGER PRIMARY KEY, count INT)")
    conn.execute("INSERT INTO counter (id, count) VALUES (1, 0)")
    conn.commit()
    conn.close()
    stop_event = threading.Event()
    read_counts: list[int] = []
    errors: list[Exception] = []

    def reader_task() -> None:
        try:
            rconn = sqlite3.connect(str(db_path), timeout=10.0, isolation_level=None)
            rconn.execute("PRAGMA synchronous=OFF;")
            while not stop_event.is_set():
                cur = rconn.cursor()
                cur.execute("SELECT count FROM counter WHERE id = 1")
                row = cur.fetchone()
                if row:
                    read_counts.append(row[0])
                time.sleep(0.001)
            rconn.close()
        except (sqlite3.Error, OSError, threading.BrokenBarrierError) as exc:
            errors.append(exc)

    def writer_task() -> None:
        try:
            wconn = sqlite3.connect(str(db_path), timeout=10.0, isolation_level=None)
            wconn.execute("PRAGMA synchronous=OFF;")
            for i in range(1, 21):
                _execute_with_retry(wconn, "UPDATE counter SET count = ? WHERE id = 1", (i,))
                time.sleep(0.001)
            wconn.close()
        except (sqlite3.Error, OSError, threading.BrokenBarrierError) as exc:
            errors.append(exc)
    reader_thread = threading.Thread(target=reader_task, daemon=True)
    writer_thread = threading.Thread(target=writer_task, daemon=True)
    reader_thread.start()
    writer_thread.start()
    writer_thread.join(timeout=10.0)
    stop_event.set()
    reader_thread.join(timeout=10.0)
    assert not errors, f"Errors occurred during reader/writer concurrency: {errors}"
    assert len(read_counts) > 0, "Reader thread never executed any reads"
    with sqlite3.connect(str(db_path), timeout=10.0) as verify_conn:
        row = verify_conn.execute("SELECT count FROM counter WHERE id = 1").fetchone()
        assert row[0] == 20

def test_condition_polling_eliminates_sleep_races() -> None:
    event_queue: queue.Queue[str] = queue.Queue()

    def delayed_producer() -> None:
        timer = threading.Timer(0.05, lambda: event_queue.put("COMPLETED"))
        timer.daemon = True
        timer.start()
    delayed_producer()
    res = wait_until(lambda: event_queue.get_nowait() if not event_queue.empty() else None, timeout=2.0, error_message="Event was not produced in time")
    assert res == "COMPLETED"

def test_sqlite_tmp_path_isolation(tmp_path: pathlib.Path) -> None:
    db_file = tmp_path / "private_test.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("CREATE TABLE kv (k TEXT, v TEXT)")
    conn.execute("INSERT INTO kv VALUES ('alpha', 'one')")
    conn.commit()
    conn.close()
    assert db_file.exists()
    assert db_file.parent == tmp_path
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    assert not (repo_root / "private_test.db").exists()

def test_concurrency_lock_with_dynamic_port() -> None:
    port1 = find_free_port()
    port2 = find_free_port()
    assert port1 > 1024
    assert port2 > 1024
    assert port1 not in (5555, 8000)
    assert port2 not in (5555, 8000)

def test_negative_control_sqlite_busy_without_wal(tmp_path: pathlib.Path) -> None:
    db_file = tmp_path / "locked.db"
    conn1 = sqlite3.connect(str(db_file))
    conn1.execute("PRAGMA journal_mode=DELETE;")
    conn1.execute("CREATE TABLE t (x INT)")
    conn1.commit()
    conn1.execute("BEGIN EXCLUSIVE TRANSACTION")
    conn1.execute("INSERT INTO t VALUES (42)")
    conn2 = sqlite3.connect(str(db_file), timeout=0.0)
    with pytest.raises(sqlite3.OperationalError):
        conn2.execute("INSERT INTO t VALUES (99)")
    conn1.rollback()
    conn1.close()
    conn2.close()
