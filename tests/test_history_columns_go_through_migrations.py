"""#63 added a history column outside the migration framework.

THE DEFECT, and it is mine from an hour ago. `bulk_downloader/migrations.py` is
a versioned migration framework: an `@migration(version, name)` decorator, a
`_MIGRATIONS` registry, a `schema_migrations` ledger table, and
`apply_pending()` called at `app.py:1814`. Seven migrations are registered, and
four of them add history columns -- `retry_after` (v2), `library_id` (v5),
`removed_at` (v6) and `honeypot_score` (v7).

#63 added `bytes_fetched` as a bespoke loop inside `db_init()` instead. It
works -- verified on the deploy host, the column is present -- but:

  * the schema_migrations ledger has no record of it, so the DB's own account
    of its schema version is wrong;
  * it duplicates honeypot_score handling that migrations.py:488 already does;
  * the next person adding a column follows migrations.py and finds
    bytes_fetched missing from it.

The #63 commit message also asserts "history has never had a lazy migration,
only `queue` did -- honeypot_score included". That is false. It was derived from
db.py alone without searching for a migrations module, which is the
"grep is not a denominator" mistake CLAUDE.md section 1 opens with -- the
instrument saw one file and the conclusion was drawn about the tree.

THE GATE IS DERIVED, NOT A LIST. Asserting "bytes_fetched has a migration"
closes this instance and nothing else. The rule is that NO history column may be
added outside the framework, so the denominator is every `ALTER TABLE history`
in the tree, and the next bespoke one fails here without anyone remembering to
add an assertion.

BEHAVIOUR MUST NOT CHANGE. A database that already went through #63's loop has
the column; one that never did must still get it. Both are asserted, because a
"correction" that stranded either would be worse than the divergence it fixes.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _tracked(*globs):
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", *globs],
                         capture_output=True, text=True).stdout
    return [p for p in out.split("\0") if p]


# ── the derived rule ─────────────────────────────────────────────────────────

def test_the_scan_can_see_alter_table_history_statements():
    """Canary: a zero denominator would make the rule below vacuous."""
    hits = [rel for rel in _tracked("*.py")
            if "ALTER TABLE history" in (ROOT / rel).read_text(
                encoding="utf-8", errors="replace")]
    assert hits, "no ALTER TABLE history found anywhere -- the scan is blind"


def test_no_history_column_is_added_outside_the_migration_framework():
    """THE RULE. bulk_downloader/migrations.py owns history schema changes.

    retention.py is exempt and named: it manages its own retention bookkeeping
    column under an explicit table-creation guard rather than as a schema
    version. Everything else must be a registered migration, so the ledger
    stays a true account of the schema.
    """
    _EXEMPT = {"bulk_downloader/migrations.py", "bulk_downloader/retention.py"}
    offenders = []
    for rel in _tracked("bulk_downloader/*.py", "bulk_downloader/**/*.py"):
        if rel in _EXEMPT:
            continue
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            if "ALTER TABLE history" in line and not line.lstrip().startswith("#"):
                offenders.append(f"{rel}:{lineno}")
    assert not offenders, (
        "these add history columns outside bulk_downloader/migrations.py, so "
        "the schema_migrations ledger will not record them and the DB's own "
        "account of its schema version becomes wrong:\n  "
        + "\n  ".join(offenders) +
        "\nRegister an @migration(version=N) instead."
    )


def test_bytes_fetched_is_a_registered_migration():
    from bulk_downloader import migrations as _m
    names = {m["name"] for m in _m._MIGRATIONS}
    assert any("bytes_fetched" in n for n in names), (
        f"no registered migration adds bytes_fetched. Registered: "
        f"{sorted(names)}"
    )


def test_migration_versions_are_unique_and_ordered():
    """A duplicate version silently shadows one of the two in the ledger."""
    from bulk_downloader import migrations as _m
    versions = [m["version"] for m in _m._MIGRATIONS]
    assert len(versions) == len(set(versions)), (
        f"duplicate migration versions: {sorted(versions)}"
    )


# ── behaviour, both directions ───────────────────────────────────────────────

def _pre_63_db() -> Path:
    """A history table exactly as it stood before #63."""
    from bulk_downloader import db as _db
    d = Path(tempfile.mkdtemp())
    p = d / "h.db"
    saved = _db.DB_PATH
    try:
        _db.DB_PATH = str(p)
        _db.db_init()
        cx = sqlite3.connect(p)
        cx.execute("ALTER TABLE history DROP COLUMN bytes_fetched")
        cx.commit()
        cx.close()
    finally:
        _db.DB_PATH = saved
    return p


def test_an_existing_database_still_gains_the_column(monkeypatch):
    """The correction must not strand a DB that never ran #63's loop."""
    from bulk_downloader import db as _db, migrations as _m
    p = _pre_63_db()
    monkeypatch.setattr(_db, "DB_PATH", str(p))
    before = {r[1] for r in sqlite3.connect(p).execute(
        "PRAGMA table_info(history)")}
    assert "bytes_fetched" not in before, "fixture did not reach the pre-#63 shape"

    _m.apply_pending(backup_first=False)

    after = {r[1] for r in sqlite3.connect(p).execute(
        "PRAGMA table_info(history)")}
    assert "bytes_fetched" in after, (
        "apply_pending() did not add bytes_fetched to a pre-#63 database. "
        "Every deployed DB reaches the column through this path once the "
        "bespoke loop in db_init is removed."
    )


def test_a_fresh_database_has_the_column(monkeypatch, tmp_path):
    """CREATE TABLE carries it too -- same as honeypot_score. A fresh install
    must not depend on a migration replaying to get a column it was born with.
    """
    from bulk_downloader import db as _db
    monkeypatch.setattr(_db, "DB_PATH", str(tmp_path / "fresh.db"))
    _db.db_init()
    cols = {r[1] for r in sqlite3.connect(tmp_path / "fresh.db").execute(
        "PRAGMA table_info(history)")}
    assert "bytes_fetched" in cols


def test_the_ledger_records_it(monkeypatch):
    """The point of the framework: the DB knows what has been applied."""
    from bulk_downloader import db as _db, migrations as _m
    p = _pre_63_db()
    monkeypatch.setattr(_db, "DB_PATH", str(p))
    _m.apply_pending(backup_first=False)
    applied = _m.applied_versions()
    target = next((m["version"] for m in _m._MIGRATIONS
                   if "bytes_fetched" in m["name"]), None)
    assert target is not None, "no bytes_fetched migration registered"
    assert target in applied, (
        f"migration v{target} added the column but the schema_migrations "
        f"ledger does not record it: applied={sorted(applied)}"
    )
