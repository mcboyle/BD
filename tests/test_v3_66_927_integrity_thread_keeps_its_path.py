"""The deep integrity check must verify the database it was SCHEDULED for.

Item 11's remaining half, and the last repo-root database writer. v3.66.926
moved every module-scope DB operation into boot_once(), so a bare import
creates nothing -- measured, zero on-disk opens with BD_DISABLE_KEEPALIVE set
AND unset. But a band still landed `downloader_history.db` at the repo root,
and the file had NO TABLES AT ALL, which is the tell: something opened a
connection without ever running db_init, and sqlite3.connect() creates the file
on contact.

TRACED, not reasoned. Wrapping sqlite3.connect with a stack recorder and
running one test file pinned it to exactly one caller: db.py's `_do_check`,
the body of `run_integrity_check()`. That function starts a FIRE-AND-FORGET
daemon thread (`_threading.Thread(target=_do_check, daemon=True,
name="bd-db-integrity")`) which is never joined, and `_do_check` calls
`db_conn()` -- which resolves DB_PATH AT CALL TIME, per db._resolve_db_path().

So the sequence is:

    1. a test points DB_PATH at a tmpdir and calls boot_once()
    2. boot_once() schedules the deep check on a background thread
    3. the test finishes and RESTORES DB_PATH to the bare relative default
    4. the thread finally runs, re-resolves, and gets the cwd -- the repo root
    5. sqlite3.connect() creates an empty database there

The check verifies a database nobody asked about, reports OK for it, and leaves
a file behind. That is CLAUDE.md section 0 in a background thread: an
instrument whose denominator is decided after the question was asked.

THE FIX IS TO CAPTURE THE PATH AT SCHEDULE TIME. `run_integrity_check` resolves
once, in the calling thread, and hands the resolved path to `_do_check`. A
check scheduled for database A then verifies database A even if the process
later points DB_PATH somewhere else -- which is the only reading of "run
integrity_check on the database" that is true at both ends of a thread
boundary.

WHY NOT GATE IT ON BD_DISABLE_KEEPALIVE. Because that is suppression, not a
fix: the leak would persist for the SERVICE, which runs with the flag unset,
and the tests would go green over a defect that still ships. The same
distinction v3.66.926 turned on.

BOTH DIRECTIONS. `test_the_check_still_examines_the_database` is the
over-correction guard -- a "fix" that simply stops running the check satisfies
every leak assertion here and destroys a corruption detector.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


@pytest.fixture
def db_at_tmp(monkeypatch):
    """Point DB_PATH at a tmpdir and yield (module, path, restore-callable).

    The restore is handed to the TEST rather than done in teardown, because the
    defect is precisely what happens when DB_PATH moves while a scheduled
    thread is in flight -- so the test has to control that moment.
    """
    import bulk_downloader.db as db

    original = db.DB_PATH
    tmp = Path(tempfile.mkdtemp(prefix="integ927_"))
    target = tmp / "scheduled.db"
    db.DB_PATH = str(target)
    db.db_init()
    # A clean debounce state, or force=True is the only way in and the test
    # would not exercise the normal path.
    try:
        yield db, target, (lambda: setattr(db, "DB_PATH", original))
    finally:
        db.DB_PATH = original


def test_the_check_verifies_the_database_it_was_scheduled_for(db_at_tmp, tmp_path,
                                                              monkeypatch):
    """RED: the thread re-resolves DB_PATH and lands on whatever the cwd is.

    cwd is moved to a tmpdir rather than left at the repo root, so a failure
    reports the defect instead of littering the checkout -- and BD_INSTALL_DIR
    is popped so rung 2 of _resolve_db_path cannot mask rung 3, which is the
    rung that actually bites (CLAUDE.md section 5).
    """
    db, scheduled, restore = db_at_tmp
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BD_INSTALL_DIR", raising=False)

    thread = db.run_integrity_check(force=True)
    assert thread is not None, "force=True must schedule a check"
    # The move that reproduces it: DB_PATH goes back to the bare relative
    # default while the scheduled check is still in flight.
    restore()
    thread.join(timeout=30)
    assert not thread.is_alive(), "integrity thread did not finish"

    stray = sorted(p.name for p in tmp_path.glob("*.db*"))
    assert stray == [], (
        f"the scheduled check created {stray} in the cwd -- it re-resolved "
        f"DB_PATH after the caller moved it, so it verified a database nobody "
        f"asked about and left the file behind")


def test_the_check_still_examines_the_database(db_at_tmp):
    """OVER-CORRECTION GUARD.

    A fix that stops running the check satisfies the leak test above and
    removes a corruption detector. sync=True runs it inline and returns the
    verdict, so this asserts the instrument still answers.
    """
    db, scheduled, _restore = db_at_tmp
    out = db.run_integrity_check(force=True, sync=True)
    assert isinstance(out, dict), out
    assert out.get("ok") is True, out
    assert out.get("result") == ["ok"], out


def test_a_scheduled_check_survives_a_later_db_path_move(db_at_tmp, tmp_path,
                                                         monkeypatch):
    """The positive half of the same property, stated over the RIGHT database.

    Not merely "no stray file" -- the check must still have examined the
    database it was scheduled for. Asserted by giving that database a real
    table and confirming the check reports ok for it after the move.
    """
    db, scheduled, restore = db_at_tmp
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BD_INSTALL_DIR", raising=False)

    with sqlite3.connect(scheduled) as cx:
        cx.execute("CREATE TABLE IF NOT EXISTS canary(id INTEGER PRIMARY KEY)")
        cx.execute("INSERT INTO canary(id) VALUES (1)")

    thread = db.run_integrity_check(force=True)
    restore()
    thread.join(timeout=30)

    # The scheduled database is intact and still the one that existed.
    #
    # NOT `immutable=1` here, and that is not a style choice. db_init puts the
    # database in WAL mode, and an immutable open SKIPS THE WAL -- so a row
    # committed but not yet checkpointed is invisible and the read returns
    # "no such table". Measured the hard way twice in one day: once recovering
    # a quarantined database (114 rows immutable, 151 with the WAL replayed),
    # and once here, in the test written by the person who had just measured
    # it. immutable is the right tool for surveying a file you must not touch;
    # it is the wrong tool for asserting what a writer just wrote.
    with sqlite3.connect(scheduled) as cx:
        assert cx.execute("SELECT COUNT(*) FROM canary").fetchone()[0] == 1
        assert cx.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert sorted(p.name for p in tmp_path.glob("*.db*")) == []
