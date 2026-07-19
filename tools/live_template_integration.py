#!/usr/bin/env python3
"""live_template_integration.py — Phase 3 advisory layer for the live workflow.

Recognition and confidence guidance ONLY. This layer does NOT drive a browser,
click, navigate, fetch, or download. It sits on either side of the EXISTING live
workflow (detect.find_best_download + the live page observation the runner already
performs) and:

  * PRE-FLIGHT: turns the Phase-1 site_profile and Phase-2 selector_confidence into
    an enriched `learned` dict (the same shape find_best_download already consumes),
    with selector fallback ORDERED by confidence and expectations attached. The live
    workflow still does the actual locating/scoring/clicking against the live page.

  * POST-FLIGHT: given what the live page actually presented (candidates the existing
    workflow observed), reports whether the live page matched the learned template,
    what drifted, and why a given option would be selected — as observations, never as
    actions.

The hard line: this produces DATA (an enriched learned dict + reports), never a
program. There is no code path that emits a Playwright script, a click sequence, or
that replays a captured request. It never reuses a captured signing value or
reconstructs a signed URL — it operates on the LIVE observation and the descriptive
profiles, both of which are query-stripped / signing-masked already.

Boundaries (enforced): no request replay, no captured-token reuse, no signed-URL
reconstruction, no generated replay script, no UI bypass, no automatic corpus write,
no automatic debt retirement. Profile/selector/corpus updates are SUGGESTED only.

Reuses: detect.res_score (live resolution scoring — Phase 3 does NOT reimplement
resolution logic), selector_drift.status_for (live history), the find_best_download
`learned` schema (row_selectors/trigger_selectors/url_attribute).
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


def _load_json(path: Optional[str]) -> Optional[Dict[str, Any]]:
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


# ── PRE-FLIGHT: build an enriched `learned` dict for find_best_download ──
def build_learned_guidance(site_profile: Optional[Dict[str, Any]],
                           selector_confidence: Optional[Any]) -> Dict[str, Any]:
    """Produce the `learned` dict the existing find_best_download consumes, with
    row_selectors ORDERED by Phase-2 confidence (highest first) so the live fast
    path tries the most trustworthy selectors first and falls through to the full
    live sweep on a miss — exactly the existing contract. Attaches expectations
    from the Phase-1 profile as advisory metadata (the live workflow may ignore
    them; they never override live observation)."""
    learned: Dict[str, Any] = {"row_selectors": [], "trigger_selectors": [],
                               "_expectations": {}, "_confidence": {}}

    # selector_confidence is the Phase-2 ranked list (or a blocked marker).
    if isinstance(selector_confidence, list):
        # order by confidence desc; download/action roles → row_selectors,
        # login/trigger-ish → trigger_selectors. Confidence recorded for the caller.
        ordered = sorted(selector_confidence,
                         key=lambda s: -float(s.get("confidence", 0)))
        for s in ordered:
            sel = s.get("selector")
            if not sel:
                continue
            roles = s.get("roles") or []
            bucket = "trigger_selectors" if ("login" in roles and
                                             "download" not in roles) else "row_selectors"
            learned[bucket].append(sel)
            learned["_confidence"][sel] = s.get("confidence")

    # Phase-1 expectations (advisory only — never override live state).
    if isinstance(site_profile, dict):
        learned["_expectations"] = {
            "identity_descriptors": site_profile.get("known_identity_descriptors", []),
            "rendition_descriptors": site_profile.get("known_rendition_descriptors", []),
            "signing_markers": site_profile.get("known_signing_markers", []),  # names only
            "known_goal_url_shapes": site_profile.get("known_goal_url_shapes", []),
        }
    learned["_note"] = ("row_selectors are confidence-ordered guidance; the live "
                        "workflow scores and selects against the LIVE page and falls "
                        "through to its full sweep on a miss. Expectations are advisory "
                        "and never override live observation. No signing value here.")
    return learned


# ── POST-FLIGHT: compare what the live page presented to the template ──
def _classify_match(live_obs: Dict[str, Any],
                    expectations: Dict[str, Any]) -> Dict[str, Any]:
    """Compare a LIVE observation (produced by the existing workflow) against the
    learned expectations and classify the match / drift. live_obs is descriptive:
      {
        "identity": "<descriptor or None>",         # from the live page/url path
        "renditions": ["1080p", ...],               # live available renditions
        "signing_markers": ["token", ...],          # names only, from live url
        "goal_url_shape": "<query-stripped path>",  # live, query stripped
        "selector_hits": {"<sel>": True/False},     # which learned selectors matched live
        "via_learned": True/False,                  # did the learned fast-path hit
        "structural_ok": True/False,                # live page had a usable layout
      }
    """
    flags: List[str] = []
    exp = expectations or {}

    # identity
    exp_ids = set(exp.get("identity_descriptors") or [])
    if live_obs.get("identity") is not None and exp_ids:
        if live_obs["identity"] not in exp_ids:
            flags.append("identity_drift")

    # rendition
    exp_rends = set(exp.get("rendition_descriptors") or [])
    live_rends = set(live_obs.get("renditions") or [])
    if exp_rends and live_rends and not (live_rends & exp_rends):
        flags.append("rendition_drift")

    # signing markers (names only)
    exp_sign = set(exp.get("signing_markers") or [])
    live_sign = set(live_obs.get("signing_markers") or [])
    if exp_sign and live_sign and exp_sign != live_sign:
        flags.append("signing_pattern_drift")

    # selectors
    hits = live_obs.get("selector_hits") or {}
    if hits:
        missed = [s for s, ok in hits.items() if not ok]
        if missed and not any(hits.values()):
            flags.append("selector_drift")           # nothing learned matched
        elif missed:
            flags.append("partial_selector_drift")

    # structural
    if live_obs.get("structural_ok") is False:
        flags.append("structural_drift")

    # overall verdict
    if live_obs.get("structural_ok") is False or not (live_obs.get("renditions")
                                                      or hits):
        verdict = "unknown_layout"
    elif not flags:
        verdict = "template_matched"
    elif flags == ["partial_selector_drift"] or set(flags) <= {"rendition_drift"}:
        verdict = "template_partially_matched"
    else:
        verdict = "template_partially_matched" if "structural_drift" not in flags \
            else "structural_drift"
    return {"verdict": verdict, "drift_flags": flags}


def explain_download_decision(live_obs: Dict[str, Any]) -> Dict[str, Any]:
    """Explain which live rendition would be selected and why — using the EXISTING
    resolution scorer on the LIVE options. Reuses detect.res_score; does not invent
    resolution logic. Selects the highest-scoring CURRENTLY AVAILABLE option."""
    try:
        from bulk_downloader.detect import res_score
    except Exception:
        def res_score(t: str) -> int:  # fallback ordering by common labels
            order = {"2160": 6, "4k": 6, "1440": 5, "1080": 4, "720": 3,
                     "480": 2, "360": 1}
            t = (t or "").lower()
            for k, v in order.items():
                if k in t:
                    return v
            return 0
    options = live_obs.get("renditions") or []
    scored = sorted(((opt, res_score(opt)) for opt in options),
                    key=lambda x: -x[1])
    chosen = scored[0][0] if scored else None
    return {
        "available_renditions_live": options,
        "scored": [{"rendition": o, "score": s} for o, s in scored],
        "selected": chosen,
        "selection_basis": ("highest-scoring currently-available rendition on the live "
                            "page (detect.res_score). Selected from what the live page "
                            "presented — not from the template, not reconstructed."),
        "via_learned_fast_path": bool(live_obs.get("via_learned")),
    }


# ── reports ─────────────────────────────────────────────────────────
def _run_report(site: str, match: Dict[str, Any], decision: Dict[str, Any],
                guidance: Dict[str, Any]) -> str:
    L = [f"# Live template run report — {site}", ""]
    L += ["This run used the learned profile to GUIDE recognition and selector fallback "
          "order; the live authorized session, current page state, and visible controls "
          "drove the actual selection. The template did not replace live observation.", ""]
    L.append(f"**Template match:** {match['verdict']}")
    if match["drift_flags"]:
        L.append(f"**Drift observed:** {', '.join(match['drift_flags'])}")
    else:
        L.append("**Drift observed:** none")
    L.append("")
    L.append("## Download decision (from the live page)")
    L.append(f"- selected rendition: **{decision['selected']}**")
    L.append(f"- available live: {decision['available_renditions_live']}")
    L.append(f"- basis: {decision['selection_basis']}")
    L.append(f"- learned fast-path hit: {decision['via_learned_fast_path']}")
    L.append("")
    L.append("## Selector guidance applied (confidence-ordered)")
    rows = guidance.get("row_selectors", [])
    for sel in rows[:10]:
        conf = guidance.get("_confidence", {}).get(sel)
        L.append(f"- `{sel}`" + (f" (confidence {conf})" if conf is not None else ""))
    if not rows:
        L.append("- none (no Phase-2 selectors; live workflow used its own full sweep)")
    L += ["", "## What needs human approval",
          "Any selector promotion, profile update, or corpus entry suggested by this run is "
          "advisory only. Live behavior was unchanged except for the ORDER in which learned "
          "selectors were tried (which always falls through to the full live sweep on a miss)."]
    return "\n".join(L)


def _suggested_profile_update(site: str, match: Dict[str, Any],
                              live_obs: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "_status": "SUGGESTED — advisory only. Does NOT auto-update the profile, promote "
                   "selectors, write the corpus, or retire debt. A maintainer reviews.",
        "site": site,
        "observed_verdict": match["verdict"],
        "observed_drift": match["drift_flags"],
        "live_renditions_seen": live_obs.get("renditions") or [],
        "note": "Recognition-only; signing by marker name only; live URL query-stripped.",
    }


def run(argv=None) -> int:
    p = argparse.ArgumentParser(description="Phase 3 live-template integration (advisory)")
    p.add_argument("--site", required=True)
    p.add_argument("--site-profile", default=None, help="Phase-1 site_profile.json")
    p.add_argument("--selector-confidence", default=None,
                   help="Phase-2 selector_confidence.json")
    p.add_argument("--live-observation", default=None,
                   help="JSON of what the EXISTING live workflow observed on the page "
                        "(renditions, identity, signing-marker names, selector hits, "
                        "structural_ok, via_learned). If omitted, only PRE-FLIGHT "
                        "guidance is produced.")
    p.add_argument("--out-dir", default="./live_runs")
    p.add_argument("--emit-learned", action="store_true",
                   help="Write the enriched learned dict (pre-flight guidance) to "
                        "guidance_learned.json for the live workflow to consume.")
    args = p.parse_args(argv)
    from bulk_downloader.capture_ingest import posture_scan

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    profile = _load_json(args.site_profile)
    sel_conf = _load_json(args.selector_confidence)
    # selector_confidence.json may be a list (scored) or a blocked marker dict
    guidance = build_learned_guidance(profile, sel_conf)

    artifacts: Dict[str, Any] = {"guidance_learned": guidance}

    live = _load_json(args.live_observation)
    if live is not None:
        exp = guidance.get("_expectations", {})
        match = _classify_match(live, exp)
        decision = explain_download_decision(live)
        update = _suggested_profile_update(args.site, match, live)
        report = _run_report(args.site, match, decision, guidance)

        artifacts.update({
            "template_match_report": {"site": args.site, **match},
            "download_decision_report": {"site": args.site, **decision},
            "live_drift_observation": {"site": args.site, "verdict": match["verdict"],
                                       "drift_flags": match["drift_flags"]},
            "suggested_profile_update": update,
            "_report_md": report,
        })

    # POSTURE: no signing value, and NO executable/replay content, in any artifact.
    blob = json.dumps(artifacts, default=list)
    leaks = posture_scan(blob)
    if leaks:
        print(f"POSTURE FAIL: signing value would appear ({leaks}); refusing to write.",
              file=sys.stderr)
        return 2
    import re
    if re.search(r"page\.(goto|click|fill)|await\s|playwright|new_page\(|requests\.(get|post)",
                 blob):
        print("POSTURE FAIL: artifact contains executable/replay content; refusing.",
              file=sys.stderr)
        return 2

    # write
    if args.emit_learned:
        _write_json(out / "guidance_learned.json", guidance)
    if live is not None:
        _write_json(out / "template_match_report.json", artifacts["template_match_report"])
        _write_json(out / "download_decision_report.json", artifacts["download_decision_report"])
        _write_json(out / "live_drift_observation.json", artifacts["live_drift_observation"])
        _write_json(out / "suggested_profile_update.json", artifacts["suggested_profile_update"])
        _write_text(out / "live_template_run_report.md", artifacts["_report_md"])
        print(f"Phase-3 run artifacts written to {out}/  verdict: "
              f"{artifacts['template_match_report']['verdict']}")
    else:
        print(f"Phase-3 pre-flight guidance built. "
              f"{'Wrote guidance_learned.json.' if args.emit_learned else 'Use --emit-learned to write it.'} "
              f"Provide --live-observation for the post-flight match/drift reports.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
