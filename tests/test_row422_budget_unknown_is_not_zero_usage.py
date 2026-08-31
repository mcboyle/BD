"""Row 422: an unreadable history database must not read as zero budget usage.

``policy_gates.current_period_usage()`` wrapped its ``SUM(file_size)`` query in
``except Exception: pass`` with ``used = 0`` pre-seeded, so a locked or corrupt
database produced ``used_bytes=0`` and ``budget_state()`` answered
``state='ok'``, ``pct=0``, ``throttle_factor=1.0`` over a measurement never
taken -- a hard-cap stop turned into unthrottled downloading at exactly the
moment usage cannot be seen.  The sibling ``daily_budget._budget_report()``
already refuses that equation (``unknown=True`` / ``over=None``); these tests
pin the same semantics here and for all three consumers.

Every test proves its precondition first: exactly one ``done`` history row with
a nonzero ``file_size`` sums THROUGH THE PRODUCTION QUERY to a nonzero
``used_bytes`` before the database is made to raise, and the DB-failure branch
is proven to fire exactly once.  The negative controls keep a healthy database
answering a genuine ``ok`` with the measured nonzero pct, so the UNKNOWN path
cannot launder ordinary green.
"""
from __future__ import annotations

import datetime
import sqlite3
import sys

import pytest


BD_GATE_SCOPE = "module"

_GIB = 1024 ** 3
_ROW_BYTES = _GIB               # 1.0 GiB -- survives round(x / 2**30, 2)
_BUDGET_GB = 10.0               # 1 GiB of 10 GB budget == pct 10.0 exactly
_SITE = "row422site"
_CFG = {"monthly_budget_gb": _BUDGET_GB, "budget_reset_day": 1}
_CFG_UNSET = {"monthly_budget_gb": 0, "budget_reset_day": 1}


def _isolated_history(tmp_path, monkeypatch):
    """Point the DB at a fresh temp file holding exactly one done row.

    Returns the ``db`` module.  Asserts the fixture actually built the intended
    shape rather than trusting the INSERT.
    """
    from bulk_downloader import db as _db

    monkeypatch.delenv("BD_INSTALL_DIR", raising=False)
    monkeypatch.setattr(_db, "DB_PATH", str(tmp_path / "history.db"),
                        raising=False)
    _db.db_init()
    now_iso = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with _db.db_conn() as cx:
        cx.execute(
            "INSERT INTO history(site_id, url, status, filename, file_size, ts)"
            " VALUES(?,?,?,?,?,?)",
            (_SITE, "https://example.invalid/a.mp4", "done", "a.mp4",
             _ROW_BYTES, now_iso),
        )
    # Precondition: exactly one done row, nonzero size, inside the period.
    with _db.db_conn() as cx:
        rows = cx.execute(
            "SELECT file_size FROM history WHERE status='done'").fetchall()
    assert len(rows) == 1, f"fixture built {len(rows)} done rows, expected 1"
    assert int(rows[0][0]) == _ROW_BYTES > 0
    return _db


def _break_db(monkeypatch, _db):
    """Make every ``db_conn()`` lease raise; return the call-site recorder.

    Callers assert on ``_usage_calls(calls)`` rather than ``len(calls)``: a
    consumer such as ``metrics_prom.render()`` leases the database for many
    unrelated sections, and only the leases taken by
    ``current_period_usage`` are this row's DB-failure branch.
    """
    calls: list = []

    def _raising(*a, **kw):
        calls.append(sys._getframe(1).f_code.co_name)
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(_db, "db_conn", _raising)
    return calls


def _usage_calls(calls):
    """Leases attempted by ``current_period_usage`` itself."""
    return [c for c in calls if c == "current_period_usage"]


def _measured_usage(_db):
    """The production query's own answer on a healthy database."""
    from bulk_downloader import policy_gates as _pg
    usage = _pg.current_period_usage(_CFG)
    assert usage["used_bytes"] == _ROW_BYTES, usage
    assert usage["used_gb"] == 1.0, usage
    return usage


# ─── Core: current_period_usage ───────────────────────────────────────

def test_healthy_db_measures_the_row_through_the_production_query(
        tmp_path, monkeypatch):
    """NEGATIVE CONTROL / precondition: the seam really sums the row."""
    _db = _isolated_history(tmp_path, monkeypatch)
    usage = _measured_usage(_db)
    assert usage.get("unknown") is False, usage
    assert not usage.get("error"), usage


