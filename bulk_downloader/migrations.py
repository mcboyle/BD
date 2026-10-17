"""Schema migration helpers (Phase 155, Block Q).

BD's many modules add columns/tables incrementally. After enough
versions, the schema state is hard to reason about. This module
provides:

  • detect_drift() — compare actual DB schema to a manifest of expected
    tables/columns. Reports missing or unexpected items.
  • migrate(version_from, version_to) — apply known migration scripts
    in order. Idempotent; safe to re-run.
  • apply_pending() — auto-run any pending migrations.
  • schema_history — table tracking which versions have been applied.

This is NOT a heavyweight ORM (Alembic, etc.). Just a lightweight
ledger so we can be sure BD's DB matches the code version.
"""
from __future__ import annotations

from contextlib import contextmanager
import enum
import os
import sqlite3
import sys
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple


class BarrierState(str, enum.Enum):
    """Lifecycle states for the DDL Locking Barrier."""
    IDLE = "IDLE"
    DRAINING = "DRAINING"
    MIGRATING = "MIGRATING"
    COMMITTED = "COMMITTED"
    DRAIN_FAILED = "DRAIN_FAILED"
    ABORTED = "ABORTED"


def _evict_idle_connections() -> None:
    """Evict and close any idle thread-local connection holding half-open read transactions.
    Mirrors db.py O1164/FLEET_RULE 47 protocol to resolve SQLITE_LOCKED on sqlite_master."""
    try:
        from . import db as _db
        idle = getattr(_db._DB_CONN_LOCAL, "idle", None)
        if idle is not None:
            _db._DB_CONN_LOCAL.idle = None
            try:
                idle[1].rollback()
            except Exception:
                pass
            _db._close_history_conn(idle[1])
    except Exception:
        pass


