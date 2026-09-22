"""Row 1016: Lock-Free Bulk Ingestion Pipeline via Temporary Staging Tables.

Provides high-throughput, lock-minimized bulk ingestion for SQLite by staging
and validating batches in connection-private temporary tables before transferring
them into target destination tables.
"""

from __future__ import annotations

import enum
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


class ConflictStrategy(str, enum.Enum):
    REPLACE = "REPLACE"
    IGNORE = "IGNORE"
    FAIL = "FAIL"


@dataclass
class StagingIngestConfig:
    chunk_size: int = 1000
    temp_table_prefix: str = "_staging_"
    conflict_strategy: ConflictStrategy = ConflictStrategy.REPLACE


@dataclass
class StagingResult:
    staged: int = 0
    transferred: int = 0
    errors: int = 0
    flush_duration_ms: float = 0.0
    target_table: str = ""


class StagingBatch:
    def __init__(self, target_table: str, temp_table: str, columns: List[str]):
        self.target_table = target_table
        self.temp_table = temp_table
        self.columns = columns
        self.staged_count = 0
        self.staged_columns: List[str] = []


class StagingSession:
    def __init__(
        self,
        pipeline: StagingIngestPipeline,
        target_table: str,
        conflict_strategy: Optional[ConflictStrategy] = None,
    ):
        self.pipeline = pipeline
        self.target_table = target_table
        self.conflict_strategy = conflict_strategy
        self._buffer: List[Dict[str, Any]] = []

    def __enter__(self) -> StagingSession:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is None:
            self.flush()
        else:
            self._buffer.clear()

    def add(self, row: Dict[str, Any]) -> None:
        self._buffer.append(row)
        if len(self._buffer) >= self.pipeline.config.chunk_size:
            self._stage_buffer()

    def add_many(self, rows: Sequence[Dict[str, Any]]) -> None:
        self._buffer.extend(rows)
        if len(self._buffer) >= self.pipeline.config.chunk_size:
            self._stage_buffer()

    def _stage_buffer(self) -> None:
        if self._buffer:
            self.pipeline.stage_rows(self.target_table, self._buffer)
            self._buffer.clear()

    def flush(self) -> StagingResult:
        self._stage_buffer()
        return self.pipeline.flush(self.target_table, conflict_strategy=self.conflict_strategy)


_GLOBAL_TELEMETRY = {
    "total_rows_staged": 0,
    "total_rows_transferred": 0,
    "total_flushes": 0,
    "total_flush_time_ms": 0.0,
    "avg_flush_duration_ms": 0.0,
}


