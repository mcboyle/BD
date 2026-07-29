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

import os
import sys
import time
from typing import Callable, Optional


# Migration registry — populated by @migration decorator.
# Each entry: {version, name, fn(cx), applied_at}
_MIGRATIONS: list = []


def migration(version: int, name: str = ""):
    """Decorator: register a migration. Migrations apply in version
    order, lowest first."""
    def decorator(fn: Callable):
        _MIGRATIONS.append({
            "version": int(version),
            "name": name or fn.__name__,
            "fn": fn,
        })
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


def apply_pending(*, dry_run: bool = False, backup_first: bool = True) -> dict:
    """Apply any unapplied migrations in order.

    v3.48 (#44): dry_run now executes migrations against an IN-MEMORY
    snapshot of the schema (CREATE TABLE clones, not rows) to verify
    they'd actually succeed before committing to the real DB. Returns
    a per-migration result with `would_succeed: bool` and the resulting
    schema changes."""
    pend = pending_migrations()
    out = {"considered": len(pend), "applied": 0, "errors": 0,
           "dry_run": dry_run, "results": []}
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

    # ── ROB-2: crash-safe real apply ─────────────────────────────────
    # (1) PREFLIGHT GATE: run the in-memory dry-run first. If ANY pending
    #     migration would fail, abort WITHOUT applying anything, so a bad
    #     migration can never leave a half-migrated DB that wedges startup.
    # (2) BACKUP: copy the live DB aside so a data-dependent failure the
    #     schema-only dry-run can't catch is recoverable (abort-to-prior).
    if backup_first:
        preflight = apply_pending(dry_run=True, backup_first=False)
        if preflight.get("errors"):
            out["aborted"] = True
            out["abort_reason"] = ("migration dry-run preflight found a migration "
                                   "that would fail; no migrations were applied")
            out["preflight"] = preflight.get("results", [])
            return out
        _bak = _backup_db_before_migration()
        if _bak:
            out["backup"] = _bak

    from . import db as _db
    for m in pend:
        started = time.time()
        try:
            with _db.db_conn() as cx:
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
                if _restore_db_from_backup(out["backup"]):
                    out["restored_to_prior"] = True
            # Stop on first error — later migrations might depend on
            # this one's columns
            break
    return out


def _backup_db_before_migration():
    """ROB-2: copy the live DB file aside before migrations mutate it, returning
    the backup path (``<db>.premigration.bak``) or None if there is no DB file /
    the copy failed. Best-effort and safe at boot (single-threaded, pre-workers):
    checkpoints WAL first so the copied file is self-contained."""
    import shutil
    try:
        from . import db as _db
        src = _db._resolve_db_path()
    except Exception:
        return None
    if not src or not os.path.exists(src):
        return None
    dst = src + ".premigration.bak"
    try:
        try:
            with _db.db_conn() as cx:
                cx.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
        shutil.copy2(src, dst)
        return dst
    except Exception:
        return None


def _restore_db_from_backup(bak: str) -> bool:
    """ROB-2: restore the live DB from a pre-migration backup. Returns True on a
    successful copy-back. Best-effort; safe at boot."""
    import shutil
    try:
        from . import db as _db
        src = _db._resolve_db_path()
        if bak and os.path.exists(bak) and src:
            shutil.copy2(bak, src)
            return True
    except Exception:
        pass
    return False


def _snapshot_schema(cx) -> dict:
    """Capture a structural snapshot of `cx`'s schema for diff comparison.
    Returns {tables: set[str], columns: {table: set[str]}}."""
    out = {"tables": set(), "columns": {}}
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
    """Compare actual DB schema to EXPECTED_SCHEMA. Returns
    {ok, missing_tables, missing_columns, extra_tables}."""
    out = {"ok": True, "missing_tables": [],
           "missing_columns": {}, "extra_tables": []}
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
    """Add commonly-needed indexes that earlier versions may have missed."""
    cx.execute("CREATE INDEX IF NOT EXISTS idx_history_status ON history(status)")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_history_site ON history(site_id)")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_history_ts ON history(ts)")


@migration(version=2, name="add_history_retry_after")
def _m2(cx):
    """Add a retry_after column to history if missing. Used by Phase 99
    circuit breaker to delay retries."""
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    if "retry_after" not in cols:
        cx.execute("ALTER TABLE history ADD COLUMN retry_after TEXT DEFAULT NULL")


# ── v3.50 (Phase 3): library + tags schema ──────────────────────────────
# The library table is the operator's collection: one row per FILE ON
# DISK that the app knows about. Rows are produced two ways:
#
#   1. Forward: when runner completes a download, library_record() is
#      called with the new file path + extracted metadata
#   2. Backward: the library scanner walks configured roots and reconciles
#      against the history table — historical downloads pre-dating this
#      release pick up a library row on first scan
#
# The `tags` table is operator-defined free-form labels. A row in
# `library_tags` is a many-to-many junction. ON DELETE CASCADE so dropping
# a library row also drops its tag links (orphan-cleanup is essentially
# free).

