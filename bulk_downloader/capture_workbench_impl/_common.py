"""capture_workbench_impl._common -- shape consts, plan codes, dataclasses, and the
SOLE extraction_core/capture_synth/netlog_classify import site (the routed-producer
entry the importer-set guard tracks). Sink: imports nothing intra-package. Verbatim;
do not reformat (emitted rationale strings carry intentional Unicode)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from ..capture_synth import classify_value
from ..netlog_classify import _SIGN_MARKER
from ..extraction_core import (
    IDENTITY,
    RENDITION,
    DraftPattern,
    segment_role as _segment_role,
    is_addressable as _segment_is_addressable,
    segment_regex as _segment_regex,
    derive_pattern as _derive_pattern,
)


WORKBENCH_VERSION = 1


SIGNING = "signing"


PROVENANCE = "provenance_linked"


STABLE_ID = "stable_id"


CLIENT_COMPUTED = "client_computed_suspected"


ROTATING_OPAQUE = "rotating_opaque"


INVARIANT = "invariant"


_ID_SHAPES = ("uuid", "sha256", "md5", "id", "filename")


_SIGNING_SHAPES = ("jwt",)


@dataclass
class Slot:
    """One typed, stability-classified parameter slot."""
    request_key: str
    param: str
    in_path: bool
    shape: str                     # classify_value label
    verdict: str                   # one of the verdicts above
    confidence: str                # high | medium | low
    known: bool                    # True = directly observed; False = inferred/heuristic
    rationale: str
    provenance: Optional[str] = None  # the synth source label, when relevant
    evidence: List[str] = field(default_factory=list)   # concrete observations behind the verdict
    strengthen: Optional[str] = None  # what extra capture would raise confidence
    affects: Optional[str] = None     # which detector component this maps to


@dataclass
class DetectorDraft:
    host: str
    entry_url: Optional[str]
    confidence: str                       # overall (inherits synth's N-driven floor)
    goal_request_key: Optional[str]
    slots: List[Slot] = field(default_factory=list)
    provenance_edges: List[Dict[str, Any]] = field(default_factory=list)
    draft_patterns: List[DraftPattern] = field(default_factory=list)
    opaque_slots: List[Dict[str, Any]] = field(default_factory=list)  # signing/unrecoverable, by design
    unrecoverable: List[Dict[str, Any]] = field(default_factory=list)  # client-computed/rotating-opaque
    skeleton: Optional[Dict[str, Any]] = None  # goal URL template + addressable path segments
    impact: Optional[Dict[str, Any]] = None    # which detector components are likely affected
    change_plan: List[Dict[str, Any]] = field(default_factory=list)  # prioritized maintainer triage
    uncertainty: Optional[Dict[str, Any]] = None  # ranked evidence-collection leverage
    uncertainty_flow: Optional[Dict[str, Any]] = None  # dependency graph of conclusions
    assumption_stability: Optional[Dict[str, Any]] = None  # per-assumption trust + verify-first
    blast_radius: Optional[Dict[str, Any]] = None  # cost-of-being-wrong per assumption
    generalization: Optional[Dict[str, Any]] = None  # framework-level vs site-specific
    contradictions: List[Dict[str, Any]] = field(default_factory=list)  # cross-layer consistency pass
    decision_confidence: Optional[Dict[str, Any]] = None  # weakest-link confidence + trace per decision
    sensitivity: Optional[Dict[str, Any]] = None  # robustness of rankings under weight/fragility perturbation
    notes: List[str] = field(default_factory=list)
    workbench_version: int = WORKBENCH_VERSION

    def to_dict(self) -> Dict[str, Any]:
        def _slot(s: Slot) -> Dict[str, Any]:
            d = {"request": s.request_key, "param": s.param,
                 "in_path": s.in_path, "shape": s.shape, "verdict": s.verdict,
                 "confidence": s.confidence,
                 "basis": "observed" if s.known else "inferred",
                 "rationale": s.rationale}
            if s.provenance:
                d["provenance"] = s.provenance
            if s.evidence:
                d["evidence"] = list(s.evidence)
            if s.strengthen:
                d["strengthen"] = s.strengthen
            if s.affects:
                d["affects"] = s.affects
            return d

        return {
            "workbench_version": self.workbench_version,
            "host": self.host,
            "entry_url": self.entry_url,
            "confidence": self.confidence,
            "goal_request": self.goal_request_key,
            "slots": [_slot(s) for s in self.slots],
            "provenance_edges": list(self.provenance_edges),
            "draft_patterns": [vars(p) for p in self.draft_patterns],
            "opaque_slots": list(self.opaque_slots),
            "unrecoverable": list(self.unrecoverable),
            "skeleton": self.skeleton,
            "impact": self.impact,
            "change_plan": list(self.change_plan),
            "uncertainty": self.uncertainty,
            "uncertainty_flow": self.uncertainty_flow,
            "assumption_stability": self.assumption_stability,
            "blast_radius": self.blast_radius,
            "generalization": self.generalization,
            "contradictions": list(self.contradictions),
            "decision_confidence": self.decision_confidence,
            "sensitivity": self.sensitivity,
            "posture": (
                "Recognition only. Draft patterns extract stable identifiers; "
                "signing material is left opaque and is fetched by the live "
                "authenticated session, never reconstructed or computed. No "
                "replay, no synthesis."),
            "notes": list(self.notes),
        }


_CONF_BY_VERDICT = {
    SIGNING: "high",          # high confidence it IS a credential
    PROVENANCE: "medium",     # N=2: the edge is real but confirm at N>=3
    STABLE_ID: "medium",
    CLIENT_COMPUTED: "high",  # high confidence it's NOT observation-recoverable
    ROTATING_OPAQUE: "low",
    INVARIANT: "medium",
}


_RATIONALE_BY_VERDICT = {
    SIGNING: ("credential/short-lived value — recognized as a signing slot, "
              "left opaque; the live session supplies it, the detector never "
              "computes it"),
    PROVENANCE: ("value carried from an earlier captured response — the "
                 "dependency edge is stable even though the value rotates"),
    STABLE_ID: ("matches a content-identifier shape — likely a stable id the "
                "site reuses; candidate for an extraction pattern"),
    CLIENT_COMPUTED: ("not found in the page, any prior response, or header — "
                      "most likely computed in client-side JS; cannot be "
                      "recovered by observation, the live session must produce "
                      "it"),
    ROTATING_OPAQUE: ("rotates with no observable source — even where the "
                      "value has an id-like shape, nothing in the page, prior "
                      "responses, or headers defines it, so it reads as a "
                      "session-local or client-derived value (e.g. a telemetry "
                      "counter), not a stable content id; verify against more "
                      "captures before relying on it"),
    INVARIANT: "constant across captures — a literal in the workflow",
}


_STRUCTURAL_WORD = re.compile(r"^[A-Za-z]{1,12}$")


_HEXISH = re.compile(r"^[0-9a-fA-F]{6,}$")


_RENDITION_SIGNAL = re.compile(
    r"(?:\d{2,5}\s*[xX]\s*\d{2,5})"       # 1280x720, 3840x2160, 5568x3132
    r"|(?:\b\d{3,4}[pi]\b)"               # 720p, 1080p, 2160p, 480i
    r"|(?:\b(?:[2348]k|uhd|qhd|fhd|hd|sd)\b)"  # 4k, 8k, uhd, hd, sd ...
    r"|(?:\bfps\b)|(?:\d+\s*fps)"         # 60fps, fps
    r"|(?:\b\d{3,5}\s*kbps\b)"            # 4500kbps (bitrate renditions)
    # v3.66.84 (VC-0028) — a BARE resolution width/height suffix (nubile: _480,
    # _3840). Matched only against the KNOWN resolution set after a _/-/x
    # delimiter, so it does not fire on arbitrary numbers (ids, segment indices).
    r"|(?:[_x-](?:144|240|288|360|480|540|576|720|960|1080|1280|1440|1920|"
    r"2160|2560|2880|3840|4096|4320|5760|7680)(?=[_x.]|$))",
    re.IGNORECASE)


_CANDIDATE_FLOOR = 1


_STORAGE_HINT = re.compile(r"^(?:ssd|hdd|hd|vol|disk|dsk|srv|node|stor)\d{1,3}$",
                           re.IGNORECASE)


_PATH_SIGN_TYPE = {
    "key": "token", "sig": "signature", "signature": "signature",
    "token": "token", "expires": "expiry", "expire": "expiry", "exp": "expiry",
    "end": "expiry", "st": "expiry", "start": "expiry", "limit": "expiry",
    "policy": "policy", "hmac": "hash", "hash": "hash", "md5": "hash",
    "keypair": "keypair", "keypairid": "keypair", "credential": "credential",
    "ip": "ip-binding", "cui": "opaque", "uh": "opaque",
}


_AFFECTS = {
    SIGNING: ("no classify rule — a credential is acquired by the live "
              "authenticated session (workflow/login layer), never by a "
              "detector pattern"),
    PROVENANCE: ("resolver/workflow layer — carry the value from its source "
                 "request at detect time; not a classify_url change"),
    CLIENT_COMPUTED: ("no detector component can recover this — it is produced "
                      "by client-side JS; the live browser session must run to "
                      "produce it"),
    ROTATING_OPAQUE: ("no extraction pattern — session-local/telemetry value; "
                      "no detector component depends on it"),
}


CP_NO_ACTION = "no_action_required"


CP_ADDITIONAL_CAPTURE = "additional_capture_recommended"


CP_DETECTOR_CONFIG = "detector_configuration_change_likely"


CP_SELECTOR_WORKFLOW = "selector_chain_or_workflow_investigation_likely"


CP_CLASSIFIER_SUFFICIENT = "existing_classifier_already_sufficient"


CP_UNRECOVERABLE = "unrecoverable_from_observer_side_evidence"


_GENERIC_BUCKETS = ("direct_file", "extensionless_file", "hls_manifest",
                    "dash_manifest")


_UNC_W_FLOOR = 3


_UNC_W_SKELETON = 3


_UNC_W_STRUCTURE = 2


_UNC_W_EDGE = 2


_UNC_W_SRC_UNKNOWN = 1


_NODE_WEIGHT = {"plan": 3, "impact": 2, "pattern": 1, "skeleton": 1,
                "finding": 1, "assume": 0, "edge": 0}


_STAB = {
    "heuristic":            {"mult": 3.0, "band": "low"},
    "shape_heuristic":      {"mult": 2.0, "band": "medium"},
    "assumption_untested":  {"mult": 2.5, "band": "low-medium"},
    "negative_observation": {"mult": 1.0, "band": "high"},
    "structural_limitation": {"mult": 1.0, "band": "medium"},
}


_FRAGILITY = {
    "goal_selection": {
        "basis": "heuristic",
        "perturbation": {"different_title": "may_invalidate",
                         "more_sessions": "neutral",
                         "player_config": "likely_invalidates",
                         "workflow": "likely_invalidates"},
        "scope": "heuristic generalizes; its correctness here is local",
        "survival_strength": "weak (a heuristic pick, not verified against the "
                             "page's declared download target)"},
    "n2_floor": {
        "basis": "structural_limitation",
        "perturbation": {"different_title": "neutral",
                         "more_sessions": "resolves",
                         "player_config": "neutral", "workflow": "neutral"},
        "scope": "generalizes (a sample-size fact, not site-specific)",
        "survival_strength": "n/a — a known limitation, resolved by more "
                             "sessions rather than invalidated"},
    "title_invariant": {
        "basis": "assumption_untested",
        "perturbation": {"different_title": "validates",
                         "more_sessions": "neutral",
                         "player_config": "may_invalidate",
                         "workflow": "may_invalidate"},
        "scope": "local (this site's URL scheme)",
        "survival_strength": "validated for different_title — a second title "
                             "(ba7f0af0) used the same URL scheme, so the scheme "
                             "is invariant across titles (corpus); "
                             "player_config/workflow axes remain untested"},
    "skeleton_identity": {
        "basis": "shape_heuristic",
        "perturbation": {"different_title": "validates",
                         "more_sessions": "neutral",
                         "player_config": "may_invalidate", "workflow": "neutral"},
        "scope": "the identity key's shape generalizes; the value is local",
        "survival_strength": "VALIDATED — a second title (53eb2252 -> ba7f0af0) "
                             "showed the content_id co-varies with title, so "
                             "different_title validates it (corpus VC-0006). "
                             "player_config/workflow axes remain untested."},
    "skeleton_rendition": {
        "basis": "shape_heuristic",
        # v3.66.76 correction: the pre-.76 model gave every skeleton slot the
        # identity rule (different_title -> validates). A second title left the
        # rendition filename unchanged (1280x720_60FPS.mp4 on both titles), so a
        # different title does NOT validate the rendition slot — it MAY simply no
        # longer serve the recorded member. Corpus VC-0005 falsified the old rule.
        "perturbation": {"different_title": "may_invalidate",
                         "more_sessions": "neutral",
                         "player_config": "may_invalidate", "workflow": "neutral"},
        "scope": "the rendition descriptor's shape generalizes; it is "
                 "resolution-keyed and does NOT co-vary with title",
        "survival_strength": "CORRECTED from 'validates' — a different title does "
                             "not promote the rendition slot; the recorded member "
                             "may be absent (corpus VC-0005). Untested axes: "
                             "player_config/workflow."},
    "src_unknown": {
        "basis": "negative_observation",
        "perturbation": {"different_title": "neutral", "more_sessions": "neutral",
                         "player_config": "may_invalidate", "workflow": "neutral"},
        "scope": "generalizes (client-computed telemetry behaves so everywhere)",
        "survival_strength": "strong (absence confirmed against page, headers, "
                             "and retained bodies)"},
}


_GENERALIZES_KIND = {"structural_limitation"}


_REUSABLE_CLASS_KIND = {"negative_observation"}


_METHOD_ONLY_KIND = {"heuristic", "shape_heuristic"}


_LOCAL_KIND = {"assumption_untested"}


_BAND_ORDER = {"low": 0, "low-medium": 1, "medium": 2, "high": 3}


_OBSERVED_BASES = {"observed"}
