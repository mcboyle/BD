"""validation_corpus.py — a permanent, append-only ledger of validation events.

The workbench can now validate itself; this module is where those validations
ACCUMULATE instead of being treated independently. Each entry records one
validation event — a prediction the framework made, what a real capture
observed, whether it was confirmed or falsified, and the model change (if any)
that resulted. Over time the corpus answers the questions that matter for a
self-correcting system:

  * What has the framework been WRONG about?               -> outcome == falsified
  * Which assumption CATEGORIES fail most often?            -> falsified by basis_kind
  * Which confidence caps were actually PREDICTIVE?         -> category confidence_cap
  * Which sensitivity flags became real findings?           -> category sensitivity_flag
  * Which perturbation rules were confirmed / falsified?    -> category perturbation_rule
  * Is the framework IMPROVING over time?                   -> model_change ledger

PERSISTENCE: the corpus is a repo data file (JSONL — one JSON object per line,
append-friendly and git-diffable). It ships in the release zip and accumulates
across sessions as the operator carries the tree forward. It is DATA, not
consolidated-KB wisdom: appending an event is part of doing validation and is
allowed in any session, unlike the override-gated KB merge.

POSTURE: recognition-only metadata. Entries describe predictions and outcomes in
prose; no capture bytes, no signing values, no credentials are stored here.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

CORPUS_VERSION = 1

# An entry's required + optional fields. Kept as a flat dict (not a dataclass)
# so JSONL round-trips trivially and old entries with extra fields survive.
_REQUIRED = ("id", "date", "version", "subject", "category", "prediction",
             "observation", "outcome", "evidence")
_OPTIONAL = ("basis_kind", "model_change", "notes", "conclusion_class",
             "failure_class", "correction", "resolves")

# A model_correction entry SHOULD carry a structured `correction` record with
# these six fields, so a correction is self-describing: what was believed, what
# disproved it, what replaced it, what confirms the replacement, what it touches,
# and what was observed after shipping it. A `resolves` list names prior pending
# entries this correction closes (append-only: resolution is recorded by a new
# entry pointing back, never by editing the old one).
CORRECTION_FIELDS = ("old_assumption", "falsifying_evidence",
                     "corrected_assumption", "validation_evidence",
                     "downstream_layers", "observed_effect")

# Richer fields every correction SHOULD answer going forward (optional so older
# correction entries stay valid). They make the loop auditable: why the error
# happened, what was predicted before shipping, whether the prediction matched,
# and whether the fix generalized beyond the site that surfaced it.
CORRECTION_OPTIONAL_FIELDS = ("root_cause", "expected_effect",
                              "prediction_matched", "generalized")

# A validation event is about one of these kinds of framework OUTPUT.
CATEGORIES = ("assumption", "confidence_cap", "sensitivity_flag",
              "perturbation_rule", "drift_verdict")
# The outcome of testing the prediction against a real capture. 'untested' is
# validation debt: a registered assertion the framework makes that no capture has
# yet challenged (distinct from confirmed/falsified, which have been tested).
OUTCOMES = ("confirmed", "falsified", "partial", "untested")

# An orthogonal axis (added v3.66.75): what KIND of validated conclusion the
# entry represents. The four the operator tracks explicitly are framework_level
# / site_specific / anomaly / capability_gap; model_correction and
# method_validation cover the rest so every entry classifies.
#   framework_level  — confirmed to transfer verbatim across unrelated sites
#                      (signing-opacity, telemetry-unrecoverable, goal_selection)
#   site_specific    — confirmed local; correctly did NOT over-generalize
#   anomaly          — a rule reproduced but with wrong granularity/behavior
#   capability_gap   — a boundary the framework correctly declined, but a
#                      recurring outcome justifies a new (recognition-only)
#                      capability
#   model_correction — a falsification that shipped (or is pending) a fix
#   method_validation— a predictive cap / sensitivity flag / drift verdict /
#                      role confirmation that held
CONCLUSION_CLASSES = ("framework_level", "site_specific", "anomaly",
                      "capability_gap", "model_correction", "method_validation")

# Entries written BEFORE conclusion_class existed are classified here, by
# explicit human judgment, so the JSONL lines stay immutable (append-only is
# preserved — existing outcomes are never rewritten). New entries carry the
# field natively; as they accrue this map becomes vestigial.
_CONCLUSION_CLASS_BACKFILL = {
    "VC-0001": "method_validation",   # segment-role identity detection works
    "VC-0002": "model_correction",    # filename -> .69 identity/rendition split
    "VC-0003": "method_validation",   # confidence cap was predictive
    "VC-0004": "method_validation",   # sensitivity flag was predictive
    "VC-0005": "model_correction",    # _FRAGILITY rendition rule (pending fix)
    "VC-0006": "method_validation",   # perturbation rule confirmed for identity
    "VC-0007": "method_validation",   # drift taxonomy: no-churn reference case
    "VC-0008": "method_validation",   # drift taxonomy: rendition-drift case
    "VC-0009": "framework_level",     # goal_selection robust across sites
    "VC-0010": "framework_level",     # signing-opacity across schemes
    "VC-0011": "framework_level",     # reusable classes reproduce cross-site
    "VC-0012": "anomaly",             # bros over-split (CDN path-sharding)
    "VC-0013": "site_specific",       # direct_file classification is local
}


def conclusion_class(entry: Dict[str, Any]) -> str:
    """The conclusion class for an entry: an explicit field wins, else the
    backfill map for pre-field entries, else 'unclassified'."""
    if entry.get("conclusion_class"):
        return entry["conclusion_class"]
    return _CONCLUSION_CLASS_BACKFILL.get(entry.get("id"), "unclassified")


# v3.66.80 — the FAILURE TAXONOMY. conclusion_class says what KIND of conclusion
# an entry is; failure_class says what KIND of mistake it represents. The corpus
# is no longer just a list of findings — it records what kinds of errors the
# framework makes. Five classes, learned from the corpus itself:
#   candidate_loss            the correct candidate was never generated (discarded
#                             before the uncertainty machinery — INVISIBLE to every
#                             downstream safeguard). nubile slug.
#   candidate_over_generation too many candidates generated for one logical thing
#                             (visible, but noise-inflating). bros sharded path.
#   bad_candidate_selection   the right candidates were generated and VISIBLE, but
#                             a role/label/perturbation choice among them was wrong
#                             (the machinery hedges these). filename role, fragility.
#   capability_boundary       the framework correctly DECLINED to recognize/resolve
#                             something (conservatively incomplete, not wrong). HLS,
#                             short signing names.
#   insufficient_evidence     no candidate change helps; the claim is simply
#                             untested and needs more captures. fragility axes, n2.
FAILURE_CLASSES = ("candidate_loss", "candidate_over_generation",
                   "bad_candidate_selection", "capability_boundary",
                   "insufficient_evidence")

# id-keyed (NOT subject-keyed: VC-0005 falsified and VC-0006 confirmed share a
# subject). A correction is tagged with the class of failure it addresses, so the
# finding and its fix carry the same class. Confirmations/validations are not
# failures and are absent here (failure_class -> None).
_FAILURE_CLASS_BACKFILL = {
    "VC-0002": "bad_candidate_selection",    # filename promoted: role choice wrong
    "VC-0005": "bad_candidate_selection",    # fragility rule on a visible candidate
    "VC-0012": "candidate_over_generation",  # bros: 11 slots for one id
    "VC-0014": "capability_boundary",        # HLS manifest: declined recognition
    "VC-0015": "bad_candidate_selection",    # fix of VC-0005
    "VC-0016": "candidate_over_generation",  # fix of VC-0012
    "VC-0017": "insufficient_evidence",      # player_config axis untested
    "VC-0018": "insufficient_evidence",      # workflow axis untested
    "VC-0019": "insufficient_evidence",      # n2_floor untested
    "VC-0020": "candidate_loss",             # nubile slug discarded (primary); the
                                             # rendition facet is bad_selection
    "VC-0021": "capability_boundary",        # short signing names: declined
}


def failure_class(entry: Dict[str, Any]) -> Optional[str]:
    """What KIND of mistake an entry represents, or None if it is not a failure
    (a confirmation/validation). Explicit field wins, else the backfill."""
    if entry.get("failure_class"):
        return entry["failure_class"]
    return _FAILURE_CLASS_BACKFILL.get(entry.get("id"))


def _is_failure_bearing(entry: Dict[str, Any]) -> bool:
    """An entry that represents a mistake, gap, correction, or untested claim —
    i.e. something the failure taxonomy should classify."""
    return (entry.get("outcome") in ("falsified", "partial", "untested")
            or conclusion_class(entry) in ("capability_gap", "model_correction",
                                           "anomaly"))


def default_corpus_path() -> str:
    """The corpus lives at the repo root, alongside CHANGELOG.md."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "validation_corpus.jsonl")