@migration(version=3, name="create_library_table")
def _m3(cx):
    """Create the library table. Idempotent — CREATE IF NOT EXISTS."""
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
        notes TEXT DEFAULT '',
        rating INTEGER DEFAULT NULL,  /* 1-5, NULL=unrated */
        added_at REAL NOT NULL DEFAULT 0,
        FOREIGN KEY(history_id) REFERENCES history(id) ON DELETE SET NULL
    )""")
    # Indexes — every column we filter or sort by deserves one because
    # the library tab supports browse-by-anything.
    cx.execute(
        "CREATE INDEX IF NOT EXISTS idx_lib_site     ON library(site_id)")
    cx.execute(
        "CREATE INDEX IF NOT EXISTS idx_lib_watched  ON library(watched)")
    cx.execute(
        "CREATE INDEX IF NOT EXISTS idx_lib_studio   ON library(studio)")
    cx.execute(
        "CREATE INDEX IF NOT EXISTS idx_lib_performer ON library(performer)")
    cx.execute(
        "CREATE INDEX IF NOT EXISTS idx_lib_year     ON library(year)")
    cx.execute(
        "CREATE INDEX IF NOT EXISTS idx_lib_added_at ON library(added_at DESC)")
    cx.execute(
        "CREATE INDEX IF NOT EXISTS idx_lib_rating   ON library(rating)")
    cx.execute(
        "CREATE INDEX IF NOT EXISTS idx_lib_exists   ON library(file_exists)")


@migration(version=4, name="create_tags_tables")
def _m4(cx):
    """Operator-defined tags + the library↔tags junction."""
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
    cx.execute(
        "CREATE INDEX IF NOT EXISTS idx_libtag_tag ON library_tags(tag_id)")


@migration(version=5, name="add_history_library_back_ref")
def _m5(cx):
    """Add a library_id column to history so we can navigate from a
    history row to its library entry in one hop.

    Soft-add: existing history rows get NULL until the scanner backfills.
    The runner forward-path populates this column on new completions."""
    cols = {r[1] for r in cx.execute(
        "PRAGMA table_info(history)").fetchall()}
    if "library_id" not in cols:
        cx.execute(
            "ALTER TABLE history ADD COLUMN library_id INTEGER DEFAULT NULL")
        cx.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_lib "
            "ON history(library_id)")


@migration(version=6, name="add_history_removed_at")
def _m6(cx):
    """Soft-delete support for history. Setting removed_at hides the row
    from most queries (db_search filters on removed_at IS NULL by default
    after this) without losing the audit trail."""
    cols = {r[1] for r in cx.execute(
        "PRAGMA table_info(history)").fetchall()}
    if "removed_at" not in cols:
        cx.execute(
            "ALTER TABLE history ADD COLUMN removed_at REAL DEFAULT NULL")


@migration(version=7, name="add_history_honeypot_score")
def _m7(cx):
    """P5-2b: persist the resolve-time honeypot score on the history row.

    Soft-add: existing rows get NULL. The per-site threshold learner
    (honeypot_threshold.py) quantile-fits the non-NULL scores on confirmed
    traps; a fully-NULL column simply yields no samples → default
    threshold, so this migration is behaviourally inert until db_log
    starts stamping scores."""
    cols = {r[1] for r in cx.execute(
        "PRAGMA table_info(history)").fetchall()}
    if "honeypot_score" not in cols:
        cx.execute(
            "ALTER TABLE history ADD COLUMN honeypot_score REAL DEFAULT NULL")


@migration(version=8, name="add_history_bytes_fetched")
def _m8(cx):
    """v3.66.819: record how many bytes BD actually transferred for a job.

    No existing column could answer "did this download anything". file_size is
    an on-disk stat, so a job that skipped because the file was already present
    recorded the size of the file that was already there; message is prose that
    varies by path, and one no-fetch path positively asserts a download that
    did not happen. Fifteen call sites write status='done' and at least five of
    them transfer nothing.

    Soft-add: existing rows get NULL, which means "this path does not record
    it" -- UNKNOWN, and never proof of a download. Consumers must not read NULL
    as a transfer, or the migration window reopens the hole for the whole
    existing history.

    #63 originally added this as a bespoke loop in db.db_init(), outside this
    framework, so the schema_migrations ledger had no record of it. This is that
    correction; the column is idempotent either way.
    """
    cols = {r[1] for r in cx.execute(
        "PRAGMA table_info(history)").fetchall()}
    if "bytes_fetched" not in cols:
        cx.execute(
            "ALTER TABLE history ADD COLUMN bytes_fetched INTEGER DEFAULT NULL")

