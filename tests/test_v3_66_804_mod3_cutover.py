"""v3.66.804 -- MOD-3 cut 5 of 5: cutover + rollback.

This is the cut that can lose data, so its centre of gravity is the PREFLIGHT
REFUSAL, not the flip. Anyone can write the flip.

The failure this cut exists to make impossible: cutting over because shadow-read
reported "0 divergences" when it had performed ZERO comparisons. That number is
truthful, clean, and catastrophic -- it is the empty denominator wearing a green
badge, and here it would authorise moving the authoritative store. Cut 3 built
`compared` precisely so this preflight could demand it, and the demand is the
point of cut 5. `preflight_cutover()` therefore requires `compared > 0` AND
`diverged == 0`; either alone is not evidence.

Design for reversibility, not for confidence:
  * FAIL-CLOSED. `cutover_engaged()` is false unless preflight positively
    passes. An unverifiable precondition never reads as permission.
  * WRITES CONTINUE TO SQLITE while cut over. Postgres becomes authoritative
    for READS first; the old store stays current so rollback is a flag flip
    with nothing to reconcile. A cutover you cannot walk back is not a
    migration step, it is a leap.
  * ROLLBACK IS PROVEN, NOT HOPED: the gate flips forward, writes, flips back,
    and requires SQLite to hold the data.

HONEST CEILING: this cut delivers the MECHANISM. The real exit criterion is the
tracker's EXIT-3 row -- full on-stash suite green post-cutover plus an operator
soak -- and it stays open and operator-bound. Nothing here shortens it. The
real-Postgres classes SKIP on stash with the reason named; binding there are the
default-off, fail-closed and denominator-refusal properties, which are the ones
that keep a cutover from happening by accident.
"""
from __future__ import annotations

import importlib
import os

import pytest


import sys as _sys
_sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import mod3_pg_isolation   # noqa: E402  (sibling helper, path set above)

_MODULE = "test_v3_66_804_mod3_cutover.py"


