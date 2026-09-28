"""v3.66.1682 -- MOD3 shadow-read coverage via PG compatibility functions
(Row 127 cut 2, brief ROW127-CUT2-PG-COMPAT-20260926T1945Z).

DEFECT: live e3867228 shadow-read compared 111 / skipped 408. The dialect
layer (v3.66.1681) skipped every read calling a SQLite-only function
(strftime, julianday, group_concat, date/datetime on a column).

CORRECTION (pg_backend, read side only; mirror()/translate() untouched):
  1. _PG_COMPAT: datetime/date/julianday/strftime/ifnull/group_concat with
     SQLite 3.45 semantics, installed by _compat_ready() (ensure_schema, or
     the first shadow read needing one; idempotent CREATE OR REPLACE).
  2. _shadow_dialect passes those calls when their format and modifiers are
     ones the PG side implements; 'localtime' is pinned to the app's zone;
     CAST(.. AS REAL|INTEGER) keeps SQLite's 8-byte types; every % reaches
     psycopg escaped. A result computed from 'now' is skipped (now-in-result):
     two engines read the clock at different instants.
PROOF: byte-identical SQLite vs PG over edge values; the app's own statements,
driven through the real db proxy against a dual-written store, compare equal.
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tests"))

import mod3_pg_isolation

from bulk_downloader import pg_backend

_MODULE = "test_v3_66_1682_mod3_pg_compat.py"
_ZONE = "America/New_York"


def _pg_dsn() -> str:
    if not mod3_pg_isolation.real_dsn():
        pytest.skip("REAL-PG compat functions not verifiable here: "
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


def _zero_shadow(monkeypatch):
    for k in ("compared", "matched", "diverged", "skipped", "errors"):
        monkeypatch.setitem(pg_backend._shadow, k, 0)
    monkeypatch.setitem(pg_backend._shadow, "last_divergence", None)
    monkeypatch.setattr(pg_backend, "_shadow_errors_seen", set())
    monkeypatch.setattr(pg_backend, "_shadow_skip_reasons", {})


@pytest.fixture
def pg(monkeypatch, tmp_path):
    """Isolated schema, app SQLite store in tmp_path, compat state fresh."""
    dsn = _pg_dsn()
    monkeypatch.setenv("BD_INSTALL_DIR", str(tmp_path))
    monkeypatch.setenv("BD_HOME", str(tmp_path))
    monkeypatch.delenv("MOD3_CUTOVER", raising=False)
    monkeypatch.delenv("MOD3_SHADOW_READ", raising=False)
    monkeypatch.setenv("MOD3_PG_DSN", dsn)
    monkeypatch.setattr(pg_backend, "_compat", {"ok": False, "next_try": 0.0},
                        raising=False)
    monkeypatch.setitem(pg_backend._stats, "degraded_reason", None)
    _zero_shadow(monkeypatch)
    return dsn


@pytest.fixture
def local_zone():
    """SQLite's 'localtime' follows TZ; pin it (DST-observing on purpose)."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = _ZONE
    time.tzset()
    yield _ZONE
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


# -- 1. The functions: byte-identical to SQLite ----------------------------

_VALUES = [
    "2026-09-26T18:04:05", "2026-09-26 18:04:05", "2026-01-01T10:00:00Z",
    "2026-01-01T10:00:00+02:30", "2026-01-01T10:00:00 -01:00",
    "2026-01-01 10:00:00.5", "2026-01-01 10:00:00.1234567",
    "1969-12-31 23:59:59.5", "2023-02-31", "2026-01-01 ", "2026-01-01T",
    "2026-01-0112:00", "2026-03-08T07:30:00", "2026-11-01T05:30:00",
    # SQLite answers NULL for these
    None, "garbage", " 2026-01-01", "2026-13-01", "2026-01-01 10:00:60",
    "2026-01-01 10:00:00.", "2026-1-01"]
_MODS = [(), ("-1 hour",), ("+1.5 hours",), ("-0.0005 seconds",),
         ("1 day",), ("-7 days", "+3 minutes"),
         ("-1hour",)]    # invalid for SQLite too: NULL both sides
# ('+1 month' is valid SQLite the PG side does not implement: the dialect gate
# skips it, test_dialect_gate_names_what_it_will_not_send.)
_FMT = "%Y-%m-%d %H:%M:%S %f %s %j %w %F %T %d %m %%"


