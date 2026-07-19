"""Impact Analysis (Phase G / G5) — READ-ONLY analysis of a SINGLE proposed change.

Given a proposed change (a candidate dict), this reports what it would touch and whether
it would clear the safety gates, composing the earlier phases:
  * blast radius — how many sites the change would affect (single-change only; a candidate
    naming more than one site is flagged as family-wide, which is out of scope), plus the
    concurrency limit (no other site may have an in-flight change);
  * reversibility — whether a reverser is registered for the target kind (G2);
  * pinned target — whether it touches a permanently-ineligible action/credential;
  * trust + oracle tier + evidence-qualification (G1/G3, via `eligibility.evaluate_site`).

`safe_to_consider` is True only if it would clear all of those gates. Even then,
`participation_eligible` is **always False** (Approve-each; no Class C apply path) — this
module analyses; it never promotes a change across a family and never applies anything.

POSTURE: read-only; no module-level I/O; no apply/promotion writes; single-change scope
only; no network/browser/capture/credential access.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from tools import autonomy_oracle as ao
from tools import autonomy_eligibility as el
from tools import autonomy_rollback as arb
from tools import autonomy_guardrails as agr
from tools import autonomy_trust as atr


def impact_report(candidate: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Read-only impact analysis of one proposed change. `candidate` may carry `site`,
    `target_kind`, `action`, and (for footprint) `sites`."""
    candidate = candidate or {}
    site = candidate.get("site", "")
    target_kind = candidate.get("target_kind")
    action = candidate.get("action")
    affected = candidate.get("sites") or ([site] if site else [])
    family_wide = len(affected) > 1

    elig = el.evaluate_site(site, candidate=candidate)
    ov = ao.oracle_verdict(site, candidate=candidate)
    rev = arb.rollback_capability(target_kind)
    br = agr.blast_radius_ok(site)
    pinned = bool(elig.get("candidate_blocked"))

    concerns: List[str] = []
    if not rev["reversible"]:
        concerns.append(rev["reason"])
    if pinned:
        concerns.append(f"targets a permanently-ineligible action: {action}")
    if not elig["evidence_qualified"]:
        concerns.append("not evidence-qualified: " + "; ".join(elig.get("decay_reasons", [])))
    if family_wide:
        concerns.append(f"affects {len(affected)} sites — family-wide promotion is out of "
                        f"scope (single-change analysis only)")
    if not br["ok"]:
        concerns.append(f"blast-radius: another site has an in-flight change "
                        f"({br.get('blocking_sites')})")

    safe = bool(rev["reversible"] and not pinned and elig["evidence_qualified"]
                and not family_wide and br["ok"])
    return {
        "site": site, "target_kind": target_kind, "action": action,
        "affected_sites": affected, "blast_radius": len(affected), "family_wide": family_wide,
        "reversible": rev["reversible"],
        "touches_pinned": pinned,
        "oracle_tier": ov.get("tier"), "oracle_tier_name": ov.get("tier_name"),
        "trust": elig.get("trust"), "trust_eligible": atr.trust_eligible(site) if site else False,
        "evidence_qualified": elig["evidence_qualified"],
        "participation_eligible": elig["participation_eligible"],  # always False
        "inflight_ok": br["ok"], "blocking_sites": br.get("blocking_sites", []),
        "concerns": concerns, "safe_to_consider": safe,
        "_note": "Read-only analysis of a SINGLE proposed change. 'safe_to_consider' means "
                 "it would clear the reversibility / pinned / evidence / blast-radius gates "
                 "— but participation_eligible is still False (Approve-each; no Class C "
                 "apply path). This never promotes a change family-wide and never applies "
                 "anything.",
    }


def impact_overview(sites: Optional[List[str]] = None) -> Dict[str, Any]:
    """Per-site impact of a benign, reversible probe change (target_kind='staging_json').
    Shows each site's reversibility / trust / tier / evidence-qualification. Read-only."""
    sites = sites if sites is not None else ao._all_sites()
    rows = [impact_report({"site": s, "target_kind": "staging_json"}) for s in sites]
    return {"sites": rows, "site_count": len(rows),
            "any_participation_eligible": any(r["participation_eligible"] for r in rows),
            "safe_to_consider_count": sum(1 for r in rows if r["safe_to_consider"]),
            "_note": "Read-only. A benign reversible probe per site. participation_eligible "
                     "is False everywhere (no apply path); 'safe_to_consider' only means "
                     "the evidence/safety gates would pass."}


def impact_status() -> Dict[str, Any]:
    """Compact status for the cockpit header. Read-only."""
    ov = impact_overview()
    return {"site_count": ov["site_count"],
            "safe_to_consider_count": ov["safe_to_consider_count"],
            "any_participation_eligible": ov["any_participation_eligible"],
            "_note": "Impact analysis is read-only and single-change. It never promotes "
                     "family-wide or applies anything; participation remains Approve-each "
                     "with no apply path."}
