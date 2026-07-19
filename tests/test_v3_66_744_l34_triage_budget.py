"""v3.66.744 — phase 1 triages with a SHORT budget; the full budget is phase 2's.

THE 743 STASH CAPTURE: `[FAIL] L34 — 372 route(s) UNPROBED (phase-1 deadline)`.
The deadline held (the honesty half worked) but the sweep starved: phase 1
probed every route with the FULL 8s adjudication budget, so each genuinely-slow
route held one of the 4 workers for 8 seconds — one slow route costs ~400
fast-route slots. 151 of 523 routes were probed in the 39.6s phase-1 share;
the other 372 were honestly reported UNPROBED, which is a correct verdict
about an incorrectly-budgeted sweep.

The 741 in-sandbox test missed this because its world was UNIFORMLY slow —
every probe cost 0.5s — so "deadline bounds the sweep" went green against a
shape that cannot starve. The real shape is BIMODAL: hundreds of ~20ms routes
behind a handful of ~8s ones. This file models that shape.

The fix: phase 1 probes at `_L34_TRIAGE_BUDGET_S` (5s — long enough that the
heaviest healthy view measured, 4.1s alone @733, is not manufactured into a
suspect; short enough that a pathological route cannot monopolize a worker for
the adjudication budget). Anything that misses triage is a SUSPECT; phase 2
re-probes suspects serially at the FULL `_L34_ROUTE_BUDGET_S`, inside the
reserve, exactly as before. Triage decides who gets adjudicated; it never
issues the verdict.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

import live_tests.checks as checks
import live_tests.harness as h


class _BimodalCtx:
    """The stash shape: many fast routes behind a few that hang past every
    budget. Slow routes are FIRST in the route list, so they grab the whole
    worker pool immediately — the exact starvation order."""

    def __init__(self, n_fast=60, n_slow=4, fast_cost=0.01):
        slow = [{"rule": f"/api/slow{i}", "methods": ["GET"]}
                for i in range(n_slow)]
        fast = [{"rule": f"/api/r{i:03d}", "methods": ["GET"]}
                for i in range(n_fast)]
        self._routes = slow + fast
        self._fast_cost = fast_cost
        self._log = []

    def get(self, path, timeout=15):
        if path == "/api/dev/routes":
            return True, 200, {"routes": self._routes}, 1.0
        if "/slow" in path:
            time.sleep(timeout)  # hangs until whatever budget it was given
            return False, None, "URLError: <urlopen error timed out>", timeout * 1000
        time.sleep(self._fast_cost)
        return True, 200, "", self._fast_cost * 1000

    def log(self, msg):
        self._log.append(msg)

    @property
    def log_text(self):
        return "\n".join(self._log)


def _small_world(monkeypatch):
    """Scaled constants preserving the real ratios: wall 6s, full budget 1.5s,
    triage 0.3s, 4 workers. Pristine 743 (triage == full budget) starves: 4
    slow routes hold all 4 workers 1.5s per wave against a 3.3s phase-1 share."""
    monkeypatch.setattr(checks, "_L34_WALL_S", 6.0)
    monkeypatch.setattr(checks, "_L34_ROUTE_BUDGET_S", 1.5)
    if hasattr(checks, "_L34_TRIAGE_BUDGET_S"):
        monkeypatch.setattr(checks, "_L34_TRIAGE_BUDGET_S", 0.3)


def test_slow_routes_do_not_starve_the_sweep(monkeypatch):
    """THE STASH FAILURE, in miniature: 24 slow routes x 1.5s full budget /
    8 workers = 4.5s of slow-only wall against a 3.3s phase-1 deadline, so a
    full-budget phase 1 MUST starve (arithmetic, not scheduling luck --
    re-scaled at 745 when workers went back to 8). With triage (0.3s) the
    slow cohort costs 0.9s, every fast route is probed, and the slow ones
    surface as findings, not as a denominator collapse."""
    _small_world(monkeypatch)
    ctx = _BimodalCtx(n_fast=60, n_slow=24)

    level, detail = checks.l34_full_route_smoke(ctx)

    blob = detail + "\n" + ctx.log_text
    assert "UNPROBED (phase-1 deadline)" not in detail and ", 0 unprobed" in ctx.log_text, (
        "fast routes starved behind slow ones — phase 1 is probing at the "
        "full adjudication budget, so one slow route costs a worker-slot "
        "hundreds of fast probes (the 743 stash capture: 372/523 UNPROBED). "
        f"detail: {detail[:200]}"
    )
    # the slow routes are still FINDINGS — triage must not launder them
    assert level == h.FAIL
    assert "/api/slow0" in blob


def test_triage_never_issues_the_verdict(monkeypatch):
    """A route that misses triage but answers within the FULL budget is
    healthy: phase 2 must adjudicate it at _L34_ROUTE_BUDGET_S and RECOVER it.
    Triage decides who gets a second look, never who failed."""
    _small_world(monkeypatch)

    class _MidCtx(_BimodalCtx):
        """One route answers at 0.6s — past the 0.3s triage, well inside the
        1.5s full budget. It must NOT be reported."""

        def get(self, path, timeout=15):
            if path == "/api/dev/routes":
                return True, 200, {"routes": self._routes}, 1.0
            if "/slow" in path:
                cost = 0.6
                if timeout < cost:
                    time.sleep(timeout)
                    return False, None, "URLError: <urlopen error timed out>", timeout * 1000
                time.sleep(cost)
                return True, 200, "", cost * 1000
            time.sleep(self._fast_cost)
            return True, 200, "", self._fast_cost * 1000

    ctx = _MidCtx(n_fast=30, n_slow=1)
    level, detail = checks.l34_full_route_smoke(ctx)

    assert level == h.PASS, (
        f"a route answering inside the full budget was reported: {detail} — "
        "triage flagged it (correct) but phase 2 failed to clear it (wrong)"
    )
    assert "RECOVERED" in ctx.log_text.upper()


def test_the_wall_still_binds_with_triage(monkeypatch):
    """Triage must not reopen the 733/737 hole: with everything slow AND a
    tiny wall, the check still exits inside wall + one drain + slack, and
    still names what it could not reach."""
    monkeypatch.setattr(checks, "_L34_WALL_S", 1.5)
    monkeypatch.setattr(checks, "_L34_ROUTE_BUDGET_S", 0.75)
    if hasattr(checks, "_L34_TRIAGE_BUDGET_S"):
        monkeypatch.setattr(checks, "_L34_TRIAGE_BUDGET_S", 0.5)
    ctx = _BimodalCtx(n_fast=64, n_slow=8, fast_cost=0.5)

    t0 = time.monotonic()
    level, detail = checks.l34_full_route_smoke(ctx)
    elapsed = time.monotonic() - t0

    assert elapsed < 1.5 + 0.75 + 1.0
    assert level == h.FAIL
    assert "UNPROBED" in (detail + ctx.log_text).upper()


def test_throughput_fits_a_loaded_app(monkeypatch):
    """v3.66.745 — THE 744 STASH CAPTURE, second half. Triage fixed the
    slow-route starvation, and the sweep STILL missed its deadline: 346/523
    UNPROBED, because on a loaded box the MEAN route costs ~420ms (sibling
    live checks hammer the app during the sweep) and 523 x 0.42s / 4 workers
    = ~55s against a 39.6s phase-1 share. Throughput, not tail latency.

    Miniature with the same ratios: 64 uniformly-moderate routes at 0.25s,
    wall 6s -> 3.3s phase-1 share. At 4 workers the sweep needs 4.0s and
    starves; at 8 it needs 2.0s and fits. The 741 workers cut treated
    contention artifacts by cutting throughput; triage + serial
    re-confirmation now handle the artifacts, so throughput gets its
    workers back."""
    monkeypatch.setattr(checks, "_L34_WALL_S", 6.0)
    monkeypatch.setattr(checks, "_L34_ROUTE_BUDGET_S", 1.5)
    monkeypatch.setattr(checks, "_L34_TRIAGE_BUDGET_S", 0.75)

    class _ModerateCtx(_BimodalCtx):
        def __init__(self):
            super().__init__(n_fast=64, n_slow=0, fast_cost=0.25)

    ctx = _ModerateCtx()
    level, detail = checks.l34_full_route_smoke(ctx)

    assert level == h.PASS, (
        f"a healthy-but-loaded sweep starved: {detail[:200]} — 64 routes at "
        "0.25s need 16 worker-seconds; the 3.3s phase-1 share fits them at 8 "
        "workers (2.0s) and not at 4 (4.0s). This is the 744 stash capture "
        "in miniature (346/523 UNPROBED at mean 420ms)."
    )
