"""Row 875: PGVECTOR-HNSW-INDEXING-FOR-SUB-MILLISECOND-SIMILARITY-SEARCH.

Covers the three register row acceptance points:
  (1) HNSW index creation on vector column (with m=16, ef_construction=64)
  (2) Query latency <2ms over 50,000 vectors
  (3) Recall parity with flat index >99%

Target: PostgreSQL 16 on 127.0.0.1:5432 with pgvector extension.
"""
from __future__ import annotations

import importlib
import os
import random
import time
import types
from typing import Sequence

import pytest

try:
    pv = importlib.import_module("bulk_downloader.pg_vector")
except ImportError:
    # Behavioral fallback for RED verification on unmodified base
    class _StubPGVector:
        DEFAULT_M = 16
        DEFAULT_EF_CONSTRUCTION = 64
        DEFAULT_EF_SEARCH = 100

        @staticmethod
        def pg_vector_dsn():
            return os.environ.get("PGVECTOR_DSN") or os.environ.get("MOD3_PG_DSN") or None

        @staticmethod
        def connect(*args, **kwargs):
            return None

        @staticmethod
        def ensure_extension(conn):
            return False

        @staticmethod
        def ensure_schema(conn, schema="row875vec"):
            return False

        @staticmethod
        def drop_schema(conn, schema="row875vec"):
            return False

        @staticmethod
        def create_vector_table(conn, table, dim, schema="row875vec"):
            return False

        @staticmethod
        def create_hnsw_index(conn, table, schema="row875vec", m=16, ef_construction=64, index_name=None):
            return False

        @staticmethod
        def index_exists(conn, table, schema="row875vec", index_name=None):
            return False

        @staticmethod
        def insert_vectors(conn, table, vectors, schema="row875vec"):
            return 0

        @staticmethod
        def benchmark(conn, table, queries, k=10, schema="row875vec", ef_search=100):
            return None

    pv = _StubPGVector()

BD_GATE_SCOPE = "module"

SCHEMA = "row875_test_schema"
TABLE = "bench50k"
DIM = 16
N_VECTORS = 50000


@pytest.fixture(scope="module")
def pg_conn():
    """Live connection to Postgres on 127.0.0.1:5432."""
    dsn = pv.pg_vector_dsn()
    conn = pv.connect(dsn)
    if conn is not None:
        pv.ensure_extension(conn)
        pv.drop_schema(conn, schema=SCHEMA)
        pv.ensure_schema(conn, schema=SCHEMA)
    yield conn
    if conn is not None:
        try:
            pv.drop_schema(conn, schema=SCHEMA)
            conn.close()
        except Exception:
            pass


class TestIdentifierAndValidation:
    """Security and argument validation checks."""

    def test_ident_validation_rejects_sql_injection(self):
        with pytest.raises((ValueError, AttributeError)):
            pv.create_vector_table(None, 'vectors"; DROP TABLE history; --', dim=DIM, schema=SCHEMA)

    def test_dim_validation_rejects_invalid_values(self):
        with pytest.raises((ValueError, AttributeError)):
            pv.create_vector_table(None, TABLE, dim=-1, schema=SCHEMA)
        with pytest.raises((ValueError, AttributeError)):
            pv.create_vector_table(None, TABLE, dim=0, schema=SCHEMA)
        with pytest.raises((ValueError, AttributeError)):
            pv.create_vector_table(None, TABLE, dim=True, schema=SCHEMA)


class TestFailOpenConnection:
    """Fail-open connection contract."""

    def test_unreachable_host_returns_none(self):
        assert pv.connect("postgresql://nobody@127.0.0.1:1/nonexistent") is None


