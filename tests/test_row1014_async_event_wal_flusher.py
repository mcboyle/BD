"""Row 1014: Asynchronous Non-Blocking Event-Driven WAL Flusher Pipeline.

Validates non-blocking event-driven WAL checkpointing, event batching/coalescing,
asynchronous background pipeline execution, metric accounting, and integration with
bulk_downloader.db_maintenance.

RED on baseline: fails with explicit AssertionError (capability missing), not an unhandled ImportError.
Includes positive control test passing on baseline to prove the probe can say YES.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import time
import pytest

BD_GATE_SCOPE = "module"

try:
    from bulk_downloader import async_wal_flusher
except ImportError:
    async_wal_flusher = None


def _wait_result(pipeline, event_id, timeout=5.0):
    """Poll the background worker for an event result; None only if it never lands."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        res = pipeline.get_event_result(event_id)
        if res is not None:
            return res
        time.sleep(0.01)
    return None


def _make_wal_db(path, rows=50):
    """Create a WAL-mode SQLite DB whose -wal sidecar still holds frames."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE t (x INT)")
    for i in range(rows):
        conn.execute("INSERT INTO t VALUES (?)", (i,))
    conn.commit()
    return conn


def _manual_pipeline(**kw):
    from bulk_downloader.async_wal_flusher import AsyncWalFlusherPipeline, FlusherConfig

    return AsyncWalFlusherPipeline(config=FlusherConfig(auto_start=False, **kw))


def test_positive_control_db_maintenance_wal_checkpoint_baseline():
    """Positive control (Rule 7): proves test runner and probe can say YES on baseline capabilities."""
    from bulk_downloader import db_maintenance

    assert hasattr(db_maintenance, "run_sqlite_maintenance")
    assert callable(db_maintenance.run_sqlite_maintenance)


def test_async_wal_flusher_capability_implemented():
    """RED assertion 1: capability and product callers must be implemented with semantic AssertionError on base."""
    from bulk_downloader import db_maintenance

    assert async_wal_flusher is not None, (
        "Row 1014 capability missing: Asynchronous Non-Blocking Event-Driven WAL Flusher Pipeline "
        "not implemented in bulk_downloader.async_wal_flusher"
    )
    assert hasattr(db_maintenance, "schedule_async_wal_flush"), (
        "Row 1014 caller missing: bulk_downloader.db_maintenance.schedule_async_wal_flush"
    )


def test_module_exports():
    """Verify bulk_downloader.async_wal_flusher exports all pipeline components."""
    assert async_wal_flusher is not None, "async_wal_flusher capability missing"

    assert hasattr(async_wal_flusher, "FlushEvent")
    assert hasattr(async_wal_flusher, "FlushResult")
    assert hasattr(async_wal_flusher, "FlusherConfig")
    assert hasattr(async_wal_flusher, "AsyncWalFlusherPipeline")
    assert hasattr(async_wal_flusher, "get_async_wal_flusher")
    assert hasattr(async_wal_flusher, "reset_async_wal_flusher")


def test_flusher_config_defaults_and_customization():
    """Verify configuration model for flusher pipeline."""
    assert async_wal_flusher is not None, "async_wal_flusher capability missing"
    from bulk_downloader.async_wal_flusher import FlusherConfig

    cfg = FlusherConfig(flush_interval_ms=25.0, batch_size=5, max_queue_size=50)
    assert cfg.flush_interval_ms == 25.0
    assert cfg.batch_size == 5
    assert cfg.max_queue_size == 50
    assert cfg.default_checkpoint_mode in ("PASSIVE", "FULL", "RESTART", "TRUNCATE")


def test_non_blocking_enqueue_and_metrics():
    """Verify enqueuing flush events returns event tokens without blocking."""
    assert async_wal_flusher is not None, "async_wal_flusher capability missing"
    from bulk_downloader.async_wal_flusher import (
        AsyncWalFlusherPipeline,
        FlusherConfig,
    )

    pipeline = AsyncWalFlusherPipeline(config=FlusherConfig(auto_start=False))
    event_id = pipeline.enqueue_flush("/tmp/test_db.sqlite", checkpoint_mode="PASSIVE")
    assert isinstance(event_id, str)
    assert event_id.startswith("ev_")

    metrics = pipeline.get_metrics()
    assert metrics.total_enqueued == 1
    assert metrics.pending_events == 1
    assert metrics.total_flushed == 0


def test_background_flush_execution_on_sqlite_wal():
    """Verify background worker performs real WAL checkpointing on a SQLite database in WAL mode."""
    assert async_wal_flusher is not None, "async_wal_flusher capability missing"
    from bulk_downloader.async_wal_flusher import (
        AsyncWalFlusherPipeline,
        FlusherConfig,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_wal.sqlite")
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE t (x INT)")
        for i in range(50):
            conn.execute("INSERT INTO t VALUES (?)", (i,))
        conn.commit()
        conn.close()

        pipeline = AsyncWalFlusherPipeline(
            config=FlusherConfig(flush_interval_ms=10.0, auto_start=True)
        )
        try:
            ev_id = pipeline.enqueue_flush(db_path, checkpoint_mode="PASSIVE")
            result = _wait_result(pipeline, ev_id)
            assert result is not None
            assert result.success is True
            assert result.checkpointed_frames >= 0

            metrics = pipeline.get_metrics()
            assert metrics.total_flushed >= 1
        finally:
            pipeline.stop()


def test_flush_event_coalescing():
    """Verify multiple rapid flush events for the same database are coalesced into a single checkpoint."""
    assert async_wal_flusher is not None, "async_wal_flusher capability missing"
    from bulk_downloader.async_wal_flusher import (
        AsyncWalFlusherPipeline,
        FlusherConfig,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "coalesce.sqlite")
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES ('a')")
        conn.commit()
        conn.close()

        # Stop worker temporarily to accumulate queue
        pipeline = AsyncWalFlusherPipeline(
            config=FlusherConfig(flush_interval_ms=10.0, auto_start=False)
        )

        ev1 = pipeline.enqueue_flush(db_path, checkpoint_mode="PASSIVE")
        ev2 = pipeline.enqueue_flush(db_path, checkpoint_mode="PASSIVE")
        ev3 = pipeline.enqueue_flush(db_path, checkpoint_mode="PASSIVE")

        # Manually process batch
        flushed_count = pipeline.process_pending_batch()
        # Coalesced: only 1 physical checkpoint operation executed for the single target DB
        assert flushed_count == 1

        r1 = pipeline.get_event_result(ev1)
        r2 = pipeline.get_event_result(ev2)
        r3 = pipeline.get_event_result(ev3)
        assert r1 is not None and r1.success is True
        assert r2 is not None and r2.success is True
        assert r3 is not None and r3.success is True

        metrics = pipeline.get_metrics()
        assert metrics.coalesced_events == 2


def test_db_maintenance_caller_integration():
    """Verify concrete integration with bulk_downloader.db_maintenance.schedule_async_wal_flush."""
    assert async_wal_flusher is not None, "async_wal_flusher capability missing"
    from bulk_downloader import db_maintenance

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "maint_wal.sqlite")
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE test (id INT)")
        conn.commit()
        conn.close()

        try:
            ev_id = db_maintenance.schedule_async_wal_flush(db_path, checkpoint_mode="PASSIVE")
            assert isinstance(ev_id, str)
            assert ev_id.startswith("ev_")

            flusher = async_wal_flusher.get_async_wal_flusher()
            res = _wait_result(flusher, ev_id)
            assert res is not None
            assert res.success is True
        finally:
            async_wal_flusher.reset_async_wal_flusher()


def test_missing_database_fails_closed():
    """E2: a flush that could not run is a failure, not a flushed event."""
    assert async_wal_flusher is not None, "async_wal_flusher capability missing"
    pipeline = _manual_pipeline()
    ev = pipeline.enqueue_flush("/nonexistent/dir/row1014.db")
    assert pipeline.process_pending_batch() == 1

    res = pipeline.get_event_result(ev)
    assert res is not None
    assert res.success is False, res
    assert "does not exist" in (res.error or ""), res.error
    metrics = pipeline.get_metrics()
    assert metrics.total_flushed == 0, metrics
    assert metrics.errors_count == 1, metrics


def test_reader_held_truncate_reports_busy():
    """E4: a TRUNCATE blocked by an open reader reports busy and success False."""
    assert async_wal_flusher is not None, "async_wal_flusher capability missing"
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "busy.sqlite")
        writer = _make_wal_db(db_path)
        reader = sqlite3.connect(db_path, isolation_level=None)
        try:
            reader.execute("BEGIN")
            reader.execute("SELECT count(*) FROM t").fetchone()
            writer.execute("INSERT INTO t VALUES (999)")
            writer.commit()

            pipeline = _manual_pipeline()
            ev = pipeline.enqueue_flush(db_path, checkpoint_mode="TRUNCATE")
            pipeline.process_pending_batch()
            res = pipeline.get_event_result(ev)
            assert res is not None
            assert res.busy >= 1, res
            assert res.success is False, res
            assert pipeline.get_metrics().errors_count == 1
            assert pipeline.get_metrics().total_flushed == 0
        finally:
            reader.execute("ROLLBACK")
            reader.close()
            writer.close()


def test_strictest_mode_reaches_the_pragma():
    """E4: coalesced PASSIVE+TRUNCATE runs TRUNCATE, which empties the -wal sidecar."""
    assert async_wal_flusher is not None, "async_wal_flusher capability missing"
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "mode.sqlite")
        keep = _make_wal_db(db_path)
        wal = db_path + "-wal"
        try:
            # PRECONDITION: the sidecar holds frames, so a TRUNCATE is observable.
            assert os.path.getsize(wal) > 0

            pipeline = _manual_pipeline()
            ev_p = pipeline.enqueue_flush(db_path, checkpoint_mode="PASSIVE")
            ev_t = pipeline.enqueue_flush(db_path, checkpoint_mode="truncate")
            assert pipeline.process_pending_batch() == 1

            for ev in (ev_p, ev_t):
                res = pipeline.get_event_result(ev)
                assert res is not None and res.success is True, res
                assert res.checkpoint_mode == "TRUNCATE", res
            assert os.path.getsize(wal) == 0
        finally:
            keep.close()


def test_queue_overflow_is_a_failed_event():
    """E4: a dropped event resolves as a failure and is counted."""
    assert async_wal_flusher is not None, "async_wal_flusher capability missing"
    pipeline = _manual_pipeline(max_queue_size=1)
    pipeline.enqueue_flush("/nonexistent/row1014-a.db")
    dropped = pipeline.enqueue_flush("/nonexistent/row1014-b.db")

    res = pipeline.get_event_result(dropped)
    assert res is not None, "dropped event left unresolved"
    assert res.success is False
    assert "queue full" in (res.error or "")
    metrics = pipeline.get_metrics()
    assert metrics.errors_count == 1, metrics
    assert metrics.total_enqueued == 1, metrics


def test_results_are_bounded():
    """NOTE: per-event results cannot grow without bound in a long-running process."""
    assert async_wal_flusher is not None, "async_wal_flusher capability missing"
    pipeline = _manual_pipeline(max_results=3)
    ids = [pipeline.enqueue_flush(f"/nonexistent/row1014-{i}.db") for i in range(5)]
    pipeline.process_pending_batch()
    assert pipeline.get_event_result(ids[0]) is None
    assert pipeline.get_event_result(ids[-1]) is not None
    assert len(pipeline._results) == 3


def test_db_maintenance_uses_the_one_pipeline(monkeypatch):
    """E3: db_maintenance routes through async_wal_flusher's singleton, not a copy."""
    assert async_wal_flusher is not None, "async_wal_flusher capability missing"
    from bulk_downloader import db_maintenance

    pipeline = _manual_pipeline()
    monkeypatch.setattr(async_wal_flusher, "_GLOBAL_FLUSHER", pipeline)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "one.sqlite")
        _make_wal_db(db_path).close()
        ev = db_maintenance.schedule_async_wal_flush(db_path)
        assert db_maintenance.trigger_async_wal_flush(db_path=db_path) is True
        assert pipeline.get_metrics().total_enqueued == 2
        pipeline.process_pending_batch()
        res = pipeline.get_event_result(ev)
        assert res is not None and res.success is True, res


