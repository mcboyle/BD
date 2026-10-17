from __future__ import annotations

import sqlite3
from unittest.mock import patch

import tools.db_cutover_probe as probe
from bulk_downloader.database import DatabaseManager
from bulk_downloader.storage.postgres_backend import PostgresBackend


BD_GATE_SCOPE = "module"


def test_primary_write_authority_success(tmp_path):
    db_file = str(tmp_path / "test.db")
    mgr = DatabaseManager(sqlite_path=db_file, dsn="postgresql://test:test@127.0.0.1:5432/test")
    with patch.object(mgr.backend, "primary_write_enabled", return_value=True), patch.object(mgr.backend, "write_authoritative", return_value=(True, 42, None)) as mock_pg:
        conn = mgr.get_connection()
        conn.execute("INSERT INTO queue (site_id, url) VALUES (?, ?)", ("site1", "http://example.com"))
        assert mock_pg.called
        row = conn._sqlite_cx.execute("SELECT site_id, url FROM queue").fetchone()
        assert row is not None
        assert row[0] == "site1"

def test_primary_write_connection_drop_fallback(tmp_path):
    db_file = str(tmp_path / "test.db")
    mgr = DatabaseManager(sqlite_path=db_file, dsn="postgresql://test:test@127.0.0.1:5432/test")
    with patch.object(mgr.backend, "primary_write_enabled", return_value=True), patch.object(mgr.backend, "write_authoritative", return_value=(False, None, "Connection dropped: EOF")):
        conn = mgr.get_connection()
        conn.execute("INSERT INTO queue (site_id, url) VALUES (?, ?)", ("site2", "http://example.com/2"))
        row = conn._sqlite_cx.execute("SELECT site_id, url FROM queue WHERE site_id='site2'").fetchone()
        assert row is not None
        assert row[1] == "http://example.com/2"
        assert mgr.backend.fallback_count >= 1

def test_dual_write_replication_lag():
    backend = PostgresBackend()
    lag_ms = backend.measure_replication_lag()
    assert isinstance(lag_ms, float)
    assert lag_ms >= 0.0
    assert lag_ms < 1.0

def test_record_parity_verification():
    backend = PostgresBackend()
    sqlite_cx = sqlite3.connect(":memory:")
    sqlite_cx.execute("CREATE TABLE queue (site_id TEXT, url TEXT, PRIMARY KEY (site_id, url))")
    sqlite_cx.execute("INSERT INTO queue VALUES ('s1', 'u1')")
    parity_result = backend.verify_record_parity(sqlite_cx=sqlite_cx, pg_records={"queue": [("s1", "u1")]})
    assert parity_result["ok"] is True
    assert parity_result["parity"] == 1.0
    assert parity_result["mismatches"] == 0

def test_hnsw_recall_parity():
    backend = PostgresBackend()
    recall_stats = backend.verify_hnsw_recall_parity(dim=16, n_samples=100, k=10)
    assert recall_stats["ok"] is True
    assert recall_stats["recall"] >= 0.99
    assert recall_stats["latency_p50_ms"] < 2.0

def test_db_cutover_probe_cli_verify_parity():
    rc, payload = probe.run_probe(verify_parity=True)
    assert rc == 0
    assert payload["replication_lag_ms"] < 1.0
    assert payload["parity"] == 1.0
    assert payload["hnsw_recall"] >= 0.99
    assert payload["status"] == "READY_FOR_CUTOVER"
