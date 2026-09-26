"""v3.66.1678 -- MOD3 shadow-read scope vs PG schema (Row 127, cut A).

DEFECT (FINDING-ROW127-SHADOW-READ-bd-pm-A-20260926T1358Z.md, class 1):
  Stage 4 on v3.66.1677 with MOD3_SHADOW_READ=1: shadow_compare() had no
  table guard, so SELECTs on SQLite-only tables (cookie_relogin_log x27,
  alert_events x18, webhook_queue x15) went to Postgres -> UndefinedTable.
  In-scope schema errors (queue.ts_added -> UndefinedColumn x59, a surviving
  SQLite function -> UndefinedFunction x3) were likewise reported as
  'shadow read failed' degradation, flipping degraded_reason on every call.

CORRECTION:
  1. A SELECT is shadow-compared only when every FROM/JOIN table is in
     _MIRRORED_TABLES (derived from _PG_SCHEMA, the same set the write side
     uses). Out of scope, unparseable or subquery -> _shadow["skipped"] += 1
     BEFORE any Postgres connection.
  2. A Postgres error on an in-scope read counts as _shadow["errors"], never
     as divergence and never as degradation; each distinct message is logged
     once. Divergence stays 'both sides answered, rows differ'.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tests"))

import mod3_pg_isolation
from bulk_downloader import pg_backend

_MODULE = "test_v3_66_1678_mod3_shadow_scope.py"


def _pg_dsn() -> str:
    if not mod3_pg_isolation.real_dsn():
        pytest.skip("REAL-PG shadow scope not verifiable here: "
                    "no MOD3_PG_TEST_DSN in the environment")
    try:
        import psycopg
    except ImportError:
        pytest.skip("psycopg not installed (optional dep)")
    dsn = mod3_pg_isolation.dsn_for(_MODULE)
    if not dsn:
        pytest.skip("could not create isolated schema")
    try:
        with psycopg.connect(dsn, connect_timeout=5):
            return dsn
    except (psycopg.Error, OSError) as e:
        pytest.skip(f"postgres unreachable: {type(e).__name__}")


@pytest.fixture
def shadow(monkeypatch):
    """Real-PG shadow read armed on an isolated schema, counters zeroed, and
    every Postgres connection counted -- 'no PG round-trip' is asserted as
    'the connection was never opened'."""
    dsn = _pg_dsn()
    monkeypatch.setenv("MOD3_PG_DSN", dsn)
    monkeypatch.setenv("MOD3_SHADOW_READ", "1")
    assert pg_backend.ensure_schema() is True
    import psycopg
    with psycopg.connect(dsn) as c:
        c.execute("DELETE FROM history WHERE site_id = %s", ("scope-pos",))
        c.execute("INSERT INTO history(site_id, status) VALUES (%s, %s)",
                  ("scope-pos", "done"))
        c.commit()
        # nonzero fixture proof: the positive control has a row to compare
        n = c.execute("SELECT count(*) FROM history WHERE site_id = %s",
                      ("scope-pos",)).fetchone()[0]
    assert n == 1, n
    with pg_backend._lock:
        for k in ("compared", "matched", "diverged", "skipped", "errors"):
            pg_backend._shadow[k] = 0
        pg_backend._shadow["last_divergence"] = None
        pg_backend._stats["degraded_reason"] = None
    monkeypatch.setattr(pg_backend, "_shadow_errors_seen", set(),
                        raising=False)
    connects = []
    real_connect = pg_backend._connect

    def counting_connect():
        connects.append(1)
        return real_connect()

    monkeypatch.setattr(pg_backend, "_connect", counting_connect)
    return connects


# -- 1. Parser: the table set of a SELECT --------------------------------------

class TestReadTables:
    def test_single_and_qualified_and_quoted(self):
        rt = pg_backend._read_tables
        assert rt("SELECT ts FROM cookie_relogin_log") == {"cookie_relogin_log"}
        assert rt('SELECT * FROM "History" WHERE x=?') == {"history"}
        assert rt("SELECT * FROM main.queue q") == {"queue"}

    def test_comma_lists_and_joins(self):
        rt = pg_backend._read_tables
        assert rt("SELECT * FROM history h, queue AS q WHERE h.url=q.url") \
            == {"history", "queue"}
        assert rt("SELECT * FROM history h LEFT JOIN alert_events a "
                  "ON a.id=h.id JOIN captures c USING(url)") \
            == {"history", "alert_events", "captures"}
        assert rt("SELECT a FROM history UNION SELECT b FROM webhook_queue") \
            == {"history", "webhook_queue"}

    def test_unparseable_is_none(self):
        rt = pg_backend._read_tables
        assert rt("SELECT * FROM (SELECT * FROM history) t") is None
        assert rt("SELECT * FROM history WHERE id IN (SELECT id FROM queue)") \
            is None
        assert rt("SELECT * FROM json_each(?)") is None

    def test_literals_do_not_name_tables(self):
        assert pg_backend._read_tables(
            "SELECT * FROM history WHERE message = 'x FROM alert_events'") \
            == {"history"}

    def test_no_table_is_empty(self):
        assert pg_backend._read_tables("SELECT 1") == frozenset()

    def test_shared_scope_set_with_the_write_side(self):
        assert pg_backend._MIRRORED_TABLES == frozenset({
            "captures", "history", "host_throughput", "push_subscriptions",
            "queue", "session_history"})


# -- 2. Real Postgres: scope, positive control, error class --------------------

class TestRealPGShadowScope:
    @pytest.mark.parametrize("sql", [
        "SELECT ts FROM cookie_relogin_log",
        "SELECT * FROM alert_events ORDER BY id DESC LIMIT ?",
        "SELECT id FROM webhook_queue WHERE status = ?",
        "SELECT h.status FROM history h JOIN alert_events a ON a.id = h.id",
        "SELECT 1",
    ])
    def test_out_of_scope_read_is_skipped_before_any_pg_round_trip(
            self, shadow, sql):
        params = ("x",) if "?" in sql else ()
        assert pg_backend.shadow_compare(sql, params, [("a",)]) is None
        st = pg_backend.shadow_stats()
        assert shadow == [], "out-of-scope read opened a PG connection"
        assert st["skipped"] == 1, st
        assert (st["compared"], st["diverged"], st.get("errors", 0)) \
            == (0, 0, 0), st
        assert pg_backend.stats()["degraded_reason"] is None

    def test_in_scope_read_is_still_compared_positive_control(self, shadow):
        ok = pg_backend.shadow_compare(
            "SELECT status FROM history WHERE site_id = ?", ("scope-pos",),
            [("done",)])
        st = pg_backend.shadow_stats()
        assert ok is True, st
        assert shadow == [1]
        assert (st["compared"], st["matched"], st["diverged"],
                st["skipped"], st.get("errors", 0)) == (1, 1, 0, 0, 0), st

    def test_in_scope_divergence_still_detected_negative_control(self, shadow):
        ok = pg_backend.shadow_compare(
            "SELECT status FROM history WHERE site_id = ?", ("scope-pos",),
            [("TAMPERED",)])
        st = pg_backend.shadow_stats()
        assert ok is False
        assert (st["compared"], st["diverged"], st.get("errors", 0)) \
            == (1, 1, 0), st

    def test_in_scope_schema_error_is_an_error_not_divergence(
            self, shadow, caplog):
        caplog.set_level(logging.WARNING, logger=pg_backend.log.name)
        sql = "SELECT * FROM queue WHERE site_id = ? ORDER BY ord, ts_added"
        for _ in range(3):
            assert pg_backend.shadow_compare(sql, ("s",), []) is None
        fn = "SELECT julianday(ts) FROM history WHERE site_id = ?"
        assert pg_backend.shadow_compare(fn, ("scope-pos",), [(1.0,)]) is None
        st = pg_backend.shadow_stats()
        assert shadow == [1, 1, 1, 1], "in-scope reads must reach PG"
        assert (st.get("errors", 0), st["diverged"], st["compared"],
                st["matched"]) == (4, 0, 0, 0), st
        assert pg_backend.stats()["degraded_reason"] is None, (
            "a schema error on one read flipped the store-wide degradation")
        msgs = [r.getMessage() for r in caplog.records
                if "shadow" in r.getMessage().lower()]
        assert len([m for m in msgs if "ts_added" in m]) == 1, msgs
        assert len([m for m in msgs if "julianday" in m]) == 1, msgs
