"""v3.66.801 -- MOD-3 cut 3 of 5: shadow-read comparison (SQLite vs Postgres).

Cut 2 (@800) mirrors WRITES to Postgres. Cut 3 reads BOTH stores for the same
statement and compares, so divergence is measured BEFORE cutover (cut 5) rather
than discovered after it. SQLite remains authoritative and the caller's result
is never touched.

THE PROPERTY THIS CUT IS BUILT AROUND -- an unmeasurable comparison must report
UNKNOWN, never MATCH. A comparator that silently skips what it cannot translate
and then reports "0 divergences" is the dominant failure shape in this project:
truthful, clean, and useless, because the denominator excluded everything hard.
So `shadow_stats()` must expose `compared` alongside `diverged`: "0 diverged"
is only meaningful next to "compared > 0", and skips are counted separately and
never as agreement.

Ordering is the other trap in the opposite direction. Without ORDER BY the two
engines may return the same rows in different orders; a naive comparator calls
that a divergence, the noise gets ignored, and the gate dies of false alarms.
Comparison is therefore order-insensitive by normalisation, and that is pinned.

HONEST CEILING (unchanged from cut 2): the real-Postgres classes SKIP on stash
-- no driver, no server -- with the reason named. The binding stash gate covers
DEFAULT-OFF, FAIL-OPEN, caller-isolation and the UNKNOWN-not-MATCH property
(all engine-free); actual divergence detection is sandbox-proven against a real
PG 16. A green stash run is NOT evidence that comparison works.
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


def _reload(monkeypatch, tmp_path, dsn=None, shadow=None):
    monkeypatch.setenv("BD_HOME", str(tmp_path))
    if dsn is None:
        monkeypatch.delenv("MOD3_PG_DSN", raising=False)
    else:
        monkeypatch.setenv("MOD3_PG_DSN", dsn)
    if shadow is None:
        monkeypatch.delenv("MOD3_SHADOW_READ", raising=False)
    else:
        monkeypatch.setenv("MOD3_SHADOW_READ", shadow)
    from bulk_downloader import db, pg_backend
    importlib.reload(pg_backend)
    importlib.reload(db)
    return db, pg_backend


class TestDefaultOff:
    def test_shadow_read_off_without_the_flag(self, monkeypatch, tmp_path):
        _, pg = _reload(monkeypatch, tmp_path,
                        dsn="postgresql://x@127.0.0.1:1/x", shadow=None)
        assert pg.shadow_read_enabled() is False

    def test_shadow_read_requires_dual_write_too(self, monkeypatch, tmp_path):
        """Shadow-read against a store nothing has been written to would
        diverge on every row and teach nothing. The flag alone must not arm
        it."""
        _, pg = _reload(monkeypatch, tmp_path, dsn=None, shadow="1")
        assert pg.shadow_read_enabled() is False

    def test_no_comparison_happens_when_off(self, monkeypatch, tmp_path):
        db, pg = _reload(monkeypatch, tmp_path,
                         dsn="postgresql://x@127.0.0.1:1/x", shadow=None)
        db.db_init()
        with db.db_conn() as cx:
            cx.execute("SELECT COUNT(*) FROM history").fetchone()
        assert pg.shadow_stats()["compared"] == 0


class TestCallerIsolation:
    def test_unreachable_postgres_does_not_break_the_read(
            self, monkeypatch, tmp_path):
        db, pg = _reload(monkeypatch, tmp_path,
                         dsn="postgresql://nobody@127.0.0.1:1/none", shadow="1")
        db.db_init()
        with db.db_conn() as cx:
            cx.execute("INSERT INTO history(site_id, status) VALUES (?,?)",
                       ("iso-site", "done"))
        with db.db_conn() as cx:
            rows = cx.execute("SELECT site_id, status FROM history "
                              "WHERE site_id=?", ("iso-site",)).fetchall()
        assert len(rows) == 1 and rows[0]["status"] == "done", rows

    def test_divergent_postgres_never_changes_the_returned_rows(
            self, monkeypatch, tmp_path):
        """The strongest form of caller-isolation: even when the shadow store
        holds DIFFERENT data, the caller receives SQLite's rows unmodified.
        Proven by making the shadow return something else entirely."""
        db, pg = _reload(monkeypatch, tmp_path,
                         dsn="postgresql://x@127.0.0.1:1/x", shadow="1")
        db.db_init()
        with db.db_conn() as cx:
            cx.execute("INSERT INTO history(site_id, status) VALUES (?,?)",
                       ("truth", "sqlite-wins"))
        monkeypatch.setattr(pg, "_shadow_fetch",
                            lambda sql, params=(): [("LIE", "pg-wins")])
        with db.db_conn() as cx:
            rows = cx.execute("SELECT site_id, status FROM history "
                              "WHERE site_id=?", ("truth",)).fetchall()
        assert [tuple(r) for r in rows] == [("truth", "sqlite-wins")], rows


class TestUnknownIsNotMatch:
    """The anti-'clean but blind' controls. These need no engine, so they are
    binding on stash too -- deliberately, because they are the ones that keep
    a green report meaningful."""

    def test_untranslatable_statement_counts_as_skipped_not_matched(
            self, monkeypatch, tmp_path):
        db, pg = _reload(monkeypatch, tmp_path,
                         dsn="postgresql://x@127.0.0.1:1/x", shadow="1")
        before = pg.shadow_stats()
        pg.shadow_compare("SELECT strftime('%s','now') AS t", (), [(1,)])
        after = pg.shadow_stats()
        assert after["skipped"] == before["skipped"] + 1, after
        assert after["matched"] == before["matched"], (
            "an uncomparable statement was counted as agreement")
        assert after["compared"] == before["compared"], after

    def test_unreachable_shadow_counts_as_skipped_not_matched(
            self, monkeypatch, tmp_path):
        db, pg = _reload(monkeypatch, tmp_path,
                         dsn="postgresql://nobody@127.0.0.1:1/none", shadow="1")
        before = pg.shadow_stats()
        pg.shadow_compare("SELECT site_id FROM history", (), [("a",)])
        after = pg.shadow_stats()
        assert after["matched"] == before["matched"], (
            "a comparison against an unreachable store was counted as a match")
        assert after["compared"] == before["compared"], after

    def test_stats_expose_compared_so_zero_diverged_is_readable(
            self, monkeypatch, tmp_path):
        """'0 diverged' is only meaningful beside 'compared'. If a consumer
        cannot see the denominator, the number is decoration."""
        _, pg = _reload(monkeypatch, tmp_path,
                        dsn="postgresql://x@127.0.0.1:1/x", shadow="1")
        s = pg.shadow_stats()
        for k in ("compared", "matched", "diverged", "skipped"):
            assert k in s, f"shadow_stats() lacks {k!r}: {s}"


class TestComparisonSemantics:
    def test_row_order_is_not_a_divergence(self, monkeypatch, tmp_path):
        """Two engines may return the same rows in different orders without an
        ORDER BY. Calling that a divergence produces noise that gets ignored,
        which kills the gate as surely as blindness does."""
        _, pg = _reload(monkeypatch, tmp_path,
                        dsn="postgresql://x@127.0.0.1:1/x", shadow="1")
        a = [("b", 2), ("a", 1)]
        b = [("a", 1), ("b", 2)]
        assert pg._rows_equal(a, b) is True

    def test_a_real_difference_is_a_divergence(self, monkeypatch, tmp_path):
        _, pg = _reload(monkeypatch, tmp_path,
                        dsn="postgresql://x@127.0.0.1:1/x", shadow="1")
        assert pg._rows_equal([("a", 1)], [("a", 2)]) is False
        assert pg._rows_equal([("a", 1)], []) is False


class TestRealShadowRead:
    """Sandbox-only (see the module docstring)."""

    def _skip_unless_pg(self):
        ok, why = _pg_available()
        if not ok:
            pytest.skip(f"REAL-PG shadow-read not verifiable here: {why}")
        return why

    def test_agreeing_stores_compare_and_match(self, monkeypatch, tmp_path):
        dsn = self._skip_unless_pg()
        import psycopg
        db, pg = _reload(monkeypatch, tmp_path, dsn=dsn, shadow="1")
        pg.ensure_schema()
        with psycopg.connect(dsn) as c:
            c.execute("DELETE FROM history WHERE site_id = %s", ("agree",))
            c.commit()
        db.db_init()
        with db.db_conn() as cx:      # dual-write puts it in BOTH
            cx.execute("INSERT INTO history(site_id, status) VALUES (?,?)",
                       ("agree", "done"))
        before = pg.shadow_stats()
        with db.db_conn() as cx:
            cx.execute("SELECT site_id, status FROM history WHERE site_id=?",
                       ("agree",)).fetchall()
        after = pg.shadow_stats()
        assert after["compared"] > before["compared"], (
            "the read was never actually compared: %s" % after)
        assert after["diverged"] == before["diverged"], after

    def test_a_planted_divergence_is_detected(self, monkeypatch, tmp_path):
        """The gate's own falsification: break the shadow on purpose and
        require the comparator to NOTICE. A comparator that cannot fail here
        proves nothing when it reports agreement."""
        dsn = self._skip_unless_pg()
        import psycopg
        db, pg = _reload(monkeypatch, tmp_path, dsn=dsn, shadow="1")
        pg.ensure_schema()
        with psycopg.connect(dsn) as c:
            c.execute("DELETE FROM history WHERE site_id = %s", ("diverge",))
            c.commit()
        db.db_init()
        with db.db_conn() as cx:
            cx.execute("INSERT INTO history(site_id, status) VALUES (?,?)",
                       ("diverge", "done"))
        with psycopg.connect(dsn) as c:      # corrupt ONLY the shadow
            c.execute("UPDATE history SET status = %s WHERE site_id = %s",
                      ("TAMPERED", "diverge"))
            c.commit()
        before = pg.shadow_stats()
        with db.db_conn() as cx:
            rows = cx.execute("SELECT site_id, status FROM history "
                              "WHERE site_id=?", ("diverge",)).fetchall()
        after = pg.shadow_stats()
        assert after["diverged"] > before["diverged"], (
            "a planted divergence went UNDETECTED: %s" % after)
        assert [tuple(r) for r in rows] == [("diverge", "done")], (
            "the caller saw the tampered shadow data: %s" % rows)
