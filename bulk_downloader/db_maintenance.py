"""bulk_downloader.db_maintenance -- Row 877: Automated table vacuum and index bloat compactor.

Provides scheduled, low-impact index bloat detection, non-blocking maintenance
(REINDEX CONCURRENTLY / WAL checkpointing), and database storage compaction.
Follows Fleet Rule 21 (zero site logins touched).
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def quote_ident(name: str) -> str:
    """SQL identifier quoting shared by SQLite and PostgreSQL: double quotes,
    embedded double quotes doubled ("idx with space" -> "\"idx with space\"")."""
    return '"' + str(name).replace('"', '""') + '"'


def is_sqlite_connection(conn: Any) -> bool:
    """Backend detection by TYPE, not by attribute guessing: a psycopg Connection
    also has .execute() and no .pg_conn, so the old duck-typing sent
    sqlite_master SQL to PostgreSQL."""
    if isinstance(conn, sqlite3.Connection):
        return True
    module = type(conn).__module__ or ""
    return module.startswith("sqlite3") or module.startswith("pysqlite")


def detect_index_bloat(
    conn: Any,
    bloat_threshold: float = 0.3,
) -> list[dict[str, Any]]:
    """Detect index page fragmentation and bloat across database indexes.

    Returns a list of dicts with index metadata, bloat ratio, and is_bloated flag.
    """
    reports: list[dict[str, Any]] = []

    is_sqlite = is_sqlite_connection(conn)

    if is_sqlite:
        cursor = conn.cursor()
        try:
            indexes = cursor.execute(
                "SELECT name, tbl_name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_autoindex%'"
            ).fetchall()
        except (sqlite3.Error, OSError) as exc:
            log.warning("Failed to query sqlite_master for indexes: %s", exc)
            return reports

        # Check overall database freelist ratio
        freelist_count = 0
        page_count = 1
        try:
            fl_row = cursor.execute("PRAGMA freelist_count").fetchone()
            pc_row = cursor.execute("PRAGMA page_count").fetchone()
            if fl_row:
                freelist_count = fl_row[0]
            if pc_row and pc_row[0] > 0:
                page_count = pc_row[0]
        except (sqlite3.Error, OSError) as exc:
            log.debug("PRAGMA freelist/page_count unavailable: %s", exc)

        # Database free space (freelist) is a property of the FILE, not of any
        # index: it is reported alongside each row for context but never folded
        # into an index's bloat ratio (deleting an unrelated table must not flag
        # a healthy index).
        freelist_ratio = freelist_count / max(1, page_count)

        for idx_name, tbl_name in indexes:
            unused_bytes = 0
            payload_bytes = 0
            dbstat_ratio = 0.0
            measured = False

            try:
                stat_row = cursor.execute(
                    "SELECT coalesce(sum(unused), 0), coalesce(sum(payload), 0) FROM dbstat WHERE name = ?",
                    (idx_name,),
                ).fetchone()
                if stat_row:
                    unused_bytes, payload_bytes = stat_row
                    total_bytes = unused_bytes + payload_bytes
                    measured = True
                    if total_bytes > 0:
                        dbstat_ratio = unused_bytes / total_bytes
            except (sqlite3.Error, OSError) as exc:
                log.debug("dbstat query unavailable for %s: %s", idx_name, exc)

            reports.append({
                "index_name": idx_name,
                "table_name": tbl_name,
                "bloat_ratio": round(dbstat_ratio, 4),
                "is_bloated": measured and dbstat_ratio >= bloat_threshold,
                "measured": measured,
                "unused_bytes": unused_bytes,
                "payload_bytes": payload_bytes,
                "database_freelist_ratio": round(freelist_ratio, 4),
            })
    else:
        reports.extend(_detect_postgres_index_bloat(conn, bloat_threshold))

    return reports


_PG_INDEX_STATS = """
SELECT n.nspname, c.relname AS tablename, i.relname AS indexname,
       pg_relation_size(i.oid) AS index_bytes,
       (pgstatindex(i.oid)).avg_leaf_density AS avg_leaf_density,
       (pgstatindex(i.oid)).leaf_fragmentation AS leaf_fragmentation
