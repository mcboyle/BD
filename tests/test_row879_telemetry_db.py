"""Row 879 -- Time-series ingestion rate telemetry in PostgreSQL / partitioned storage.

WHY THIS GATE EXISTS (Row 879, O870f / O882):
Historical ingestion metrics were summarized only in static log files,
preventing multi-month trend analysis. A partitioned time-series table
in bulk_downloader/telemetry_db.py tracks hourly byte volume, success rates,
and duration per domain over time.

WHAT THIS GATE ASSERTS:
1. Time-series partition generation and record insertion.
2. Domain and hourly aggregation queries execute in <5ms.
3. Automatic partition retention policy purges records >90 days.
4. Preserves 0 site logins touched (Rule 21).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

BD_GATE_SCOPE = "module"


def test_telemetry_module_exists():
    """RED assertion: bulk_downloader.telemetry_db must be importable."""
    from bulk_downloader import telemetry_db

    assert hasattr(telemetry_db, "ensure_schema")
    assert hasattr(telemetry_db, "record_ingestion_metric")
    assert hasattr(telemetry_db, "get_domain_aggregates")
    assert hasattr(telemetry_db, "purge_old_partitions")


def test_partition_generation_and_insertion():
    """Acceptance (1): Time-series partition generation and insertion."""
    from bulk_downloader import telemetry_db

    db = telemetry_db.TelemetryDB(dsn="sqlite:///:memory:")
    db.init_schema()

    now = datetime(2026, 9, 19, 20, 15, 0, tzinfo=timezone.utc)
    # 1. Insert metric in current partition
    part_name = db.record_ingestion_metric(
        domain="cdn.example.com",
        bytes_ingested=10485760,  # 10 MB
        success=True,
        duration_seconds=1.25,
        timestamp=now,
    )
    assert part_name is not None
    assert "2026" in part_name

    # 2. Insert metric in an older partition
    past = now - timedelta(days=45)
    past_part = db.record_ingestion_metric(
        domain="cdn.example.com",
        bytes_ingested=5242880,  # 5 MB
        success=False,
        duration_seconds=3.5,
        timestamp=past,
    )
    assert past_part is not None
    assert past_part != part_name

    # Verify both partitions exist
    partitions = db.list_partitions()
    assert part_name in partitions
    assert past_part in partitions


def test_aggregation_queries_execute_under_5ms():
    """Acceptance (2): Aggregation queries execute in <5ms."""
    from bulk_downloader import telemetry_db

    db = telemetry_db.TelemetryDB(dsn="sqlite:///:memory:")
    db.init_schema()

    base_time = datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)
    # Seed 200 hourly metric rows across domains
    for hour in range(100):
        ts = base_time + timedelta(hours=hour)
        db.record_ingestion_metric(
            domain="media.example.org",
            bytes_ingested=1024 * 1024 * (hour + 1),
            success=(hour % 10 != 0),  # 90% success rate
            duration_seconds=0.5 + (hour % 5) * 0.1,
            timestamp=ts,
        )
        db.record_ingestion_metric(
            domain="other.example.net",
            bytes_ingested=512 * 1024,
            success=True,
            duration_seconds=0.2,
            timestamp=ts,
        )

    # Measure execution latency of domain aggregation query
    query_start = base_time
    query_end = base_time + timedelta(hours=50)

    # Warm up
    db.get_domain_aggregates("media.example.org", query_start, query_end)

    durations: list[float] = []
    for _ in range(10):
        t0 = time.perf_counter()
        res = db.get_domain_aggregates("media.example.org", query_start, query_end)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        durations.append(elapsed_ms)

    avg_latency_ms = sum(durations) / len(durations)
    assert avg_latency_ms < 5.0, f"Query average latency {avg_latency_ms:.2f}ms exceeded 5ms limit"

    # Verify result accuracy
    assert res["domain"] == "media.example.org"
    assert res["total_bytes"] > 0
    assert 0.85 <= res["success_rate"] <= 0.95
    assert res["avg_duration_s"] > 0


def test_automatic_partition_retention_purges_records_over_90_days():
    """Acceptance (3): Automatic partition retention policy purges records >90 days."""
    from bulk_downloader import telemetry_db

    db = telemetry_db.TelemetryDB(dsn="sqlite:///:memory:")
    db.init_schema()

    now = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)

    # Partition A: 120 days old (>90 days)
    t_old = now - timedelta(days=120)
    old_part = db.record_ingestion_metric(
        domain="archive.example.org",
        bytes_ingested=1000,
        success=True,
        duration_seconds=1.0,
        timestamp=t_old,
    )

    # Partition B: 30 days old (<90 days)
    t_recent = now - timedelta(days=30)
    recent_part = db.record_ingestion_metric(
        domain="archive.example.org",
        bytes_ingested=2000,
        success=True,
        duration_seconds=1.0,
        timestamp=t_recent,
    )

    # Partition C: today (<90 days)
    today_part = db.record_ingestion_metric(
        domain="archive.example.org",
        bytes_ingested=3000,
        success=True,
        duration_seconds=1.0,
        timestamp=now,
    )

    assert old_part in db.list_partitions()
    assert recent_part in db.list_partitions()
    assert today_part in db.list_partitions()

    # Run retention purge
    purged = db.purge_old_partitions(retention_days=90, now=now)

    assert old_part in purged, f"Expected {old_part} to be purged"
    remaining = db.list_partitions()
    assert old_part not in remaining, f"Old partition {old_part} still present after purge"
    assert recent_part in remaining, "Recent partition should be retained"
    assert today_part in remaining, "Current partition should be retained"


def test_negative_control_nonexistent_domain_aggregate():
    """Negative control: query for unknown domain returns 0 values, never raises."""
    from bulk_downloader import telemetry_db

    db = telemetry_db.TelemetryDB(dsn="sqlite:///:memory:")
    db.init_schema()

    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    t1 = datetime(2026, 9, 2, tzinfo=timezone.utc)

    res = db.get_domain_aggregates("nonexistent-domain.xyz", t0, t1)
    assert res["domain"] == "nonexistent-domain.xyz"
    assert res["total_bytes"] == 0
    assert res["success_count"] == 0
    assert res["failure_count"] == 0
    assert res["success_rate"] == 0.0
    assert res["avg_duration_s"] == 0.0


def test_postgres_branch_is_hermetic_and_binds_utc_bounds(monkeypatch):
    """row879 fixer (E2): the former live test connected to an ambient
    127.0.0.1:5432 service and pytest.skip()ped when absent (FLEET_RULE 46:
    fail closed, never skip). The Postgres branch is now driven through a
    fake connection: no socket, no credentials, and it asserts what the live
    test could not -- that the bound window reaches the server in UTC."""
    from bulk_downloader import telemetry_db

    executed = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            executed.append((" ".join(sql.split()), params))

        def fetchone(self):
            return (2048000, 1, 0, 0.8)

    class Conn:
        closed = False

        def cursor(self):
            return Cursor()

    db = telemetry_db.TelemetryDB(dsn="postgresql://fixture.invalid/telemetry")
    assert db.is_pg
    monkeypatch.setattr(db, "_get_connection", lambda: Conn())

    plus4 = timezone(timedelta(hours=4))
    start = datetime(2026, 9, 19, 15, 0, tzinfo=plus4)   # 11:00 UTC
    end = datetime(2026, 9, 19, 17, 0, tzinfo=plus4)     # 13:00 UTC
    res = db.get_domain_aggregates("pg.example.com", start, end)

    assert res["total_bytes"] == 2048000
    assert res["success_rate"] == 1.0
    sql, params = executed[-1]
    assert "bucket_hour >= %s AND bucket_hour < %s" in sql
    _domain, bound_start, bound_end = params
    assert bound_start == datetime(2026, 9, 19, 11, 0, tzinfo=timezone.utc)
    assert bound_end == datetime(2026, 9, 19, 13, 0, tzinfo=timezone.utc)
    assert bound_start.utcoffset() == timedelta(0) and bound_end.utcoffset() == timedelta(0)


def test_sqlite_aggregates_convert_non_utc_bounds(tmp_path):
    """row879 fixer (E1): a 12:00 UTC record must be FOUND by an 11:00-13:00
    UTC window expressed in +04:00 (15:00-17:00+04:00) and NOT found by a
    13:00-15:00 UTC window expressed in -05:00 (08:00-10:00-05:00)."""
    from bulk_downloader import telemetry_db

    db = telemetry_db.TelemetryDB(dsn=f"sqlite:///{tmp_path / 't.db'}")
    db.init_schema()
    noon = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
    db.record_ingestion_metric(domain="tz.example.com", bytes_ingested=100,
                               success=True, duration_seconds=0.1, timestamp=noon)

    plus4 = timezone(timedelta(hours=4))
    hit = db.get_domain_aggregates("tz.example.com",
                                   datetime(2026, 9, 19, 15, 0, tzinfo=plus4),
                                   datetime(2026, 9, 19, 17, 0, tzinfo=plus4))
    assert hit["total_bytes"] == 100

    minus5 = timezone(timedelta(hours=-5))
    miss = db.get_domain_aggregates("tz.example.com",
                                    datetime(2026, 9, 19, 8, 0, tzinfo=minus5),
                                    datetime(2026, 9, 19, 10, 0, tzinfo=minus5))
    assert miss["total_bytes"] == 0
    db.close()

