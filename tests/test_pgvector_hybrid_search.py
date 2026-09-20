"""Row 855: PGVECTOR-NATIVE-HYBRID-SEMANTIC-SEARCH-IN-POSTGRES.

Covers the acceptance points:
  (1) Cosine distance indexing (HNSW with vector_cosine_ops) and combined full-text
      (tsvector/tsquery/ts_rank) + vector proximity query in a single SQL statement.
  (2) Native SQL query execution under 10ms.
  (3) Zero external egress (local PostgreSQL 127.0.0.1:5432 only).
  (4) 0 site logins touched (Fleet Rule 21).

Target: PostgreSQL 16 on 127.0.0.1:5432 with pgvector extension.
"""
from __future__ import annotations

import importlib
import os
import random
import re
import time
from typing import Sequence

import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

try:
    ds = importlib.import_module("bulk_downloader.db_search")
except ImportError:
    # Behavioral fallback for RED verification on unmodified base
    class _StubDBSearch:
        DEFAULT_M = 16
        DEFAULT_EF_CONSTRUCTION = 64
        DEFAULT_EF_SEARCH = 100

        @staticmethod
        def db_search_dsn():
            return "postgresql://postgres:postgres@127.0.0.1:5432/ai_mesh"

        @staticmethod
        def connect(*args, **kwargs):
            return None

        @staticmethod
        def ensure_extension(conn):
            return False

        @staticmethod
        def ensure_schema(conn, schema="row855_test_schema"):
            return False

        @staticmethod
        def drop_schema(conn, schema="row855_test_schema"):
            return False

        @staticmethod
        def create_hybrid_table(conn, table, dim, schema="row855_test_schema"):
            return False

        @staticmethod
        def create_indexes(conn, table, schema="row855_test_schema", **kwargs):
            return False

        @staticmethod
        def index_exists(conn, table, schema="row855_test_schema", index_name=None):
            return False

        @staticmethod
        def insert_documents(conn, table, documents, schema="row855_test_schema"):
            return 0

        @staticmethod
        def hybrid_search(conn, table, query_text, query_vector, k=10, **kwargs):
            return []

    ds = _StubDBSearch()

BD_GATE_SCOPE = "module"

SCHEMA = "row855_test_schema"
TABLE = "hybrid_docs"
DIM = 16
N_DOCS = 250

# Test-owned copies of the module's DSN defaults: the skip decision below must not
# depend on the module under test (on base the stub's connect() is None, which must
# FAIL the live tests, not skip them).
_DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:5432/ai_mesh"
_FALLBACK_DSN = "postgresql://postgres:postgres@127.0.0.1:5432/postgres"


def _candidate_dsns() -> list[str]:
    """Operator DSNs (PGVECTOR_DSN / MOD3_PG_DSN) are authoritative when set: an explicit
    unreachable override must skip, never be silently replaced by the default cluster."""
    env = [(os.environ.get(k) or "").strip() for k in ("PGVECTOR_DSN", "MOD3_PG_DSN")]
    env = [d for d in env if d]
    return env if env else [_DEFAULT_DSN, _FALLBACK_DSN]


def _probe_pgvector_dsn() -> str | None:
    """Independent psycopg probe: first DSN that connects AND can provide pgvector, else None."""
    try:
        import psycopg
    except Exception:
        return None
    for dsn in _candidate_dsns():
        try:
            with psycopg.connect(dsn, connect_timeout=5) as c:
                with c.cursor() as cur:
                    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                    cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
                    if cur.fetchone() is None:
                        continue
                c.commit()
            return dsn
        except Exception:
            continue
    return None


