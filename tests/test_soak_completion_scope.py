"""Row PG-CUTOVER-SOAK-COMPLETION (O1422/O1443; brief
ROW127-SOAK-COMPLETION-20260926T1952Z) -- finish the soak after an early flip.

GAP: under cutover a NEWLY translatable statement was served by Postgres at
once, with no shadow-compare phase (the 22:07Z UndefinedFunction RED).
CORRECTION (pg_backend):
  1. proven-shapes gate: read_authoritative() serves a statement shape only
     after _PROVEN_MIN (20) consecutive clean shadow comparisons; a divergence
     demotes it (divergent until re-proven). Unproven -> None ("unproven"
     fallback) and the db.py seam shadow-compares it. The proven set persists
     in PG (mod3_proven_shapes); counts on /api/health .mod3.proven. PG runs
     the same _shadow_dialect() rendering the shadow compared.
  2. coverage: see test_v3_66_1682 (strftime compat, oldest-pending metric).
  3. Stage 6 metrics: .mod3.metrics (latency p50/p95/p99, read/write error
     rates, fallback incidents); probe columns: the harness candidate test.
  4. Stage 7: `python -m bulk_downloader.pg_backend soak-receipt`.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import sys
from collections import deque
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tests"))

import mod3_pg_isolation
from test_v3_66_1682_mod3_pg_compat import _engage_cutover, _seed

from bulk_downloader import pg_backend

_MODULE = "test_soak_completion_scope.py"
_SQL = "SELECT site_id, status FROM history WHERE site_id = ?"


@pytest.fixture(autouse=True)
def _fresh_soak_state(monkeypatch):
    """pg_backend state is process-global: every test starts empty.
    raising=False: on a base without the gate the tests fail on behaviour."""
    for name, value in (("_shapes", {}), ("_shapes_loaded", {"at": None}),
                        ("_proven_ddl", {"ok": False, "next_try": 0.0}),
                        ("_latency_ms", deque(maxlen=64)),
                        ("_reads", {"attempts": 0, "errors": 0})):
        monkeypatch.setattr(pg_backend, name, value, raising=False)
    monkeypatch.setattr(pg_backend, "_cutover_fallback_reasons",
                        {"scope": 0, "untranslatable": 0, "unproven": 0,
                         "unreachable": 0, "error": 0})
    for k in ("compared", "matched", "diverged", "skipped", "errors"):
        monkeypatch.setitem(pg_backend._shadow, k, 0)
    monkeypatch.setattr(pg_backend, "_shadow_skip_reasons", {})
    for k in ("mirrored", "skipped", "failed"):
        monkeypatch.setitem(pg_backend._stats, k, 0)
    monkeypatch.setitem(pg_backend._stats, "degraded_reason", None)


@pytest.fixture
def pg(monkeypatch, tmp_path):
    if not mod3_pg_isolation.real_dsn():
        pytest.skip("REAL-PG proven gate not verifiable here: "
                    "no MOD3_PG_TEST_DSN in the environment")
    psycopg = pytest.importorskip("psycopg")
    dsn = mod3_pg_isolation.dsn_for(_MODULE)
    if not dsn:
        pytest.skip("could not create isolated schema")
    try:
        psycopg.connect(dsn, connect_timeout=5).close()
    except (psycopg.Error, OSError) as e:
        pytest.skip(f"postgres unreachable: {type(e).__name__}")
    monkeypatch.setenv("BD_INSTALL_DIR", str(tmp_path))
    monkeypatch.setenv("BD_HOME", str(tmp_path))
    monkeypatch.delenv("MOD3_CUTOVER", raising=False)
    monkeypatch.delenv("MOD3_SHADOW_READ", raising=False)
    monkeypatch.setenv("MOD3_PG_DSN", dsn)
    monkeypatch.setattr(pg_backend, "_compat", {"ok": False, "next_try": 0.0},
                        raising=False)
    with psycopg.connect(dsn) as c:
        c.execute("DROP TABLE IF EXISTS mod3_proven_shapes")
        c.commit()
    return dsn


def _pg_rows(dsn, sql, params=()):
    import psycopg
    with psycopg.connect(dsn) as c:
        return c.execute(sql, params).fetchall()


def _plant_pg_only(dsn):
    """A row ONLY Postgres holds: seeing it proves PG served the read."""
    import psycopg
    with psycopg.connect(dsn) as c:
        c.execute("INSERT INTO history(site_id, status) VALUES (%s, %s)",
                  ("pg-only", "from-postgres"))
        c.commit()


def _app_read(sql=_SQL, params=("pg-only",)):
    from bulk_downloader import db
    with db.db_conn() as cx:
        return [tuple(r) for r in cx.execute(sql, params).fetchall()]


# -- 1. The proven gate ------------------------------------------------------

def test_shape_is_served_by_postgres_only_after_n_clean_comparisons(
        pg, monkeypatch):
    _seed(pg)
    _engage_cutover(pg, monkeypatch)
    _plant_pg_only(pg)
    # the planted row makes SQLite and PG disagree on 'pg-only': prove the
    # shape on a key both hold, then read the planted key.
    for i in range(pg_backend._PROVEN_MIN):
        assert _app_read(params=("cmp",)), "seeded rows missing"
        want = i + 1 >= pg_backend._PROVEN_MIN
        assert pg_backend._is_proven(_SQL) is want, i
    assert pg_backend.cutover_fallback_reasons()["unproven"] == \
        pg_backend._PROVEN_MIN
    assert pg_backend.shadow_stats()["compared"] >= pg_backend._PROVEN_MIN
    assert _app_read() == [("pg-only", "from-postgres")], (
        "a proven shape under cutover must be served by Postgres")
    assert _pg_rows(pg, "SELECT proven, divergent FROM mod3_proven_shapes "
                        "WHERE shape = %s", (pg_backend._shape(_SQL),)) \
        == [(True, False)]


def test_unproven_shape_is_shadow_compared_not_served_negative_control(
        pg, monkeypatch):
    """THE GAP (22:07Z): a never-compared read under engaged cutover was
    served by Postgres at once. Unproven -> SQLite serves (the PG-only row is
    invisible) and the seam compares it; marking it proven flips the SAME
    probe to PG, so the probe can say yes."""
    _seed(pg)
    _engage_cutover(pg, monkeypatch)
    _plant_pg_only(pg)
    assert _app_read() == [], "unproven shape was served by Postgres"
    assert pg_backend.cutover_fallback_reasons()["unproven"] == 1
    assert pg_backend.shadow_stats()["diverged"] == 1, "seam did not compare"
    monkeypatch.setitem(pg_backend._shadow, "diverged", 0)  # re-engage
    monkeypatch.setattr(pg_backend, "_is_proven", lambda sql: True)
    assert _app_read() == [("pg-only", "from-postgres")]


def test_divergence_demotes_and_marks_divergent_until_reproven(
        pg, monkeypatch):
    _seed(pg)
    ok_rows = _pg_rows(pg, "SELECT site_id, status FROM history "
                           "WHERE site_id = 'cmp'")
    assert ok_rows
    monkeypatch.setenv("MOD3_SHADOW_READ", "1")

    def compare(rows):
        return pg_backend.shadow_compare(_SQL, ("cmp",), rows)
    for _ in range(pg_backend._PROVEN_MIN):
        assert compare(ok_rows) is True
    assert pg_backend.proven_counts() == {
        "threshold": 20, "proven": 1, "unproven": 0, "divergent": 0}
    assert compare(ok_rows[1:]) is False
    assert pg_backend._is_proven(_SQL) is False
    assert pg_backend.proven_counts() == {
        "threshold": 20, "proven": 0, "unproven": 1, "divergent": 1}
    assert _pg_rows(pg, "SELECT proven, divergent, divergences FROM "
                        "mod3_proven_shapes") == [(False, True, 1)]
    for i in range(pg_backend._PROVEN_MIN):
        assert compare(ok_rows) is True
        assert pg_backend._is_proven(_SQL) is (
            i + 1 == pg_backend._PROVEN_MIN)
    assert pg_backend.proven_counts()["divergent"] == 0
    assert _pg_rows(pg, "SELECT proven, divergent, divergences FROM "
                        "mod3_proven_shapes") == [(True, False, 1)]


def test_proven_set_survives_a_restart_and_a_demotion_reaches_it(
        pg, monkeypatch):
    _seed(pg)
    monkeypatch.setenv("MOD3_SHADOW_READ", "1")
    rows = _pg_rows(pg, "SELECT site_id, status FROM history "
                        "WHERE site_id = 'cmp'")
    for _ in range(pg_backend._PROVEN_MIN):
        pg_backend.shadow_compare(_SQL, ("cmp",), rows)
    assert pg_backend._is_proven(_SQL)
    # restart: empty cache; the table is the only memory
    monkeypatch.setattr(pg_backend, "_shapes", {})
    monkeypatch.setattr(pg_backend, "_shapes_loaded", {"at": None})
    assert pg_backend._is_proven(_SQL) is False
    pg_backend._proven_sync()
    assert pg_backend._is_proven(_SQL) is True
    # a first sighting elsewhere never clobbers the proven row ...
    monkeypatch.setattr(pg_backend, "_shapes", {})
    pg_backend.shadow_compare(_SQL, ("cmp",), rows)
    assert _pg_rows(pg, "SELECT proven FROM mod3_proven_shapes") == [(True,)]
    # ... but a divergence elsewhere demotes it here on the next refresh
    pg_backend._proven_sync(force=True)
    assert pg_backend._is_proven(_SQL)
    import psycopg
    with psycopg.connect(pg) as c:
        c.execute("UPDATE mod3_proven_shapes SET proven = FALSE, "
                  "divergent = TRUE")
        c.commit()
    pg_backend._proven_sync(force=True)
    assert pg_backend._is_proven(_SQL) is False
    assert pg_backend.proven_counts()["divergent"] == 1


def test_authoritative_read_runs_the_proven_rendering(pg, monkeypatch):
    """translate() only rewrites placeholders: a literal % reached psycopg raw
    and the served read failed (error fallback + degraded). PG now runs the
    _shadow_dialect() text the shadow compared, % escaped."""
    _seed(pg)
    _engage_cutover(pg, monkeypatch)
    monkeypatch.setattr(pg_backend, "_is_proven", lambda sql: True,
                        raising=False)
    sql = ("SELECT site_id FROM history WHERE status LIKE 'do%' "
           "AND site_id = ? ORDER BY id")
    got = pg_backend.read_authoritative(sql, ("cmp",))
    assert pg_backend.stats()["degraded_reason"] is None
    assert got is not None, pg_backend.cutover_fallback_reasons()
    from bulk_downloader import db
    want = sqlite3.connect(db._resolve_db_path()).execute(
        sql, ("cmp",)).fetchall()
    assert [tuple(r) for r in got] == want and want


def test_shape_normalizes_whitespace_outside_literals_only():
    a = pg_backend._shape("SELECT  a,\n  b FROM t WHERE x = 'p  q'")
    assert a == pg_backend._shape("SELECT a, b FROM t WHERE x = 'p  q'  ")
    assert a != pg_backend._shape("SELECT a, b FROM t WHERE x = 'p q'")


def test_shape_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(pg_backend, "_SHAPES_MAX", 2)
    monkeypatch.setattr(pg_backend, "_persist_shape", lambda *a, **k: None)
    for n in range(3):
        pg_backend._record_shape(f"SELECT {n} FROM history", True)
    assert len(pg_backend._shapes) == 2
    assert not pg_backend._is_proven("SELECT 2 FROM history")


# -- 3. Stage 6 metrics ------------------------------------------------------

def test_metrics_count_real_reads_writes_and_errors(pg, monkeypatch):
    _seed(pg)                          # dual-written rows: mirror writes
    monkeypatch.setenv("MOD3_SHADOW_READ", "1")
    before = pg_backend.soak_metrics()
    assert before["writes"]["attempts"] > 0
    assert before["writes"]["error_rate"] == 0.0
    rows = _pg_rows(pg, "SELECT site_id, status FROM history "
                        "WHERE site_id = 'cmp'")
    assert pg_backend.shadow_compare(_SQL, ("cmp",), rows) is True
    assert pg_backend.shadow_compare(
        "SELECT no_such_column FROM history", (), [(1,)]) is None
    m = pg_backend.soak_metrics()
    assert m["reads"] == {"attempts": 2, "errors": 1, "error_rate": 0.5}
    lat = m["latency_ms"]
    assert lat["samples"] == before["latency_ms"]["samples"] + 2
    assert 0 <= lat["p50"] <= lat["p95"] <= lat["p99"]


def test_fallback_incidents_count_pg_failures_not_design():
    pg_backend._cutover_fallback("scope")
    pg_backend._cutover_fallback("unproven")
    pg_backend._cutover_fallback("untranslatable")
    assert pg_backend.soak_metrics()["fallback_incidents"] == 0
    pg_backend._cutover_fallback("unreachable")
    pg_backend._cutover_fallback("error")
    assert pg_backend.soak_metrics()["fallback_incidents"] == 2


@pytest.mark.parametrize("values,q,want", [
    ([], 50, None), ([7.0], 99, 7.0), (list(range(1, 101)), 50, 50),
    (list(range(1, 101)), 95, 95), (list(range(1, 101)), 99, 99),
    ([1.0, 2.0, 3.0], 50, 2.0), ([1.0, 2.0], 99, 2.0)])
def test_percentile_is_nearest_rank(values, q, want):
    assert pg_backend._percentile(sorted(values), q) == want


# -- 4. Stage 7 receipt ------------------------------------------------------

_TODAY = dt.date(2026, 10, 10)


def _probe(day, green=True, dw="True"):
    div = "0" if green else "1"
    return (f"{day.isoformat()}T06:00:03Z\tabc123 soak\t{dw}\tTrue\t0\t{div}"
            "\t0\tbd-pg-soak-line cmp=900 skip=10\t1.0\t2.0\t3.0\t0.0\t0.0"
            "\t0\t40\t1\t0")


def _log(tmp_path, lines):
    p = tmp_path / "PG-SOAK-LOG.tsv"
    p.write_text("utc\tbuild\tdual_write\tshadow_read\tdegraded_lines\t"
                 "divergence_lines\tpg_errors\tsource\n"
                 + "\n".join(lines) + "\n", encoding="utf-8")
    return p


def _health(proven=40, unproven=1, divergent=0):
    return {"build": {"sha": "abc123"},
            "mod3": {"proven": {"threshold": 20, "proven": proven,
                                "unproven": unproven,
                                "divergent": divergent}}}


def _days(n, end=_TODAY):
    return [end - dt.timedelta(days=i) for i in reversed(range(n))]


def test_receipt_written_on_seven_green_days(tmp_path):
    hand = ("20260927T220716Z\t596c817f0308\tTrue\tRED->ROLLED-BACK\t"
            "degraded\t0\t0\tROW127-STAGE5-FLIP-RECEIPT.md")
    log = _log(tmp_path, [hand] + [_probe(d) for d in _days(7)])
    res = pg_backend.soak_receipt(_health(), str(log), str(tmp_path),
                                  today=_TODAY)
    assert res["ok"] is True, res
    receipt = Path(res["receipt"])
    assert receipt.name == "RECEIPT-ROW127-SOAK-COMPLETE.md"
    text = receipt.read_text()
    assert text.startswith("RECEIPT: ROW127-SOAK-COMPLETE GREEN\n")
    assert "7 consecutive daily GREEN lines ending 2026-10-10" in text
    assert "PROVEN: 40/41 shapes" in text and "BUILD: abc123" in text
    assert len(text.splitlines()) <= 40


_REFUSALS = [
    ("six days", [_probe(d) for d in _days(6)], _health(),
     "6 consecutive daily GREEN soak line(s): need 7"),
    ("gap day", [_probe(d) for d in _days(8) if d != _TODAY
                 - dt.timedelta(days=3)], _health(),
     "3 consecutive daily GREEN"),
    ("red day", [_probe(d, green=(d != _TODAY - dt.timedelta(days=2)))
                 for d in _days(9)], _health(), "2 consecutive daily GREEN"),
    ("red line same day", [_probe(d) for d in _days(7)]
     + [_probe(_TODAY, green=False)], _health(), "0 consecutive daily GREEN"),
    ("dual-write off", [_probe(d, dw="False") for d in _days(7)], _health(),
     "0 consecutive daily GREEN"),
    ("stale", [_probe(d) for d in _days(7, end=_TODAY
                                           - dt.timedelta(days=2))],
     _health(), "the probe has stopped"),
    ("proven share", [_probe(d) for d in _days(7)], _health(94, 6),
     "proven 94/100 shapes"),
    ("no shapes", [_probe(d) for d in _days(7)], _health(0, 0),
     "proven 0/0 shapes"),
    ("divergent", [_probe(d) for d in _days(7)], _health(divergent=1),
     "1 shape(s) left divergent"),
    ("old build", [_probe(d) for d in _days(7)], {"mod3": {}},
     "health mod3.proven missing or invalid"),
]


@pytest.mark.parametrize("case,lines,health,why", _REFUSALS,
                         ids=[r[0].replace(" ", "-") for r in _REFUSALS])
def test_receipt_refuses(tmp_path, case, lines, health, why):
    res = pg_backend.soak_receipt(health, str(_log(tmp_path, lines)),
                                  str(tmp_path), today=_TODAY)
    assert res["ok"] is False, case
    assert any(why in r for r in res["reasons"]), (case, res["reasons"])
    assert res["receipt"] is None
    assert not (tmp_path / "RECEIPT-ROW127-SOAK-COMPLETE.md").exists()


def test_receipt_cli_exit_codes(tmp_path, monkeypatch, capsys):
    import json
    log = _log(tmp_path, [_probe(d) for d in _days(7, end=dt.datetime.now(
        dt.timezone.utc).date())])
    monkeypatch.setattr(pg_backend, "_fetch_health",
                        lambda url: (_health(), None))
    argv = ["soak-receipt", "--health", "http://h/api/health",
            "--log", str(log), "--out", str(tmp_path)]
    assert pg_backend.main(argv) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    monkeypatch.setattr(pg_backend, "_fetch_health",
                        lambda url: (None, "health unavailable: refused"))
    assert pg_backend.main(argv) == 1
    assert "health unavailable: refused" in capsys.readouterr().out
    assert pg_backend.main(["soak-receipt", "--log", str(log)]) == 2
