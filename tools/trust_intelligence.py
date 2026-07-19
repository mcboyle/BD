#!/usr/bin/env python3
"""trust_intelligence.py — Phases 13-18 + cross-cutting trust/governance views.

The objective is trust, not capability. Every subcommand consumes existing Phase
1-12 artifacts and answers a trust question — how sure are we, why, when to stop
being sure, what evidence matters, what to review, what will fail next. None acts:
no live behavior, no browser, no replay, no signing reuse, no corpus or debt write.
Each fails closed on a posture scan before writing.

Subcommands:
  calibrate   Phase 13 — confidence calibration (is 0.80 actually ~80% correct?)
  regress     Phase 14 — regression of VERDICTS across versions
  capquality  Phase 15 — capture quality scoring (before evidence enters pipeline)
  forecast    Phase 16 — drift forecasting (predict instability before failure)
  simulate    Phase 17 — policy-impact simulation (preview approvals, apply nothing)
  freshness   Phase 18 — evidence freshness & aging
  failures    Cross-cut — failure intelligence (classify failure types)
  impact      Cross-cut — evidence impact analysis (Low/Medium/High/Critical)
  family      Cross-cut — family-level intelligence (extends Phase 12)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(v) -> Optional[datetime]:
    if v is None:
        return None
    try:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, timezone.utc)
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (ValueError, OSError):
        return None


def _live_outcome(site: str) -> Dict[str, Any]:
    """Read-only live success/failure signal from selector_drift."""
    try:
        from bulk_downloader.selector_drift import status_for
        s = status_for(site)
        return {"consecutive_failures": s.get("consecutive_failures", 0),
                "flagged_stale": s.get("flagged_stale", False),
                "last_success_ts": s.get("last_success_ts"),
                "last_failure_ts": s.get("last_failure_ts")}
    except Exception:
        return {}


# ── Phase 13: confidence calibration ────────────────────────────────
def _bucket(c: float) -> str:
    lo = int(c * 10) * 10
    return f"{lo}-{lo+10}%"


def cmd_calibrate(args) -> int:
    """Compare PREDICTED confidence to OBSERVED success. Observations come from an
    explicit outcomes file (selector -> {predicted, success_bool}) and/or the live
    selector_drift signal. Analysis only — changes no score."""
    outcomes = _load(args.outcomes) or []   # [{subsystem, predicted, success}]
    sel = _load(args.selector_confidence)
    # if no explicit outcomes, derive a coarse signal from live drift per selector
    if not outcomes and isinstance(sel, list):
        live = _live_outcome(args.site)
        ok = not live.get("flagged_stale") and live.get("consecutive_failures", 0) == 0
        outcomes = [{"subsystem": "selector", "predicted": float(s.get("confidence", 0)),
                     "success": ok} for s in sel]

    by_bucket: Dict[str, Dict[str, int]] = {}
    by_subsystem: Dict[str, Dict[str, float]] = {}
    over = under = 0
    n = 0
    sse = 0.0
    for o in outcomes:
        p = float(o.get("predicted", 0))
        succ = 1 if o.get("success") else 0
        sub = o.get("subsystem", "unknown")
        b = _bucket(p)
        bk = by_bucket.setdefault(b, {"n": 0, "correct": 0})
        bk["n"] += 1
        bk["correct"] += succ
        ss = by_subsystem.setdefault(sub, {"sum_pred": 0.0, "sum_succ": 0.0, "n": 0})
        ss["sum_pred"] += p
        ss["sum_succ"] += succ
        ss["n"] += 1
        sse += (p - succ) ** 2
        n += 1
        if p - succ > 0.15:
            over += 1
        elif succ - p > 0.15:
            under += 1

    bucket_report = {}
    cal_error = 0.0
    for b, v in by_bucket.items():
        observed = round(v["correct"] / v["n"], 3) if v["n"] else 0
        mid = (int(b.split("-")[0]) + 5) / 100
        bucket_report[b] = {"predicted_mid": mid, "observed_rate": observed,
                            "n": v["n"], "gap": round(observed - mid, 3)}
        cal_error += abs(observed - mid) * v["n"]
    cal_error = round(cal_error / n, 3) if n else None
    brier = round(sse / n, 3) if n else None

    subsystem_reliability = {
        sub: {"avg_predicted": round(v["sum_pred"] / v["n"], 3),
              "avg_observed": round(v["sum_succ"] / v["n"], 3),
              "reliability_gap": round(v["sum_succ"] / v["n"] - v["sum_pred"] / v["n"], 3),
              "n": v["n"]}
        for sub, v in by_subsystem.items() if v["n"]
    }

    obj = {
        "site": args.site, "n_observations": n,
        "calibration_error": cal_error, "brier_score": brier,
        "overconfidence_rate": round(over / n, 3) if n else None,
        "underconfidence_rate": round(under / n, 3) if n else None,
        "by_confidence_bucket": bucket_report,
        "by_subsystem": subsystem_reliability,
        "_status": "Analysis only — no score is changed. Calibration measures whether "
                   "predicted confidence matches observed success.",
        "_caveat": ("Calibration needs real outcome data to be meaningful. With few "
                    "observations or only a coarse live-drift signal, treat as indicative."),
    }
    if (leaks := _posture_ok(obj)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "confidence_calibration.json", obj)
    L = [f"# Confidence accuracy report — {args.site}", "",
         f"Observations: {n}. Calibration error: {cal_error}. Brier: {brier}.",
         f"Overconfidence rate: {obj['overconfidence_rate']}; "
         f"underconfidence rate: {obj['underconfidence_rate']}.", "",
         "## When confidence says X, how often correct?"]
    for b, v in sorted(bucket_report.items()):
        L.append(f"- predicted ~{int(v['predicted_mid']*100)}%: observed "
                 f"{int(v['observed_rate']*100)}% (n={v['n']}, gap {v['gap']})")
    L += ["", "## Reliability by subsystem"]
    for sub, v in subsystem_reliability.items():
        L.append(f"- {sub}: predicts {v['avg_predicted']}, observes {v['avg_observed']} "
                 f"(gap {v['reliability_gap']}, n={v['n']})")
    L.append(f"\n{obj['_caveat']}")
    _write_text(out / "confidence_accuracy_report.md", "\n".join(L))
    # over/under split reports
    _write_text(out / "overconfidence_report.md",
                f"# Overconfidence — {args.site}\n\nRate: {obj['overconfidence_rate']}. "
                f"Buckets where observed << predicted:\n" +
                "\n".join(f"- ~{int(v['predicted_mid']*100)}%: observed "
                          f"{int(v['observed_rate']*100)}% (gap {v['gap']})"
                          for v in bucket_report.values() if v["gap"] < -0.1))
    _write_text(out / "underconfidence_report.md",
                f"# Underconfidence — {args.site}\n\nRate: {obj['underconfidence_rate']}. "
                f"Buckets where observed >> predicted:\n" +
                "\n".join(f"- ~{int(v['predicted_mid']*100)}%: observed "
                          f"{int(v['observed_rate']*100)}% (gap {v['gap']})"
                          for v in bucket_report.values() if v["gap"] > 0.1))
    print(f"Phase 13 calibrate: {args.site} cal_error={cal_error} over={obj['overconfidence_rate']}")
    return 0


# ── Phase 14: regression of verdicts ────────────────────────────────
def cmd_regress(args) -> int:
    """Compare PAST verdicts to CURRENT verdicts. Inputs are two snapshots of the
    same site's verdict-bearing artifacts (health/selector-policy/maturity). Reports
    what changed and flags it for human judgement — regression testing for conclusions."""
    old = _load(args.old_snapshot) or {}
    new = _load(args.new_snapshot) or {}
    changes = []
    for key in ("maturity_state", "overall", "verdict"):
        ov, nv = old.get(key), new.get(key)
        if ov != nv and (ov is not None or nv is not None):
            changes.append({"field": key, "was": ov, "now": nv,
                            "expected": None,
                            "note": "verdict changed across snapshots — confirm intended"})
    # selector decisions
    old_sel = {s.get("selector"): s.get("action")
               for s in (old.get("selector_decisions") or [])}
    new_sel = {s.get("selector"): s.get("action")
               for s in (new.get("selector_decisions") or [])}
    for sel in set(old_sel) | set(new_sel):
        if old_sel.get(sel) != new_sel.get(sel):
            changes.append({"field": f"selector_policy:{sel}",
                            "was": old_sel.get(sel), "now": new_sel.get(sel),
                            "note": "selector trust decision changed"})
    # drift classification sets
    old_d = set(old.get("drift_flags") or [])
    new_d = set(new.get("drift_flags") or [])
    if old_d != new_d:
        changes.append({"field": "drift_flags",
                        "was": sorted(old_d), "now": sorted(new_d),
                        "note": "drift classification changed"})

    obj = {"site": args.site, "verdict_changes": changes,
           "n_changes": len(changes),
           "_status": "Read-only verdict regression. Flags conclusion changes for human "
                      "review; applies nothing. A change is not necessarily wrong — it "
                      "must be judged expected or not."}
    if (leaks := _posture_ok(obj)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "verdict_drift_history.json", obj)
    _write_json(out / "verdict_change_queue.json",
                {"site": args.site, "to_review": changes})
    L = [f"# Verdict regression report — {args.site}", "",
         f"{len(changes)} verdict change(s) between snapshots. Each needs a human to "
         f"confirm whether the change was expected (improved analyzer) or a regression."]
    for c in changes:
        L.append(f"- **{c['field']}**: {c['was']} → {c['now']} ({c.get('note','')})")
    if not changes:
        L.append("\nNo verdict changes — conclusions are stable across these snapshots.")
    _write_text(out / "verdict_regression_report.md", "\n".join(L))
    print(f"Phase 14 regress: {args.site} — {len(changes)} verdict changes")
    return 0


# ── Phase 15: capture quality scoring ───────────────────────────────
_QUALITY_BANDS = ["Discard", "Weak", "Usable", "Good", "Excellent"]


def cmd_capquality(args) -> int:
    """Score a capture's evidence quality BEFORE it feeds learning."""
    cap = _load(args.capture) or {}
    raw = cap.get("_raw", cap)
    nlog = raw.get("network_log") or []
    dom = raw.get("dom_log") or []
    dom_count = raw.get("dom_log_count", len(dom))

    # component scores 0..1
    has_goal = any("m3u8" in str(r.get("url", "")) or "mp4" in str(r.get("url", ""))
                   or "/v/" in str(r.get("url", "")) for r in nlog)
    dom_completeness = min(1.0, dom_count / 1.0) if dom_count else 0.0
    workflow_completeness = min(1.0, sum(1 for e in dom
                                if (e.get("data") or {}).get("adds")) / 3) if dom else 0.0
    # selector coverage: action-ish nodes present
    action_nodes = 0
    for e in dom:
        node = (e.get("data") or {}).get("node") or {}
        action_nodes += json.dumps(node, default=list).lower().count('"a"') \
            + json.dumps(node, default=list).lower().count("button")
    selector_coverage = min(1.0, action_nodes / 3) if action_nodes else 0.0
    rendition_coverage = 1.0 if has_goal else 0.0
    goal_quality = 1.0 if has_goal else 0.0

    comps = {"dom_completeness": round(dom_completeness, 3),
             "workflow_completeness": round(workflow_completeness, 3),
             "selector_coverage": round(selector_coverage, 3),
             "rendition_coverage": round(rendition_coverage, 3),
             "goal_quality": round(goal_quality, 3)}
    score = round(sum(comps.values()) / len(comps), 3)
    if not has_goal:
        band = "Discard"          # no goal → unusable downstream
    elif score >= 0.85:
        band = "Excellent"
    elif score >= 0.65:
        band = "Good"
    elif score >= 0.4:
        band = "Usable"
    else:
        band = "Weak"

    obj = {"capture": args.capture, "quality_band": band, "score": score,
           "components": comps,
           "usefulness": ("feeds all downstream learning" if band in ("Excellent", "Good")
                          else "usable with caveats" if band == "Usable"
                          else "low value; consider re-capture" if band == "Weak"
                          else "no media goal — discard, do not feed learning"),
           "_status": "Read-only quality gate. Flags weak evidence before it affects "
                      "downstream confidence; promotes nothing."}
    if (leaks := _posture_ok(obj)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "capture_quality.json", obj)
    _write_text(out / "capture_quality_report.md",
                f"# Capture quality — {Path(args.capture).name}\n\n"
                f"**{band}** (score {score}).\n\n"
                + "\n".join(f"- {k}: {v}" for k, v in comps.items())
                + f"\n\nUsefulness: {obj['usefulness']}\n")
    print(f"Phase 15 capquality: {Path(args.capture).name} → {band} ({score})")
    return 0


