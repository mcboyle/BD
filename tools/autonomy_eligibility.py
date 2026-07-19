"""Per-site PARTICIPATION eligibility for tightly-controlled Class C experimentation
(Phase G — Eligibility Governance). EVALUATION ONLY: this module performs no
automation, issues no grant, and creates no Class C apply path.

Building this evaluation infrastructure does NOT enable autonomy. Class C remains at
Approve-each by default, and `participation_eligible` is False for EVERY site in this
build for the same three independent reasons Phase E documents:
  (1) Class C defaults to Approve-each (`autonomy_policy.can_autonomously("C")` is not
      allowed by default);
  (2) the per-site auto-grant store is empty by design (no writer exists), so
      `autonomy_oracle.class_c_site_eligible` is False for every site;
  (3) no Class C apply path exists anywhere in the codebase — `apply_path_exists()`
      below returns False, and participation is gated on it.

What this layer ADDS over the oracle: a *participation* gate with DECAY. The oracle
assesses descriptor quality (Tier 0-3); this layer decides which Tier-3 sites are
*evidence-qualified* to be CONSIDERED for human-approved experimentation, and withdraws
that qualification automatically when the held-out evidence goes stale. The decay is the
load-bearing safety property: trust may only ever DECREASE here. There is no path by
which a site gains eligibility automatically — only the operator designating fresh
held-out evidence can raise the oracle tier, and even then participation stays gated by
the absent grant + absent apply path. Staleness, freezes, in-flight blast-radius, and
pinned/permanently-ineligible targets all REMOVE eligibility.

POSTURE: descriptors by NAME only (inherited from the oracle — this module never
inspects signing values itself). No network fetch, no media re-download, no browser
interaction, no byte comparison, no signed-URL reconstruction, no capture or login
execution. No module-level I/O — every function reads governance state on demand and
returns a verdict; nothing is written here.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional

from tools import autonomy_policy as ap
from tools import autonomy_oracle as ao
from tools import autonomy_guardrails as agr
from tools import autonomy_rollback as arb
from tools import autonomy_trust as atr

# ── thresholds ────────────────────────────────────────────────────────────────
# Held-out evidence older than this is treated as STALE -> qualification decays.
EVIDENCE_FRESH_DAYS = 30
# Only the strongest oracle tier (multiple agreeing held-out captures) can qualify.
MIN_ORACLE_TIER = 3
# "Evaluate only a small number of Tier-3-eligible sites" — a hard cap on how many
# sites may even be CONSIDERED for experimentation at once. Advisory; participation is
# still False for all of them in this build.
MAX_ELIGIBLE_SITES = 3

# Re-export for callers that want the canonical permanently-ineligible action list.
PERMANENTLY_INELIGIBLE = ao.PERMANENTLY_INELIGIBLE


def _now_dt() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _now() -> str:
    return _now_dt().isoformat()


def _as_dt(value: Any) -> Optional[_dt.datetime]:
    """Parse an ISO string / datetime into a tz-aware UTC datetime. Naive inputs are
    assumed UTC. Returns None on anything unparseable (treated as freshness-unknown)."""
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        dt = value
    else:
        try:
            dt = _dt.datetime.fromisoformat(str(value))
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt


def apply_path_exists(kind: str = "live_site_config") -> bool:
    """True iff a Class C apply path for `kind` is loaded — derived from the guardrail
    reverser registry. A kind's apply module registers its reverser on import (via
    `register_apply_kind`); once loaded, the path exists and this returns True. Participation
    is gated on this AND a per-(site, kind) grant AND tier-3 evidence, so this returning True
    does not by itself enable any site. Coupling the apply path to a registered reverser
    guarantees nothing applies that cannot be rolled back."""
    return agr.has_reverser(kind)


def _evidence_ts_for(site: str) -> Optional[str]:
    """When the site's held-out evidence was designated, from the oracle's capture
    provenance (`held_out_designated_at`). Absent in this build's empty stores -> None
    -> freshness UNKNOWN -> treated as stale (fail-safe). Read-only."""
    prov = ao._provenance().get(site, {})
    return prov.get("held_out_designated_at")


def _evidence_age_days(evidence_ts: Any, now_dt: _dt.datetime) -> Optional[float]:
    ts = _as_dt(evidence_ts)
    if ts is None:
        return None
    return (now_dt - ts).total_seconds() / 86400.0


def evaluate_site(site: str, *, kind: str = "live_site_config",
                  held_out: Optional[List[Dict[str, Any]]] = None,
                  evidence_ts: Any = "__derive__", now: Any = None,
                  candidate: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Evaluate one site's participation eligibility. EVALUATION ONLY — never applies,
    approves, grants, or writes anything.

    `held_out` / `candidate` are passed through to the oracle (injectable for tests or
    future wiring; derived from provenance otherwise). `evidence_ts` defaults to the
    designated-at timestamp from provenance; `now` is injectable for decay tests.

    Returns a verdict with two distinct dimensions:
      * `evidence_qualified` — Tier >= MIN_ORACLE_TIER AND evidence fresh AND not frozen
        AND no oracle hard failures AND the candidate (if any) is not a permanently-
        ineligible target. This is what DECAYS: it flips to False automatically when the
        evidence goes stale.
      * `participation_eligible` — `evidence_qualified` AND the per-site Class C gate is
        open (grant + level; always False here) AND a Class C apply path exists (always
        False) AND no OTHER site has an unreviewed change in flight (blast-radius). This
        is False for EVERY site in this build.
    """
    now_dt = _as_dt(now) or _now_dt()
    frozen = ap.is_frozen()

    v = ao.oracle_verdict(site, candidate=candidate, held_out=held_out)
    tier = v["tier"]
    hard_failures = v.get("hard_failures")

    if evidence_ts == "__derive__":
        evidence_ts = _evidence_ts_for(site)
    age = _evidence_age_days(evidence_ts, now_dt)
    fresh = age is not None and age <= EVIDENCE_FRESH_DAYS

    candidate_action = (candidate or {}).get("action")
    candidate_blocked = bool(candidate_action in PERMANENTLY_INELIGIBLE) if candidate_action else False

    # G2 wire: a change is eligible only if it can be ROLLED BACK. If the candidate names
    # a target_kind with no registered reverser, the change would be irreversible.
    target_kind = (candidate or {}).get("target_kind")
    rb = arb.rollback_capability(target_kind)
    rollback_capable = bool(rb.get("reversible"))

    # G3 wire: a site whose trust has DECAYED below the floor is not eligible until a
    # human restores it — even if its current evidence looks good. Trust only ever
    # decreases automatically.
    trust = atr.effective_trust(site)

    # ── decay dimension: why a site is NOT evidence-qualified ──
    decay_reasons: List[str] = []
    if tier < MIN_ORACLE_TIER:
        decay_reasons.append(f"oracle tier {tier} below required Tier {MIN_ORACLE_TIER}")
    if not fresh:
        if age is None:
            decay_reasons.append("held-out evidence freshness unknown (no designated "
                                 "timestamp) — treated as stale")
        else:
            decay_reasons.append(f"held-out evidence stale ({age:.1f}d > "
                                 f"{EVIDENCE_FRESH_DAYS}d freshness window)")
    if frozen:
        decay_reasons.append("automation is frozen")
    if hard_failures:
        decay_reasons.append("oracle hard failure(s) present")
    if candidate_blocked:
        decay_reasons.append(f"candidate targets a permanently-ineligible action: "
                             f"{candidate_action}")
    if target_kind and not rollback_capable:
        decay_reasons.append(f"no registered reverser for target_kind '{target_kind}' — "
                             f"the change would be irreversible")
    if trust < atr.MIN_TRUST:
        decay_reasons.append(f"trust {trust:.2f} below minimum {atr.MIN_TRUST} — "
                             f"restoring trust is a human governance action")

    evidence_qualified = not decay_reasons

    # ── participation dimension: why a site CANNOT participate (always populated) ──
    reasons: List[str] = list(decay_reasons)
    gate = ao.class_c_site_eligible(site, kind)    # read-only; per-(site, kind)
    if not gate.get("eligible"):
        gr = "; ".join(gate.get("reasons", [])) or "closed"
        reasons.append(f"per-site Class C gate closed: {gr}")
    path_ok = apply_path_exists(kind)
    if not path_ok:
        reasons.append(f"no Class C apply path for kind {kind!r} (no registered reverser)")
    blast = agr.blast_radius_ok(site)              # read-only
    if not blast.get("ok"):
        reasons.append(f"blast-radius: another site has an unreviewed change in flight "
                       f"({blast.get('blocking_sites')})")

    participation_eligible = bool(evidence_qualified and gate.get("eligible")
                                  and path_ok and blast.get("ok"))

    return {
        "site": site,
        "oracle_tier": tier,
        "tier_name": v["tier_name"],
        "evidence_fresh": fresh,
        "evidence_age_days": age,
        "evidence_ts": evidence_ts,
        "candidate_blocked": candidate_blocked,
        "rollback_target_kind": target_kind,
        "rollback_capable": rollback_capable,
        "trust": trust,
        "permanently_ineligible_actions": list(PERMANENTLY_INELIGIBLE),
        "evidence_qualified": evidence_qualified,
        "participation_eligible": participation_eligible,
        "apply_path_exists": path_ok,
        "frozen": frozen,
        "decay_reasons": decay_reasons or None,
        "reasons": reasons or None,
        "_note": "Participation eligibility is EVALUATION ONLY and is False for every "
                 "site in this build (no per-site grant, no Class C apply path, "
                 "Approve-each default). `evidence_qualified` decays to False "
                 "automatically when held-out evidence goes stale — trust only ever "
                 "decreases. Descriptors by name only; read-only.",
    }