@pytest.fixture(scope="module")
def pg_conn():
    """Live psycopg connection to Postgres on 127.0.0.1:5432 (independent of the module).

    Skips only when a genuinely absent Postgres/pgvector makes the probe fail; with a
    live cluster the tests below run and must FAIL on base for the intended reason.
    """
    dsn = _probe_pgvector_dsn()
    if dsn is None:
        pytest.skip("PostgreSQL on 127.0.0.1:5432 unavailable or pgvector not loadable")
    import psycopg

    conn = psycopg.connect(dsn, connect_timeout=5)
    ds.ensure_extension(conn)
    ds.drop_schema(conn, schema=SCHEMA)
    ds.ensure_schema(conn, schema=SCHEMA)
    yield conn
    try:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        conn.commit()
    except Exception:
        pass
    try:
        conn.close()
    except Exception:
        pass


class TestIdentifierAndValidation:
    """Security and argument validation checks."""

    def test_ident_validation_rejects_sql_injection(self):
        with pytest.raises((ValueError, AttributeError)):
            ds.create_hybrid_table(None, 'docs"; DROP TABLE history; --', dim=DIM, schema=SCHEMA)
        with pytest.raises((ValueError, AttributeError)):
            ds.create_hybrid_table(None, TABLE, dim=DIM, schema='public"; DROP TABLE history; --')

    def test_dim_validation_rejects_invalid_values(self):
        with pytest.raises((ValueError, AttributeError)):
            ds.create_hybrid_table(None, TABLE, dim=-1, schema=SCHEMA)
        with pytest.raises((ValueError, AttributeError)):
            ds.create_hybrid_table(None, TABLE, dim=0, schema=SCHEMA)
        with pytest.raises((ValueError, AttributeError)):
            ds.create_hybrid_table(None, TABLE, dim=True, schema=SCHEMA)


class TestFailOpenConnection:
    """Fail-open connection contract."""

    def test_unreachable_host_returns_none(self):
        assert ds.connect("postgresql://nobody@127.0.0.1:1/nonexistent") is None


class TestZeroExternalEgress:
    """Zero external egress and zero site login safety guarantees."""

    def test_zero_external_egress(self, pg_conn):
        assert pg_conn is not None, "PostgreSQL connection on 127.0.0.1:5432 must be live"
        # Verify host address is strictly loopback
        host = getattr(pg_conn.info, "host", None) or getattr(pg_conn.pgconn, "host", None)
        assert host in ("127.0.0.1", "localhost", "::1", None), f"External egress prohibited: {host}"

    def test_zero_site_logins_touched(self):
        # Fleet Rule 21 invariant: local pgvector search never touches site logins or
        # external credentials -- measured on the module source, not asserted by fiat.
        src = (ROOT / "bulk_downloader" / "db_search.py").read_text(encoding="utf-8")
        assert re.search(r"(?im)^\s*(from|import)\s+\S*(login|auth|cookie|credential)", src) is None
        assert re.search(r"(?i)(cookie_file|login_url|password|site_login)", src) is None