# ── Phase 16: drift forecasting ─────────────────────────────────────
def cmd_forecast(args) -> int:
    """Forecast instability from history trajectory. Forecast only — no action."""
    drift_hist = _load(args.drift_history) or []
    conf_hist = _load(args.confidence_history) or []
    health = _load(args.site_health_score) or {}

    # recent drift rate (drift events per run, weighted toward recent)
    n = len(drift_hist)
    recent = drift_hist[-3:] if n >= 3 else drift_hist
    recent_drift = sum(len(h.get("drift_flags") or []) for h in recent)
    drift_rate = round(recent_drift / max(1, len(recent)), 3)
    has_structural = any("structural_drift" in (h.get("drift_flags") or [])
                         for h in drift_hist)

    # confidence trajectory (declining?)
    confs = [h.get("avg_confidence") for h in conf_hist if h.get("avg_confidence") is not None]
    declining = len(confs) >= 2 and confs[-1] < confs[0]

    p_drift = min(0.95, round(0.3 * drift_rate + (0.2 if declining else 0)
                              + (0.3 if recent_drift else 0), 3))
    p_fragile = min(0.95, round(p_drift * (1.5 if drift_rate >= 1 else 1.0), 3))
    p_broken = min(0.9, round((0.5 if has_structural else 0.1) + 0.2 * drift_rate, 3))
    # crude time estimates in "runs"
    tt_fragile = "imminent" if drift_rate >= 1.5 else \
        f"~{max(1, round(3 / max(0.1, drift_rate)))} runs" if drift_rate else "no signal"

    obj = {"site": args.site,
           "probability_future_drift": p_drift,
           "probability_enter_fragile": p_fragile,
           "probability_enter_broken": p_broken,
           "expected_time_to_fragile": tt_fragile,
           "expected_time_to_review": "now" if p_drift >= 0.6 else "monitor",
           "signals": {"recent_drift_rate": drift_rate, "confidence_declining": declining,
                       "structural_seen": has_structural,
                       "current_maturity": health.get("maturity_state")},
           "_status": "Forecast only — probabilistic estimate from history trajectory. "
                      "No automated action; informs review priority."}
    if (leaks := _posture_ok(obj)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "drift_forecast.json", obj)
    _write_text(out / "site_risk_forecast.md",
                f"# Site risk forecast — {args.site}\n\n"
                f"- P(future drift): {p_drift}\n- P(→ Fragile): {p_fragile}\n"
                f"- P(→ Broken): {p_broken}\n- time-to-fragile: {tt_fragile}\n"
                f"- review: {obj['expected_time_to_review']}\n\n"
                f"Signals: {obj['signals']}\n\nForecast only — no action taken.\n")
    print(f"Phase 16 forecast: {args.site} P(drift)={p_drift} P(fragile)={p_fragile}")
    return 0