class DDLLockingBarrier:
    """Zero-Downtime Schema Migration & DDL Locking Barrier (Row 978).

    Coordinates schema evolution DDL operations with concurrent readers/writers:
    1. Drains in-flight database borrower leases before executing DDL.
    2. Provides bounded adaptive retries with exponential backoff on SQLITE_LOCKED/busy.
    3. Flushes idle thread-local connections to eliminate shared-cache table lock contention.
    4. Tracks barrier telemetry and migration lifecycle states.
    """

    def __init__(
        self,
        drain_timeout: float = 2.0,
        barrier_timeout: float = 5.0,
        retry_interval: float = 0.01,
        max_retry_interval: float = 0.5,
    ) -> None:
        self.drain_timeout = float(drain_timeout)
        self.barrier_timeout = float(barrier_timeout)
        self.retry_interval = float(retry_interval)
        self.max_retry_interval = float(max_retry_interval)
        self._state = BarrierState.IDLE
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._active_leases: Dict[str, float] = {}
        self._lease_counter = 0

    @property
    def state(self) -> BarrierState:
        with self._lock:
            return self._state

    @property
    def active_leases_count(self) -> int:
        with self._lock:
            return len(self._active_leases)

    def is_idle(self) -> bool:
        with self._lock:
            return self._state in (BarrierState.IDLE, BarrierState.COMMITTED)

    def acquire_lease(self, owner: str = "") -> str:
        """Register an active reader/writer lease."""
        with self._lock:
            if self._state in (BarrierState.DRAINING, BarrierState.MIGRATING):
                raise RuntimeError(
                    f"Cannot acquire lease: barrier is {self._state.value}"
                )
            self._lease_counter += 1
            token = f"lease_{self._lease_counter}_{owner}_{time.time()}"
            self._active_leases[token] = time.time()
            return token

    def release_lease(self, token: str) -> None:
        """Release a previously acquired lease and notify barrier waiters."""
        with self._lock:
            self._active_leases.pop(token, None)
            if not self._active_leases:
                self._cond.notify_all()

    def drain_in_flight(self, timeout: Optional[float] = None) -> bool:
        """Wait for active in-flight leases to clear before entering migration DDL phase."""
        t_limit = self.drain_timeout if timeout is None else float(timeout)
        deadline = time.time() + t_limit
        _evict_idle_connections()

        with self._lock:
            self._state = BarrierState.DRAINING
            while self._active_leases and time.time() < deadline:
                remaining = max(0.001, deadline - time.time())
                self._cond.wait(timeout=remaining)
            drained = len(self._active_leases) == 0
            _evict_idle_connections()
            if not drained:
                self._state = BarrierState.DRAIN_FAILED
            return drained

    @contextmanager
    def barrier_context(self, drain: bool = True, timeout: Optional[float] = None):
        """Context manager bracketing a DDL migration batch within the barrier."""
        with self._lock:
            if drain:
                drained = self.drain_in_flight(timeout=self.drain_timeout if timeout is None else timeout)
                if not drained:
                    self._state = BarrierState.DRAIN_FAILED
                    raise RuntimeError(
                        f"DDL locking barrier drain failed: {len(self._active_leases)} active lease(s) still held"
                    )
            self._state = BarrierState.MIGRATING
        try:
            yield self
        except Exception:
            with self._lock:
                if self._state != BarrierState.DRAIN_FAILED:
                    self._state = BarrierState.ABORTED
            raise
        else:
            with self._lock:
                self._state = BarrierState.COMMITTED
        finally:
            _evict_idle_connections()
            with self._lock:
                self._cond.notify_all()

    def execute_ddl_guarded(
        self,
        cx: Any,
        ddl_callable_or_sql: Any,
        *args: Any,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> Tuple[Any, int]:
        """Execute a DDL callable or SQL query under adaptive lock contention retry."""
        limit = self.barrier_timeout if timeout is None else float(timeout)
        deadline = time.time() + limit
        interval = self.retry_interval
        retries = 0

        while True:
            try:
                if callable(ddl_callable_or_sql):
                    result = ddl_callable_or_sql(cx, *args, **kwargs)
                elif isinstance(ddl_callable_or_sql, str):
                    result = cx.execute(ddl_callable_or_sql, *args, **kwargs)
                else:
                    raise TypeError(f"Expected callable or SQL string, got {type(ddl_callable_or_sql)}")
                return result, retries
            except sqlite3.OperationalError as exc:
                err_msg = str(exc).lower()
                if ("locked" not in err_msg and "busy" not in err_msg) or time.time() >= deadline:
                    raise
                retries += 1
                _evict_idle_connections()
                time.sleep(interval)
                interval = min(interval * 1.5, self.max_retry_interval)


_GLOBAL_BARRIER: Optional[DDLLockingBarrier] = None
_BARRIER_LOCK = threading.Lock()


def get_barrier(
    drain_timeout: float = 2.0,
    barrier_timeout: float = 5.0,
) -> DDLLockingBarrier:
    """Return the shared DDLLockingBarrier instance."""
    global _GLOBAL_BARRIER
    with _BARRIER_LOCK:
        if _GLOBAL_BARRIER is None:
            _GLOBAL_BARRIER = DDLLockingBarrier(
                drain_timeout=drain_timeout,
                barrier_timeout=barrier_timeout,
            )
        return _GLOBAL_BARRIER


def reset_barrier() -> None:
    """Test helper. Reset the global barrier singleton."""
    global _GLOBAL_BARRIER
    with _BARRIER_LOCK:
        _GLOBAL_BARRIER = None


_MIGRATIONS: list = []

def migration(version: int, name: str="", down_fn: Optional[Callable]=None):

    def decorator(fn: Callable):
        for m in _MIGRATIONS:
            if m["version"] == int(version):
                m["name"] = name or fn.__name__
                m["fn"] = fn
                if down_fn is not None:
                    m["down_fn"] = down_fn
                return fn
        _MIGRATIONS.append({"version": int(version), "name": name or fn.__name__, "fn": fn, "down_fn": down_fn})
        return fn
    return decorator

def downgrade(version: int):

    def decorator(fn: Callable):
        for m in _MIGRATIONS:
            if m["version"] == int(version):
                m["down_fn"] = fn
                return fn
        _MIGRATIONS.append({"version": int(version), "name": f"migration_{version}", "fn": None, "down_fn": fn})
        return fn
    return decorator


def _ensure_history_table():
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS schema_migrations(
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at REAL NOT NULL,
                duration_ms REAL,
                success INTEGER NOT NULL,
                error TEXT
            )""")
    except Exception as e:
        sys.stderr.write(f"[migrations] history table: {e}\n")


def applied_versions() -> set:
    _ensure_history_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute("""SELECT version FROM schema_migrations
                                  WHERE success = 1""").fetchall()
        return {int(r[0] if not hasattr(r, "keys") else r["version"])
                for r in rows}
    except Exception:
        return set()


def pending_migrations() -> list:
    """Migrations not yet applied (sorted ascending by version)."""
    done = applied_versions()
    out = [m for m in _MIGRATIONS if m["version"] not in done]
    return sorted(out, key=lambda m: m["version"])


def apply_pending(
    *,
    dry_run: bool = False,
    backup_first: bool = True,
    zero_downtime: bool = True,
    barrier_timeout: float = 5.0,
    drain_timeout: float = 2.0,
) -> dict:
    """Apply any unapplied migrations in order.

    v3.48 (#44): dry_run now executes migrations against an IN-MEMORY
    snapshot of the schema (CREATE TABLE clones, not rows) to verify
    they'd actually succeed before committing to the real DB. Returns
    a per-migration result with `would_succeed: bool` and the resulting
    schema changes.

    Row 978: zero_downtime (default True) executes migrations under the
    DDLLockingBarrier, which coordinates borrower lease draining and
    bounded adaptive retry on table lock contention (SQLITE_LOCKED).
    """
    pend = pending_migrations()
    out: dict[str, Any] = {"considered": len(pend), "applied": 0, "errors": 0, "dry_run": dry_run, "results": []}
    if not pend:
        return out
    if dry_run:
        # v3.48 (#44): real dry-run — apply each migration against an
        # in-memory replica of the live schema and report what would
        # change. Catches "migration X tries to add a column that
        # already exists" / "renames a table not present" before they
        # land in production.
        import sqlite3 as _sqlite3
        from . import db as _db
        try:
            # Snapshot current schema into RAM via sqlite3 :memory:
            mem = _sqlite3.connect(":memory:")
            with _db.db_conn() as live:
                for row in live.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE sql NOT NULL AND type IN ('table','index')"
                ).fetchall():
                    if row[0]:
                        try: mem.execute(row[0])
                        except _sqlite3.OperationalError: pass
            for m in pend:
                started = time.time()
                pre_objects = _snapshot_schema(mem)
                try:
                    m["fn"](mem)
                    post_objects = _snapshot_schema(mem)
                    out["results"].append({
                        "version": m["version"],
                        "name": m["name"],
                        "would_succeed": True,
                        "duration_ms": (time.time() - started) * 1000,
                        "added_tables": sorted(
                            post_objects["tables"] - pre_objects["tables"]),
                        "added_columns": _column_diff(
                            pre_objects["columns"], post_objects["columns"]),
                    })
                except Exception as e:
                    out["results"].append({
                        "version": m["version"],
                        "name": m["name"],
                        "would_succeed": False,
                        "error": str(e)[:300],
                    })
                    out["errors"] += 1
                    # Don't apply further — later migrations may depend
                    # on the failed one's effects
                    break
            mem.close()
        except Exception as e:
            out["error"] = f"dry-run setup failed: {e}"
        return out

    # hub-db-corruption-rootcause r1: never back up or migrate a damaged DB.
    # The backup is VACUUM INTO, which rebuilds pages from the logical rows and
    # drops broken index btrees, so a clean .premigration.bak hid that the 1904
    # hub DB was already malformed when migration 12 ran on it.
    refusal = _integrity_refusal()
    if refusal:
        out["aborted"] = True
        out["abort_reason"] = ("pre-migration integrity gate: %s; no backup taken, "
                               "no migrations were applied" % refusal)
        sys.stderr.write("[migrations] REFUSED: %s\n" % out["abort_reason"])
        return out

    # ── ROB-2: crash-safe real apply ─────────────────────────────────
    # (1) PREFLIGHT GATE: run the in-memory dry-run first. If ANY pending
    #     migration would fail, abort WITHOUT applying anything, so a bad
    #     migration can never leave a half-migrated DB that wedges startup.
    # (2) BACKUP: copy the live DB aside so a data-dependent failure the
    #     schema-only dry-run can't catch is recoverable (abort-to-prior).
    #     Taken in _execute_migrations, inside the barrier (mif-backup-window).
    if backup_first:
        preflight = apply_pending(dry_run=True, backup_first=False)
        if preflight.get("errors"):
            out["aborted"] = True
            out["abort_reason"] = ("migration dry-run preflight found a migration "
                                   "that would fail; no migrations were applied")
            out["preflight"] = preflight.get("results", [])
            return out

    from . import db as _db
    barrier = get_barrier(drain_timeout=drain_timeout, barrier_timeout=barrier_timeout) if zero_downtime else None
    total_retries = 0

    # mif-backup-window: the drain excludes no writer (nothing else takes a
    # barrier lease), so a commit can land between the backup and a failing
    # migration, and abort-to-prior would discard it. The backup is read through
    # the pooled handle the migrations then reuse; PRAGMA data_version on that
    # handle moves only for OTHER connections' commits. Moved, or the handle was
    # replaced -> the restore is refused and the late rows are kept.
    pin: dict[str, Any] = {}

    def _take_backup():
        with _db.db_conn() as cx:
            dv = cx.execute("PRAGMA data_version").fetchone()[0]
            bak = _backup_db_before_migration(source=cx)
        if bak:
            out["backup"] = bak
            pin.update(cx=cx, data_version=dv)

    def _foreign_commit_since_backup():
        """None when the pinned handle proves no other connection committed."""
        with _db.db_conn() as cx:
            if cx is not pin.get("cx"):
                return "pooled handle replaced since the backup; cannot prove no other commit"
            dv = cx.execute("PRAGMA data_version").fetchone()[0]
        if dv != pin["data_version"]:
            return "PRAGMA data_version moved since the backup: another connection committed"
        return None

    def _execute_migrations():
        nonlocal total_retries
        if backup_first:
            _take_backup()
        for m in pend:
            started = time.time()
            try:
                with _db.db_conn() as cx:
                    if barrier:
                        _, retries = barrier.execute_ddl_guarded(cx, m["fn"], timeout=barrier_timeout)
                        total_retries += retries
                    else:
                        m["fn"](cx)
                    cx.execute("""INSERT INTO schema_migrations(
                        version, name, applied_at, duration_ms, success
                    ) VALUES (?,?,?,?,1)""",
                        (m["version"], m["name"], time.time(),
                         (time.time() - started) * 1000))
                out["applied"] += 1
                out["results"].append({"version": m["version"],
                                       "name": m["name"], "ok": True})
            except Exception as e:
                refused = None
                if backup_first and out.get("backup"):
                    try:
                        refused = _foreign_commit_since_backup()
                    except Exception as chk:
                        refused = "data_version check failed: %s" % chk
                try:
                    with _db.db_conn() as cx:
                        cx.execute("""INSERT INTO schema_migrations(
                            version, name, applied_at, duration_ms, success, error
                        ) VALUES (?,?,?,?,0,?)""",
                            (m["version"], m["name"], time.time(),
                             (time.time() - started) * 1000, str(e)[:300]))
                except Exception:
                    pass
                out["errors"] += 1
                out["results"].append({"version": m["version"],
                                       "name": m["name"],
                                       "ok": False, "error": str(e)[:200]})
                # ROB-2: a migration failed at apply time despite passing the
                # dry-run preflight (a data-dependent failure). Restore the
                # pre-migration DB so we don't leave a half-migrated state.
                if backup_first and out.get("backup"):
                    if refused:
                        out["restore_refused"] = refused
                    elif _restore_db_from_backup(out["backup"]):
                        out["restored_to_prior"] = True
                # Stop on first error — later migrations might depend on
                # this one's columns
                break

    if barrier:
        drain_start = time.time()
        try:
            with barrier.barrier_context(drain=True, timeout=drain_timeout):
                _execute_migrations()
        except RuntimeError as exc:
            if "drain failed" in str(exc).lower():
                drain_dur = (time.time() - drain_start) * 1000
                out["aborted"] = True
                out["abort_reason"] = str(exc)
                out["barrier_telemetry"] = {
                    "zero_downtime": True,
                    "drained": False,
                    "barrier_state": barrier.state.value if hasattr(barrier.state, "value") else str(barrier.state),
                    "leases_outstanding": barrier.active_leases_count,
                    "drain_duration_ms": drain_dur,
                    "retries": 0,
                }
                return out
            raise
        drain_dur = (time.time() - drain_start) * 1000
        out["barrier_telemetry"] = {
            "zero_downtime": True,
            "drained": True,
            "barrier_state": barrier.state.value if hasattr(barrier.state, "value") else str(barrier.state),
            "leases_outstanding": barrier.active_leases_count,
            "drain_duration_ms": drain_dur,
            "retries": total_retries,
        }
    else:
        _execute_migrations()

    return out


def downgrade_to(
    target_version: int,
    *,
    backup_first: bool = True,
    zero_downtime: bool = True,
    barrier_timeout: float = 5.0,
    drain_timeout: float = 2.0,
) -> dict:
    """Revert applied migrations down to target_version (exclusive).

    Migrations with version > target_version are reverted in descending order.
    """
    applied = applied_versions()
    to_revert = [m for m in _MIGRATIONS if m["version"] in applied and m["version"] > target_version]
    to_revert = sorted(to_revert, key=lambda m: m["version"], reverse=True)

    out = {"considered": len(to_revert), "reverted": 0, "errors": 0, "results": []}
    if not to_revert:
        return out

    from . import db as _db
    barrier = get_barrier(drain_timeout=drain_timeout, barrier_timeout=barrier_timeout) if zero_downtime else None
    total_retries = 0

    if backup_first:
        try:
            bak = _backup_db_before_migration()
            if bak:
                out["backup"] = bak
        except Exception:
            pass

    def _execute_downgrades():
        nonlocal total_retries
        for m in to_revert:
            started = time.time()
            down_fn = m.get("down_fn")
            if not callable(down_fn):
                err = f"Migration v{m['version']} ({m['name']}) has no callable down_fn"
                out["errors"] += 1
                out["results"].append({"version": m["version"], "name": m["name"], "ok": False, "error": err})
                break
            try:
                with _db.db_conn() as cx:
                    if barrier:
                        _, retries = barrier.execute_ddl_guarded(cx, down_fn, timeout=barrier_timeout)
                        total_retries += retries
                    else:
                        down_fn(cx)
                    cx.execute("DELETE FROM schema_migrations WHERE version = ?", (m["version"],))
                out["reverted"] += 1
                out["results"].append({
                    "version": m["version"],
                    "name": m["name"],
                    "ok": True,
                    "duration_ms": (time.time() - started) * 1000,
                })
            except Exception as e:
                out["errors"] += 1
                out["results"].append({
                    "version": m["version"],
                    "name": m["name"],
                    "ok": False,
                    "error": str(e)[:300],
                })
                break

    if barrier:
        drain_start = time.time()
        try:
            with barrier.barrier_context(drain=True, timeout=drain_timeout):
                _execute_downgrades()
        except RuntimeError as exc:
            if "drain failed" in str(exc).lower():
                drain_dur = (time.time() - drain_start) * 1000
                out["aborted"] = True
                out["abort_reason"] = str(exc)
                out["barrier_telemetry"] = {
                    "zero_downtime": True,
                    "drained": False,
                    "barrier_state": barrier.state.value if hasattr(barrier.state, "value") else str(barrier.state),
                    "leases_outstanding": barrier.active_leases_count,
                    "drain_duration_ms": drain_dur,
                    "retries": 0,
                }
                return out
            raise
        drain_dur = (time.time() - drain_start) * 1000
        out["barrier_telemetry"] = {
            "zero_downtime": True,
            "drained": True,
            "barrier_state": barrier.state.value if hasattr(barrier.state, "value") else str(barrier.state),
            "leases_outstanding": barrier.active_leases_count,
            "drain_duration_ms": drain_dur,
            "retries": total_retries,
        }
    else:
        _execute_downgrades()

    return out


def step_down(target_version: Optional[int] = None) -> dict:
    """Step down by one version, or to target_version."""
    applied = sorted(applied_versions())
    if not applied:
        return {"considered": 0, "reverted": 0, "errors": 0, "results": []}
    if target_version is None:
        target = applied[-2] if len(applied) >= 2 else 0
    else:
        target = target_version
    return downgrade_to(target, backup_first=False)


def step_up(target_version: Optional[int] = None) -> dict:
    """Step up by one version, or to target_version."""
    pend = pending_migrations()
    if not pend:
        return {"considered": 0, "applied": 0, "errors": 0, "results": []}
    if target_version is not None:
        target_v = target_version
    else:
        target_v = pend[0]["version"]

    saved = list(_MIGRATIONS)
    try:
        _MIGRATIONS[:] = [m for m in saved if m["version"] <= target_v]
        return apply_pending(backup_first=False)
    finally:
        _MIGRATIONS[:] = saved


def _integrity_refusal() -> Optional[str]:
    """None when PRAGMA integrity_check reads exactly 'ok', else why not.

    quick_check is not enough: it skips index-vs-table content, and the
    quarantined hub DB passed quick_check with two index roots overwritten
    by another table's leaf page. A check that cannot run is a refusal too."""
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = [r[0] for r in cx.execute("PRAGMA integrity_check").fetchall()]
    except Exception as e:
        return "integrity_check raised %s: %s" % (type(e).__name__, e)
    if rows == ["ok"]:
        return None
    return "integrity_check: " + "; ".join(str(r) for r in rows[:3])


def _backup_db_before_migration(source=None):
    """ROB-2: copy the live DB aside before migrations mutate it, returning
    the backup path (``<db>.premigration.bak``) or None if there is no DB file /
    the copy failed. Best-effort and safe at boot.

    migrations-integrity-fix r2: the copy used to be wal_checkpoint(TRUNCATE) +
    shutil.copy2 of the main file. An open reader makes that checkpoint busy (its
    rc was swallowed), so commits still in <db>-wal were missing from the .bak and
    a restore discarded them. The pages now come through SQLite's online backup
    API, which reads the committed state including the WAL, into a temp file that
    replaces the .bak only once complete.

    r3 (train-4 eject): the copy is VACUUM INTO on a db.db_conn() lease -- the same
    committed-state read, but the history DB is opened only through the MOD-3 seam
    and nothing but the history DB is ever handed to sqlite3.connect.

    mif-backup-window: `source` is the open seam connection to copy through
    (apply_pending passes the pooled handle its migrations run on); default leases one."""
    try:
        from . import db as _db
        src = _db._resolve_db_path()
    except Exception:
        return None
    if not src or not os.path.exists(src):
        return None
    dst = src + ".premigration.bak"
    tmp = dst + ".tmp"
    try:
        if os.path.exists(tmp):
            os.unlink(tmp)
        def _copy(cx):
            # A handle that is not the history file (inmemory_sqlite's shared-cache
            # DB) has nothing of src to copy; the restore would then retry forever.
            opened = cx.execute("PRAGMA database_list").fetchone()[2]
            if not opened or not os.path.samefile(opened, src):
                raise sqlite3.OperationalError("seam handle is not %s" % src)
            cx.execute("VACUUM INTO ?", (tmp,))

        if source is not None:
            _copy(source)
        else:
            with _db.db_conn() as cx:
                _copy(cx)
        os.replace(tmp, dst)
        return dst
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return None


def _restore_db_from_backup(bak: str) -> bool:
    """ROB-2: restore the live DB from a pre-migration backup. Returns True only
    when the live DB's schema reads back as the backup's. Best-effort; safe at boot.

    staging-integrity F3: a file copy over the live DB left <db>-wal/-shm behind and
    their frames won, so the migrated schema and ledger survived a reported restore.
    The pages now go through SQLite's online backup API (the live DB's own journal),
    after the pooled handles that saw the migrated state are closed.

    r3 (train-4 eject): the live DB is a db.db_conn() lease; only the .bak is opened
    directly. r7: through db.guarded_connect, so a .bak on a network mount is refused (returns False)."""
    try:
        from . import db as _db
        if not (bak and os.path.exists(bak)):
            return False
        _db.close_all_pooled_connections()
        schema = "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
        source = _db.guarded_connect(bak, sqlite3_module=sqlite3)  # r7: the .bak sits beside the live DB; same history guard
        try:
            # backup() retries SQLITE_LOCKED forever, so a source that resolves to the
            # live DB (inmemory_sqlite's shared cache) would hang boot. Only the .bak.
            opened = source.execute("PRAGMA database_list").fetchone()[2]
            if not opened or not os.path.samefile(opened, bak):
                return False
            with _db.db_conn() as live:
                # The backup API needs the sqlite3 handle; _DualWriteConn proxies one.
                source.backup(getattr(live, "_cx", live))
                # The seam's row_factory is sqlite3.Row, which never equals a tuple.
                return ([tuple(r) for r in live.execute(schema).fetchall()]
                        == source.execute(schema).fetchall())
        finally:
            source.close()
    except Exception:
        return False


def _snapshot_schema(cx) -> dict:
    out: dict[str, Any] = {"tables": set(), "columns": {}}
    for row in cx.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        t = row[0]
        out["tables"].add(t)
        cols = set()
        try:
            for c in cx.execute(f"PRAGMA table_info({t})").fetchall():
                cols.add(c[1])
        except Exception:
            pass
        out["columns"][t] = cols
    return out


def _column_diff(pre: dict, post: dict) -> dict:
    """Return {table: [added cols]} for any table whose column set grew."""
    diff = {}
    for t, post_cols in post.items():
        pre_cols = pre.get(t, set())
        added = sorted(post_cols - pre_cols)
        if added:
            diff[t] = added
    return diff


# ─── Drift detection ──────────────────────────────────────────────────

# Manifest of expected tables + their key columns. This is a
# convenience surface — not the canonical schema, which lives in each
# module's _ensure_table(). The intent is: "if you're missing one of
# these, something's wrong."
EXPECTED_SCHEMA = {
    "history": ["id", "site_id", "site_name", "url", "status",
                "filename", "file_size", "message", "ts"],
    # Phase modules add their own tables; this manifest is for the
    # core surface — others are checked via "if it exists, what does
    # it have?"
}


def detect_drift() -> dict:
    out: dict[str, Any] = {"ok": True, "missing_tables": [], "missing_columns": {}, "extra_tables": []}
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            actual_tables = {
                r[0] if not hasattr(r, "keys") else r["name"]
                for r in cx.execute(
                    """SELECT name FROM sqlite_master
                       WHERE type='table'""").fetchall()
                if not (r[0] if not hasattr(r, "keys") else r["name"]).startswith("sqlite_")
            }
            for table, cols in EXPECTED_SCHEMA.items():
                if table not in actual_tables:
                    out["missing_tables"].append(table)
                    out["ok"] = False
                    continue
                actual_cols = {
                    r[1] for r in cx.execute(
                        f'PRAGMA table_info("{table}")').fetchall()
                }
                missing = [c for c in cols if c not in actual_cols]
                if missing:
                    out["missing_columns"][table] = missing
                    out["ok"] = False
            # Extra tables aren't a problem — many phases add them.
            # Just list for visibility.
            out["extra_tables"] = sorted(
                actual_tables - set(EXPECTED_SCHEMA.keys()))
    except Exception as e:
        out["ok"] = False
        out["error"] = str(e)[:200]
    return out


def status() -> dict:
    """One-shot snapshot of migration state."""
    _ensure_history_table()
    return {
        "registered_migrations": len(_MIGRATIONS),
        "applied_versions": sorted(applied_versions()),
        "pending": [{"version": m["version"], "name": m["name"]}
                    for m in pending_migrations()],
        "drift": detect_drift(),
    }


def reset_registry():
    """Test helper. Clears in-memory migration list."""
    _MIGRATIONS.clear()


# ─── Built-in migrations ──────────────────────────────────────────────
# These represent forward fixes for schema issues found in earlier
# versions. Add new ones here when needed.

@migration(version=1, name="ensure_history_indexes")
def _m1(cx):
    cx.execute("CREATE INDEX IF NOT EXISTS idx_history_status ON history(status)")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_history_site ON history(site_id)")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_history_ts ON history(ts)")

@downgrade(version=1)
def _down_m1(cx):
    cx.execute("DROP INDEX IF EXISTS idx_history_status")
    cx.execute("DROP INDEX IF EXISTS idx_history_site")
    cx.execute("DROP INDEX IF EXISTS idx_history_ts")

@migration(version=2, name="add_history_retry_after")
def _m2(cx):
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    if "retry_after" not in cols:
        cx.execute("ALTER TABLE history ADD COLUMN retry_after TEXT DEFAULT NULL")
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "_downgrade_history_retry_after_archive" in tables:
        cx.execute("""
            UPDATE history
            SET retry_after = (
                SELECT retry_after FROM _downgrade_history_retry_after_archive
                WHERE _downgrade_history_retry_after_archive.id = history.id
            )
            WHERE id IN (SELECT id FROM _downgrade_history_retry_after_archive)
        """)

@downgrade(version=2)
def _down_m2(cx):
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    if "retry_after" not in cols:
        return
    cx.execute("""
        CREATE TABLE IF NOT EXISTS _downgrade_history_retry_after_archive (
            id INTEGER PRIMARY KEY,
            retry_after TEXT,
            archived_at REAL DEFAULT (strftime('%s', 'now'))
        )
    """)
    cx.execute("""
        INSERT OR REPLACE INTO _downgrade_history_retry_after_archive (id, retry_after)
        SELECT id, retry_after FROM history WHERE retry_after IS NOT NULL
    """)
    try:
        cx.execute("ALTER TABLE history DROP COLUMN retry_after")
    except Exception:
        cols_keep = [c for c in cols if c != "retry_after"]
        cols_csv = ", ".join((f'"{c}"' for c in cols_keep))
        cx.execute(f"CREATE TABLE history_recreate_tmp AS SELECT {cols_csv} FROM history WHERE 0")
        cx.execute(f"INSERT INTO history_recreate_tmp ({cols_csv}) SELECT {cols_csv} FROM history")
        cx.execute("DROP TABLE history")
        cx.execute("ALTER TABLE history_recreate_tmp RENAME TO history")

@migration(version=3, name="create_library_table")
def _m3(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS library(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        history_id INTEGER,
        site_id TEXT,
        file_path TEXT NOT NULL UNIQUE,
        file_exists INTEGER NOT NULL DEFAULT 1,
        file_size INTEGER DEFAULT 0,
        file_mtime REAL DEFAULT 0,
        last_scanned REAL DEFAULT 0,
        watched INTEGER NOT NULL DEFAULT 0,
        watched_at REAL DEFAULT NULL,
        cover_thumb TEXT DEFAULT '',
        duration_s REAL DEFAULT NULL,
        resolution TEXT DEFAULT '',
        codec TEXT DEFAULT '',
        performer TEXT DEFAULT '',
        studio TEXT DEFAULT '',
        year INTEGER DEFAULT NULL,
        title TEXT DEFAULT '',
        title_source TEXT DEFAULT '',
        notes TEXT DEFAULT '',
        rating INTEGER DEFAULT NULL,  /* 1-5, NULL=unrated */
        added_at REAL NOT NULL DEFAULT 0,
        FOREIGN KEY(history_id) REFERENCES history(id) ON DELETE SET NULL
    )""")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_lib_site     ON library(site_id)")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_lib_watched  ON library(watched)")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_lib_studio   ON library(studio)")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_lib_performer ON library(performer)")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_lib_year     ON library(year)")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_lib_added_at ON library(added_at DESC)")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_lib_rating   ON library(rating)")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_lib_exists   ON library(file_exists)")
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "_downgrade_library_archive" in tables:
        lib_cols = [r[1] for r in cx.execute("PRAGMA table_info(library)").fetchall()]
        arch_cols = [r[1] for r in cx.execute("PRAGMA table_info(_downgrade_library_archive)").fetchall()]
        common = [c for c in lib_cols if c in arch_cols]
        if common:
            cols_csv = ", ".join((f'"{c}"' for c in common))
            cx.execute(f"INSERT OR IGNORE INTO library ({cols_csv}) SELECT {cols_csv} FROM _downgrade_library_archive")

@downgrade(version=3)
def _down_m3(cx):
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "library" not in tables:
        return
    cx.execute("CREATE TABLE IF NOT EXISTS _downgrade_library_archive AS SELECT * FROM library")
    for idx in ["idx_lib_site", "idx_lib_watched", "idx_lib_studio", "idx_lib_performer", "idx_lib_year", "idx_lib_added_at", "idx_lib_rating", "idx_lib_exists", "idx_lib_history_id"]:
        cx.execute(f"DROP INDEX IF EXISTS {idx}")
    cx.execute("DROP TABLE IF EXISTS library")

@migration(version=4, name="create_tags_tables")
def _m4(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS tags(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        color TEXT DEFAULT '',
        description TEXT DEFAULT '',
        created_at REAL NOT NULL DEFAULT 0
    )""")
    cx.execute("""CREATE TABLE IF NOT EXISTS library_tags(
        library_id INTEGER NOT NULL,
        tag_id INTEGER NOT NULL,
        added_at REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (library_id, tag_id),
        FOREIGN KEY (library_id) REFERENCES library(id) ON DELETE CASCADE,
        FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
    )""")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_libtag_tag ON library_tags(tag_id)")
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "_downgrade_tags_archive" in tables:
        cx.execute("INSERT OR IGNORE INTO tags SELECT * FROM _downgrade_tags_archive")
    if "_downgrade_library_tags_archive" in tables:
        cx.execute("INSERT OR IGNORE INTO library_tags SELECT * FROM _downgrade_library_tags_archive")

@downgrade(version=4)
def _down_m4(cx):
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "tags" in tables:
        cx.execute("CREATE TABLE IF NOT EXISTS _downgrade_tags_archive AS SELECT * FROM tags")
    if "library_tags" in tables:
        cx.execute("CREATE TABLE IF NOT EXISTS _downgrade_library_tags_archive AS SELECT * FROM library_tags")
    cx.execute("DROP INDEX IF EXISTS idx_libtag_tag")
    cx.execute("DROP TABLE IF EXISTS library_tags")
    cx.execute("DROP TABLE IF EXISTS tags")

@migration(version=5, name="add_history_library_back_ref")
def _m5(cx):
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    if "library_id" not in cols:
        cx.execute("ALTER TABLE history ADD COLUMN library_id INTEGER DEFAULT NULL")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_history_lib "
        "ON history(library_id)")
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "_downgrade_history_library_id_archive" in tables:
        cx.execute("""
            UPDATE history
            SET library_id = (
                SELECT library_id FROM _downgrade_history_library_id_archive
                WHERE _downgrade_history_library_id_archive.id = history.id
            )
            WHERE id IN (SELECT id FROM _downgrade_history_library_id_archive)
        """)

