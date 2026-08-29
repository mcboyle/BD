"""Row 378: logical database leases must not be physical reconnects.

The assertions here are counts, not timings.  Each workload proves its own
logical-lease and statement denominator before judging physical connection or
configuration-stat work, so an empty fixture cannot manufacture a fast pass.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest


BD_GATE_SCOPE = "module"


def _isolated_db_and_config(tmp_path, monkeypatch, *, tracing: bool,
                            threshold_ms: int = -1):
    """Point both mutable stores at this test and count config-file stats."""
    from bulk_downloader import db
    from bulk_downloader import global_config

    db_path = tmp_path / "history.db"
    config_path = tmp_path / "app_config.json"
    values = {
        "slow_query_log": tracing,
        "slow_query_ms": threshold_ms,
    }
    config_path.write_text(json.dumps(values), encoding="utf-8")

    real_stat = Path.stat
    cached_mtime = real_stat(config_path).st_mtime
    monkeypatch.setattr(db, "DB_PATH", str(db_path), raising=False)
    monkeypatch.delenv("MOD3_PG_DSN", raising=False)
    monkeypatch.setattr(global_config, "_CONFIG_FILE", config_path)
    monkeypatch.setattr(global_config, "_cached", dict(values))
    monkeypatch.setattr(global_config, "_cached_mtime", cached_mtime)

    config_stats = []

    def counted_stat(path, *args, **kwargs):
        if path == config_path:
            config_stats.append(path)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", counted_stat)
    return db, config_stats


def _count_physical_opens(db, monkeypatch):
    real_connect = db.sqlite3.connect
    calls = []
    calls_lock = threading.Lock()

    def counted_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        with calls_lock:
            calls.append((threading.get_ident(), str(args[0]), id(connection)))
        return connection

    monkeypatch.setattr(db.sqlite3, "connect", counted_connect)
    return calls


def test_many_logical_leases_use_one_physical_connection_and_one_config_stat(
        tmp_path, monkeypatch):
    """Catch reconnecting and disabled-tracer stat work at their real seams."""
    db, config_stats = _isolated_db_and_config(
        tmp_path, monkeypatch, tracing=False)
    physical_opens = _count_physical_opens(db, monkeypatch)

    workload_leases = 12
    completed_leases = 0
    completed_statements = 0
    for value in range(workload_leases):
        with db.db_conn() as cx:
            cx.execute(
                "CREATE TABLE IF NOT EXISTS lease_probe("
                "value INTEGER PRIMARY KEY)")
            completed_statements += 1
            cx.execute("INSERT INTO lease_probe(value) VALUES(?)", (value,))
            completed_statements += 1
        completed_leases += 1

    with db.db_conn() as cx:
        stored = cx.execute("SELECT value FROM lease_probe ORDER BY value").fetchall()
        journal_mode = cx.execute("PRAGMA journal_mode").fetchone()[0]
        synchronous = cx.execute("PRAGMA synchronous").fetchone()[0]
        busy_timeout = cx.execute("PRAGMA busy_timeout").fetchone()[0]

    assert workload_leases == 12, "precondition: configured workload changed"
    assert completed_leases == 12, "precondition: workload lost logical leases"
    assert completed_statements == 24, "precondition: workload lost statements"
    assert [row[0] for row in stored] == list(range(12)), (
        "every logical lease must still commit its row")
    assert str(journal_mode).lower() == "wal"
    assert int(synchronous) == 1, "SQLite NORMAL synchronous mode is numeric 1"
    assert int(busy_timeout) == 10_000
    assert (len(physical_opens), len(config_stats)) == (1, 1), (
        "12 write leases plus one verification lease must reuse one physical "
        "connection, and disabled tracing must stat its config only once; "
        f"observed opens={len(physical_opens)}, stats={len(config_stats)}")


def test_concurrent_threads_reuse_only_their_own_connections_and_keep_every_row(
        tmp_path, monkeypatch):
    """A shared cross-thread handle must fail both the count and row controls."""
    db, _config_stats = _isolated_db_and_config(
        tmp_path, monkeypatch, tracing=False)
    physical_opens = _count_physical_opens(db, monkeypatch)
    db.db_init()

    worker_count = 5
    leases_per_worker = 8
    expected_writes = worker_count * leases_per_worker
    start = threading.Barrier(worker_count)
    completed = set()
    lease_connection_ids = {worker_id: [] for worker_id in range(worker_count)}
    worker_thread_ids = {}
    worker_pragmas = {}
    errors = []
    result_lock = threading.Lock()

    def writer(worker_id):
        try:
            worker_thread_ids[worker_id] = threading.get_ident()
            start.wait()
            for sequence in range(leases_per_worker):
                token = f"row378-{worker_id}-{sequence}"
                with db.db_conn() as cx:
                    if sequence == 0:
                        worker_pragmas[worker_id] = (
                            str(cx.execute("PRAGMA journal_mode").fetchone()[0]).lower(),
                            int(cx.execute("PRAGMA synchronous").fetchone()[0]),
                            int(cx.execute("PRAGMA busy_timeout").fetchone()[0]),
                        )
                    cursor = cx.execute(
                        "INSERT INTO history(site_id, url, status) "
                        "VALUES (?, ?, 'done')",
                        (f"row378-{worker_id}", token),
                    )
                with result_lock:
                    completed.add(token)
                    lease_connection_ids[worker_id].append(id(cursor.connection))
        except BaseException as exc:  # retain the worker identity in the verdict
            with result_lock:
                errors.append((worker_id, type(exc).__name__, str(exc)))

    threads = [
        threading.Thread(target=writer, args=(worker_id,))
        for worker_id in range(worker_count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert worker_count == 5 and leases_per_worker == 8, (
        "precondition: concurrent workload dimensions changed")
    assert expected_writes == 40, "precondition: write denominator is empty"
    assert not errors, f"concurrent database workers failed: {errors}"
    assert len(completed) == expected_writes, (
        f"only {len(completed)} of {expected_writes} logical writes completed")

    with db.db_conn() as cx:
        total, distinct_urls = cx.execute(
            "SELECT COUNT(*), COUNT(DISTINCT url) FROM history "
            "WHERE site_id LIKE 'row378-%'").fetchone()
        main_pragmas = (
            str(cx.execute("PRAGMA journal_mode").fetchone()[0]).lower(),
            int(cx.execute("PRAGMA synchronous").fetchone()[0]),
            int(cx.execute("PRAGMA busy_timeout").fetchone()[0]),
        )

    assert (total, distinct_urls) == (expected_writes, expected_writes), (
        "concurrent reuse lost or duplicated committed history rows")
    assert len(physical_opens) == worker_count + 1, (
        "the main thread and five workers must each own one physical SQLite "
        f"connection; observed {len(physical_opens)} opens")
    assert len({thread_id for thread_id, _path, _cx in physical_opens}) == \
        worker_count + 1, "a physical SQLite connection crossed thread ownership"
    assert all(len(ids) == leases_per_worker for ids in lease_connection_ids.values()), (
        f"not every logical lease recorded its physical owner: {lease_connection_ids}")
    worker_connection_ids = {
        worker_id: set(ids) for worker_id, ids in lease_connection_ids.items()
    }
    assert all(len(ids) == 1 for ids in worker_connection_ids.values()), (
        f"a worker did not reuse exactly one physical handle: {worker_connection_ids}")
    assert len(set().union(*worker_connection_ids.values())) == worker_count, (
        f"workers shared a physical SQLite handle: {worker_connection_ids}")
    opens_by_thread = {
        thread_id: connection_id
        for thread_id, _path, connection_id in physical_opens
    }
    assert all(
        worker_connection_ids[worker_id] == {opens_by_thread[thread_id]}
        for worker_id, thread_id in worker_thread_ids.items()
    ), "a worker borrowed a connection physically opened by another thread"
    expected_pragmas = ("wal", 1, 10_000)
    assert main_pragmas == expected_pragmas
    assert worker_pragmas == {
        worker_id: expected_pragmas for worker_id in range(worker_count)
    }, "every worker connection must retain WAL/NORMAL/10s durability settings"


def test_enabled_tracer_caches_threshold_and_still_traces(
        tmp_path, monkeypatch):
    """The optimization must remove per-statement stats, not the tracer."""
    db, config_stats = _isolated_db_and_config(
        tmp_path, monkeypatch, tracing=True, threshold_ms=-1)
    from bulk_downloader import log as bd_log

    warnings = []

    class RecordingLogger:
        def warning(self, message, *args):
            warnings.append((message, args))

    monkeypatch.setattr(bd_log, "get_logger", lambda _name: RecordingLogger())

    statement_count = 6
    with db.db_conn() as cx:
        results = [cx.execute("SELECT ?", (value,)).fetchone()[0]
                   for value in range(statement_count)]

    assert statement_count == 6, "precondition: traced statement workload changed"
    assert results == list(range(6)), "precondition: traced statements did not run"
    assert len(warnings) == statement_count - 1, (
        "enabled tracing must report every post-first statement at threshold -1; "
        f"observed {len(warnings)} warnings")
    assert len(config_stats) == 2, (
        "enabled and threshold must each be read once when the physical "
        f"connection is configured, not per statement; observed {len(config_stats)} stats")


def test_reused_connection_refreshes_tracing_after_a_config_write(
        tmp_path, monkeypatch):
    """A Settings write applies on the next lease without reopening or polling."""
    db, config_stats = _isolated_db_and_config(
        tmp_path, monkeypatch, tracing=True, threshold_ms=-1)
    from bulk_downloader import global_config
    from bulk_downloader import log as bd_log

    warnings = []

    class RecordingLogger:
        def warning(self, message, *args):
            warnings.append((message, args))

    monkeypatch.setattr(bd_log, "get_logger", lambda _name: RecordingLogger())
    physical_opens = _count_physical_opens(db, monkeypatch)

    with db.db_conn() as cx:
        cx.execute("SELECT 1").fetchone()
        cx.execute("SELECT 2").fetchone()
    assert len(warnings) == 1, "precondition: initial enabled tracer did not run"
    assert len(config_stats) == 2, (
        "precondition: initial enabled/threshold lookup count changed")

    assert global_config.set_config({"slow_query_log": False}) is True
    stats_after_write = len(config_stats)
    with db.db_conn() as cx:
        cx.execute("SELECT 3").fetchone()
        cx.execute("SELECT 4").fetchone()

    assert len(physical_opens) == 1, (
        "a tracing config change must reconfigure the existing physical handle")
    assert len(config_stats) - stats_after_write == 1, (
        "the next lease must consume one invalidated enabled lookup, not poll "
        "the config file per statement")
    assert len(warnings) == 1, (
        "disabling slow-query tracing did not take effect on the next DB lease")


def test_reused_connection_rolls_back_exceptions_and_keeps_nested_boundaries(
        tmp_path, monkeypatch):
    """Returning a lease must not retain or prematurely commit a transaction."""
    db, _config_stats = _isolated_db_and_config(
        tmp_path, monkeypatch, tracing=False)

    with db.db_conn() as cx:
        cx.execute("CREATE TABLE tx_probe(value INTEGER PRIMARY KEY)")
        cx.execute("INSERT INTO tx_probe(value) VALUES(1)")

    with pytest.raises(RuntimeError, match="outer failure"):
        with db.db_conn() as outer:
            outer.execute("INSERT INTO tx_probe(value) VALUES(2)")
            with db.db_conn() as inner:
                inner_visible = inner.execute(
                    "SELECT value FROM tx_probe ORDER BY value").fetchall()
            assert [row[0] for row in inner_visible] == [1], (
                "a nested logical lease reused and observed the outer transaction")
            raise RuntimeError("outer failure")

    with db.db_conn() as cx:
        after = cx.execute("SELECT value FROM tx_probe ORDER BY value").fetchall()
    assert [row[0] for row in after] == [1], (
        "the failed outer lease was committed or left live for the next borrower")


def test_logical_lease_exit_finalizes_unconsumed_cursors(tmp_path, monkeypatch):
    """Connection reuse must not leave a read snapshot pinning the WAL."""
    db, _config_stats = _isolated_db_and_config(
        tmp_path, monkeypatch, tracing=False)
    with db.db_conn() as cx:
        cx.execute("CREATE TABLE cursor_probe(value INTEGER PRIMARY KEY)")
        cx.executemany(
            "INSERT INTO cursor_probe(value) VALUES(?)",
            [(value,) for value in range(4)],
        )

    with db.db_conn() as cx:
        cursor = cx.execute("SELECT value FROM cursor_probe ORDER BY value")
        assert cursor.fetchone()[0] == 0, (
            "precondition: cursor must retain three unconsumed rows")

    with pytest.raises(sqlite3.ProgrammingError):
        cursor.fetchone()


def test_connection_reference_cannot_write_after_its_logical_lease(
        tmp_path, monkeypatch):
    """A stale borrower must not inject work into a later transaction."""
    db, _config_stats = _isolated_db_and_config(
        tmp_path, monkeypatch, tracing=False)
    with db.db_conn() as escaped:
        escaped.execute("CREATE TABLE escape_probe(value INTEGER PRIMARY KEY)")
        escaped.execute("INSERT INTO escape_probe(value) VALUES(1)")

    with pytest.raises(sqlite3.ProgrammingError):
        escaped.execute("INSERT INTO escape_probe(value) VALUES(2)")

    with db.db_conn() as cx:
        rows = cx.execute("SELECT value FROM escape_probe ORDER BY value").fetchall()
    assert [row[0] for row in rows] == [1], (
        "an escaped connection reference contaminated a later logical lease")


def test_closing_an_expired_reference_does_not_poison_the_idle_connection(
        tmp_path, monkeypatch):
    """close() remains harmless after exit instead of closing the pooled handle."""
    db, _config_stats = _isolated_db_and_config(
        tmp_path, monkeypatch, tracing=False)
    with db.db_conn() as escaped:
        escaped.execute("CREATE TABLE close_probe(value INTEGER PRIMARY KEY)")
        escaped.execute("INSERT INTO close_probe(value) VALUES(1)")

    escaped.close()
    with db.db_conn() as cx:
        observed = cx.execute("SELECT value FROM close_probe").fetchone()[0]
    assert observed == 1


def test_atomic_database_replacement_retires_the_cached_file_identity(
        tmp_path, monkeypatch):
    """A same-path restore must not keep serving the replaced database inode."""
    db, _config_stats = _isolated_db_and_config(
        tmp_path, monkeypatch, tracing=False)
    db_path = Path(db.DB_PATH)

    with db.db_conn() as cx:
        cx.execute("CREATE TABLE identity_probe(value TEXT NOT NULL)")
        cx.execute("INSERT INTO identity_probe(value) VALUES('original')")

    original_identity = (db_path.stat().st_dev, db_path.stat().st_ino)
    replacement = tmp_path / "replacement.db"
    replacement_cx = sqlite3.connect(replacement)
    try:
        replacement_cx.execute("CREATE TABLE identity_probe(value TEXT NOT NULL)")
        replacement_cx.execute(
            "INSERT INTO identity_probe(value) VALUES('replacement')")
        replacement_cx.commit()
    finally:
        replacement_cx.close()
    replacement_identity = (replacement.stat().st_dev, replacement.stat().st_ino)
    assert replacement_identity != original_identity, (
        "precondition: replacement and original unexpectedly share one inode")

    replacement.replace(db_path)
    moved_companions = []
    for suffix in ("-wal", "-shm"):
        companion = Path(str(db_path) + suffix)
        if companion.exists():
            companion.replace(tmp_path / f"original.db{suffix}")
            moved_companions.append(suffix)
    assert (db_path.stat().st_dev, db_path.stat().st_ino) == replacement_identity, (
        "precondition: atomic replacement did not move the new database into place")
    assert moved_companions == ["-wal", "-shm"], (
        "precondition: WAL-mode replacement did not preserve both companions; "
        f"moved {moved_companions}")
    physical_opens = _count_physical_opens(db, monkeypatch)

    with db.db_conn() as cx:
        observed = cx.execute("SELECT value FROM identity_probe").fetchone()[0]

    assert observed == "replacement", (
        "the cached connection kept serving the unlinked pre-restore database")
    assert len(physical_opens) == 1, (
        "the first lease after a same-path replacement must physically reopen; "
        f"observed {len(physical_opens)} opens")


def test_replacement_between_connect_and_identity_binding_is_retried(
        tmp_path, monkeypatch):
    """Never cache an old open handle under a newly replaced path's inode."""
    db, _config_stats = _isolated_db_and_config(
        tmp_path, monkeypatch, tracing=False)
    db_path = Path(db.DB_PATH)

    def make_database(path, value):
        cx = sqlite3.connect(path)
        try:
            cx.execute("CREATE TABLE race_probe(value TEXT NOT NULL)")
            cx.execute("INSERT INTO race_probe(value) VALUES(?)", (value,))
            cx.commit()
        finally:
            cx.close()

    make_database(db_path, "before")
    replacement = tmp_path / "race-replacement.db"
    make_database(replacement, "after")
    real_open = db._open_history_conn
    opened_values = []

    def replace_after_first_open(path=None):
        cx = real_open(path)
        opened_values.append(
            cx.execute("SELECT value FROM race_probe").fetchone()[0])
        if len(opened_values) == 1:
            replacement.replace(db_path)
            for suffix in ("-wal", "-shm"):
                companion = Path(str(db_path) + suffix)
                if companion.exists():
                    companion.replace(tmp_path / f"race-before.db{suffix}")
        return cx

    monkeypatch.setattr(db, "_open_history_conn", replace_after_first_open)
    with db.db_conn() as first:
        first_observed = first.execute("SELECT value FROM race_probe").fetchone()[0]
    with db.db_conn() as second:
        second_observed = second.execute("SELECT value FROM race_probe").fetchone()[0]

    assert opened_values == ["before", "after"], (
        "precondition: the open/replacement race did not force one retry; "
        f"opened {opened_values}")
    assert (first_observed, second_observed) == ("after", "after"), (
        "a connection opened before replacement was cached under the new inode")
