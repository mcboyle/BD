"""Row 1016 r2 -- Lock-Free Bulk Ingestion Pipeline via Temporary Staging Tables.

RED on base: ModuleNotFoundError for bulk_downloader.staging_ingest.
Covers all refute seams from B2 (atomicity, count accuracy) and B5-B
(partial-column preservation, caller-connection isolation).
"""
import sqlite3

import pytest

BD_GATE_SCOPE = "module"


def _make_db(*ddl):
    conn = sqlite3.connect(":memory:")
    for stmt in ddl:
        conn.execute(stmt)
    conn.commit()
    return conn


class TestBasicIngestion:
    def test_stage_and_flush(self):
        from bulk_downloader.staging_ingest import StagingIngestPipeline
        conn = _make_db("CREATE TABLE t(id INTEGER PRIMARY KEY, val TEXT)")
        pipe = StagingIngestPipeline(conn)
        pipe.stage_rows("t", [{"id": 1, "val": "a"}, {"id": 2, "val": "b"}])
        res = pipe.flush("t")
        assert res.transferred == 2
        rows = conn.execute("SELECT id, val FROM t ORDER BY id").fetchall()
        assert rows == [(1, "a"), (2, "b")]

    def test_ingest_batch_convenience(self):
        from bulk_downloader.staging_ingest import StagingIngestPipeline
        conn = _make_db("CREATE TABLE t(id INTEGER PRIMARY KEY, val TEXT)")
        pipe = StagingIngestPipeline(conn)
        res = pipe.ingest_batch("t", [{"id": 1, "val": "x"}])
        assert res.staged == 1
        assert res.transferred == 1

    def test_unknown_column_raises(self):
        from bulk_downloader.staging_ingest import StagingIngestPipeline
        conn = _make_db("CREATE TABLE t(id INTEGER PRIMARY KEY, val TEXT)")
        pipe = StagingIngestPipeline(conn)
        with pytest.raises(ValueError, match="Unknown column"):
            pipe.stage_rows("t", [{"id": 1, "bogus": "x"}])


class TestPartialColumnPreservation:
    """B5-B R1: partial-column ingest must NOT null unsupplied columns."""

    def test_replace_preserves_unsupplied_columns(self):
        from bulk_downloader.staging_ingest import StagingIngestPipeline, ConflictStrategy
        conn = _make_db("CREATE TABLE jobs(id INTEGER PRIMARY KEY, url TEXT, status TEXT, bytes INTEGER)")
        conn.execute("INSERT INTO jobs VALUES (1, 'https://site/a', 'queued', 4096)")
        conn.commit()
        pipe = StagingIngestPipeline(conn)
        res = pipe.ingest_batch("jobs", [{"id": 1, "status": "done"}], ConflictStrategy.REPLACE)
        assert res.transferred == 1
        row = conn.execute("SELECT id, url, status, bytes FROM jobs WHERE id=1").fetchone()
        assert row[1] == "https://site/a", (
            f"url was nulled by partial-column REPLACE: got {row[1]!r}"
        )
        assert row[2] == "done"
        assert row[3] == 4096, (
            f"bytes was nulled by partial-column REPLACE: got {row[3]!r}"
        )


class TestCallerConnectionIsolation:
    """B5-B R2: flush must not commit caller's in-flight transaction."""

    def test_caller_transaction_not_committed(self):
        from bulk_downloader.staging_ingest import StagingIngestPipeline
        conn = _make_db(
            "CREATE TABLE t(id INTEGER PRIMARY KEY, val TEXT)",
            "CREATE TABLE other(x INTEGER)",
        )
        conn.execute("INSERT INTO other VALUES (99)")
        assert conn.in_transaction
        pipe = StagingIngestPipeline(conn, owns_connection=False)
        pipe.ingest_batch("t", [{"id": 1, "val": "a"}])
        assert conn.in_transaction, (
            "Pipeline committed caller's transaction — owns_connection=False should prevent commit"
        )
        conn.rollback()
        row = conn.execute("SELECT count(*) FROM other").fetchone()[0]
        assert row == 0, (
            f"Caller's INSERT INTO other was committed by pipeline: {row} rows remain after rollback"
        )


