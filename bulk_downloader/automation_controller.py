"""bulk_downloader.automation_controller -- A9: supervised-autonomy controller.

The maximalist capstone. The operator sets policy ONCE and the controller runs
the A1->A2/A3/A5/A7/A8 loop unattended for trusted-auto hosts, surfacing ONLY
trust-boundary exceptions.

LOAD-BEARING, BUILT FIRST (per the roadmap): the MASTER OFF-SWITCH and full
reversibility.

  * The master off-switch (``automation.master_off_switch``) is an instant
    revert-to-manual that DOMINATES the controller toggle and is checked BEFORE
    any policy evaluation. While engaged the controller is inert -- no step
    runs, regardless of any other toggle. (Read via ``_read_off_switch`` so the
    kill path is a single, test-pinned indirection.)
  * Every action is recorded to an audit trail entry carrying a ``revert``
    handle, so the run is fully reversible + traceable.

Posture: the ``controller`` toggle is DEFAULT OFF and NOT keystone-required --
it ORCHESTRATES already-gated entries (auto_promote / self_heal remain
individually keystone-gated downstream; defense in depth). Boundary-crossing
candidates (new API host, first-time host, below the resolution floor, a
content-rule hit) are SURFACED for manual confirm and never auto-acted.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from typing import Any, Callable, Dict, List, Optional

from . import lifecycle_automation as la

_OFF_SWITCH_KEY = "automation.master_off_switch"


def _read_off_switch() -> bool:
    """Read the master off-switch (default False = NOT engaged). Single
    indirection so the kill path is test-pinned. Fail-safe: any error -> treat
    as ENGAGED (fail to the safe, manual side)."""
    try:
        from . import global_config
        return bool(global_config.get(_OFF_SWITCH_KEY, False))
    except Exception:
        return True  # cannot read -> assume engaged (safe: revert to manual)


def off_switch_engaged() -> bool:
    return _read_off_switch()


def controller_armed() -> bool:
    """Armed iff the controller toggle is on AND the master off-switch is clear.
    The off-switch always wins."""
    if off_switch_engaged():
        return False
    return la.is_enabled("controller")


def is_trusted_auto(host: str, policy: Dict[str, Any]) -> bool:
    """Only hosts in the operator's trusted-auto set run unattended."""
    trusted = (policy or {}).get("trusted_auto") or set()
    try:
        return host in trusted
    except Exception:
        return False


def classify_boundary(host: str, candidate: Dict[str, Any],
                      policy: Dict[str, Any]) -> List[str]:
    """Return the trust-boundary reasons that force a manual confirm. Empty list
    == clean (auto-actable). Boundaries: new API host, first-time host, below
    the resolution floor, a content-rule hit."""
    reasons: List[str] = []
    cand = candidate or {}
    pol = policy or {}

    if cand.get("new_api_host"):
        reasons.append(f"new_api_host:{cand.get('new_api_host')}")
    if cand.get("first_time"):
        reasons.append("first_time_host")

    floor = pol.get("resolution_floor")
    res = cand.get("resolution")
    if floor is not None and res is not None:
        try:
            if int(res) < int(floor):
                reasons.append(f"below_resolution_floor:{res}<{floor}")
        except Exception:
            reasons.append("resolution_unparseable")

    for rule in (pol.get("content_rules") or []):
        try:
            if callable(rule) and rule(cand):
                reasons.append("content_rule_hit")
                break
        except Exception:
            reasons.append("content_rule_error")
            break

    return reasons



# ── X-AUTO-2 (v3.66.707): a CEILING on the autonomous cycle ──────────────────
# run_host_cycle is the ONE place autonomous action happens, and its only brake was the
# master off-switch -- which is binary. Past that gate the loop ran EVERY step with no
# wall-clock limit, no step cap, and -- the runaway -- it CONTINUED AFTER A FAILURE. A
# pipeline failing every step kept right on executing steps, burning actions while
# broken. This is the ceiling that stops it.
#
# A ceiling is a HALT, not a skip: on breach the cycle STOPS and says why. A guardrail
# that lets the loop finish "just this once" is not a guardrail.
#
# Default = UNCAPPED (0 on every field), so an unconfigured cycle is BYTE-IDENTICAL to
# pre-707. Opt-in, like every other automation control -- but an operator running L2
# autonomy should set it. (Same shape as run_budget.RunBudget, deliberately: one budget
# idiom across the codebase.)
@dataclass
class AutoBudget:
    max_steps: int = 0      # max steps executed in one cycle (0 = uncapped)
    wall_s: float = 0.0     # max wall-clock seconds for one cycle (0 = uncapped)
    max_errors: int = 0     # halt after this many failing steps (0 = uncapped)

    def is_active(self) -> bool:
        return bool(self.max_steps or self.wall_s or self.max_errors)

    def breach(self, *, steps: int = 0, elapsed_s: float = 0.0,
               errors: int = 0) -> Optional[str]:
        """The name of the ceiling that broke, or None. Checked BEFORE each step, so a
        breach prevents the NEXT action rather than merely noticing the last one."""
        if self.max_steps and steps >= self.max_steps:
            return "max_steps"
        if self.wall_s and elapsed_s >= self.wall_s:
            return "wall_s"
        if self.max_errors and errors >= self.max_errors:
            return "max_errors"
        return None