def validate_entry(entry: Dict[str, Any]) -> None:
    """Raise ValueError if an entry is malformed. Called before every append so
    the corpus never accumulates junk."""
    missing = [f for f in _REQUIRED if f not in entry or entry[f] in (None, "")]
    if missing:
        raise ValueError(f"validation entry missing required fields: {missing}")
    if entry["category"] not in CATEGORIES:
        raise ValueError(f"category must be one of {CATEGORIES}, "
                         f"got {entry['category']!r}")
    if entry["outcome"] not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}, "
                         f"got {entry['outcome']!r}")
    cc = entry.get("conclusion_class")
    if cc is not None and cc not in CONCLUSION_CLASSES:
        raise ValueError(f"conclusion_class must be one of {CONCLUSION_CLASSES}, "
                         f"got {cc!r}")
    fc = entry.get("failure_class")
    if fc is not None and fc not in FAILURE_CLASSES:
        raise ValueError(f"failure_class must be one of {FAILURE_CLASSES}, "
                         f"got {fc!r}")
    corr = entry.get("correction")
    if corr is not None:
        if not isinstance(corr, dict):
            raise ValueError("correction must be a dict")
        missing_c = [f for f in CORRECTION_FIELDS if not corr.get(f)]
        if missing_c:
            raise ValueError(f"correction record missing fields: {missing_c}")
    res = entry.get("resolves")
    if res is not None and not isinstance(res, list):
        raise ValueError("resolves must be a list of entry ids")


