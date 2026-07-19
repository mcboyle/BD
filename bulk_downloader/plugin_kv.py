"""plugin_kv.py -- O4 (plugin-v3): shared, namespaced key/value store for plugins.

A small durable store so a plugin can keep state across runs (a cursor, a seen-set
digest, a last-poll timestamp) without inventing its own file format. Every entry
is scoped to a ``namespace`` (conventionally the plugin name) so two plugins can
use the same key without colliding.

Backend abstraction
-------------------
* **SQLite (default).** BulkDownloader is SQLite-only as of v3.65.1, so the store
  lives in a ``plugin_kv`` table in the app database (created on first use via
  ``db.db_conn``). Fully sandbox + stash deployable; no extra dependency.
* **Postgres (optional).** When an explicit ``dsn`` is passed to ``PluginKV`` /
  ``for_namespace`` AND a psycopg driver is importable, the store targets the
  datastores-kit Postgres instead. Absent either, it transparently falls back to
  SQLite -- the store is NEVER a hard Postgres dependency, and there is NO ``BD_*``
  env var selecting it (an open governance surface is deliberately avoided; cf.
  the plugin-interpreter correction). The PG backend is the retro-backing target
  for R2 quarantine persistence + K6 source state once a cluster deployment
  passes a DSN in.

Values are JSON-serialized, so any JSON-able Python value round-trips. Best-effort
durability: a backend write failure is swallowed and logged (state persistence is
a nicety, never a correctness gate) -- matching the rest of the plugin subsystem.
"""
from __future__ import annotations

import json
import sys
import time
from typing import Any, List, Optional, Tuple

_TABLE = "plugin_kv"
_MISSING = object()


def backend(dsn: Optional[str] = None) -> str:
    """Return the active backend name: ``"postgres"`` or ``"sqlite"``.

    Postgres is selected ONLY when an explicit ``dsn`` is passed AND a psycopg
    driver imports; otherwise SQLite (the always-available default). Selection is
    explicit/programmatic on purpose -- there is deliberately NO env var for it
    (that would be an open governance surface, matching the plugin-interpreter
    correction). A future cluster wiring passes the DSN in directly."""
    if dsn and str(dsn).strip():
        for mod in ("psycopg", "psycopg2"):
            try:
                __import__(mod)
                return "postgres"
            except Exception:
                continue
    return "sqlite"


# ── SQLite backend (default) ──────────────────────────────────────────
def _sqlite_conn():
    from . import db
    return db.db_conn()


def _sqlite_init() -> None:
    try:
        with _sqlite_conn() as cx:
            cx.execute(
                f"CREATE TABLE IF NOT EXISTS {_TABLE}("
                "namespace TEXT NOT NULL, key TEXT NOT NULL, value TEXT,"
                "updated_at TEXT DEFAULT(strftime('%Y-%m-%dT%H:%M:%S','now')),"
                "PRIMARY KEY(namespace, key))")
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[plugin_kv] sqlite init failed: {e}\n")


def _pg_conn(dsn: str):
    try:
        import psycopg  # type: ignore
        return psycopg.connect(dsn)
    except Exception:
        import psycopg2  # type: ignore
        return psycopg2.connect(dsn)


def _pg_init(dsn: str) -> None:
    try:
        conn = _pg_conn(dsn)
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"CREATE TABLE IF NOT EXISTS {_TABLE}("
                    "namespace TEXT NOT NULL, key TEXT NOT NULL, value TEXT,"
                    "updated_at TIMESTAMPTZ DEFAULT now(),"
                    "PRIMARY KEY(namespace, key))")
        conn.close()
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[plugin_kv] postgres init failed: {e}\n")


