#!/usr/bin/env python3
"""closed_loop_learning.py — Phase 4 closed-loop site learning.

Turns offline captures and live observations into REVIEWABLE profile-update
candidates. It compares the stored profile against new evidence, tracks confidence
and drift history, and distinguishes one-off from repeated drift and confirmed from
weak evidence. Everything it emits is a suggestion for a human.

It writes NONE of: the live profile, learned-selector storage, or the corpus. It
retires no debt. It adds no detector behavior — it aggregates artifacts the earlier
phases already produced. Recognition-only posture unchanged: it handles descriptive
data (query-stripped URLs, signing marker NAMES), never signing values, never replay.

Inputs (any subset; missing ones are tolerated):
  --site-profile site_profile.json            (Phase 1)
  --selector-confidence selector_confidence.json (Phase 2)
  --live-drift live_drift_observation.json     (Phase 3)
  --download-decision download_decision_report.json (Phase 3)
  --prior-confidence-history confidence_history.json (this phase, prior run)
  --prior-drift-history drift_history.json          (this phase, prior run)

Outputs:
  profile_update_candidate.json, profile_diff_report.md, site_learning_report.md,
  confidence_history.json, drift_history.json
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

# evidence strength thresholds
_REPEAT_DRIFT_MIN = 2          # >=2 same-axis observations = repeated, not one-off
_CONFIRM_MIN_OBS = 2           # >=2 consistent observations = confirmed evidence


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


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── history tracking ────────────────────────────────────────────────
def _append_confidence_history(prior: Optional[List[Dict[str, Any]]],
                               selector_confidence: Optional[Any]) -> List[Dict[str, Any]]:
    hist = list(prior or [])
    if isinstance(selector_confidence, list) and selector_confidence:
        avg = round(sum(float(s.get("confidence", 0)) for s in selector_confidence)
                    / len(selector_confidence), 3)
        top = sorted(selector_confidence, key=lambda s: -float(s.get("confidence", 0)))[:5]
        hist.append({
            "at": _now(),
            "n_selectors": len(selector_confidence),
            "avg_confidence": avg,
            "top_selectors": [{"selector": s.get("selector"),
                               "confidence": s.get("confidence")} for s in top],
        })
    return hist


def _append_drift_history(prior: Optional[List[Dict[str, Any]]],
                          live_drift: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    hist = list(prior or [])
    if isinstance(live_drift, dict) and live_drift.get("drift_flags") is not None:
        hist.append({
            "at": _now(),
            "verdict": live_drift.get("verdict"),
            "drift_flags": live_drift.get("drift_flags", []),
        })
    return hist


def _drift_recurrence(drift_history: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Count how often each drift axis has been seen — one-off vs repeated."""
    counts: Dict[str, int] = {}
    for h in drift_history:
        for f in h.get("drift_flags", []):
            counts[f] = counts.get(f, 0) + 1
    repeated = {k: v for k, v in counts.items() if v >= _REPEAT_DRIFT_MIN}
    one_off = {k: v for k, v in counts.items() if v < _REPEAT_DRIFT_MIN}
    return {"counts": counts, "repeated": repeated, "one_off": one_off}


# ── profile diff ────────────────────────────────────────────────────
def _profile_diff(old: Optional[Dict[str, Any]],
                  new_evidence: Dict[str, Any]) -> Dict[str, Any]:
    """Compare stored profile fields to fresh evidence. Descriptive sets only."""
    old = old or {}
    diff: Dict[str, Any] = {}
    for field in ("known_rendition_descriptors", "known_identity_descriptors",
                  "known_signing_markers", "known_goal_url_shapes"):
        was = set(old.get(field) or [])
        now = set(new_evidence.get(field) or [])
        added = sorted(now - was)
        removed = sorted(was - now)
        if added or removed:
            diff[field] = {"added": added, "removed_not_seen_this_round": removed}
    return diff


def _evidence_strength(confidence_history: List[Dict[str, Any]],
                       drift_recurrence: Dict[str, Any]) -> str:
    """Confirmed vs weak: confirmed needs enough consistent observations; a single
    run is weak; conflicting/repeated drift weakens it."""
    n = len(confidence_history)
    if n >= _CONFIRM_MIN_OBS and not drift_recurrence.get("repeated"):
        return "confirmed"
    if drift_recurrence.get("repeated"):
        return "weak_repeated_drift"
    return "weak_single_observation" if n < _CONFIRM_MIN_OBS else "moderate"


