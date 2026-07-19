#!/usr/bin/env python3
"""offline_capture_analyze.py — offline capture-ingestion CLI (v3.66.89).

Digest local capture artifacts into the framework's existing analysis pipeline, fully
offline, and emit reviewable reports. This is a thin wrapper: all loading, normalization,
and analysis lives in ``bulk_downloader.capture_ingest`` and the existing analysis modules
it reuses. The CLI's own job is argument handling and report generation.

It never fetches, replays, reconstructs, or computes signing material; every URL it writes
is query-stripped, signing is named by marker only, and it surfaces only the temporal
harness's boolean drift verdicts, never a fingerprint. It never writes the corpus — a
suggested entry is emitted as a reviewable artifact with no resolution pointer, and it
cannot retire debt.

Examples:
    # one or more captures of the same title (auto-detects a same-identity series)
    python tools/offline_capture_analyze.py capA.json later.wacz --out ./offline_out

    # a directory of capture artifacts, treated as an ordered temporal series
    python tools/offline_capture_analyze.py ./captures_dir --series --out ./offline_out

    # a real baseline/perturbed pair on one perturbation axis
    python tools/offline_capture_analyze.py --baseline base.wacz \\
        --perturbed perturbed.wacz --axis player_config --out ./offline_out
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bulk_downloader import capture_ingest as ci
from bulk_downloader.temporal_harness import CONFIRMED, UNTESTED, FALSIFIED


# ── report writers (prose-forward markdown) ─────────────────────────
def _path_only(path_template: str) -> str:
    """The path portion of a template, for display alongside a host."""
    if not path_template:
        return ""
    i = path_template.find("/")
    return path_template[i:] if i >= 0 else "/" + path_template


def _capture_inventory(result: Dict[str, Any]) -> str:
    L: List[str] = ["# Capture inventory", ""]
    L.append(f"This inventory describes the {result['n_captures']} capture artifact(s) "
             f"ingested offline. Every URL shown here is query-stripped, and signing is "
             f"named by marker only; no signing value appears in this report.")
    L.append("")
    for m, pc in zip(result["models"], result["per_capture"]):
        caps = m["capabilities"]
        a = pc["analysis"]
        L.append(f"## {m['source_name']}")
        L.append("")
        if "error" in a:
            goal_desc = "none — no media request could be identified"
        else:
            goal_desc = (f"{a['host']}{_path_only(a.get('path_template') or '')} "
                         f"(a masked template; signing values are never shown)")
        L.append(f"The capture targets host {m['host'] or 'unknown'} and carries "
                 f"{m['n_requests']} request(s). Its selected media goal is {goal_desc}.")
        present = []
        present.append("response status" if caps["has_responses"] else None)
        present.append("headers" if caps["has_headers"] else None)
        present.append("initiator" if caps["has_initiator"] else None)
        have = [p for p in present if p]
        lack = [n for p, n in zip(present, ("response status", "headers", "initiator"))
                if not p]
        L.append(f"It carries {', '.join(have) if have else 'none'} of the optional "
                 f"signals, and lacks {', '.join(lack) if lack else 'none'}. The model "
                 f"records each as present or absent rather than assuming a fixed shape.")
        # redaction summary across requests
        states = {}
        for r in m["requests"]:
            states[r["redaction_state"]] = states.get(r["redaction_state"], 0) + 1
        L.append(f"Redaction state across requests: " +
                 ", ".join(f"{k} ({v})" for k, v in sorted(states.items())) + ".")
        # signing markers on the goal
        goal_markers = next((r["signing_markers"] for r in m["requests"]
                             if r["url"] == m["goal_url"]), [])
        if goal_markers:
            names = ", ".join(f"{mk['name']} ({mk['location']})" for mk in goal_markers)
            L.append(f"The goal request shows signing markers by name: {names}. Their "
                     f"values are masked and never read into this report.")
        else:
            L.append("The goal request shows no detected signing markers.")
        L.append("")
    return "\n".join(L)


def _offline_analysis(result: Dict[str, Any]) -> str:
    L: List[str] = ["# Offline analysis", ""]
    L.append("This report records what the framework's existing analysis produced over "
             "the ingested captures: goal selection, candidate scoring, identity and "
             "rendition slotting, and signing recognition, all run offline. Path-signing "
             "values are masked by the existing skeleton masker; query-signing is named "
             "by marker only.")
    L.append("")
    for pc in result["per_capture"]:
        a = pc["analysis"]
        L.append(f"## {pc['source']}")
        L.append("")
        if "error" in a:
            L.append(f"No analysis was possible: {a['error']}.")
            L.append("")
            continue
        L.append(f"Goal selection chose the media target resolving to "
                 f"{a['host']}{_path_only(a.get('path_template') or '')}, shown as a "
                 f"masked template so no signing value appears.")
        ident = ", ".join(a["identity_slots"]) or "none"
        rend = ", ".join(a["rendition_slots"]) or "none"
        L.append(f"The skeleton identifies the content-identity slot(s) as {ident} and "
                 f"the rendition slot(s) as {rend}.")
        # candidate scoring, described in prose
        scored = [c for c in a["candidate_slots"] if c.get("score") is not None]
        if scored:
            parts = [f"{c['sample']} (role {c['role']}, score {c['score']})"
                     for c in scored]
            L.append("Candidate scoring admitted the following slots: " +
                     "; ".join(parts) + ".")
        if a.get("path_signing"):
            L.append(f"Path-embedded signing was recognized and masked: "
                     f"{json.dumps(a['path_signing'])}.")
        if a.get("query_signing_markers"):
            names = ", ".join(mk["name"] for mk in a["query_signing_markers"])
            L.append(f"Query signing markers were recognized by name: {names} "
                     f"(values masked).")
        L.append("")
    return "\n".join(L)


def _axis_label(outcome: str, evidence_is_real: bool) -> str:
    """Map a harness axis outcome to a finding label."""
    if outcome == CONFIRMED:
        return "confirmed" if evidence_is_real else "confirmed (machinery)"
    if outcome == UNTESTED:
        return "insufficient"
    if outcome == FALSIFIED:
        return "confirmed-contradiction"
    return "possible"


def _drift_report(result: Dict[str, Any],
                  pert: Optional[Dict[str, Any]]) -> Optional[str]:
    t = result.get("temporal")
    if not t and not pert:
        return None
    L: List[str] = ["# Drift report", ""]
    if t and "axes" in t:
        L.append(f"A temporal drift analysis was run over {t['n_captures']} ordered "
                 f"captures using the existing temporal harness. The harness compares "
                 f"values only by in-memory fingerprint and reports boolean verdicts; no "
                 f"fingerprint or signing value appears below.")
        L.append("")
        for ax, d in t["axes"].items():
            L.append(f"On the {ax} axis the verdict is {d['outcome']}. {d['observation']}")
            L.append("")
        floor = t.get("vc_0019_floor", {})
        if floor:
            L.append(f"On the N=2 floor question the verdict is {floor.get('outcome')}: "
                     f"{floor.get('observation')}")
            L.append("")
    if pert:
        ev_real = pert.get("evidence") == "real"
        L.append(f"## Perturbation — {pert.get('axis')} axis")
        L.append("")
        L.append(f"A perturbation analysis was run on a real baseline/perturbed pair "
                 f"using the existing perturbation harness with evidence marked real, "
                 f"which means the data decides the verdict rather than it being "
                 f"pre-forced. The harness left its debt, confidence, and sensitivity "
                 f"flags pending (resolves_debt={pert.get('resolves_debt')}), exactly as "
                 f"a real run should until a corpus session weighs it.")
        L.append("")
        for k in pert.get("per_kind", []):
            if k.get("observed") in ("not structurally observable from one capture",):
                continue
            note = ("a tension on real evidence is a falsification signal"
                    if "TENSION" in k.get("outcome", "") else
                    "consistent with the prediction")
            L.append(f"For {k['kind']}, the prediction was {k['prediction']} and the "
                     f"observation was {k['observed']}; {k['outcome']} — {note}.")
            L.append("")
    return "\n".join(L)


def _validation_readiness(result: Dict[str, Any],
                          pert: Optional[Dict[str, Any]],
                          suggested: Optional[Dict[str, Any]]) -> str:
    """The labeled-findings report: confirmed / possible / unsupported /
    validation-ready / insufficient / required-next-capture."""
    L: List[str] = ["# Validation readiness", ""]
    L.append("This report classifies what the offline analysis can and cannot conclude, "
             "in the framework's required terms. It does not retire debt and it does not "
             "write the corpus; where evidence would support a corpus entry, that entry "
             "is emitted separately as a reviewable suggestion.")
    L.append("")

    confirmed: List[str] = []
    possible: List[str] = []
    unsupported: List[str] = []
    ready: List[str] = []
    insufficient: List[str] = []
    next_capture: List[str] = []

    # per-capture confirmed observations
    for pc in result["per_capture"]:
        a = pc["analysis"]
        if "error" in a:
            insufficient.append(f"{pc['source']}: no media goal could be selected, so no "
                                f"skeleton analysis was possible.")
            next_capture.append(f"{pc['source']}: a capture in which a media request is "
                                f"actually present.")
            continue
        if a["identity_slots"]:
            confirmed.append(f"{pc['source']}: a content-identity slot was directly "
                             f"observed in the goal ({', '.join(a['identity_slots'])}).")
        if a.get("query_signing_markers") or a.get("path_signing"):
            confirmed.append(f"{pc['source']}: signing was recognized and surfaced by "
                             f"marker name only, with values masked.")

    # temporal axes
    t = result.get("temporal")
    if t and "axes" in t:
        for ax, d in t["axes"].items():
            if d["outcome"] == CONFIRMED:
                confirmed.append(f"temporal {ax}: reproduced across the series on real "
                                 f"captures ({d['outcome']}).")
            elif d["outcome"] == UNTESTED:
                insufficient.append(f"temporal {ax}: {d['observation']}")
                if ax == "signing":
                    next_capture.append("a same-title series of at least two captures "
                                        "that RETAIN signing values (fingerprinted in "
                                        "memory, never surfaced) to measure signing drift.")
            elif d["outcome"] == FALSIFIED:
                confirmed.append(f"temporal {ax}: an unexpected result was observed "
                                 f"({d['observation']}).")
        floor = t.get("vc_0019_floor", {})
        if floor.get("outcome") == UNTESTED:
            insufficient.append(f"N=2 floor: {floor.get('observation')}")
            next_capture.append("a third qualifying same-title capture to test the N>=3 "
                                "floor lift on this title.")
        elif floor.get("outcome") == CONFIRMED:
            confirmed.append("N=2 floor: lifted on this title with N>=3 captures.")
        # validation-ready: confirmed drift on RETAINED signing only
        sign = t["axes"].get("signing", {})
        if sign.get("outcome") == CONFIRMED:
            ready.append("a measured signing-drift result on retained values, which could "
                         "support a drift_verdict corpus entry after review.")

    # perturbation: real evidence bearing on open debt
    if pert:
        axis = pert.get("axis")
        debt = "VC-0017" if axis == "player_config" else "VC-0018"
        ready.append(f"a real perturbation outcome on the {axis} axis, which bears on "
                     f"open validation debt {debt} and could support a corpus entry after "
                     f"a dedicated review session.")
        tensions = [k for k in pert.get("per_kind", []) if "TENSION" in k.get("outcome", "")]
        if tensions:
            possible.append(f"the {axis} perturbation produced tension(s) against the "
                            f"fragility prediction on real evidence, a falsification "
                            f"signal to be weighed in review (not auto-applied).")
        else:
            possible.append(f"the {axis} perturbation was consistent with the fragility "
                            f"prediction on real evidence, supporting evidence to be "
                            f"weighed in review.")

    # always-unsupported conclusions, grounded in corpus doctrine
    unsupported.append("title uniqueness — that any observed identity value is unique to "
                       "its title rather than potentially shared — is not established by "
                       "these captures and must not be inferred.")
    unsupported.append("generalization beyond the captures actually ingested — these "
                       "results describe the captures provided, not the framework "
                       "universally.")
    if t and t["axes"].get("signing", {}).get("outcome") == UNTESTED:
        unsupported.append("any conclusion about signing behavior on this series — the "
                           "values were scrubbed, so signing drift is unmeasured, not "
                           "absent.")
    if not next_capture:
        next_capture.append("no further capture is strictly required to interpret what "
                            "was provided; additional captures would broaden coverage.")

    def _section(title: str, items: List[str]) -> None:
        L.append(f"## {title}")
        L.append("")
        if not items:
            L.append("None identified from the captures provided.")
        else:
            for it in items:
                L.append(f"- {it}")
        L.append("")

    # the operator explicitly asked these be labeled; a short enumerated list per
    # label is the clearest faithful rendering of a classification result
    _section("Confirmed findings", confirmed)
    _section("Possible findings", possible)
    _section("Unsupported conclusions", unsupported)
    _section("Validation-ready evidence", ready)
    _section("Insufficient evidence", insufficient)
    _section("Required next capture", next_capture)

    if suggested is not None:
        L.append("## Suggested corpus entry")
        L.append("")
        L.append("A suggested corpus entry was generated as a reviewable artifact "
                 "(corpus_candidate_entry.json). It carries no resolution pointer, it "
                 "does not retire debt, and it has NOT been written to the corpus. It is "
                 "schema-shaped so a reviewer can assess it, but assigning its id, "
                 "confirming its outcome, and deciding whether it resolves anything are "
                 "human steps.")
        L.append("")
    return "\n".join(L)


def _build_suggested_entry(result: Dict[str, Any],
                           pert: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Produce a reviewable, schema-shaped suggested entry — or None if nothing
    corpus-worthy was produced. NEVER carries a `resolves` pointer; NEVER written."""
    t = result.get("temporal")
    if pert:
        axis = pert.get("axis")
        debt = "VC-0017" if axis == "player_config" else "VC-0018"
        tensions = [k for k in pert.get("per_kind", []) if "TENSION" in k.get("outcome", "")]
        return {
            "id": "<assign-on-review>",
            "date": str(date.today()),
            "version": ci.CAPTURE_INGEST_VERSION,
            "subject": f"perturbation_{axis}_real_capture",
            "category": "perturbation_rule",
            "conclusion_class": "method_validation",
            "basis_kind": "structural_limitation",
            "prediction": (f"the fragility map's {axis} predictions for goal selection "
                           f"and the skeleton slots"),
            "observation": (f"a real {axis} perturbation pair was analyzed offline; the "
                            f"harness reported "
                            f"{'tension(s) against' if tensions else 'consistency with'} "
                            f"the predictions, with the debt/confidence/sensitivity "
                            f"verdict left pending for review"),
            "outcome": "untested",
            "evidence": ("offline perturbation_run on a real baseline/perturbed pair; "
                         "per-kind outcomes recorded in drift_report.md"),
            "notes": (f"SUGGESTION ONLY — generated offline, requires human review, has "
                      f"NO resolves pointer, does NOT retire debt or write the corpus. A "
                      f"reviewer must decide whether this bears on {debt} and in which "
                      f"direction. Recognition-only; no signing value involved."),
        }
    if t and "axes" in t:
        axes = t["axes"]
        outcome = "confirmed" if all(
            axes[a]["outcome"] == CONFIRMED for a in ("identity", "rendition", "structural")
        ) else "partial"
        return {
            "id": "<assign-on-review>",
            "date": str(date.today()),
            "version": ci.CAPTURE_INGEST_VERSION,
            "subject": "temporal_reproduction_offline_ingest",
            "category": "drift_verdict",
            "conclusion_class": "method_validation",
            "basis_kind": "structural_limitation",
            "prediction": ("the framework's temporal behaviors (identity invariance, "
                           "rendition drift attribution, structural stability) hold on "
                           "this series"),
            "observation": ("offline temporal drift analysis over the ingested series; "
                            "per-axis verdicts recorded in drift_report.md, signing "
                            f"{axes.get('signing', {}).get('outcome', 'n/a')}"),
            "outcome": outcome,
            "evidence": "offline drift_series over the ingested captures",
            "notes": ("SUGGESTION ONLY — generated offline, requires human review, has NO "
                      "resolves pointer, does NOT retire debt or write the corpus. "
                      "Recognition-only; signing values were never surfaced."),
        }
    return None


