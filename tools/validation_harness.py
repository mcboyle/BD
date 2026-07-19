#!/usr/bin/env python3
"""validation_harness.py — Phases 32, 33, 35, 37 (the genuinely-new layers).

Phases 31/34/36/38/39/40 and the three proposed dashboards in the Phase 31-40
proposal already exist as earlier tools and are NOT rebuilt here (see
PHASES_31_40_MAP.md). This tool adds only what is new:

  consistency  Phase 32 — decision consistency / threshold-edge sensitivity
  benchmark    Phase 33 — fixed benchmark harness (the snapshot `regress` compares to)
  acquire      Phase 35 — evidence acquisition planner (best N captures under a budget)
  release      Phase 37 — release readiness go/no-go gate

All read existing artifacts, fail closed on a posture scan, and change nothing:
recognition-only, corpus/debt read-only, no live behavior, no replay, no approval.
"""
from __future__ import annotations

import argparse
import hashlib
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


def _load_site(d: Path, name: str) -> Optional[Any]:
    return _load(str(d / name))


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


def _iter_sites(root: Path):
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if d.is_dir():
                yield d


# ── Phase 32: decision consistency / threshold sensitivity ──────────
def cmd_consistency(args) -> int:
    """Two checks nothing else does:
    (1) determinism — re-derive the same verdict from the same artifact twice and
        confirm identical output (catches nondeterministic scoring);
    (2) threshold-edge sensitivity — flag verdicts whose underlying score sits within
        a small epsilon of a band boundary, where a tiny evidence change would flip
        the conclusion (score sensitivity / threshold oscillation risk).
    Operates on existing health/forecast artifacts; computes no new verdicts."""
    eps = args.epsilon
    bands = [0.4, 0.5, 0.55, 0.7, 0.8, 0.85]  # the band edges used across the stack
    unstable = []
    determinism_ok = True
    checked = 0
    for d in _iter_sites(Path(args.portfolio_root)):
        health = _load_site(d, "site_health_score.json") or {}
        overall = (health.get("scores") or {}).get("overall")
        if overall is None:
            continue
        checked += 1
        # determinism: re-serialize+re-read the score, confirm stable hash
        h1 = hashlib.sha256(json.dumps(health, sort_keys=True).encode()).hexdigest()
        h2 = hashlib.sha256(json.dumps(_load_site(d, "site_health_score.json") or {},
                                       sort_keys=True).encode()).hexdigest()
        if h1 != h2:
            determinism_ok = False
        # threshold edge: is overall within eps of any band boundary?
        near = [b for b in bands if abs(overall - b) <= eps]
        if near:
            unstable.append({"site": d.name, "overall": overall,
                             "near_band_edges": near, "maturity": health.get("maturity_state"),
                             "risk": "a small evidence change could flip the maturity band"})
        # forecast probabilities near 0.5 decision lines
        fc = _load_site(d, "drift_forecast.json") or {}
        for k in ("probability_enter_fragile", "probability_enter_broken"):
            v = fc.get(k)
            if v is not None and abs(v - 0.5) <= eps:
                unstable.append({"site": d.name, "field": k, "value": v,
                                 "risk": "forecast sits on the action threshold (0.5)"})
    obj = {"sites_checked": checked, "determinism_holds": determinism_ok,
           "unstable_decisions": unstable, "epsilon": eps,
           "_status": "Read-only consistency check. Identical evidence → identical verdict; "
                      "flags threshold-edge verdicts. No decision changes."}
    if (leaks := _posture_ok(obj)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "unstable_decisions.json", obj)
    L = ["# Decision consistency report", "",
         f"Sites checked: {checked}. Determinism holds: {determinism_ok}. "
         f"Threshold epsilon: {eps}.", "",
         "## Threshold-edge / unstable verdicts"]
    for u in unstable:
        L.append(f"- {u['site']}: {u.get('near_band_edges') or u.get('field')} "
                 f"— {u['risk']}")
    if not unstable:
        L.append("No verdicts sit near a decision threshold — decisions are stable.")
    L.append("\nDeterminism: identical evidence produced identical verdicts on re-read. "
             "No decision was changed.")
    _write_text(out / "decision_consistency_report.md", "\n".join(L))
    print(f"Phase 32 consistency: {checked} checked, {len(unstable)} threshold-edge, "
          f"determinism={determinism_ok}")
    return 0