def test_time_functions_are_byte_identical_to_sqlite(pg):
    import psycopg
    assert pg_backend._compat_ready() is True
    lite = sqlite3.connect(":memory:")
    diffs, n = [], 0
    with psycopg.connect(pg) as c:
        for v in _VALUES:
            for mods in _MODS:
                for fn, head in (("datetime", ()), ("date", ()),
                                 ("julianday", ()), ("strftime", (_FMT,))):
                    args = (*head, v, *mods)
                    a = lite.execute(
                        f"SELECT {fn}({','.join('?' * len(args))})",
                        args).fetchone()[0]
                    b = c.execute(
                        f"SELECT {fn}({','.join(['%s::text'] * len(args))})",
                        args).fetchone()[0]
                    n += 1
                    if a != b:
                        diffs.append((fn, v, mods, a, b))
    assert n == len(_VALUES) * len(_MODS) * 4
    assert diffs == []
    # nonzero proof: most cases carry a value, not NULL == NULL
    nonnull = sum(lite.execute("SELECT datetime(?)", (v,)).fetchone()[0]
                  is not None for v in _VALUES)
    assert nonnull >= 14


def test_localtime_modifier_matches_sqlite_across_dst(pg, local_zone):
    import psycopg
    assert pg_backend._compat_ready() is True
    lite = sqlite3.connect(":memory:")
    with psycopg.connect(pg) as c:
        for v in ("2026-01-15T12:00:00", "2026-07-15T12:00:00",
                  "2026-03-08T06:59:59", "2026-03-08T07:00:00"):
            a = lite.execute("SELECT strftime('%H', ?, 'localtime'), "
                             "datetime(?, 'localtime')", (v, v)).fetchone()
            b = c.execute(
                "SELECT strftime('%%H', %s::text, %s), datetime(%s::text, %s)",
                (v, f"localtime:{local_zone}", v,
                 f"localtime:{local_zone}")).fetchone()
            assert a == b, (v, a, b)
    # positive control: the zone really moved the clock (EST vs EDT)
    assert lite.execute("SELECT strftime('%H', '2026-01-15T12:00:00', "
                        "'localtime')").fetchone()[0] == "07"


def test_ifnull_and_group_concat_match_sqlite(pg):
    import psycopg
    assert pg_backend._compat_ready() is True
    lite = sqlite3.connect(":memory:")
    rows = "(VALUES (1, 'a'), (NULL, NULL), (3, 'b')) v(x, y)"
    q_lite = ("SELECT group_concat(x), group_concat(x, '|'), group_concat(y), "
              "ifnull(NULL, 'z'), ifnull(2, 3) FROM (SELECT 1 x, 'a' y "
              "UNION ALL SELECT NULL, NULL UNION ALL SELECT 3, 'b')")
    q_pg = ("SELECT group_concat(x), group_concat(x, '|'), group_concat(y), "
            f"ifnull(NULL, 'z'), ifnull(2, 3) FROM {rows}")
    with psycopg.connect(pg) as c:
        assert tuple(c.execute(q_pg).fetchone()) \
            == lite.execute(q_lite).fetchone() == ("1,3", "1|3", "a,b", "z", 2)
        empty = c.execute("SELECT group_concat(x) FROM (SELECT 1 x "
                          "WHERE false) q").fetchone()[0]
    assert empty is None \
        and lite.execute("SELECT group_concat(1) WHERE 0").fetchone()[0] is None


# -- 2. Install: idempotent, parity intact, failure is a skip ---------------

def _compat_rows(dsn):
    import psycopg
    with psycopg.connect(dsn) as c:
        return sorted(c.execute(
            "SELECT p.oid::int, p.xmin::text, p.proname FROM pg_proc p "
            "WHERE p.pronamespace = current_schema()::regnamespace "
            "AND p.proname = ANY(%s)",
            (sorted(pg_backend._COMPAT_FUNCS | {"mod3_sqlite_ts"}),)).fetchall())


