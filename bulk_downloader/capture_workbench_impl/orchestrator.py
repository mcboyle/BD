"""capture_workbench_impl.orchestrator -- build_workbench orchestrator (verbatim)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ._common import (
    CLIENT_COMPUTED,
    DetectorDraft,
    INVARIANT,
    PROVENANCE,
    ROTATING_OPAQUE,
    SIGNING,
    Slot,
    _AFFECTS,
    _CONF_BY_VERDICT,
    _ID_SHAPES,
    _RATIONALE_BY_VERDICT,
    _derive_pattern,
)
from .skeleton import _value_for_pattern, _verdict_for_param, goal_skeleton
from .analysis import (
    _assumption_stability,
    _blast_radius,
    _bodies_retained,
    _change_plan,
    _contradictions,
    _decision_confidence,
    _generalization,
    _impact,
    _request_key_of,
    _sensitivity,
    _slot_evidence,
    _slot_strengthen,
    _uncertainty_flow,
    _uncertainty_plan,
)


def build_workbench(synth: Dict[str, Any], *,
                    captures: Optional[Any] = None) -> DetectorDraft:
    """Turn a ``capture_synth.synthesize`` result into a reviewable
    :class:`DetectorDraft`. Pure function of the synth dict — no capture
    replay, no network, no rule-table writes."""
    host = synth.get("host") or ""
    draft = DetectorDraft(
        host=host,
        entry_url=synth.get("entry_url"),
        confidence=synth.get("confidence", "low"),
        goal_request_key=None,
    )

    goal_req = None
    for r in synth.get("requests", []):
        if r.get("goal"):
            goal_req = r
            draft.goal_request_key = r.get("key")

    for r in synth.get("requests", []):
        rkey = r.get("key") or _request_key_of(r)
        for p in r.get("params", []):
            name = p.get("key") or p.get("param") or ""
            in_path = bool(p.get("in_path"))
            shape = p.get("type") or "opaque"
            # Invariant params aren't in synth's per-request params list (that
            # list is the varying/credential slots); treat everything we see
            # here as a varying or credential slot.
            verdict = _verdict_for_param(p)
            slot = Slot(
                request_key=rkey, param=name, in_path=in_path, shape=shape,
                verdict=verdict,
                confidence=_CONF_BY_VERDICT.get(verdict, "low"),
                known=(verdict in (SIGNING, PROVENANCE, INVARIANT)),
                rationale=_RATIONALE_BY_VERDICT.get(verdict, ""),
                provenance=(p.get("source")
                            if p.get("source") not in
                            (None, "source_unknown", "redacted_credential")
                            else None),
                evidence=_slot_evidence(p, verdict),
                strengthen=_slot_strengthen(p, verdict),
                affects=_AFFECTS.get(verdict),
            )
            draft.slots.append(slot)

            if verdict == SIGNING:
                draft.opaque_slots.append({
                    "request": rkey, "param": name, "shape": shape,
                    "reason": "signing/credential — supplied live, not "
                              "reconstructed"})
            elif verdict == PROVENANCE:
                draft.provenance_edges.append({
                    "request": rkey, "param": name,
                    "from_source": p.get("source"),
                    "confidence": slot.confidence,
                    "note": "carry the value from this source at detect time; "
                            "the edge survives value rotation"})
                # A provenance-linked value that is *addressable* — it sits in
                # the URL path, or has a content-identifier shape — also gets a
                # candidate extraction pattern, so the operator gets both the
                # edge (where it comes from) and a draft regex (how to pull
                # it). Opaque values that aren't path-addressable get the edge
                # only — a regex for them would be guesswork. Signing material
                # never reaches this branch (it's SIGNING above).
                if in_path or shape in _ID_SHAPES:
                    val = _value_for_pattern(p.get("value_a")) \
                          or _value_for_pattern(p.get("value_b"))
                    if val is not None:
                        draft.draft_patterns.append(
                            _derive_pattern(name, val, in_path))
            elif verdict in (CLIENT_COMPUTED, ROTATING_OPAQUE):
                draft.unrecoverable.append({
                    "request": rkey, "param": name, "shape": shape,
                    "verdict": verdict,
                    "reason": _RATIONALE_BY_VERDICT[verdict]})

    # Surface synth's own unresolved set as additional unrecoverable items,
    # de-duplicated against what we already flagged.
    seen = {(u["request"], u["param"]) for u in draft.unrecoverable}
    for u in synth.get("unresolved", []):
        keyp = (u.get("request"), u.get("param"))
        if keyp not in seen:
            draft.unrecoverable.append({
                "request": u.get("request"), "param": u.get("param"),
                "shape": "unknown", "verdict": CLIENT_COMPUTED,
                "reason": u.get("reason", "source-unknown")})
            seen.add(keyp)

    bodies_state = _bodies_retained(captures)
    draft.skeleton = goal_skeleton(synth)
    draft.impact = _impact(synth, draft.skeleton, bodies_state)
    draft.change_plan = _change_plan(draft, bodies_state)
    draft.uncertainty = _uncertainty_plan(draft, bodies_state)
    draft.uncertainty_flow = _uncertainty_flow(draft)
    _ncap = len(captures) if captures else 2
    draft.assumption_stability = _assumption_stability(
        draft, draft.uncertainty_flow, _ncap)
    draft.blast_radius = _blast_radius(draft)
    draft.generalization = _generalization(draft)
    draft.contradictions = _contradictions(draft, bodies_state)
    # v3.66.70: weakest-link decision confidence over the dependency graph +
    # stability profiles. Runs last — it reads the flow and stability layers.
    draft.decision_confidence = _decision_confidence(
        draft, draft.uncertainty_flow, draft.assumption_stability or {})
    # v3.66.71: sensitivity analysis — robustness of the graph-derived rankings
    # under the approved weight/fragility perturbation sweep. Runs last; reads
    # the flow + fragility structures, mutates nothing.
    draft.sensitivity = _sensitivity(draft)

    # Notes: carry synth's structural caveats forward, then add the workbench's
    # own scope statement so the draft can never be mistaken for a finished,
    # auto-installable rule.
    draft.notes.extend(synth.get("notes", []))
    draft.notes.append(
        "Draft only: review and harden before adding to deep_detect. "
        "Patterns target STABLE identifiers; signing slots are opaque and "
        "left for the live session. Selector chains are not emitted (network "
        "synth carries no DOM context).")
    if goal_req is None:
        draft.notes.append(
            "No goal media request identified — the workflow may not end in a "
            "direct media URL, or the captures didn't reach the download.")
    return draft
