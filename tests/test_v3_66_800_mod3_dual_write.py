"""v3.66.800 -- MOD-3 cut 2 of 5: history-DB dual-write to Postgres.

Cut 1 (@795) made ``db._open_history_conn()`` THE single history-DB connection
point precisely so this cut has exactly one place to intercept. Cut 2 mirrors
history-DB WRITES to Postgres while SQLite stays authoritative; reads are
untouched (that is cut 3, shadow-read).

Three properties this suite pins, in descending order of how badly they hurt if
wrong:

1. **DEFAULT OFF.** With ``MOD3_PG_DSN`` unset there is no proxy, no import of
   psycopg, no contact. A staged migration must be invisible until switched on.
2. **FAIL-OPEN.** A Postgres that is down / misconfigured / absent must NEVER
   break the authoritative SQLite write. A mirror that can take down the primary
   is worse than no mirror.
3. **REAL ROUND-TRIP.** With dual-write on, a write through ``db_conn()`` lands
   in BOTH stores -- asserted against a REAL Postgres, never a mock. A mock
   would only prove the wrapper calls a mock.

SCOPE BOUNDARY (also pinned): DML only (INSERT/UPDATE/DELETE). The Postgres
schema is bootstrapped as explicit PG-dialect DDL rather than translating
SQLite's ``AUTOINCREMENT`` / ``strftime`` CREATE statements -- dialect
translation of DDL is where migrations acquire silent divergence. SELECT /
PRAGMA / CREATE / ALTER / VACUUM stay SQLite-only.

WHY ``MOD3_PG_DSN`` AND NOT A BD_-PREFIXED NAME (which is deliberately not
spelled out here): tools/config_surface_inventory.py
does a bare token scan for ``BD_[A-Z0-9_]+``, so a BD_-prefixed name -- even in
a string literal -- registers as an operator-tunable env var and owes FE
settings wiring plus a config_gui_manifest row (the ENV-TRANCHE footgun). This
flag is an internal staged-migration switch, not an operator surface, so it
follows the ``NETNS_NS`` precedent.

HONEST CEILING (read this before trusting a green stash run): the REAL
ROUND-TRIP class needs a live Postgres and psycopg, neither of which exists on
stash. Those tests SKIP there, loudly and with the reason. The binding stash
gate therefore verifies properties 1 and 2 only; property 3 is sandbox-proven.
That is a genuine denominator limit, stated rather than hidden -- do not read a
green stash suite as evidence that the round-trip works.
"""
from __future__ import annotations

import importlib
import os
import sqlite3

import pytest


import sys as _sys
_sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import mod3_pg_isolation   # noqa: E402  (sibling helper, path set above)

_MODULE = "test_v3_66_800_mod3_dual_write.py"


def _pg_available():
    """(available, reason). Unknown is a third state: a missing driver and an
    unreachable server are DIFFERENT skips and both are named."""
    # ISOLATED PER MODULE. Five MOD3 files run concurrently in capture.sh's
    # parallel lane against one database, and 804's whole-table
    # `DELETE FROM history` wiped rows another module had just written --
    # measured on the box at d2fa6bb as a one-in-three flake. See
    # tests/mod3_pg_isolation.py. The base DSN is read FIRST so the three skip
    # reasons stay distinguishable: absent, driverless, and unreachable are
    # different states and each is named.
    if not mod3_pg_isolation.real_dsn():
        return False, "no MOD3_PG_TEST_DSN in the environment"
    try:
        import psycopg
    except ImportError:
        return False, "psycopg not installed (optional dep; absent on stash)"
    dsn = mod3_pg_isolation.dsn_for(_MODULE)
    if not dsn:
        return False, "could not create this module's isolated schema"
    try:
        with psycopg.connect(dsn, connect_timeout=5):
            return True, dsn
    except Exception as e:
        return False, f"postgres unreachable: {type(e).__name__}"


@pytest.fixture(autouse=True)
def _isolated_history_db(tmp_path, monkeypatch):
    """Pin BD_INSTALL_DIR to this test's tmp_path (clean_workdir's pattern).

    db._resolve_db_path() prefers BD_INSTALL_DIR over cwd, so an AMBIENT value
    -- e.g. a probe-hygiene prefix on the pytest invocation itself -- makes
    every test in the run share ONE history DB. Rows then accumulate across
    tests, db_init()'s FTS backfill count shadow-diverges from the
    freshly-cleared Postgres mirror, and the cutover preflight refuses.
    Measured 2026-08-10: exporting the var turned this green 38-test battery
    into 6 failures with no code change. The parent's environment is part of
    this suite's denominator, so the var is pinned rather than inherited."""
    monkeypatch.setenv("BD_INSTALL_DIR", str(tmp_path))


class TestDefaultOff:
    def test_dual_write_is_off_without_a_dsn(self, monkeypatch):
        monkeypatch.delenv("MOD3_PG_DSN", raising=False)
        pg = importlib.import_module("bulk_downloader.pg_backend")
        importlib.reload(pg)
        assert pg.dual_write_enabled() is False

    def test_seam_returns_a_plain_sqlite_connection_when_off(self, monkeypatch, tmp_path):
        """No proxy object when the feature is off -- the default path must be
        byte-for-byte the pre-800 behaviour, not a wrapper that happens to
        forward."""
        monkeypatch.delenv("MOD3_PG_DSN", raising=False)
        monkeypatch.setenv("BD_HOME", str(tmp_path))
        from bulk_downloader import db
        importlib.reload(db)
        cx = db._open_history_conn()
        try:
            assert isinstance(cx, sqlite3.Connection), type(cx)
        finally:
            cx.close()


