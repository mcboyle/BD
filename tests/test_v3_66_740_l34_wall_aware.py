"""v3.66.740 — L34 must budget itself against the harness's wall.

THE BUG I SHIPPED AT 737, CAUGHT BY THE 737 CAPTURE.

  733: L34 serial      -> TIMEOUT after 90s, thread leaked (523 routes don't fit)
  734: made concurrent -> COMPLETED. But reported 37/523 over budget, because an
                          8-way fan-out was loading the app it was measuring
                          (/api/community_scrapers/index answers in 4090ms alone
                          and appeared in the >8s list).
  737: added serial re-confirmation to strip that contention -- and made it
       UNBOUNDED. ~37 suspects x 8s = ~300s against a 90s wall.
       -> TIMEOUT after 90s, thread leaked. AGAIN.

The fix for the contamination reintroduced the timeout the contamination fix was
built on top of. Two releases, same failure mode, opposite causes.

THE REAL DEFECT, which neither 734 nor 737 addressed: **L34 was never wall-aware.**
The harness gives each check 90s and, on expiry, ABANDONS the thread -- which
keeps smoking all 523 routes underneath L31 (memory) and L33 (leak scan) and
poisons their readings. So a check that CAN run past the wall does not merely
fail; it corrupts the checks that follow it. Any check whose cost is a function
of a growing denominator (523 routes and climbing) will eventually cross the wall.
Bounding it is not an optimisation, it is the correctness property.

So: L34 watches the clock. It re-confirms suspects only while budget remains, and
whatever it cannot reach is UNCONFIRMED -- which is UNKNOWN, and unknown FAILS.
It is never silently dropped, and never assumed innocent.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

import live_tests.checks as checks
import live_tests.harness as h


class _SlowCtx:
    """Every route is a suspect, and every re-probe costs real time. Serially
    this is 40 x 0.05s in phase 2 -- and with a tiny wall, most of it cannot be
    reached."""

    def __init__(self, n=40, probe_cost=0.05):
        self._routes = [{"rule": f"/api/r{i:03d}", "methods": ["GET"]}
                        for i in range(n)]
        self._cost = probe_cost
        self._seen = {}
        self._log = []

    def get(self, path, timeout=15):
        if path == "/api/dev/routes":
            return True, 200, {"routes": self._routes}, 1.0
        n = self._seen.get(path, 0)
        self._seen[path] = n + 1
        time.sleep(self._cost)
        # always "times out" -> always a suspect, on every probe
        return False, None, "URLError: <urlopen error timed out>", 8000.0

    def log(self, msg):
        self._log.append(msg)

    @property
    def log_text(self):
        return "\n".join(self._log)


def test_l34_never_runs_past_its_wall(monkeypatch):
    """The property that matters. If L34 overruns, the harness abandons the
    thread and L31/L33 measure a polluted app -- which is exactly what the 733
    and 737 captures did."""
    monkeypatch.setattr(checks, "_L34_WALL_S", 2.0)
    monkeypatch.setattr(checks, "_L34_ROUTE_BUDGET_S", 0.01)
    ctx = _SlowCtx(n=40, probe_cost=0.05)

    t0 = time.monotonic()
    level, detail = checks.l34_full_route_smoke(ctx)
    elapsed = time.monotonic() - t0

    assert elapsed < 2.0 + 1.5, (
        f"L34 ran {elapsed:.1f}s against a 2.0s wall — it does not bound itself. "
        "Past the wall the harness abandons the thread, which keeps smoking "
        "routes underneath the checks that follow."
    )
    assert level == h.FAIL


def test_unreached_suspects_are_UNCONFIRMED_not_cleared(monkeypatch):
    """The half that makes the bound honest. Suspects we ran out of clock for are
    UNKNOWN -- named, and failing. Dropping them silently would turn a budget
    shortfall into a clean bill of health."""
    monkeypatch.setattr(checks, "_L34_WALL_S", 2.0)
    monkeypatch.setattr(checks, "_L34_ROUTE_BUDGET_S", 0.01)
    ctx = _SlowCtx(n=40, probe_cost=0.05)

    level, detail = checks.l34_full_route_smoke(ctx)

    assert level == h.FAIL
    assert "UNCONFIRMED" in detail or "UNCONFIRMED" in ctx.log_text, (
        "suspects that could not be re-probed were not reported as UNCONFIRMED — "
        "a suspect we ran out of clock for is UNKNOWN, and unknown fails. It must "
        "never be quietly dropped from the denominator."
    )
    assert "unknown" in detail.lower() or "unconfirmed" in detail.lower()


def test_a_fast_clean_app_still_passes(monkeypatch):
    """The bound must not turn a healthy sweep into a failure."""

    class _FastCtx(_SlowCtx):
        def get(self, path, timeout=15):
            if path == "/api/dev/routes":
                return True, 200, {"routes": self._routes}, 1.0
            return True, 200, "", 5.0

    monkeypatch.setattr(checks, "_L34_WALL_S", 5.0)
    ctx = _FastCtx(n=20)
    level, detail = checks.l34_full_route_smoke(ctx)
    assert level == h.PASS, f"clean app should PASS, got {level} — {detail}"