class PluginKV:
    """A namespaced handle on the shared KV store.

    All operations are scoped to ``namespace``. Values are JSON-serialized; any
    JSON-able value round-trips. Read methods fail-soft to an empty result; write
    methods fail-soft to a no-op (and are logged) -- a store outage never raises
    into the plugin call path.
    """

    def __init__(self, namespace: str, dsn: Optional[str] = None):
        self.namespace = str(namespace)
        self._dsn = dsn
        self._backend = backend(dsn)
        if self._backend == "postgres":
            _pg_init(dsn)
        else:
            _sqlite_init()

    # -- internal exec helpers (param-style differs per backend) --------
    def _is_pg(self) -> bool:
        return self._backend == "postgres"

    def set(self, key: str, value: Any) -> None:
        """Store ``value`` (JSON-serialized) under ``key`` in this namespace."""
        blob = json.dumps(value)
        try:
            if self._is_pg():
                conn = _pg_conn(self._dsn)
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            f"INSERT INTO {_TABLE}(namespace,key,value) "
                            "VALUES(%s,%s,%s) ON CONFLICT(namespace,key) "
                            "DO UPDATE SET value=EXCLUDED.value, updated_at=now()",
                            (self.namespace, str(key), blob))
                conn.close()
            else:
                with _sqlite_conn() as cx:
                    cx.execute(
                        f"INSERT INTO {_TABLE}(namespace,key,value,updated_at) "
                        "VALUES(?,?,?,?) ON CONFLICT(namespace,key) "
                        "DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                        (self.namespace, str(key), blob,
                         time.strftime("%Y-%m-%dT%H:%M:%S")))
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[plugin_kv] set({self.namespace}:{key}) failed: {e}\n")

    def get(self, key: str, default: Any = None) -> Any:
        """Return the value for ``key`` (JSON-decoded) or ``default`` if absent."""
        try:
            if self._is_pg():
                conn = _pg_conn(self._dsn)
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            f"SELECT value FROM {_TABLE} WHERE namespace=%s AND key=%s",
                            (self.namespace, str(key)))
                        row = cur.fetchone()
                conn.close()
                raw = row[0] if row else _MISSING
            else:
                with _sqlite_conn() as cx:
                    row = cx.execute(
                        f"SELECT value FROM {_TABLE} WHERE namespace=? AND key=?",
                        (self.namespace, str(key))).fetchone()
                raw = row[0] if row else _MISSING
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[plugin_kv] get({self.namespace}:{key}) failed: {e}\n")
            return default
        if raw is _MISSING:
            return default
        try:
            return json.loads(raw)
        except Exception:
            return default

    def delete(self, key: str) -> bool:
        """Remove ``key`` from this namespace. Returns True iff a row was removed."""
        try:
            if self._is_pg():
                conn = _pg_conn(self._dsn)
                removed = 0
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            f"DELETE FROM {_TABLE} WHERE namespace=%s AND key=%s",
                            (self.namespace, str(key)))
                        removed = cur.rowcount
                conn.close()
                return removed > 0
            with _sqlite_conn() as cx:
                cur = cx.execute(
                    f"DELETE FROM {_TABLE} WHERE namespace=? AND key=?",
                    (self.namespace, str(key)))
                return cur.rowcount > 0
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[plugin_kv] delete({self.namespace}:{key}) failed: {e}\n")
            return False

    def keys(self) -> List[str]:
        """All keys in this namespace (sorted)."""
        try:
            if self._is_pg():
                conn = _pg_conn(self._dsn)
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            f"SELECT key FROM {_TABLE} WHERE namespace=%s ORDER BY key",
                            (self.namespace,))
                        rows = cur.fetchall()
                conn.close()
                return [r[0] for r in rows]
            with _sqlite_conn() as cx:
                rows = cx.execute(
                    f"SELECT key FROM {_TABLE} WHERE namespace=? ORDER BY key",
                    (self.namespace,)).fetchall()
            return [r[0] for r in rows]
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[plugin_kv] keys({self.namespace}) failed: {e}\n")
            return []

    def items(self) -> List[Tuple[str, Any]]:
        """All (key, value) pairs in this namespace (JSON-decoded)."""
        out: List[Tuple[str, Any]] = []
        for k in self.keys():
            out.append((k, self.get(k)))
        return out

    def clear(self) -> int:
        """Remove every key in this namespace. Returns the count removed."""
        try:
            if self._is_pg():
                conn = _pg_conn(self._dsn)
                removed = 0
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            f"DELETE FROM {_TABLE} WHERE namespace=%s",
                            (self.namespace,))
                        removed = cur.rowcount
                conn.close()
                return removed
            with _sqlite_conn() as cx:
                cur = cx.execute(
                    f"DELETE FROM {_TABLE} WHERE namespace=?", (self.namespace,))
                return cur.rowcount
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[plugin_kv] clear({self.namespace}) failed: {e}\n")
            return 0


def for_namespace(namespace: str, dsn: Optional[str] = None) -> PluginKV:
    """Factory: a :class:`PluginKV` handle scoped to ``namespace`` (optionally
    Postgres-backed via an explicit ``dsn``)."""
    return PluginKV(namespace, dsn=dsn)
