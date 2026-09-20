"""Row 875: pgvector HNSW indexing for similarity search.

Targets PostgreSQL 16 on 127.0.0.1:5432 with the `vector` extension (pgvector).
Builds an HNSW approximate-nearest-neighbor index with m=16, ef_construction=64
(the register row's SCOPE values) and measures query latency and recall parity
against exact flat sequential scan.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Sequence

DEFAULT_M = 16
DEFAULT_EF_CONSTRUCTION = 64
DEFAULT_EF_SEARCH = 100
DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:5432/postgres"

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _dim_ok(dim: int) -> set[int]:
    """{dim} when it is a positive vector dimension, else empty."""
    return {dim} if isinstance(dim, int) and not isinstance(dim, bool) and dim > 0 else set()


def _ident_ok(name: str) -> set[str]:
    """{name} when it is a safe SQL identifier, else empty."""
    return {name} if isinstance(name, str) and _IDENT_RE.match(name) else set()


def _valid_ident(name: str, what: str) -> str:
    if name not in _ident_ok(name):
        raise ValueError(f"invalid {what} identifier: {name!r}")
    return name


def pg_vector_dsn() -> str:
    """Configured DSN for pgvector. Checks PGVECTOR_DSN, MOD3_PG_DSN, or falls back to DEFAULT_DSN."""
    return (
        (os.environ.get("PGVECTOR_DSN") or "").strip()
        or (os.environ.get("MOD3_PG_DSN") or "").strip()
        or DEFAULT_DSN
    )


def connect(dsn: str | None = None):
    """A psycopg connection, or None. Fail-open on connection error or missing driver."""
    target_dsn = dsn or pg_vector_dsn()
    if not target_dsn:
        return None
    try:
        import psycopg
    except Exception:
        return None
    try:
        return psycopg.connect(target_dsn, connect_timeout=5)
    except Exception:
        return None


def ensure_extension(conn) -> bool:
    """CREATE EXTENSION IF NOT EXISTS vector. Returns True on success."""
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False


def ensure_schema(conn, schema: str = "row875vec") -> bool:
    """CREATE SCHEMA IF NOT EXISTS. Returns True on success."""
    schema = _valid_ident(schema, "schema")
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False


def drop_schema(conn, schema: str = "row875vec") -> bool:
    """DROP SCHEMA IF EXISTS ... CASCADE. Safe against aborted prior transactions."""
    schema = _valid_ident(schema, "schema")
    if conn is None:
        return False
    try:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False


def create_vector_table(conn, table: str, dim: int, schema: str = "row875vec") -> bool:
    """Create table with vector(dim) column."""
    schema = _valid_ident(schema, "schema")
    table = _valid_ident(table, "table")
    if dim not in _dim_ok(dim):
        raise ValueError(f"invalid vector dimension: {dim!r}")
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                f'CREATE TABLE IF NOT EXISTS "{schema}"."{table}"('
                f'id SERIAL PRIMARY KEY, embedding vector({dim}))'
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False


def create_hnsw_index(
    conn,
    table: str,
    schema: str = "row875vec",
    m: int = DEFAULT_M,
    ef_construction: int = DEFAULT_EF_CONSTRUCTION,
    index_name: str | None = None,
) -> bool:
    """Create HNSW index on embedding column with specified m and ef_construction."""
    schema = _valid_ident(schema, "schema")
    table = _valid_ident(table, "table")
    idx = _valid_ident(index_name or f"{table}_embedding_hnsw", "index")
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                f'CREATE INDEX IF NOT EXISTS "{idx}" ON "{schema}"."{table}" '
                f'USING hnsw (embedding vector_l2_ops) '
                f'WITH (m={int(m)}, ef_construction={int(ef_construction)})'
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False


def index_exists(
    conn,
    table: str,
    schema: str = "row875vec",
    index_name: str | None = None,
) -> bool:
    """Check if index exists in pg_indexes."""
    try:
        schema = _valid_ident(schema, "schema")
        table = _valid_ident(table, "table")
        idx = _valid_ident(index_name or f"{table}_embedding_hnsw", "index")
    except ValueError:
        return False
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_indexes WHERE schemaname=%s AND tablename=%s AND indexname=%s",
                (schema, table, idx),
            )
            return cur.fetchone() is not None
    except Exception:
        return False


def insert_vectors(
    conn,
    table: str,
    vectors: Sequence[Sequence[float]],
    schema: str = "row875vec",
) -> int:
    """Bulk insert vectors using COPY protocol for high throughput."""
    schema = _valid_ident(schema, "schema")
    table = _valid_ident(table, "table")
    if conn is None or not vectors:
        return 0
    try:
        with conn.cursor() as cur:
            with cur.copy(f'COPY "{schema}"."{table}" (embedding) FROM STDIN') as copy:
                for v in vectors:
                    copy.write_row([f"[{','.join(str(float(x)) for x in v)}]"])
        conn.commit()
        return len(vectors)
    except Exception:
        conn.rollback()
        return 0


@dataclass
class BenchResult:
    n_queries: int
    k: int
    mean_latency_ms: float
    p99_latency_ms: float
    recall_at_k: float


def benchmark(
    conn,
    table: str,
    queries: Sequence[Sequence[float]],
    k: int = 10,
    schema: str = "row875vec",
    ef_search: int = DEFAULT_EF_SEARCH,
) -> BenchResult:
    """Measure HNSW query latency and recall parity vs sequential flat scan."""
    schema = _valid_ident(schema, "schema")
    table = _valid_ident(table, "table")
    if conn is None or not queries:
        return BenchResult(0, k, 0.0, 0.0, 0.0)

    # 1. Configure HNSW index search params
    with conn.cursor() as cur:
        cur.execute(f"SET hnsw.ef_search = {int(ef_search)}")
        cur.execute("SET enable_indexscan = on")
        cur.execute("SET enable_bitmapscan = on")
    conn.commit()

    latencies_ms = []
    hnsw_results = []

    # 2. Measure HNSW query latency
    with conn.cursor() as cur:
        for q in queries:
            q_str = "[" + ",".join(str(float(x)) for x in q) + "]"
            t0 = time.perf_counter()
            cur.execute(
                f'SELECT id FROM "{schema}"."{table}" ORDER BY embedding <-> %s::vector LIMIT %s',
                (q_str, int(k)),
                prepare=True,
            )
            hnsw_ids = [r[0] for r in cur.fetchall()]
            t_hnsw = (time.perf_counter() - t0) * 1000.0
            latencies_ms.append(t_hnsw)
            hnsw_results.append(hnsw_ids)
    conn.commit()

    # 3. Ground truth exact sequential scans (index disabled)
    with conn.cursor() as cur:
        cur.execute("SET enable_indexscan = off")
        cur.execute("SET enable_bitmapscan = off")
    conn.commit()

    recalls = []
    with conn.cursor() as cur:
        for q, hnsw_ids in zip(queries, hnsw_results):
            q_str = "[" + ",".join(str(float(x)) for x in q) + "]"
            cur.execute(
                f'SELECT id FROM "{schema}"."{table}" ORDER BY embedding <-> %s::vector LIMIT %s',
                (q_str, int(k)),
                prepare=True,
            )
            exact_ids = [r[0] for r in cur.fetchall()]
            if exact_ids:
                recalls.append(len(set(hnsw_ids) & set(exact_ids)) / len(exact_ids))

    # Reset scan params
    with conn.cursor() as cur:
        cur.execute("SET enable_indexscan = on")
        cur.execute("SET enable_bitmapscan = on")
    conn.commit()

    latencies_ms.sort()
    n = len(latencies_ms)
    mean_ms = sum(latencies_ms) / n if n else 0.0
    p99_ms = latencies_ms[min(n - 1, int(n * 0.99))] if n else 0.0
    mean_recall = sum(recalls) / len(recalls) if recalls else 0.0

    return BenchResult(
        n_queries=n,
        k=k,
        mean_latency_ms=mean_ms,
        p99_latency_ms=p99_ms,
        recall_at_k=mean_recall,
    )