def load_corpus(path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read all entries from the JSONL corpus. Missing file -> empty corpus.
    Blank lines are skipped; a malformed line raises (the corpus is supposed to
    be well-formed — a parse error is a real problem, not silently ignored)."""
    path = path or default_corpus_path()
    if not os.path.exists(path):
        return []
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"corpus line {i} is not valid JSON: {exc}")
    return out


def _next_id(entries: List[Dict[str, Any]]) -> str:
    nums = [int(e["id"].split("-")[1]) for e in entries
            if isinstance(e.get("id"), str) and e["id"].startswith("VC-")
            and e["id"].split("-")[1].isdigit()]
    return f"VC-{(max(nums) + 1) if nums else 1:04d}"


def append_entry(entry: Dict[str, Any],
                 path: Optional[str] = None) -> Dict[str, Any]:
    """Append one validation event. Assigns id + date if absent, validates, then
    appends a single JSONL line (append-only — existing history is never
    rewritten)."""
    path = path or default_corpus_path()
    entries = load_corpus(path)
    entry = dict(entry)
    entry.setdefault("id", _next_id(entries))
    entry.setdefault("date", datetime.now(timezone.utc).isoformat())
    validate_entry(entry)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=True) + "\n")
    return entry


def query(entries: List[Dict[str, Any]], *, category: Optional[str] = None,
          outcome: Optional[str] = None, basis_kind: Optional[str] = None,
          subject: Optional[str] = None) -> List[Dict[str, Any]]:
    """Filter entries by any combination of fields."""
    def _ok(e: Dict[str, Any]) -> bool:
        return ((category is None or e.get("category") == category)
                and (outcome is None or e.get("outcome") == outcome)
                and (basis_kind is None or e.get("basis_kind") == basis_kind)
                and (subject is None or e.get("subject") == subject))
    return [e for e in entries if _ok(e)]


def _count_by(entries: List[Dict[str, Any]], field: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for e in entries:
        k = e.get(field) or "(none)"
        out[k] = out.get(k, 0) + 1
    return out


def summarize(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The failure-accumulation readout. Each section answers one of the
    operator's questions directly; all are pure aggregations over the corpus."""
    falsified = query(entries, outcome="falsified")
    confirmed = query(entries, outcome="confirmed")

    # which perturbation RULES have been confirmed vs falsified (a ledger per
    # rule subject, since the same rule can be confirmed in one role and
    # falsified in another — exactly the skeleton different_title->validates case)
    rule_ledger: Dict[str, Dict[str, int]] = {}
    for e in query(entries, category="perturbation_rule"):
        led = rule_ledger.setdefault(e["subject"], {"confirmed": 0,
                                                    "falsified": 0, "partial": 0})
        led[e["outcome"]] = led.get(e["outcome"], 0) + 1

    # the four conclusion classes the operator tracks explicitly, plus a full
    # by-class count. An entry's class: explicit field > backfill > unclassified.
    def _by_class(cls: str, require_confirmed: bool = True):
        return [{"id": e["id"], "subject": e["subject"],
                 "outcome": e["outcome"], "model_change": e.get("model_change")}
                for e in entries if conclusion_class(e) == cls
                and (not require_confirmed or e["outcome"] == "confirmed")]

    class_counts: Dict[str, int] = {}
    for e in entries:
        c = conclusion_class(e)
        class_counts[c] = class_counts.get(c, 0) + 1

    # v3.66.80 — failure taxonomy distribution. Only failure-bearing entries
    # (mistakes/gaps/corrections/untested) are counted; confirmations are not
    # failures. A correction shares the class of the failure it addresses, so a
    # class can span a finding and its fix.
    failure_counts: Dict[str, int] = {}
    unclassified_failures: List[Dict[str, str]] = []
    for e in entries:
        if not _is_failure_bearing(e):
            continue
        fc = failure_class(e)
        if fc is None:
            unclassified_failures.append({"id": e["id"], "subject": e["subject"]})
        else:
            failure_counts[fc] = failure_counts.get(fc, 0) + 1

    # resolution: a later correction entry names prior items it closes via
    # `resolves`. Those ids drop out of pending (append-only — the original
    # falsified entry is never edited; a new entry retires it by reference).
    resolved_ids = set()
    for e in entries:
        for rid in (e.get("resolves") or []):
            resolved_ids.add(rid)

    return {
        "corpus_version": CORPUS_VERSION,
        "total_events": len(entries),
        "by_outcome": _count_by(entries, "outcome"),
        "by_category": _count_by(entries, "category"),
        "by_conclusion_class": class_counts,
        "by_failure_class": failure_counts,
        "unclassified_failures": unclassified_failures,
        "resolved_corrections": [
            {"id": e["id"], "subject": e["subject"], "resolves": e["resolves"]}
            for e in entries if e.get("resolves")],

        # the four explicitly-tracked conclusion classes
        # framework_level / site_specific require a confirmed outcome; anomaly
        # and capability_gap are "confirmed" by being established as such (an
        # anomaly may carry a partial outcome — the anomaly itself is confirmed).
        "confirmed_framework_level": _by_class("framework_level"),
        "confirmed_site_specific": _by_class("site_specific"),
        "confirmed_anomalies": _by_class("anomaly", require_confirmed=False),
        "confirmed_capability_gaps": _by_class("capability_gap",
                                               require_confirmed=False),

        # "What has the framework been wrong about?"
        "falsifications": [
            {"id": e["id"], "subject": e["subject"], "category": e["category"],
             "basis_kind": e.get("basis_kind"),
             "model_change": e.get("model_change")}
            for e in falsified],

        # "Which assumption categories fail most often?"
        "falsification_by_basis_kind": _count_by(falsified, "basis_kind"),

        # "Which confidence caps were predictive?" (a cap is predictive when the
        # capped conclusion was confirmed-as-fragile / later falsified)
        "confidence_caps": [
            {"id": e["id"], "subject": e["subject"], "outcome": e["outcome"]}
            for e in query(entries, category="confidence_cap")],

        # "Which sensitivity flags became real findings?"
        "sensitivity_flags": [
            {"id": e["id"], "subject": e["subject"], "outcome": e["outcome"]}
            for e in query(entries, category="sensitivity_flag")],

        # "Which perturbation rules were confirmed / falsified?"
        "perturbation_rule_ledger": rule_ledger,

        # "Is the framework improving?" — corrections that resulted from a
        # falsification, plus the falsifications still awaiting a fix.
        "model_changes": [
            {"id": e["id"], "subject": e["subject"],
             "model_change": e["model_change"], "version": e.get("version")}
            for e in entries if e.get("model_change")
            and not str(e["model_change"]).lower().startswith("pending")],
        # a falsified OR partial finding awaiting a fix: no model change yet, or
        # one explicitly marked pending — AND not yet resolved by a later entry.
        "pending_corrections": [
            {"id": e["id"], "subject": e["subject"],
             "outcome": e["outcome"], "model_change": e.get("model_change")}
            for e in entries
            if e.get("outcome") in ("falsified", "partial")
            and e["id"] not in resolved_ids
            and (not e.get("model_change")
                 or str(e.get("model_change")).lower().startswith("pending"))],

        "note": ("a permanent, append-only validation ledger. confirmed/"
                 "falsified outcomes accumulate so the framework's error history "
                 "and model improvements are measurable, not anecdotal. A "
                 "perturbation rule can appear confirmed in one role and "
                 "falsified in another — the ledger keeps both."),
    }


def debt_report(entries: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The corpus as a planning artifact: separate the three KINDS of debt,
    because they are different kinds of work.

      correction debt  — the framework is known to be WRONG: falsified/partial
                         findings not yet resolved (anomalies included).
      capability debt  — the framework is conservatively INCOMPLETE: capability
                         gaps not yet closed (it declined correctly, but a
                         recurring boundary justifies a new recognition).
      validation debt  — the framework has NOT YET been challenged: registered
                         assertions (outcome 'untested') no capture has exercised.

    Reaching zero correction debt is the meaningful checkpoint; validation debt
    is expected to remain non-zero (not everything can or should be challenged).
    """
    entries = load_corpus() if entries is None else entries
    resolved_ids = {rid for e in entries for rid in (e.get("resolves") or [])}

    def addressed(e):
        # a problem is addressed if a later entry resolves it OR it carries a
        # SHIPPED (non-pending) model change. (VC-0002's filename falsification
        # shipped its fix in v3.66.69, before the resolves mechanism existed — it
        # is addressed even without a resolver pointer.)
        if e["id"] in resolved_ids:
            return True
        mc = e.get("model_change")
        return bool(mc and not str(mc).lower().startswith("pending"))

    def open_(e):  # a problem entry still owed work
        return not addressed(e)

    correction_debt = [e for e in entries
                       if e.get("outcome") in ("falsified", "partial")
                       and conclusion_class(e) != "capability_gap"
                       and open_(e)]
    capability_debt = [e for e in entries
                       if conclusion_class(e) == "capability_gap" and open_(e)]
    # validation debt honors resolution the same way correction/capability debt
    # do: the corpus is append-only, so a tested prediction keeps its original
    # 'untested' entry forever and is retired by a LATER entry that `resolves` it
    # (with real qualifying data). Without open_() here a retired validation item
    # would linger as debt forever and the report would misstate the truth.
    # (v3.66.87 — surfaced when the temporal harness retired VC-0019.)
    validation_debt = [e for e in entries
                       if e.get("outcome") == "untested" and open_(e)]

    def brief(lst):
        return [{"id": e["id"], "subject": e["subject"],
                 "outcome": e["outcome"],
                 "conclusion_class": conclusion_class(e),
                 "failure_class": failure_class(e),
                 "model_change": e.get("model_change")} for e in lst]

    falsifications_open = [e for e in correction_debt
                           if e.get("outcome") == "falsified"]
    anomalies_open = [e for e in entries
                      if conclusion_class(e) == "anomaly" and open_(e)]
    # evidence with no action item: a problem entry that is open AND carries no
    # model_change at all (nothing is even planned to address it).
    orphaned = [e for e in entries
                if open_(e)
                and (e.get("outcome") in ("falsified", "partial")
                     or conclusion_class(e) == "capability_gap")
                and not e.get("model_change")]

    return {
        "correction_debt": brief(correction_debt),
        "capability_debt": brief(capability_debt),
        "validation_debt": brief(validation_debt),
        # the five questions, answered directly
        "unresolved_corrections": brief(correction_debt),
        "unaddressed_falsifications": brief(falsifications_open),
        "open_anomalies": brief(anomalies_open),
        "open_capability_gaps": brief(capability_debt),
        "evidence_without_action_item": brief(orphaned),
        # the checkpoint test (correction debt zero is the transition)
        "checkpoint": {
            "no_pending_corrections": len(correction_debt) == 0,
            "no_unresolved_falsifications": len(falsifications_open) == 0,
            "no_open_anomalies": len(anomalies_open) == 0,
            "open_capability_gaps": len(capability_debt),
            "validation_debt_items": len(validation_debt),
            "at_clean_correction_checkpoint": (len(correction_debt) == 0
                                               and len(orphaned) == 0),
        },
        "note": ("correction debt = known wrong; capability debt = conservatively "
                 "incomplete; validation debt = not yet challenged. These are "
                 "different work. Zero correction debt is the checkpoint; "
                 "validation debt is expected to persist."),
    }