@downgrade(version=5)
def _down_m5(cx):
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    if "library_id" not in cols:
        return
    cx.execute("""
        CREATE TABLE IF NOT EXISTS _downgrade_history_library_id_archive (
            id INTEGER PRIMARY KEY,
            library_id INTEGER,
            archived_at REAL DEFAULT (strftime('%s', 'now'))
        )
    """)
    cx.execute("""
        INSERT OR REPLACE INTO _downgrade_history_library_id_archive (id, library_id)
        SELECT id, library_id FROM history WHERE library_id IS NOT NULL
    """)
    cx.execute("DROP INDEX IF EXISTS idx_history_lib")
    try:
        cx.execute("ALTER TABLE history DROP COLUMN library_id")
    except Exception:
        cols_keep = [c for c in cols if c != "library_id"]
        cols_csv = ", ".join((f'"{c}"' for c in cols_keep))
        cx.execute(f"CREATE TABLE history_recreate_tmp AS SELECT {cols_csv} FROM history WHERE 0")
        cx.execute(f"INSERT INTO history_recreate_tmp ({cols_csv}) SELECT {cols_csv} FROM history")
        cx.execute("DROP TABLE history")
        cx.execute("ALTER TABLE history_recreate_tmp RENAME TO history")

@migration(version=6, name="add_history_removed_at")
def _m6(cx):
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    if "removed_at" not in cols:
        cx.execute("ALTER TABLE history ADD COLUMN removed_at REAL DEFAULT NULL")
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "_downgrade_history_removed_at_archive" in tables:
        cx.execute("""
            UPDATE history
            SET removed_at = (
                SELECT removed_at FROM _downgrade_history_removed_at_archive
                WHERE _downgrade_history_removed_at_archive.id = history.id
            )
            WHERE id IN (SELECT id FROM _downgrade_history_removed_at_archive)
        """)

