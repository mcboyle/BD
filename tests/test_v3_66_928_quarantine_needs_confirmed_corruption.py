"""v3.66.928: auto_recover_sqlite may only move a database aside on a
POSITIVE corruption signal, and its quarantine name must be unique.

Two measured defects, both on the operator's production history:

  1. `except sqlite3.DatabaseError` caught OperationalError, which is a
     SUBCLASS -- so `database is locked` and `disk I/O error`, the ordinary
     signatures of parallel contention, read as CONFIRMED corruption. A
     3.7 MiB database with integrity=ok and 114 history rows was renamed
     aside and replaced with an empty one.

  2. The quarantine name was keyed on int(time.time()) -- one-second
     resolution -- while Path.rename overwrites its destination silently on
     POSIX. Two recoveries in the same second destroyed one of the two
     files, leaving no trace that it had existed.

Both directions are asserted here. A fix that simply stops quarantining
would satisfy the first half and destroy the tool, so the genuine-corruption
cases below are as load-bearing as the contention ones.
"""
from __future__ import annotations

import sqlite3

import pytest

from bulk_downloader.selftest import OK, WARN, auto_recover_sqlite


# ── helpers ───────────────────────────────────────────────────────────

def _healthy_db(path, rows: int = 500) -> None:
    """A real database with real rows, verified healthy before use."""
    cx = sqlite3.connect(str(path))
    cx.execute("CREATE TABLE history (id INTEGER PRIMARY KEY, url TEXT)")
    cx.executemany("INSERT INTO history (url) VALUES (?)",
                   [(f"http://example/{i}",) for i in range(rows)])
    cx.commit()
    assert cx.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    cx.close()


def _quarantined(directory) -> list:
    """Quarantine files, excluding the -wal/-shm companions."""
    return sorted(p for p in directory.iterdir()
                  if ".corrupt." in p.name
                  and not p.name.endswith(("-wal", "-shm")))


def _row_count(path) -> int:
    cx = sqlite3.connect(str(path))
    try:
        return cx.execute("SELECT count(*) FROM history").fetchone()[0]
    finally:
        cx.close()


def _raising_connect(exc):
    """A stand-in for sqlite3.connect that fails the way SQLite would."""
    def _connect(*_a, **_kw):
        raise exc
    return _connect


def _sqlite_error(cls, message: str, code: int, name: str):
    err = cls(message)
    err.sqlite_errorcode = code
    err.sqlite_errorname = name
    return err


# ── contention must NOT be read as corruption ─────────────────────────

