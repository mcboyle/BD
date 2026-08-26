"""automation_pipeline -- A-PIPE / A9: the capstone that actually RUNS the chain.

The automation program was ~90% BUILT and 0% RUNNING. A0 (gold backup), A1 (scheduled
drift sweep), A2/A5 (keystone-backed refresh), A3 (quarantine state), A-REPAIR
(drift -> review-only draft), X-AUTO-1 (restore rehearsal) and X-AUTO-2 (the cycle
ceiling) all exist -- and `automation_controller.run_host_cycle`, the ONE function that
executes an autonomous cycle (off-switch, trust gates, audit trail, ceiling), was called
by NOTHING. No scheduler task, no route. The engine was built and never connected.

This module is the connection: the ordered, checkpointed chain

    capture -> build -> lint -> blocked_scan -> drift_stage -> apply

run as ONE flow with a rollback point at each stage, instead of discrete operator-poked
steps.

THE SAFETY PROPERTIES, and why each is the way it is:

  * ORDER IS SAFETY. `lint` and `blocked_scan` MUST precede `drift_stage`/`apply`. A
    pipeline free to reorder could apply a template it never checked.

  * A FAILED STAGE HALTS THE CHAIN. It does not carry on. This is the opposite of
    run_host_cycle's step behaviour ("a throwing step is isolated, the loop continues"),
    and deliberately so: steps in a cycle are INDEPENDENT, stages in a pipeline are
    SEQUENTIAL AND DEPENDENT. Carrying on past a failed `lint` would apply a template
    that failed its check -- the failure would compound instead of stopping.

  * MUTATING STAGES ARE A0-BACKED. `drift_stage` and `apply` take a restorable snapshot
    FIRST, and if the snapshot cannot be taken the stage does NOT run (fail-closed --
    the A0 contract). An autonomous overwrite without a recovery point is precisely what
    A0 exists to forbid.

  * IT RUNS THROUGH run_host_cycle. The off-switch, the trust gates, the audit trail and
    the X-AUTO-2 ceiling are INHERITED, not re-implemented. Two budgets would be two
    behaviours.

  * DEFAULT OFF. Registering the bg task is behaviour-neutral.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import automation_controller as ac

ENABLE_KEY = "automation.pipeline_enabled"

# The stages that MUTATE. These are the ones that must be A0-backed.
MUTATING = {"drift_stage", "apply"}

Stage = Tuple[str, Callable[[str, Dict[str, Any]], Any]]


def _enabled() -> bool:
    try:
        from . import global_config
        return bool(global_config.get(ENABLE_KEY, False))
    except Exception:
        return False


def _snapshot(host: str) -> Dict[str, Any]:
    """The A0 keystone snapshot: a generational, restorable backup of the live gold
    BEFORE a mutating stage runs. Delegates to template_backup (the authority) -- never
    re-derives it."""
    try:
        from . import template_backup as tb
        return tb.backup_template(host, reason="a_pipe")
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:120]}


def _noop(name: str) -> Callable[[str, Dict[str, Any]], Any]:
    def _fn(host: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
        return {"stage": name, "noop": True}
    return _fn


def default_stages() -> List[Stage]:
    """The documented chain. Inert placeholders by default: the ORDER is the contract
    this module owns; each stage's real implementation is injected by the caller (or
    wired in a later cut) so this capstone never silently re-implements a step that
    already exists elsewhere."""
    return [(n, _noop(n)) for n in
            ("capture", "build", "lint", "blocked_scan", "drift_stage", "apply")]


def run_pipeline(host: str,
                 ctx: Dict[str, Any],
                 policy: Dict[str, Any],
                 *,
                 stages: Optional[List[Stage]] = None,
                 budget: Optional["ac.AutoBudget"] = None) -> Dict[str, Any]:
    """Run the chain for one host, THROUGH run_host_cycle (so the off-switch, trust
    gates, audit and the 707 ceiling all apply), halting on the first stage failure.

    Returns {ok, host, audit, halted_at?, error?, inert?, halted?}."""
    chain = list(stages if stages is not None else default_stages())

    state: Dict[str, Any] = {"failed": None, "error": ""}

    def _wrap(name: str, fn: Callable[[str, Dict[str, Any]], Any]):
        def _staged(h: str, c: Dict[str, Any]):
            # Once a stage has failed, every later stage is a NO-OP. run_host_cycle owns
            # the loop (and the ceiling), so the halt is expressed here rather than by
            # breaking out of someone else's loop.
            if state["failed"]:
                return {"stage": name, "skipped": "halted", "after": state["failed"]}
            if name in MUTATING:
                snap = _snapshot(h)
                if not snap.get("ok"):
                    # FAIL-CLOSED: no recovery point -> the mutating stage does NOT run.
                    state["failed"] = name
                    state["error"] = (f"A0 snapshot failed before '{name}'; stage "
                                      f"aborted: {snap.get('error')}")
                    raise RuntimeError(state["error"])
            try:
                result = fn(h, c)
                # Stages use conventional result dictionaries as well as
                # exceptions.  ``ok: false`` is a completed measurement of
                # failure, not a successful call merely because it returned.
                if isinstance(result, dict) and result.get("ok") is False:
                    state["failed"] = name
                    state["error"] = str(
                        result.get("error") or result.get("reason") or
                        f"stage '{name}' returned ok:false"
                    )[:200]
                return result
            except Exception as e:
                state["failed"] = name
                state["error"] = str(e)[:200]
                raise
        return _staged

    steps = {name: _wrap(name, fn) for name, fn in chain}
    started = time.time()
    res = ac.run_host_cycle(host, ctx, policy, steps=steps, budget=budget)

    out: Dict[str, Any] = {
        "host": host,
        "audit": [a for a in res.get("audit", [])
                  if not (a.get("result") or {}).get("skipped")]
        if isinstance(res.get("audit"), list) else [],
        "elapsed_s": round(time.time() - started, 3),
    }
    # the controller's own verdicts pass straight through -- it outranks this module
    for k in ("inert", "reason", "skipped", "surfaced", "reasons", "halted",
              "halt_reason"):
        if k in res:
            out[k] = res[k]
    if res.get("inert") or res.get("skipped") or res.get("surfaced"):
        out["ok"] = bool(res.get("ok", True))
        return out
    if state["failed"]:
        out["ok"] = False
        out["halted_at"] = state["failed"]
        out["error"] = state["error"]
        return out
    out["ok"] = True
    return out


def _record_cycle(out: Dict[str, Any]) -> None:
    """Fail-soft bridge to the AF5 readout. A readout that can take out the loop it
    reports on is worse than no readout -- so this never raises. It does not go
    quiet either: automation_status writes to stderr if the verdict is lost."""
    try:
        from . import automation_status as _as
        _as.record_cycle(out)
    except Exception:
        pass


def scheduled_pipeline(*, policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The bg-scheduler entry point. NO-OPS unless the opt-in flag is on, so registering
    the task is behaviour-neutral. Never raises."""
    try:
        if not _enabled():
            return {"ran": False, "reason": "disabled"}
        budget = ac.budget_from_config()
        hosts = list((policy or {}).get("trusted_auto_hosts") or [])
        results = [run_pipeline(h, {"candidate": {}}, policy or {}, budget=budget)
                   for h in hosts]
        out = {"ran": True, "reason": "ok", "hosts": len(results),
               "halted": [r["host"] for r in results if not r.get("ok", True)]}
        # AF5 (723): PERSIST the verdict. Until now this dict was returned to a
        # scheduler task wrapper that threw it away, so a halt -- the guardrail
        # firing -- was visible to nobody by morning. Recorded HERE rather than in
        # the caller so the verdict survives no matter who drives the pass.
        _record_cycle(out)
        return out
    except Exception as e:
        return {"ran": False, "reason": f"error:{type(e).__name__}"}