@downgrade(version=6)
def _down_m6(cx):
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    if "removed_at" not in cols:
        return
    cx.execute("""
        CREATE TABLE IF NOT EXISTS _downgrade_history_removed_at_archive (
            id INTEGER PRIMARY KEY,
            removed_at REAL,
            archived_at REAL DEFAULT (strftime('%s', 'now'))
        )
    """)
    cx.execute("""
        INSERT OR REPLACE INTO _downgrade_history_removed_at_archive (id, removed_at)
        SELECT id, removed_at FROM history WHERE removed_at IS NOT NULL
    """)
    try:
        cx.execute("ALTER TABLE history DROP COLUMN removed_at")
    except Exception:
        cols_keep = [c for c in cols if c != "removed_at"]
        cols_csv = ", ".join((f'"{c}"' for c in cols_keep))
        cx.execute(f"CREATE TABLE history_recreate_tmp AS SELECT {cols_csv} FROM history WHERE 0")
        cx.execute(f"INSERT INTO history_recreate_tmp ({cols_csv}) SELECT {cols_csv} FROM history")
        cx.execute("DROP TABLE history")
        cx.execute("ALTER TABLE history_recreate_tmp RENAME TO history")

@migration(version=7, name="add_history_honeypot_score")
def _m7(cx):
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    if "honeypot_score" not in cols:
        cx.execute("ALTER TABLE history ADD COLUMN honeypot_score REAL DEFAULT NULL")
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "_downgrade_history_honeypot_archive" in tables:
        cx.execute("""
            UPDATE history
            SET honeypot_score = (
                SELECT honeypot_score FROM _downgrade_history_honeypot_archive
                WHERE _downgrade_history_honeypot_archive.id = history.id
            )
            WHERE id IN (SELECT id FROM _downgrade_history_honeypot_archive)
        """)