def test_lock_contention_does_not_quarantine_a_healthy_database(tmp_path):
    """The measured production failure: a competing writer holds the DB,
    quick_check raises SQLITE_BUSY, and a healthy file is destroyed."""
    db = tmp_path / "downloader_history.db"
    _healthy_db(db)

    holder = sqlite3.connect(str(db), isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        result = auto_recover_sqlite(str(db))
    finally:
        holder.rollback()
        holder.close()

    assert db.exists(), (
        "a healthy database was quarantined because another connection held "
        "a lock -- this is the defect that cost the operator their history")
    assert _quarantined(tmp_path) == []
    assert result["status"] != "fail"
    assert _row_count(db) == 500


@pytest.mark.parametrize("cls, message, code, name", [
    (sqlite3.OperationalError, "database is locked", 5, "SQLITE_BUSY"),
    (sqlite3.OperationalError, "disk I/O error", 10, "SQLITE_IOERR"),
    (sqlite3.OperationalError, "unable to open database file", 14,
     "SQLITE_CANTOPEN"),
    (sqlite3.OperationalError, "attempt to write a readonly database", 8,
     "SQLITE_READONLY"),
])
def test_transient_sqlite_errors_do_not_quarantine(
        tmp_path, monkeypatch, cls, message, code, name):
    """Every one of these is contention or environment. None of them is
    evidence that the FILE is damaged, so none may move it aside."""
    db = tmp_path / "downloader_history.db"
    _healthy_db(db)
    before = db.read_bytes()

    monkeypatch.setattr(
        sqlite3, "connect",
        _raising_connect(_sqlite_error(cls, message, code, name)))
    result = auto_recover_sqlite(str(db))

    assert db.exists(), f"{name} must not be read as corruption"
    assert db.read_bytes() == before
    assert _quarantined(tmp_path) == []
    # Unknown is a third state and it must be VISIBLE, not silently OK.
    assert result["status"] == WARN
    assert message in result["message"] or message in str(result["detail"])


# ── genuine corruption must STILL be quarantined ──────────────────────

def test_garbage_bytes_are_still_quarantined(tmp_path):
    """SQLITE_NOTADB. The over-correction guard: a fix that stops
    quarantining altogether passes every contention test above."""
    db = tmp_path / "downloader_history.db"
    db.write_bytes(b"NOT A DATABASE AT ALL" * 100)

    result = auto_recover_sqlite(str(db))

    assert not db.exists(), "confirmed corruption must still be moved aside"
    assert len(_quarantined(tmp_path)) == 1
    assert result["status"] == WARN
    assert result["detail"]["backup_path"]


def test_mangled_pages_are_still_quarantined(tmp_path):
    """SQLITE_CORRUPT: a valid header over a destroyed body."""
    db = tmp_path / "downloader_history.db"
    _healthy_db(db, rows=2000)
    raw = bytearray(db.read_bytes())
    assert len(raw) > 8192, "setup: db too small to mangle a whole page"
    raw[4096:8192] = b"\xff" * 4096
    db.write_bytes(bytes(raw))

    # Setup precondition, so a setup regression cannot present as a
    # subject failure (a stub raising the same exception proves nothing).
    with pytest.raises(sqlite3.DatabaseError) as caught:
        sqlite3.connect(str(db)).execute("PRAGMA quick_check").fetchone()
    assert caught.value.sqlite_errorname in ("SQLITE_CORRUPT", "SQLITE_NOTADB")

    result = auto_recover_sqlite(str(db))

    assert not db.exists()
    assert len(_quarantined(tmp_path)) == 1
    assert result["status"] == WARN


def test_quick_check_reporting_damage_is_still_quarantined(
        tmp_path, monkeypatch):
    """quick_check can also RETURN the damage instead of raising."""
    db = tmp_path / "downloader_history.db"
    _healthy_db(db)

    class _Cursor:
        def fetchone(self):
            return ("*** in database main ***\nPage 4 is never used",)

    class _Conn:
        def execute(self, *_a, **_kw):
            return _Cursor()

        def close(self):
            pass

    monkeypatch.setattr(sqlite3, "connect", lambda *_a, **_kw: _Conn())
    result = auto_recover_sqlite(str(db))

    assert not db.exists()
    assert len(_quarantined(tmp_path)) == 1
    assert result["status"] == WARN


@pytest.mark.parametrize("code, name", [
    (11, "SQLITE_CORRUPT"),
    (26, "SQLITE_NOTADB"),
    (267, "SQLITE_CORRUPT_VTAB"),      # 11 | (1 << 8)
    (523, "SQLITE_CORRUPT_SEQUENCE"),  # 11 | (2 << 8)
    (779, "SQLITE_CORRUPT_INDEX"),     # 11 | (3 << 8)
])
def test_extended_corruption_codes_are_recognised(
        tmp_path, monkeypatch, code, name):
    """SQLite reports corruption through EXTENDED result codes too, which
    carry the primary code in the low 8 bits. Testing only the primary
    codes would let a fix that compares the whole integer pass."""
    db = tmp_path / "downloader_history.db"
    _healthy_db(db)

    monkeypatch.setattr(sqlite3, "connect", _raising_connect(
        _sqlite_error(sqlite3.DatabaseError, "database disk image is "
                      "malformed", code, name)))
    result = auto_recover_sqlite(str(db))

    assert not db.exists(), f"{name} must be recognised as corruption"
    assert len(_quarantined(tmp_path)) == 1
    assert result["status"] == WARN


def test_healthy_database_is_reported_ok_and_left_alone(tmp_path):
    db = tmp_path / "downloader_history.db"
    _healthy_db(db)

    result = auto_recover_sqlite(str(db))

    assert result["status"] == OK
    assert db.exists()
    assert _quarantined(tmp_path) == []


# ── the quarantine name must be unique ────────────────────────────────

def test_two_quarantines_in_the_same_second_both_survive(tmp_path):
    """int(time.time()) collides at one-second resolution and Path.rename
    overwrites silently, so the first file vanished without trace."""
    db = tmp_path / "downloader_history.db"

    db.write_bytes(b"FIRST-DB" * 100)
    first = auto_recover_sqlite(str(db))
    db.write_bytes(b"SECOND-DB" * 100)
    second = auto_recover_sqlite(str(db))

    assert first["detail"]["backup_path"] != second["detail"]["backup_path"]

    kept = _quarantined(tmp_path)
    assert len(kept) == 2, (
        f"one quarantine overwrote the other: {[p.name for p in kept]}")
    bodies = b"".join(p.read_bytes() for p in kept)
    assert b"FIRST-DB" in bodies and b"SECOND-DB" in bodies


def test_quarantine_carries_companions_under_the_new_basename(
        tmp_path, monkeypatch):
    """A quarantined database separated from its -wal has lost its most
    recent transactions, so the companions must follow it.

    connect() is stubbed to raise SQLITE_NOTADB so that SQLite never opens
    the files: a real open owns the -wal/-shm and rewrites or removes them
    on close, which would make this a measurement of SQLite's lifecycle
    rather than of the mover.
    """
    db = tmp_path / "downloader_history.db"
    db.write_bytes(b"NOT A DATABASE" * 100)
    (tmp_path / "downloader_history.db-wal").write_bytes(b"WAL-BODY")
    (tmp_path / "downloader_history.db-shm").write_bytes(b"SHM-BODY")

    monkeypatch.setattr(sqlite3, "connect", _raising_connect(
        _sqlite_error(sqlite3.DatabaseError, "file is not a database",
                      26, "SQLITE_NOTADB")))
    result = auto_recover_sqlite(str(db))
    backup = result["detail"]["backup_path"]

    from pathlib import Path
    assert Path(backup).exists()
    assert Path(backup + "-wal").read_bytes() == b"WAL-BODY"
    assert Path(backup + "-shm").read_bytes() == b"SHM-BODY"


def test_quarantine_leaves_no_companion_orphaned_at_the_old_path(tmp_path):
    """The hazard the move exists to prevent: a stale -wal left beside the
    path where db_init() is about to create a fresh database."""
    db = tmp_path / "downloader_history.db"
    db.write_bytes(b"NOT A DATABASE" * 100)
    (tmp_path / "downloader_history.db-wal").write_bytes(b"WAL-BODY")

    auto_recover_sqlite(str(db))

    assert not db.exists()
    assert not (tmp_path / "downloader_history.db-wal").exists()
    assert not (tmp_path / "downloader_history.db-shm").exists()


def test_missing_database_is_not_a_corruption(tmp_path):
    result = auto_recover_sqlite(str(tmp_path / "never_existed.db"))
    assert result["status"] == OK
    assert _quarantined(tmp_path) == []
