"""v3.66.1681 -- MOD3 shadow-read must not send SQLite-dialect SQL to PG (Row 127).

DEFECT (FINDING-ROW127-SHADOW-DIALECT-bd-pm-A-20260926T1759Z.md, class 5):
  Stage 4 re-entry at 68f89c6a: alerts_engine's periodic
  "SELECT ... FROM history WHERE ts >= datetime('now', '-1 hour')" was sent to
  Postgres verbatim -> UndefinedFunction datetime(unknown, unknown), one
  shadow error a minute, so the soak signal could never be clean.

CORRECTION (pg_backend._shadow_dialect, read side only; mirror() untouched):
  1. datetime('now'[, offset]) / date('now'[, offset]) with a plain
     '<n> second|minute|hour|day' offset (literal or ? parameter) become the
     equivalent UTC to_char(now() ...) text; IFNULL( becomes COALESCE(.
  2. Every other function is either on the PG-compatible allowlist, on the
     explicit skip list, or unknown; skip-list and unknown functions (and
     unsupported modifiers) count as shadow 'skipped' with a reason in
     shadow_skip_reasons() -- never as errors, and before any PG round-trip.
  3. Census: every SELECT on mirrored tables in bulk_downloader/ is either
     translated or skipped for a named skip-list reason (no unknown idiom).
"""
from __future__ import annotations

import ast
import datetime as dt
import sqlite3
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tests"))

import mod3_pg_isolation
from bulk_downloader import pg_backend

_MODULE = "test_v3_66_1681_mod3_shadow_dialect.py"
_RED_SQL = "SELECT count(*) FROM history WHERE ts >= datetime('now', '-1 hour')"


def _iso(delta: dt.timedelta) -> str:
    return (dt.datetime.now(dt.timezone.utc) + delta).strftime("%Y-%m-%dT%H:%M:%S")


_ROWS = [("dial", "done", _iso(-dt.timedelta(minutes=10))),
         ("dial", None, _iso(-dt.timedelta(days=3)))]


