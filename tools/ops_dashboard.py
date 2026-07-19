#!/usr/bin/env python3
"""ops_dashboard.py — Phase 6 operational dashboard & review workflow.

Rolls the Phase 1-5 artifacts into day-to-day operational reports and the final
end-of-phase report. It adds NO detector behavior — it reads existing artifacts and
the read-only corpus debt report and summarizes them. It writes nothing back: no
profile, no selector, no corpus, no debt change. Recognition-only posture unchanged.

Inputs (any subset; per-site dirs are scanned for the known artifact filenames):
  --sites-root <dir>   a dir containing one subdir per site, each holding that site's
                       Phase 1-5 artifacts (site_profile.json, selector_confidence.json,
                       site_health_report.md, automation_decision_report.json,
                       profile_update_candidate.json, drift_history.json, ...)
  --out-dir <dir>

Outputs:
  site_dashboard.md, framework_operations_dashboard.md, review_queue.md,
  pending_evidence.md, operator_next_actions.md, end_of_phase_report.md
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


def _load(path: Path) -> Optional[Any]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def _write_text(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _gather_site(site_dir: Path) -> Dict[str, Any]:
    """Collect the per-site artifact summary from whatever Phase 1-5 files exist."""
    s: Dict[str, Any] = {"site": site_dir.name}
    prof = _load(site_dir / "site_profile.json")
    s["has_profile"] = bool(prof)
    sel = _load(site_dir / "selector_confidence.json")
    if isinstance(sel, list) and sel:
        s["n_selectors"] = len(sel)
        s["avg_selector_confidence"] = round(
            sum(float(x.get("confidence", 0)) for x in sel) / len(sel), 3)
    elif isinstance(sel, dict) and sel.get("status") == "blocked_no_dom":
        s["selectors"] = "blocked_no_dom"
    dh = _load(site_dir / "drift_history.json") or []
    flags: Dict[str, int] = {}
    for h in dh:
        for f in (h.get("drift_flags") or []):
            flags[f] = flags.get(f, 0) + 1
    s["drift_flags"] = flags
    dec = _load(site_dir / "automation_decision_report.json") or {}
    s["queued"] = dec.get("queued", {})
    cand = _load(site_dir / "profile_update_candidate.json") or {}
    s["evidence_strength"] = cand.get("evidence_strength")
    s["update_recommendation"] = cand.get("recommendation")
    return s


def _corpus_debt() -> Dict[str, Any]:
    """Read-only debt status. Never writes or retires."""
    try:
        from bulk_downloader.validation_corpus import debt_report
        r = debt_report()
        return {"correction": len(r["correction_debt"]),
                "capability": len(r["capability_debt"]),
                "validation": len(r["validation_debt"]),
                "validation_items": [d.get("id") for d in r["validation_debt"]],
                "note": r.get("note", "")}
    except Exception as e:
        return {"error": f"debt report unavailable: {str(e)[:80]}"}


def run(argv=None) -> int:
    p = argparse.ArgumentParser(description="Phase 6 operational dashboard")
    p.add_argument("--sites-root", required=True,
                   help="Dir with one subdir per site holding Phase 1-5 artifacts.")
    p.add_argument("--out-dir", default="./dashboard")
    args = p.parse_args(argv)
    from bulk_downloader.capture_ingest import posture_scan

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    root = Path(args.sites_root)
    sites = [_gather_site(d) for d in sorted(root.iterdir()) if d.is_dir()] \
        if root.is_dir() else []
    debt = _corpus_debt()

    # ── site dashboard ──
    sd = ["# Site dashboard", ""]
    if not sites:
        sd.append("No per-site artifact directories found.")
    for s in sites:
        sd.append(f"## {s['site']}")
        sd.append(f"- profile: {'yes' if s['has_profile'] else 'no'}; "
                  f"selectors: {s.get('n_selectors', s.get('selectors','none'))}"
                  + (f" (avg conf {s['avg_selector_confidence']})"
                     if 'avg_selector_confidence' in s else ""))
        sd.append(f"- drift seen: {s['drift_flags'] or 'none'}")
        sd.append(f"- evidence strength: {s.get('evidence_strength','n/a')}; "
                  f"update: {s.get('update_recommendation','n/a')}")
        q = s.get("queued", {})
        sd.append(f"- queued — review:{q.get('manual_review',0)} "
                  f"captures:{q.get('capture_requests',0)} "
                  f"approvals:{q.get('profile_approvals',0)}")

    # ── framework dashboard ──
    tot_review = sum(s.get("queued", {}).get("manual_review", 0) for s in sites)
    tot_cap = sum(s.get("queued", {}).get("capture_requests", 0) for s in sites)
    tot_app = sum(s.get("queued", {}).get("profile_approvals", 0) for s in sites)
    fd = ["# Framework operations dashboard", "",
          f"Sites tracked: {len(sites)}.",
          f"Validation debt: correction {debt.get('correction','?')} / "
          f"capability {debt.get('capability','?')} / validation {debt.get('validation','?')}"
          + (f" ({', '.join(x for x in debt.get('validation_items',[]) if x)})"
             if debt.get('validation_items') else ""),
          f"Pending across all sites — manual review: {tot_review}, "
          f"capture requests: {tot_cap}, profile approvals: {tot_app}.", ""]
    repeated = [s["site"] for s in sites
                if any(v >= 2 for v in s.get("drift_flags", {}).values())]
    if repeated:
        fd.append(f"Sites with repeated drift (most fragile): {', '.join(repeated)}.")

    # ── queues ──
    rq = ["# Review queue", ""]
    pe = ["# Pending evidence (captures needed)", ""]
    na = ["# Operator next actions", ""]
    for s in sites:
        q = s.get("queued", {})
        if q.get("manual_review"):
            rq.append(f"- {s['site']}: {q['manual_review']} item(s) for review")
        if q.get("capture_requests"):
            pe.append(f"- {s['site']}: {q['capture_requests']} fresh capture(s) requested")
        if q.get("profile_approvals"):
            na.append(f"- {s['site']}: approve profile update "
                      f"({s.get('evidence_strength')})")
    if len(rq) == 2: rq.append("Nothing queued for review.")
    if len(pe) == 2: pe.append("No captures currently needed.")
    if debt.get("validation"):
        pe.append(f"- Framework: {debt['validation']} validation-debt item(s) "
                  f"({', '.join(x for x in debt.get('validation_items',[]) if x)}) "
                  f"need real captures — cannot be retired synthetically.")
    if len(na) == 2:
        na.append("No profile approvals pending. Next: keep capturing pairs for "
                  "sites with weak evidence; review any repeated-drift sites.")

    # ── end-of-phase report (the five questions) ──
    eo = ["# End-of-phase report — Phases 1-6", "",
          "## 1. What can now run automatically",
          "- Capture (parallel/sequential, persistent profile, autofill, per-title URL "
          "memory) — human-driven, auto-advancing queue.",
          "- Phase 1: capture→template validation, rendition/drift profiling, "
          "draft corpus entries.",
          "- Phase 2: DOM→selector candidate extraction and confidence scoring.",
          "- Phase 3: confidence-ordered selector guidance + live drift classification "
          "feeding the existing live workflow.",
          "- Phase 4: profile-diff, confidence/drift history, evidence-strength scoring.",
          "- Phase 5: evidence-gated policy decisions (trust/warn/review/request).",
          "- Phase 6: these dashboards and queues.",
          "All of the above is recognition, scoring, and reporting — it produces data and "
          "recommendations, never actions.", "",
          "## 2. What still requires human approval",
          "- Promoting any selector into the live learned set.",
          "- Applying any profile update.",
          "- Writing any corpus entry (all phases emit drafts/suggestions only).",
          "- Retiring any debt.",
          "- Any structural-drift response (always human review).", "",
          "## 3. What still requires new captures",
          f"- The {debt.get('validation','?')} open validation-debt items "
          f"({', '.join(x for x in debt.get('validation_items',[]) if x)}) — real "
          f"same-title perturbation captures; not retirable synthetically.",
          "- Sites flagged with weak evidence or repeated drift (see pending evidence).",
          "- Any site captured without DOM logs, before selector learning is possible.", "",
          "## 4. What remains prohibited by posture",
          "- Replaying captured network requests; reusing captured signing values; "
          "reconstructing signed/short-lived URLs.",
          "- Generating Playwright replay scripts or click sequences from captures.",
          "- Bypassing site UI; recreating sessions.",
          "- Auto-writing the corpus or auto-retiring debt.",
          "- Signing-pattern drift triggering token reuse (hard rule).", "",
          "## 5. Next highest-ROI phase",
          "- Capturing real perturbation pairs for the two open validation-debt items is the "
          "single highest-value next step — it is the only thing that retires standing debt, "
          "and the harnesses to consume it already exist.",
          "- Second: feed Phase-6 queues into the existing operator UI so review/capture/"
          "approval items surface in-app rather than as files.",
          "- Selector learning expands automatically as more DOM-carrying captures arrive; "
          "no new build needed there."]

    # POSTURE
    blob = "\n".join(sd + fd + rq + pe + na + eo) + json.dumps(debt, default=list)
    leaks = posture_scan(blob)
    if leaks:
        print(f"POSTURE FAIL: signing value in dashboard ({leaks}); refusing.",
              file=sys.stderr)
        return 2

    _write_text(out / "site_dashboard.md", "\n".join(sd))
    _write_text(out / "framework_operations_dashboard.md", "\n".join(fd))
    _write_text(out / "review_queue.md", "\n".join(rq))
    _write_text(out / "pending_evidence.md", "\n".join(pe))
    _write_text(out / "operator_next_actions.md", "\n".join(na))
    _write_text(out / "end_of_phase_report.md", "\n".join(eo))
    print(f"Phase-6 dashboard written to {out}/  sites:{len(sites)}  "
          f"debt(validation):{debt.get('validation','?')}  "
          f"pending review:{tot_review} captures:{tot_cap} approvals:{tot_app}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
