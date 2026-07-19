"""v3.66.708 -- A-PIPE / A9: the capstone. Actually RUN the autonomous chain.

The automation program turned out to be ~90% BUILT and 0% RUNNING:

  A0  gold-backup-with-restore ...... shipped @471, declared @705
  A1  scheduled drift sweep ......... registered (lifecycle.drift_sweep)
  A2/A5 keystone-backed refresh ..... built (lifecycle_drift.on_fresh_capture)
  A3  quarantined state machine ..... built (lifecycle_automation)
  A-REPAIR drift -> review-only draft registered (drift_repair.daily)
  X-AUTO-1 restore rehearsal ........ shipped @706
  X-AUTO-2 cycle ceiling ............ shipped @707

...and `automation_controller.run_host_cycle` -- the ONE function that executes an
autonomous cycle, with the off-switch, the trust gates, the audit trail and the ceiling
-- is called by NOTHING. No scheduler task. No route. The engine was built and never
connected to anything.

A-PIPE is that connection: the ordered, checkpointed chain the plan describes --
capture -> build -> lint -> blocked-term scan -> drift/stage -> (L2) apply -- run as ONE
flow with a rollback point at each stage, instead of discrete operator-poked steps.

NON-NEGOTIABLES (each pinned below):
  * the MASTER OFF-SWITCH dominates -- checked before anything;
  * a FAILED STAGE HALTS THE CHAIN and stages the evidence. It does NOT carry on to the
    next stage: a pipeline that keeps going after a failed step is exactly the runaway
    707 built the ceiling for, and here it would compound (apply a template built from a
    capture that failed lint);
  * every MUTATING stage is A0-BACKED (a restorable snapshot first) and abortable;
  * DEFAULT OFF. Registering the task must be behaviour-neutral.

RED-first: all of it fails on pristine v3.66.707 (automation_pipeline does not exist).
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bulk_downloader import automation_pipeline as AP
from bulk_downloader import automation_controller as AC


def _arm(monkeypatch, off=False):
    """Past the gates so the CHAIN is what is under test. Explicit, not an autouse
    fixture: run_tests.py (the harness capture.sh runs) does not inject monkeypatch into
    an autouse fixture -- the 707 lesson."""
    monkeypatch.setattr(AC, "off_switch_engaged", lambda: off)
    monkeypatch.setattr(AC, "controller_armed", lambda: True)
    monkeypatch.setattr(AC, "is_trusted_auto", lambda h, p: True)
    monkeypatch.setattr(AC, "classify_boundary", lambda h, c, p: [])
    monkeypatch.setattr(AC.la, "is_enabled", lambda name: True)


# ── the chain ────────────────────────────────────────────────────────────
def test_stage_order_is_the_documented_chain():
    """The order is the safety property: lint and the blocked-term scan MUST precede
    stage/apply, or the pipeline can apply a template it never checked."""
    names = [s for s, _ in AP.default_stages()]
    assert names == ["capture", "build", "lint", "blocked_scan", "drift_stage", "apply"]
    assert names.index("lint") < names.index("apply")
    assert names.index("blocked_scan") < names.index("apply")


def test_pipeline_runs_every_stage_in_order(monkeypatch):
    _arm(monkeypatch)
    seen = []
    stages = [(n, (lambda n: (lambda host, ctx: seen.append(n) or {"ok": True}))(n))
              for n in ("capture", "build", "lint", "blocked_scan", "drift_stage", "apply")]
    r = AP.run_pipeline("demo.test", {"candidate": {}}, {}, stages=stages)
    assert r["ok"] is True
    assert seen == ["capture", "build", "lint", "blocked_scan", "drift_stage", "apply"]


# ── a failed stage HALTS the chain ───────────────────────────────────────
def test_a_failed_stage_HALTS_and_does_not_run_later_stages(monkeypatch):
    """THE control. If lint fails, `apply` must NEVER run -- otherwise the pipeline
    applies a template it failed to check. A chain that carries on past a failure is not
    a pipeline, it is a hazard."""
    _arm(monkeypatch)
    seen = []

    def _ok(n):
        return lambda host, ctx: seen.append(n) or {"ok": True}

    def _boom(host, ctx):
        seen.append("lint")
        raise RuntimeError("lint rejected the template")

    stages = [("capture", _ok("capture")), ("build", _ok("build")),
              ("lint", _boom), ("apply", _ok("apply"))]
    r = AP.run_pipeline("demo.test", {"candidate": {}}, {}, stages=stages)
    assert r["ok"] is False
    assert r["halted_at"] == "lint"
    assert "apply" not in seen, "apply MUST NOT run after lint failed"
    assert seen == ["capture", "build", "lint"]


def test_a_halt_stages_the_evidence(monkeypatch):
    """A halt the operator cannot inspect is a dead end. The failed stage's error and the
    audit up to that point must be returned."""
    _arm(monkeypatch)

    def _boom(host, ctx):
        raise RuntimeError("blocked term found")

    r = AP.run_pipeline("demo.test", {"candidate": {}}, {},
                        stages=[("blocked_scan", _boom)])
    assert r["halted_at"] == "blocked_scan"
    assert "blocked term" in r["error"]
    assert r["audit"], "the audit trail up to the halt must be staged"


# ── A0 backing: a mutating stage snapshots FIRST ─────────────────────────
def test_mutating_stage_takes_an_A0_snapshot_first(monkeypatch):
    """Every mutating stage must be A0-backed: a restorable snapshot BEFORE it acts, or
    the 'reversible' in 'reversible autonomy' is a word, not a guarantee."""
    _arm(monkeypatch)
    calls = []
    monkeypatch.setattr(AP, "_snapshot", lambda host: calls.append(host) or {"ok": True})
    AP.run_pipeline("demo.test", {"candidate": {}}, {},
                    stages=[("apply", lambda h, c: {"ok": True})])
    assert calls == ["demo.test"], "the apply stage must snapshot before it mutates"


def test_a_failed_snapshot_ABORTS_before_the_mutating_stage(monkeypatch):
    """FAIL-CLOSED (the A0 contract): if the snapshot cannot be taken, the mutating stage
    must NOT run. An autonomous overwrite without a recovery point is the exact thing A0
    exists to forbid."""
    _arm(monkeypatch)
    ran = []
    monkeypatch.setattr(AP, "_snapshot",
                        lambda host: {"ok": False, "error": "disk full"})
    r = AP.run_pipeline("demo.test", {"candidate": {}}, {},
                        stages=[("apply", lambda h, c: ran.append("apply"))])
    assert r["ok"] is False
    assert ran == [], "the mutating stage must NOT run without a snapshot"
    assert "snapshot" in r["error"].lower()


# ── the gates still dominate ─────────────────────────────────────────────
def test_off_switch_stops_the_whole_pipeline(monkeypatch):
    _arm(monkeypatch, off=True)
    ran = []
    r = AP.run_pipeline("demo.test", {"candidate": {}}, {},
                        stages=[("capture", lambda h, c: ran.append("x"))])
    assert r["inert"] is True
    assert ran == []


def test_the_707_cycle_ceiling_still_applies(monkeypatch):
    """A-PIPE runs THROUGH run_host_cycle, so it inherits the ceiling rather than
    re-implementing one. Two budgets would be two behaviours."""
    _arm(monkeypatch)
    seen = []
    stages = [(n, (lambda n: (lambda h, c: seen.append(n)))(n))
              for n in ("capture", "build", "lint")]
    r = AP.run_pipeline("demo.test", {"candidate": {}}, {}, stages=stages,
                        budget=AC.AutoBudget(max_steps=1))
    assert r.get("halted") is True
    assert len(seen) == 1


# ── default OFF ──────────────────────────────────────────────────────────
def test_scheduled_pipeline_is_default_off():
    """Registering the bg task must be behaviour-neutral."""
    assert AP.ENABLE_KEY == "automation.pipeline_enabled"
    r = AP.scheduled_pipeline()
    assert r["ran"] is False
    assert r["reason"] == "disabled"
