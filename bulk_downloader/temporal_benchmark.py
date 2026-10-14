"""temporal_benchmark.py — measure whether the drift taxonomy and the
introspection conclusions stay MEANINGFUL across time, not just whether a
template still matches.

The second-title test (v3.66.69-71) validated the model across TITLES. This
module is the harness for validating it across DATES: same title, same workflow,
captured later. It is recognition-only — it reads structure, classifies drift,
and compares two workbenches; it never reads or reconstructs signed values.

Two questions it answers once a later same-title capture exists:

  1. DRIFT TAXONOMY (needs template + later capture only).
     Does each kind of real-world change land in the verdict bucket the
     taxonomy predicts? `classify_churn` maps the v3.66.69 five-way diff verdict
     (plus a cross-host probe) onto the operator's named churn categories:
       signing_churn   — signing markers changed; goal shape + slots hold.
       rendition_churn — RENDITION_DRIFT: same family, recorded member gone.
       cdn_churn       — the goal host moved, but a same-SHAPE request exists on
                         a different host (re-hosted, not broken).
       structural_drift— host present, templated shape changed.
       breakage        — goal host absent AND no same-shape request anywhere.
       (identity_change is reported but is NOT drift — a title change is
        expected; for a same-title temporal run it should NOT appear, and if it
        does it is itself a finding.)

  2. SECOND-ORDER STABILITY (needs the original pair + the later capture).
     Re-run the workbench on the later capture and compare to the before
     workbench. `build_transition` reports, per assumption, one of
     ASSUMPTION_TRANSITIONS, and whether the confidence and sensitivity
     conclusions themselves held (a contingent ordering became robust, a
     decision's weakest-link band moved, a decision or ordering appeared or
     disappeared).

     The workbench assigns an assumption's stability band and basis from a fixed
     table keyed by the assumption's KIND (capture_workbench_impl/_common.py
     _FRAGILITY/_STAB), not from what a capture observed. So on two workbenches
     built by the same code a shared assumption is always STABLE; across real
     captures the outcomes are STABLE, APPEARED (only in the later workbench) and
     DISAPPEARED (only in the before one). STRENGTHENED / WEAKENED /
     CHANGED_CATEGORY are reported only when the two inputs carry a different
     band or basis for the same assumption (e.g. a stored --baseline-json written
     by an older workbench); UNKNOWN when a band is not one of _BAND_ORDER.

POSTURE (load-bearing): recognition-only. No HTTP, no stream assembly, no value
synthesis. Signing is reported by marker NAME only; every echoed URL is
query-stripped. Reads `network_log` URLs for path SHAPE only.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from .capture_synth import synthesize
from .capture_workbench import build_workbench
from .capture_template import (
    migrate_template, diff_template, _segments, _parse_template_path, _safe_url,
    HELD, RENDITION_DRIFT, IDENTITY_CHANGE, IDENTITY_AND_RENDITION_CHANGE,
    STRUCTURAL_DRIFT, MISSING)

# Churn categories (the operator's temporal taxonomy).
SIGNING_CHURN = "signing_churn"
RENDITION_CHURN = "rendition_churn"
CDN_CHURN = "cdn_churn"
STRUCTURAL_DRIFT_CHURN = "structural_drift"
BREAKAGE = "breakage"
NO_CHURN = "no_churn"
IDENTITY_CHANGED = "identity_change_unexpected"  # a title change in a same-title run
# a diff verdict this taxonomy does not know (absent, None, a new constant):
# never read as "all held"
UNCLASSIFIED = "unclassified_verdict"
_KNOWN_VERDICTS = (HELD, RENDITION_DRIFT, IDENTITY_CHANGE,
                   IDENTITY_AND_RENDITION_CHANGE, STRUCTURAL_DRIFT, MISSING)

# Every per-assumption transition build_transition can report; each is a
# summary key (0 when none).
ASSUMPTION_TRANSITIONS = ("stable", "strengthened", "weakened",
                          "changed_category", "appeared", "disappeared",
                          "unknown")

# Stability band ordinal — shared with the confidence layer's notion of order.
_BAND_ORDER = {"low": 0, "low-medium": 1, "medium": 2, "high": 3}


# ── 1) churn classification ────────────────────────────────────────
def _same_shape_on_other_host(template: Dict[str, Any],
                              capture: Dict[str, Any]) -> Optional[str]:
    """If the goal host is gone, look for a request with the SAME path shape
    (segment count, literal segments, and each slot's regex — the same shape
    test the goal matcher applies on the goal host) on a DIFFERENT host — that
    is CDN churn (re-hosted), not breakage. A template with no path segments
    has no shape to find elsewhere. Returns the other host, or None. Query is
    stripped; no signing is read."""
    goal = template.get("goal") or {}
    t_host, t_segs = _parse_template_path(goal.get("path_template") or "")
    if not t_segs:
        return None
    slot_re = {s.get("name"): s.get("regex")
               for s in goal.get("slots") or [] if isinstance(s, dict)}
    for e in capture.get("network_log") or []:
        if not isinstance(e, dict):
            continue
        host, segs = _segments(e.get("url") or "")
        if not host or host == t_host or len(segs) != len(t_segs):
            continue
        ok = True
        for (kind, val), observed in zip(t_segs, segs):
            if kind == "lit":
                ok = observed == val
            else:  # slot: non-empty and matches the slot regex if known
                rx = slot_re.get(val)
                ok = bool(observed) and not (rx and not re.fullmatch(rx, observed))
            if not ok:
                break
        if ok:
            return host
    return None


def classify_churn(template: Dict[str, Any],
                   diff_result: Dict[str, Any],
                   capture: Dict[str, Any]) -> Dict[str, Any]:
    """Map the v3.66.69 diff verdict + a cross-host probe onto the temporal
    churn taxonomy. Pure function of the diff result + capture structure."""
    template = migrate_template(template)
    verdict = diff_result.get("verdict")
    checks = {c.get("prediction"): c for c in diff_result.get("checks") or []
              if isinstance(c, dict)}
    signing = checks.get("signing", {})
    signing_drifted = signing.get("status") and signing["status"] != HELD

    categories: List[str] = []
    notes: List[str] = []

    if verdict == MISSING:
        other = _same_shape_on_other_host(template, capture)
        if other:
            categories.append(CDN_CHURN)
            notes.append(f"goal host absent, but the same path shape appears on "
                         f"'{other}' — re-hosted (CDN churn), not broken.")
        else:
            categories.append(BREAKAGE)
            notes.append("goal host absent and no same-shape request on any "
                         "other host — the goal is no longer reachable.")
    elif verdict == STRUCTURAL_DRIFT:
        categories.append(STRUCTURAL_DRIFT_CHURN)
        notes.append("goal host present but the templated URL shape changed "
                     "(segment count / literals / slot shape).")
    elif verdict in (RENDITION_DRIFT, IDENTITY_AND_RENDITION_CHANGE):
        categories.append(RENDITION_CHURN)
        notes.append("the goal family still matches but the recorded rendition "
                     "member is no longer served.")
        if verdict == IDENTITY_AND_RENDITION_CHANGE:
            categories.append(IDENTITY_CHANGED)
            notes.append("the content identity ALSO changed — unexpected in a "
                         "same-title temporal run; verify the capture is the "
                         "same title.")
    elif verdict == IDENTITY_CHANGE:
        categories.append(IDENTITY_CHANGED)
        notes.append("the content identity changed — unexpected in a same-title "
                     "temporal run; verify the capture is the same title.")
    elif verdict not in _KNOWN_VERDICTS:
        categories.append(UNCLASSIFIED)
        notes.append(f"diff verdict {verdict!r} is not one the churn taxonomy "
                     f"knows — not classified, and not read as held.")

    # signing churn is orthogonal: it can co-occur with a held goal shape, and
    # for a healthy temporal capture it is the EXPECTED change (tokens rotate).
    if signing_drifted:
        categories.append(SIGNING_CHURN)
        absent = signing.get("observed_absent", [])
        new = signing.get("observed_new", [])
        bits = []
        if absent:
            bits.append(f"expected marker(s) absent: {', '.join(absent)}")
        if new:
            bits.append(f"new signing-like marker(s): {', '.join(new)}")
        notes.append("signing markers changed (" + "; ".join(bits) +
                     ") — names only, values never read.")

    if not categories:
        categories.append(NO_CHURN)
        notes.append("goal shape, classification, slots, and signing markers "
                     "all held — the template still recognizes the goal.")

    # is this drift expected (signing rotation only) or a real problem?
    real_drift = any(c in (RENDITION_CHURN, CDN_CHURN, STRUCTURAL_DRIFT_CHURN,
                           BREAKAGE, IDENTITY_CHANGED, UNCLASSIFIED)
                     for c in categories)
    return {
        "verdict": verdict,
        "churn_categories": categories,
        "expected_only": (categories == [SIGNING_CHURN]
                          or categories == [NO_CHURN]),
        "real_drift": real_drift,
        "notes": notes,
    }


# ── 2) before/later transition over the introspection layers ───────
def _band_delta(before: Optional[str], later: Optional[str]) -> str:
    b, l = _BAND_ORDER.get(before), _BAND_ORDER.get(later)
    if b is None or l is None:
        return "unknown"
    if l > b:
        return "strengthened"
    if l < b:
        return "weakened"
    return "stable"


def _index_assumptions(draft_dict: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    stab = (draft_dict.get("assumption_stability") or {})
    return {a["node"]: a for a in stab.get("assumptions", [])}


def _index_confidence(draft_dict: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    dc = (draft_dict.get("decision_confidence") or {})
    out = {}
    for i, d in enumerate(dc.get("decisions", [])):
        # a decision naming neither is still reported, under its position
        key = d.get("category") or d.get("decision") or f"unnamed#{i}"
        out[key] = d
    return out


def _index_sensitivity(draft_dict: Dict[str, Any]) -> Dict[str, bool]:
    sens = (draft_dict.get("sensitivity") or {})
    out = {}
    for r in (sens.get("robustness", {}) or {}).get("by_downstream_weight", []):
        out[r["assumption"]] = bool(r.get("robust"))
    return out


def build_transition(before: Dict[str, Any],
                     later: Dict[str, Any]) -> Dict[str, Any]:
    """Compare two workbench to_dict() outputs (before vs later) and report how
    each assumption and each introspection conclusion moved over time."""
    b_assume, l_assume = _index_assumptions(before), _index_assumptions(later)
    assumptions: List[Dict[str, Any]] = []
    for node in sorted(set(b_assume) | set(l_assume)):
        b, l = b_assume.get(node), l_assume.get(node)
        if b and l:
            band_move = _band_delta(b.get("stability_band"),
                                    l.get("stability_band"))
            basis_changed = b.get("basis") != l.get("basis")
            transition = ("changed_category" if basis_changed else band_move)
            assumptions.append({
                "assumption": node,
                "transition": transition,
                "before": {"band": b.get("stability_band"),
                           "basis": b.get("basis"),
                           "downstream_weight": b.get("downstream_weight")},
                "later": {"band": l.get("stability_band"),
                          "basis": l.get("basis"),
                          "downstream_weight": l.get("downstream_weight")},
            })
        elif l and not b:
            assumptions.append({"assumption": node, "transition": "appeared",
                                "later": {"band": l.get("stability_band"),
                                          "basis": l.get("basis")}})
        else:
            assumptions.append({"assumption": node, "transition": "disappeared",
                                "before": {"band": b.get("stability_band"),
                                           "basis": b.get("basis")}})

    # confidence: did each decision's weakest-link band move?
    b_conf, l_conf = _index_confidence(before), _index_confidence(later)
    confidence: List[Dict[str, Any]] = []
    for key in sorted(set(b_conf) | set(l_conf)):
        b, l = b_conf.get(key), l_conf.get(key)
        # a decision on one side only is reported, not dropped
        confidence.append({
            "decision": key,
            "transition": (_band_delta(b.get("confidence"), l.get("confidence"))
                           if b and l else "appeared" if l else "disappeared"),
            "before_confidence": b.get("confidence") if b else None,
            "later_confidence": l.get("confidence") if l else None,
            "before_capped_by": b.get("capped_by") if b else None,
            "later_capped_by": l.get("capped_by") if l else None,
        })

    # sensitivity: did a contingent ordering become robust (or vice versa)?
    b_sens, l_sens = _index_sensitivity(before), _index_sensitivity(later)
    sensitivity: List[Dict[str, Any]] = []
    for node in sorted(set(b_sens) | set(l_sens)):
        bw, lw = b_sens.get(node), l_sens.get(node)
        if bw is None:
            t = "appeared"  # an ordering only the later workbench ranks
        elif lw is None:
            t = "disappeared"
        elif bw and lw:
            t = "stayed_robust"
        elif (not bw) and (not lw):
            t = "stayed_contingent"
        elif lw and not bw:
            t = "became_robust"
        else:
            t = "became_contingent"
        sensitivity.append({"assumption": node, "transition": t,
                            "before_robust": bw, "later_robust": lw})

    summary = {t: sum(1 for a in assumptions if a["transition"] == t)
               for t in ASSUMPTION_TRANSITIONS}
    return {
        "assumptions": assumptions,
        "confidence": confidence,
        "sensitivity": sensitivity,
        "summary": {
            **summary,
            "became_robust": sum(1 for s in sensitivity
                                 if s["transition"] == "became_robust"),
            "became_contingent": sum(1 for s in sensitivity
                                     if s["transition"] == "became_contingent"),
        },
        "note": ("transition over time: bands and basis are fixed per "
                 "assumption kind by the workbench, so between workbenches of "
                 "the same code a shared assumption is STABLE and the movement "
                 "is in which assumptions APPEARED or DISAPPEARED; STRENGTHENED/"
                 "WEAKENED/CHANGED_CATEGORY appear only when the inputs carry "
                 "different bands or basis for one assumption (e.g. an older "
                 "stored baseline). became_robust means a previously "
                 "weight-contingent ordering held under perturbation in the "
                 "later capture — the strongest longitudinal signal."),
    }


# ── orchestration ──────────────────────────────────────────────────
def temporal_run(template: Dict[str, Any],
                 later_capture: Dict[str, Any],
                 *,
                 baseline_pair: Optional[Tuple[Dict, Dict]] = None,
                 baseline_json: Optional[Dict[str, Any]] = None,
                 ) -> Dict[str, Any]:
    """Run the temporal benchmark.

    Always produces the diff (template vs later) + churn classification. If a
    baseline is available — either the original capture PAIR (to build the
    before workbench) or a stored baseline workbench JSON — AND the original
    pair is available to synth a later workbench, also produces the second-order
    transition report. Degrades gracefully and names what is missing.
    """
    if baseline_pair is not None and len(baseline_pair) != 2:
        raise ValueError("baseline_pair must be the original (capture_a, "
                         f"capture_b) pair, got {len(baseline_pair)} item(s)")
    template = migrate_template(template)
    diff = diff_template(template, later_capture)
    churn = classify_churn(template, diff, later_capture)

    out: Dict[str, Any] = {
        "template_host": template.get("host"),
        "diff": diff,
        "churn": churn,
        "transition": None,
        "transition_status": None,
    }

    before_dict = baseline_json
    if before_dict is None and baseline_pair is not None:
        a, b = baseline_pair
        before_dict = build_workbench(synthesize(a, b), captures=(a, b)).to_dict()

    if before_dict is None:
        out["transition_status"] = (
            "second-order comparison PENDING: provide the original same-title "
            "capture pair (--baseline-pair A B) or a stored baseline workbench "
            "(--baseline-json) to compare confidence/sensitivity/stability "
            "before vs later. The diff + churn classification above need no "
            "baseline and are complete.")
        return out

    # build the LATER workbench: pair an original with the later capture (same
    # title, time-separated) so the synth reflects what changed over time.
    if baseline_pair is not None:
        orig_a = baseline_pair[0]
        later_dict = build_workbench(
            synthesize(orig_a, later_capture),
            captures=(orig_a, later_capture)).to_dict()
        out["transition"] = build_transition(before_dict, later_dict)
        out["transition_status"] = "complete"
    else:
        out["transition_status"] = (
            "second-order comparison PARTIAL: a baseline workbench was provided "
            "but building the LATER workbench needs an original capture to pair "
            "with the later one (--baseline-pair). Diff + churn are complete.")
    return out


def snapshot_baseline(cap_a: Dict[str, Any],
                      cap_b: Dict[str, Any]) -> Dict[str, Any]:
    """Build the before workbench from the original same-title pair and return
    its to_dict() — store this alongside the template so a future temporal run
    has a baseline without re-supplying the original captures. Recognition-only;
    the workbench output contains no signing values."""
    return build_workbench(synthesize(cap_a, cap_b),
                           captures=(cap_a, cap_b)).to_dict()