class TestFailOpen:
    def test_unreachable_postgres_does_not_break_the_sqlite_write(
            self, monkeypatch, tmp_path):
        """THE safety property. Dual-write ON, Postgres pointed at a dead port:
        the SQLite write must still commit and be readable."""
        monkeypatch.setenv("MOD3_PG_DSN",
                           "postgresql://nobody@127.0.0.1:1/nonexistent")
        monkeypatch.setenv("BD_HOME", str(tmp_path))
        from bulk_downloader import db, pg_backend
        importlib.reload(pg_backend)
        importlib.reload(db)
        db.db_init()
        with db.db_conn() as cx:
            cx.execute(
                "INSERT INTO history(site_id, url, status) VALUES (?,?,?)",
                ("failopen-site", "http://example.invalid/a", "done"))
        with db.db_conn() as cx:
            row = cx.execute(
                "SELECT site_id, status FROM history WHERE site_id=?",
                ("failopen-site",)).fetchone()
        assert row is not None, "the authoritative SQLite write was LOST"
        assert row["status"] == "done"

    def test_missing_driver_does_not_break_the_sqlite_write(
            self, monkeypatch, tmp_path):
        """Same property via the other absence: psycopg not importable at all
        (which is the stash condition). Must degrade to SQLite-only, silently
        to the caller and loudly in the module's own state."""
        monkeypatch.setenv("MOD3_PG_DSN", "postgresql://x@127.0.0.1:1/x")
        monkeypatch.setenv("BD_HOME", str(tmp_path))
        import builtins
        real_import = builtins.__import__

        def _no_psycopg(name, *a, **k):
            if name == "psycopg":
                raise ImportError("simulated: psycopg absent")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", _no_psycopg)
        from bulk_downloader import db, pg_backend
        importlib.reload(pg_backend)
        importlib.reload(db)
        db.db_init()
        with db.db_conn() as cx:
            cx.execute(
                "INSERT INTO history(site_id, url, status) VALUES (?,?,?)",
                ("nodriver-site", "http://example.invalid/b", "done"))
        with db.db_conn() as cx:
            row = cx.execute(
                "SELECT site_id FROM history WHERE site_id=?",
                ("nodriver-site",)).fetchone()
        assert row is not None, "SQLite write lost when psycopg was absent"


class TestRealRoundTrip:
    """Sandbox-only by construction (see the module docstring's HONEST CEILING).
    Every test here names its skip reason rather than reporting a silent pass."""

    def _skip_unless_pg(self):
        ok, why = _pg_available()
        if not ok:
            pytest.skip(f"REAL-PG round-trip not verifiable here: {why}")
        return why

    def test_insert_lands_in_both_stores(self, monkeypatch, tmp_path):
        dsn = self._skip_unless_pg()
        import psycopg
        monkeypatch.setenv("MOD3_PG_DSN", dsn)
        monkeypatch.setenv("BD_HOME", str(tmp_path))
        from bulk_downloader import db, pg_backend
        importlib.reload(pg_backend)
        importlib.reload(db)
        pg_backend.ensure_schema()
        with psycopg.connect(dsn) as c:
            c.execute("DELETE FROM history WHERE site_id = %s", ("rt-site",))
            c.commit()
        db.db_init()
        with db.db_conn() as cx:
            cx.execute(
                "INSERT INTO history(site_id, url, status) VALUES (?,?,?)",
                ("rt-site", "http://example.invalid/rt", "done"))
        # SQLite (authoritative)
        with db.db_conn() as cx:
            assert cx.execute("SELECT 1 FROM history WHERE site_id=?",
                              ("rt-site",)).fetchone() is not None
        # Postgres (mirror)
        with psycopg.connect(dsn) as c:
            got = c.execute(
                "SELECT status FROM history WHERE site_id = %s",
                ("rt-site",)).fetchone()
        assert got is not None, "the write did NOT reach Postgres"
        assert got[0] == "done", got

    def test_select_is_not_mirrored(self, monkeypatch, tmp_path):
        """Scope boundary: reads stay SQLite-only in cut 2. If a SELECT were
        mirrored, cut 3's shadow-read comparison would be measuring its own
        side effect."""
        dsn = self._skip_unless_pg()
        monkeypatch.setenv("MOD3_PG_DSN", dsn)
        monkeypatch.setenv("BD_HOME", str(tmp_path))
        from bulk_downloader import db, pg_backend
        importlib.reload(pg_backend)
        importlib.reload(db)
        db.db_init()
        mirrored = []
        monkeypatch.setattr(pg_backend, "mirror",
                            lambda sql, params=(): mirrored.append(sql))
        with db.db_conn() as cx:
            cx.execute("SELECT COUNT(*) FROM history").fetchone()
        assert mirrored == [], f"a read was mirrored to Postgres: {mirrored}"


class TestSeamStillHolds:
    def test_dual_write_did_not_add_a_second_connect_point(self):
        """The @795 invariant: db.py holds exactly one real sqlite3.connect
        CALL. Cut 2 must intercept AT the seam, not beside it.

        Counted via AST, not text: a plain substring count also matches the
        docstring at db.py:27 ('...which sqlite3.connect() resolves'), i.e. a
        denominator that includes prose. That first draft failed on pristine
        source -- the check was wrong, not the tree. The real @795 gate scans
        the AST for the same reason."""
        import ast
        import pathlib
        src = pathlib.Path(
            __file__).resolve().parent.parent / "bulk_downloader" / "db.py"
        tree = ast.parse(src.read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "connect"
                 and isinstance(n.func.value, ast.Name)
                 and n.func.value.id == "sqlite3"]
        assert len(calls) == 1, (
            f"db.py has {len(calls)} sqlite3.connect() CALL sites "
            f"(lines {[c.lineno for c in calls]}), expected 1")
