from __future__ import annotations

import sqlite3
import types
from collections.abc import Sequence
from typing import Any, Self

from bulk_downloader import db as _sqlite_db
from bulk_downloader.storage.postgres_backend import PostgresBackend


class PrimaryWriteCursor:

    def __init__(self, conn: PrimaryWriteConnection, cur: Any) -> None:
        self._conn = conn
        self._cur = cur

    def execute(self, sql: str, params: Sequence[Any]=()) -> Any:
        return self._conn.execute(sql, params)

    def executemany(self, sql: str, seq: Sequence[Sequence[Any]]) -> Any:
        return self._conn.executemany(sql, seq)

    def fetchone(self) -> Any:
        return self._cur.fetchone()

    def fetchall(self) -> list[Any]:
        return self._cur.fetchall()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cur, name)

class PrimaryWriteConnection:

    def __init__(self, sqlite_cx: sqlite3.Connection, backend: PostgresBackend) -> None:
        self._sqlite_cx = sqlite_cx
        self._backend = backend

    @property
    def backend(self) -> PostgresBackend:
        return self._backend

    def execute(self, sql: str, params: Sequence[Any]=()) -> Any:
        from bulk_downloader import pg_backend
        is_mirrored = pg_backend.is_mirrored(sql)
        primary_enabled = self._backend.primary_write_enabled()
        if is_mirrored and primary_enabled:
            success, _last_id, _err = self._backend.write_authoritative(sql, params)
            if success:
                cur = self._sqlite_cx.execute(sql, params)
                return cur
            else:
                self._backend.record_fallback("connection_drop")
                cur = self._sqlite_cx.execute(sql, params)
                return cur
        elif is_mirrored:
            cur = self._sqlite_cx.execute(sql, params)
            pg_backend.mirror(sql, params, pg_backend.inserted_rowid(cur))
            return cur
        if primary_enabled and pg_backend._verb(sql) == "SELECT":
            rows = self._backend.read_authoritative(sql, params)
            if rows is not None:
                cur = self._sqlite_cx.cursor()
                return _sqlite_db._PgResultCursor(rows, cur)
        return self._sqlite_cx.execute(sql, params)

    def executemany(self, sql: str, seq: Sequence[Sequence[Any]]) -> Any:
        rows = list(seq)
        for p in rows:
            self.execute(sql, p)
        return self._sqlite_cx.cursor()

    def cursor(self) -> PrimaryWriteCursor:
        return PrimaryWriteCursor(self, self._sqlite_cx.cursor())

    def commit(self) -> None:
        self._sqlite_cx.commit()

    def rollback(self) -> None:
        self._sqlite_cx.rollback()

    def close(self) -> None:
        self._sqlite_cx.close()

    def __enter__(self) -> Self:
        self._sqlite_cx.__enter__()
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: types.TracebackType | None) -> Any:
        return self._sqlite_cx.__exit__(exc_type, exc_val, exc_tb)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._sqlite_cx, name)

class DatabaseManager:

    def __init__(self, sqlite_path: str | None=None, dsn: str | None=None) -> None:
        self.sqlite_path = sqlite_path or _sqlite_db._resolve_db_path()
        self.backend = PostgresBackend(dsn=dsn)

    def get_connection(self) -> PrimaryWriteConnection:
        cx = sqlite3.connect(self.sqlite_path)
        cx.execute("PRAGMA journal_mode=WAL")
        self._ensure_schema(cx)
        return PrimaryWriteConnection(cx, self.backend)

    def _ensure_schema(self, cx: sqlite3.Connection) -> None:
        cx.execute("CREATE TABLE IF NOT EXISTS queue ("
            "site_id TEXT, url TEXT, status TEXT DEFAULT 'pending', PRIMARY KEY (site_id, url))")
        cx.execute("CREATE TABLE IF NOT EXISTS history ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, site_id TEXT, url TEXT, status TEXT, ts REAL)")
db_conn = _sqlite_db.db_conn
db_init = _sqlite_db.db_init
async_engine = _sqlite_db.async_engine
DB_PATH = _sqlite_db.DB_PATH
