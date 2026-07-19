"""perturbation_harness.py — perturbation-capture harness (v3.66.88).

The fragility map (`capture_workbench._FRAGILITY`) predicts, for each assumption
kind, how that assumption responds to perturbation along four axes. Two of those
columns — `player_config` and `workflow` — have never been exercised, because every
capture to date varied only title and session. VC-0017 and VC-0018 record exactly
that: the predictions are hand-authored and untested.

This harness is the infrastructure to test them. Given a BASELINE capture and a
capture PERTURBED along one axis, it re-derives the goal skeleton for each and
checks, per fragility kind, whether the observed change matches the predicted
response (neutral / may_invalidate / likely_invalidates).

THE CRITICAL BOUNDARY, ENFORCED IN CODE: a SYNTHETIC perturbation can validate the
harness MACHINERY — that it re-derives, detects per-kind change, and maps to the
prediction — but it can NEVER retire validation debt. A synthetic perturbation is a
"what-if" the harness itself fabricated; confirming the harness notices its own
fabrication says nothing about how a REAL alternate player or workflow behaves. So
whenever evidence == "synthetic", the harness forces resolves_debt = False and
affects_confidence / affects_sensitivity / affects_corpus_conclusion = False. Only a
real perturbation-varied capture (evidence == "real") can move those.

Recognition-only. Builds on goal_skeleton and the temporal harness's goal
extraction; not imported by app.py.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Tuple

from .capture_workbench import goal_skeleton, _FRAGILITY, IDENTITY, RENDITION
from .temporal_harness import _goal_url

PERTURBATION_HARNESS_VERSION = "3.66.88"

PERTURBATION_AXES = ("player_config", "workflow")

# the fragility kinds with a structurally observable facet in the goal skeleton;
# other kinds (n2_floor, src_unknown) carry an axis prediction but no facet that a
# single perturbed capture exposes, so they are reported prediction-only
_OBSERVABLE_KINDS = ("goal_selection", "skeleton_identity",
                     "skeleton_rendition", "title_invariant")

# which kinds feed the downstream layers — used ONLY to describe what REAL
# evidence on this axis would touch; synthetic evidence touches nothing
_FEEDS_CONFIDENCE = {"goal_selection", "skeleton_identity", "skeleton_rendition",
                     "title_invariant", "n2_floor"}
_FEEDS_SENSITIVITY = {"goal_selection", "skeleton_identity", "skeleton_rendition"}

_RES = re.compile(r"\d{2,5}[xX]\d{2,5}|\b\d{3,4}[pi]\b")


def _facets(goal_url: str) -> Dict[str, Any]:
    """Per-kind structural facets of one capture's goal."""
    sk = goal_skeleton({"requests": [{"goal": True, "url_template": goal_url}]})
    return {
        "goal_selection": sk["path_template"],          # which goal was selected
        "skeleton_identity": tuple(s["sample"] for s in sk["skeleton_slots"]
                                   if s["role"] == IDENTITY),
        "skeleton_rendition": tuple(s["sample"] for s in sk["skeleton_slots"]
                                    if s["role"] == RENDITION),
        "title_invariant": (sk["host"], _shape(sk["path_template"])),
    }


def _shape(path_template: str) -> str:
    """Structural shape: literals collapsed, slots kept — so a rendition value
    change does not count as a structural change, but a path-shape change does."""
    return re.sub(r"\{[^}]+\}", "{}", path_template)


def _outcome(predicted: Optional[str], changed: bool) -> str:
    if predicted == "neutral":
        return ("consistent: predicted no change, none observed" if not changed
                else "TENSION: predicted neutral but the facet changed")
    if predicted == "likely_invalidates":
        return ("consistent: predicted invalidation, change observed" if changed
                else "TENSION: predicted likely-invalidate but the facet was stable")
    if predicted == "may_invalidate":
        return ("consistent: 'may_invalidate' admits a change" if changed
                else "consistent: 'may_invalidate' admits no change (not falsifiable "
                     "from one capture)")
    return "n/a"


