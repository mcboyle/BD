"""Consolidation — the generic Class-C apply harness + the single authority read surface.

A Class-C apply kind is a REGISTRATION, not a module. `register_apply_kind` records a kind's
hooks (gate / current / proposer / applier / reverser, plus optional corroborate / validator
/ backup / unchanged / transition) and registers its reverser with the guardrail engine.
`apply_for_kind` runs the one shared orchestration: gate -> propose -> corroborate -> before
/after -> backup -> record_change -> apply -> register_pending -> (validate -> revert on
miss) -> transition. Fail-closed semantics (silence->sweep revert, reject->revert,
accept->bless) live in the guardrail chain + `review_decide`, reused verbatim — no new POST.

Authority model (unchanged): the system may auto-suspend a grant (contraction); it may never
auto-create or auto-unsuspend (expansion). No kind applies without a registered reverser.

Imports only `autonomy_guardrails` + `autonomy_promotion` at module level; the read views
lazy-import oracle/eligibility. No module-level I/O.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from tools import autonomy_guardrails as agr
from tools import autonomy_promotion as apr

# belt-and-suspenders only — real enforcement is eligibility + reverser presence + oracle
# hard-failures + the fact that pinned actions are never registered as kinds.
_FORBIDDEN_KIND_SUBSTR = ("credential", "login_template", "login_credential", "corpus",
                          "debt", "finding", "policy", "posture", "release", "cookie")

_APPLY_KINDS: Dict[str, Dict[str, Any]] = {}


def _default_ref(site: str) -> str:
    return f"site::{site}"


def register_apply_kind(kind: str, *, gate: Callable, current: Callable,
                        proposer: Callable, applier: Callable, reverser: Callable,
                        corroborate: Optional[Callable] = None,
                        validator: Optional[Callable] = None,
                        backup: Optional[Callable] = None,
                        unchanged: Optional[Callable] = None,
                        transition: Optional[Callable] = None,
                        target_ref: Optional[Callable] = None,
                        action_class: str = "C",
                        transition_field: Optional[str] = None) -> None:
    """Register a Class-C apply kind. Requires a reverser (no kind applies without one).
    Rejects pinned/credential/login-style kind names (belt-and-suspenders)."""
    if reverser is None:
        raise ValueError(f"apply kind {kind!r} requires a reverser")
    # pinned-action guard (canonical set from the oracle) + substring denylist
    try:
        from tools import autonomy_oracle as _ao
        pinned = set(_ao.PERMANENTLY_INELIGIBLE)
    except Exception:
        pinned = set()
    low = kind.lower()
    if kind in pinned or any(s in low for s in _FORBIDDEN_KIND_SUBSTR):
        raise ValueError(f"refusing to register a permanently-human / unsafe apply kind: {kind!r}")

    tf = transition_field or kind

    def _default_transition(site, before, after, *, by="system",
                            phase="applied", detail=None):
        if phase == "reverted":
            apr.record_transition(site, tf, "applied_pending", "reverted_validation",
                                  by="system", reason=str((detail or {}).get("reason", "")))
        else:
            apr.record_transition(site, tf, "stable", "applied_pending",
                                  by=by, reason=f"{kind} apply")

    _APPLY_KINDS[kind] = {
        "gate": gate, "current": current, "proposer": proposer, "applier": applier,
        "reverser": reverser, "corroborate": corroborate, "validator": validator,
        "backup": backup, "unchanged": unchanged or (lambda b, a: b == a),
        "transition": transition or _default_transition,
        "target_ref": target_ref or _default_ref, "action_class": action_class,
        "transition_field": tf,
    }
    agr.register_reverser(kind, reverser)


def is_registered(kind: str) -> bool:
    return kind in _APPLY_KINDS


def _skip(site, kind, reason, extra=None):
    out = {"ok": False, "skipped": True, "site": site, "kind": kind, "reason": reason}
    if extra:
        out.update(extra)
    return out


def apply_for_kind(site: str, kind: str, *, by: str = "system") -> Dict[str, Any]:
    """The single Class-C apply orchestration. Used by every kind (H, v1, and I/J/K)."""
    s = _APPLY_KINDS.get(kind)
    if s is None:
        return _skip(site, kind, "kind not registered")
    g = s["gate"](site)
    if not g:
        return _skip(site, kind, "not eligible")
    after = s["proposer"](site)
    if after is None:
        return _skip(site, kind, "no proposal")
    if s["corroborate"] and not s["corroborate"](site):
        return _skip(site, kind, "not corroborated")
    before = s["current"](site)
    if before is None and after is None:
        return _skip(site, kind, "nothing to apply")
    if s["unchanged"](before, after):
        return {"ok": True, "skipped": True, "site": site, "kind": kind, "reason": "idempotent"}
    ref = s["target_ref"](site)
    bkp = s["backup"]() if s["backup"] else None
    rec = agr.record_change(kind, ref, before, after, by=by)
    cid = rec.get("id")
    if not cid:
        return {"ok": False, "site": site, "kind": kind, "error": rec.get("error", "record_change failed")}
    s["applier"](site, after)
    pend = agr.register_pending(cid, s["action_class"], site, by)
    if s["validator"]:
        vr = s["validator"](site, after)
        if not vr.get("ok"):
            agr.rollback(cid, by="system:validation")
            try:
                s["transition"](site, before, after, by="system", phase="reverted", detail=vr)
            except Exception:
                pass
            return {"ok": False, "site": site, "kind": kind, "change_id": cid,
                    "reverted": True, "validation": vr}
    try:
        s["transition"](site, before, after, by=by, phase="applied")
    except Exception:
        pass
    return {"ok": True, "site": site, "kind": kind, "change_id": cid,
            "deadline": pend.get("deadline"), "backup": str(bkp) if bkp else None}


def apply_all(kind: str, *, by: str = "system",
              sites: Optional[List[str]] = None) -> Dict[str, Any]:
    """Host-scheduled loop for one kind. With no grant or no tier-3 evidence, nothing is
    eligible and nothing applies (build-dark)."""
    if sites is None:
        try:
            from tools import autonomy_oracle as _ao
            sites = _ao._all_sites()
        except Exception:
            sites = []
    applied, skipped = [], 0
    for site in sites:
        r = apply_for_kind(site, kind, by=by)
        if r.get("ok") and not r.get("skipped"):
            applied.append(site)
        else:
            skipped += 1
    return {"ok": True, "kind": kind, "scanned": len(sites), "applied": applied,
            "applied_count": len(applied), "skipped": skipped}


# ── single authority read surface (cockpit + the /api/live/* compat aliases) ──

def registered_kinds() -> List[Dict[str, Any]]:
    return [{"kind": k, "action_class": s["action_class"],
             "has_reverser": agr.has_reverser(k), "has_validator": s["validator"] is not None}
            for k, s in sorted(_APPLY_KINDS.items())]


def authority_kinds() -> Dict[str, Any]:
    ks = registered_kinds()
    return {"kinds": ks, "count": len(ks),
            "_note": "Registered Class-C apply kinds. Each applies only with a registered "
                     "reverser, an active per-(site,kind) grant, Class C at auto, and tier 3."}


def authority_grants(kind: Optional[str] = None) -> Dict[str, Any]:
    from tools import autonomy_oracle as ao
    from tools import autonomy_eligibility as el
    grants = ao._load_grants()
    rows = []
    for site, kinds in grants.items():
        for k, g in kinds.items():
            if kind is not None and k != kind:
                continue
            try:
                ev = el.evaluate_site(site, kind=k)
            except Exception:
                ev = {}
            rows.append({"site": site, "kind": k, "granted_by": g.get("granted_by"),
                         "granted_at": g.get("granted_at"), "expires_at": g.get("expires_at"),
                         "suspended": g.get("suspended", False),
                         "suspend_reason": g.get("suspend_reason"),
                         "oracle_tier": ev.get("oracle_tier"), "trust": ev.get("trust"),
                         "participation_eligible": ev.get("participation_eligible")})
    rows.sort(key=lambda r: (r["site"], r["kind"]))
    return {"grants": rows, "count": len(rows),
            "_note": "Grants are created human-only (CLI). The system may only suspend "
                     "(contraction), never grant or un-suspend."}


def _pending_rows(kind: Optional[str] = None) -> List[Dict[str, Any]]:
    known = set(_APPLY_KINDS.keys())
    out = []
    for v in agr.outstanding_unreviewed():
        cid = v.get("change_id")
        rec = agr.change_record(cid) if cid else None
        if not rec:
            continue
        k = rec.get("target_kind")
        if k not in known:
            continue
        if kind is not None and k != kind:
            continue
        before, after = rec.get("before") or {}, rec.get("after") or {}
        changed = sorted([key for key in set(list(before.keys()) + list(after.keys()))
                          if before.get(key) != after.get(key)]) \
            if isinstance(before, dict) and isinstance(after, dict) else ["(value)"]
        out.append({"site": v.get("site"), "kind": k, "change_id": cid,
                    "deadline": v.get("deadline"), "changed_keys": changed})
    return out


def authority_pending(kind: Optional[str] = None) -> Dict[str, Any]:
    rows = _pending_rows(kind)
    return {"pending": rows, "count": len(rows),
            "_note": "Pending Class-C changes. Accept blesses (stops the clock); reject "
                     "reverts immediately; silence reverts at the deadline; validation "
                     "failure already reverted."}


def authority_change(change_id: str, kind: Optional[str] = None) -> Dict[str, Any]:
    rec = agr.change_record(change_id)
    if not rec or rec.get("target_kind") not in _APPLY_KINDS:
        return {"change_id": change_id, "exists": False, "_note": "No such Class-C change."}
    k = rec.get("target_kind")
    if kind is not None and k != kind:
        return {"change_id": change_id, "exists": False,
                "_note": f"Change is not of kind {kind!r}."}
    ref = rec.get("target_ref", "")
    site = ref.split("site::", 1)[1] if "site::" in str(ref) else None
    return {"change_id": change_id, "exists": True, "kind": k, "site": site,
            "before": rec.get("before"), "after": rec.get("after"),
            "rollback_preview": rec.get("before"),
            "_note": "Read-only. rollback_preview is the exact prior state a revert restores."}


def authority_status(kind: Optional[str] = None) -> Dict[str, Any]:
    from tools import autonomy_oracle as ao
    grants = ao._load_grants()
    pairs = [(s, k, e) for s, kinds in grants.items() for k, e in kinds.items()
             if kind is None or k == kind]
    pend = _pending_rows(kind)
    deadlines = sorted([p["deadline"] for p in pend if p.get("deadline")])
    try:
        from tools import autonomy_policy as ap
        class_c_allowed = bool(ap.can_autonomously("C").get("allowed"))
    except Exception:
        class_c_allowed = False
    return {"pending_count": len(pend), "next_deadline": deadlines[0] if deadlines else None,
            "grants_active": sum(1 for _, _, e in pairs
                                 if e.get("granted") and not e.get("suspended")),
            "grants_suspended": sum(1 for _, _, e in pairs if e.get("suspended")),
            "kinds": [k["kind"] for k in registered_kinds()],
            "class_c_allowed": class_c_allowed,
            "review_window_hours": agr.REVIEW_WINDOW_HOURS,
            "_note": "Class-C apply. participation_eligible contracts automatically "
                     "(trust/tier/freeze/expiry); it never expands automatically. Grants are "
                     "human-only."}