def test_ensure_schema_installs_once_and_second_run_is_a_noop(pg):
    from bulk_downloader import db
    db.db_init()
    assert pg_backend.ensure_schema() is True
    first = _compat_rows(pg)
    assert {r[2] for r in first} == pg_backend._COMPAT_FUNCS | {"mod3_sqlite_ts"}
    assert pg_backend.ensure_schema() is True
    assert _compat_rows(pg) == first             # untouched: same oid, xmin
    # a fresh process re-runs the CREATE OR REPLACE: same functions (oids)
    pg_backend._compat.update(ok=False, next_try=0.0)
    assert pg_backend.ensure_schema() is True
    assert [(o, n) for o, _x, n in _compat_rows(pg)] \
        == [(o, n) for o, _x, n in first]
    rep = pg_backend.schema_parity()
    assert "error" not in rep, rep
    assert all(not v["missing"] and not v["type_mismatch"]
               for v in rep.values()), rep


def test_install_failure_skips_compat_reads_never_errors(pg, monkeypatch):
    from bulk_downloader import db
    db.db_init()
    monkeypatch.setattr(pg_backend, "_PG_COMPAT",
                        (("CREATE FUNCTION no_such_language() RETURNS int "
                          "LANGUAGE nope AS 'x'"),))
    assert pg_backend.ensure_schema() is True    # the mirror is unaffected
    monkeypatch.setenv("MOD3_SHADOW_READ", "1")
    _zero_shadow(monkeypatch)
    pg_backend._compat.update(ok=False, next_try=0.0)
    sql = "SELECT strftime('%Y', ts) FROM history"
    assert pg_backend.shadow_compare(sql, (), []) is None
    st = pg_backend.shadow_stats()
    assert (st["skipped"], st["errors"], st["compared"]) == (1, 0, 0), st
    assert pg_backend.shadow_skip_reasons() == {"compat-missing": 1}
    # retried later, not on every read
    assert pg_backend._compat_ready() is False
    assert pg_backend._compat["next_try"] > time.monotonic()


# -- 3. The app's own statements, through the real db proxy -----------------

def _iso(delta, sep="T"):
    return (dt.datetime.now(dt.timezone.utc) + delta).strftime(
        f"%Y-%m-%d{sep}%H:%M:%S")


def _seed(dsn):
    """History + queue rows written through the app's dual-write proxy, so
    both stores hold the same ids and text; proven equal before any
    verdict."""
    import psycopg

    from bulk_downloader import db
    db.db_init()
    assert pg_backend.ensure_schema() is True
    with psycopg.connect(dsn) as c:
        c.execute("DELETE FROM history")
        c.execute("DELETE FROM queue")
        c.commit()
    m = dt.timedelta(minutes=1)
    hist = [
        ("cmp", "done", "a.bin", 100, "", _iso(-10 * m)),
        ("cmp", "failed", None, None, "boom: timeout", _iso(-120 * m, " ")),
        ("cmp", "done", "b.bin", 250, "", _iso(-180 * m) + ".250Z"),
        ("cmp", "done", "c.bin", 50, "", _iso(-26 * 60 * m)),
        ("cmp", "failed", None, None, "old: 404", _iso(-3 * 1440 * m)),
        ("cmp", "needs_review", "d.bin", 7, "", _iso(-10 * 1440 * m)),
        ("cmp", "done", "e.bin", 1, "", "2026-01-15T12:00:00"),
        ("cmp", "done", "f.bin", 2, "", "2026-07-15T12:00:00"),
        ("cmp", "pending", None, None, "", None),
    ]
    with db.db_conn() as cx:
        for r in hist:
            cx.execute("INSERT INTO history(site_id, url, status, filename, "
                       "file_size, message, ts) VALUES (?,?,?,?,?,?,?)",
                       (r[0], f"https://example.test/{r[2]}", *r[1:]))
        for url, st, added, upd in (
                ("u1", "done", _iso(-50 * m), _iso(-20 * m)),
                ("u2", "failed", _iso(-5 * 1440 * m), _iso(-4 * 1440 * m))):
            cx.execute("INSERT INTO queue(site_id, url, status, retries, "
                       "file_size, ts_added, ts_updated) "
                       "VALUES (?,?,?,?,?,?,?)",
                       ("cmp", url, st, 1, 10, added, upd))
    with db.db_conn() as cx:
        lite = {t: cx.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in ("history", "queue")}
    with psycopg.connect(dsn) as c:
        pgn = {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
               for t in ("history", "queue")}
    assert lite == pgn == {"history": 9, "queue": 2}, (lite, pgn)