@downgrade(version=7)
def _down_m7(cx):
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    if "honeypot_score" not in cols:
        return
    cx.execute("""
        CREATE TABLE IF NOT EXISTS _downgrade_history_honeypot_archive (
            id INTEGER PRIMARY KEY,
            honeypot_score REAL,
            archived_at REAL DEFAULT (strftime('%s', 'now'))
        )
    """)
    cx.execute("""
        INSERT OR REPLACE INTO _downgrade_history_honeypot_archive (id, honeypot_score)
        SELECT id, honeypot_score FROM history WHERE honeypot_score IS NOT NULL
    """)
    try:
        cx.execute("ALTER TABLE history DROP COLUMN honeypot_score")
    except Exception:
        cols_keep = [c for c in cols if c != "honeypot_score"]
        cols_csv = ", ".join((f'"{c}"' for c in cols_keep))
        cx.execute(f"CREATE TABLE history_recreate_tmp AS SELECT {cols_csv} FROM history WHERE 0")
        cx.execute(f"INSERT INTO history_recreate_tmp ({cols_csv}) SELECT {cols_csv} FROM history")
        cx.execute("DROP TABLE history")
        cx.execute("ALTER TABLE history_recreate_tmp RENAME TO history")

@migration(version=8, name="add_history_bytes_fetched")
def _m8(cx):
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    if "bytes_fetched" not in cols:
        cx.execute("ALTER TABLE history ADD COLUMN bytes_fetched INTEGER DEFAULT NULL")
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "_downgrade_history_bytes_archive" in tables:
        cx.execute("""
            UPDATE history
            SET bytes_fetched = (
                SELECT bytes_fetched FROM _downgrade_history_bytes_archive
                WHERE _downgrade_history_bytes_archive.id = history.id
            )
            WHERE id IN (SELECT id FROM _downgrade_history_bytes_archive)
        """)