class TestPgvectorHybridSearchAcceptance:
    """Full hybrid search acceptance: indexing, single SQL execution, latency < 10ms."""

    def test_1_create_hybrid_table_and_insert_docs(self, pg_conn):
        created = ds.create_hybrid_table(pg_conn, TABLE, dim=DIM, schema=SCHEMA)
        assert created is True, "Hybrid table creation must succeed"

        topics = [
            ("PostgreSQL pgvector", "HNSW cosine distance index speeds up high dimensional vector similarity searches in postgres"),
            ("Python search engine", "Hybrid BM25 and vector search combines text rankings with dense neural embeddings"),
            ("Database transaction engine", "PostgreSQL guarantees robust ACID transactions and foreign key integrity"),
            ("Distributed cluster storage", "Distributed object storage with shard distribution across hub and satellite nodes"),
            ("Vector quantization", "Scalar quantization and product quantization reduce RAM overhead for vector embeddings"),
        ]

        rng = random.Random(855)
        docs = []
        for i in range(N_DOCS):
            topic_idx = i % len(topics)
            title = f"{topics[topic_idx][0]} document {i}"
            content = f"{topics[topic_idx][1]} with metadata sequence {i}"
            vec = [round(rng.random(), 4) for _ in range(DIM)]
            docs.append({"title": title, "content": content, "embedding": vec})

        inserted = ds.insert_documents(pg_conn, TABLE, docs, schema=SCHEMA)
        # Exact-count assertion proves fixture built the shape (nonzero) before any verdict
        assert inserted == N_DOCS, f"Exact count assertion: expected {N_DOCS} docs inserted, got {inserted}"

        with pg_conn.cursor() as cur:
            cur.execute(f'SELECT count(*) FROM "{SCHEMA}"."{TABLE}"')
            row_count = cur.fetchone()[0]
        assert row_count == N_DOCS, f"Table row count must match exact count {N_DOCS}"

    def test_2_cosine_distance_indexing_and_gin_index(self, pg_conn):
        # Verify indexes do not exist before creation
        vec_idx = f"{TABLE}_vec_hnsw"
        tsv_idx = f"{TABLE}_tsv_gin"
        assert ds.index_exists(pg_conn, TABLE, schema=SCHEMA, index_name=vec_idx) is False
        assert ds.index_exists(pg_conn, TABLE, schema=SCHEMA, index_name=tsv_idx) is False

        # Acceptance point 1a: create HNSW index with vector_cosine_ops and GIN index for full-text
        created = ds.create_indexes(
            pg_conn,
            TABLE,
            schema=SCHEMA,
            m=ds.DEFAULT_M,
            ef_construction=ds.DEFAULT_EF_CONSTRUCTION,
            vector_index_name=vec_idx,
            text_index_name=tsv_idx,
        )
        assert created is True, "Index creation must succeed"
        assert ds.index_exists(pg_conn, TABLE, schema=SCHEMA, index_name=vec_idx) is True
        assert ds.index_exists(pg_conn, TABLE, schema=SCHEMA, index_name=tsv_idx) is True

        # Verify index definition in pg_indexes uses vector_cosine_ops and hnsw
        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT indexdef FROM pg_indexes WHERE schemaname = %s AND tablename = %s AND indexname = %s",
                (SCHEMA, TABLE, vec_idx),
            )
            row = cur.fetchone()
            assert row is not None, "Index row must be present in pg_indexes"
            assert "vector_cosine_ops" in row[0], "Index must use vector_cosine_ops for cosine distance"
            assert "hnsw" in row[0], "Index must use HNSW access method"

    def test_2a_text_channel_candidates_carry_true_cosine_similarity(self, pg_conn):
        # Text-only weighting with candidate_limit == k: the winners are the text-channel
        # candidates, most of which are NOT among the top-k vector neighbours, so their
        # vector_similarity must come from the fallback 1-(embedding <=> q), not 0.0.
        k = 10
        query_text = "PostgreSQL pgvector"
        rng = random.Random(855)
        qv = [round(rng.random(), 4) for _ in range(DIM)]
        qv_str = "[" + ",".join(str(float(x)) for x in qv) + "]"

        with pg_conn.cursor() as cur:
            cur.execute(f'SELECT count(*) FROM "{SCHEMA}"."{TABLE}"')
            assert cur.fetchone()[0] == N_DOCS, "Fixture shape: 250-doc corpus must be present"
            cur.execute(
                f'SELECT id, (1.0 - (embedding <=> %s::vector)) FROM "{SCHEMA}"."{TABLE}"',
                (qv_str,),
            )
            true_sim = {int(i): float(sim) for i, sim in cur.fetchall()}
            cur.execute(
                f'SELECT id FROM "{SCHEMA}"."{TABLE}" ORDER BY embedding <=> %s::vector LIMIT %s',
                (qv_str, k),
            )
            top_vec_ids = {int(r[0]) for r in cur.fetchall()}
        pg_conn.rollback()
        assert len(true_sim) == N_DOCS and len(top_vec_ids) == k

        res = ds.hybrid_search(
            pg_conn, TABLE, query_text, qv, k=k, candidate_limit=k, w_text=1.0, w_vec=0.0, schema=SCHEMA
        )
        assert len(res) == k, f"Expected exactly {k} results, got {len(res)}"
        ids = {r.id for r in res}
        assert not ids <= top_vec_ids, (
            f"Fixture shape: at least one text-channel-only candidate must be returned: {ids} vs {top_vec_ids}"
        )
        for r in res:
            assert r.text_score > 0.0, r
            assert r.vector_similarity > 0.0, f"text-only candidate must carry its true cosine similarity: {r}"
            assert abs(r.vector_similarity - true_sim[r.id]) < 1e-6, (r, true_sim[r.id])

    def test_2b_vector_channel_fetches_exactly_candidate_limit_rows(self, pg_conn):
        # The vector CTE must be bounded by LIMIT cand_limit: the HNSW index scan in the
        # executed statement returns exactly max(candidate_limit, k) rows under a Limit node.
        k = 10
        candidate_limit = 10
        rng = random.Random(855)
        qv = [round(rng.random(), 4) for _ in range(DIM)]
        vec_idx = f"{TABLE}_vec_hnsw"
        assert ds.index_exists(pg_conn, TABLE, schema=SCHEMA, index_name=vec_idx) is True
        captured: list[tuple[str, object]] = []

        class _RecordingCursor:
            def __init__(self, cur):
                self._cur = cur

            def execute(self, sql, params=None):
                captured.append((sql, params))
                return self._cur.execute(sql, params)

            def __getattr__(self, name):
                return getattr(self._cur, name)

            def __enter__(self):
                self._cur.__enter__()
                return self

            def __exit__(self, *exc):
                return self._cur.__exit__(*exc)

        class _RecordingConn:
            def __init__(self, conn):
                self._conn = conn

            def cursor(self):
                return _RecordingCursor(self._conn.cursor())

            def __getattr__(self, name):
                return getattr(self._conn, name)

        res = ds.hybrid_search(
            _RecordingConn(pg_conn), TABLE, "PostgreSQL pgvector", qv,
            k=k, candidate_limit=candidate_limit, schema=SCHEMA,
        )
        assert len(res) == k, f"Expected exactly {k} results, got {len(res)}"
        hybrid = [(sql, params) for sql, params in captured if "vector_matches" in sql]
        assert len(hybrid) == 1, f"Fixture shape: exactly one hybrid statement captured, got {len(captured)}"
        sql, params = hybrid[0]

        pg_conn.rollback()
        with pg_conn.cursor() as cur:
            cur.execute("SET enable_seqscan = off")
        try:
            with pg_conn.cursor() as cur:
                cur.execute("EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY OFF) " + sql, params)
                plan = [row[0] for row in cur.fetchall()]
        finally:
            pg_conn.rollback()
            with pg_conn.cursor() as cur:
                cur.execute("RESET enable_seqscan")
            pg_conn.commit()

        assert plan, "EXPLAIN ANALYZE must return a plan"
        scans = [i for i, line in enumerate(plan) if f"Index Scan using {vec_idx}" in line]
        assert len(scans) == 1, "\n".join(plan)
        i = scans[0]
        m = re.search(r"\(actual rows=(\d+)", plan[i])
        assert m is not None, plan[i]
        expected = max(candidate_limit, k)
        assert int(m.group(1)) == expected, (
            f"HNSW index scan must fetch exactly {expected} candidates (LIMIT cand_limit), got {plan[i]!r}"
        )
        assert i > 0 and "Limit" in plan[i - 1], (
            "A Limit node must sit directly above the HNSW index scan:\n" + "\n".join(plan)
        )

    def test_3_combined_full_text_and_vector_query_single_sql(self, pg_conn):
        # Acceptance point 1b: combined full-text (tsvector/tsquery/ts_rank) + vector proximity query in single SQL statement
        query_text = "PostgreSQL pgvector"
        rng = random.Random(855)
        # Vector corresponding roughly to topic 0
        target_vec = [round(rng.random(), 4) for _ in range(DIM)]

        k = 10
        results = ds.hybrid_search(
            pg_conn,
            TABLE,
            query_text=query_text,
            query_vector=target_vec,
            k=k,
            w_text=0.5,
            w_vec=0.5,
            schema=SCHEMA,
        )

        assert len(results) == k, f"Expected {k} search results, got {len(results)}"
        # Verify rank ordering: descending by hybrid_score
        scores = [r.hybrid_score for r in results]
        assert scores == sorted(scores, reverse=True), "Results must be ranked descending by hybrid_score"

        # Verify scores are populated
        top = results[0]
        assert top.hybrid_score > 0.0, "Top result must have positive hybrid score"
        assert top.vector_similarity > 0.0, "Vector similarity must be populated"
        assert top.text_score >= 0.0, "Text score must be non-negative"

        # Relevant topic documents should appear at the top
        assert any("postgresql" in r.title.lower() for r in results[:3]), (
            "Top results must include PostgreSQL documents matched by hybrid search"
        )

    def test_4_native_sql_latency_under_10ms(self, pg_conn):
        # Acceptance point 2: native SQL query execution under 10ms
        query_text = "database transactions ACID"
        rng = random.Random(1234)
        target_vec = [round(rng.random(), 4) for _ in range(DIM)]

        # Warm-up run
        ds.hybrid_search(pg_conn, TABLE, query_text=query_text, query_vector=target_vec, k=10, schema=SCHEMA)

        latencies_ms = []
        for _ in range(25):
            t0 = time.perf_counter()
            res = ds.hybrid_search(
                pg_conn,
                TABLE,
                query_text=query_text,
                query_vector=target_vec,
                k=10,
                schema=SCHEMA,
            )
            t1 = time.perf_counter()
            assert len(res) > 0
            latencies_ms.append((t1 - t0) * 1000.0)

        mean_latency_ms = sum(latencies_ms) / len(latencies_ms)
        p95_latency_ms = sorted(latencies_ms)[int(len(latencies_ms) * 0.95)]
        assert mean_latency_ms < 10.0, (
            f"Mean SQL query latency {mean_latency_ms:.2f}ms must be under 10ms"
        )
        assert p95_latency_ms < 10.0, (
            f"P95 SQL query latency {p95_latency_ms:.2f}ms must be under 10ms"
        )

    def test_5_negative_control_unmatched_text_query(self, pg_conn):
        # Negative control: query text with nonexistent terms produces text_score == 0.0
        # while vector search still operates correctly
        nonsense_text = "xyzzy999nonexistentword"
        rng = random.Random(42)
        target_vec = [round(rng.random(), 4) for _ in range(DIM)]

        results = ds.hybrid_search(
            pg_conn,
            TABLE,
            query_text=nonsense_text,
            query_vector=target_vec,
            k=5,
            schema=SCHEMA,
        )

        assert len(results) == 5, f"Expected 5 results, got {len(results)}"
        # Negative control verification: no document matches the nonsense keyword
        for r in results:
            assert r.text_score == 0.0, (
                f"Negative control: text_score for unmatched query must be 0.0, got {r.text_score}"
            )
            assert r.vector_similarity > 0.0, "Vector similarity still evaluates correctly"