# ── Phase 17: policy-impact simulation ──────────────────────────────
def cmd_simulate(args) -> int:
    """Preview what approving the queue WOULD change. Applies nothing."""
    policy = _load(args.automation_decision_report) or {}
    candidate = _load(args.profile_update_candidate) or {}
    health = _load(args.site_health_score) or {}

    would_promote = [s["selector"] for s in (policy.get("selector_decisions") or [])
                     if s.get("action") == "trust"]
    would_distrust = [s["selector"] for s in (policy.get("selector_decisions") or [])
                      if s.get("action") in ("distrust", "distrust_recheck")]
    profile_change = candidate.get("proposed_field_changes", {})
    cur_maturity = health.get("maturity_state")
    # projected maturity if promotions applied: a Watch/Learning site can rise to Stable
    # when there are promotions and they are not outnumbered by distrusts (a single weak
    # selector among trusted ones should not block the projection).
    projected = cur_maturity
    if cur_maturity in ("Watch", "Learning") and would_promote \
            and len(would_promote) >= len(would_distrust):
        projected = "Stable"

    obj = {"site": args.site,
           "if_approved": {
               "selectors_promoted": would_promote,
               "selectors_distrusted": would_distrust,
               "profile_fields_changed": profile_change,
               "maturity": {"current": cur_maturity, "projected": projected},
           },
           "review_queue_impact": {
               "items_cleared_if_approved": len(would_promote) + len(profile_change)},
           "_status": "SIMULATION ONLY. Nothing is applied. Shows the consequence of a "
                      "batch approval so a human can decide before approving."}
    if (leaks := _posture_ok(obj)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "approval_simulation.json", obj)
    _write_text(out / "policy_impact_report.md",
                f"# Policy impact simulation — {args.site}\n\n"
                f"If everything in the queue were approved:\n"
                f"- selectors promoted: {would_promote or 'none'}\n"
                f"- selectors distrusted: {would_distrust or 'none'}\n"
                f"- profile fields changed: {list(profile_change) or 'none'}\n"
                f"- maturity: {cur_maturity} → {projected}\n\n"
                f"Nothing is applied. Simulation only.\n")
    print(f"Phase 17 simulate: {args.site} would promote {len(would_promote)} selectors")
    return 0