def can_participate(site: str, candidate: Optional[Dict[str, Any]] = None,
                    *, kind: str = "live_site_config") -> bool:
    """The single chokepoint any Class C apply path MUST consult before acting. Returns
    `participation_eligible` for the (site, kind). Dark by default (no grant / Class C not
    at auto / no tier-3); the infrastructure exists, the authority is granted per-(site,
    kind) and human-only."""
    return evaluate_site(site, kind=kind, candidate=candidate)["participation_eligible"]


def eligibility_overview(sites: Optional[List[str]] = None, *,
                         now: Any = None) -> Dict[str, Any]:
    """Per-site eligibility rollup. `participation_eligible_sites` is 0 for every build
    here. The evidence-qualified set is CAPPED at MAX_ELIGIBLE_SITES to enforce
    'evaluate only a small number of Tier-3-eligible sites'. Read-only."""
    sites = sites if sites is not None else ao._all_sites()
    rows = [evaluate_site(s, now=now) for s in sites]
    qualified = [r["site"] for r in rows if r["evidence_qualified"]]
    considered = qualified[:MAX_ELIGIBLE_SITES]
    over_cap = qualified[MAX_ELIGIBLE_SITES:]
    return {
        "sites": rows,
        "site_count": len(rows),
        "evidence_qualified_sites": qualified,
        "considered_for_experimentation": considered,
        "over_cap_excluded": over_cap,
        "max_eligible_sites": MAX_ELIGIBLE_SITES,
        "participation_eligible_sites": 0,   # always — no grant, no apply path
        "frozen": ap.is_frozen(),
        "apply_path_exists": apply_path_exists(),
        "permanently_ineligible_actions": list(PERMANENTLY_INELIGIBLE),
        "_note": "Read-only eligibility rollup. No site is participation-eligible "
                 "(assessment + qualification only). The evidence-qualified set is "
                 "capped; qualification decays with evidence staleness. No automation.",
    }


def eligibility_status() -> Dict[str, Any]:
    """Compact status for the cockpit/header. Read-only."""
    ov = eligibility_overview()
    return {
        "class_c_level": ap.load_policy()["levels"].get("C"),
        "class_c_auto_enabled_by_default": False,
        "apply_path_exists": apply_path_exists(),
        "frozen": ov["frozen"],
        "site_count": ov["site_count"],
        "evidence_qualified_count": len(ov["evidence_qualified_sites"]),
        "considered_count": len(ov["considered_for_experimentation"]),
        "participation_eligible_sites": 0,
        "evidence_fresh_days": EVIDENCE_FRESH_DAYS,
        "min_oracle_tier": MIN_ORACLE_TIER,
        "max_eligible_sites": MAX_ELIGIBLE_SITES,
        "permanently_ineligible_actions": list(PERMANENTLY_INELIGIBLE),
        "_note": "Phase G Eligibility Governance: evaluates which Tier-3 sites are "
                 "evidence-qualified to be CONSIDERED for human-approved Class C "
                 "experimentation, with automatic decay on stale evidence. Enables no "
                 "automation; participation eligibility is False for every site "
                 "(Approve-each default, empty per-site grant, no apply path). "
                 "Read-only.",
    }