def _pg_dsn() -> str:
    if not mod3_pg_isolation.real_dsn():
        pytest.skip("REAL-PG shadow dialect not verifiable here: "
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
def lite():
    """The same two history rows in SQLite: the authoritative answer."""
    cx = sqlite3.connect(":memory:")
    cx.execute("CREATE TABLE history(site_id TEXT, status TEXT, ts TEXT)")
    cx.executemany("INSERT INTO history VALUES (?,?,?)", _ROWS)
    yield cx
    cx.close()


@pytest.fixture
def shadow(monkeypatch):
    """Real-PG shadow read on an isolated schema holding exactly _ROWS;
    counters zeroed; PG connections counted."""
    dsn = _pg_dsn()
    monkeypatch.setenv("MOD3_PG_DSN", dsn)
    monkeypatch.setenv("MOD3_SHADOW_READ", "1")
    assert pg_backend.ensure_schema() is True
    import psycopg
    with psycopg.connect(dsn) as c:
        c.execute("DELETE FROM history")
        for r in _ROWS:
            c.execute("INSERT INTO history(site_id, status, ts) "
                      "VALUES (%s, %s, %s)", r)
        c.commit()
        n = c.execute("SELECT count(*) FROM history").fetchone()[0]
    assert n == 2, n       # nonzero fixture proof
    for k in ("compared", "matched", "diverged", "skipped", "errors"):
        monkeypatch.setitem(pg_backend._shadow, k, 0)
    monkeypatch.setitem(pg_backend._shadow, "last_divergence", None)
    monkeypatch.setitem(pg_backend._stats, "degraded_reason", None)
    monkeypatch.setattr(pg_backend, "_shadow_errors_seen", set())
    monkeypatch.setattr(pg_backend, "_shadow_skip_reasons", {}, raising=False)
    connects = []
    real_connect = pg_backend._connect

    def counting_connect():
        connects.append(1)
        return real_connect()

    monkeypatch.setattr(pg_backend, "_connect", counting_connect)
    return connects


def _compare(lite, sql, params=()):
    rows = lite.execute(sql, params).fetchall()
    return pg_backend.shadow_compare(sql, params, rows)


def _reasons():
    return pg_backend.shadow_skip_reasons() \
        if hasattr(pg_backend, "shadow_skip_reasons") else {}


# -- 1. Real Postgres ----------------------------------------------------------

class TestRealPGShadowDialect:
    def test_red_fixture_compares_equal(self, shadow, lite):
        """RED on 86734c77: UndefinedFunction -> errors == 1."""
        ok = _compare(lite, _RED_SQL)
        st = pg_backend.shadow_stats()
        assert (st.get("errors", 0), st["compared"], st["matched"],
                st["diverged"], st["skipped"]) == (0, 1, 1, 0, 0), st
        assert ok is True
        assert lite.execute(_RED_SQL).fetchone()[0] == 1   # nonzero answer

    @pytest.mark.parametrize("sql,params", [
        ("SELECT count(*) FROM history WHERE ts >= datetime('now', ?)",
         ("-1 hour",)),
        (("SELECT count(*) FROM history WHERE site_id = ? "
          "AND ts >= datetime('now', ?) AND ts < datetime('now')"),
         ("dial", "-24 hours")),
        ("SELECT count(*) FROM history WHERE ts >= date('now', ?)",
         ("-1 day",)),
        ("SELECT site_id, IFNULL(status, 'none') FROM history", ()),
        ("SELECT DISTINCT date(ts) FROM history", ()),
    ])
    def test_translated_idioms_compare_equal(self, shadow, lite, sql, params):
        assert _compare(lite, sql, params) is True
        st = pg_backend.shadow_stats()
        assert (st.get("errors", 0), st["compared"], st["matched"]) \
            == (0, 1, 1), st

    @pytest.mark.parametrize("sql,params,reason", [
        (("SELECT strftime('%Y-%m-%d %H:00', ts) AS h, COUNT(*) FROM history "
          "WHERE ts >= datetime('now', ?) GROUP BY h"), ("-1 day",),
         "dialect:strftime"),
        ("SELECT julianday('now') - julianday(ts) FROM history", (),
         "dialect:julianday"),
        ("SELECT group_concat(site_id) FROM history", (),
         "dialect:group_concat"),
        ("SELECT datetime(ts, '+1 hour') FROM history", (), "dialect:datetime"),
        ("SELECT count(*) FROM history WHERE ts >= datetime('now', ?)",
         ("start of day",), "modifier"),
        (("SELECT count(*) FROM history WHERE ts >= datetime('now', "
          "'localtime')"), (), "modifier"),
    ])
    def test_untranslatable_is_skipped_with_reason_not_error(
            self, shadow, lite, sql, params, reason):
        assert _compare(lite, sql, params) is None
        st = pg_backend.shadow_stats()
        assert shadow == [], "a skipped read opened a PG connection"
        assert (st["skipped"], st.get("errors", 0), st["compared"]) \
            == (1, 0, 0), st
        assert _reasons() == {reason: 1}

    def test_broken_statement_still_counts_as_error_negative_control(
            self, shadow):
        sql = "SELECT no_such_column FROM history"
        assert pg_backend.shadow_compare(sql, (), [(1,)]) is None
        st = pg_backend.shadow_stats()
        assert shadow == [1]
        assert (st.get("errors", 0), st["skipped"], st["diverged"]) \
            == (1, 0, 0), st


# -- 2. Census over the package ------------------------------------------------

def _select_literals():
    """(path, lineno, sql) for every string constant / f-string (holes -> ?)
    in bulk_downloader/ that is a SELECT statement."""
    out = []
    for path in sorted((_REPO / "bulk_downloader").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                s = node.value
            elif isinstance(node, ast.JoinedStr):
                s = "".join(v.value if isinstance(v, ast.Constant) else "?"
                            for v in node.values)
            else:
                continue
            if pg_backend._verb(s) == "SELECT":
                out.append((path.relative_to(_REPO), node.lineno, s))
    return out


def test_census_every_app_select_idiom_is_translated_or_skip_listed():
    translated, skipped, bad = [], [], []
    for path, line, sql in _select_literals():
        tables = pg_backend._read_tables(sql)
        if not tables or not tables <= pg_backend._MIRRORED_TABLES:
            continue
        pg_sql, reason = pg_backend._shadow_dialect(
            sql, ("-1 hour",) * sql.count("?"))
        if pg_sql is not None:
            translated.append((path, line, sql))
        elif reason.startswith("dialect:") or reason == "modifier":
            skipped.append((path, line, reason))
        else:
            bad.append((path, line, reason))
    assert not bad, f"unclassified SQLite idiom(s): {bad}"
    # positive control: the live RED statement is found and translated
    red = [t for t in translated if t[0].name == "alerts_engine.py"
           and "datetime('now', '-1 hour')" in t[2]]
    assert red, "census did not find alerts_engine's datetime('now') read"
    assert len(translated) >= 10 and len(skipped) >= 5, (
        len(translated), len(skipped))


def test_skip_list_and_allowlist_are_disjoint():
    assert not (pg_backend._SHADOW_SKIP_FUNCS & pg_backend._PG_SAME_FUNCS)
    assert {"strftime", "julianday", "group_concat"} \
        <= pg_backend._SHADOW_SKIP_FUNCS
