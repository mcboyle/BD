#!/usr/bin/env python3
"""site_learning.py — Phase 1 capture→template continuous-learning loop.

Recognition-only. This orchestrates the EXISTING engines (capture_ingest,
capture_template, temporal_harness, capture_workbench.goal_skeleton) over a
site's captures and emits descriptive artifacts: it validates captures against a
stored template, profiles renditions, reports drift, accumulates a site profile,
scores confidence, and recommends the next capture. It adds NO analysis logic of
its own — every verdict comes from a module that is already unit-tested and
already posture-gated.

What it does NOT do (Phase-1 boundaries, all inherited from the engines):
no replay, no fetching, no downloading, no signed-URL reconstruction, no token
reuse, no generated Playwright script, no automatic corpus writes, no automatic
debt retirement. Captures teach descriptive templates; templates validate future
captures; nothing reproduces a session.

Selector learning is explicitly OUT of Phase 1 (it needs a DOM-log extractor that
the current stack does not have). This tool only REPORTS whether usable DOM/rrweb
logs are present, to scope that future Phase-2 work — it never extracts selectors.

Usage:
    # validate new capture(s) for a site against its stored template (if any),
    # and refresh that site's profile:
    python3 tools/site_learning.py \
        --captures ./captures/bros_run1.wacz ./captures/bros_run2.wacz \
        --site bros \
        --template ./site_templates/bros.template.json \
        --out-dir ./site_learning/bros

    # first time (no template yet): omit --template; the tool builds one from a
    # confirmed draft if --draft is given, else profiles what it can and says so.
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


# ── small helpers ───────────────────────────────────────────────────
def _load_json(path: Optional[str]) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def _write_json(path: Path, obj: Any) -> None:
    """Atomic .tmp-then-replace write (matches project state-file discipline)."""
    import os
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _write_text(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _severity_for_axis(verdict: str) -> str:
    """Map an engine drift verdict to cosmetic/moderate/breaking. Identity change
    is informational (expected when testing another title); rendition drift is
    moderate; structural/missing goal is breaking."""
    return {
        "held": "none",
        "identity_change": "cosmetic",          # expected, informational
        "rendition_drift": "moderate",
        "identity_and_rendition_change": "moderate",
        "structural_drift": "breaking",
        "missing": "breaking",
    }.get(verdict, "moderate")


# ── DOM/rrweb presence determination (Phase-2 scoping only) ─────────
def _dom_log_status(models: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Report whether the captures carry a usable DOM/rrweb log. This is the
    Phase-2 gate — it does NOT read or extract selectors, only counts whether a
    dom_log exists and is non-trivial, so we know if selector learning is even
    possible against these captures."""
    per = []
    any_dom = False
    for m in models:
        raw = m.get("_raw") or {}
        cnt = raw.get("dom_log_count")
        if cnt is None:
            dom = raw.get("dom_log")
            cnt = len(dom) if isinstance(dom, list) else 0
        per.append({"source": m.get("source_name"), "dom_log_events": cnt})
        any_dom = any_dom or (cnt and cnt > 0)
    return {"any_dom_log_present": bool(any_dom), "per_capture": per}


# ── report builders (prose-forward, mirror offline_capture_analyze) ──
def _template_validation_report(site: str, diffs: List[Dict[str, Any]],
                                have_template: bool) -> str:
    L = [f"# Template validation report — {site}", ""]
    if not have_template:
        L += ["No stored template was supplied for this site, so there is nothing to "
              "validate the captures against yet. Once a template exists (built from a "
              "confirmed draft), future captures will be diffed against it here. The "
              "rendition profile and site profile below still describe what these "
              "captures contain.", ""]
        return "\n".join(L)
    L += ["Each capture below was diffed against the stored template using the existing "
          "`diff_template` engine (recognition-only: structural URL match, classification, "
          "signing-marker presence — nothing is reconstructed or fetched). Verdicts are the "
          "engine's own HELD / DRIFTED / NEW per prediction.", ""]
    for d in diffs:
        src = d.get("_source", "capture")
        L.append(f"## {src}")
        overall = d.get("overall_drift", d.get("verdict", "unknown"))
        L.append(f"Overall: **{overall}**.")
        for chk in d.get("checks", []):
            pred = chk.get("prediction")
            status = chk.get("verdict", chk.get("status"))
            detail = chk.get("detail", "")
            exp = chk.get("expected")
            obs = chk.get("observed")
            line = f"- `{pred}`: {status}"
            if detail:
                line += f" — {detail}"
            L.append(line)
            if exp or obs:
                L.append(f"    - expected: {exp}")
                L.append(f"    - observed: {obs}  (query-stripped)")
        if d.get("decayed"):
            L.append(f"\nPredictions that drifted: {', '.join(d['decayed'])}.")
        L.append("")
    return "\n".join(L)