FROM pg_index x
JOIN pg_class i ON i.oid = x.indexrelid
JOIN pg_class c ON c.oid = x.indrelid
JOIN pg_namespace n ON n.oid = i.relnamespace
JOIN pg_am am ON am.oid = i.relam
WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') AND am.amname = 'btree'
"""

_PG_INDEX_SIZES = """
SELECT n.nspname, c.relname AS tablename, i.relname AS indexname, pg_relation_size(i.oid) AS index_bytes
FROM pg_index x
JOIN pg_class i ON i.oid = x.indexrelid
JOIN pg_class c ON c.oid = x.indrelid
JOIN pg_namespace n ON n.oid = i.relnamespace
WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
"""


def _pg_rows(conn: Any, sql: str) -> list:
    cur = conn.execute(sql) if hasattr(conn, "execute") else conn.cursor().execute(sql)
    if hasattr(cur, "fetchall"):
        return list(cur.fetchall())
    return list(conn.fetchall()) if hasattr(conn, "fetchall") else []


def _detect_postgres_index_bloat(conn: Any, bloat_threshold: float) -> list[dict[str, Any]]:
    """PostgreSQL: MEASURE bloat with pgstattuple's pgstatindex() (btree leaf
    density); when the extension is absent, report sizes with measured=False and
    bloat_ratio=None -- never a fabricated 0.0."""
    reports: list[dict[str, Any]] = []
    try:
        for schema, table, index, index_bytes, density, fragmentation in _pg_rows(conn, _PG_INDEX_STATS):
            ratio = None
            if density is not None:
                ratio = max(0.0, min(1.0, 1.0 - float(density) / 100.0))
            reports.append({
                "index_name": index,
                "table_name": table,
                "schema": schema,
                "bloat_ratio": round(ratio, 4) if ratio is not None else None,
                "is_bloated": ratio is not None and ratio >= bloat_threshold,
                "measured": ratio is not None,
                "index_bytes": int(index_bytes or 0),
                "leaf_fragmentation": fragmentation,
            })
        return reports
    except Exception as exc:  # noqa: BLE001 -- pgstattuple absent / no privilege / driver shape
        log.info("pgstatindex unavailable (%s); falling back to size-only report", exc)
        try:
            if hasattr(conn, "rollback"):
                conn.rollback()
        except Exception:  # noqa: BLE001
            pass
    try:
        for schema, table, index, index_bytes in _pg_rows(conn, _PG_INDEX_SIZES):
            reports.append({
                "index_name": index,
                "table_name": table,
                "schema": schema,
                "bloat_ratio": None,
                "is_bloated": False,
                "measured": False,
                "index_bytes": int(index_bytes or 0),
            })
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to query pg index catalog: %s", exc)
    return reports


def run_postgres_maintenance(
    cursor: Any,
    index_names: list[str] | None = None,
    tables: list[str] | None = None,
) -> dict[str, Any]:
    """Run non-blocking maintenance for PostgreSQL using REINDEX CONCURRENTLY and CHECKPOINT.

    REINDEX CONCURRENTLY acquires ShareUpdateExclusiveLock rather than AccessExclusiveLock,
    allowing concurrent SELECT, INSERT, UPDATE, and DELETE operations.
    """
    reindexed: list[str] = []

    if index_names:
        for idx in index_names:
            cursor.execute(f"REINDEX INDEX CONCURRENTLY {quote_ident(idx)}")
            reindexed.append(idx)
    elif tables:
        for tbl in tables:
            cursor.execute(f"REINDEX TABLE CONCURRENTLY {quote_ident(tbl)}")
            reindexed.append(tbl)
    else:
        cursor.execute("REINDEX SCHEMA CONCURRENTLY public")
        reindexed.append("SCHEMA:public")

    cursor.execute("CHECKPOINT")
    return {
        "ok": True,
        "reindexed": reindexed,
        "checkpoint": True,
    }


def run_sqlite_maintenance(
    conn: Any,
    checkpoint_mode: str = "PASSIVE",
    index_names: list[str] | None = None,
) -> dict[str, Any]:
    """Run non-blocking SQLite maintenance during idle windows.

    Executes non-blocking WAL checkpointing and selective index reindexing.
    """
    cursor = conn.cursor()

    busy = 0
    log_size = 0
    checkpointed = 0
    try:
        row = cursor.execute(f"PRAGMA wal_checkpoint({checkpoint_mode})").fetchone()
        if row:
            busy, log_size, checkpointed = row
    except (sqlite3.Error, OSError) as exc:
        log.warning("PRAGMA wal_checkpoint failed: %s", exc)
        return {"ok": False, "error": str(exc)}

    # REINDEX needs the write lock. "Non-blocking" means we never WAIT for it and
    # never fail the run: with an active writer the index is DEFERRED to the next
    # idle window and reported as such.
    reindexed: list[str] = []
    deferred: list[dict[str, str]] = []
    if index_names:
        previous_timeout = None
        try:
            row = cursor.execute("PRAGMA busy_timeout").fetchone()
            previous_timeout = row[0] if row else None
            cursor.execute("PRAGMA busy_timeout = 0")
        except (sqlite3.Error, OSError):
            pass
        try:
            for idx in index_names:
                try:
                    cursor.execute(f"REINDEX {quote_ident(idx)}")
                    reindexed.append(idx)
                except sqlite3.OperationalError as exc:
                    if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                        deferred.append({"index": idx, "reason": str(exc)})
                        log.info("REINDEX %s deferred: %s", idx, exc)
                    else:
                        raise
        finally:
            if previous_timeout is not None:
                try:
                    cursor.execute(f"PRAGMA busy_timeout = {int(previous_timeout)}")
                except (sqlite3.Error, OSError):
                    pass

    return {
        "ok": True,
        "checkpoint": {
            "mode": checkpoint_mode,
            "busy": busy,
            "log": log_size,
            "checkpointed": checkpointed,
        },
        "reindexed": reindexed,
        "deferred": deferred,
    }


def compact_database(
    conn: Any,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Compact database storage by checkpointing WAL and running database vacuum.

    Achieves post-compaction storage reduction.
    """
    try:
        # Checkpoint WAL tail into database file
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        # Run VACUUM to reclaim free/fragmented pages
        conn.execute("VACUUM")

        # Ensure final state is checkpointed
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return {"ok": True, "error": None}
    except (sqlite3.Error, OSError) as exc:
        log.error("Database compaction failed: %s", exc)
        return {"ok": False, "error": str(exc)}