class StagingIngestPipeline:
    def __init__(
        self,
        conn: sqlite3.Connection,
        config: Optional[StagingIngestConfig] = None,
        *,
        owns_connection: bool = True,
    ):
        self.conn = conn
        self.config = config or StagingIngestConfig()
        self.owns_connection = owns_connection
        self._column_cache: Dict[str, List[str]] = {}
        self._active_batches: Dict[str, StagingBatch] = {}
        self._telemetry = {
            "total_rows_staged": 0,
            "total_rows_transferred": 0,
            "total_flushes": 0,
            "total_flush_time_ms": 0.0,
            "avg_flush_duration_ms": 0.0,
        }

    def _get_table_columns(self, target_table: str) -> List[str]:
        if target_table in self._column_cache:
            return self._column_cache[target_table]
        cur = self.conn.execute(f"PRAGMA table_info({target_table})")
        cols = [row[1] for row in cur.fetchall()]
        if not cols:
            raise ValueError(f"Unknown target table: {target_table}")
        self._column_cache[target_table] = cols
        return cols

    def _pk_columns(self, target_table: str) -> List[str]:
        cur = self.conn.execute(f"PRAGMA table_info({target_table})")
        return [row[1] for row in cur.fetchall() if row[5] > 0]

    def _staging_table_name(self, target_table: str) -> str:
        return f"{self.config.temp_table_prefix}{target_table}"

    def _ensure_staging_table(self, target_table: str) -> StagingBatch:
        temp_name = self._staging_table_name(target_table)
        cols = self._get_table_columns(target_table)
        col_defs = ", ".join([f'"{col}"' for col in cols])
        self.conn.execute(f"CREATE TEMP TABLE IF NOT EXISTS {temp_name} ({col_defs})")
        batch = StagingBatch(target_table, temp_name, cols)
        self._active_batches[target_table] = batch
        return batch

    def stage_rows(self, target_table: str, rows: Sequence[Dict[str, Any]]) -> int:
        if not rows:
            return 0

        batch = self._ensure_staging_table(target_table)
        valid_cols = set(batch.columns)

        for row in rows:
            for k in row.keys():
                if k not in valid_cols:
                    raise ValueError(f"Unknown column '{k}' in row for table '{target_table}'")

        ordered_cols = [c for c in batch.columns if any(c in r for r in rows)]
        col_names = ", ".join([f'"{c}"' for c in ordered_cols])
        placeholders = ", ".join(["?" for _ in ordered_cols])
        sql = f"INSERT INTO {batch.temp_table} ({col_names}) VALUES ({placeholders})"

        values = []
        for r in rows:
            values.append([r.get(c) for c in ordered_cols])

        self.conn.executemany(sql, values)
        count = len(rows)
        batch.staged_count += count
        for c in ordered_cols:
            if c not in batch.staged_columns:
                batch.staged_columns.append(c)
        self._telemetry["total_rows_staged"] += count
        _GLOBAL_TELEMETRY["total_rows_staged"] += count
        return count

    def flush(
        self,
        target_table: str,
        conflict_strategy: Optional[ConflictStrategy] = None,
        *,
        upsert_clause: Optional[str] = None,
    ) -> StagingResult:
        """Transfer the staged rows into ``target_table`` in ONE statement.

        ``upsert_clause`` is a caller-supplied ``ON CONFLICT(...) DO UPDATE SET ...``
        tail (row 1016 WIRE): product callers such as ``db.queue_bulk_upsert`` need
        the row-722 reset semantics, which neither ``OR REPLACE`` (deletes and
        re-inserts, losing untouched columns) nor ``OR IGNORE`` expresses. When it
        is given, ``conflict_strategy`` is ignored and the staged columns are
        transferred with ``INSERT ... SELECT ... WHERE true <upsert_clause>``.
        """
        temp_name = self._staging_table_name(target_table)
        strategy = conflict_strategy or self.config.conflict_strategy

        all_cols = self._get_table_columns(target_table)
        batch = self._active_batches.get(target_table)
        staged_cols = batch.staged_columns if batch and batch.staged_columns else all_cols
        is_partial = set(staged_cols) != set(all_cols)

        strategy_clause = ""
        if strategy == ConflictStrategy.REPLACE:
            strategy_clause = "OR REPLACE"
        elif strategy == ConflictStrategy.IGNORE:
            strategy_clause = "OR IGNORE"

        cur = self.conn.execute(f"SELECT count(*) FROM {temp_name}")
        staged_in_temp = cur.fetchone()[0]
        if staged_in_temp == 0:
            return StagingResult(target_table=target_table)

        if is_partial and strategy == ConflictStrategy.REPLACE and not upsert_clause:
            all_col_list = ", ".join([f'"{c}"' for c in all_cols])
            select_exprs = []
            for c in all_cols:
                qc = f'"{c}"'
                if c in staged_cols:
                    select_exprs.append(f"s.{qc}")
                else:
                    select_exprs.append(f"COALESCE(d.{qc}, s.{qc}) AS {qc}")
            select_list = ", ".join(select_exprs)
            transfer_sql = (
                f"INSERT OR REPLACE INTO {target_table} ({all_col_list}) "
                f"SELECT {select_list} FROM {temp_name} s "
                f"LEFT JOIN {target_table} d USING ({', '.join(f'\"' + c + '\"' for c in staged_cols if c in self._pk_columns(target_table))})"
            )
            if not self._pk_columns(target_table):
                col_list = ", ".join([f'"{c}"' for c in staged_cols])
                transfer_sql = (
                    f"INSERT OR REPLACE INTO {target_table} ({col_list}) "
                    f"SELECT {col_list} FROM {temp_name}"
                )
        elif upsert_clause:
            col_list = ", ".join([f'"{c}"' for c in staged_cols])
            # `WHERE true` is required: SQLite cannot otherwise parse ON CONFLICT
            # after an INSERT ... SELECT (the upsert-clause / join ambiguity).
            transfer_sql = (
                f"INSERT INTO {target_table} ({col_list}) "
                f"SELECT {col_list} FROM {temp_name} WHERE true {upsert_clause}"
            )
        else:
            col_list = ", ".join([f'"{c}"' for c in staged_cols])
            transfer_sql = (
                f"INSERT {strategy_clause} INTO {target_table} ({col_list}) "
                f"SELECT {col_list} FROM {temp_name}"
            )

        changes_before = self.conn.total_changes
        t0 = time.perf_counter()
        self.conn.execute(transfer_sql)
        transferred = self.conn.total_changes - changes_before
        self.conn.execute(f"DELETE FROM {temp_name}")
        if self.owns_connection:
            self.conn.commit()
        t_delta_ms = (time.perf_counter() - t0) * 1000.0

        self._telemetry["total_rows_transferred"] += transferred
        self._telemetry["total_flushes"] += 1
        self._telemetry["total_flush_time_ms"] += t_delta_ms
        self._telemetry["avg_flush_duration_ms"] = (
            self._telemetry["total_flush_time_ms"] / self._telemetry["total_flushes"]
        )

        _GLOBAL_TELEMETRY["total_rows_transferred"] += transferred
        _GLOBAL_TELEMETRY["total_flushes"] += 1
        _GLOBAL_TELEMETRY["total_flush_time_ms"] += t_delta_ms
        _GLOBAL_TELEMETRY["avg_flush_duration_ms"] = (
            _GLOBAL_TELEMETRY["total_flush_time_ms"] / _GLOBAL_TELEMETRY["total_flushes"]
        )

        if target_table in self._active_batches:
            self._active_batches[target_table].staged_count = 0
            self._active_batches[target_table].staged_columns = []

        return StagingResult(
            staged=staged_in_temp,
            transferred=transferred,
            errors=0,
            flush_duration_ms=t_delta_ms,
            target_table=target_table,
        )

    def ingest_batch(
        self,
        target_table: str,
        rows: Sequence[Dict[str, Any]],
        conflict_strategy: Optional[ConflictStrategy] = None,
        *,
        upsert_clause: Optional[str] = None,
    ) -> StagingResult:
        staged = self.stage_rows(target_table, rows)
        res = self.flush(target_table, conflict_strategy=conflict_strategy, upsert_clause=upsert_clause)
        res.staged = staged
        return res

    def staging_session(
        self,
        target_table: str,
        conflict_strategy: Optional[ConflictStrategy] = None,
    ) -> StagingSession:
        return StagingSession(self, target_table, conflict_strategy=conflict_strategy)

    def get_telemetry(self) -> Dict[str, Any]:
        return dict(self._telemetry)


def get_staging_pipeline(cx: Optional[sqlite3.Connection] = None) -> StagingIngestPipeline:
    if cx is None:
        from bulk_downloader.db import db_conn
        cx = db_conn()
    return StagingIngestPipeline(cx)


def get_global_staging_telemetry() -> Dict[str, Any]:
    return dict(_GLOBAL_TELEMETRY)
