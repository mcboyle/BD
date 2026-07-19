"""Rollback Center (Phase G / G2) — READ-ONLY orchestration & capability surface over
the guardrail rollback engine. It performs NO rollback itself: reverting a change is an
audited guardrail function (`autonomy_guardrails.rollback` / the reject path of
`mark_reviewed` / `sweep_review_windows`), invoked by the host-scheduled cycle or the
operator — never from this module, and never from a cockpit button.

What this layer ADDS over the guardrail engine:
  * read-only views of rollback HISTORY, current REVERSIBILITY, the registered-reverser
    registry, and pending review windows;
  * `rollback_capability(target_kind)` — whether a reverser is registered for a target
    kind. This is the precondition the eligibility layer (G1) consults: a change is only
    eligible if it can be reverted. A change kind with no registered reverser is
    irreversible and therefore never eligible.

The engine itself already guarantees the load-bearing properties (proven by the
guardrail tests and re-anchored by this phase's tests): a revert restores before-state
and is idempotent; a review REJECTION triggers an immediate revert; an expired-unreviewed
Class C change auto-reverts (fail-closed); and a reverser that ERRORS freezes all
automation (the guardrail-failure branch) rather than leaving state half-reverted.

POSTURE: read-only; no module-level I/O; no mutation here (no apply, no revert execution,
no policy or review writes). No network, browser, media re-download, byte comparison, or
capture/login execution.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional

from tools import autonomy_policy as ap
from tools import autonomy_guardrails as agr


def _now_dt() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _now() -> str:
    return _now_dt().isoformat()


def _registered_kinds() -> List[str]:
    """Target kinds that have a registered reverser. Read-only view of the guardrail
    engine's reverser registry."""
    try:
        return sorted(agr._REVERSERS.keys())
    except Exception:
        return []


def reverser_registry() -> Dict[str, Any]:
    """The target kinds with a registered reverser (the kinds of change that CAN be
    reverted). In this build only the confined `staging_json` target is registered; a
    real Class C apply would register its own reverser at apply time. Read-only."""
    kinds = _registered_kinds()
    return {"target_kinds": kinds, "count": len(kinds),
            "_note": "Registered reversers = the change kinds that are reversible. A "
                     "change kind with no reverser is irreversible and never eligible. "
                     "Read-only."}


def rollback_capability(target_kind: Optional[str] = None) -> Dict[str, Any]:
    """Whether a change of `target_kind` can be reverted. With no `target_kind`, reports
    whether the rollback engine is OPERATIONAL (>= 1 reverser registered). This is the
    precondition the eligibility layer consults — no reverser => not eligible."""
    kinds = set(_registered_kinds())
    if target_kind is None:
        ok = len(kinds) > 0
        return {"target_kind": None, "reversible": ok,
                "reason": "rollback engine operational" if ok else "no reverser registered",
                "_note": "Engine-readiness check (no specific target)."}
    ok = target_kind in kinds
    return {"target_kind": target_kind, "reversible": ok,
            "reason": (f"reverser registered for '{target_kind}'" if ok else
                       f"no registered reverser for '{target_kind}' — irreversible"),
            "_note": "A change is eligible only if its target kind is reversible."}


def rollback_history(limit: int = 200) -> Dict[str, Any]:
    """Recorded changes with rollback status (oldest first, capped). Read-only — reflects
    the guardrail change log; reverting happens elsewhere."""
    rows = agr.list_changes(limit=limit)
    rolled = [r for r in rows if r.get("rolled_back")]
    return {"changes": rows, "total": len(rows), "rolled_back": len(rolled),
            "_note": "Change history + which entries were reverted. Read-only."}


def reversibility_report() -> Dict[str, Any]:
    """Per recorded change: currently reversible? (a reverser is registered for its kind
    AND it is not already rolled back). Read-only."""
    kinds = set(_registered_kinds())
    rows: List[Dict[str, Any]] = []
    for c in agr.list_changes(limit=1000):
        tk = c.get("target_kind")
        already = bool(c.get("rolled_back"))
        rev = (tk in kinds) and not already
        rows.append({"id": c.get("id"), "target_kind": tk, "rolled_back": already,
                     "reversible_now": rev,
                     "reason": ("already rolled back" if already else
                                ("reverser registered" if tk in kinds else
                                 f"no reverser for '{tk}'"))})
    irreversible = [r["id"] for r in rows if not r["reversible_now"] and not r["rolled_back"]]
    return {"changes": rows, "count": len(rows), "irreversible_pending": irreversible,
            "_note": "A pending change with no reverser is irreversible — that is a "
                     "guardrail failure if it were ever applied. Read-only."}


def pending_windows() -> Dict[str, Any]:
    """Pending review windows (read-only snapshot). Flags expired-unreviewed Class C
    windows that the next host-invoked sweep would auto-revert; does NOT sweep here."""
    now = _now_dt()
    out: List[Dict[str, Any]] = []
    for p in agr.outstanding_unreviewed():
        dl = p.get("deadline")
        expired = False
        if p.get("action_class") == "C" and dl:
            try:
                expired = now > _dt.datetime.fromisoformat(dl)
            except Exception:
                expired = False
        out.append({"change_id": p.get("change_id"), "action_class": p.get("action_class"),
                    "site": p.get("site"), "deadline": dl,
                    "expired_would_auto_revert": expired})
    return {"pending": out, "count": len(out),
            "expired_pending_class_c": [p["change_id"] for p in out
                                        if p["expired_would_auto_revert"]],
            "_note": "Read-only. Expired-unreviewed Class C windows auto-revert on the "
                     "next host-invoked sweep (fail-closed) — not from here."}


def rollback_center() -> Dict[str, Any]:
    """The Rollback Center dashboard (read-only): engine readiness, reverser registry,
    rollback-history counts, pending/expiring windows, throttle health, freeze state."""
    reg = reverser_registry()
    hist = rollback_history(limit=1000)
    pw = pending_windows()
    thr = agr.throttle_metrics()
    return {
        "engine_operational": reg["count"] > 0,
        "reverser_kinds": reg["target_kinds"],
        "reverser_count": reg["count"],
        "changes_recorded": hist["total"],
        "changes_rolled_back": hist["rolled_back"],
        "pending_windows": pw["count"],
        "expired_pending_class_c": pw["expired_pending_class_c"],
        "rollback_rate": thr.get("rollback_rate"),
        "review_expiry_rate": thr.get("review_expiry_rate"),
        "frozen": ap.is_frozen(),
        "_note": "Read-only Rollback Center. Reverting a change is an audited guardrail "
                 "function invoked by the host cycle or operator — never from the "
                 "cockpit. Review rejection triggers a revert; expired Class C "
                 "auto-reverts; a reverser error freezes all automation.",
    }
