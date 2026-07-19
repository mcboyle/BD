#!/usr/bin/env python3
"""ops_intelligence.py — Phases 7-12 operational intelligence layers.

Six aggregation/governance layers over the Phase 1-6 artifacts. None adds detector
behavior; none drives the browser, replays, reuses signing values, reconstructs
signed URLs, writes the corpus, or retires debt. Every layer consumes existing
artifacts, produces reviewable data, and fails closed on a posture scan.

Subcommands:
  health    Phase 7  — site health scoring + maturity state (explainable)
  trace     Phase 8  — decision trace engine (reconstructable reasoning)
  capqueue  Phase 9  — evidence-driven capture priority queue
  login     Phase 10 — descriptive login workflow profiles (NO credentials)
  workflow  Phase 11 — download workflow-structure profiles (workflow vs selector drift)
  patterns  Phase 12 — cross-site structural pattern families (analysis only)

Each writes to --out-dir. Run `ops_intelligence.py <subcommand> --help` for inputs.
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


# ── shared io + posture ─────────────────────────────────────────────
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
    """Fail-closed posture check shared by all phases. Returns leaks or None."""
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


# ── Phase 7: site health scoring ────────────────────────────────────
_MATURITY = ["Unknown", "Learning", "Stable", "Trusted", "Watch", "Fragile", "Broken"]


def _score_band(v: Optional[float]) -> str:
    if v is None:
        return "unknown"
    if v >= 0.8:
        return "high"
    if v >= 0.5:
        return "moderate"
    return "low"


def cmd_health(args) -> int:
    profile = _load(args.site_profile) or {}
    sel = _load(args.selector_confidence)
    conf_hist = _load(args.confidence_history) or []
    drift_hist = _load(args.drift_history) or []
    decision = _load(args.automation_decision_report) or {}
    debt = _read_debt()

    expl: Dict[str, Any] = {}

    # selector health: avg confidence, penalized by recurring selector drift
    if isinstance(sel, list) and sel:
        avg = sum(float(s.get("confidence", 0)) for s in sel) / len(sel)
        sel_drift = sum(1 for h in drift_hist
                        if "selector_drift" in (h.get("drift_flags") or []))
        selector_health = max(0.0, round(avg - 0.1 * sel_drift, 3))
        expl["selector_health"] = (f"avg selector confidence {round(avg,3)} over {len(sel)} "
                                    f"selectors, minus {sel_drift} selector-drift events")
    elif isinstance(sel, dict) and sel.get("status") == "blocked_no_dom":
        selector_health = None
        expl["selector_health"] = "no DOM logs — selector health undeterminable"
    else:
        selector_health = None
        expl["selector_health"] = "no selector data"

    # rendition health: did renditions stay attributable (no repeated rendition drift)
    rend_drift = sum(1 for h in drift_hist
                     if "rendition_drift" in (h.get("drift_flags") or []))
    rendition_health = round(max(0.0, 1.0 - 0.2 * rend_drift), 3) \
        if profile.get("known_rendition_descriptors") else None
    expl["rendition_health"] = (f"{rend_drift} rendition-drift events; "
                                f"{len(profile.get('known_rendition_descriptors') or [])} "
                                f"known descriptors")

    # profile health: presence + breadth of known descriptors
    fields = sum(1 for f in ("known_rendition_descriptors", "known_identity_descriptors",
                             "known_signing_markers", "known_goal_url_shapes")
                 if profile.get(f))
    profile_health = round(fields / 4, 3)
    expl["profile_health"] = f"{fields}/4 profile descriptor fields populated"

    # drift health: inverse of total drift recurrence
    total_drift = sum(len(h.get("drift_flags") or []) for h in drift_hist)
    drift_health = round(max(0.0, 1.0 - 0.1 * total_drift), 3)
    expl["drift_health"] = f"{total_drift} total drift flags across {len(drift_hist)} runs"

    # validation health: framework-level (read-only debt)
    validation_health = 1.0 if debt.get("validation", 0) == 0 else round(
        max(0.0, 1.0 - 0.25 * debt.get("validation", 0)), 3)
    expl["validation_health"] = (f"{debt.get('validation','?')} open validation-debt items "
                                 f"(framework-level, read-only)")

    # operational health: fewer queued review/capture items = healthier
    q = decision.get("queued", {})
    op_penalty = 0.1 * (q.get("manual_review", 0) + q.get("capture_requests", 0))
    operational_health = round(max(0.0, 1.0 - op_penalty), 3)
    expl["operational_health"] = (f"{q.get('manual_review',0)} review + "
                                  f"{q.get('capture_requests',0)} capture items queued")

    measured = [v for v in (selector_health, rendition_health, profile_health,
                            drift_health, validation_health, operational_health)
                if v is not None]
    overall = round(sum(measured) / len(measured), 3) if measured else None

    # maturity state
    n_runs = len(conf_hist)
    has_repeat_drift = any(
        sum(1 for h in drift_hist if f in (h.get("drift_flags") or [])) >= 2
        for f in {x for h in drift_hist for x in (h.get("drift_flags") or [])})
    structural = any("structural_drift" in (h.get("drift_flags") or []) for h in drift_hist)
    if overall is None or not profile:
        maturity = "Unknown"
    elif structural:
        maturity = "Broken"
    elif has_repeat_drift:
        maturity = "Fragile"
    elif overall >= 0.8 and n_runs >= 2 and not has_repeat_drift:
        maturity = "Trusted"
    elif overall >= 0.8:
        maturity = "Stable"
    elif total_drift > 0:
        maturity = "Watch"
    else:
        maturity = "Learning"

    score = {
        "site": args.site,
        "scores": {"selector_health": selector_health, "rendition_health": rendition_health,
                   "profile_health": profile_health, "drift_health": drift_health,
                   "validation_health": validation_health,
                   "operational_health": operational_health, "overall": overall},
        "bands": {k: _score_band(v) for k, v in
                  {"selector_health": selector_health, "rendition_health": rendition_health,
                   "profile_health": profile_health, "drift_health": drift_health,
                   "validation_health": validation_health,
                   "operational_health": operational_health}.items()},
        "maturity_state": maturity,
        "explanations": expl,
        "_status": "Read-only health scoring. No promotion, no write, no debt change.",
    }
    leaks = _posture_ok(score)
    if leaks:
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "site_health_score.json", score)
    L = [f"# Site health report — {args.site}", "",
         f"**Maturity: {maturity}** (overall {overall}).", "",
         "Every score is explainable:"]
    for k, v in score["scores"].items():
        if k == "overall":
            continue
        L.append(f"- {k}: {v} ({score['bands'].get(k,'?')}) — {expl.get(k,'')}")
    L += ["", f"Maturity rationale: {n_runs} history points, "
          f"{'structural drift present → Broken' if structural else ''}"
          f"{'repeated drift → Fragile' if has_repeat_drift and not structural else ''}"
          f"{'' if (structural or has_repeat_drift) else 'no repeated/structural drift'}."]
    _write_text(out / "site_health_report.md", "\n".join(L))
    print(f"Phase 7 health: {args.site} → {maturity} (overall {overall})")
    return 0


def _read_debt() -> Dict[str, Any]:
    try:
        from bulk_downloader.validation_corpus import debt_report
        r = debt_report()
        return {"correction": len(r["correction_debt"]),
                "capability": len(r["capability_debt"]),
                "validation": len(r["validation_debt"]),
                "validation_items": [d.get("id") for d in r["validation_debt"]]}
    except Exception:
        return {}


# ── Phase 8: decision trace engine ──────────────────────────────────
def cmd_trace(args) -> int:
    """Reconstruct WHY each significant decision occurred, from the artifacts that
    drove it. Reads, never decides anew — it explains decisions already made."""
    match = _load(args.template_match_report) or {}
    decision = _load(args.download_decision_report) or {}
    policy = _load(args.automation_decision_report) or {}
    traces: List[Dict[str, Any]] = []

    if decision:
        traces.append({
            "decision": "rendition_selection",
            "inputs_considered": decision.get("available_renditions_live", []),
            "evidence_used": decision.get("scored", []),
            "confidence": "deterministic (highest live res via detect.res_score)",
            "rule_path": ["score each LIVE rendition", "pick max score",
                          "from current page only — not template, not reconstructed"],
            "final_outcome": decision.get("selected"),
        })
    if match:
        traces.append({
            "decision": "template_match",
            "inputs_considered": ["live identity/renditions/signing-markers/selectors"],
            "evidence_used": match.get("drift_flags", []),
            "confidence": match.get("verdict"),
            "rule_path": ["compare live observation to profile expectations",
                          "flag per-axis drift", "classify verdict"],
            "final_outcome": match.get("verdict"),
        })
    for sd in (policy.get("selector_decisions") or []):
        traces.append({
            "decision": f"selector_policy:{sd.get('selector')}",
            "inputs_considered": ["selector confidence", "selector-drift recurrence"],
            "evidence_used": {"confidence": sd.get("confidence")},
            "confidence": sd.get("confidence"),
            "rule_path": [sd.get("why", "")],
            "final_outcome": sd.get("action"),
        })
    for axis, pol in (policy.get("axis_policy") or {}).items():
        traces.append({
            "decision": f"drift_policy:{axis}",
            "inputs_considered": ["drift history"],
            "evidence_used": {"occurrences": pol.get("occurrences")},
            "confidence": "rule-based",
            "rule_path": [pol.get("hard_rule") or pol.get("decision", "")],
            "final_outcome": pol.get("decision"),
        })

    obj = {"site": args.site, "traces": traces,
           "_status": "Read-only reconstruction. Every decision shows inputs, evidence, "
                      "confidence, rule path, outcome. No hidden decisions; no action taken."}
    leaks = _posture_ok(obj)
    if leaks:
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "decision_trace.json", obj)
    L = [f"# Decision trace report — {args.site}", "",
         "Each significant decision, reconstructable from its inputs:"]
    for t in traces:
        L += [f"## {t['decision']} → {t['final_outcome']}",
              f"- inputs: {t['inputs_considered']}",
              f"- evidence: {t['evidence_used']}",
              f"- confidence: {t['confidence']}",
              f"- rule path: {' → '.join(str(r) for r in t['rule_path'] if r)}", ""]
    _write_text(out / "decision_trace_report.md", "\n".join(L))
    print(f"Phase 8 trace: {args.site} — {len(traces)} decisions reconstructed")
    return 0


# ── Phase 9: capture priority queue ─────────────────────────────────
def cmd_capqueue(args) -> int:
    """Rank sites for fresh capture by uncertainty/repeated-drift/missing-evidence/
    validation relevance/information gain. Explains each priority."""
    sites = []
    root = Path(args.sites_root)
    debt = _read_debt()
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            dh = _load(str(d / "drift_history.json")) or []
            ch = _load(str(d / "confidence_history.json")) or []
            health = _load(str(d / "site_health_score.json")) or {}
            flags: Dict[str, int] = {}
            for h in dh:
                for f in (h.get("drift_flags") or []):
                    flags[f] = flags.get(f, 0) + 1
            repeated = {k: v for k, v in flags.items() if v >= 2}
            n_obs = len(ch)
            overall = (health.get("scores") or {}).get("overall")
            # priority components
            uncertainty = 1.0 if n_obs < 2 else round(1.0 / (n_obs + 1), 3)
            repeat_drift = min(1.0, 0.3 * sum(repeated.values()))
            missing_evidence = 1.0 if not ch else 0.0
            low_health = round(1.0 - overall, 3) if overall is not None else 0.5
            info_gain = round(min(1.0, uncertainty + repeat_drift + 0.2 * missing_evidence), 3)
            score = round(min(1.0, 0.35 * uncertainty + 0.3 * repeat_drift
                              + 0.2 * low_health + 0.15 * missing_evidence), 3)
            sites.append({
                "site": d.name, "priority_score": score,
                "components": {"uncertainty": uncertainty, "repeated_drift": repeat_drift,
                               "missing_evidence": missing_evidence,
                               "low_health": low_health, "information_gain": info_gain},
                "repeated_drift_axes": repeated,
                "explanation": (f"{n_obs} observations, repeated drift {repeated or 'none'}, "
                                f"overall health {overall}; higher score = capture sooner"),
            })
    sites.sort(key=lambda s: -s["priority_score"])
    obj = {"queue": sites,
           "validation_debt_note": (f"{debt.get('validation','?')} open validation-debt "
                                    f"items ({debt.get('validation_items')}) need REAL "
                                    f"perturbation captures — highest framework-level "
                                    f"priority, not retirable synthetically."),
           "_status": "Read-only priority ranking. Captures remain operator-driven."}
    leaks = _posture_ok(obj)
    if leaks:
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "capture_priority_queue.json", obj)
    L = [f"# Capture priority report", "", obj["validation_debt_note"], "",
         "## Sites ranked (highest priority first)"]
    for s in sites:
        L.append(f"- **{s['site']}** (score {s['priority_score']}): {s['explanation']}")
    if not sites:
        L.append("No site artifact dirs found.")
    _write_text(out / "capture_priority_report.md", "\n".join(L))
    print(f"Phase 9 capqueue: ranked {len(sites)} sites")
    return 0


# ── Phase 10: login workflow profiles (descriptive, NO credentials) ──
def cmd_login(args) -> int:
    """Descriptive login workflow profile from login selector/flow artifacts. NEVER
    stores or replays credentials; records STRUCTURE only (selector roles, step
    order, MFA presence, cookie-persistence/session-longevity observations)."""
    flow = _load(args.login_flow) or {}     # from login_flow_recorder, if provided
    drift_hist = _load(args.drift_history) or []
    # descriptive selector roles only — values/credentials never read
    roles = {}
    for r in ("user_field", "pass_field", "submit_btn", "mfa_field", "remember_me"):
        sels = flow.get(r)
        if sels:
            roles[r] = sels if isinstance(sels, list) else [sels]
    profile = {
        "site": args.site,
        "login_selectors_by_role": roles,         # selectors only, no values
        "workflow_steps": flow.get("steps", []),  # ordered step descriptors
        "mfa_present": bool(flow.get("mfa_field") or flow.get("mfa_present")),
        "cookie_persistence_observed": flow.get("cookie_persistence"),
        "session_longevity_note": flow.get("session_longevity"),
        "login_success_rate": flow.get("success_rate"),
        "_status": "DESCRIPTIVE ONLY. No credential is stored or replayed; no session is "
                   "reconstructed. Selector roles and step ORDER only.",
    }
    login_drift = [h for h in drift_hist if "login" in str(h.get("drift_flags") or [])]
    leaks = _posture_ok(profile)
    if leaks:
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "login_profile.json", profile)
    _write_text(out / "login_health_report.md",
                f"# Login health — {args.site}\n\n"
                f"MFA present: {profile['mfa_present']}. "
                f"Cookie persistence observed: {profile['cookie_persistence_observed']}. "
                f"Session longevity: {profile['session_longevity_note']}. "
                f"Success rate: {profile['login_success_rate']}.\n\n"
                f"Descriptive only — no credentials, no replay, no session reconstruction.\n")
    _write_text(out / "login_drift_report.md",
                f"# Login drift — {args.site}\n\nLogin-related drift observations: "
                f"{login_drift or 'none recorded'}.\n")
    print(f"Phase 10 login: {args.site} profiled (descriptive, MFA={profile['mfa_present']})")
    return 0


# ── Phase 11: download workflow profiles (workflow vs selector drift) ─
def cmd_workflow(args) -> int:
    """Workflow STRUCTURE profile — stages and their order, distinct from individual
    selectors. A selector change is NOT automatically a workflow change."""
    flow = _load(args.workflow_flow) or {}
    drift_hist = _load(args.drift_history) or []
    stages = flow.get("stages") or ["navigate", "open_modal", "select_resolution",
                                     "confirm", "initiate_download"]
    profile = {
        "site": args.site,
        "workflow_stages": stages,
        "modal_usage": flow.get("modal_usage"),
        "resolution_selection_flow": flow.get("resolution_flow"),
        "confirmation_flow": flow.get("confirmation_flow"),
        "download_initiation_flow": flow.get("initiation_flow"),
        "_status": "DESCRIPTIVE workflow structure. Not an executable flow. A selector "
                   "change alone does NOT constitute a workflow change.",
    }
    # distinguish selector drift from workflow drift
    sel_drift = sum(1 for h in drift_hist
                    if any(x in (h.get("drift_flags") or [])
                           for x in ("selector_drift", "partial_selector_drift")))
    struct_drift = sum(1 for h in drift_hist
                       if "structural_drift" in (h.get("drift_flags") or []))
    classification = {
        "selector_drift_events": sel_drift,
        "workflow_drift_events": struct_drift,
        "interpretation": ("structural/workflow drift is a STAGE change (a stage appeared, "
                           "vanished, or reordered); selector drift is the same stage with a "
                           "changed locator. They are scored separately — a selector change "
                           "is not promoted to a workflow change."),
    }
    leaks = _posture_ok(profile, classification)
    if leaks:
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "workflow_profile.json", profile)
    _write_text(out / "workflow_health_report.md",
                f"# Workflow health — {args.site}\n\nStages: {stages}\n\n"
                f"Modal usage: {profile['modal_usage']}. "
                f"Descriptive structure only; not executable.\n")
    _write_text(out / "workflow_drift_report.md",
                f"# Workflow drift — {args.site}\n\n"
                f"Selector-drift events: {sel_drift}. Workflow-drift (stage) events: "
                f"{struct_drift}.\n\n{classification['interpretation']}\n")
    print(f"Phase 11 workflow: {args.site} — {len(stages)} stages; "
          f"selector-drift {sel_drift} vs workflow-drift {struct_drift}")
    return 0


# ── Phase 12: cross-site pattern families (analysis only) ────────────
def cmd_patterns(args) -> int:
    """Group sites into structural families by player/CDN/workflow signature. Reuses
    cross_site_selectors.selector_shape for the structural fingerprint. Families are
    for confidence/analysis only — they NEVER change live behavior."""
    try:
        from bulk_downloader.cross_site_selectors import selector_shape
    except Exception:
        def selector_shape(s): return s

    root = Path(args.sites_root)
    PLAYER_MARKERS = {
        "kaltura": ("kaltura", "kwidget", "/p/"),
        "jwplayer": ("jwplayer", "jwplayer.com", "jw-"),
        "videojs": ("video-js", "vjs-", "videojs"),
        "brightcove": ("brightcove", "bcove"),
        "wistia": ("wistia", "wistia_"),
    }
    families: Dict[str, List[str]] = {}
    membership: Dict[str, Dict[str, Any]] = {}
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            prof = _load(str(d / "site_profile.json")) or {}
            sel = _load(str(d / "selector_confidence.json"))
            hay = json.dumps(prof, default=list).lower()
            sels = [s.get("selector", "") for s in sel] if isinstance(sel, list) else []
            hay += " " + " ".join(sels).lower()
            fam = "unknown"
            for name, markers in PLAYER_MARKERS.items():
                if any(m in hay for m in markers):
                    fam = f"{name}_family"
                    break
            # CDN family from goal url shapes
            cdn = None
            for u in (prof.get("known_goal_url_shapes") or []):
                host = str(u).split("/")[2] if "://" in str(u) else ""
                if host:
                    cdn = host.split(".")[-2] if "." in host else host
                    break
            shapes = sorted({selector_shape(s) for s in sels if s})
            families.setdefault(fam, []).append(d.name)
            membership[d.name] = {"player_family": fam, "cdn_hint": cdn,
                                  "selector_shapes": shapes}
    patterns = {
        "families": families,
        "_status": "Analysis/confidence only. Family membership NEVER changes live "
                   "behavior, promotes a selector, or alters a profile. Recognition only.",
    }
    leaks = _posture_ok(patterns, membership)
    if leaks:
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "cross_site_patterns.json", patterns)
    _write_json(out / "site_family_membership.json", membership)
    L = ["# Pattern family report", "",
         "Sites grouped by structural family (player/CDN/selector-shape). For confidence "
         "and analysis only — families never change live behavior.", ""]
    for fam, members in families.items():
        L.append(f"## {fam} ({len(members)})")
        for m in members:
            L.append(f"- {m} (cdn: {membership[m].get('cdn_hint')})")
    _write_text(out / "pattern_family_report.md", "\n".join(L))
    print(f"Phase 12 patterns: {sum(len(v) for v in families.values())} sites → "
          f"{len(families)} families")
    return 0


# ── dispatch ────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Phases 7-12 operational intelligence")
    sub = p.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("health"); h.set_defaults(fn=cmd_health)
    h.add_argument("--site", required=True)
    for a in ("--site-profile", "--selector-confidence", "--confidence-history",
              "--drift-history", "--automation-decision-report"):
        h.add_argument(a, default=None)
    h.add_argument("--out-dir", default="./health")

    t = sub.add_parser("trace"); t.set_defaults(fn=cmd_trace)
    t.add_argument("--site", required=True)
    for a in ("--template-match-report", "--download-decision-report",
              "--automation-decision-report"):
        t.add_argument(a, default=None)
    t.add_argument("--out-dir", default="./trace")

    c = sub.add_parser("capqueue"); c.set_defaults(fn=cmd_capqueue)
    c.add_argument("--sites-root", required=True)
    c.add_argument("--out-dir", default="./capqueue")

    g = sub.add_parser("login"); g.set_defaults(fn=cmd_login)
    g.add_argument("--site", required=True)
    g.add_argument("--login-flow", default=None)
    g.add_argument("--drift-history", default=None)
    g.add_argument("--out-dir", default="./login")

    w = sub.add_parser("workflow"); w.set_defaults(fn=cmd_workflow)
    w.add_argument("--site", required=True)
    w.add_argument("--workflow-flow", default=None)
    w.add_argument("--drift-history", default=None)
    w.add_argument("--out-dir", default="./workflow")

    pat = sub.add_parser("patterns"); pat.set_defaults(fn=cmd_patterns)
    pat.add_argument("--sites-root", required=True)
    pat.add_argument("--out-dir", default="./patterns")
    return p


def run(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(run())