def _app_reads():
    """(label, call) for every app reader whose SELECT used a function the
    v3.66.1681 dialect layer skipped, plus the T108 RED statement."""
    from bulk_downloader import (
        alerts_engine,
        bw_chart,
        cost_economics,
        db,
        site_changelog,
        timeline,
    )
    from bulk_downloader.dev_suite import db_tools
    now = time.time()
    return [
        ("alerts RED datetime('now','-1 hour')",
         lambda: alerts_engine._evaluate_metric("bd_failure_rate_1h")),
        ("bw_chart.hourly_bandwidth strftime bucket",
         lambda: bw_chart.hourly_bandwidth(hours=48, site_id="cmp")),
        ("bw_chart.hourly_jobs strftime bucket",
         lambda: bw_chart.hourly_jobs(hours=48)),
        ("db.db_hourly_success_rate strftime localtime",
         lambda: db.db_hourly_success_rate(site_id="cmp", since_days=400)),
        ("cost_economics julianday arithmetic",
         lambda: cost_economics.run_accounting_summary("cmp", window_days=30)),
        ("site_changelog._success_rate CAST(strftime('%s') AS REAL)",
         lambda: site_changelog._success_rate(
             "cmp", since_seconds=now - 86400 * 5, until_seconds=now)),
        ("site_changelog._new_failure_fingerprints",
         lambda: site_changelog._new_failure_fingerprints("cmp")),
        ("timeline strftime('%s') > ?",
         lambda: timeline._entries_from_history(now - 86400 * 2, 50)),
        ("db_tools.queue_throughput strftime bucket",
         lambda: db_tools.queue_throughput(hours=72, bucket="hour")),
    ]


def test_app_statements_compare_equal_through_the_proxy(
        pg, monkeypatch, local_zone):
    _seed(pg)
    monkeypatch.setenv("MOD3_SHADOW_READ", "1")
    _zero_shadow(monkeypatch)
    bad = []
    for label, call in _app_reads():
        before = pg_backend.shadow_stats()
        call()
        st = pg_backend.shadow_stats()
        if (st["compared"] <= before["compared"]
                or st["matched"] - before["matched"]
                != st["compared"] - before["compared"]
                or st["errors"] != before["errors"]):
            bad.append((label, before, st, pg_backend.shadow_skip_reasons()))
    assert bad == []
    st = pg_backend.shadow_stats()
    assert (st["diverged"], st["errors"]) == (0, 0), st
    assert st["compared"] >= len(_app_reads())
    reasons = pg_backend.shadow_skip_reasons()
    assert set(reasons) <= {"scope"}, reasons


def test_now_in_result_is_skipped_not_compared(pg, monkeypatch):
    """A value computed from 'now' in SQL: each engine reads its own clock,
    so it can never be compared."""
    import sqlite3

    from bulk_downloader import db
    _seed(pg)
    monkeypatch.setenv("MOD3_SHADOW_READ", "1")
    _zero_shadow(monkeypatch)
    sql = ("SELECT (strftime('%s', 'now') - strftime('%s', MIN(ts))) / 3600.0"
           " FROM history WHERE status = 'pending'")
    rows = sqlite3.connect(db._resolve_db_path()).execute(sql).fetchall()
    assert pg_backend.shadow_compare(sql, (), rows) is None
    st = pg_backend.shadow_stats()
    assert (st["errors"], st["compared"]) == (0, 0), st
    assert pg_backend.shadow_skip_reasons() == {"now-in-result": 1}


def test_oldest_pending_metric_reads_now_in_python_and_is_compared(
        pg, monkeypatch):
    """SOAK-COMPLETION coverage: alerts' oldest-pending metric was the app's
    only now-in-result read. It now reads the clock in Python, so the SELECT
    is shadow-compared (and can be proven) with the metric value unchanged."""
    import time

    from bulk_downloader import alerts_engine, db
    _seed(pg)
    with db.db_conn() as cx:            # dual-write: both stores move
        cx.execute("UPDATE history SET ts = '2026-01-01T00:00:00' "
                   "WHERE status = 'pending'")
    monkeypatch.setenv("MOD3_SHADOW_READ", "1")
    _zero_shadow(monkeypatch)
    monkeypatch.setattr(time, "time", lambda: 1767225600.0 + 36 * 3600)
    assert alerts_engine._evaluate_metric("bd_oldest_pending_hours") == 36.0
    st = pg_backend.shadow_stats()
    assert (st["errors"], st["compared"], st["matched"]) == (0, 1, 1), (
        st, pg_backend.shadow_skip_reasons())
    assert pg_backend.shadow_skip_reasons() == {}


