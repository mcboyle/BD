"""B1 — run-history advisory invariant.

The history store must NEVER fail a download. Every record_* entry point is
wrapped so that a DB error (locked, missing table, disk-full) is swallowed and
logged, not raised. If this invariant breaks, a history-table problem would
take down the actual download path — exactly backwards.

RED-first: run_history does not exist yet; the import 404s on pristine source.
The advisory wrappers (record_run_*_safe, or record_* being safe-by-contract)
are what GREEN must provide.
"""
from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_record_start_swallows_db_error():
    from bulk_downloader import db, run_history as rh

    orig = db.db_conn

    def _boom(*a, **k):
        raise RuntimeError("simulated db lock")

    db.db_conn = _boom
    try:
        # Must NOT raise — advisory. Returns a falsy run id on failure.
        rid = rh.record_run_start("s", "u")
        assert not rid, "failed start should return a falsy id, not raise"
    finally:
        db.db_conn = orig


def test_record_event_and_finish_swallow_db_error():
    from bulk_downloader import db, run_history as rh

    orig = db.db_conn

    def _boom(*a, **k):
        raise RuntimeError("simulated db lock")

    db.db_conn = _boom
    try:
        # Neither may raise even with a bogus run id and a broken connection.
        rh.record_run_event(123456, "progress", "x")
        rh.record_run_finish(123456, "done")
    finally:
        db.db_conn = orig