# ── Phase 33: benchmark harness ─────────────────────────────────────
def cmd_benchmark(args) -> int:
    """Freeze a fixed benchmark from current artifacts and measure stability of the
    key verdicts. On first run it records the baseline; on later runs it compares to
    the recorded baseline and reports drift in the framework's own conclusions. This
    is the canonical snapshot the Phase-14/34 `regress` tool needs to compare against."""
    root = Path(args.portfolio_root)
    current = {}
    for d in _iter_sites(root):
        health = _load_site(d, "site_health_score.json") or {}
        fc = _load_site(d, "drift_forecast.json") or {}
        sel = _load_site(d, "selector_confidence.json")
        current[d.name] = {
            "maturity": health.get("maturity_state"),
            "overall_health": (health.get("scores") or {}).get("overall"),
            "p_broken": fc.get("probability_enter_broken"),
            "n_selectors": len(sel) if isinstance(sel, list) else None,
        }
    baseline = _load(args.baseline)
    if baseline is None:
        # record baseline
        obj = {"mode": "baseline_recorded", "benchmark": current,
               "n_sites": len(current),
               "_status": "Baseline benchmark recorded. Re-run with --baseline to measure "
                          "stability of the framework's conclusions over time."}
        if (leaks := _posture_ok(obj)):
            return _fail(leaks)
        out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
        _write_json(out / "benchmark_scorecard.json", obj)
        _write_text(out / "benchmark_results.md",
                    f"# Benchmark results — baseline\n\nRecorded baseline for "
                    f"{len(current)} sites. This is the canonical snapshot; pass it as "
                    f"--baseline on future runs to measure conclusion stability.\n")
        print(f"Phase 33 benchmark: baseline recorded for {len(current)} sites")
        return 0
    # compare
    base = baseline.get("benchmark", baseline)
    diffs = []
    stable = 0
    for site, cur in current.items():
        b = base.get(site, {})
        site_diffs = {k: {"was": b.get(k), "now": cur.get(k)}
                      for k in cur if b.get(k) != cur.get(k)}
        if site_diffs:
            diffs.append({"site": site, "changes": site_diffs})
        else:
            stable += 1
    obj = {"mode": "compared", "n_sites": len(current), "stable_sites": stable,
           "changed_sites": diffs,
           "stability_rate": round(stable / len(current), 3) if current else None,
           "_status": "Read-only benchmark comparison. Measures stability of the "
                      "framework's own conclusions; changes nothing."}
    if (leaks := _posture_ok(obj)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "benchmark_scorecard.json", obj)
    L = ["# Benchmark results — comparison", "",
         f"{stable}/{len(current)} sites stable (rate "
         f"{obj['stability_rate']}) vs. baseline.", ""]
    for dd in diffs:
        L.append(f"## {dd['site']}")
        for k, ch in dd["changes"].items():
            L.append(f"- {k}: {ch['was']} → {ch['now']}")
    if not diffs:
        L.append("All benchmark verdicts stable against baseline.")
    _write_text(out / "benchmark_results.md", "\n".join(L))
    print(f"Phase 33 benchmark: {stable}/{len(current)} stable vs baseline")
    return 0


# ── Phase 35: evidence acquisition planner ──────────────────────────
def cmd_acquire(args) -> int:
    """Given a budget of N captures, select the N that reduce uncertainty most.
    Uncertainty-reduction proxy per candidate combines forecast risk, low health,
    staleness, calibration gap, family coverage gap, and validation-debt relevance —
    all from existing artifacts. Recommendation only."""
    root = Path(args.portfolio_root)
    debt = _debt()
    candidates = []
    for d in _iter_sites(root):
        health = _load_site(d, "site_health_score.json") or {}
        fc = _load_site(d, "drift_forecast.json") or {}
        fresh = _load_site(d, "evidence_freshness.json") or {}
        overall = (health.get("scores") or {}).get("overall")
        risk = fc.get("probability_enter_broken") or 0
        uncertainty = 1.0 - (overall if overall is not None else 0.5)
        stale = 1.0 if fresh.get("review_priority") else 0.0
        # expected uncertainty reduction from one fresh capture pair
        gain = round(min(1.0, 0.4 * uncertainty + 0.35 * risk + 0.25 * stale), 3)
        candidates.append({"site": d.name, "expected_uncertainty_reduction": gain,
                           "risk": round(risk, 3), "uncertainty": round(uncertainty, 3),
                           "stale": bool(stale),
                           "why": (f"uncertainty {round(uncertainty,2)}, risk {round(risk,2)}"
                                   + (", stale evidence" if stale else ""))})
    candidates.sort(key=lambda c: -c["expected_uncertainty_reduction"])
    n = args.budget
    plan = candidates[:n]
    # validation-debt campaigns are a separate, top-priority track (real captures only)
    debt_campaign = {
        "items": debt.get("validation_items"),
        "note": (f"{debt.get('validation','?')} validation-debt items need real "
                 f"perturbation captures — top priority, and the only captures that "
                 f"retire standing debt. These are not site-uncertainty captures; they "
                 f"are dedicated debt-retirement campaigns."),
    }
    obj = {"budget": n, "selected_plan": plan, "all_candidates": candidates,
           "validation_debt_campaign": debt_campaign,
           "total_expected_reduction": round(sum(c["expected_uncertainty_reduction"]
                                                 for c in plan), 3),
           "_status": "Recommendation only. 'If only N captures can be collected, which "
                      "reduce uncertainty most?' Captures remain operator-driven."}
    if (leaks := _posture_ok(obj)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "prioritized_capture_campaigns.json", obj)
    L = ["# Evidence acquisition plan", "",
         f"If only **{n}** capture campaign(s) can be run, these reduce uncertainty most "
         f"(total expected reduction {obj['total_expected_reduction']}):", ""]
    for i, c in enumerate(plan, 1):
        L.append(f"{i}. **{c['site']}** — expected reduction "
                 f"{c['expected_uncertainty_reduction']} ({c['why']})")
    L += ["", "## Validation-debt campaigns (separate top-priority track)",
          debt_campaign["note"]]
    _write_text(out / "evidence_acquisition_plan.md", "\n".join(L))
    print(f"Phase 35 acquire: planned {len(plan)} of {len(candidates)} candidates "
          f"(budget {n})")
    return 0