def perturbation_run(baseline: Dict[str, Any], perturbed: Dict[str, Any],
                     axis: str, *, evidence: str,
                     change_manifest: Optional[Dict[str, Any]] = None
                     ) -> Dict[str, Any]:
    """Check the fragility predictions for one perturbation axis.

    `evidence` is "synthetic" (a fabricated perturbation, validates machinery only)
    or "real" (a genuine perturbation-varied capture). The synthetic/real flag is
    the load-bearing input: it decides whether anything here can move debt,
    confidence, sensitivity, or a corpus conclusion.
    """
    if axis not in PERTURBATION_AXES:
        return {"error": f"unknown axis {axis!r}; expected {PERTURBATION_AXES}"}
    if evidence not in ("synthetic", "real"):
        return {"error": "evidence must be 'synthetic' or 'real'"}

    b_goal, p_goal = _goal_url(baseline), _goal_url(perturbed)
    if b_goal is None or p_goal is None:
        return {"error": "baseline or perturbed capture has no media goal"}
    b_fac, p_fac = _facets(b_goal), _facets(p_goal)

    is_synthetic = evidence == "synthetic"
    per_kind: List[Dict[str, Any]] = []
    for kind in _FRAGILITY:
        predicted = _FRAGILITY[kind]["perturbation"].get(axis)
        if kind in _OBSERVABLE_KINDS:
            changed = b_fac[kind] != p_fac[kind]
            observed = "changed" if changed else "unchanged"
            outcome = _outcome(predicted, changed)
        else:
            observed = "not structurally observable from one capture"
            outcome = "prediction recorded; not exercised by this facet set"
        per_kind.append({
            "kind": kind,
            "prediction": predicted,
            "observed": observed,
            "outcome": outcome,
            # what REAL evidence on this kind would touch (informational only)
            "would_feed_confidence": kind in _FEEDS_CONFIDENCE,
            "would_feed_sensitivity": kind in _FEEDS_SENSITIVITY,
        })

    return {
        "axis": axis,
        "evidence": evidence,
        # ── the enforced boundary ──────────────────────────────────────
        "resolves_debt": False if is_synthetic else None,   # None = awaiting a real
        "affects_confidence": False if is_synthetic else None,  #       decision when
        "affects_sensitivity": False if is_synthetic else None,  #      real evidence
        "affects_corpus_conclusion": False if is_synthetic else None,  # arrives
        "per_kind": per_kind,
        "change_manifest": change_manifest,
        "note": ("SYNTHETIC: this validates that the harness re-derives, detects "
                 "per-kind change, and maps to the prediction. It is a fabricated "
                 "perturbation and updates nothing — not debt, confidence, "
                 "sensitivity, or any corpus conclusion. Only a real "
                 "perturbation-varied capture can." if is_synthetic else
                 "REAL: a genuine perturbation-varied capture; outcomes may confirm "
                 "or falsify the fragility predictions and update the corpus."),
    }


# ── synthetic perturbation generators (validate machinery ONLY) ─────
def _swap_rendition(goal_url: str) -> str:
    """Return the goal URL with its resolution token changed (a different player
    default). If no resolution token, insert one before the extension."""
    if _RES.search(goal_url):
        return _RES.sub("1280x720", goal_url, count=1)
    return re.sub(r"(\.[A-Za-z0-9]{2,5})(\?|$)", r"_720p\1\2", goal_url, count=1)


def _replace_goal_in_log(cap: Dict[str, Any], old: str, new: str) -> None:
    for e in (cap.get("network_log") or cap.get("requests") or []):
        if isinstance(e, dict) and (e.get("url") or "") == old:
            e["url"] = new
            return


def synthesize_player_config_perturbation(capture: Dict[str, Any]
                                          ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """SYNTHETIC. Simulate a different player by changing the goal media's selected
    rendition. Validates harness machinery; NOT a real player-config capture."""
    cap = copy.deepcopy(capture)
    goal = _goal_url(cap)
    new_goal = _swap_rendition(goal) if goal else goal
    if goal and new_goal != goal:
        _replace_goal_in_log(cap, goal, new_goal)
    return cap, {
        "axis": "player_config", "synthetic": True,
        "change": "goal resolution token swapped (simulated alternate player "
                  "default rendition); query untouched",
    }


def synthesize_workflow_perturbation(capture: Dict[str, Any]
                                     ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """SYNTHETIC. Simulate a different workflow by appending a higher-seq media
    request with a different path shape (a different navigation fetching an extra
    asset last). Validates harness machinery; NOT a real workflow capture."""
    cap = copy.deepcopy(capture)
    log = cap.get("network_log")
    if log is None:
        log = cap.setdefault("network_log", [])
    goal = _goal_url(cap) or "https://example.invalid/v/x_720p.mp4"
    from urllib.parse import urlsplit
    sp = urlsplit(goal.partition("?")[0])
    maxseq = max((e.get("seq", 0) for e in log if isinstance(e, dict)), default=0)
    # a different path SHAPE: a download-endpoint reveal a different workflow uses
    new_url = f"{sp.scheme}://{sp.netloc}/download/reveal/clip_720p.mp4"
    log.append({"seq": maxseq + 1, "url": new_url})
    return cap, {
        "axis": "workflow", "synthetic": True,
        "change": "appended a higher-seq media request with a different path shape "
                  "(simulated alternate workflow reveal endpoint)",
    }


def validate_machinery(capture: Dict[str, Any]) -> Dict[str, Any]:
    """Run both synthetic perturbations against a baseline capture and return the
    per-axis reports. This proves the harness works; it retires no debt."""
    p_cap, p_man = synthesize_player_config_perturbation(capture)
    w_cap, w_man = synthesize_workflow_perturbation(capture)
    return {
        "harness_version": PERTURBATION_HARNESS_VERSION,
        "evidence": "synthetic",
        "machinery_validated": True,
        "retires_debt": False,
        "axes": {
            "player_config": perturbation_run(capture, p_cap, "player_config",
                                              evidence="synthetic",
                                              change_manifest=p_man),
            "workflow": perturbation_run(capture, w_cap, "workflow",
                                        evidence="synthetic",
                                        change_manifest=w_man),
        },
        "note": ("Machinery validation only. VC-0017 (player_config) and VC-0018 "
                 "(workflow) remain UNTESTED; they can be retired only by real "
                 "perturbation-varied captures, which do not yet exist."),
    }
