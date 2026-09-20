"""Cut 877: database maintenance and index bloat compactor contract tests."""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from bulk_downloader import db_maintenance


def test_index_bloat_detection_accurately_flags_fragmented_indexes():
    """Verify that index bloat detection accurately flags fragmented indexes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_bloat.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE queue_items (id INTEGER PRIMARY KEY, site_id TEXT, url TEXT, payload TEXT)")
        conn.execute("CREATE INDEX idx_queue_site_url ON queue_items(site_id, url)")

        # Populate with data
        items = [(f"site_{i % 5}", f"https://example.com/item_{i}", "x" * 200) for i in range(1000)]
        conn.executemany("INSERT INTO queue_items (site_id, url, payload) VALUES (?, ?, ?)", items)
        conn.commit()

        # Check clean index initially
        initial_reports = db_maintenance.detect_index_bloat(conn, bloat_threshold=0.3)
        assert any(r["index_name"] == "idx_queue_site_url" for r in initial_reports)
        idx_initial = next(r for r in initial_reports if r["index_name"] == "idx_queue_site_url")
        assert not idx_initial["is_bloated"]

        # Delete 80% of data to produce index fragmentation / free pages
        conn.execute("DELETE FROM queue_items WHERE id % 5 != 0")
        conn.commit()

        # Bloat detection should now flag fragmented index
        bloat_reports = db_maintenance.detect_index_bloat(conn, bloat_threshold=0.3)
        idx_report = next(r for r in bloat_reports if r["index_name"] == "idx_queue_site_url")
        assert idx_report["is_bloated"] is True
        assert idx_report["bloat_ratio"] >= 0.3
        conn.close()


def test_non_blocking_maintenance_runs_without_write_locks():
    """Verify that maintenance commands run concurrently without acquiring write/exclusive locks."""
    # 1. PostgreSQL dialect verification: must use REINDEX CONCURRENTLY and CHECKPOINT
    mock_pg_cursor = MagicMock()
    db_maintenance.run_postgres_maintenance(mock_pg_cursor, index_names=["idx_queue_site_url"])

    executed_queries = [call[0][0].strip().upper() for call in mock_pg_cursor.execute.call_args_list]
    assert any("REINDEX INDEX CONCURRENTLY" in q for q in executed_queries), f"Expected REINDEX CONCURRENTLY, got: {executed_queries}"
    assert any("CHECKPOINT" in q for q in executed_queries), f"Expected CHECKPOINT, got: {executed_queries}"
    # Verify no exclusive write lock requested
    for q in executed_queries:
        assert "ACCESS EXCLUSIVE" not in q

    # 2. SQLite non-blocking concurrency: verify WAL checkpoint runs in non-blocking mode (PASSIVE/TRUNCATE)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_concurrent.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'val')")
        conn.commit()

        # Run maintenance
        result = db_maintenance.run_sqlite_maintenance(conn, checkpoint_mode="TRUNCATE")
        assert result["ok"] is True
        assert result["checkpoint"]["busy"] == 0
        conn.close()


def test_database_size_reduction_verified_post_compaction():
    """Verify that compaction reduces the database and WAL storage size."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_reduction.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE churn (id INTEGER PRIMARY KEY, data TEXT)")
        conn.execute("CREATE INDEX idx_churn_data ON churn(data)")

        # High churn: insert 2000 rows, then delete 1800
        payload = "x" * 1024
        conn.executemany("INSERT INTO churn (data) VALUES (?)", [(f"{payload}_{i}",) for i in range(2000)])
        conn.commit()

        conn.execute("DELETE FROM churn WHERE id > 200")
        conn.commit()

        # Checkpoint WAL so pages reside in main file or WAL
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        pre_compaction_bytes = db_path.stat().st_size

        # Run compactor
        result = db_maintenance.compact_database(conn, db_path=db_path)
        assert result["ok"] is True

        post_compaction_bytes = db_path.stat().st_size
        assert post_compaction_bytes < pre_compaction_bytes, (
            f"Expected size reduction: pre={pre_compaction_bytes}B, post={post_compaction_bytes}B"
        )
        conn.close()


# ---- fixer (O928) controls for the correctness REFUTE E1-E4 --------------

def _wal_db(tmpdir):
    db_path = Path(tmpdir) / "m.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE q (id INTEGER PRIMARY KEY, site_id TEXT, url TEXT, payload TEXT)")
    conn.execute("CREATE INDEX idx_q ON q(site_id, url)")
    conn.executemany("INSERT INTO q (site_id, url, payload) VALUES (?, ?, ?)",
                     [(f"site_{i % 5}", f"https://example.com/item_{i}", "x" * 200) for i in range(1000)])
    conn.commit()
    return db_path, conn