@downgrade(version=8)
def _down_m8(cx):
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    if "bytes_fetched" not in cols:
        return
    cx.execute("""
        CREATE TABLE IF NOT EXISTS _downgrade_history_bytes_archive (
            id INTEGER PRIMARY KEY,
            bytes_fetched INTEGER,
            archived_at REAL DEFAULT (strftime('%s', 'now'))
        )
    """)
    cx.execute("""
        INSERT OR REPLACE INTO _downgrade_history_bytes_archive (id, bytes_fetched)
        SELECT id, bytes_fetched FROM history WHERE bytes_fetched IS NOT NULL
    """)
    try:
        cx.execute("ALTER TABLE history DROP COLUMN bytes_fetched")
    except Exception:
        cols_keep = [c for c in cols if c != "bytes_fetched"]
        cols_csv = ", ".join((f'"{c}"' for c in cols_keep))
        cx.execute(f"CREATE TABLE history_recreate_tmp AS SELECT {cols_csv} FROM history WHERE 0")
        cx.execute(f"INSERT INTO history_recreate_tmp ({cols_csv}) SELECT {cols_csv} FROM history")
        cx.execute("DROP TABLE history")
        cx.execute("ALTER TABLE history_recreate_tmp RENAME TO history")

@migration(version=9, name="add_history_transfer_mode")
def _m9(cx):
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    if "transfer_mode" not in cols:
        cx.execute("ALTER TABLE history ADD COLUMN transfer_mode TEXT DEFAULT NULL")
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "_downgrade_history_transfer_mode_archive" in tables:
        cx.execute("""
            UPDATE history
            SET transfer_mode = (
                SELECT transfer_mode FROM _downgrade_history_transfer_mode_archive
                WHERE _downgrade_history_transfer_mode_archive.id = history.id
            )
            WHERE id IN (SELECT id FROM _downgrade_history_transfer_mode_archive)
        """)

