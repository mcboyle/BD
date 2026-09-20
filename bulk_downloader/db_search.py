"""Row 855: PGVECTOR-NATIVE-HYBRID-SEMANTIC-SEARCH-IN-POSTGRES.

Leverages local PostgreSQL cluster (127.0.0.1:5432) enabling pgvector extension
with HNSW indexes (vector_cosine_ops) for hybrid BM25 + cosine vector searches
via single SQL queries.
Zero external egress (strictly 127.0.0.1:5432 loopback).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Sequence

DEFAULT_M = 16
DEFAULT_EF_CONSTRUCTION = 64
DEFAULT_EF_SEARCH = 100
DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:5432/ai_mesh"
FALLBACK_DSN = "postgresql://postgres:postgres@127.0.0.1:5432/postgres"

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


def db_search_dsn() -> str:
    """Configured DSN for pgvector hybrid search."""
    return (
        (os.environ.get("PGVECTOR_DSN") or "").strip()
        or (os.environ.get("MOD3_PG_DSN") or "").strip()
        or DEFAULT_DSN
    )


def connect(dsn: str | None = None):
    """A psycopg connection, or None. Fail-open on connection error or missing driver."""
    target_dsn = dsn or db_search_dsn()
    if not target_dsn:
        return None
    try:
        import psycopg
    except Exception:
        return None
    try:
        return psycopg.connect(target_dsn, connect_timeout=5)
    except Exception:
        if dsn is None and target_dsn != FALLBACK_DSN:
            try:
                return psycopg.connect(FALLBACK_DSN, connect_timeout=5)
            except Exception:
                return None
        return None


def ensure_extension(conn) -> bool:
    """CREATE EXTENSION IF NOT EXISTS vector."""
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


def ensure_schema(conn, schema: str = "db_search") -> bool:
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


def drop_schema(conn, schema: str = "db_search") -> bool:
    """DROP SCHEMA IF EXISTS ... CASCADE."""
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


def create_hybrid_table(conn, table: str, dim: int, schema: str = "db_search") -> bool:
    """Create table with text, vector(dim), and generated tsvector columns."""
    schema = _valid_ident(schema, "schema")
    table = _valid_ident(table, "table")
    if dim not in _dim_ok(dim):
        raise ValueError(f"invalid vector dimension: {dim!r}")
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                f'CREATE TABLE IF NOT EXISTS "{schema}"."{table}" ('
                f'id SERIAL PRIMARY KEY, '
                f'title TEXT NOT NULL, '
                f'content TEXT NOT NULL, '
                f'embedding vector({dim}), '
                f'tsv tsvector GENERATED ALWAYS AS ('
                f'  to_tsvector(\'english\', coalesce(title, \'\') || \' \' || coalesce(content, \'\'))'
                f') STORED'
                f')'
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False


def create_indexes(
    conn,
    table: str,
    schema: str = "db_search",
    m: int = DEFAULT_M,
    ef_construction: int = DEFAULT_EF_CONSTRUCTION,
    vector_index_name: str | None = None,
    text_index_name: str | None = None,
) -> bool:
    """Create HNSW cosine distance index and GIN full-text index."""
    schema = _valid_ident(schema, "schema")
    table = _valid_ident(table, "table")
    v_idx = _valid_ident(vector_index_name or f"{table}_vec_hnsw", "index")
    t_idx = _valid_ident(text_index_name or f"{table}_tsv_gin", "index")
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                f'CREATE INDEX IF NOT EXISTS "{v_idx}" ON "{schema}"."{table}" '
                f'USING hnsw (embedding vector_cosine_ops) '
                f'WITH (m={int(m)}, ef_construction={int(ef_construction)})'
            )
            cur.execute(
                f'CREATE INDEX IF NOT EXISTS "{t_idx}" ON "{schema}"."{table}" '
                f'USING gin (tsv)'
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False


def index_exists(
    conn,
    table: str,
    schema: str = "db_search",
    index_name: str | None = None,
) -> bool:
    """Check if index exists in pg_indexes."""
    try:
        schema = _valid_ident(schema, "schema")
        table = _valid_ident(table, "table")
        idx = _valid_ident(index_name or f"{table}_vec_hnsw", "index")
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


@dataclass
class SearchResult:
    id: int
    title: str
    content: str
    hybrid_score: float
    vector_similarity: float
    text_score: float


def insert_documents(
    conn,
    table: str,
    documents: Sequence[dict[str, Any] | tuple[str, str, Sequence[float]]],
    schema: str = "db_search",
) -> int:
    """Insert documents with title, content, and embedding vector."""
    schema = _valid_ident(schema, "schema")
    table = _valid_ident(table, "table")
    if conn is None or not documents:
        return 0
    try:
        rows = []
        for doc in documents:
            if isinstance(doc, dict):
                t = str(doc.get("title", ""))
                c = str(doc.get("content", ""))
                v = doc.get("embedding", [])
            else:
                t, c, v = doc
            v_str = "[" + ",".join(str(float(x)) for x in v) + "]"
            rows.append((t, c, v_str))

        with conn.cursor() as cur:
            cur.executemany(
                f'INSERT INTO "{schema}"."{table}" (title, content, embedding) '
                f'VALUES (%s, %s, %s::vector)',
                rows,
            )
        conn.commit()
        return len(documents)
    except Exception:
        conn.rollback()
        return 0


def hybrid_search(
    conn,
    table: str,
    query_text: str,
    query_vector: Sequence[float],
    k: int = 10,
    w_text: float = 0.5,
    w_vec: float = 0.5,
    schema: str = "db_search",
    candidate_limit: int = 50,
    ef_search: int = DEFAULT_EF_SEARCH,
) -> list[SearchResult]:
    """Execute hybrid BM25 + cosine vector search in a single SQL query."""
    schema = _valid_ident(schema, "schema")
    table = _valid_ident(table, "table")
    if conn is None:
        return []

    # Configure HNSW ef_search parameter for the transaction
    with conn.cursor() as cur:
        cur.execute(f"SET hnsw.ef_search = {int(ef_search)}")
    conn.commit()

    qvec_str = "[" + ",".join(str(float(x)) for x in query_vector) + "]"
    cand_lim = max(int(candidate_limit), int(k))

    single_sql = f'''
        WITH text_matches AS (
            SELECT id, ts_rank_cd(tsv, plainto_tsquery('english', %(query_text)s)) AS text_score
            FROM "{schema}"."{table}"
            WHERE tsv @@ plainto_tsquery('english', %(query_text)s)
            ORDER BY text_score DESC
            LIMIT %(cand_limit)s
        ),
        vector_matches AS (
            SELECT id, (1.0 - (embedding <=> %(query_vector)s::vector)) AS vec_score
            FROM "{schema}"."{table}"
            ORDER BY embedding <=> %(query_vector)s::vector
            LIMIT %(cand_limit)s
        ),
        combined AS (
            SELECT
                COALESCE(t.id, v.id) AS id,
                (%(w_text)s * COALESCE(t.text_score, 0.0) + %(w_vec)s * COALESCE(v.vec_score, (1.0 - (doc.embedding <=> %(query_vector)s::vector)))) AS hybrid_score,
                COALESCE(t.text_score, 0.0) AS text_score,
                COALESCE(v.vec_score, (1.0 - (doc.embedding <=> %(query_vector)s::vector))) AS vector_similarity
            FROM text_matches t
            FULL OUTER JOIN vector_matches v ON t.id = v.id
            JOIN "{schema}"."{table}" doc ON doc.id = COALESCE(t.id, v.id)
        )
        SELECT doc.id, doc.title, doc.content, c.hybrid_score, c.vector_similarity, c.text_score
        FROM combined c
        JOIN "{schema}"."{table}" doc ON doc.id = c.id
        ORDER BY c.hybrid_score DESC
        LIMIT %(k)s
    '''

    params = {
        "query_text": query_text,
        "query_vector": qvec_str,
        "cand_limit": cand_lim,
        "w_text": float(w_text),
        "w_vec": float(w_vec),
        "k": int(k),
    }

    results = []
    with conn.cursor() as cur:
        cur.execute(single_sql, params)
        for row in cur.fetchall():
            results.append(
                SearchResult(
                    id=row[0],
                    title=row[1],
                    content=row[2],
                    hybrid_score=float(row[3]),
                    vector_similarity=float(row[4]),
                    text_score=float(row[5]),
                )
            )
    return results
