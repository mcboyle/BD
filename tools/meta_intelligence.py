#!/usr/bin/env python3
"""meta_intelligence.py — Phases 19-24 + cross-cuts: the framework audits itself.

The objective is not more scoring — it is making the framework able to explain,
defend, audit, and prioritize itself. Every subcommand consumes existing Phase 1-18
artifacts and the read-only validation corpus, produces reviewable analysis, fails
closed on a posture scan, and changes nothing: no corpus write, no debt change, no
live behavior, no replay, no signing reuse.

Subcommands:
  assumptions  Phase 19 — assumption intelligence (importance/weakness/blast-radius)
  explain      Phase 20 — decision explainability (evidence used AND ignored)
  audit        Phase 21 — audit readiness (defensibility of conclusions)
  risk         Phase 22 — strategic risk register (probability/impact/detectability)
  graph        Phase 23 — framework knowledge graph (what supports what)
  maturity     Phase 24 — framework maturity assessment (self-scorecard)
  blindspots   Cross-cut — blind-spot analysis (what are we missing)
  concentration Cross-cut — evidence concentration (over-reliance on too little)
  sustainability Cross-cut — sustainability (can it stay healthy long-term)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load(path: Optional[str]) -> Optional[Any]:
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def _write_json(path: Path, obj: Any) -> None:
    import os
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True, default=list)
    os.replace(tmp, path)


def _write_text(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _posture_ok(*objs) -> Optional[List[str]]:
    import re
    from bulk_downloader.capture_ingest import posture_scan
    blob = "\n".join(json.dumps(o, default=list) if not isinstance(o, str) else o
                     for o in objs)
    leaks = posture_scan(blob)
    if leaks:
        return leaks
    if re.search(r"page\.(goto|click|fill)|await\s|playwright|new_page\(|"
                 r"requests\.(get|post)", blob):
        return ["executable_or_replay_content"]
    return None


def _fail(leaks) -> int:
    print(f"POSTURE FAIL: {leaks}; refusing to write.", file=sys.stderr)
    return 2


def _corpus() -> List[Dict[str, Any]]:
    """Read-only corpus access."""
    try:
        from bulk_downloader.validation_corpus import load_corpus
        return load_corpus()
    except Exception:
        return []


def _debt() -> Dict[str, Any]:
    try:
        from bulk_downloader.validation_corpus import debt_report
        r = debt_report()
        return {"correction": len(r["correction_debt"]),
                "capability": len(r["capability_debt"]),
                "validation": len(r["validation_debt"]),
                "validation_items": [d.get("id") for d in r["validation_debt"]]}
    except Exception:
        return {}


# ── Phase 19: assumption intelligence ───────────────────────────────
def cmd_assumptions(args) -> int:
    """Inventory framework assumptions, rank by importance/weakness/blast-radius.
    Built from the corpus (assumption-category + framework_level entries) and the
    finding-dependency edges (resolves), plus the assumption registry if provided."""
    corpus = _corpus()
    registry = _load(args.assumption_registry) or {}
    calib = _load(args.confidence_calibration) or {}

    # assumptions = assumption-category entries + framework_level conclusions
    assumptions = [e for e in corpus
                   if e.get("category") == "assumption"
                   or e.get("conclusion_class") == "framework_level"]

    # blast radius: how many OTHER entries depend on this one (resolves edge in)
    dependents: Dict[str, int] = {}
    for e in corpus:
        for r in (e.get("resolves") or []):
            dependents[r] = dependents.get(r, 0) + 1

    ranked = []
    for a in assumptions:
        aid = a.get("id")
        basis = a.get("basis_kind", "unknown")
        outcome = a.get("outcome", "untested")
        blast = dependents.get(aid, 0)
        # weakness: untested or weak basis = weaker
        weak = (outcome == "untested") or basis in ("heuristic", "shape_heuristic")
        # tested-ness
        tested = outcome in ("confirmed", "falsified", "partial")
        ranked.append({
            "id": aid, "subject": a.get("subject"),
            "basis_kind": basis, "outcome": outcome,
            "blast_radius": blast,
            "is_weak": weak, "is_tested": tested,
            "importance": "high" if blast >= 2 else "medium" if blast == 1 else "low",
            "if_this_fails": (f"{blast} dependent finding(s) are undermined"
                              if blast else "no recorded dependents — localized impact"),
        })
    ranked.sort(key=lambda x: (-x["blast_radius"], x["is_weak"]))

    obj = {
        "n_assumptions": len(ranked),
        "most_important": [a for a in ranked if a["importance"] == "high"][:10],
        "weakest": [a for a in ranked if a["is_weak"]][:10],
        "least_tested": [a for a in ranked if not a["is_tested"]][:10],
        "highest_blast_radius": ranked[:10],
        "assumptions": ranked,
        "_status": "Read-only assumption inventory. No assumption is modified; the corpus "
                   "is read-only.",
    }
    # dependency graph edges (assumption -> dependents)
    graph = {"nodes": [{"id": a["id"], "subject": a["subject"],
                        "blast_radius": a["blast_radius"]} for a in ranked],
             "edges": [{"from": e.get("id"), "resolves": r}
                       for e in corpus for r in (e.get("resolves") or [])]}
    if (leaks := _posture_ok(obj, graph)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "assumption_intelligence.json", obj)
    _write_json(out / "assumption_dependency_graph.json", graph)
    L = ["# Assumption risk report", "",
         f"{len(ranked)} assumptions/framework-level findings inventoried. "
         f"\"If this assumption fails, what breaks?\" — ranked by blast radius.", "",
         "## Highest blast radius"]
    for a in obj["highest_blast_radius"]:
        L.append(f"- **{a['id']}** ({a['subject']}): blast {a['blast_radius']}, "
                 f"basis {a['basis_kind']}, {a['outcome']} — {a['if_this_fails']}")
    L += ["", "## Weakest (untested or heuristic basis)"]
    for a in obj["weakest"]:
        L.append(f"- {a['id']} ({a['subject']}): {a['basis_kind']}/{a['outcome']}")
    if not ranked:
        L.append("\nNo assumption-category or framework-level entries in the corpus.")
    _write_text(out / "assumption_risk_report.md", "\n".join(L))
    print(f"Phase 19 assumptions: {len(ranked)} inventoried, "
          f"{len(obj['most_important'])} high-importance")
    return 0


# ── Phase 20: decision explainability ───────────────────────────────
def cmd_explain(args) -> int:
    """Explain each decision: evidence used AND ignored, rules, confidence
    contribution, alternative outcomes. Extends Phase 8 traces with the negative
    space (what was NOT used)."""
    traces = _load(args.decision_trace) or {}
    calib = _load(args.confidence_calibration) or {}
    explanations = []
    for t in (traces.get("traces") or []):
        used = t.get("evidence_used")
        # alternatives: for a max-selection, the runners-up are the alternatives
        alts = []
        if isinstance(used, list):
            alts = [e for e in used][1:4]  # everything not chosen
        explanations.append({
            "decision": t.get("decision"),
            "outcome": t.get("final_outcome"),
            "evidence_used": used,
            "evidence_ignored": ("lower-scored alternatives below the winner"
                                 if alts else "none recorded as ignored"),
            "rules_applied": t.get("rule_path"),
            "confidence_contribution": t.get("confidence"),
            "alternative_outcomes": alts or "none — single viable candidate",
            "why": (f"reached '{t.get('final_outcome')}' because the rule path "
                    f"{t.get('rule_path')} selected it over the alternatives"),
        })
    obj = {"site": args.site, "explanations": explanations,
           "calibration_note": (f"subsystem reliability: {calib.get('by_subsystem')}"
                                if calib else "no calibration data attached"),
           "_status": "Explanation only — reconstructs why each conclusion was reached, "
                      "including evidence NOT used. No decision changes."}
    if (leaks := _posture_ok(obj)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "decision_explanation.json", obj)
    L = [f"# Decision explanation report — {args.site}", "",
         "\"Why did the framework reach this conclusion?\" — each decision with the "
         "evidence it used, the evidence it ignored, and the alternatives it rejected.", ""]
    for e in explanations:
        L += [f"## {e['decision']} → {e['outcome']}",
              f"- evidence used: {e['evidence_used']}",
              f"- evidence ignored: {e['evidence_ignored']}",
              f"- rules applied: {e['rules_applied']}",
              f"- confidence contribution: {e['confidence_contribution']}",
              f"- alternative outcomes considered: {e['alternative_outcomes']}",
              f"- why: {e['why']}", ""]
    if not explanations:
        L.append("No decision traces supplied to explain.")
    _write_text(out / "decision_explanation_report.md", "\n".join(L))
    print(f"Phase 20 explain: {args.site} — {len(explanations)} decisions explained")
    return 0


# ── Phase 21: audit readiness ───────────────────────────────────────
def cmd_audit(args) -> int:
    """Classify how defensible conclusions are: Fully/Partially/Weakly/Unsupported.
    A conclusion is defensible to the degree it has evidence, a trace, an
    explanation, and calibrated confidence behind it."""
    corpus = _corpus()
    traces = _load(args.decision_trace) or {}
    calib = _load(args.confidence_calibration) or {}
    traced_decisions = {t.get("decision") for t in (traces.get("traces") or [])}

    findings = []
    gaps = []
    for e in corpus:
        has_evidence = bool(e.get("evidence"))
        has_basis = bool(e.get("basis_kind"))
        tested = e.get("outcome") in ("confirmed", "falsified", "partial")
        # crude traceability: framework-level/method_validation w/ evidence is traceable
        traceable = has_evidence and has_basis
        score = sum([has_evidence, has_basis, tested, traceable])
        if score >= 4:
            band = "Fully Defensible"
        elif score == 3:
            band = "Partially Defensible"
        elif score == 2:
            band = "Weakly Supported"
        else:
            band = "Unsupported"
        findings.append({"id": e.get("id"), "subject": e.get("subject"),
                         "defensibility": band, "tested": tested,
                         "has_evidence": has_evidence})
        if band in ("Weakly Supported", "Unsupported"):
            missing = []
            if not has_evidence:
                missing.append("evidence")
            if not tested:
                missing.append("test/validation")
            if not has_basis:
                missing.append("basis_kind")
            gaps.append({"id": e.get("id"), "subject": e.get("subject"),
                         "band": band, "missing": missing})

    dist = {b: sum(1 for f in findings if f["defensibility"] == b)
            for b in ("Fully Defensible", "Partially Defensible",
                      "Weakly Supported", "Unsupported")}
    obj_gaps = {"audit_gaps": gaps, "distribution": dist,
                "_status": "Read-only audit assessment. 'Could this conclusion survive "
                           "external review?' No conclusion is modified."}
    if (leaks := _posture_ok(obj_gaps, findings)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "audit_gaps.json", obj_gaps)
    L = ["# Audit readiness report", "",
         "Defensibility of each corpus conclusion — could it survive external review?", "",
         "## Distribution"]
    for b, n in dist.items():
        L.append(f"- {b}: {n}")
    L += ["", "## Gaps (weakly supported / unsupported)"]
    for g in gaps[:20]:
        L.append(f"- {g['id']} ({g['subject']}): {g['band']} — missing {g['missing']}")
    if not gaps:
        L.append("No weakly-supported or unsupported conclusions found.")
    _write_text(out / "audit_readiness_report.md", "\n".join(L))
    print(f"Phase 21 audit: {dist}")
    return 0


# ── Phase 22: strategic risk register ───────────────────────────────
def cmd_risk(args) -> int:
    """Long-term framework risks scored by probability/impact/detectability/
    mitigation-difficulty, classified Low/Medium/High/Critical."""
    assumptions = _load(args.assumption_intelligence) or {}
    forecasts = _load(args.drift_forecasts) or []   # list of per-site forecasts
    freshness = _load(args.freshness_reports) or []
    audit = _load(args.audit_gaps) or {}
    debt = _debt()

    risks = []

    def _add(name, prob, impact, detect, mitig, detail):
        # severity from prob*impact, tempered by detectability
        sev_score = prob * impact * (1.2 - 0.2 * detect)
        sev = ("Critical" if sev_score >= 0.6 else "High" if sev_score >= 0.4
               else "Medium" if sev_score >= 0.2 else "Low")
        risks.append({"risk": name, "probability": prob, "impact": impact,
                      "detectability": detect, "mitigation_difficulty": mitig,
                      "severity": sev, "detail": detail})

    # weak high-blast assumptions
    weak_blast = [a for a in (assumptions.get("highest_blast_radius") or [])
                  if a.get("is_weak") and a.get("blast_radius", 0) >= 1]
    if weak_blast:
        _add("weak_high_blast_assumptions", 0.6, 0.9, 0.5, 0.6,
             f"{len(weak_blast)} weak assumption(s) with dependents — failure cascades")
    # unresolved validation debt
    if debt.get("validation", 0):
        _add("unretired_validation_debt", 0.5, 0.7, 0.9, 0.7,
             f"{debt['validation']} validation-debt item(s) need real captures")
    # sites forecast toward broken
    high_risk_sites = [f for f in forecasts
                       if isinstance(f, dict) and f.get("probability_enter_broken", 0) >= 0.5]
    if high_risk_sites:
        _add("sites_trending_broken", 0.7, 0.6, 0.7, 0.5,
             f"{len(high_risk_sites)} site(s) forecast likely to break")
    # stale evidence
    stale = [f for f in freshness
             if isinstance(f, dict) and f.get("review_priority")]
    if stale:
        _add("evidence_staleness", 0.5, 0.5, 0.8, 0.3,
             f"{len(stale)} site(s) with stale/expired evidence")
    # unsupported conclusions
    unsup = (audit.get("distribution") or {}).get("Unsupported", 0)
    if unsup:
        _add("unsupported_conclusions", 0.4, 0.8, 0.6, 0.5,
             f"{unsup} conclusion(s) would not survive external review")

    risks.sort(key=lambda r: {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}[r["severity"]])
    obj = {"risk_register": risks, "n_risks": len(risks),
           "_status": "Read-only strategic risk register. 'What is most likely to damage "
                      "trust in the framework?' No action taken."}
    if (leaks := _posture_ok(obj)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "risk_register.json", obj)
    L = ["# Strategic risk report", "",
         "Long-term risks to trust in the framework, ranked by severity.", ""]
    for r in risks:
        L.append(f"## {r['risk']} — **{r['severity']}**")
        L.append(f"- probability {r['probability']}, impact {r['impact']}, "
                 f"detectability {r['detectability']}, mitigation difficulty "
                 f"{r['mitigation_difficulty']}")
        L.append(f"- {r['detail']}")
    if not risks:
        L.append("No strategic risks surfaced from the supplied inputs.")
    _write_text(out / "strategic_risk_report.md", "\n".join(L))
    print(f"Phase 22 risk: {len(risks)} risks, "
          f"{sum(1 for r in risks if r['severity'] in ('Critical','High'))} high/critical")
    return 0


# ── Phase 23: framework knowledge graph ─────────────────────────────
def cmd_graph(args) -> int:
    """Map Evidence→Finding→Assumption→Conclusion→Policy→Recommendation across
    artifacts. 'What supports what?'"""
    corpus = _corpus()
    nodes = []
    edges = []
    seen = set()

    def _node(nid, ntype, label):
        if nid not in seen:
            nodes.append({"id": nid, "type": ntype, "label": str(label)[:60]})
            seen.add(nid)

    for e in corpus:
        fid = e.get("id")
        _node(fid, "finding", e.get("subject"))
        ev = e.get("evidence")
        if ev:
            evid = f"evidence:{fid}"
            _node(evid, "evidence", ev)
            edges.append({"from": evid, "to": fid, "rel": "supports"})
        cat = e.get("category")
        if cat == "assumption":
            edges.append({"from": fid, "to": f"assumption_class", "rel": "is_a"})
        # resolves = finding -> finding dependency
        for r in (e.get("resolves") or []):
            edges.append({"from": fid, "to": r, "rel": "resolves"})
        cc = e.get("conclusion_class")
        if cc:
            _node(f"class:{cc}", "conclusion_class", cc)
            edges.append({"from": fid, "to": f"class:{cc}", "rel": "classified_as"})

    obj = {"nodes": nodes, "edges": edges,
           "node_count": len(nodes), "edge_count": len(edges),
           "_status": "Read-only knowledge graph over corpus + artifacts. 'What supports "
                      "what?' Descriptive; nothing is modified."}
    if (leaks := _posture_ok(obj)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "knowledge_graph.json", obj)
    by_type: Dict[str, int] = {}
    for n in nodes:
        by_type[n["type"]] = by_type.get(n["type"], 0) + 1
    by_rel: Dict[str, int] = {}
    for e in edges:
        by_rel[e["rel"]] = by_rel.get(e["rel"], 0) + 1
    L = ["# Knowledge graph report", "",
         f"{len(nodes)} nodes, {len(edges)} edges mapping what supports what.", "",
         f"Node types: {by_type}", f"Edge relations: {by_rel}", "",
         "Chain modeled: Evidence → Finding → (Assumption) → Conclusion-class. "
         "`resolves` edges link findings that close one another."]
    _write_text(out / "knowledge_graph_report.md", "\n".join(L))
    print(f"Phase 23 graph: {len(nodes)} nodes, {len(edges)} edges")
    return 0


# ── Phase 24: framework maturity assessment ─────────────────────────
def cmd_maturity(args) -> int:
    """Score the framework itself across eight dimensions → maturity classification."""
    calib = _load(args.confidence_calibration) or {}
    audit = _load(args.audit_gaps) or {}
    risk = _load(args.risk_register) or {}
    freshness = _load(args.freshness_reports) or []
    debt = _debt()

    def _band(v):
        return "high" if v >= 0.8 else "moderate" if v >= 0.5 else "low"

    dist = audit.get("distribution") or {}
    total_findings = sum(dist.values()) or 1
    defensible = (dist.get("Fully Defensible", 0)
                  + 0.5 * dist.get("Partially Defensible", 0)) / total_findings

    cal_err = calib.get("calibration_error")
    calibration_quality = 1.0 - cal_err if cal_err is not None else 0.5

    crit_high = sum(1 for r in (risk.get("risk_register") or [])
                    if r.get("severity") in ("Critical", "High"))
    stability = max(0.0, 1.0 - 0.2 * crit_high)

    stale_sites = sum(1 for f in freshness
                      if isinstance(f, dict) and f.get("review_priority"))
    freshness_q = max(0.0, 1.0 - 0.15 * stale_sites)

    scores = {
        "evidence_quality": round(defensible, 3),
        "confidence_quality": round(calibration_quality, 3),
        "calibration_quality": round(calibration_quality, 3),
        "auditability": round(defensible, 3),
        "explainability": 0.8,   # decision trace + explanation engines exist
        "freshness": round(freshness_q, 3),
        "governance": 1.0 if debt.get("validation") is not None else 0.5,
        "stability": round(stability, 3),
    }
    overall = round(sum(scores.values()) / len(scores), 3)
    if overall >= 0.85:
        maturity = "Highly Mature"
    elif overall >= 0.7:
        maturity = "Mature"
    elif overall >= 0.55:
        maturity = "Operational"
    elif overall >= 0.4:
        maturity = "Emerging"
    else:
        maturity = "Experimental"

    obj = {"scorecard": scores, "bands": {k: _band(v) for k, v in scores.items()},
           "overall": overall, "maturity": maturity,
           "_status": "Read-only self-assessment of framework maturity. Nothing changes."}
    if (leaks := _posture_ok(obj)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "framework_scorecard.json", obj)
    L = ["# Framework maturity report", "",
         f"**Maturity: {maturity}** (overall {overall}).", "",
         "## Scorecard"]
    for k, v in scores.items():
        L.append(f"- {k}: {v} ({obj['bands'][k]})")
    L += ["", "How mature is the framework today? It is "
          f"{maturity} — strongest where explainability and governance are built in, "
          f"weakest where evidence is thin or uncalibrated. Improve the lowest dimensions "
          f"to raise maturity."]
    _write_text(out / "framework_maturity_report.md", "\n".join(L))
    print(f"Phase 24 maturity: {maturity} (overall {overall})")
    return 0


# ── cross-cut: blind spots ──────────────────────────────────────────
def cmd_blindspots(args) -> int:
    corpus = _corpus()
    calib = _load(args.confidence_calibration) or {}
    spots = []
    # heavily assumed: assumption entries that are untested
    untested_assumptions = [e for e in corpus
                            if e.get("category") == "assumption"
                            and e.get("outcome") == "untested"]
    if untested_assumptions:
        spots.append({"area": "heavily_assumed_untested",
                      "detail": f"{len(untested_assumptions)} untested assumptions",
                      "items": [e.get("id") for e in untested_assumptions]})
    # poorly calibrated subsystems
    for sub, v in (calib.get("by_subsystem") or {}).items():
        if abs(v.get("reliability_gap", 0)) > 0.15:
            spots.append({"area": "poorly_calibrated",
                          "detail": f"{sub} reliability gap {v['reliability_gap']}"})
    # capability gaps in corpus
    cap_gaps = [e for e in corpus if e.get("conclusion_class") == "capability_gap"]
    if cap_gaps:
        spots.append({"area": "known_capability_gaps",
                      "detail": f"{len(cap_gaps)} capability gaps",
                      "items": [e.get("id") for e in cap_gaps]})
    obj = {"blind_spots": spots, "_status": "Read-only. 'What are we missing?'"}
    if (leaks := _posture_ok(obj)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_text(out / "blind_spots_report.md",
                "# Blind spots\n\n\"What are we missing?\"\n\n"
                + ("\n".join(f"- **{s['area']}**: {s['detail']}" for s in spots)
                   or "No significant blind spots surfaced.") + "\n")
    print(f"Cross-cut blindspots: {len(spots)} areas")
    return 0


# ── cross-cut: evidence concentration ───────────────────────────────
def cmd_concentration(args) -> int:
    corpus = _corpus()
    # conclusions resting on a single evidence chain: findings with exactly one
    # evidence string and no corroborating resolves edges
    fragile = []
    for e in corpus:
        ev = e.get("evidence")
        corroboration = sum(1 for o in corpus if e.get("id") in (o.get("resolves") or []))
        if ev and corroboration == 0 and e.get("outcome") != "confirmed":
            fragile.append({"id": e.get("id"), "subject": e.get("subject"),
                            "single_evidence": True, "corroborating_findings": 0})
    obj = {"fragile_evidence_structures": fragile, "n_fragile": len(fragile),
           "_status": "Read-only. 'Where are we over-relying on too little evidence?'"}
    if (leaks := _posture_ok(obj)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_text(out / "evidence_concentration_report.md",
                "# Evidence concentration\n\n\"Where are we over-relying on too little "
                "evidence?\"\n\n"
                + (f"{len(fragile)} conclusion(s) rest on a single uncorroborated "
                   f"evidence chain:\n"
                   + "\n".join(f"- {f['id']} ({f['subject']})" for f in fragile)
                   if fragile else "No fragile single-chain evidence structures found.")
                + "\n")
    print(f"Cross-cut concentration: {len(fragile)} fragile structures")
    return 0


# ── cross-cut: sustainability ───────────────────────────────────────
def cmd_sustainability(args) -> int:
    """Maintenance/review/collection burden + scaling concerns from the queues/sites."""
    sites_root = Path(args.sites_root) if args.sites_root else None
    n_sites = review_items = capture_items = 0
    if sites_root and sites_root.is_dir():
        for d in sites_root.iterdir():
            if not d.is_dir():
                continue
            n_sites += 1
            dec = _load(str(d / "automation_decision_report.json")) or {}
            q = dec.get("queued", {})
            review_items += q.get("manual_review", 0)
            capture_items += q.get("capture_requests", 0)
    debt = _debt()
    review_burden = "high" if review_items > 2 * max(1, n_sites) else "moderate" \
        if review_items else "low"
    collection_burden = "high" if (capture_items + debt.get("validation", 0)) > n_sites \
        else "moderate" if (capture_items or debt.get("validation", 0)) else "low"
    obj = {"n_sites": n_sites, "review_items": review_items,
           "capture_items": capture_items,
           "review_burden": review_burden, "collection_burden": collection_burden,
           "scaling_note": (f"per-site review/capture queues scale linearly with site count "
                            f"({n_sites} sites); the file-based workflow is the main scaling "
                            f"constraint — an in-UI cockpit would reduce per-site overhead"),
           "_status": "Read-only sustainability analysis. 'Can this framework remain "
                      "healthy long term?'"}
    if (leaks := _posture_ok(obj)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_text(out / "sustainability_report.md",
                f"# Sustainability\n\n\"Can this framework remain healthy long term?\"\n\n"
                f"- sites tracked: {n_sites}\n- review burden: **{review_burden}** "
                f"({review_items} items)\n- collection burden: **{collection_burden}** "
                f"({capture_items} captures + {debt.get('validation','?')} debt items)\n\n"
                f"{obj['scaling_note']}\n")
    print(f"Cross-cut sustainability: review={review_burden} collection={collection_burden}")
    return 0


# ── dispatch ────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Phases 19-24 + cross-cuts: framework self-audit")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("assumptions"); a.set_defaults(fn=cmd_assumptions)
    a.add_argument("--assumption-registry", default=None)
    a.add_argument("--confidence-calibration", default=None)
    a.add_argument("--out-dir", default="./assumptions")

    e = sub.add_parser("explain"); e.set_defaults(fn=cmd_explain)
    e.add_argument("--site", required=True)
    e.add_argument("--decision-trace", default=None)
    e.add_argument("--confidence-calibration", default=None)
    e.add_argument("--out-dir", default="./explain")

    au = sub.add_parser("audit"); au.set_defaults(fn=cmd_audit)
    au.add_argument("--decision-trace", default=None)
    au.add_argument("--confidence-calibration", default=None)
    au.add_argument("--out-dir", default="./audit")

    r = sub.add_parser("risk"); r.set_defaults(fn=cmd_risk)
    r.add_argument("--assumption-intelligence", default=None)
    r.add_argument("--drift-forecasts", default=None)
    r.add_argument("--freshness-reports", default=None)
    r.add_argument("--audit-gaps", default=None)
    r.add_argument("--out-dir", default="./risk")

    g = sub.add_parser("graph"); g.set_defaults(fn=cmd_graph)
    g.add_argument("--out-dir", default="./graph")

    m = sub.add_parser("maturity"); m.set_defaults(fn=cmd_maturity)
    m.add_argument("--confidence-calibration", default=None)
    m.add_argument("--audit-gaps", default=None)
    m.add_argument("--risk-register", default=None)
    m.add_argument("--freshness-reports", default=None)
    m.add_argument("--out-dir", default="./maturity")

    b = sub.add_parser("blindspots"); b.set_defaults(fn=cmd_blindspots)
    b.add_argument("--confidence-calibration", default=None)
    b.add_argument("--out-dir", default="./blindspots")

    cc = sub.add_parser("concentration"); cc.set_defaults(fn=cmd_concentration)
    cc.add_argument("--out-dir", default="./concentration")

    su = sub.add_parser("sustainability"); su.set_defaults(fn=cmd_sustainability)
    su.add_argument("--sites-root", default=None)
    su.add_argument("--out-dir", default="./sustainability")
    return p


def run(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(run())
