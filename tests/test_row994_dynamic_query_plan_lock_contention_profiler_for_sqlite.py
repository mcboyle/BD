"""Row 994: SQLite lock contention profiler (RESCOPED per RULING-2338-row994-RESCOPE-a).

Query-plan classification stays with dev_suite.db_tools._explain_query_plan /
index_advisor; this row pins (1) the constant-row and subquery steps that
classifier used to call full table scans, and (2) lock-contention telemetry fed
by db.db_init's locked-retry loop and read through dev_suite.db_overview.

RED on base is behavioural: the profiler module is imported inside a guard, so
a missing capability fails an assertion, never a collection ImportError.
"""
from __future__ import annotations

import importlib
import os
import sqlite3
import tempfile

import pytest

import bulk_downloader.db as db
from bulk_downloader.dev_suite import db_tools, introspection

BD_GATE_SCOPE = "repo-wide"

try:
    db_profiler = importlib.import_module("bulk_downloader.db_profiler")
except ImportError:
    db_profiler = None


def _profiler_module():
    assert db_profiler is not None, "row 994: bulk_downloader.db_profiler (lock contention profiler) is missing"
    return db_profiler


@pytest.fixture
def clean_profiler():
    """Drop the process profiler around a test; the capability itself is asserted in the body."""
    if db_profiler is not None:
        db_profiler.reset_contention_profiler()
    yield
    if db_profiler is not None:
        db_profiler.reset_contention_profiler()


def _plan_cx():
    cx = sqlite3.connect(":memory:")
    cx.row_factory = sqlite3.Row
    cx.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, a)")
    cx.execute("CREATE INDEX ia ON t(a)")
    return cx


# --- E3: the existing classifier on product query shapes ---

def test_explain_constant_row_and_values_list_are_not_full_scans():
    cx = _plan_cx()
    one = db_tools._explain_query_plan(cx, "SELECT 1", ())
    assert one["steps"] == ["SCAN CONSTANT ROW"]
    assert one["full_scan"] is False, f"'SELECT 1' classified as a full table scan: {one}"
    values = db_tools._explain_query_plan(cx, "SELECT * FROM (VALUES(1),(2))", ())
    assert values["full_scan"] is False, f"VALUES list classified as a full table scan: {values}"


def test_explain_still_flags_real_table_scans_through_subqueries():
    """Negative control: the exclusion must not hide a genuine scan."""
    cx = _plan_cx()
    assert db_tools._explain_query_plan(cx, "SELECT * FROM t", ())["full_scan"] is True
    inner = db_tools._explain_query_plan(cx, "SELECT * FROM t WHERE id IN (SELECT a FROM t)", ())
    assert inner["full_scan"] is True
    assert db_tools._explain_query_plan(cx, "SELECT * FROM t WHERE a = 1", ())["full_scan"] is False


# --- E4: BUSY vs LOCKED from SQLite's own wording ---

def test_real_file_lock_is_classified_busy_not_locked(clean_profiler):
    mod = _profiler_module()
    d = tempfile.mkdtemp(prefix="row994_")
    path = os.path.join(d, "busy.db")
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("CREATE TABLE q(x)")
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO q VALUES (1)")
    other = sqlite3.connect(path, timeout=0)
    try:
        with pytest.raises(sqlite3.OperationalError) as exc_info:
            other.execute("INSERT INTO q VALUES (2)")
        assert getattr(exc_info.value, "sqlite_errorname", "SQLITE_BUSY") == "SQLITE_BUSY"
        assert mod.classify_lock_error(exc_info.value) == "SQLITE_BUSY"
        mod.record_lock_stall("q", 1.0, exc_info.value)
        report = mod.get_contention_profiler().get_contention_report()
        assert report["contended_tables"]["q"]["last_error"] == "SQLITE_BUSY"
    finally:
        other.close()
        holder.execute("ROLLBACK")
        holder.close()


def test_shared_cache_table_lock_is_classified_locked(clean_profiler):
    mod = _profiler_module()
    msg = "database table is locked: sqlite_master"
    assert mod.classify_lock_error(sqlite3.OperationalError(msg)) == "SQLITE_LOCKED"
    assert mod.classify_lock_error("no such table: x") is None


# --- E2: fed by the product's locked-retry loop, read by db_overview ---

def test_report_is_unobserved_until_a_feed_attaches(clean_profiler):
    _profiler_module()
    report = db.db_lock_contention_report()
    assert report["status"] == "unobserved"
    assert report["feeds"] == []
    assert report["total_contention_events"] == 0


def test_db_init_locked_retry_feeds_the_profiler_and_db_overview_reads_it(clean_profiler, monkeypatch):
    _profiler_module()
    real_once = db._db_init_once
    calls = {"n": 0}

    def locked_twice():
        calls["n"] += 1
        if calls["n"] <= 2:
            raise sqlite3.OperationalError("database table is locked: sqlite_master")
        return real_once()

    monkeypatch.setattr(db, "_db_init_once", locked_twice)
    monkeypatch.setattr(db, "_sleep", lambda s: None)
    db.db_init()
    assert calls["n"] == 3

    report = db.db_lock_contention_report()
    assert report["status"] == "observed"
    assert report["feeds"] == ["db.db_init"]
    assert report["total_contention_events"] == 1, "one stalled init is one event"
    assert report["contended_tables"]["sqlite_master"]["last_error"] == "SQLITE_LOCKED"

    overview = introspection.db_overview()
    assert overview["lock_contention"]["total_contention_events"] == 1
    assert overview["lock_contention"]["contended_tables"]["sqlite_master"]["events"] == 1


def test_db_init_without_a_stall_attaches_the_feed_and_records_nothing(clean_profiler):
    _profiler_module()
    db.db_init()
    report = introspection.db_overview()["lock_contention"]
    assert report["status"] == "observed"
    assert report["total_contention_events"] == 0


def test_db_init_give_up_records_the_stall_before_raising(clean_profiler, monkeypatch):
    _profiler_module()

    def always_locked():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db, "_db_init_once", always_locked)
    with pytest.raises(sqlite3.OperationalError):
        db.db_init(_retry_seconds=0)
    report = db.db_lock_contention_report()
    assert report["total_contention_events"] == 1
    assert report["contended_tables"]["unknown"]["last_error"] == "SQLITE_BUSY"


# --- E5: aggregation arithmetic and the history bound ---

def test_contention_aggregates_and_bounds_history(clean_profiler):
    mod = _profiler_module()
    prof = mod.SQLiteLockContentionProfiler(max_history=2)
    prof.record_contention("a", 10.0, "SQLITE_BUSY")
    prof.record_contention("a", 30.0, "SQLITE_LOCKED")
    prof.record_contention("b", 5.0, "SQLITE_BUSY")
    report = prof.get_contention_report()
    assert report["total_contention_events"] == 3
    assert report["total_stall_ms"] == 45.0
    assert report["max_stall_ms"] == 30.0
    assert report["avg_stall_ms"] == 15.0
    assert report["contended_tables"]["a"] == {
        "events": 2, "total_stall_ms": 40.0, "max_stall_ms": 30.0, "last_error": "SQLITE_LOCKED"}
    assert report["recent_events_count"] == 2
