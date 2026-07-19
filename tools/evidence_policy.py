#!/usr/bin/env python3
"""evidence_policy.py — Phase 5 evidence-gated automation policy.

Decides, from accumulated evidence, WHEN the system should trust, distrust, warn,
request a new capture, or queue manual review. It is policy logic over the Phase
1-4 artifacts — it takes NO action itself: it queues recommendations for a human.

It never promotes a selector, writes the corpus, retires debt, reuses a signing
value, or triggers any live behavior. Critically: signing-pattern drift NEVER
triggers token reuse — it can only raise a warning and (at most) request a fresh
capture. Recognition-only posture unchanged.

Policy rules (as specified):
  * one observation MAY suggest an update (low-confidence suggestion);
  * repeated consistent observations MAY increase confidence (trust);
  * conflicting observations LOWER confidence (distrust/warn);
  * structural drift REQUIRES human review (always queued, never auto);
  * signing-pattern drift must NEVER trigger token reuse (warn + capture only).

Inputs:
  --selector-confidence selector_confidence.json   (Phase 2)
  --drift-history drift_history.json                (Phase 4)
  --confidence-history confidence_history.json      (Phase 4)
  --profile-update-candidate profile_update_candidate.json (Phase 4)

Outputs:
  automation_policy.md, automation_decision_report.json, manual_review_queue.md,
  capture_request_queue.md, profile_approval_queue.md
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

_TRUST_CONF = 0.7
_DISTRUST_CONF = 0.4
_REPEAT_MIN = 2


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


def _selector_decisions(sel_conf: Optional[Any],
                        drift_history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """trust / distrust / warn per selector from confidence + drift recurrence."""
    decisions = []
    # how many times selector drift has recurred
    sel_drift_count = sum(1 for h in drift_history
                          if "selector_drift" in (h.get("drift_flags") or [])
                          or "partial_selector_drift" in (h.get("drift_flags") or []))
    conflicting = sel_drift_count >= _REPEAT_MIN
    if isinstance(sel_conf, list):
        for s in sel_conf:
            c = float(s.get("confidence", 0))
            if conflicting:
                action = "distrust_recheck"   # conflicting observations lower confidence
                why = (f"selector drift recurred {sel_drift_count}x — confidence lowered "
                       f"regardless of score")
            elif c >= _TRUST_CONF:
                action = "trust"
                why = f"confidence {c} >= {_TRUST_CONF} and no repeated selector drift"
            elif c < _DISTRUST_CONF:
                action = "distrust"
                why = f"confidence {c} < {_DISTRUST_CONF}"
            else:
                action = "warn_use_with_fallback"
                why = f"middling confidence {c}; keep but rely on fallback order"
            decisions.append({"selector": s.get("selector"), "confidence": c,
                              "action": action, "why": why})
    return decisions


def _axis_policy(drift_history: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    for h in drift_history:
        for f in (h.get("drift_flags") or []):
            counts[f] = counts.get(f, 0) + 1
    policy = {}
    # structural drift ALWAYS requires human review
    if counts.get("structural_drift"):
        policy["structural_drift"] = {"decision": "require_human_review",
                                      "occurrences": counts["structural_drift"],
                                      "auto_action": "none — human review mandatory"}
    # signing-pattern drift: warn + request capture, NEVER token reuse
    if counts.get("signing_pattern_drift"):
        policy["signing_pattern_drift"] = {
            "decision": "warn_and_request_capture",
            "occurrences": counts["signing_pattern_drift"],
            "auto_action": "none",
            "hard_rule": "signing-pattern drift must NEVER trigger token reuse or "
                         "signed-URL reconstruction — only a warning and a fresh capture"}
    # rendition drift: moderate, request capture if repeated
    if counts.get("rendition_drift"):
        rep = counts["rendition_drift"] >= _REPEAT_MIN
        policy["rendition_drift"] = {
            "decision": "request_capture" if rep else "monitor",
            "occurrences": counts["rendition_drift"],
            "auto_action": "none"}
    # identity drift: usually means wrong title / mis-assignment → review
    if counts.get("identity_drift"):
        policy["identity_drift"] = {"decision": "queue_review",
                                    "occurrences": counts["identity_drift"],
                                    "auto_action": "none"}
    return policy


def run(argv=None) -> int:
    p = argparse.ArgumentParser(description="Phase 5 evidence-gated automation policy")
    p.add_argument("--site", required=True)
    p.add_argument("--selector-confidence", default=None)
    p.add_argument("--drift-history", default=None)
    p.add_argument("--confidence-history", default=None)
    p.add_argument("--profile-update-candidate", default=None)
    p.add_argument("--out-dir", default="./policy")
    args = p.parse_args(argv)
    from bulk_downloader.capture_ingest import posture_scan

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    sel_conf = _load(args.selector_confidence)
    drift_history = _load(args.drift_history) or []
    conf_history = _load(args.confidence_history) or []
    candidate = _load(args.profile_update_candidate) or {}

    sel_decisions = _selector_decisions(sel_conf, drift_history)
    axis_policy = _axis_policy(drift_history)

    # queues
    manual_review: List[str] = []
    capture_requests: List[str] = []
    approvals: List[str] = []

    for axis, pol in axis_policy.items():
        d = pol["decision"]
        if d in ("require_human_review", "queue_review"):
            manual_review.append(f"{axis}: {pol.get('occurrences',1)}x — {d}")
        if d in ("request_capture", "warn_and_request_capture"):
            capture_requests.append(f"{axis}: {pol.get('occurrences',1)}x — fresh capture")
    for sd in sel_decisions:
        if sd["action"] in ("distrust", "distrust_recheck"):
            manual_review.append(f"selector `{sd['selector']}` → {sd['action']} ({sd['why']})")
    if candidate.get("recommendation") == "promote after review":
        approvals.append(f"profile update for {args.site}: evidence "
                         f"{candidate.get('evidence_strength')} — ready for approval")
    elif candidate.get("evidence_strength", "").startswith("weak"):
        capture_requests.append(f"{args.site}: profile evidence weak — another capture "
                                f"would strengthen it")

    decision_report = {
        "site": args.site,
        "_status": "POLICY RECOMMENDATIONS — the system takes no action; these queue work "
                   "for a human. No selector promotion, corpus write, debt retirement, "
                   "token reuse, or live change occurs automatically.",
        "selector_decisions": sel_decisions,
        "axis_policy": axis_policy,
        "queued": {"manual_review": len(manual_review),
                   "capture_requests": len(capture_requests),
                   "profile_approvals": len(approvals)},
    }

    policy_md = [f"# Automation policy — {args.site}", "",
                 "Evidence-gated policy. The system trusts, distrusts, warns, or requests "
                 "review — it does NOT act. Hard rules: structural drift always requires "
                 "human review; signing-pattern drift never triggers token reuse or "
                 "signed-URL reconstruction (warning + fresh capture only).", "",
                 "## Selector decisions"]
    for sd in sel_decisions[:20]:
        policy_md.append(f"- `{sd['selector']}` → **{sd['action']}** ({sd['why']})")
    policy_md += ["", "## Drift-axis policy"]
    for axis, pol in axis_policy.items():
        policy_md.append(f"- {axis}: **{pol['decision']}** "
                         f"(seen {pol.get('occurrences',1)}x; auto-action: {pol['auto_action']})")
        if pol.get("hard_rule"):
            policy_md.append(f"    - HARD RULE: {pol['hard_rule']}")

    def _queue_md(title: str, items: List[str], empty: str) -> str:
        L = [f"# {title}", ""]
        L += [f"- {i}" for i in items] if items else [empty]
        return "\n".join(L)

    # POSTURE
    blob = json.dumps(decision_report, default=list) + "\n".join(policy_md) \
        + "\n".join(manual_review + capture_requests + approvals)
    leaks = posture_scan(blob)
    if leaks:
        print(f"POSTURE FAIL: signing value in policy output ({leaks}); refusing.",
              file=sys.stderr)
        return 2

    _write_text(out / "automation_policy.md", "\n".join(policy_md))
    _write_json(out / "automation_decision_report.json", decision_report)
    _write_text(out / "manual_review_queue.md",
                _queue_md(f"Manual review queue — {args.site}", manual_review,
                          "Nothing queued for manual review."))
    _write_text(out / "capture_request_queue.md",
                _queue_md(f"Capture request queue — {args.site}", capture_requests,
                          "No fresh captures requested."))
    _write_text(out / "profile_approval_queue.md",
                _queue_md(f"Profile approval queue — {args.site}", approvals,
                          "No profile updates awaiting approval."))
    print(f"Phase-5 policy artifacts written to {out}/  "
          f"review:{len(manual_review)} captures:{len(capture_requests)} "
          f"approvals:{len(approvals)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