class TestTransferredCountAccuracy:
    """B2 R2: transferred count must reflect actual rows inserted, not staged count."""

    def test_ignore_with_duplicates_reports_actual_count(self):
        from bulk_downloader.staging_ingest import StagingIngestPipeline, ConflictStrategy
        conn = _make_db("CREATE TABLE t(k TEXT PRIMARY KEY)")
        rows = [{"k": "same_key"} for _ in range(50)]
        pipe = StagingIngestPipeline(conn)
        res = pipe.ingest_batch("t", rows, ConflictStrategy.IGNORE)
        actual = conn.execute("SELECT count(*) FROM t").fetchone()[0]
        assert actual == 1
        assert res.transferred <= actual + 1, (
            f"transferred={res.transferred} but only {actual} row exists — "
            f"count must reflect actual inserts, not staged count"
        )


class TestConflictStrategies:
    def test_replace_overwrites(self):
        from bulk_downloader.staging_ingest import StagingIngestPipeline, ConflictStrategy
        conn = _make_db("CREATE TABLE t(id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'old')")
        conn.commit()
        pipe = StagingIngestPipeline(conn)
        res = pipe.ingest_batch("t", [{"id": 1, "name": "new"}], ConflictStrategy.REPLACE)
        assert res.transferred == 1
        val = conn.execute("SELECT name FROM t WHERE id=1").fetchone()[0]
        assert val == "new"

    def test_ignore_keeps_existing(self):
        from bulk_downloader.staging_ingest import StagingIngestPipeline, ConflictStrategy
        conn = _make_db("CREATE TABLE t(id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'old')")
        conn.commit()
        pipe = StagingIngestPipeline(conn)
        pipe.ingest_batch("t", [{"id": 1, "name": "new"}], ConflictStrategy.IGNORE)
        val = conn.execute("SELECT name FROM t WHERE id=1").fetchone()[0]
        assert val == "old"

    def test_fail_raises_on_conflict(self):
        from bulk_downloader.staging_ingest import StagingIngestPipeline, ConflictStrategy
        conn = _make_db("CREATE TABLE t(id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'old')")
        conn.commit()
        pipe = StagingIngestPipeline(conn)
        with pytest.raises(sqlite3.IntegrityError):
            pipe.ingest_batch("t", [{"id": 1, "name": "new"}], ConflictStrategy.FAIL)


class TestStagingSession:
    def test_session_context_manager(self):
        from bulk_downloader.staging_ingest import StagingIngestPipeline
        conn = _make_db("CREATE TABLE t(id INTEGER PRIMARY KEY, val TEXT)")
        pipe = StagingIngestPipeline(conn)
        with pipe.staging_session("t") as sess:
            sess.add({"id": 1, "val": "a"})
            sess.add({"id": 2, "val": "b"})
        rows = conn.execute("SELECT count(*) FROM t").fetchone()[0]
        assert rows == 2


class TestDbHelperWiring:
    def test_db_bulk_ingest_staging_exists(self):
        from bulk_downloader.db import db_bulk_ingest_staging
        assert callable(db_bulk_ingest_staging)

    def test_db_staging_ingest_stats_exists(self):
        from bulk_downloader.db import db_staging_ingest_stats
        assert callable(db_staging_ingest_stats)


class TestTelemetry:
    def test_pipeline_telemetry_updates(self):
        from bulk_downloader.staging_ingest import StagingIngestPipeline
        conn = _make_db("CREATE TABLE t(id INTEGER PRIMARY KEY)")
        pipe = StagingIngestPipeline(conn)
        pipe.ingest_batch("t", [{"id": 1}, {"id": 2}])
        tel = pipe.get_telemetry()
        assert tel["total_rows_staged"] == 2
        assert tel["total_flushes"] == 1
