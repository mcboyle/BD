"""v3.66.641 -- OBS-1 (enforcing endpoint cost ceiling) + OBS-2 core.

The app already SOFT-warns on slow requests (dev_metrics.slow_request_note, logged
in the after_request hook) but nothing BOUNDS a request. This cut adds a per-request
budget: a deadline the long collect-style routes can cooperatively honor, an
after-hook that flags ANY over-budget response (so a non-cooperative slow route is
still visible before it wedges), and a 503 mapping for the raise.

Flask here is synchronous/threaded -- a running request thread can't be force-killed
from a hook -- so the budget is a COOPERATIVE deadline (the generalized `limit=`
pattern), plus fail-visible telemetry for everything else. The budget is a module
constant (REQUEST_BUDGET_MS), a sibling of the existing SLOW_REQUEST_MS threshold --
NOT a new BD_ env var or config field (dodges the config-surface gate).

OBS-2 core: route_percentiles() aggregates p50/p95 per rule from the existing
_requests deque (the durations are already recorded). The status PANEL that surfaces
it needs a new route and is a separate follow-on (S1.1b); this cut ships only the
computable core, which is pure and sandbox-testable.

Sandbox-safe: dev_metrics is flask-free and pure; no DISPLAY, no network, no pytest
builtins. Globals restored in finally.
"""
from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_APP_PY = _REPO_ROOT / "bulk_downloader" / "app.py"


# ---- OBS-1: the budget mechanism (pure, in dev_metrics) ------------------

def test_request_budget_constant_exists():
    """REQUEST_BUDGET_MS is defined as a float sibling of SLOW_REQUEST_MS.
    RED on pristine (constant absent)."""
    from bulk_downloader import dev_metrics as dm
    assert hasattr(dm, "REQUEST_BUDGET_MS"), "REQUEST_BUDGET_MS constant is missing"
    assert isinstance(dm.REQUEST_BUDGET_MS, float)
    assert dm.REQUEST_BUDGET_MS > 0
    # It should be a hard ceiling well above the soft-warn threshold, so a slow
    # request warns long before it is budget-killed.
    assert dm.REQUEST_BUDGET_MS >= dm.SLOW_REQUEST_MS


def test_request_budget_exception_carries_context():
    """RequestBudgetExceeded exists and carries the diagnostic fields the 503
    handler surfaces. RED on pristine (class absent)."""
    from bulk_downloader import dev_metrics as dm
    assert hasattr(dm, "RequestBudgetExceeded")
    exc = dm.RequestBudgetExceeded(method="GET", path="/api/x",
                                   elapsed_ms=41000.0, budget_ms=30000.0)
    assert exc.method == "GET"
    assert exc.path == "/api/x"
    assert exc.elapsed_ms == 41000.0
    assert exc.budget_ms == 30000.0
    assert isinstance(exc, Exception)


def test_check_budget_raises_only_when_over():
    """check_budget(t0, now) raises RequestBudgetExceeded iff elapsed >= budget.
    Pure (explicit t0/now, no clock). RED on pristine (function absent)."""
    from bulk_downloader import dev_metrics as dm
    # 10s elapsed under a 30s budget -> no raise.
    dm.check_budget(0.0, now=10.0, budget_ms=30000.0, method="GET", path="/ok")
    # 31s elapsed over a 30s budget -> raise.
    raised = False
    try:
        dm.check_budget(0.0, now=31.0, budget_ms=30000.0, method="GET", path="/slow")
    except dm.RequestBudgetExceeded as e:
        raised = True
        assert e.path == "/slow"
        assert e.elapsed_ms >= 30000.0
    assert raised, "check_budget must raise once the deadline is exceeded"


def test_over_budget_is_a_pure_predicate():
    """over_budget(elapsed_ms) mirrors the boolean the after-hook uses to flag a
    slow response. RED on pristine (function absent)."""
    from bulk_downloader import dev_metrics as dm
    assert dm.over_budget(dm.REQUEST_BUDGET_MS + 1.0) is True
    assert dm.over_budget(1.0) is False


# ---- OBS-2 core: per-route percentiles over the existing buffer ----------

def test_route_percentiles_computes_p50_p95():
    """route_percentiles() aggregates p50/p95/max/count per rule from the
    _requests deque. RED on pristine (function absent)."""
    from bulk_downloader import dev_metrics as dm
    dm.reset()
    try:
        # 10 samples for one rule: 100,200,...,1000 ms.
        for i in range(1, 11):
            dm.record_request("GET", "/api/thing", "/api/thing", 200, float(i * 100))
        pct = dm.route_percentiles()
        assert "/api/thing" in pct, "the rule should appear in the percentile map"
        row = pct["/api/thing"]
        assert row["count"] == 10
        # p50 of 100..1000 sits mid-range; p95 near the top; max is exact.
        assert 400.0 <= row["p50"] <= 700.0, row
        assert row["p95"] >= row["p50"]
        assert row["max"] == 1000.0
    finally:
        dm.reset()


def test_route_percentiles_empty_is_empty():
    """No recorded requests -> an empty map, not an error."""
    from bulk_downloader import dev_metrics as dm
    dm.reset()
    try:
        assert dm.route_percentiles() == {}
    finally:
        dm.reset()


# ---- app.py wiring (source-level; the hook + handler live in the megafile) --

def test_app_enforces_and_flags_the_budget():
    """app.py must (a) register a RequestBudgetExceeded error handler that returns
    503, and (b) flag over-budget responses in the after_request hook. RED on
    pristine (neither present)."""
    src = _APP_PY.read_text(encoding="utf-8")
    assert "RequestBudgetExceeded" in src, (
        "app.py must handle RequestBudgetExceeded (the 503 mapping)"
    )
    assert "503" in src and "errorhandler" in src, (
        "the budget-exceeded handler must map to a 503 response"
    )
    assert "over_budget" in src, (
        "the after_request hook must flag over-budget responses via over_budget()"
    )