# ---------------------------------------------------------------------------
# Controlled corpus (dim 4) pinning the combined full-text + vector semantics
# with exact order and exact scores.  Query: 'brown fox' / [1,0,0,0].
#   A  text-only match   ('the quick brown fox jumps', [0,0,0,1])
#   B  vector-only match ('lorem ipsum',               [1,0,0,0])
#   C  both              ('brown fox again',           [0.9,0.1,0,0])
#   D  neither           ('unrelated words',           [0,0,1,0])
# ---------------------------------------------------------------------------

CTRL_TABLE = "ctrl_docs"
CTRL_DIM = 4
CTRL_QUERY_TEXT = "brown fox"
CTRL_QUERY_VEC = [1.0, 0.0, 0.0, 0.0]
CTRL_DOCS = [
    ("A", "the quick brown fox jumps", [0.0, 0.0, 0.0, 1.0]),
    ("B", "lorem ipsum", [1.0, 0.0, 0.0, 0.0]),
    ("C", "brown fox again", [0.9, 0.1, 0.0, 0.0]),
    ("D", "unrelated words", [0.0, 0.0, 1.0, 0.0]),
]
TOL = 1e-3
# cos([0.9,0.1,0,0],[1,0,0,0]) = 0.9/sqrt(0.82); ts_rank_cd of adjacent 'brown fox' = 0.1
C_VEC = 0.9 / (0.82 ** 0.5)
C_TEXT = 0.1
C_HYBRID = 0.5 * C_TEXT + 0.5 * C_VEC