def test_e1_unrelated_table_deletion_does_not_bloat_a_healthy_index():
    with tempfile.TemporaryDirectory() as tmpdir:
        _, conn = _wal_db(tmpdir)
        before = next(r for r in db_maintenance.detect_index_bloat(conn) if r["index_name"] == "idx_q")
        assert before["measured"] is True and not before["is_bloated"]
        conn.execute("CREATE TABLE other (id INTEGER PRIMARY KEY, blob TEXT)")
        conn.executemany("INSERT INTO other VALUES (?, ?)", [(i, "y" * 500) for i in range(3000)])
        conn.commit()
        conn.execute("DELETE FROM other")
        conn.commit()
        after = next(r for r in db_maintenance.detect_index_bloat(conn) if r["index_name"] == "idx_q")
        assert after["bloat_ratio"] == before["bloat_ratio"]
        assert after["unused_bytes"] == before["unused_bytes"] and after["payload_bytes"] == before["payload_bytes"]
        assert not after["is_bloated"]
        assert after["database_freelist_ratio"] > before["database_freelist_ratio"]  # the FILE has free space; the index does not
        conn.close()


def test_e2_active_writer_defers_reindex_instead_of_raising():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path, conn = _wal_db(tmpdir)
        writer = sqlite3.connect(str(db_path), isolation_level=None)
        writer.execute("BEGIN IMMEDIATE")  # holds the write lock
        writer.execute("INSERT INTO q (site_id, url, payload) VALUES ('w', 'u', 'p')")
        try:
            result = db_maintenance.run_sqlite_maintenance(conn, checkpoint_mode="PASSIVE", index_names=["idx_q"])
        finally:
            writer.execute("COMMIT")
            writer.close()
        assert result["ok"] is True, result
        assert result["reindexed"] == []
        assert [d["index"] for d in result["deferred"]] == ["idx_q"]
        # idle window: the same call now reindexes
        result = db_maintenance.run_sqlite_maintenance(conn, checkpoint_mode="PASSIVE", index_names=["idx_q"])
        assert result["reindexed"] == ["idx_q"] and result["deferred"] == []
        conn.close()


def test_e3_postgres_connection_shape_gets_backend_aware_statistics():
    """A psycopg-shaped connection (has .execute, no .pg_conn) must not receive
    sqlite_master SQL; bloat is measured via pgstatindex or reported unmeasured."""
    class Cur:
        def __init__(self, rows): self.rows = rows
        def fetchall(self): return self.rows

    class PgConn:  # not an sqlite3.Connection
        def __init__(self, stats_ok):
            self.sql, self.stats_ok, self.rolled_back = [], stats_ok, False
        def execute(self, sql):
            self.sql.append(sql)
            if "pgstatindex" in sql:
                if not self.stats_ok:
                    raise RuntimeError('function pgstatindex(oid) does not exist')
                return Cur([("public", "queue_items", "idx_queue_site_url", 8192 * 40, 62.5, 10.0)])
            return Cur([("public", "queue_items", "idx_queue_site_url", 8192 * 40)])
        def rollback(self): self.rolled_back = True

    pg = PgConn(stats_ok=True)
    reports = db_maintenance.detect_index_bloat(pg, bloat_threshold=0.3)
    assert not any("sqlite_master" in s for s in pg.sql)
    assert reports == [{
        "index_name": "idx_queue_site_url", "table_name": "queue_items", "schema": "public",
        "bloat_ratio": 0.375, "is_bloated": True, "measured": True, "index_bytes": 8192 * 40,
        "leaf_fragmentation": 10.0,
    }]
    pg = PgConn(stats_ok=False)
    reports = db_maintenance.detect_index_bloat(pg, bloat_threshold=0.3)
    assert pg.rolled_back
    assert reports[0]["measured"] is False and reports[0]["bloat_ratio"] is None and reports[0]["is_bloated"] is False
    assert reports[0]["index_bytes"] == 8192 * 40
    assert db_maintenance.is_sqlite_connection(sqlite3.connect(":memory:")) is True
    assert db_maintenance.is_sqlite_connection(pg) is False


def test_e4_identifiers_with_spaces_are_quoted():
    with tempfile.TemporaryDirectory() as tmpdir:
        _, conn = _wal_db(tmpdir)
        conn.execute('CREATE INDEX "idx with space" ON q(url)')
        conn.commit()
        result = db_maintenance.run_sqlite_maintenance(conn, index_names=["idx with space"])
        assert result["ok"] is True and result["reindexed"] == ["idx with space"], result
        conn.close()
    cur = MagicMock()
    db_maintenance.run_postgres_maintenance(cur, index_names=['idx with space', 'we"ird'])
    sqls = [c[0][0] for c in cur.execute.call_args_list]
    assert 'REINDEX INDEX CONCURRENTLY "idx with space"' in sqls
    assert 'REINDEX INDEX CONCURRENTLY "we""ird"' in sqls
    assert db_maintenance.quote_ident('a"b') == '"a""b"'