def _pg_available():
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

    An AMBIENT value makes every test in the run share ONE history DB (see
    test_v3_66_800_mod3_dual_write.py for the measured failure signature):
    a leftover co-1 row makes db_init()'s FTS backfill count shadow-diverge
    from the freshly-cleared mirror, the preflight refuses, and
    cutover_engaged() correctly reports False -- failing this file's cutover
    tests for a reason that is not in the code under test. The parent's
    environment is part of this suite's denominator, so the var is pinned
    rather than inherited."""
    monkeypatch.setenv("BD_INSTALL_DIR", str(tmp_path))


def _reload(monkeypatch, tmp_path, dsn=None, shadow=None, cutover=None):
    monkeypatch.setenv("BD_HOME", str(tmp_path))
    for name, val in (("MOD3_PG_DSN", dsn), ("MOD3_SHADOW_READ", shadow),
                      ("MOD3_CUTOVER", cutover)):
        if val is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, val)
    from bulk_downloader import db, pg_backend
    importlib.reload(pg_backend)
    importlib.reload(db)
    return db, pg_backend


class TestDefaultOffAndFailClosed:
    def test_cutover_off_by_default(self, monkeypatch, tmp_path):
        _, pg = _reload(monkeypatch, tmp_path,
                        dsn="postgresql://x@127.0.0.1:1/x")
        assert pg.cutover_engaged() is False

    def test_flag_alone_does_not_engage_cutover(self, monkeypatch, tmp_path):
        """FAIL-CLOSED: the operator asking for cutover is not the same as the
        preconditions being met. An unverifiable precondition must never read
        as permission."""
        _, pg = _reload(monkeypatch, tmp_path,
                        dsn="postgresql://nobody@127.0.0.1:1/none",
                        shadow="1", cutover="1")
        assert pg.cutover_engaged() is False

    def test_preflight_refuses_without_dual_write(self, monkeypatch, tmp_path):
        _, pg = _reload(monkeypatch, tmp_path, dsn=None, cutover="1")
        r = pg.preflight_cutover()
        assert r["ok"] is False and r.get("reasons"), r


class TestEmptyDenominatorRefusal:
    """THE control this cut exists for -- engine-free, so binding on stash."""

    def test_zero_comparisons_is_not_evidence_of_agreement(
            self, monkeypatch, tmp_path):
        """0 divergences over 0 comparisons must REFUSE. This is the exact
        shape that would otherwise authorise moving the authoritative store on
        no evidence at all."""
        _, pg = _reload(monkeypatch, tmp_path,
                        dsn="postgresql://x@127.0.0.1:1/x",
                        shadow="1", cutover="1")
        st = pg.shadow_stats()
        assert st["compared"] == 0 and st["diverged"] == 0, st
        r = pg.preflight_cutover()
        assert r["ok"] is False, (
            "preflight authorised cutover on ZERO comparisons -- the empty "
            "denominator wearing a green badge: %s" % r)
        joined = " ".join(r.get("reasons") or []).lower()
        assert "compar" in joined, (
            "the refusal must NAME the empty comparison denominator: %s" % r)

    def test_preflight_reports_the_numbers_it_judged_on(
            self, monkeypatch, tmp_path):
        _, pg = _reload(monkeypatch, tmp_path,
                        dsn="postgresql://x@127.0.0.1:1/x",
                        shadow="1", cutover="1")
        r = pg.preflight_cutover()
        for k in ("ok", "reasons", "checks"):
            assert k in r, f"preflight result lacks {k!r}: {r}"
        assert "shadow_compared" in r["checks"], r["checks"]


class TestRealCutover:
    def _dsn(self):
        ok, why = _pg_available()
        if not ok:
            pytest.skip(f"REAL-PG cutover not verifiable here: {why}")
        return why

    def _prepare(self, monkeypatch, tmp_path, cutover=None):
        """Bring the system to a genuinely cutover-eligible state: dual-write
        on, schema present, rows migrated-equal, and shadow comparisons that
        actually happened."""
        dsn = self._dsn()
        db, pg = _reload(monkeypatch, tmp_path, dsn=dsn, shadow="1",
                         cutover=cutover)
        import psycopg
        pg.ensure_schema()
        with psycopg.connect(dsn) as c:
            c.execute("DELETE FROM history")
            c.commit()
        db.db_init()
        with db.db_conn() as cx:
            cx.execute("INSERT INTO history(site_id, url, status) "
                       "VALUES (?,?,?)", ("co-1", "http://x.invalid/1", "done"))
        with db.db_conn() as cx:          # a REAL comparison, so compared > 0
            cx.execute("SELECT site_id, url, status FROM history "
                       "WHERE site_id=?", ("co-1",)).fetchall()
        return db, pg, dsn

    def test_preflight_passes_when_everything_is_verified(
            self, monkeypatch, tmp_path):
        db, pg, _ = self._prepare(monkeypatch, tmp_path)
        r = pg.preflight_cutover()
        assert r["checks"]["shadow_compared"] > 0, r
        assert r["ok"] is True, r

    def test_writes_still_reach_sqlite_while_cut_over(
            self, monkeypatch, tmp_path):
        """The rollback safety net. If cutover stopped writing to SQLite, the
        old store would go stale and rollback would lose data."""
        db, pg, _ = self._prepare(monkeypatch, tmp_path, cutover="1")
        assert pg.cutover_engaged() is True, pg.preflight_cutover()
        with db.db_conn() as cx:
            cx.execute("INSERT INTO history(site_id, status) VALUES (?,?)",
                       ("post-cutover", "done"))
        import sqlite3
        raw = sqlite3.connect(db._resolve_db_path())
        try:
            got = raw.execute(
                "SELECT status FROM history WHERE site_id=?",
                ("post-cutover",)).fetchone()
        finally:
            raw.close()
        assert got is not None, (
            "a post-cutover write never reached SQLite -- rollback would lose "
            "it")

    def test_rollback_restores_sqlite_authority_without_loss(
            self, monkeypatch, tmp_path):
        """Flip forward, write, flip back, and require the data to be there."""
        db, pg, dsn = self._prepare(monkeypatch, tmp_path, cutover="1")
        with db.db_conn() as cx:
            cx.execute("INSERT INTO history(site_id, status) VALUES (?,?)",
                       ("rollback-row", "kept"))
        monkeypatch.delenv("MOD3_CUTOVER", raising=False)   # ROLL BACK
        importlib.reload(pg)
        importlib.reload(db)
        assert pg.cutover_engaged() is False
        with db.db_conn() as cx:
            row = cx.execute("SELECT status FROM history WHERE site_id=?",
                             ("rollback-row",)).fetchone()
        assert row is not None and row["status"] == "kept", (
            "data written during cutover was lost on rollback: %s" % (row,))

    def test_reads_are_actually_served_by_postgres_when_cut_over(
            self, monkeypatch, tmp_path):
        """Proves the cutover ROUTES, rather than merely reporting that it is
        engaged. A row is planted in Postgres ONLY -- if the read still came
        from SQLite it could not be seen."""
        db, pg, dsn = self._prepare(monkeypatch, tmp_path, cutover="1")
        assert pg.cutover_engaged() is True
        import psycopg
        with psycopg.connect(dsn) as c:
            c.execute("INSERT INTO history(site_id, status) VALUES (%s,%s)",
                      ("pg-only", "from-postgres"))
            c.commit()
        with db.db_conn() as cx:
            row = cx.execute("SELECT site_id, status FROM history "
                             "WHERE site_id=?", ("pg-only",)).fetchone()
        assert row is not None, (
            "cutover is engaged but the read was still served by SQLite")
        assert row["status"] == "from-postgres", row

    def test_rows_support_column_name_access(self, monkeypatch, tmp_path):
        """Consumers index by column name (sqlite3.Row semantics). Bare tuples
        would not fail here -- they would raise deep inside unrelated call
        sites, which is worse than not cutting over."""
        db, pg, _ = self._prepare(monkeypatch, tmp_path, cutover="1")
        with db.db_conn() as cx:
            row = cx.execute("SELECT site_id, url, status FROM history "
                             "WHERE site_id=?", ("co-1",)).fetchone()
        assert row["site_id"] == "co-1" and row[0] == "co-1", row
        assert tuple(row)[0] == "co-1", tuple(row)

    def test_postgres_outage_falls_back_to_sqlite_not_empty(
            self, monkeypatch, tmp_path):
        """The dangerous conflation: 'could not serve' must not read as 'no
        rows'. An outage degrades to the old store, never to apparent data
        loss."""
        db, pg, _ = self._prepare(monkeypatch, tmp_path, cutover="1")
        monkeypatch.setattr(pg, "read_authoritative",
                            lambda sql, params=(): None)
        with db.db_conn() as cx:
            row = cx.execute("SELECT site_id, status FROM history "
                             "WHERE site_id=?", ("co-1",)).fetchone()
        assert row is not None, (
            "a Postgres outage during cutover surfaced as an EMPTY result "
            "instead of falling back to SQLite")