# ── Phase 18: evidence freshness & aging ────────────────────────────
def _freshness_band(age_days: Optional[float]) -> str:
    if age_days is None:
        return "Unknown"
    if age_days <= 7:
        return "Fresh"
    if age_days <= 30:
        return "Aging"
    if age_days <= 90:
        return "Stale"
    return "Expired"


def cmd_freshness(args) -> int:
    """Measure how current each evidence type is. Informs review priority; changes no trust."""
    now = _now()

    def _age(hist):
        if isinstance(hist, list) and hist:
            ts = _parse_ts(hist[-1].get("at"))
            if ts:
                return round((now - ts).total_seconds() / 86400, 2)
        return None

    items = {
        "selectors": _age(_load(args.confidence_history)),
        "workflows": _age(_load(args.workflow_history)),
        "login": _age(_load(args.login_history)),
        "site_profile": _age(_load(args.confidence_history)),  # proxy
        "health": _age(_load(args.drift_history)),
    }
    bands = {k: _freshness_band(v) for k, v in items.items()}
    obj = {"site": args.site, "age_days": items, "freshness": bands,
           "review_priority": [k for k, b in bands.items() if b in ("Stale", "Expired")],
           "_status": "Freshness informs review priority only — it never automatically "
                      "changes trust or promotes/demotes anything."}
    if (leaks := _posture_ok(obj)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "evidence_freshness.json", obj)
    _write_text(out / "evidence_aging_report.md",
                f"# Evidence aging — {args.site}\n\n"
                + "\n".join(f"- {k}: **{bands[k]}**"
                            + (f" ({items[k]}d)" if items[k] is not None else "")
                            for k in items)
                + (f"\n\nNeeds review (stale/expired): {obj['review_priority']}"
                   if obj["review_priority"] else "\n\nAll evidence current.")
                + "\n\nFreshness informs review priority only; trust is unchanged.\n")
    print(f"Phase 18 freshness: {args.site} — {bands}")
    return 0


