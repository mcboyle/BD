"""v3.66.803 -- MOD-3 cut 4 of 5: migration REHEARSAL.

Cuts 2/3 mirror new writes and compare reads. Neither moves the data that was
already in SQLite before dual-write was switched on, and neither answers the
question cutover actually depends on: *would a full migration succeed, and would
the result be equal?* Cut 4 rehearses it -- backfill into a SCRATCH Postgres
schema, verify, report, tear down -- so the answer is measured before cut 5
commits to it.

This deliberately inherits the X-AUTO-1 @706 contract from
`backup_verify.rehearse()`, including its central judgement:

    "NOT-ok is the honest answer for: an unrestorable archive, AND no backup at
     all. 'No backup on disk' is not 'fine' -- it is the loudest possible
     failure of a backup system, and it must never read as ok."

The migration analogue: **a rehearsal over an EMPTY source is not ok.** Zero rows
migrated with zero mismatches is arithmetically perfect and epistemically
worthless -- it is the empty denominator wearing a green badge. It must report
not-ok with a reason that says so.

The second trap is the one this session already paid for once: **equal counts
can mask a swap.** A verifier that compares row COUNTS is clean and blind. So
verification compares CONTENT, and the gate falsifies it with a planted
same-count, different-content divergence -- if the verifier cannot fail that, it
proves nothing when it passes.

Isolation: the rehearsal runs in a scratch schema and must not disturb the live
shadow data cut 3 compares against, or the rehearsal corrupts the very signal
cut 5 will read.

HONEST CEILING (unchanged): real-Postgres classes SKIP on stash with the reason
named. Binding on stash are the never-raise contract, the empty-source verdict,
and the no-Postgres verdict -- all engine-free.
"""
from __future__ import annotations

import importlib
import os

import pytest


def _pg_available():
    dsn = os.environ.get("MOD3_PG_DSN") or os.environ.get("MOD3_PG_TEST_DSN")
    if not dsn:
        return False, "no MOD3_PG_TEST_DSN in the environment"
    try:
        import psycopg
    except ImportError:
        return False, "psycopg not installed (optional dep; absent on stash)"
    try:
        with psycopg.connect(dsn, connect_timeout=5):
            return True, dsn
    except Exception as e:
        return False, f"postgres unreachable: {type(e).__name__}"


def _reload(monkeypatch, tmp_path, dsn=None):
    monkeypatch.setenv("BD_HOME", str(tmp_path))
    if dsn is None:
        monkeypatch.delenv("MOD3_PG_DSN", raising=False)
    else:
        monkeypatch.setenv("MOD3_PG_DSN", dsn)
    monkeypatch.delenv("MOD3_SHADOW_READ", raising=False)
    from bulk_downloader import db, pg_backend
    importlib.reload(pg_backend)
    importlib.reload(db)
    return db, pg_backend


class TestHarnessContract:
    """The @706 posture, engine-free and therefore binding on stash."""

    def test_never_raises_without_postgres(self, monkeypatch, tmp_path):
        _, pg = _reload(monkeypatch, tmp_path, dsn=None)
        r = pg.rehearse_migration()          # must not raise
        assert isinstance(r, dict) and r["ok"] is False
        assert r.get("error"), "a not-ok rehearsal must say WHY"

    def test_never_raises_with_an_unreachable_postgres(
            self, monkeypatch, tmp_path):
        db, pg = _reload(monkeypatch, tmp_path,
                         dsn="postgresql://nobody@127.0.0.1:1/none")
        db.db_init()
        r = pg.rehearse_migration()
        assert r["ok"] is False and r.get("error")

    def test_result_carries_its_own_denominator(self, monkeypatch, tmp_path):
        """A verdict without the counts it was computed over is unreadable --
        the same reason shadow_stats() exposes `compared`."""
        _, pg = _reload(monkeypatch, tmp_path, dsn=None)
        r = pg.rehearse_migration()
        for k in ("ok", "rows_source", "rows_migrated", "mismatches", "error"):
            assert k in r, f"rehearsal result lacks {k!r}: {r}"