# row875-latency-isolation: test_3 times 20 queries (median < 2 ms) and failed only under the band's -n 12
# load. The band runs band_isolated tests alone after its xdist pass. The whole class carries the marker:
# test_3 reads the table and index test_1/test_2 build in the module-scoped pg_conn schema, which is
# dropped at fixture setup, so test_3 alone in the serial pass would have no table.
@pytest.mark.band_isolated
class TestRow875Acceptance:
    """Full 50,000 vector benchmark and HNSW verification."""

    def test_1_create_vector_table_and_insert_50k(self, pg_conn):
        created = pv.create_vector_table(pg_conn, TABLE, dim=DIM, schema=SCHEMA)
        assert created is True, "Vector table creation must succeed"

        # Generate deterministic 50,000 vectors
        rng = random.Random(42)
        vectors = [[round(rng.random(), 4) for _ in range(DIM)] for _ in range(N_VECTORS)]
        inserted = pv.insert_vectors(pg_conn, TABLE, vectors, schema=SCHEMA)
        assert inserted == N_VECTORS, f"Expected {N_VECTORS} inserted, got {inserted}"

    def test_2_hnsw_index_creation(self, pg_conn):
        # Verify index does not exist before creation
        assert pv.index_exists(pg_conn, TABLE, schema=SCHEMA) is False

        # Acceptance point 1: HNSW index creation on vector column with m=16, ef_construction=64
        t0 = time.time()
        created = pv.create_hnsw_index(
            pg_conn,
            TABLE,
            schema=SCHEMA,
            m=pv.DEFAULT_M,
            ef_construction=pv.DEFAULT_EF_CONSTRUCTION,
        )
        build_time = time.time() - t0
        assert created is True, "HNSW index creation must succeed"
        assert pv.index_exists(pg_conn, TABLE, schema=SCHEMA) is True, "Index must exist in pg_indexes"

    def test_3_query_latency_and_recall_parity_50k(self, pg_conn):
        # Generate 20 test queries
        rng = random.Random(999)
        queries = [[round(rng.random(), 4) for _ in range(DIM)] for _ in range(20)]
        k = 10

        result = pv.benchmark(pg_conn, TABLE, queries=queries, k=k, schema=SCHEMA, ef_search=100)
        assert result is not None, "Benchmark result must not be None"
        assert result.n_queries == 20
        assert result.k == 10

        print(f"ROW875_LATENCY median={result.median_latency_ms:.3f}ms "
              f"mean={result.mean_latency_ms:.3f}ms p99={result.p99_latency_ms:.3f}ms "
              f"recall={result.recall_at_k:.4f}")
        # Acceptance point 2: query latency < 2ms over 50,000 vectors, on the median.
        # The mean carried host stalls: the stale gate measured mean 13.326ms with
        # p99 37.6ms against a 0.25ms network floor (PM ruling 0745Z, option 1).
        assert result.median_latency_ms < 2.0, (
            f"Median latency {result.median_latency_ms:.3f}ms must be < 2ms over 50,000 vectors "
            f"(mean {result.mean_latency_ms:.3f}ms, p99 {result.p99_latency_ms:.3f}ms)"
        )

        # Acceptance point 3: recall parity with flat index > 99%
        assert result.recall_at_k > 0.99, (
            f"Recall {result.recall_at_k:.4f} must be > 0.99 (99% parity with exact flat scan)"
        )


class _BenchCursor:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None, **kwargs):
        pass

    def fetchall(self):
        return [(1,), (2,)]


class _BenchConn:
    def cursor(self):
        return _BenchCursor()

    def commit(self):
        pass

    def rollback(self):
        pass


@pytest.mark.parametrize("latencies_ms,median_ms", [
    ([0.5] * 19 + [40.0], 0.5),           # one stalled query among 20 (even count)
    ([3.0, 1.0, 2.0], 2.0),               # odd count: the middle value
    ([4.0, 1.0, 3.0, 2.0], 2.5),          # even count: mean of the two middle values
])
def test_benchmark_reports_the_median_beside_mean_and_p99(monkeypatch, latencies_ms, median_ms):
    ticks = iter([t for ms in latencies_ms for t in (0.0, ms / 1000.0)])
    # The benchmark's own clock only: other threads in the worker keep the real one.
    clock = types.ModuleType("time")
    clock.__dict__.update(vars(pv.time))
    clock.perf_counter = lambda: next(ticks)
    monkeypatch.setattr(pv, "time", clock)
    queries = [[0.1, 0.2]] * len(latencies_ms)
    result = pv.benchmark(_BenchConn(), "vectors", queries, k=2)
    ordered = sorted(latencies_ms)
    assert result.median_latency_ms == pytest.approx(median_ms)
    assert result.mean_latency_ms == pytest.approx(sum(ordered) / len(ordered))
    assert result.p99_latency_ms == pytest.approx(ordered[min(len(ordered) - 1, int(len(ordered) * 0.99))])