def test_unreadable_db_reports_unknown_usage_not_zero(tmp_path, monkeypatch):
    from bulk_downloader import policy_gates as _pg

    _db = _isolated_history(tmp_path, monkeypatch)
    _measured_usage(_db)                      # proves the nonzero seam first
    calls = _break_db(monkeypatch, _db)

    usage = _pg.current_period_usage(_CFG)

    fired = _usage_calls(calls)
    assert len(fired) == 1, (
        f"DB-failure branch fired {len(fired)}x, expected 1; all leases={calls}")
    assert usage["used_bytes"] is None, usage
    assert usage["used_gb"] is None, usage
    assert usage.get("unknown") is True, usage
    assert usage.get("error"), usage
    # The defect's exact shape, refused positively.
    assert usage["used_bytes"] != 0
    assert usage["used_gb"] != 0


# ─── Core: budget_state ───────────────────────────────────────────────

def test_healthy_db_below_soft_cap_still_yields_genuine_ok(
        tmp_path, monkeypatch):
    """NEGATIVE CONTROL: the UNKNOWN path must not launder ordinary green."""
    from bulk_downloader import policy_gates as _pg

    _db = _isolated_history(tmp_path, monkeypatch)
    state = _pg.budget_state(_CFG)

    assert state["state"] == "ok", state
    assert state["pct"] == 10.0, state          # measured, nonzero
    assert state["used_gb"] == 1.0, state
    assert state["throttle_factor"] == 1.0, state
    assert state.get("unknown") is False, state


def test_unreadable_db_yields_a_distinct_unknown_verdict(tmp_path, monkeypatch):
    from bulk_downloader import policy_gates as _pg

    _db = _isolated_history(tmp_path, monkeypatch)
    _measured_usage(_db)
    calls = _break_db(monkeypatch, _db)

    state = _pg.budget_state(_CFG)

    fired = _usage_calls(calls)
    assert len(fired) == 1, (
        f"DB-failure branch fired {len(fired)}x, expected 1; all leases={calls}")
    # Positive shape: a distinct UNKNOWN verdict (CLAUDE.md A7).
    assert state["state"] == "unknown", state
    assert state["used_gb"] is None, state
    assert state["pct"] is None, state
    assert state["throttle_factor"] == 0.0, state
    assert state.get("unknown") is True, state
    assert state.get("error"), state
    # The defect, refused explicitly.
    assert state["state"] != "ok"
    assert state["pct"] != 0
    assert state["throttle_factor"] != 1.0


def test_unset_budget_does_not_fabricate_zero_usage(tmp_path, monkeypatch):
    """budget<=0 short-circuits to 'unset' -- nothing to enforce, but the
    unread counter still must not be rendered as 0.0 GB used."""
    from bulk_downloader import policy_gates as _pg

    _db = _isolated_history(tmp_path, monkeypatch)
    calls = _break_db(monkeypatch, _db)

    state = _pg.budget_state(_CFG_UNSET)

    fired = _usage_calls(calls)
    assert len(fired) == 1, (
        f"DB-failure branch fired {len(fired)}x, expected 1; all leases={calls}")
    assert state["state"] == "unset", state
    assert state["used_gb"] is None, state
    assert state["pct"] is None, state
    assert state.get("unknown") is True, state


# ─── Consumer 1: app_budget.py (/api/budget/<sid>) ────────────────────

def _budget_client(monkeypatch, cfg):
    from flask import Flask
    from bulk_downloader import app_budget as M, app_state

    monkeypatch.setattr(app_state, "s_cfg", {_SITE: dict(cfg)}, raising=False)
    app = Flask(__name__)
    n = M.register_routes(app)
    assert n >= 1, f"blueprint registered {n} routes"
    return app.test_client()


def test_api_budget_surfaces_unknown_instead_of_an_ok_panel(
        tmp_path, monkeypatch):
    _db = _isolated_history(tmp_path, monkeypatch)
    _measured_usage(_db)
    calls = _break_db(monkeypatch, _db)
    client = _budget_client(monkeypatch, _CFG)

    resp = client.get(f"/api/budget/{_SITE}")

    assert resp.status_code == 200, resp.data
    fired = _usage_calls(calls)
    assert len(fired) == 1, (
        f"DB-failure branch fired {len(fired)}x, expected 1; all leases={calls}")
    budget = resp.get_json()["budget"]
    assert budget["state"] == "unknown", budget
    assert budget["used_gb"] is None, budget
    assert budget["pct"] is None, budget
    assert budget["throttle_factor"] == 0.0, budget
    assert budget.get("unknown") is True, budget