def test_trigger_without_a_path_fails_closed(monkeypatch):
    """E2 (P6): no database named -> nothing flushed and the call says so."""
    assert async_wal_flusher is not None, "async_wal_flusher capability missing"
    from bulk_downloader import db_maintenance

    pipeline = _manual_pipeline()
    monkeypatch.setattr(async_wal_flusher, "_GLOBAL_FLUSHER", pipeline)
    assert db_maintenance.trigger_async_wal_flush() is False
    assert pipeline.get_metrics().total_enqueued == 0


def _scheduler_task(monkeypatch, db_path):
    from bulk_downloader import bg_scheduler as bg
    from bulk_downloader import db as _db
    from bulk_downloader import db_maintenance

    pipeline = _manual_pipeline()
    monkeypatch.setattr(async_wal_flusher, "_GLOBAL_FLUSHER", pipeline)
    monkeypatch.setattr(db_maintenance, "_LAST_SCHEDULED_FLUSH", None, raising=False)
    monkeypatch.setattr(_db, "_resolve_db_path", lambda: db_path)
    monkeypatch.setattr(bg, "_tasks", {})
    bg.register_default_tasks(s_cfg_getter=lambda: {})
    task = bg._tasks.get("db.wal_flush")
    assert task is not None, sorted(bg._tasks)
    return bg, task, pipeline


