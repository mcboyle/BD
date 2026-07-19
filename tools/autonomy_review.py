"""Human review experience (Phase D). Makes human approval of correctness-critical
changes BETTER-INFORMED — the opposite of autonomy.

This is a read-only composition over what Phases A–C already record. It does NOT
enable any autonomy (Class C auto stays impossible until the Phase E oracle), and it
does NOT mutate anything: the review DECISION itself flows through the existing
audited paths (`autonomy_guardrails.mark_reviewed` / the inert `/api/review/decide`).
Phase D only makes the human well-informed before that deliberate commit.

Four surfaces (doc §10 Step 5):
  * evidence_chain   — for a change: its decision snapshot (policy version + hash +
    inputs), the before/after/diff, the pending-review status + fail-closed deadline,
    and the site's evidence base. The falsifiable "why", reconstructable later.
  * change_diff      — the before/after with the structural diff highlighted.
  * rollback_preview — exactly what reverting WOULD restore, without executing.
  * decision_audit   — one timeline across the policy audit (A), the housekeeping log
    (B), and the guardrail alerts + change ledger + review decisions (C).

No writes, no live fetch, no external push, no scheduler. No module-level I/O.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from tools import autonomy_policy as ap
from tools import autonomy_guardrails as agr
from tools import autonomy_housekeeping as ahk


# ── evidence chain ───────────────────────────────────────────────────────────
def _site_evidence(site: Optional[str]) -> Dict[str, Any]:
    """A light, read-only evidence summary for a site (degrades gracefully when the
    site/config is absent, e.g. in a fresh environment)."""
    if not site:
        return {"site": None, "note": "change is not site-scoped"}
    try:
        from tools.cockpit_templates import site_playbook
        pb = site_playbook(site)
        return {"site": site,
                "open_concerns": pb.get("known_failure_modes") or pb.get("open_concerns"),
                "confidence_history": pb.get("confidence_history"),
                "drift_history": pb.get("drift_history"),
                "families": pb.get("families")}
    except Exception:
        return {"site": site, "note": "no evidence on file in this environment"}


def _pending_status(change_id: str) -> Optional[Dict[str, Any]]:
    return agr._load_pending().get("pending", {}).get(change_id)


def evidence_chain(change_id: str) -> Dict[str, Any]:
    """The full, reconstructable provenance of a proposed/applied change: decision
    snapshot (policy state in effect), before/after/diff, review window status, and the
    site evidence base. Read-only."""
    rec = agr.change_record(change_id)
    if not rec:
        return {"ok": False, "error": "change not found"}
    snap = None
    sid = rec.get("snapshot_id")
    if sid:
        snap = ap.get_decision_snapshot(sid)
    pend = _pending_status(change_id)
    return {
        "ok": True,
        "change_id": change_id,
        "action_class": rec.get("action_class"),
        "target": {"kind": rec.get("target_kind"), "ref": rec.get("target_ref")},
        "recorded_by": rec.get("by"),
        "recorded_at": rec.get("ts"),
        "rolled_back": rec.get("rolled_back"),
        "decision_snapshot": snap or {
            "note": "no decision snapshot linked — an autonomous Class C change would "
                    "carry one (policy version + hash + inputs)."},
        "diff": rec.get("diff"),
        "review": pend or {"note": "not registered for review"},
        "site_evidence": _site_evidence(rec.get("site") or rec.get("target_ref")
                                        if rec.get("action_class") == "C" else None),
        "_note": "Read-only evidence chain. The decision is committed via the existing "
                 "audited review path (mark_reviewed / /api/review/decide), not here.",
    }


# ── before/after diff ────────────────────────────────────────────────────────
def _selectorize(diff: Any) -> Dict[str, Any]:
    """Render a structural diff into added/removed/changed for human reading."""
    added, removed, changed = [], [], []
    if isinstance(diff, dict):
        for k, v in diff.items():
            if isinstance(v, dict) and "from" in v and "to" in v:
                frm, to = v["from"], v["to"]
                if isinstance(frm, list) and isinstance(to, list):
                    added += [f"{k}: +{x}" for x in to if x not in frm]
                    removed += [f"{k}: -{x}" for x in frm if x not in to]
                else:
                    changed.append({"field": k, "from": frm, "to": to})
    return {"added": added, "removed": removed, "changed": changed}


def change_diff(change_id: str) -> Dict[str, Any]:
    """The before/after of a change plus the structured diff (added/removed/changed),
    for human review. Read-only."""
    rec = agr.change_record(change_id)
    if not rec:
        return {"ok": False, "error": "change not found"}
    return {"ok": True, "change_id": change_id,
            "before": rec.get("before"), "after": rec.get("after"),
            "diff": rec.get("diff"), "summary": _selectorize(rec.get("diff")),
            "_note": "Read-only diff."}


# ── rollback preview (never executes) ────────────────────────────────────────
def rollback_preview(change_id: str) -> Dict[str, Any]:
    """What rolling this change back WOULD restore — without executing anything. Read-
    only: shows the current (after) state, the restore (before) state, and whether the
    change is reversible."""
    rec = agr.change_record(change_id)
    if not rec:
        return {"ok": False, "error": "change not found"}
    reversible = rec.get("target_kind") in agr._REVERSERS
    return {
        "ok": True, "change_id": change_id,
        "reversible": reversible,
        "already_rolled_back": bool(rec.get("rolled_back")),
        "currently_applied": rec.get("after"),
        "would_restore": rec.get("before"),
        "target": {"kind": rec.get("target_kind"), "ref": rec.get("target_ref")},
        "_note": "PREVIEW ONLY — nothing is reverted here. Execute via "
                 "autonomy_guardrails.rollback(change_id, by).",
    }


# ── unified decision audit ───────────────────────────────────────────────────
def decision_audit(limit: int = 200) -> Dict[str, Any]:
    """One timeline across every governance/automation decision: policy edits + safety
    demotions + freeze/unfreeze (Phase A), Class B housekeeping (Phase B), and
    guardrail alerts + change ledger + review decisions (Phase C). Read-only."""
    events: List[Dict[str, Any]] = []

    for e in ap.read_audit(limit=10000):
        events.append({"ts": e.get("ts"), "source": "policy", "kind": e.get("action"),
                       "detail": _policy_detail(e), "by": e.get("by")})

    for e in ahk.housekeeping_log(limit=10000):
        if e.get("mode") == "marker":
            continue
        kind = "housekeeping:" + (e.get("action") or "")
        det = (e.get("reason") or
               (f"changed {e.get('changed')}" if e.get("changed") is not None else "") or
               (f"created {e.get('created')}" if e.get("created") is not None else ""))
        events.append({"ts": e.get("ts"), "source": "housekeeping", "kind": kind,
                       "detail": det, "by": e.get("by")})

    for a in agr.alerts(limit=10000):
        events.append({"ts": a.get("ts"), "source": "guardrail", "kind": a.get("kind"),
                       "detail": a.get("detail"), "by": a.get("by")})

    for c in agr.list_changes(limit=10000):
        events.append({"ts": c.get("ts"), "source": "change",
                       "kind": "rolled_back" if c.get("rolled_back") else "recorded",
                       "detail": f"{c.get('id')} ({c.get('target_kind')})",
                       "by": None})

    events.sort(key=lambda x: x.get("ts") or "", reverse=True)
    return {"events": events[:limit], "total": len(events),
            "_note": "Unified read-only decision audit across Phases A–C."}


def _policy_detail(e: Dict[str, Any]) -> str:
    a = e.get("action")
    if a in ("set_policy_level", "safety_demote"):
        return f"{e.get('class')}: {e.get('from')} → {e.get('to')} (v{e.get('version')})"
    if a in ("freeze", "unfreeze"):
        return e.get("reason") or ""
    return e.get("reason") or ""


# ── review dashboard ─────────────────────────────────────────────────────────
def review_dashboard() -> Dict[str, Any]:
    """The reviewer's home: outstanding changes awaiting review (Phase C pending),
    each with an evidence-chain pointer and its fail-closed deadline, ordered with the
    soonest deadline first. Read-only."""
    pending = agr.outstanding_unreviewed()
    items = []
    for p in pending:
        cid = p.get("change_id")
        items.append({
            "change_id": cid, "action_class": p.get("action_class"),
            "site": p.get("site"), "deadline": p.get("deadline"),
            "applied_by": p.get("by"),
            "evidence_chain": f"/cockpit/api/review/evidence?change_id={cid}",
            "diff": f"/cockpit/api/review/diff?change_id={cid}",
            "rollback_preview": f"/cockpit/api/review/rollback-preview?change_id={cid}",
        })
    items.sort(key=lambda x: (x["deadline"] or "9999"))  # soonest deadline first
    return {
        "pending": items, "pending_count": len(items),
        "backlog": agr.backlog_ok(),
        "inflight_sites": agr.inflight_sites(),
        "_note": "Outstanding reviews, soonest fail-closed deadline first. Class C "
                 "changes auto-revert if their window expires unreviewed. Decisions are "
                 "committed via the existing audited review path — this view informs "
                 "them. Empty until there are changes to review.",
    }