@downgrade(version=9)
def _down_m9(cx):
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    if "transfer_mode" not in cols:
        return
    cx.execute("""
        CREATE TABLE IF NOT EXISTS _downgrade_history_transfer_mode_archive (
            id INTEGER PRIMARY KEY,
            transfer_mode TEXT,
            archived_at REAL DEFAULT (strftime('%s', 'now'))
        )
    """)
    cx.execute("""
        INSERT OR REPLACE INTO _downgrade_history_transfer_mode_archive (id, transfer_mode)
        SELECT id, transfer_mode FROM history WHERE transfer_mode IS NOT NULL
    """)
    try:
        cx.execute("ALTER TABLE history DROP COLUMN transfer_mode")
    except Exception:
        cols_keep = [c for c in cols if c != "transfer_mode"]
        cols_csv = ", ".join((f'"{c}"' for c in cols_keep))
        cx.execute(f"CREATE TABLE history_recreate_tmp AS SELECT {cols_csv} FROM history WHERE 0")
        cx.execute(f"INSERT INTO history_recreate_tmp ({cols_csv}) SELECT {cols_csv} FROM history")
        cx.execute("DROP TABLE history")
        cx.execute("ALTER TABLE history_recreate_tmp RENAME TO history")

@migration(version=10, name="add_library_title_source")
def _m10(cx):
    cols = {r[1] for r in cx.execute("PRAGMA table_info(library)").fetchall()}
    if "title_source" not in cols:
        cx.execute("ALTER TABLE library ADD COLUMN title_source TEXT DEFAULT ''")
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "_downgrade_library_title_source_archive" in tables:
        cx.execute("""
            UPDATE library
            SET title_source = (
                SELECT title_source FROM _downgrade_library_title_source_archive
                WHERE _downgrade_library_title_source_archive.id = library.id
            )
            WHERE id IN (SELECT id FROM _downgrade_library_title_source_archive)
        """)