def test_divergence_through_a_compat_function_is_detected_negative_control(
        pg, monkeypatch):
    """Compat functions must not make every answer agree: change one ts in PG
    only and the strftime bucket read diverges."""
    import psycopg

    from bulk_downloader import bw_chart
    _seed(pg)
    with psycopg.connect(pg) as c:
        c.execute("UPDATE history SET ts = %s WHERE filename = 'a.bin'",
                  (_iso(-dt.timedelta(hours=5)),))
        c.commit()
    monkeypatch.setenv("MOD3_SHADOW_READ", "1")
    _zero_shadow(monkeypatch)
    bw_chart.hourly_bandwidth(hours=48, site_id="cmp")
    st = pg_backend.shadow_stats()
    assert (st["diverged"], st["errors"]) == (1, 0), st


def _engage_cutover(pg, monkeypatch):
    """Cutover the way the app reaches it: dual-write (DSN present), shadow
    read on with >= 1 comparison recorded, MOD3_CUTOVER requested; then the
    fail-closed gate must POSITIVELY pass or the test is not testing it."""
    monkeypatch.setenv("MOD3_SHADOW_READ", "1")
    monkeypatch.setenv("MOD3_CUTOVER", "1")
    monkeypatch.setattr(pg_backend, "_serials_synced", False)
    monkeypatch.setitem(pg_backend._shadow, "compared", 1)
    monkeypatch.setitem(pg_backend._shadow, "diverged", 0)
    assert pg_backend.cutover_engaged() is True, pg_backend.preflight_cutover()


def _all_proven(monkeypatch):
    """Parity, not proof, is under test: every shape counts as proven (the
    proven gate has its own tests, test_soak_completion_scope)."""
    monkeypatch.setattr(pg_backend, "_is_proven", lambda sql: True)


_CUTOVER_READS = [
    ("SELECT count(*) FROM history WHERE ts >= datetime('now', ?)",
     ("-1 hour",)),
    ("SELECT date(ts) FROM history WHERE ts IS NOT NULL ORDER BY id", ()),
    ("SELECT IFNULL(status, 'x') FROM history ORDER BY id", ()),
    # aggregate order is insertion order in SQLite and unspecified in PG, and
    # an ordered subquery is out of read scope (O1435): one row per group.
    (("SELECT status, group_concat(url) FROM history GROUP BY status "
      "HAVING count(*) = 1 ORDER BY status"), ()),
    ("SELECT julianday(ts) > 0 FROM history WHERE ts IS NOT NULL ORDER BY id",
     ()),
]


def test_cutover_compat_function_reads_fall_back_under_the_allowlist(
        pg, monkeypatch):
    """O1440/O1441 (FUNCTION-ALLOWLIST, v3.66.1686): translate() refuses a
    SELECT calling a function outside _FUNCTION_ALLOWLIST, so every compat
    read is SQLite-served under cutover -- no PG round-trip, no degrade -- and
    stays shadow-compared. SQLite answers each of them (non-vacuous)."""
    import sqlite3

    from bulk_downloader import db
    _seed(pg)
    _engage_cutover(pg, monkeypatch)
    _all_proven(monkeypatch)
    monkeypatch.setattr(pg_backend, "_cutover_fallback_reasons",
                        {"scope": 0, "untranslatable": 0, "unproven": 0,
                         "unreachable": 0, "error": 0})
    lite = sqlite3.connect(db._resolve_db_path())
    for sql, params in _CUTOVER_READS:
        assert lite.execute(sql, params).fetchall(), sql
        assert pg_backend.read_authoritative(sql, params) is None, sql
    assert pg_backend.cutover_fallback_reasons()["untranslatable"] == \
        len(_CUTOVER_READS)
    assert pg_backend.stats()["degraded_reason"] is None
    # boundary: a derived table has no provable table set -> SQLite serves.
    before = pg_backend.cutover_fallback_reasons()["scope"]
    assert pg_backend.read_authoritative(
        "SELECT group_concat(url) FROM (SELECT url FROM history ORDER BY id) t",
        ()) is None
    assert pg_backend.cutover_fallback_reasons()["scope"] == before + 1


