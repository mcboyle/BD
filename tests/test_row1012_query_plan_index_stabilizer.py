"""Unit tests for Row 1012: Automated Query Plan Optimizer & Statistics Index Stabilizer.

Guards:
- SQLite query plan extraction and cost estimation via EXPLAIN QUERY PLAN
- Detection of full table scans and temporary B-tree sorting bottlenecks
- Index recommendation engine for missing predicate coverage
- Statistics stabilization via scheduled ANALYZE and PRAGMA optimize
- Structured query plan telemetry and observability export
"""
from __future__ import annotations

import json
import sqlite3

import pytest

try:
    from bulk_downloader.query_plan_stabilizer import (
        IndexRecommendation,
        PlanCostEstimate,
        PlanStabilizerConfig,
        QueryPlanAnalysis,
        QueryPlanOptimizer,
        StatisticsIndexStabilizer,
    )
except (ImportError, ModuleNotFoundError):
    IndexRecommendation = None
    PlanCostEstimate = None
    PlanStabilizerConfig = None
    QueryPlanAnalysis = None
    QueryPlanOptimizer = None
    StatisticsIndexStabilizer = None

BD_GATE_SCOPE = "repo-wide"


@pytest.fixture
def test_db():
    """Create in-memory SQLite database with sample schema and test data."""
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_key TEXT NOT NULL,
            url TEXT NOT NULL,
            status TEXT NOT NULL,
            bytes_downloaded INTEGER DEFAULT 0,
            created_at REAL NOT NULL
        )
    """)
    # Insert sample rows
    for i in range(200):
        cursor.execute(
            "INSERT INTO downloads (site_key, url, status, bytes_downloaded, created_at) VALUES (?, ?, ?, ?, ?)",
            (f"site_{i % 5}", f"http://example.com/{i}", "completed" if i % 2 == 0 else "pending", i * 1024, 1700000000.0 + i),
        )
    conn.commit()
    yield conn
    conn.close()


def test_metadata_and_classes():
    """Verify class interfaces, configurations, and defaults."""
    assert QueryPlanOptimizer is not None, "QueryPlanOptimizer capability must be present (bulk_downloader.query_plan_stabilizer)"
    assert StatisticsIndexStabilizer is not None, "StatisticsIndexStabilizer capability must be present"
    assert PlanStabilizerConfig is not None, "PlanStabilizerConfig capability must be present"

    config = PlanStabilizerConfig(enable_stat_refresh=False)
    assert config.enable_stat_refresh is False

    optimizer = QueryPlanOptimizer(config=config)
    assert optimizer.config is config
    assert isinstance(optimizer.stabilizer, StatisticsIndexStabilizer)


def test_explain_query_plan_scan_detection(test_db):
    """Verify explain_plan detects unindexed table scans on downloads table."""
    optimizer = QueryPlanOptimizer()
    query = "SELECT * FROM downloads WHERE site_key = 'site_1' AND status = 'completed'"
    analysis = optimizer.analyze_query(test_db, query)

    assert isinstance(analysis, QueryPlanAnalysis)
    assert analysis.has_full_scan is True
    assert "downloads" in analysis.scanned_tables
    assert len(analysis.plan_steps) > 0


def test_index_recommendation_and_creation(test_db):
    """Verify recommendation generation and creation of covering index."""
    optimizer = QueryPlanOptimizer()
    query = "SELECT * FROM downloads WHERE site_key = 'site_1' AND status = 'completed'"
    recommendations = optimizer.recommend_indices(test_db, query)

    assert len(recommendations) >= 1
    rec = recommendations[0]
    assert isinstance(rec, IndexRecommendation)
    assert rec.table_name == "downloads"
    assert "site_key" in rec.columns or "status" in rec.columns
    assert "CREATE INDEX" in rec.suggested_sql

    # Apply recommendation
    cursor = test_db.cursor()
    cursor.execute(rec.suggested_sql)
    test_db.commit()

    # Re-analyze query: scan should be eliminated
    analysis_after = optimizer.analyze_query(test_db, query)
    assert analysis_after.has_full_scan is False


def test_statistics_stabilization(test_db):
    """Verify running stats stabilization runs ANALYZE / optimize without error."""
    stabilizer = StatisticsIndexStabilizer()
    test_db.execute("CREATE INDEX idx_downloads_status ON downloads (status)")
    executed = []
    test_db.set_trace_callback(executed.append)
    res = stabilizer.stabilize_database(test_db)
    test_db.set_trace_callback(None)
    assert res.get("ok") is True
    assert res["analyze_executed"] is True and res["pragma_optimize_executed"] is True
    # E3: both statements really run, and ANALYZE really wrote planner statistics.
    assert "ANALYZE" in executed and "PRAGMA optimize" in executed, executed
    stats = test_db.execute("SELECT idx FROM sqlite_stat1 WHERE tbl = 'downloads'").fetchall()
    assert ("idx_downloads_status",) in stats, stats


def test_query_plan_cost_and_complexity(test_db):
    """Verify cost estimate computation for indexed vs unindexed queries."""
    optimizer = QueryPlanOptimizer()
    query_unindexed = "SELECT * FROM downloads WHERE url = 'http://example.com/5'"
    cost_unindexed = optimizer.estimate_plan_cost(test_db, query_unindexed)
    assert isinstance(cost_unindexed, PlanCostEstimate)
    assert cost_unindexed.estimated_cost >= 1.0

    # Primary key query should have minimal cost
    query_pk = "SELECT * FROM downloads WHERE id = 5"
    cost_pk = optimizer.estimate_plan_cost(test_db, query_pk)
    assert cost_pk.estimated_cost <= cost_unindexed.estimated_cost


def test_telemetry_export(test_db):
    """Verify telemetry aggregation and JSON serialization."""
    optimizer = QueryPlanOptimizer()
    optimizer.analyze_query(test_db, "SELECT * FROM downloads WHERE id = 1")
    optimizer.analyze_query(test_db, "SELECT * FROM downloads WHERE site_key = 'site_0'")

    telemetry = optimizer.get_telemetry()
    assert telemetry.queries_analyzed == 2
    assert telemetry.scans_detected >= 1

    payload = optimizer.export_telemetry_dict()
    assert isinstance(payload, dict)
    assert "metrics" in payload
    assert "config" in payload

    # Ensure JSON serializable
    json_str = json.dumps(payload)
    assert len(json_str) > 0


def test_production_db_caller_stabilize_statistics(monkeypatch, tmp_path):
    """Verify production caller bulk_downloader.db.db_stabilize_statistics."""
    import bulk_downloader.db as bddb
    db_file = tmp_path / "test_history.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE history (id INTEGER PRIMARY KEY, url TEXT, status TEXT)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(bddb, "_resolve_db_path", lambda: str(db_file))
    result = bddb.db_stabilize_statistics()
    assert isinstance(result, dict)
    assert result.get("ok") is True
    assert result.get("pragma_optimize_executed") is True



def test_stat_refresh_disabled_runs_only_pragma_optimize(test_db):
    stabilizer = StatisticsIndexStabilizer(enable_stat_refresh=False)
    executed = []
    test_db.set_trace_callback(executed.append)
    res = stabilizer.stabilize_database(test_db)
    test_db.set_trace_callback(None)
    assert "ANALYZE" not in executed and "PRAGMA optimize" in executed, executed
    assert res["ok"] is True and res["analyze_executed"] is False


def test_a_failed_statistics_refresh_is_not_ok(test_db):
    """ok must reflect the work: a refresh whose statements all fail is not ok."""
    class _Failing:
        def cursor(self):
            return self

        def execute(self, _sql):
            raise sqlite3.OperationalError("database is locked")

    res = StatisticsIndexStabilizer().stabilize_database(_Failing())
    assert res["ok"] is False
    assert res["analyze_executed"] is False and res["pragma_optimize_executed"] is False


@pytest.mark.parametrize("ddl, query", [
    ("CREATE INDEX idx_downloads_created ON downloads (created_at)",
     "SELECT id FROM downloads ORDER BY created_at"),
    ("CREATE INDEX idx_downloads_site_status ON downloads (site_key, status)",
     "SELECT site_key, status FROM downloads"),
])
def test_an_index_scan_is_not_a_full_table_scan(test_db, ddl, query):
    """E2: "SCAN t USING [COVERING] INDEX i" walks an index; it is not a table scan and
    must not be charged as one or earn an index recommendation (least of all on id)."""
    test_db.execute(ddl)
    optimizer = QueryPlanOptimizer()
    analysis = optimizer.analyze_query(test_db, query)
    assert any("USING" in s and "INDEX" in s for s in analysis.plan_steps), analysis.plan_steps
    assert analysis.has_full_scan is False and analysis.scanned_tables == [], analysis
    assert analysis.used_indices, analysis
    assert optimizer.estimate_plan_cost(test_db, query).estimated_cost == 1.0
    assert optimizer.recommend_indices(test_db, query) == []


def test_a_full_scan_is_charged_more_than_an_index_lookup(test_db):
    optimizer = QueryPlanOptimizer()
    scan = optimizer.estimate_plan_cost(test_db, "SELECT * FROM downloads WHERE url = 'x'")
    lookup = optimizer.estimate_plan_cost(test_db, "SELECT * FROM downloads WHERE id = 5")
    assert scan.has_scan is True and scan.estimated_cost == 11.0
    assert lookup.has_scan is False and lookup.estimated_cost == 1.0


def test_a_scan_without_a_predicate_gets_no_recommendation(test_db):
    """No WHERE column -> no index can remove the scan -> nothing is recommended."""
    optimizer = QueryPlanOptimizer()
    assert optimizer.analyze_query(test_db, "SELECT * FROM downloads").has_full_scan is True
    assert optimizer.recommend_indices(test_db, "SELECT * FROM downloads") == []


def test_temp_btree_sort_is_detected_and_charged(test_db):
    optimizer = QueryPlanOptimizer()
    query = "SELECT * FROM downloads ORDER BY bytes_downloaded"
    analysis = optimizer.analyze_query(test_db, query)
    assert analysis.has_temp_btree is True, analysis.plan_steps
    assert optimizer.estimate_plan_cost(test_db, query).estimated_cost == 16.0


def _scheduler_task(monkeypatch, tmp_path, idle):
    from bulk_downloader import bg_scheduler, db
    db_file = tmp_path / "row1012_sched.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_file))
    monkeypatch.setattr(bg_scheduler, "is_idle", lambda: idle)
    db.db_init()
    with db.db_conn() as cx:
        cx.executemany("INSERT INTO history (site_id, url, status, message) VALUES (?, ?, 'done', '')",
                       [("s", f"http://example.com/{i}") for i in range(50)])
    bg_scheduler.register_default_tasks()
    with bg_scheduler._lock:
        task = bg_scheduler._tasks.get("sqlite.stabilize_statistics")
    assert task is not None, "sqlite.stabilize_statistics must be registered (row1012 E1: automated)"
    assert task["interval"] == 86400
    return db, task


def _stat_rows(db):
    with db.db_conn() as cx:
        if not cx.execute("SELECT 1 FROM sqlite_master WHERE name = 'sqlite_stat1'").fetchone():
            return 0
        return cx.execute("SELECT COUNT(*) FROM sqlite_stat1 WHERE tbl = 'history'").fetchone()[0]


def test_scheduler_refreshes_planner_statistics_when_idle(monkeypatch, tmp_path):
    """E1: the scheduled task reaches db_stabilize_statistics and ANALYZE writes stats."""
    db, task = _scheduler_task(monkeypatch, tmp_path, idle=True)
    assert _stat_rows(db) == 0, "positive control: fresh history has no planner statistics"
    res = task["fn"]()
    assert res["ok"] is True and res["analyze_executed"] is True, res
    assert _stat_rows(db) > 0


def test_scheduler_skips_statistics_refresh_while_busy(monkeypatch, tmp_path):
    db, task = _scheduler_task(monkeypatch, tmp_path, idle=False)
    assert task["fn"]() == {"ok": True, "skipped": "not idle"}
    assert _stat_rows(db) == 0


def test_t69_failed_refresh_reports_its_errors():
    """T69 ratchet (defect_DP_total +3, all DP-13 in query_plan_stabilizer.py): the refresh's
    failures must travel in the result, not only in a log line."""
    class _Failing:
        def cursor(self):
            return self

        def execute(self, _sql):
            raise sqlite3.OperationalError("t69 database is locked")

    res = StatisticsIndexStabilizer().stabilize_database(_Failing())
    errors = res.get("errors") or []
    assert len(errors) == 2 and all("t69 database is locked" in e for e in errors), (
        f"T69: failed statistics refresh swallowed its errors, result={res!r}")


def test_t69_unexplainable_query_is_not_reported_as_a_clean_plan(test_db):
    """An EXPLAIN that fails must not read as 'no full scan, no temp b-tree'."""
    from bulk_downloader.query_plan_stabilizer import QueryPlanOptimizer

    analysis = QueryPlanOptimizer().analyze_query(test_db, "SELECT * FROM no_such_table_t69")
    error = getattr(analysis, "error", None)
    assert error and "no_such_table_t69" in error, (
        f"T69: an unexplainable query was reported as a clean plan, error={getattr(analysis, 'error', 'MISSING')!r}")