class TestEmptySourceIsNotOk:
    def test_empty_source_reports_not_ok(self, monkeypatch, tmp_path):
        """0 rows migrated with 0 mismatches is arithmetically perfect and
        proves nothing. Following backup_verify.rehearse(): the empty case is
        the loudest failure, never a pass."""
        ok, why = _pg_available()
        if not ok:
            pytest.skip(f"needs a real Postgres: {why}")
        db, pg = _reload(monkeypatch, tmp_path, dsn=why)
        db.db_init()                     # schema exists, ZERO rows
        r = pg.rehearse_migration()
        assert r["rows_source"] == 0, r
        assert r["ok"] is False, (
            "an empty-source rehearsal reported OK -- the empty denominator "
            "wearing a green badge")
        assert "empty" in (r.get("error") or "").lower(), r


class TestRealRehearsal:
    def _dsn(self):
        ok, why = _pg_available()
        if not ok:
            pytest.skip(f"REAL-PG rehearsal not verifiable here: {why}")
        return why

    def _seed(self, db, n):
        db.db_init()
        with db.db_conn() as cx:
            for i in range(n):
                cx.execute(
                    "INSERT INTO history(site_id, url, status) VALUES (?,?,?)",
                    (f"seed-{i}", f"http://example.invalid/{i}", "done"))

    def test_populated_source_rehearses_ok(self, monkeypatch, tmp_path):
        dsn = self._dsn()
        db, pg = _reload(monkeypatch, tmp_path, dsn=dsn)
        self._seed(db, 25)
        r = pg.rehearse_migration()
        assert r["ok"] is True, r
        assert r["rows_source"] == 25 and r["rows_migrated"] == 25, r
        assert r["mismatches"] == 0, r

    def test_a_planted_same_count_swap_is_detected(self, monkeypatch, tmp_path):
        """THE falsification. Corrupt the rehearsed copy so the ROW COUNT is
        unchanged but the CONTENT differs. A count-only verifier passes this
        and is therefore worthless; the verifier must fail it."""
        dsn = self._dsn()
        db, pg = _reload(monkeypatch, tmp_path, dsn=dsn)
        self._seed(db, 10)
        r = pg.rehearse_migration(_corrupt_for_test=True)
        assert r["rows_source"] == 10 and r["rows_migrated"] == 10, (
            "the corruption changed the COUNT -- this test would then pass for "
            "the wrong reason: %s" % r)
        assert r["mismatches"] > 0, (
            "a same-count content swap went UNDETECTED -- the verifier is "
            "counting rows, not comparing them: %s" % r)
        assert r["ok"] is False, r

    def test_rehearsal_does_not_disturb_the_live_shadow_rows(
            self, monkeypatch, tmp_path):
        """The rehearsal runs in a scratch schema. If it wrote into the live
        mirror, it would corrupt the very signal cut 3 compares and cut 5
        trusts."""
        dsn = self._dsn()
        import psycopg
        db, pg = _reload(monkeypatch, tmp_path, dsn=dsn)
        pg.ensure_schema()
        with psycopg.connect(dsn) as c:
            c.execute("DELETE FROM history WHERE site_id LIKE %s", ("seed-%",))
            c.execute("INSERT INTO history(site_id, status) VALUES (%s,%s)",
                      ("live-canary", "untouched"))
            c.commit()
        self._seed(db, 5)
        pg.rehearse_migration()
        with psycopg.connect(dsn) as c:
            row = c.execute(
                "SELECT status FROM history WHERE site_id = %s",
                ("live-canary",)).fetchone()
            n_seed = c.execute(
                "SELECT COUNT(*) FROM history WHERE site_id LIKE %s",
                ("seed-%",)).fetchone()[0]
        assert row is not None and row[0] == "untouched", (
            "the rehearsal modified live mirror data: %s" % (row,))
        assert n_seed == 5, (
            "rehearsal rows leaked into the LIVE mirror table (found %d); the "
            "scratch schema is not isolated" % n_seed)

    def test_rehearsal_cleans_up_after_itself(self, monkeypatch, tmp_path):
        """A rehearsal that leaves its scratch schema behind turns every later
        run into a comparison against stale debris."""
        dsn = self._dsn()
        import psycopg
        db, pg = _reload(monkeypatch, tmp_path, dsn=dsn)
        self._seed(db, 3)
        r = pg.rehearse_migration()
        scratch = r.get("scratch_schema")
        assert scratch, r
        with psycopg.connect(dsn) as c:
            left = c.execute(
                "SELECT COUNT(*) FROM information_schema.schemata "
                "WHERE schema_name = %s", (scratch,)).fetchone()[0]
        assert left == 0, f"scratch schema {scratch!r} was left behind"
