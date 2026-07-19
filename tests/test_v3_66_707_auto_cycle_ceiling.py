"""v3.66.707 -- X-AUTO-2: a CEILING on the autonomous cycle.

`automation_controller.run_host_cycle` is the ONE place autonomous action actually
happens. Today its only brake is the master off-switch -- which is binary, all-or-nothing.
Past that gate, step 5 runs EVERY injected step with:

  * no wall-clock limit  -> a slow or hung step runs forever;
  * no step cap          -> one pass can take unbounded action;
  * and, worst, its own docstring says "a throwing step is isolated + audited, THE LOOP
    CONTINUES" -> a pipeline that is FAILING EVERY STEP keeps right on executing steps.

That last one is the runaway: an autonomous loop that is broken and does not know it,
burning actions. A-PIPE (the capstone pipeline) does not exist yet, so a literal
"per-stage" ceiling has nothing to attach to -- but the guardrail the plan is asking for
does: a per-CYCLE ceiling on the loop that autonomy actually runs today.

DESIGN: a ceiling is a HALT, not a skip. On breach the cycle STOPS and says why -- it
does not quietly continue with the remaining steps. A guardrail that lets the loop finish
"just this once" is not a guardrail.

DEFAULT = UNCAPPED (0), so with nothing configured the cycle is BYTE-IDENTICAL to today.
The ceiling is opt-in like every other automation control -- but the operator running L2
autonomy should set it, and the digest surfaces a halt.

RED-first: every assertion below fails on pristine v3.66.706 (AutoBudget does not exist).
"""
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bulk_downloader import automation_controller as AC


_POLICY = {"trusted_auto_hosts": ["demo.test"]}
_CTX = {"candidate": {}}


def _arm(monkeypatch):
    """Get past the off-switch / capability / trust gates so the LOOP is what is under
    test. Called EXPLICITLY, not as an autouse fixture: run_tests.py -- the harness
    capture.sh actually runs -- does not inject `monkeypatch` into an autouse fixture
    (it does for a test's own args). pytest-green is not harness-green; the harness is
    the authority."""
    monkeypatch.setattr(AC, "off_switch_engaged", lambda: False)
    monkeypatch.setattr(AC, "controller_armed", lambda: True)
    monkeypatch.setattr(AC, "is_trusted_auto", lambda h, p: True)
    monkeypatch.setattr(AC, "classify_boundary", lambda h, c, p: [])
    monkeypatch.setattr(AC.la, "is_enabled", lambda name: True)


def _steps(n, fail=False, slow=0.0):
    def _mk(i):
        def _fn(host, ctx):
            if slow:
                time.sleep(slow)
            if fail:
                raise RuntimeError(f"step {i} exploded")
            return {"i": i}
        return _fn
    return {f"s{i}": _mk(i) for i in range(n)}


# ── the budget object ────────────────────────────────────────────────────
def test_autobudget_uncapped_by_default():
    b = AC.AutoBudget()
    assert b.is_active() is False, "default must be UNCAPPED -- byte-identical to today"
    assert b.breach(steps=999, elapsed_s=99999, errors=999) is None


def test_autobudget_reports_which_ceiling_broke():
    b = AC.AutoBudget(max_steps=2, wall_s=10, max_errors=1)
    assert b.breach(steps=2, elapsed_s=0, errors=0) == "max_steps"
    assert b.breach(steps=0, elapsed_s=10, errors=0) == "wall_s"
    assert b.breach(steps=0, elapsed_s=0, errors=1) == "max_errors"
    assert b.breach(steps=1, elapsed_s=1, errors=0) is None


# ── the ceiling HALTS the cycle ──────────────────────────────────────────
def test_step_cap_halts_the_cycle(monkeypatch):
    _arm(monkeypatch)
    r = AC.run_host_cycle("demo.test", _CTX, _POLICY, steps=_steps(5),
                          budget=AC.AutoBudget(max_steps=2))
    assert r["halted"] is True
    assert r["halt_reason"] == "max_steps"
    assert len(r["audit"]) == 2, "the cycle must STOP at the cap, not run all 5"


def test_error_cap_halts_a_FAILING_loop(monkeypatch):
    """THE control. Today a throwing step is audited and the loop CONTINUES -- a broken
    autonomous pipeline keeps burning actions. The error ceiling is what stops it."""
    _arm(monkeypatch)
    r = AC.run_host_cycle("demo.test", _CTX, _POLICY, steps=_steps(6, fail=True),
                          budget=AC.AutoBudget(max_errors=2))
    assert r["halted"] is True
    assert r["halt_reason"] == "max_errors"
    assert len(r["audit"]) == 2, "must halt after the 2nd failure, not run all 6"


def test_wall_clock_ceiling_halts_the_cycle(monkeypatch):
    _arm(monkeypatch)
    r = AC.run_host_cycle("demo.test", _CTX, _POLICY,
                          steps=_steps(5, slow=0.05),
                          budget=AC.AutoBudget(wall_s=0.08))
    assert r["halted"] is True
    assert r["halt_reason"] == "wall_s"
    assert len(r["audit"]) < 5


def test_halt_is_recorded_in_the_audit_trail(monkeypatch):
    """A halt the operator cannot see is not a guardrail. It must be in the result the
    AF5 timeline / AF7 digest read."""
    _arm(monkeypatch)
    r = AC.run_host_cycle("demo.test", _CTX, _POLICY, steps=_steps(4),
                          budget=AC.AutoBudget(max_steps=1))
    assert r["halted"] is True and r["halt_reason"]
    assert r["ran"] is True          # it DID act -- partially. Both facts matter.


# ── NEG controls: the ceiling must not change the normal path ────────────
def test_no_budget_runs_every_step_exactly_as_before(monkeypatch):
    """The cut must be INERT until configured: with no budget the cycle behaves EXACTLY
    as it does today, including 'a throwing step is isolated and the loop continues'."""
    _arm(monkeypatch)
    r = AC.run_host_cycle("demo.test", _CTX, _POLICY, steps=_steps(4))
    assert r.get("halted", False) is False
    assert len(r["audit"]) == 4
    assert r["ran"] is True


def test_errors_below_the_cap_do_not_halt(monkeypatch):
    """NEG: the ceiling is a CEILING, not a hair-trigger. One failure under a cap of 3
    must NOT stop the cycle -- isolating a single bad step is the existing, correct
    behaviour."""
    _arm(monkeypatch)
    steps = _steps(3)
    steps["boom"] = lambda h, c: (_ for _ in ()).throw(RuntimeError("one bad step"))
    r = AC.run_host_cycle("demo.test", _CTX, _POLICY, steps=steps,
                          budget=AC.AutoBudget(max_errors=3))
    assert r.get("halted", False) is False
    assert len(r["audit"]) == 4


def test_off_switch_still_dominates_the_budget(monkeypatch):
    """The off-switch outranks everything, including a permissive budget. Order matters:
    a master stop must never be reachable-past."""
    _arm(monkeypatch)
    monkeypatch.setattr(AC, "off_switch_engaged", lambda: True)
    r = AC.run_host_cycle("demo.test", _CTX, _POLICY, steps=_steps(3),
                          budget=AC.AutoBudget(max_steps=99))
    assert r["inert"] is True
    assert r["ran"] is False
    assert r["audit"] == []