@downgrade(version=10)
def _down_m10(cx):
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "library" not in tables:
        return
    cols = {r[1] for r in cx.execute("PRAGMA table_info(library)").fetchall()}
    if "title_source" not in cols:
        return
    cx.execute("""
        CREATE TABLE IF NOT EXISTS _downgrade_library_title_source_archive (
            id INTEGER PRIMARY KEY,
            title_source TEXT,
            archived_at REAL DEFAULT (strftime('%s', 'now'))
        )
    """)
    cx.execute("""
        INSERT OR REPLACE INTO _downgrade_library_title_source_archive (id, title_source)
        SELECT id, title_source FROM library WHERE title_source != ''
    """)
    try:
        cx.execute("ALTER TABLE library DROP COLUMN title_source")
    except Exception:
        cols_keep = [c for c in cols if c != "title_source"]
        cols_csv = ", ".join((f'"{c}"' for c in cols_keep))
        cx.execute(f"CREATE TABLE library_recreate_tmp AS SELECT {cols_csv} FROM library WHERE 0")
        cx.execute(f"INSERT INTO library_recreate_tmp ({cols_csv}) SELECT {cols_csv} FROM library")
        cx.execute("DROP TABLE library")
        cx.execute("ALTER TABLE library_recreate_tmp RENAME TO library")

@migration(version=11, name="add_history_egress_ip")
def _m11(cx):
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    if "egress_ip" not in cols:
        cx.execute("ALTER TABLE history ADD COLUMN egress_ip TEXT NOT NULL DEFAULT 'UNKNOWN'")
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "_downgrade_history_egress_ip_archive" in tables:
        cx.execute("""
            UPDATE history
            SET egress_ip = (
                SELECT egress_ip FROM _downgrade_history_egress_ip_archive
                WHERE _downgrade_history_egress_ip_archive.id = history.id
            )
            WHERE id IN (SELECT id FROM _downgrade_history_egress_ip_archive)
        """)

@downgrade(version=11)
def _down_m11(cx):
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    if "egress_ip" not in cols:
        return
    cx.execute("""
        CREATE TABLE IF NOT EXISTS _downgrade_history_egress_ip_archive (
            id INTEGER PRIMARY KEY,
            egress_ip TEXT,
            archived_at REAL DEFAULT (strftime('%s', 'now'))
        )
    """)
    cx.execute("""
        INSERT OR REPLACE INTO _downgrade_history_egress_ip_archive (id, egress_ip)
        SELECT id, egress_ip FROM history WHERE egress_ip != 'UNKNOWN'
    """)
    try:
        cx.execute("ALTER TABLE history DROP COLUMN egress_ip")
    except Exception:
        cols_keep = [c for c in cols if c != "egress_ip"]
        cols_csv = ", ".join((f'"{c}"' for c in cols_keep))
        cx.execute(f"CREATE TABLE history_recreate_tmp AS SELECT {cols_csv} FROM history WHERE 0")
        cx.execute(f"INSERT INTO history_recreate_tmp ({cols_csv}) SELECT {cols_csv} FROM history")
        cx.execute("DROP TABLE history")
        cx.execute("ALTER TABLE history_recreate_tmp RENAME TO history")

@migration(version=12, name="add_covering_indexes")
def _m12(cx):
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "library" in tables:
        cx.execute("CREATE INDEX IF NOT EXISTS idx_lib_history_id ON library(history_id)")
    if "history" in tables:
        cx.execute("CREATE INDEX IF NOT EXISTS idx_hist_url_status ON history(url, status)")
        cx.execute("CREATE INDEX IF NOT EXISTS idx_hist_active_site_ts ON history(site_id, ts DESC) WHERE removed_at IS NULL")
        cx.execute("CREATE INDEX IF NOT EXISTS idx_hist_library_id ON history(library_id) WHERE library_id IS NOT NULL")
    if "queue" in tables:
        cx.execute("CREATE INDEX IF NOT EXISTS idx_q_site_status_ord ON queue(site_id, status, ord)")
    if "tags" in tables:
        cx.execute("CREATE INDEX IF NOT EXISTS idx_tags_created_at ON tags(created_at)")

@downgrade(version=12)
def _down_m12(cx):
    cx.execute("DROP INDEX IF EXISTS idx_lib_history_id")
    cx.execute("DROP INDEX IF EXISTS idx_hist_url_status")
    cx.execute("DROP INDEX IF EXISTS idx_hist_active_site_ts")
    cx.execute("DROP INDEX IF EXISTS idx_hist_library_id")
    cx.execute("DROP INDEX IF EXISTS idx_q_site_status_ord")
    cx.execute("DROP INDEX IF EXISTS idx_tags_created_at")