def _rendition_profile(site: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """Rendition intelligence from goal_skeleton's RENDITION/IDENTITY slots and the
    temporal harness's rendition axis. Path identifiers only — never signing."""
    rends: List[str] = []
    idents: List[str] = []
    for pc in result.get("per_capture", []):
        a = pc.get("analysis", {})
        for s in (a.get("rendition_slots") or []):
            if s and s not in rends:
                rends.append(s)
        for s in (a.get("identity_slots") or []):
            if s and s not in idents:
                idents.append(s)
    temporal = result.get("temporal") or {}
    rend_axis = (temporal.get("rendition") or {}) if isinstance(temporal, dict) else {}
    return {
        "site": site,
        "observed_rendition_descriptors": sorted(rends),
        "observed_identity_descriptors": sorted(idents),
        "rendition_count_distinct": len(set(rends)),
        "rendition_varies_across_series": rend_axis.get("outcome") == "falsified"
            if rend_axis else None,
        "rendition_axis_note": rend_axis.get("detail") if rend_axis else
            "single capture or no series — rendition variation not measured",
        "note": ("Descriptors are PATH identifiers extracted by goal_skeleton; no "
                 "signing value appears. 'Highest resolution' is left to the operator "
                 "to confirm — the tool records the descriptors seen, it does not "
                 "rank-by-resolution without a confirmed mapping."),
    }


def _site_drift_report(site: str, result: Dict[str, Any],
                       diffs: List[Dict[str, Any]]) -> str:
    L = [f"# Site drift report — {site}", ""]
    L += ["Drift is reported on the axes the network-log model supports: goal URL shape "
          "(structural), identity, rendition, and signing-marker presence. Verdicts come "
          "from `diff_template` and the temporal harness. DOM/workflow drift is NOT "
          "covered here — that needs the Phase-2 selector layer. Signing is compared by "
          "in-memory fingerprint only; no value or fingerprint appears below.", ""]
    # template-diff severities
    if diffs:
        L.append("## Template-diff drift (per capture)")
        for d in diffs:
            for chk in d.get("checks", []):
                v = chk.get("verdict", chk.get("status"))
                sev = _severity_for_axis(v)
                if sev != "none":
                    L.append(f"- {d.get('_source','capture')} / `{chk.get('prediction')}`: "
                             f"{v} → **{sev}**")
        L.append("")
    # temporal axes
    temporal = result.get("temporal") or {}
    if isinstance(temporal, dict) and "error" not in temporal:
        L.append("## Temporal drift (across the capture series)")
        for axis in ("identity", "rendition", "signing", "structural"):
            ax = temporal.get(axis)
            if isinstance(ax, dict):
                L.append(f"- {axis}: {ax.get('outcome','?')} — {ax.get('detail','')}")
        L.append("")
    elif isinstance(temporal, dict) and "error" in temporal:
        L.append(f"_Temporal series not run: {temporal['error']}._\n")
    return "\n".join(L)


def _site_profile(site: str, result: Dict[str, Any],
                  rendition: Dict[str, Any], prior: Optional[Dict[str, Any]],
                  diffs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The accumulating site KB: URL/rendition/signing slice only (Phase 1).
    Carries confidence_history and drift_history appended across runs."""
    prior = prior or {}
    conf_hist = list(prior.get("confidence_history", []))
    drift_hist = list(prior.get("drift_history", []))

    # signing markers seen (by name/type only)
    signing_markers: List[str] = []
    goal_urls: List[str] = []
    for pc in result.get("per_capture", []):
        a = pc.get("analysis", {})
        for m in (a.get("path_signing") or []):
            nm = m.get("marker") if isinstance(m, dict) else m
            if nm and nm not in signing_markers:
                signing_markers.append(nm)
        g = a.get("goal_url")
        if g and g not in goal_urls:
            goal_urls.append(g)

    # a compact drift snapshot for the history
    snapshot = {
        "n_captures": result.get("n_captures"),
        "labels": result.get("labels"),
        "any_template_drift": any(d.get("decayed") for d in diffs) if diffs else None,
    }
    drift_hist.append(snapshot)

    return {
        "site": site,
        "known_goal_url_shapes": sorted(goal_urls),       # query-stripped
        "known_rendition_descriptors": rendition.get("observed_rendition_descriptors", []),
        "known_identity_descriptors": rendition.get("observed_identity_descriptors", []),
        "known_signing_markers": sorted(signing_markers),  # names/types only
        "confidence_history": conf_hist,                   # appended by health report
        "drift_history": drift_hist,
        "phase": "1 (URL/rendition/signing only; no selectors/workflows yet)",
        "note": ("Accumulating descriptive KB. Selectors, workflows, and DOM-level "
                 "download patterns are Phase 2 and are NOT in this profile. No signing "
                 "value is stored — markers are names/types only; URLs are query-stripped."),
    }


def _confidence(result: Dict[str, Any], diffs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Coarse confidence scores derived from what the engines reported. Honest and
    bounded: these are recognition-confidence signals, not success guarantees."""
    n = result.get("n_captures", 0)
    # selector/login/download confidence are DOM-dependent → Phase 2; report as such.
    goal_found = all("error" not in pc.get("analysis", {})
                     for pc in result.get("per_capture", [])) and n > 0
    template_held = None
    if diffs:
        template_held = not any(d.get("decayed") for d in diffs)
    return {
        "goal_recognition_confidence": "high" if goal_found else "low",
        "template_match_confidence": (None if template_held is None
                                      else ("high" if template_held else "drifted")),
        "rendition_recognition_confidence": "high"
            if any((pc.get("analysis", {}).get("rendition_slots"))
                   for pc in result.get("per_capture", [])) else "low",
        "login_confidence": "not_measured_phase1_dom_required",
        "selector_confidence": "not_measured_phase1_dom_required",
        "download_confidence": "not_measured_phase1_dom_required",
    }


def _site_health_report(site: str, conf: Dict[str, Any], dom: Dict[str, Any]) -> str:
    L = [f"# Site health report — {site}", ""]
    L += ["Confidence scoring from the recognition engines. The login/selector/download "
          "scores are DOM-dependent and are deliberately left unmeasured in Phase 1 — they "
          "require the Phase-2 selector layer. Reported here as `not_measured` rather than "
          "guessed.", ""]
    for k, v in conf.items():
        L.append(f"- {k}: **{v}**")
    L.append("")
    L.append("## DOM/rrweb presence (Phase-2 readiness)")
    L.append(f"- usable DOM log present in captures: **{dom['any_dom_log_present']}**")
    for pc in dom["per_capture"]:
        L.append(f"    - {pc['source']}: {pc['dom_log_events']} DOM events")
    L.append("")
    if dom["any_dom_log_present"]:
        L.append("DOM logs are present, so Phase-2 selector learning is feasible against "
                 "these captures. See the Phase-2 design note.")
    else:
        L.append("No usable DOM log in these captures. Selector learning is not possible "
                 "until captures are taken with DOM capture enabled (see Phase-2 note).")
    return "\n".join(L)


def _next_capture_recommendation(site: str, result: Dict[str, Any],
                                 conf: Dict[str, Any], dom: Dict[str, Any]) -> str:
    L = [f"# Next-capture recommendation — {site}", ""]
    unknowns: List[str] = []
    if result.get("n_captures", 0) < 2:
        unknowns.append("Only one capture — no cross-session signal yet. A second "
                        "same-title capture would enable the temporal/diff axes "
                        "(which descriptors are stable vs. per-session).")
    temporal = result.get("temporal") or {}
    if isinstance(temporal, dict):
        for name in ("signing",):
            ax = temporal.get(name)
            if isinstance(ax, dict) and "undeterminable" in str(ax.get("detail", "")).lower():
                unknowns.append("Signing drift is undeterminable because values were "
                                "scrubbed in every capture — expected under redaction; "
                                "drift on that axis cannot be measured (and that is fine).")
    if not dom["any_dom_log_present"]:
        unknowns.append("No DOM log captured — selector/login/download confidence cannot "
                        "be established. Enabling DOM capture is the single highest-value "
                        "change for selector intelligence (Phase 2).")
    if conf.get("template_match_confidence") == "drifted":
        unknowns.append("The stored template drifted against the latest capture — a "
                        "fresh confirmed capture would re-baseline the template.")
    if not unknowns:
        unknowns.append("Recognition is solid on the measured axes. Highest information "
                        "gain now is a second same-title capture (if not already paired) "
                        "to harden the stable-vs-per-session distinction.")
    L += ["## What remains unknown / what would improve confidence", ""]
    for u in unknowns:
        L.append(f"- {u}")
    L += ["", "## Fragility", "",
          "Sites whose latest diff shows `structural_drift` or a missing goal are the "
          "most fragile (breaking); rendition drift is moderate; identity change is "
          "expected and informational. See the site drift report for this site's axes."]
    return "\n".join(L)


def _suggested_corpus_entry(site: str, result: Dict[str, Any],
                            diffs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """A REVIEWABLE DRAFT only. No id, no resolves pointer, outcome unconfirmed —
    so it cannot be written to the corpus or retire debt automatically. A human
    finalizes it via the corpus-entry templates. This mirrors the existing
    ingest rule that suggested entries are drafts, not writes."""
    drifted = any(d.get("decayed") for d in diffs) if diffs else None
    return {
        "_status": "DRAFT — reviewable only; not a corpus write. A human assigns the id, "
                   "scopes the observation, and appends it. Cannot retire debt.",
        "id": None,
        "date": None,
        "version": None,
        "subject": f"template_validation_{site}",
        "category": "drift_verdict",
        "conclusion_class": "method_validation",
        "prediction": "the stored template's goal/rendition/signing predictions hold "
                      "across the new capture(s)",
        "observation": ("template held on all checked predictions" if drifted is False
                        else "one or more template predictions drifted; see report"
                        if drifted else "no stored template — captures profiled only"),
        "outcome": "untested",
        "evidence": f"site_learning run over {result.get('labels')}",
        "resolves": None,
        "notes": "Recognition-only; signing by marker name only; URLs query-stripped. "
                 "Outcome left 'untested' until a human reviewer confirms.",
    }


# ── orchestration ───────────────────────────────────────────────────
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Phase 1 capture→template learning loop")
    p.add_argument("--captures", nargs="+", required=True,
                   help="Capture paths (.wacz or recon .json) for one site.")
    p.add_argument("--site", required=True, help="Site name (profile/report stem).")
    p.add_argument("--template", default=None,
                   help="Stored template JSON to validate against (omit for first run).")
    p.add_argument("--prior-profile", default=None,
                   help="Existing site_profile.json to append history onto.")
    p.add_argument("--out-dir", default="./site_learning",
                   help="Output dir for the reports/JSON (default ./site_learning).")
    return p


def run(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    from bulk_downloader.capture_ingest import analyze_captures, posture_scan
    from bulk_downloader.capture_template import diff_template, migrate_template

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 1) analyze captures through the existing engine
    result = analyze_captures(args.captures)
    models = result.get("models", [])

    # 2) DOM presence (Phase-2 gate only — no extraction)
    dom = _dom_log_status(models)

    # 3) template validation (if a template was supplied)
    template = _load_json(args.template)
    diffs: List[Dict[str, Any]] = []
    if template:
        template = migrate_template(template)
        for m in models:
            d = diff_template(template, m.get("_raw") or {})
            d["_source"] = m.get("source_name")
            diffs.append(d)

    # 4) build artifacts
    rendition = _rendition_profile(args.site, result)
    prior = _load_json(args.prior_profile)
    profile = _site_profile(args.site, result, rendition, prior, diffs)
    conf = _confidence(result, diffs)

    tv = _template_validation_report(args.site, diffs, bool(template))
    dr = _site_drift_report(args.site, result, diffs)
    hr = _site_health_report(args.site, conf, dom)
    nr = _next_capture_recommendation(args.site, result, conf, dom)
    draft = _suggested_corpus_entry(args.site, result, diffs)

    # 5) posture self-check: NO report may contain a signing value. Fail closed.
    blob = "\n".join([tv, dr, hr, nr, json.dumps(rendition), json.dumps(profile),
                      json.dumps(draft)])
    leaks = posture_scan(blob)
    if leaks:
        print(f"POSTURE FAIL: a signing value would appear in output ({leaks}); "
              f"refusing to write.", file=sys.stderr)
        return 2

    # 6) write
    _write_text(out / "template_validation_report.md", tv)
    _write_json(out / "rendition_profile.json", rendition)
    _write_text(out / "site_drift_report.md", dr)
    _write_json(out / "site_profile.json", profile)
    _write_text(out / "site_health_report.md", hr)
    _write_text(out / "next_capture_recommendation.md", nr)
    _write_json(out / "suggested_corpus_entry.json", draft)

    print(f"Phase-1 learning artifacts written to {out}/")
    print(f"  captures analyzed: {result.get('n_captures')}  "
          f"template: {'yes' if template else 'none'}  "
          f"DOM log present: {dom['any_dom_log_present']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
