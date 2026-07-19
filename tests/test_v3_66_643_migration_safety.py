"""v3.66.643 -- ROB-2: migration backup + abort-to-prior.

migrations.apply_pending() already has a dry-run mode (in-memory schema replica).
This cut wires the REAL apply path to be crash-safe:

  1. PREFLIGHT GATE: before mutating the live DB, run the in-memory dry-run; if
     ANY pending migration would fail, abort WITHOUT applying anything -- so a bad
     migration can never leave a half-migrated DB that wedges startup.
  2. BACKUP + RESTORE: copy the live DB file aside before applying; if a migration
     fails at apply time (a data-dependent failure the schema-only dry-run can't
     catch), restore the pre-migration DB (abort-to-prior).

Sandbox-safe: an isolated temp DB via monkeypatched db.DB_PATH; the migration
registry is saved/restored in finally; zero-arg tests; no pytest builtins.
"""
from __future__ import annotations

import os
import tempfile

import bulk_downloader.db as db
import bulk_downloader.migrations as mg


def _isolated_db():
    """Point db at a fresh temp DB file; return (dir, path)."""
    d = tempfile.mkdtemp(prefix="rob2_")
    return d, os.path.join(d, "queue.db")


def test_preflight_aborts_without_applying_a_failing_batch():
    """A migration that would fail the dry-run must abort the WHOLE apply with
    zero applied and the live DB untouched. RED on pristine (no preflight -> the
    failing migration is attempted against the real DB, 'aborted' key absent)."""
    saved = list(mg._MIGRATIONS)
    saved_path = db.DB_PATH
    _d, dbf = _isolated_db()
    db.DB_PATH = dbf
    try:
        def _bad(cx):
            cx.execute("ALTER TABLE __does_not_exist__ ADD COLUMN x INTEGER")
        mg._MIGRATIONS.clear()
        mg._MIGRATIONS.append({"version": 990001, "name": "bad_preflight", "fn": _bad})

        out = mg.apply_pending()
        assert out.get("aborted") is True, (
            f"a dry-run-failing batch must abort before applying; got {out}"
        )
        assert out.get("applied", 0) == 0, out
        # the failing migration must NOT have been recorded as applied
        with db.db_conn() as cx:
            n = cx.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version=990001"
            ).fetchone()[0]
        assert n == 0, "the aborted migration must not appear in schema_migrations"
    finally:
        mg._MIGRATIONS[:] = saved
        db.DB_PATH = saved_path


def test_clean_batch_still_applies_and_records():
    """A migration that passes the dry-run applies normally and is recorded --
    the ROB-2 gate must not block healthy migrations."""
    saved = list(mg._MIGRATIONS)
    saved_path = db.DB_PATH
    _d, dbf = _isolated_db()
    db.DB_PATH = dbf
    try:
        def _good(cx):
            cx.execute("CREATE TABLE rob2_ok(x INTEGER)")
        mg._MIGRATIONS.clear()
        mg._MIGRATIONS.append({"version": 990002, "name": "good", "fn": _good})

        out = mg.apply_pending()
        assert out.get("aborted") is not True, out
        assert out.get("applied", 0) == 1, f"a clean migration should apply, got {out}"
        with db.db_conn() as cx:
            n = cx.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version=990002 AND success=1"
            ).fetchone()[0]
        assert n == 1, "the applied migration must be recorded"
    finally:
        mg._MIGRATIONS[:] = saved
        db.DB_PATH = saved_path


def test_backup_helper_copies_the_live_db():
    """_backup_db_before_migration() must produce a real copy of the live DB
    file (the recovery point). RED on pristine (helper absent)."""
    saved_path = db.DB_PATH
    _d, dbf = _isolated_db()
    db.DB_PATH = dbf
    try:
        with db.db_conn() as cx:            # materialize the DB file
            cx.execute("CREATE TABLE IF NOT EXISTS t(x)")
        bak = mg._backup_db_before_migration()
        assert bak and os.path.exists(bak), f"backup file should exist, got {bak!r}"
        assert os.path.getsize(bak) > 0, "backup must not be empty"
    finally:
        db.DB_PATH = saved_path


def test_apply_pending_exposes_backup_on_clean_run():
    """A successful apply that took a backup should surface the backup path in the
    result (so the operator/log can see the recovery point was taken)."""
    saved = list(mg._MIGRATIONS)
    saved_path = db.DB_PATH
    _d, dbf = _isolated_db()
    db.DB_PATH = dbf
    try:
        with db.db_conn() as cx:            # pre-existing DB so a backup is possible
            cx.execute("CREATE TABLE IF NOT EXISTS seed(x)")

        def _good(cx):
            cx.execute("CREATE TABLE rob2_backup_ok(x INTEGER)")
        mg._MIGRATIONS.clear()
        mg._MIGRATIONS.append({"version": 990003, "name": "good_bak", "fn": _good})

        out = mg.apply_pending()
        assert out.get("backup"), f"a clean apply over an existing DB should record a backup, got {out}"
        assert os.path.exists(out["backup"])
    finally:
        mg._MIGRATIONS[:] = saved
        db.DB_PATH = saved_path