def budget_from_config() -> "AutoBudget":
    """The operator's configured cycle ceiling. Unset -> uncapped (today's behaviour)."""
    def _get(key, cast, default):
        try:
            from . import global_config
            return cast(global_config.get(key, default) or default)
        except Exception:
            return default
    return AutoBudget(max_steps=_get("automation.cycle_max_steps", int, 0),
                      wall_s=_get("automation.cycle_wall_s", float, 0.0),
                      max_errors=_get("automation.cycle_max_errors", int, 0))


def run_host_cycle(host: str, ctx: Dict[str, Any], policy: Dict[str, Any], *,
                   steps: Optional[Dict[str, Callable[[str, Dict[str, Any]], Any]]] = None,
                   budget: Optional["AutoBudget"] = None
                   ) -> Dict[str, Any]:
    """Run one supervised-autonomy cycle for ``host``.

    Order is deliberate and the FIRST check is the master off-switch:
      1. off-switch engaged -> INERT (nothing runs);
      2. controller disarmed -> skip;
      3. host not trusted-auto -> surface for manual;
      4. candidate crosses a trust boundary -> surface for manual;
      5. otherwise run each loop ``step`` (injected), auditing every action with
         a revert handle; a throwing step is isolated + audited, the loop
         continues.
    """
    # 1. MASTER OFF-SWITCH -- dominates everything, checked first.
    if off_switch_engaged():
        return {"ok": True, "inert": True, "reason": "master_off_switch_engaged",
                "ran": False, "audit": []}

    # 2. Capability toggle.
    if not la.is_enabled("controller"):
        return {"ok": True, "skipped": "controller disabled", "ran": False,
                "audit": []}

    # 3. Trusted-auto gate.
    if not is_trusted_auto(host, policy):
        return {"ok": True, "surfaced": True, "ran": False,
                "reasons": ["not_trusted_auto"], "audit": []}

    # 4. Trust-boundary classification.
    candidate = (ctx or {}).get("candidate") or {}
    reasons = classify_boundary(host, candidate, policy)
    if reasons:
        return {"ok": True, "surfaced": True, "ran": False, "reasons": reasons,
                "audit": []}

    # 5. Clean trusted candidate -> run the loop, auditing every action, UNDER A CEILING.
    #    The budget is checked BEFORE each step: a breach prevents the NEXT action rather
    #    than merely noticing the last one. On breach the cycle HALTS -- it does not
    #    quietly run the remaining steps.
    bud = budget if budget is not None else budget_from_config()
    audit: List[Dict[str, Any]] = []
    errors = 0
    started = time.time()
    halted, halt_reason = False, ""
    for name, fn in (steps or {}).items():
        if bud.is_active():
            why = bud.breach(steps=len(audit),
                             elapsed_s=time.time() - started,
                             errors=errors)
            if why:
                halted, halt_reason = True, why
                break
        entry: Dict[str, Any] = {"step": name, "host": host,
                                 "revert": {"kind": "restore_prior",
                                            "host": host, "step": name}}
        try:
            entry["result"] = fn(host, ctx)
        except Exception as e:
            entry["error"] = str(e)[:160]
            errors += 1
        audit.append(entry)

    out = {"ok": True, "ran": True, "host": host, "audit": audit}
    if halted:
        # The halt must be VISIBLE: a guardrail the operator cannot see fired is not a
        # guardrail.
        #
        # This comment used to claim the halt "surfaces on the AF5 timeline / AF7
        # digest". It surfaced on NEITHER: the verdict was RETURNED up through
        # scheduled_pipeline to a scheduler task wrapper that discarded it, and was
        # persisted nowhere. The comment ASSERTED the property instead of the code
        # HAVING it -- an aspirational doc, which is how the dominant failure shape
        # here always starts.
        #
        # As of 723 it is DERIVED: automation_pipeline.scheduled_pipeline persists
        # every pass that ran, and GET /api/automation/status renders it. This
        # returned dict is now read by something.
        out["halted"] = True
        out["halt_reason"] = halt_reason
        out["errors"] = errors
    return out