def run(argv=None) -> int:
    p = argparse.ArgumentParser(description="Phase 4 closed-loop site learning")
    p.add_argument("--site", required=True)
    p.add_argument("--site-profile", default=None)
    p.add_argument("--selector-confidence", default=None)
    p.add_argument("--live-drift", default=None)
    p.add_argument("--download-decision", default=None)
    p.add_argument("--prior-confidence-history", default=None)
    p.add_argument("--prior-drift-history", default=None)
    p.add_argument("--out-dir", default="./closed_loop")
    args = p.parse_args(argv)
    from bulk_downloader.capture_ingest import posture_scan

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    profile = _load(args.site_profile) or {}
    sel_conf = _load(args.selector_confidence)
    live_drift = _load(args.live_drift)
    download = _load(args.download_decision)

    # fresh evidence summary from this round
    new_evidence = {
        "known_rendition_descriptors": (download or {}).get("available_renditions_live")
            or profile.get("known_rendition_descriptors", []),
        "known_identity_descriptors": profile.get("known_identity_descriptors", []),
        "known_signing_markers": profile.get("known_signing_markers", []),
        "known_goal_url_shapes": profile.get("known_goal_url_shapes", []),
    }

    conf_hist = _append_confidence_history(_load(args.prior_confidence_history), sel_conf)
    drift_hist = _append_drift_history(_load(args.prior_drift_history), live_drift)
    recurrence = _drift_recurrence(drift_hist)
    diff = _profile_diff(profile, new_evidence)
    strength = _evidence_strength(conf_hist, recurrence)

    candidate = {
        "_status": "SUGGESTED — reviewable only. Does NOT auto-promote the profile, "
                   "selectors, or corpus, and retires no debt. A maintainer approves.",
        "site": args.site,
        "evidence_strength": strength,
        "proposed_field_changes": diff,
        "repeated_drift_axes": recurrence["repeated"],
        "one_off_drift_axes": recurrence["one_off"],
        "recommendation": ("promote after review" if strength == "confirmed" and diff
                           else "hold — needs more consistent evidence"
                           if strength.startswith("weak") else "review"),
    }

    # reports
    pd = [f"# Profile diff report — {args.site}", ""]
    if diff:
        pd.append("Fields where this round's evidence differs from the stored profile "
                  "(descriptive sets; no signing values):\n")
        for field, ch in diff.items():
            pd.append(f"## {field}")
            pd.append(f"- newly seen: {ch['added'] or 'none'}")
            pd.append(f"- in profile but not seen this round: "
                      f"{ch['removed_not_seen_this_round'] or 'none'}")
    else:
        pd.append("No field differences — this round's evidence matches the stored profile.")
    sl = [f"# Site learning report — {args.site}", "",
          f"Evidence strength: **{strength}**.",
          f"Confidence-history points: {len(conf_hist)}; drift-history points: {len(drift_hist)}.",
          ""]
    if recurrence["repeated"]:
        sl.append(f"**Repeated drift** (>= {_REPEAT_DRIFT_MIN} occurrences): "
                  f"{recurrence['repeated']} — this is a pattern, not a one-off, and "
                  f"warrants attention.")
    if recurrence["one_off"]:
        sl.append(f"One-off drift (seen once): {recurrence['one_off']} — may be noise; "
                  f"do not act on a single observation.")
    sl.append(f"\nRecommendation: {candidate['recommendation']}. All changes are "
              f"suggested-only; a human approves before anything is promoted.")

    # POSTURE
    blob = json.dumps(candidate, default=list) + "\n".join(pd + sl) \
        + json.dumps(conf_hist, default=list) + json.dumps(drift_hist, default=list)
    leaks = posture_scan(blob)
    if leaks:
        print(f"POSTURE FAIL: signing value in output ({leaks}); refusing.", file=sys.stderr)
        return 2

    _write_json(out / "profile_update_candidate.json", candidate)
    _write_text(out / "profile_diff_report.md", "\n".join(pd))
    _write_text(out / "site_learning_report.md", "\n".join(sl))
    _write_json(out / "confidence_history.json", conf_hist)
    _write_json(out / "drift_history.json", drift_hist)
    print(f"Phase-4 closed-loop artifacts written to {out}/  strength: {strength}  "
          f"recommendation: {candidate['recommendation']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