def _ctrl_count(conn) -> int:
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute(f'SELECT count(*) FROM "{SCHEMA}"."{CTRL_TABLE}"')
        return int(cur.fetchone()[0])


@pytest.fixture(scope="module")
def ctrl(pg_conn):
    """Build the controlled corpus through the module; never asserts (tests do)."""
    shape = {"created": False, "indexed": False, "inserted": 0, "count": 0, "ids": {}}
    shape["created"] = ds.create_hybrid_table(pg_conn, CTRL_TABLE, dim=CTRL_DIM, schema=SCHEMA)
    if shape["created"]:
        shape["indexed"] = ds.create_indexes(pg_conn, CTRL_TABLE, schema=SCHEMA)
        shape["inserted"] = ds.insert_documents(pg_conn, CTRL_TABLE, CTRL_DOCS, schema=SCHEMA)
        try:
            shape["count"] = _ctrl_count(pg_conn)
            with pg_conn.cursor() as cur:
                cur.execute(f'SELECT title, id FROM "{SCHEMA}"."{CTRL_TABLE}"')
                shape["ids"] = {t: i for t, i in cur.fetchall()}
        except Exception:
            pg_conn.rollback()
    return shape


def _assert_ctrl_shape(ctrl) -> dict[str, int]:
    """Prove the fixture built the nonzero shape before any verdict."""
    assert ctrl["created"] is True, "Hybrid table creation must succeed"
    assert ctrl["indexed"] is True, "Index creation must succeed"
    assert ctrl["inserted"] == len(CTRL_DOCS), f"Exact count: expected {len(CTRL_DOCS)} inserted, got {ctrl['inserted']}"
    assert ctrl["count"] == len(CTRL_DOCS), f"Exact count: table must hold {len(CTRL_DOCS)} rows, got {ctrl['count']}"
    assert set(ctrl["ids"]) == {"A", "B", "C", "D"}, ctrl["ids"]
    return ctrl["ids"]