# ── cross-cut: failure intelligence ─────────────────────────────────
_FAILURE_TYPES = ("Selector Failure", "Workflow Failure", "Login Failure",
                  "Session Failure", "Rendition Failure", "Infrastructure Failure",
                  "Unknown Failure")


def cmd_failures(args) -> int:
    """Classify observed failures into types from drift/policy artifacts."""
    drift_hist = _load(args.drift_history) or []
    classified: Dict[str, int] = {t: 0 for t in _FAILURE_TYPES}
    for h in drift_hist:
        for f in (h.get("drift_flags") or []):
            if "selector" in f:
                classified["Selector Failure"] += 1
            elif "structural" in f or "workflow" in f:
                classified["Workflow Failure"] += 1
            elif "rendition" in f:
                classified["Rendition Failure"] += 1
            elif "signing" in f:
                classified["Session Failure"] += 1  # signed-session expiry, not infra
            elif "identity" in f:
                classified["Unknown Failure"] += 1
            else:
                classified["Unknown Failure"] += 1
    live = _live_outcome(args.site)
    if live.get("flagged_stale"):
        classified["Login Failure"] += 0  # left to login artifacts; flagged for review
    obj = {"site": args.site, "failure_counts": {k: v for k, v in classified.items() if v},
           "_status": "Read-only failure classification from existing drift evidence."}
    if (leaks := _posture_ok(obj)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_text(out / "failure_intelligence_report.md",
                f"# Failure intelligence — {args.site}\n\n"
                + ("\n".join(f"- {k}: {v}" for k, v in obj["failure_counts"].items())
                   or "No failures classified.")
                + "\n")
    print(f"Phase cross-cut failures: {args.site} — {obj['failure_counts']}")
    return 0


# ── cross-cut: evidence impact analysis ─────────────────────────────
def cmd_impact(args) -> int:
    """Rate each observation's downstream impact Low/Medium/High/Critical."""
    drift_hist = _load(args.drift_history) or []
    observations = []
    for i, h in enumerate(drift_hist):
        flags = h.get("drift_flags") or []
        if "structural_drift" in flags:
            level = "Critical"      # breaks the workflow
        elif "selector_drift" in flags:
            level = "High"          # breaks the fast path
        elif "signing_pattern_drift" in flags:
            level = "High"          # session handling changed
        elif "rendition_drift" in flags:
            level = "Medium"
        elif flags:
            level = "Medium"
        else:
            level = "Low"
        observations.append({"index": i, "drift_flags": flags, "impact": level})
    obj = {"site": args.site, "observations": observations,
           "_status": "Read-only impact rating to focus operator attention on the "
                      "highest-downstream-effect observations."}
    if (leaks := _posture_ok(obj)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "evidence_impact.json", obj)
    crit = [o for o in observations if o["impact"] in ("Critical", "High")]
    _write_text(out / "evidence_impact_report.md",
                f"# Evidence impact — {args.site}\n\n"
                f"{len(crit)} high/critical-impact observation(s) to prioritize:\n"
                + ("\n".join(f"- obs {o['index']}: {o['drift_flags']} → **{o['impact']}**"
                             for o in crit) or "None.")
                + "\n")
    print(f"Phase cross-cut impact: {args.site} — {len(crit)} high/critical")
    return 0


# ── cross-cut: family-level intelligence (extends Phase 12) ─────────
def cmd_family(args) -> int:
    """Aggregate health/drift across a family's member sites."""
    membership = _load(args.site_family_membership) or {}
    root = Path(args.sites_root) if args.sites_root else None
    fam_stats: Dict[str, Dict[str, Any]] = {}
    for site, info in membership.items():
        fam = info.get("player_family", "unknown")
        fs = fam_stats.setdefault(fam, {"sites": [], "drift_events": 0,
                                        "maturities": [], "confidences": []})
        fs["sites"].append(site)
        if root and (root / site).is_dir():
            dh = _load(str(root / site / "drift_history.json")) or []
            fs["drift_events"] += sum(len(h.get("drift_flags") or []) for h in dh)
            hsc = _load(str(root / site / "site_health_score.json")) or {}
            if hsc.get("maturity_state"):
                fs["maturities"].append(hsc["maturity_state"])
            ov = (hsc.get("scores") or {}).get("overall")
            if ov is not None:
                fs["confidences"].append(ov)
    report = {}
    for fam, fs in fam_stats.items():
        nsites = len(fs["sites"])
        report[fam] = {
            "n_sites": nsites,
            "drift_rate_per_site": round(fs["drift_events"] / nsites, 3) if nsites else 0,
            "avg_health": round(sum(fs["confidences"]) / len(fs["confidences"]), 3)
                if fs["confidences"] else None,
            "maturity_distribution": {m: fs["maturities"].count(m)
                                      for m in set(fs["maturities"])},
            "members": fs["sites"],
        }
    obj = {"families": report,
           "_status": "Family-level aggregation for CONFIDENCE and analysis only. Patterns "
                      "may inform confidence; they NEVER directly change live behavior."}
    if (leaks := _posture_ok(obj)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_text(out / "family_health_report.md",
                "# Family health\n\n" + "\n".join(
                    f"## {fam}\n- sites: {r['n_sites']} ({', '.join(r['members'])})\n"
                    f"- avg health: {r['avg_health']}\n"
                    f"- maturity: {r['maturity_distribution']}"
                    for fam, r in report.items()) + "\n")
    _write_text(out / "family_drift_report.md",
                "# Family drift\n\n" + "\n".join(
                    f"- {fam}: drift rate {r['drift_rate_per_site']}/site over "
                    f"{r['n_sites']} sites" for fam, r in report.items())
                + "\n\nPatterns inform confidence only; never change live behavior.\n")
    print(f"Phase cross-cut family: {len(report)} families aggregated")
    return 0


# ── dispatch ────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Phases 13-18 + cross-cutting trust views")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("calibrate"); c.set_defaults(fn=cmd_calibrate)
    c.add_argument("--site", required=True)
    c.add_argument("--selector-confidence", default=None)
    c.add_argument("--outcomes", default=None)
    c.add_argument("--out-dir", default="./calibration")

    r = sub.add_parser("regress"); r.set_defaults(fn=cmd_regress)
    r.add_argument("--site", required=True)
    r.add_argument("--old-snapshot", default=None)
    r.add_argument("--new-snapshot", default=None)
    r.add_argument("--out-dir", default="./regression")

    q = sub.add_parser("capquality"); q.set_defaults(fn=cmd_capquality)
    q.add_argument("--capture", required=True)
    q.add_argument("--out-dir", default="./capquality")

    f = sub.add_parser("forecast"); f.set_defaults(fn=cmd_forecast)
    f.add_argument("--site", required=True)
    f.add_argument("--drift-history", default=None)
    f.add_argument("--confidence-history", default=None)
    f.add_argument("--site-health-score", default=None)
    f.add_argument("--out-dir", default="./forecast")

    s = sub.add_parser("simulate"); s.set_defaults(fn=cmd_simulate)
    s.add_argument("--site", required=True)
    s.add_argument("--automation-decision-report", default=None)
    s.add_argument("--profile-update-candidate", default=None)
    s.add_argument("--site-health-score", default=None)
    s.add_argument("--out-dir", default="./simulation")

    fr = sub.add_parser("freshness"); fr.set_defaults(fn=cmd_freshness)
    fr.add_argument("--site", required=True)
    fr.add_argument("--confidence-history", default=None)
    fr.add_argument("--workflow-history", default=None)
    fr.add_argument("--login-history", default=None)
    fr.add_argument("--drift-history", default=None)
    fr.add_argument("--out-dir", default="./freshness")

    fa = sub.add_parser("failures"); fa.set_defaults(fn=cmd_failures)
    fa.add_argument("--site", required=True)
    fa.add_argument("--drift-history", default=None)
    fa.add_argument("--out-dir", default="./failures")

    im = sub.add_parser("impact"); im.set_defaults(fn=cmd_impact)
    im.add_argument("--site", required=True)
    im.add_argument("--drift-history", default=None)
    im.add_argument("--out-dir", default="./impact")

    fm = sub.add_parser("family"); fm.set_defaults(fn=cmd_family)
    fm.add_argument("--site-family-membership", default=None)
    fm.add_argument("--sites-root", default=None)
    fm.add_argument("--out-dir", default="./family")
    return p


def run(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(run())
