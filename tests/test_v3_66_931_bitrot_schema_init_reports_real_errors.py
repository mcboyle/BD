"""v3.66.931: bitrot's schema init could not tell "already there" from
"could not be done".

`_ensure_integrity_table` wrapped its ALTER TABLE in

    except Exception:
        pass  # column already exists

so EVERY failure was read as the benign duplicate-column case. A missing
`provenance` table, a locked database, a read-only mount -- all of them
returned quietly, and the function went on to report success it had not
earned. `_candidates` then SELECTs `last_verified_ts` (bitrot.py:96-100), so
the real failure surfaces later, somewhere else, as a query error against a
column the init reported as present.

MEASURED, because the obvious discriminator does not work here. Unlike
v3.66.928's quarantine fix, the SQLite result CODE cannot separate these:

    duplicate column name: last_verified_ts   -> SQLITE_ERROR (1)
    no such table: provenance                 -> SQLITE_ERROR (1)
    database is locked                        -> SQLITE_BUSY  (5)
    attempt to write a readonly database      -> SQLITE_READONLY (8)

The benign case and the most likely real one share a code, so only the
message separates them. The fix therefore leads with a STRUCTURAL check --
PRAGMA table_info, which needs no exception at all -- and keeps a narrow
duplicate-column tolerance underneath it for the genuine race where two
processes add the column at once.

The register called this "a bare except". It is `except Exception`, which is
a materially different bug: a bare `except:` would also swallow
KeyboardInterrupt and SystemExit. The defect here is narrower and is what is
tested.
"""
from __future__ import annotations

import sqlite3

import pytest

from bulk_downloader import bitrot as _br
from bulk_downloader import db as _db


def _fresh_db(tmp_path, monkeypatch, *, provenance: str | None):
    """Point the app at a real throwaway database.

    An absolute monkeypatched DB_PATH is rung 1 of _resolve_db_path, so this
    cannot fall through to the repo-relative default and write into the tree.
    """
    dbp = tmp_path / "downloader_history.db"
    monkeypatch.setattr(_db, "DB_PATH", str(dbp))
    monkeypatch.setenv("BD_INSTALL_DIR", str(tmp_path))
    if provenance is not None:
        cx = sqlite3.connect(str(dbp))
        cx.execute(provenance)
        cx.commit()
        cx.close()
    return dbp


def _columns(dbp) -> set:
    cx = sqlite3.connect(str(dbp))
    try:
        return {r[1] for r in cx.execute("PRAGMA table_info(provenance)")}
    finally:
        cx.close()


# ── the real failure must be reported ─────────────────────────────────

def test_a_missing_provenance_table_is_reported_not_swallowed(
        tmp_path, monkeypatch, capsys):
    """The defect. No provenance table at all -- the ALTER cannot possibly
    mean 'column already exists', and returning quietly claims a schema the
    database does not have."""
    _fresh_db(tmp_path, monkeypatch, provenance=None)

    _br._ensure_integrity_table()

    err = capsys.readouterr().err
    assert "bitrot" in err and "provenance" in err, (
        "a genuinely failed ALTER returned silently; it is indistinguishable "
        f"from the benign duplicate-column case. stderr was {err!r}")


def test_a_readonly_database_is_reported_not_swallowed(
        tmp_path, monkeypatch, capsys):
    """SQLITE_READONLY. Nothing about it says the column exists."""
    dbp = _fresh_db(tmp_path, monkeypatch,
                    provenance="CREATE TABLE provenance(id INTEGER)")

    real_connect = sqlite3.connect

    def _ro(*_a, **_kw):
        return real_connect(f"file:{dbp}?mode=ro", uri=True)

    monkeypatch.setattr(sqlite3, "connect", _ro)
    _br._ensure_integrity_table()

    err = capsys.readouterr().err
    assert "bitrot" in err, (
        f"a read-only database was swallowed as benign. stderr was {err!r}")


# ── the benign cases must stay quiet ──────────────────────────────────

def test_an_existing_column_is_not_an_error(tmp_path, monkeypatch, capsys):
    """Over-correction guard. The whole point of the original `pass` was
    that a second run is normal; a fix that reports it would make every
    startup noisy and get switched off."""
    _fresh_db(
        tmp_path, monkeypatch,
        provenance="CREATE TABLE provenance(id INTEGER, "
                   "last_verified_ts REAL DEFAULT 0)")

    _br._ensure_integrity_table()

    err = capsys.readouterr().err
    assert err == "", f"a normal second run printed to stderr: {err!r}"


def test_the_column_is_added_on_a_first_run(tmp_path, monkeypatch, capsys):
    """The function must still do its job."""
    dbp = _fresh_db(tmp_path, monkeypatch,
                    provenance="CREATE TABLE provenance(id INTEGER)")
    assert "last_verified_ts" not in _columns(dbp)

    _br._ensure_integrity_table()

    assert "last_verified_ts" in _columns(dbp)
    assert capsys.readouterr().err == ""


def test_a_concurrent_duplicate_column_race_is_tolerated(
        tmp_path, monkeypatch, capsys):
    """Two processes can pass the PRAGMA check and both issue the ALTER.
    The loser gets `duplicate column name`, which IS benign -- the
    structural check does not remove the need for that tolerance."""
    import contextlib

    dbp = _fresh_db(tmp_path, monkeypatch,
                    provenance="CREATE TABLE provenance(id INTEGER)")

    class _Racing:
        """Wraps a real connection; only the ALTER loses the race.

        A wrapper rather than a patch of sqlite3.Connection.execute -- that
        is a C-level method, and patching it breaks unrelated connections
        during teardown, which presents as a subject failure.
        """

        def __init__(self, cx):
            self._cx = cx

        def execute(self, sql, *a, **kw):
            if "ADD COLUMN last_verified_ts" in sql:
                raise sqlite3.OperationalError(
                    "duplicate column name: last_verified_ts")
            return self._cx.execute(sql, *a, **kw)

        def __getattr__(self, name):
            return getattr(self._cx, name)

    @contextlib.contextmanager
    def _conn():
        cx = sqlite3.connect(str(dbp))
        try:
            yield _Racing(cx)
            cx.commit()
        finally:
            cx.close()

    monkeypatch.setattr(_db, "db_conn", _conn)
    _br._ensure_integrity_table()

    err = capsys.readouterr().err
    assert err == "", f"a duplicate-column race was reported as a failure: {err!r}"


def test_the_integrity_table_is_still_created(tmp_path, monkeypatch):
    """The function's primary job, unchanged."""
    dbp = _fresh_db(tmp_path, monkeypatch,
                    provenance="CREATE TABLE provenance(id INTEGER)")
    _br._ensure_integrity_table()
    cx = sqlite3.connect(str(dbp))
    try:
        names = {r[0] for r in cx.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        cx.close()
    assert "integrity_issues" in names


def test_schema_init_never_raises(tmp_path, monkeypatch, capsys):
    """It is called at the top of run_scan and from a scheduled job, so it
    must report rather than propagate -- a raising init would take the
    nightly task down instead of logging one line."""
    _fresh_db(tmp_path, monkeypatch, provenance=None)
    _br._ensure_integrity_table()      # must not raise
    assert capsys.readouterr().err != ""