def _corpus_compat_check(entry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Dry compatibility check on a suggested entry: would it be schema-shaped, and
    does it correctly avoid retiring debt? Never appends."""
    if entry is None:
        return {"applicable": False}
    from bulk_downloader.validation_corpus import _REQUIRED, CATEGORIES
    missing = [f for f in _REQUIRED if f not in entry and f != "id"]
    return {
        "applicable": True,
        "schema_shaped": not missing and entry.get("category") in CATEGORIES,
        "missing_required": missing,
        "carries_resolves": "resolves" in entry,   # must be False
        "retires_debt": "resolves" in entry,        # the only way it could
    }


# ── posture verification across all generated text ──────────────────
def _posture_verify(reports: Dict[str, str]) -> List[str]:
    offenders: List[str] = []
    for name, text in reports.items():
        for frag in ci.posture_scan(text):
            offenders.append(f"{name}: {frag}")
    return offenders


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Offline capture-ingestion analysis.")
    ap.add_argument("captures", nargs="*", help=".json/.wacz files or a directory")
    ap.add_argument("--out", default="./offline_analysis", help="output directory")
    ap.add_argument("--series", action="store_true",
                    help="treat captures as an ordered same-title temporal series")
    ap.add_argument("--labels", nargs="*", help="optional labels for the captures")
    ap.add_argument("--baseline", help="perturbation baseline capture")
    ap.add_argument("--perturbed", help="perturbation perturbed capture")
    ap.add_argument("--axis", choices=("player_config", "workflow"),
                    help="perturbation axis (required with --baseline/--perturbed)")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    pert = None
    if args.baseline or args.perturbed or args.axis:
        if not (args.baseline and args.perturbed and args.axis):
            print("error: --baseline, --perturbed, and --axis must be given together",
                  file=sys.stderr)
            return 2
        pert = ci.analyze_perturbation(args.baseline, args.perturbed, args.axis)

    if not args.captures and not pert:
        print("error: provide capture paths, or a --baseline/--perturbed/--axis triple",
              file=sys.stderr)
        return 2

    # If only a perturbation pair was given, also ingest both as captures for the
    # inventory and per-capture analysis, so the reports are complete.
    capture_paths = args.captures or [args.baseline, args.perturbed]
    result = ci.analyze_captures(capture_paths, series=args.series, labels=args.labels)

    suggested = _build_suggested_entry(result, pert)
    compat = _corpus_compat_check(suggested)

    reports: Dict[str, str] = {
        "capture_inventory.md": _capture_inventory(result),
        "offline_analysis.md": _offline_analysis(result),
        "validation_readiness.md": _validation_readiness(result, pert, suggested),
    }
    drift = _drift_report(result, pert)
    if drift is not None:
        reports["drift_report.md"] = drift

    # posture verification BEFORE writing anything
    offenders = _posture_verify(reports)
    if offenders:
        print("POSTURE FAILURE: a signing value escaped masking in generated output:",
              file=sys.stderr)
        for o in offenders:
            print(f"    {o}", file=sys.stderr)
        return 3

    written: List[str] = []
    for name, text in reports.items():
        p = out / name
        p.write_text(text, encoding="utf-8")
        written.append(str(p))
    if suggested is not None:
        # an explicit guard: the suggested entry must never carry a resolves pointer
        assert "resolves" not in suggested, "suggested entry must not retire debt"
        p = out / "corpus_candidate_entry.json"
        p.write_text(json.dumps(suggested, indent=2), encoding="utf-8")
        written.append(str(p))

    print(f"offline analysis complete — {len(written)} report(s) in {out}")
    print(f"  posture: clean (no signing value in any report)")
    print(f"  corpus: not written; suggested entry "
          f"{'emitted for review' if suggested else 'not applicable'}; "
          f"compat={compat}")
    for w in written:
        print(f"  - {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