def test_scheduler_wal_flush_routes_through_pipeline(monkeypatch):
    """E1: the periodic scheduler task is a real product caller of the pipeline."""
    assert async_wal_flusher is not None, "async_wal_flusher capability missing"
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "app.sqlite")
        _make_wal_db(db_path).close()
        bg, task, pipeline = _scheduler_task(monkeypatch, db_path)

        bg._run_one("db.wal_flush", task)
        assert task["last_status"] == "ok", task
        assert pipeline.get_metrics().total_enqueued == 1
        assert pipeline.process_pending_batch() == 1

        bg._run_one("db.wal_flush", task)
        assert task["last_status"] == "ok", task
        assert pipeline.get_metrics().total_flushed == 1


def test_scheduler_wal_flush_surfaces_a_failed_flush(monkeypatch):
    """E1+E2: a failed background flush is visible in the scheduler task status."""
    assert async_wal_flusher is not None, "async_wal_flusher capability missing"
    bg, task, pipeline = _scheduler_task(monkeypatch, "/nonexistent/row1014/app.sqlite")

    bg._run_one("db.wal_flush", task)
    assert task["last_status"] == "ok", task
    pipeline.process_pending_batch()

    bg._run_one("db.wal_flush", task)
    assert task["last_status"] == "error", task
    assert "does not exist" in task["last_error"], task["last_error"]