def test_cutover_strftime_still_falls_back_to_sqlite_without_degrading(
        pg, monkeypatch):
    """Declared boundary of this cut: translate() refuses strftime, so a
    cutover read that uses it is answered by SQLite (None), with no degraded
    reason -- the compat strftime is reachable from the shadow compare only.
    A future widening of translate() must flip this on purpose."""
    _seed(pg)
    _engage_cutover(pg, monkeypatch)
    _all_proven(monkeypatch)
    assert pg_backend.read_authoritative(
        "SELECT strftime('%Y-%m', ts) FROM history ORDER BY id") is None
    assert pg_backend.stats()["degraded_reason"] is None
    # ...and the very same connection does serve the non-strftime read.
    assert pg_backend.read_authoritative(
        "SELECT count(*) FROM history WHERE ts IS NOT NULL") is not None


def test_cutover_read_authoritative_divergence_is_visible_negative_control(
        pg, monkeypatch):
    """Parity is not tautological: the authoritative store is PG, so a row
    changed in PG only is what a cutover read returns, and it differs from
    SQLite. Also pins that PG really served it (not a SQLite fallback)."""
    import sqlite3

    import psycopg

    from bulk_downloader import db
    _seed(pg)
    with psycopg.connect(pg) as c:
        c.execute("UPDATE history SET status = NULL WHERE filename = 'a.bin'")
        c.commit()
    _engage_cutover(pg, monkeypatch)
    _all_proven(monkeypatch)
    sql = "SELECT COALESCE(status, 'x') FROM history WHERE filename = 'a.bin'"
    got = pg_backend.read_authoritative(sql)
    assert got is not None, pg_backend.stats()["degraded_reason"]
    assert [tuple(r) for r in got] == [("x",)]
    lite = sqlite3.connect(db._resolve_db_path())
    assert lite.execute(sql).fetchall() == [("done",)]


def test_percent_in_a_literal_reaches_postgres_intact(pg, monkeypatch):
    """Before cut 2 a LIKE '%..%' read reached psycopg unescaped -> error."""
    _seed(pg)
    monkeypatch.setenv("MOD3_SHADOW_READ", "1")
    _zero_shadow(monkeypatch)
    from bulk_downloader import db
    with db.db_conn() as cx:
        rows = cx.execute("SELECT message FROM history WHERE message LIKE "
                          "'%timeout%' AND site_id = ?", ("cmp",)).fetchall()
    assert len(rows) == 1
    st = pg_backend.shadow_stats()
    assert (st["compared"], st["matched"], st["errors"]) == (1, 1, 0), st


# -- 4. Dialect gate (no PG) -----------------------------------------------

@pytest.mark.parametrize("sql,params,want", [
    ("SELECT CAST(strftime('%s', ts) AS REAL) FROM history", (),
     "SELECT CAST(strftime('%%s', ts) AS double precision) FROM history"),
    ("SELECT CAST(strftime('%H', ts) AS INTEGER) h FROM history WHERE x=?",
     (1,),
     "SELECT CAST(strftime('%%H', ts) AS bigint) h FROM history WHERE x=%s"),
    ("SELECT 'a?%' FROM history WHERE y = ?", ("b",),
     "SELECT 'a?%%' FROM history WHERE y = %s"),
])
def test_dialect_output_is_psycopg_ready(sql, params, want):
    assert pg_backend._shadow_dialect(sql, params) == (want, None)


@pytest.mark.parametrize("sql,params,reason", [
    ("SELECT strftime(?, ts) FROM history", (None,), "dialect:strftime-format"),
    ("SELECT strftime('%W', ts) FROM history", (), "dialect:strftime-format"),
    ("SELECT datetime(ts, ?) FROM history", ("localtime",), "datetime"),
    ("SELECT datetime(ts, 'utc') FROM history", (), "datetime"),
    ("SELECT datetime() FROM history", (), "datetime"),
    ("SELECT datetime(ts, '+1 month') FROM history", (), "datetime"),
    ("SELECT date('now') FROM history", (), "now-in-result"),
    ("SELECT strftime('%s', ts) + 1 FROM history", (),
     "dialect:strftime-arith"),
    ("SELECT instr(url, 'x') FROM history", (), "dialect:instr"),
])
def test_dialect_gate_names_what_it_will_not_send(sql, params, reason):
    assert pg_backend._shadow_dialect(sql, params) == (None, reason)


def test_localtime_is_pinned_to_the_apps_zone(local_zone):
    sql, why = pg_backend._shadow_dialect(
        "SELECT strftime('%H', ts, 'localtime') FROM history")
    assert why is None and f"'localtime:{local_zone}'" in sql