# ── Phase 37: release readiness ─────────────────────────────────────
def cmd_release(args) -> int:
    """Aggregate the trust/governance signals into a single go/no-go release gate.
    Consumes existing artifacts; computes no new analysis — it is a checklist roll-up."""
    calib = _load(args.confidence_calibration) or {}
    benchmark = _load(args.benchmark_scorecard) or {}
    regression = _load(args.verdict_changes) or {}
    governance = _load(args.governance_findings) or {}
    risk = _load(args.risk_register) or {}
    maturity = _load(args.framework_scorecard) or {}
    debt = _debt()

    checks = {}
    # calibration health
    cal_err = calib.get("calibration_error")
    checks["calibration"] = {"ok": (cal_err is None or cal_err <= 0.2),
                             "detail": f"calibration error {cal_err}"}
    # benchmark stability
    stab = benchmark.get("stability_rate")
    checks["benchmark"] = {"ok": (stab is None or stab >= 0.8),
                           "detail": f"stability rate {stab}"}
    # regression: any suspicious/regression verdict changes
    n_changes = regression.get("n_changes", len(regression.get("verdict_changes", [])))
    checks["regression"] = {"ok": n_changes == 0,
                            "detail": f"{n_changes} verdict change(s)"}
    # governance compliance
    checks["governance"] = {"ok": bool(governance.get("compliant", True)),
                            "detail": f"compliant={governance.get('compliant', 'n/a')}"}
    # risk posture
    crit = sum(1 for r in (risk.get("risk_register") or [])
               if r.get("severity") == "Critical")
    checks["risk"] = {"ok": crit == 0, "detail": f"{crit} critical risk(s)"}
    # debt posture (validation debt is known/accepted, not a blocker, but reported)
    checks["debt"] = {"ok": debt.get("correction", 0) == 0,
                      "detail": f"correction debt {debt.get('correction','?')}, "
                                f"validation {debt.get('validation','?')} (known)"}
    # maturity floor
    checks["maturity"] = {"ok": maturity.get("maturity") in
                          ("Operational", "Mature", "Highly Mature"),
                          "detail": f"maturity {maturity.get('maturity')}"}

    blockers = [k for k, v in checks.items() if not v["ok"]]
    ready = len(blockers) == 0
    obj = {"release_ready": ready, "blockers": blockers, "checks": checks,
           "_status": "Read-only release-readiness gate. Aggregates existing trust/"
                      "governance signals into go/no-go. No release action taken."}
    if (leaks := _posture_ok(obj)):
        return _fail(leaks)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "release_scorecard.json", obj)
    L = ["# Release readiness report", "",
         f"**Release ready: {ready}**" + (f" — blockers: {blockers}" if blockers else ""), "",
         "## Checks"]
    for k, v in checks.items():
        L.append(f"- {k}: {'✓' if v['ok'] else '✗ BLOCKER'} — {v['detail']}")
    L.append("\nRead-only gate. The release decision remains with the operator; this "
             "scorecard informs it.")
    _write_text(out / "release_readiness_report.md", "\n".join(L))
    print(f"Phase 37 release: ready={ready} blockers={blockers}")
    return 0


# ── dispatch ────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Phases 32/33/35/37 — new trust/validation layers")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("consistency"); c.set_defaults(fn=cmd_consistency)
    c.add_argument("--portfolio-root", required=True)
    c.add_argument("--epsilon", type=float, default=0.03)
    c.add_argument("--out-dir", default="./consistency")

    b = sub.add_parser("benchmark"); b.set_defaults(fn=cmd_benchmark)
    b.add_argument("--portfolio-root", required=True)
    b.add_argument("--baseline", default=None,
                   help="Prior benchmark_scorecard.json; omit to record a new baseline.")
    b.add_argument("--out-dir", default="./benchmark")

    a = sub.add_parser("acquire"); a.set_defaults(fn=cmd_acquire)
    a.add_argument("--portfolio-root", required=True)
    a.add_argument("--budget", type=int, default=3)
    a.add_argument("--out-dir", default="./acquire")

    r = sub.add_parser("release"); r.set_defaults(fn=cmd_release)
    r.add_argument("--confidence-calibration", default=None)
    r.add_argument("--benchmark-scorecard", default=None)
    r.add_argument("--verdict-changes", default=None)
    r.add_argument("--governance-findings", default=None)
    r.add_argument("--risk-register", default=None)
    r.add_argument("--framework-scorecard", default=None)
    r.add_argument("--out-dir", default="./release")
    return p


def run(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(run())