def _titles(results) -> list[str]:
    return [r.title for r in results]


class TestControlledCorpusHybridSemantics:
    """Acceptance (1): combined full-text + vector ranking pinned on a 4-doc corpus."""

    def test_exact_order_and_exact_scores(self, pg_conn, ctrl):
        ids = _assert_ctrl_shape(ctrl)
        res = ds.hybrid_search(pg_conn, CTRL_TABLE, CTRL_QUERY_TEXT, CTRL_QUERY_VEC, k=4, schema=SCHEMA)
        assert len(res) == 4, f"Expected exactly 4 results, got {len(res)}"
        assert _titles(res) == ["C", "B", "A", "D"], _titles(res)
        assert [r.id for r in res] == [ids["C"], ids["B"], ids["A"], ids["D"]]
        by = {r.title: r for r in res}
        # C: both channels
        assert abs(by["C"].hybrid_score - C_HYBRID) < TOL, by["C"]
        assert abs(by["C"].vector_similarity - C_VEC) < TOL, by["C"]
        assert abs(by["C"].text_score - C_TEXT) < TOL, by["C"]
        # B: vector-only (identical direction -> similarity exactly 1)
        assert abs(by["B"].vector_similarity - 1.0) < TOL, by["B"]
        assert by["B"].text_score == 0.0, by["B"]
        assert abs(by["B"].hybrid_score - 0.5) < TOL, by["B"]
        # A: text-only (orthogonal -> similarity exactly 0)
        assert abs(by["A"].vector_similarity - 0.0) < TOL, by["A"]
        assert abs(by["A"].text_score - C_TEXT) < TOL, by["A"]
        assert abs(by["A"].hybrid_score - 0.05) < TOL, by["A"]
        # D: neither
        assert by["D"].vector_similarity == 0.0 and by["D"].text_score == 0.0, by["D"]
        assert by["D"].hybrid_score == 0.0, by["D"]
        # every hybrid score is the weighted combination of its own channels
        for r in res:
            assert abs(r.hybrid_score - (0.5 * r.text_score + 0.5 * r.vector_similarity)) < TOL, r

    def test_vector_only_weights(self, pg_conn, ctrl):
        _assert_ctrl_shape(ctrl)
        res = ds.hybrid_search(
            pg_conn, CTRL_TABLE, CTRL_QUERY_TEXT, CTRL_QUERY_VEC, k=4, w_text=0.0, w_vec=1.0, schema=SCHEMA
        )
        assert len(res) == 4, f"Expected exactly 4 results, got {len(res)}"
        assert _titles(res)[:2] == ["B", "C"], _titles(res)
        assert set(_titles(res)[2:]) == {"A", "D"}, _titles(res)
        assert abs(res[0].hybrid_score - 1.0) < TOL, res[0]
        for r in res:
            assert abs(r.hybrid_score - r.vector_similarity) < TOL, r

    def test_text_only_weights_hybrid_equals_text_score(self, pg_conn, ctrl):
        _assert_ctrl_shape(ctrl)
        res = ds.hybrid_search(
            pg_conn, CTRL_TABLE, CTRL_QUERY_TEXT, CTRL_QUERY_VEC, k=4, w_text=1.0, w_vec=0.0, schema=SCHEMA
        )
        assert len(res) == 4, f"Expected exactly 4 results, got {len(res)}"
        by = {r.title: r for r in res}
        for r in res:
            assert abs(r.hybrid_score - r.text_score) < TOL, r
        assert set(_titles(res)[:2]) == {"A", "C"}, _titles(res)
        assert by["A"].hybrid_score > 0.0 and by["C"].hybrid_score > 0.0, res
        assert abs(by["A"].text_score - C_TEXT) < TOL and abs(by["C"].text_score - C_TEXT) < TOL, res
        # non-matching text -> exactly zero, even for the best vector neighbour (B)
        assert by["B"].hybrid_score == 0.0 and by["B"].text_score == 0.0, by["B"]
        assert by["D"].hybrid_score == 0.0 and by["D"].text_score == 0.0, by["D"]

    def test_k_and_candidate_limit_exact_row_counts(self, pg_conn, ctrl):
        _assert_ctrl_shape(ctrl)
        res2 = ds.hybrid_search(pg_conn, CTRL_TABLE, CTRL_QUERY_TEXT, CTRL_QUERY_VEC, k=2, schema=SCHEMA)
        assert len(res2) == 2, f"k=2 must return exactly 2 rows, got {len(res2)}"
        assert _titles(res2) == ["C", "B"], _titles(res2)
        res3 = ds.hybrid_search(
            pg_conn, CTRL_TABLE, CTRL_QUERY_TEXT, CTRL_QUERY_VEC, k=3, candidate_limit=1, schema=SCHEMA
        )
        assert len(res3) == 3, f"k=3 with candidate_limit=1 must still return 3 rows, got {len(res3)}"
        assert _titles(res3) == ["C", "B", "A"], _titles(res3)

    def test_insert_wrong_dimension_returns_zero_and_leaves_table_unchanged(self, pg_conn, ctrl):
        _assert_ctrl_shape(ctrl)
        before = _ctrl_count(pg_conn)
        assert before == len(CTRL_DOCS)
        bad = [("X", "wrong dimension", [1.0, 0.0, 0.0])]  # dim 3 into vector(4)
        assert ds.insert_documents(pg_conn, CTRL_TABLE, bad, schema=SCHEMA) == 0, (
            "insert_documents must report 0 rows when the batch is rolled back"
        )
        assert _ctrl_count(pg_conn) == before, "Rolled-back batch must not change the row count"
        # connection is still usable after the rollback
        res = ds.hybrid_search(pg_conn, CTRL_TABLE, CTRL_QUERY_TEXT, CTRL_QUERY_VEC, k=4, schema=SCHEMA)
        assert _titles(res) == ["C", "B", "A", "D"], _titles(res)

    def test_vector_order_by_is_served_by_hnsw_index_scan(self, pg_conn, ctrl):
        _assert_ctrl_shape(ctrl)
        vec_idx = f"{CTRL_TABLE}_vec_hnsw"
        assert ds.index_exists(pg_conn, CTRL_TABLE, schema=SCHEMA, index_name=vec_idx) is True
        pg_conn.rollback()
        with pg_conn.cursor() as cur:
            cur.execute(
                f'EXPLAIN SELECT id FROM "{SCHEMA}"."{CTRL_TABLE}" ORDER BY embedding <=> %s::vector LIMIT 4',
                ("[" + ",".join(str(x) for x in CTRL_QUERY_VEC) + "]",),
            )
            plan = "\n".join(row[0] for row in cur.fetchall())
        pg_conn.rollback()
        assert plan, "EXPLAIN must return a plan"
        assert f"Index Scan using {vec_idx}" in plan, plan
        assert "Order By: (embedding <=>" in plan, plan

    def test_hybrid_query_drives_both_gin_and_hnsw_indexes(self, pg_conn, ctrl):
        """The module's single SQL statement must hit the GIN (tsv @@ filter) and HNSW indexes."""
        _assert_ctrl_shape(ctrl)
        vec_idx = f"{CTRL_TABLE}_vec_hnsw"
        tsv_idx = f"{CTRL_TABLE}_tsv_gin"

        def idx_scans() -> dict[str, int]:
            with pg_conn.cursor() as cur:
                cur.execute("SELECT pg_stat_force_next_flush()")
            pg_conn.commit()
            with pg_conn.cursor() as cur:
                cur.execute(
                    "SELECT indexrelname, idx_scan FROM pg_stat_user_indexes WHERE schemaname=%s AND relname=%s",
                    (SCHEMA, CTRL_TABLE),
                )
                return {name: int(n) for name, n in cur.fetchall()}

        pg_conn.rollback()
        with pg_conn.cursor() as cur:
            # 4 rows: make the planner prefer the GIN bitmap scan over a trivial seq scan
            cur.execute("SET enable_seqscan = off")
        pg_conn.commit()
        try:
            before = idx_scans()
            assert vec_idx in before and tsv_idx in before, before
            res = ds.hybrid_search(pg_conn, CTRL_TABLE, CTRL_QUERY_TEXT, CTRL_QUERY_VEC, k=4, schema=SCHEMA)
            assert _titles(res) == ["C", "B", "A", "D"], _titles(res)
            after = idx_scans()
        finally:
            pg_conn.rollback()
            with pg_conn.cursor() as cur:
                cur.execute("RESET enable_seqscan")
            pg_conn.commit()
        assert after[vec_idx] > before[vec_idx], f"HNSW index not scanned: {before} -> {after}"
        assert after[tsv_idx] > before[tsv_idx], f"GIN index not scanned (text CTE filter missing?): {before} -> {after}"


class TestLiveConnectEntryPoint:
    """The module's own connect() (runtime entry point) against the independently-probed cluster."""

    def test_connect_returns_live_loopback_connection(self, pg_conn):
        # pg_conn proves (independently of the module) that Postgres is up; the module's
        # own connect() must then return a usable loopback connection, not None.
        conn = ds.connect()
        assert conn is not None, "db_search.connect() must return a live connection when Postgres is up"
        try:
            host = getattr(conn.info, "host", None)
            assert host in ("127.0.0.1", "localhost", "::1"), f"External egress prohibited: {host}"
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                assert cur.fetchone() == (1,)
        finally:
            conn.close()
