"""v3.66.741 — phase 1 must honor the reserve it declared.

THE 740 RUN'S OWN NUMBER: 24 of 25 suspects UNCONFIRMED. The wall-aware fix
@740 bounded PHASE 2 only. `_L34_PHASE2_RESERVE = 0.45` was declared with a
comment ("fraction of the wall held back for re-confirmation") and NEVER
REFERENCED — phase 1 ran an unbounded `pool.map` over every route and consumed
whatever it consumed; phase 2 got the scraps. A reserve nobody enforces is a
comment, not a budget. The result was honest (unknown failed) and USELESS
(almost nothing was adjudicated).

Two changes, one property:

  1. Phase 1 runs under a deadline of `_L34_WALL_S * (1 - _L34_PHASE2_RESERVE)`.
     Routes it could not probe by then are UNPROBED — named, unknown, FAILING.
     Never silently dropped, never assumed innocent (the same contract phase 2's
     UNCONFIRMED already has).
  2. `_L34_WORKERS` 8 -> 4. Under 8-way fan-out the probe loaded the app it was
     measuring — 37 false suspects @734, 25 @740 — and every false suspect
     burns 8s of phase-2 budget to clear. Halving the fan-out roughly doubles
     phase-1 wall cost but starves phase 2 of contention artifacts, which is
     where the budget actually went. (An instrument that changes what it
     measures reports its own load back as a finding.)

The property: the check NEVER exceeds its wall — not in phase 2 (740), not in
phase 1 (this cut) — and every route is accounted for in exactly one bucket:
ok / confirmed / recovered / UNCONFIRMED / UNPROBED.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

import live_tests.checks as checks
import live_tests.harness as h


class _MolassesCtx:
    """Every probe costs real wall time. With enough routes this makes an
    UNBOUNDED phase 1 blow straight through a small wall — which is exactly
    what the pristine code does."""

    def __init__(self, n=40, probe_cost=0.5):
        self._routes = [{"rule": f"/api/r{i:03d}", "methods": ["GET"]}
                        for i in range(n)]
        self._cost = probe_cost
        self._log = []

    def get(self, path, timeout=15):
        if path == "/api/dev/routes":
            return True, 200, {"routes": self._routes}, 1.0
        time.sleep(self._cost)
        return True, 200, "", self._cost * 1000.0

    def log(self, msg):
        self._log.append(msg)

    @property
    def log_text(self):
        return "\n".join(self._log)


def test_phase1_is_bounded_by_the_wall(monkeypatch):
    """40 healthy-but-slow routes at 0.5s through the fan-out is ~4-8s of
    phase-1 work against a 1.5s wall. A wall-aware check stops probing at its
    phase-1 deadline and reports the rest UNPROBED; the pristine code runs the
    full sweep and overruns — the thread the harness would then abandon is the
    L31/L33 poisoner all over again, just moved one phase earlier."""
    monkeypatch.setattr(checks, "_L34_WALL_S", 1.5)
    monkeypatch.setattr(checks, "_L34_ROUTE_BUDGET_S", 0.75)
    ctx = _MolassesCtx(n=64, probe_cost=0.5)

    t0 = time.monotonic()
    level, detail = checks.l34_full_route_smoke(ctx)
    elapsed = time.monotonic() - t0

    # Bound: the wall, plus one in-flight probe budget for the pool to drain,
    # plus scheduling slack. NOT the full sweep (~5s).
    assert elapsed < 1.5 + 0.75 + 1.0, (
        f"L34 ran {elapsed:.1f}s against a 1.5s wall — phase 1 does not bound "
        "itself. _L34_PHASE2_RESERVE is a declared budget nobody enforces."
    )


def test_unprobed_routes_are_named_and_fail(monkeypatch):
    """The honesty half. Routes phase 1 never reached are UNKNOWN — the check
    must say so and FAIL, exactly as phase 2's UNCONFIRMED already does. A
    deadline that silently shrinks the denominator would be the blind-gate
    shape with a clock for a denominator."""
    monkeypatch.setattr(checks, "_L34_WALL_S", 1.5)
    monkeypatch.setattr(checks, "_L34_ROUTE_BUDGET_S", 0.75)
    ctx = _MolassesCtx(n=64, probe_cost=0.5)

    level, detail = checks.l34_full_route_smoke(ctx)

    assert level == h.FAIL
    blob = (detail + "\n" + ctx.log_text).upper()
    assert "UNPROBED" in blob, (
        "routes phase 1 ran out of clock for were not reported as UNPROBED — "
        "they left the denominator silently and the check certified a sweep "
        "it did not perform"
    )


def test_workers_is_eight():
    """The 741 decision (8 -> 4), REVERSED at 745 on stash evidence: with
    triage (744) capping a suspect's phase-1 cost at 5s and serial
    re-confirmation clearing healthy routes at their real latency, contention
    artifacts are cheap and THROUGHPUT is the binding constraint -- 4 workers
    left 346/523 routes UNPROBED on a loaded box (mean route 420ms). If this
    number moves again, it moves in a cut that says why, with a capture."""
    assert checks._L34_WORKERS == 8


def test_fast_clean_app_still_passes_under_the_phase1_deadline():
    """The bound must not tax health: a fast app fits inside the phase-1
    share of the default wall with room to spare."""

    class _FastCtx(_MolassesCtx):
        def get(self, path, timeout=15):
            if path == "/api/dev/routes":
                return True, 200, {"routes": self._routes}, 1.0
            return True, 200, "", 5.0

    ctx = _FastCtx(n=30)
    level, detail = checks.l34_full_route_smoke(ctx)
    assert level == h.PASS, f"clean app should PASS, got {level} — {detail}"
