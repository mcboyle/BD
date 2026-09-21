"""O1164 / FLEET_RULE 47 -- shared-cache lock contention in the test DB fixture.

The gate-suites (application-safety) shard failed in SETUP with
``sqlite3.OperationalError: database table is locked: sqlite_master`` at
``fresh_app -> db_init()``.

``mode=memory&cache=shared`` gives every connection in a process ONE table-lock
namespace, and a shared-cache table lock raises SQLITE_LOCKED, which SQLite's
busy handler does not retry -- so ``PRAGMA busy_timeout=10000`` (db.py:701)
never fires and the failure is instant.

RULING O1171: the database STAYS IN RAM. tests/test_inmemory_sqlite_fixture.py
is the tracked gate for that and must stay green; putting the database on disk
would cure the contention by deleting the property the fixture exists to
guarantee. So the two remedies here are a name that cannot collide, and an
application-level retry of the OPEN -- the documented remedy for shared cache.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import os
import sqlite3
import threading
import time

import pytest


def test_the_database_is_still_in_ram(inmemory_sqlite):
    """O1171's constraint, asserted here too so this file can never drift into
    arguing for a disk database."""
    assert "mode=memory" in inmemory_sqlite, inmemory_sqlite
    assert "cache=shared" in inmemory_sqlite, inmemory_sqlite


def test_the_uri_cannot_collide_across_workers_or_tests(inmemory_sqlite):
    # tmp_path.name alone is just the leaf ("test_..._0"), which xdist repeats
    # verbatim in every worker. The pid and a per-process serial are what make
    # two databases that should be distinct actually distinct.
    assert f"row865-{os.getpid()}-" in inmemory_sqlite, inmemory_sqlite
    # ...-<pid>-<serial>-<leaf>: the serial is what separates two tests whose
    # tmp_path leaf is the same.
    serial = inmemory_sqlite.split("-")[2]
    assert serial.isdigit() and int(serial) >= 1, inmemory_sqlite


def test_db_init_waits_out_a_shared_cache_table_lock_instead_of_failing(inmemory_sqlite):
    """THE GATE. db_init() runs while another connection holds the table lock --
    exactly the fresh_app setup that failed in the gate-suites shard."""
    from bulk_downloader import db

    # check_same_thread=False: the releasing thread below must be able to roll
    # this back. Without it sqlite3 raises ProgrammingError in that thread, the
    # lock is never released, and the test measures its own bug.
    holder = sqlite3.connect(inmemory_sqlite, uri=True, check_same_thread=False)
    holder.execute("BEGIN")
    holder.execute("SELECT * FROM sqlite_master").fetchall()
    released = threading.Event()

    def release_soon():
        time.sleep(0.2)
        holder.rollback()
        released.set()

    t = threading.Thread(target=release_soon)
    t.start()
    try:
        db.db_init()          # instant SQLITE_LOCKED before this cut
    finally:
        t.join()
        holder.close()
    # It waited for the holder rather than racing past it.
    assert released.is_set()


def test_the_retry_gives_up_rather_than_hanging_forever(inmemory_sqlite):
    """NEGATIVE CONTROL. The retry must still be able to FAIL. A wrapper that
    swallowed every lock forever would hang the suite instead of reporting, and
    would make the gate above evidence of nothing."""
    holder = sqlite3.connect(inmemory_sqlite, uri=True)
    holder.execute("BEGIN")
    holder.execute("SELECT * FROM sqlite_master").fetchall()
    from bulk_downloader import db

    t0 = time.monotonic()
    try:
        with pytest.raises(sqlite3.OperationalError) as exc:
            db.db_init(_retry_seconds=0.05)     # never released
        assert "locked" in str(exc.value), exc.value
        assert time.monotonic() - t0 < 5.0      # gave up, did not hang
    finally:
        holder.rollback(); holder.close()


def test_the_retry_re_raises_an_error_that_is_not_a_lock(inmemory_sqlite, monkeypatch):
    """And it must not retry the wrong thing: a non-lock OperationalError is
    re-raised immediately, not swallowed for the whole deadline."""
    from bulk_downloader import db

    def boom():
        raise sqlite3.OperationalError("no such table: something_else")

    monkeypatch.setattr(db, "_db_init_once", boom)
    t0 = time.monotonic()
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        db.db_init(_retry_seconds=30.0)
    assert time.monotonic() - t0 < 1.0


def test_shared_cache_really_does_raise_without_a_retry(tmp_path):
    """The defect itself, on a URI this cut does not use, so the gate above is
    evidence of something. If SQLite ever stops raising here, this fails loudly."""
    uri = f"file:row865-control-{tmp_path.name}?mode=memory&cache=shared"
    anchor = sqlite3.connect(uri, uri=True)
    writer = sqlite3.connect(uri, uri=True)
    writer.execute("PRAGMA busy_timeout=10000")
    writer.execute("CREATE TABLE a(x)")
    writer.commit()
    holder = sqlite3.connect(uri, uri=True)
    holder.execute("BEGIN")
    holder.execute("SELECT * FROM sqlite_master").fetchall()
    t0 = time.monotonic()
    try:
        with pytest.raises(sqlite3.OperationalError) as exc:
            writer.execute("CREATE TABLE b(y)")
        assert "locked" in str(exc.value), exc.value
        # busy_timeout was 10s; SQLITE_LOCKED does not consult the busy handler.
        assert time.monotonic() - t0 < 1.0
    finally:
        holder.rollback(); holder.close(); writer.close(); anchor.close()