def test_api_budget_healthy_panel_still_reports_measured_ok(
        tmp_path, monkeypatch):
    """NEGATIVE CONTROL for the endpoint seam."""
    _isolated_history(tmp_path, monkeypatch)
    client = _budget_client(monkeypatch, _CFG)

    budget = client.get(f"/api/budget/{_SITE}").get_json()["budget"]

    assert budget["state"] == "ok", budget
    assert budget["pct"] == 10.0, budget
    assert budget["used_gb"] == 1.0, budget


# ─── Consumer 2: metrics_prom.py (bd_budget_pct_used) ─────────────────

def _budget_metric_lines(text):
    return [ln for ln in text.splitlines()
            if ln.startswith("bd_budget_") and not ln.startswith("#")]


def test_prometheus_does_not_export_a_zero_gauge_for_an_unread_counter(
        tmp_path, monkeypatch):
    from bulk_downloader import metrics_prom as _mp

    _db = _isolated_history(tmp_path, monkeypatch)
    _measured_usage(_db)
    calls = _break_db(monkeypatch, _db)

    text = _mp.render({_SITE: dict(_CFG)})

    fired = _usage_calls(calls)
    assert len(fired) == 1, (
        f"DB-failure branch fired {len(fired)}x, expected 1; all leases={calls}")
    lines = _budget_metric_lines(text)
    assert not any(ln.startswith("bd_budget_pct_used") for ln in lines), lines
    marker = [ln for ln in lines
              if ln.startswith("bd_budget_usage_unknown")]
    assert len(marker) == 1, lines
    assert marker[0].endswith(" 1"), marker
    assert f'site="{_SITE}"' in marker[0], marker
    assert "# HELP bd_budget_usage_unknown" in text


def test_prometheus_still_exports_the_measured_pct_when_readable(
        tmp_path, monkeypatch):
    """NEGATIVE CONTROL for the metrics seam."""
    from bulk_downloader import metrics_prom as _mp

    _isolated_history(tmp_path, monkeypatch)

    text = _mp.render({_SITE: dict(_CFG)})

    lines = _budget_metric_lines(text)
    pct = [ln for ln in lines if ln.startswith("bd_budget_pct_used")]
    assert len(pct) == 1, lines
    assert pct[0].endswith(" 10"), pct
    assert not any(ln.startswith("bd_budget_usage_unknown") for ln in lines), lines


# ─── Consumer 3: queue_priority.py (budget ranking) ───────────────────

def _row():
    return {"id": 1, "site_id": _SITE, "url": "https://example.invalid/b.mp4",
            "ts": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}


def test_queue_does_not_rank_an_unmeasurable_site_as_under_budget(
        tmp_path, monkeypatch):
    from bulk_downloader import queue_priority as _qp

    _db = _isolated_history(tmp_path, monkeypatch)
    _measured_usage(_db)
    calls = _break_db(monkeypatch, _db)

    ctx = _qp._gather_context({_SITE: dict(_CFG)})

    fired = _usage_calls(calls)
    assert len(fired) == 1, (
        f"DB-failure branch fired {len(fired)}x, expected 1; all leases={calls}")
    assert _SITE in ctx.get("budget_unknown", {}), ctx
    assert _SITE not in ctx.get("budget_pcts", {}), ctx

    scored = _qp._score_one(_row(), s_cfg={_SITE: dict(_CFG)}, context=ctx)
    assert "budget_unmeasured_penalty" in scored["breakdown"], scored
    assert scored["breakdown"]["budget_unmeasured_penalty"] < 0, scored

    healthy_ctx = dict(ctx)
    healthy_ctx["budget_unknown"] = {}
    healthy_ctx["budget_pcts"] = {_SITE: 10.0}
    baseline = _qp._score_one(_row(), s_cfg={_SITE: dict(_CFG)},
                              context=healthy_ctx)
    assert scored["score"] < baseline["score"], (scored, baseline)


def test_queue_ranking_unchanged_for_a_measured_under_budget_site(
        tmp_path, monkeypatch):
    """NEGATIVE CONTROL: a readable, under-cap site takes no penalty."""
    from bulk_downloader import queue_priority as _qp

    _isolated_history(tmp_path, monkeypatch)

    ctx = _qp._gather_context({_SITE: dict(_CFG)})

    assert ctx["budget_pcts"].get(_SITE) == 10.0, ctx
    assert not ctx.get("budget_unknown"), ctx

    scored = _qp._score_one(_row(), s_cfg={_SITE: dict(_CFG)}, context=ctx)
    assert "budget_unmeasured_penalty" not in scored["breakdown"], scored
    assert "budget_exhausted_penalty" not in scored["breakdown"], scored
