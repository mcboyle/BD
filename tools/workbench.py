#!/usr/bin/env python3
"""workbench.py — operator CLI: capture(s) -> reviewable detector draft.

Runs the C-T1 synth over two captures of the same action, then the
``capture_workbench`` promotion layer, and prints a human-readable detector
draft — typed slots with stability verdicts, provenance edges, candidate
extraction patterns, and an explicit known-vs-inferred / recoverable-vs-not
breakdown. Optionally writes the draft as JSON.

Recognition only: this reads captures and emits a *draft detector* for the
operator to review. It never fetches, replays, or reconstructs anything;
signing material is left opaque.

Usage:
    python3 tools/workbench.py CAP_A.json CAP_B.json [--json OUT.json]
    python3 tools/workbench.py CAP.wacz CAP2.wacz        # WACZ also accepted
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load_capture(path: str) -> Dict[str, Any]:
    """Load a capture dict from a .json file or a .wacz archive (reading the
    recon capture JSON the project's wacz_export writes inside it)."""
    p = Path(path)
    if p.suffix.lower() == ".wacz" or zipfile.is_zipfile(p):
        with zipfile.ZipFile(p) as zf:
            # The recon capture is stored as a JSON resource; pick the largest
            # .json that parses to a dict with a network_log.
            cands = [n for n in zf.namelist() if n.lower().endswith(".json")]
            best = None
            for n in sorted(cands, key=lambda n: -zf.getinfo(n).file_size):
                try:
                    d = json.loads(zf.read(n))
                except Exception:
                    continue
                if isinstance(d, dict) and "network_log" in d:
                    best = d
                    break
            if best is None:
                raise SystemExit(f"no recon capture JSON found inside {path}")
            return best
    return json.loads(p.read_text(encoding="utf-8"))


def _print_report(draft_d: Dict[str, Any]) -> None:
    w = sys.stdout.write
    w("\n" + "=" * 72 + "\n")
    w(f"  DETECTOR DRAFT — {draft_d['host'] or '(unknown host)'}\n")
    w(f"  entry: {draft_d.get('entry_url') or '-'}\n")
    w(f"  overall confidence: {draft_d['confidence']}   "
      f"(goal: {draft_d.get('goal_request') or 'none identified'})\n")
    w("=" * 72 + "\n")

    plan = draft_d.get("change_plan", [])
    if plan:
        w("\nCHANGE PLAN — prioritized (read this first)\n")
        for i, r in enumerate(plan, 1):
            w(f"  {i}. [{r['category']}]\n")
            w(f"     action: {r['action']}\n")
            w(f"     why:    {r['why']}\n")
            if r.get("refs"):
                w(f"     refs:   {', '.join(str(x) for x in r['refs'])}\n")

    flow = draft_d.get("uncertainty_flow")
    if flow and flow.get("highest_carry"):
        w("\nUNCERTAINTY FLOW — what rests on each assumption\n")
        w("  assumptions carrying the most uncertainty downstream:\n")
        for c in flow["highest_carry"]:
            tag = "" if c.get("capture_resolvable") else "  [verify by hand — "\
                "no capture resolves this]"
            w(f"    {c['node']}  (downstream weight {c['downstream_weight']})"
              f"{tag}\n")
            w(f"       {c['label']}\n")
            if c.get("carries"):
                w(f"       rests on it: {', '.join(c['carries'])}\n")
        for wi in flow.get("what_if", []):
            w(f"  what-if: {wi['hypothesis']}\n")
            w(f"     would change: {', '.join(wi['would_change'])}\n")
            w(f"     effect: {wi['effect']}\n")

    unc = draft_d.get("uncertainty")
    if unc and unc.get("ranked"):
        w("\nWHERE THE NEXT HOUR PAYS OFF — uncertainty reduction ranking\n")
        bs = unc.get("bodies_retained")
        bs_str = ("bodies already retained in these captures" if bs is True
                  else "bodies not retained" if bs is False
                  else "body state unknown")
        w(f"  (total uncertainty weight: {unc['total_uncertainty_weight']}; "
          f"{bs_str})\n")
        for i, c in enumerate(unc["ranked"], 1):
            w(f"  {i}. {c['evidence']}  "
              f"~{c['estimated_uncertainty_reduction_pct']}% "
              f"({c['weighted_resolved']}/{c['of_total']})\n")
            w(f"     why: {c['note']}\n")
            w(f"     moves: {c['slots_that_move_category']}\n")
            if c.get("promotes_to_observed"):
                w(f"     promotes to observed: "
                  f"{', '.join(c['promotes_to_observed'])}\n")
            if c.get("impact_dependencies"):
                w(f"     affects conclusions: "
                  f"{'; '.join(c['impact_dependencies'])}\n")

    imp = draft_d.get("impact")
    if imp:
        w("\nDETECTOR IMPACT — how this maps to a code change\n")
        gc = imp.get("goal_classification")
        if gc:
            w(f"  goal classifies as: {gc.get('type')}  "
              f"({'; '.join(gc.get('reasons', []))})\n")
        w(f"  {imp.get('summary','')}\n")
        if imp.get("likely_components"):
            w("  likely-affected components:\n")
            for c in imp["likely_components"]:
                w(f"    - {c}\n")
        if imp.get("confidence_raising_captures"):
            w("  captures that would raise confidence:\n")
            for c in imp["confidence_raising_captures"]:
                w(f"    - {c}\n")

    sk = draft_d.get("skeleton")
    if sk:
        w("\nGOAL URL SKELETON — the detector target\n")
        w(f"  template: {sk['url_template']}\n")
        w(f"  match:    {sk['path_template']}\n")
        if sk.get("skeleton_slots"):
            w("  addressable path segments (candidate id slots):\n")
            for s in sk["skeleton_slots"]:
                w(f"    {{{s['name']}}} = {s['sample']!r} ({s['shape']})  "
                  f"regex: {s['regex']}  conf={s['confidence']}\n")
                w(f"       {s['rationale']}\n")
        if sk.get("signing_params"):
            sp = ", ".join(p["param"] for p in sk["signing_params"])
            w(f"  signing params (opaque, live-supplied): {sp}\n")
        if sk.get("literal_segments"):
            w(f"  structural literals: {'/'.join(sk['literal_segments'])}\n")

    slots = draft_d.get("slots", [])
    if slots:
        w(f"\nTYPED SLOTS ({len(slots)})\n")
        for s in slots:
            w(f"  [{s['verdict']:<24}] {s['param']:<22} "
              f"{s['shape']:<10} conf={s['confidence']:<6} {s.get('basis','')}\n")
            w(f"       {s['rationale']}\n")
            for e in s.get("evidence", []):
                w(f"       evidence: {e}\n")
            if s.get("provenance"):
                w(f"       provenance: {s['provenance']}\n")
            if s.get("affects"):
                w(f"       affects: {s['affects']}\n")
            if s.get("strengthen"):
                w(f"       strengthen: {s['strengthen']}\n")

    edges = draft_d.get("provenance_edges", [])
    if edges:
        w(f"\nPROVENANCE EDGES ({len(edges)}) — stable across value rotation\n")
        for e in edges:
            w(f"  {e['param']} <- {e.get('from_source')}  "
              f"(conf={e.get('confidence')})\n")
            w(f"       {e.get('note','')}\n")

    pats = draft_d.get("draft_patterns", [])
    if pats:
        w(f"\nCANDIDATE EXTRACTION PATTERNS ({len(pats)}) — review before use\n")
        for p in pats:
            w(f"  (\"{p['key']}\", r\"\"\"{p['regex']}\"\"\"),"
              f"   # {p['sample_shape']}, conf={p['confidence']}\n")
            w(f"       {p['rationale']}\n")

    opaque = draft_d.get("opaque_slots", [])
    if opaque:
        w(f"\nOPAQUE / SIGNING SLOTS ({len(opaque)}) — supplied live, NOT "
          f"reconstructed\n")
        for o in opaque:
            w(f"  {o['param']} ({o['shape']}) in {o['request']}\n")

    unrec = draft_d.get("unrecoverable", [])
    if unrec:
        w(f"\nUNRECOVERABLE BY OBSERVATION ({len(unrec)}) — client-computed / "
          f"session-local\n")
        for u in unrec:
            w(f"  {u['param']} in {u['request']}\n")
            w(f"       {u['reason']}\n")

    w(f"\nPOSTURE: {draft_d.get('posture','')}\n")
    stab = draft_d.get("assumption_stability")
    if stab and stab.get("assumptions"):
        w("\nASSUMPTION STABILITY — how much to trust each (vs how much rests "
          "on it)\n")
        w("  verify-first (downstream weight x fragility):\n")
        for r in stab.get("verify_first", []):
            if r.get("group"):
                w(f"    [{r['count']}x] assume:{r['group']}:*  "
                  f"risk={r['risk_score']}  (weight {r['downstream_weight']}, "
                  f"stability {r['stability_band']}, {r['basis']}) — "
                  f"triage as a block\n")
            else:
                w(f"    {r['node']}  risk={r['risk_score']}  "
                  f"(weight {r['downstream_weight']}, stability "
                  f"{r['stability_band']}, {r['basis']})\n")
        # per-assumption detail, collapsing homogeneous families (e.g. the
        # source-unknown telemetry block) to one representative
        w("  per-assumption:\n")
        shown_fam = set()
        for a in stab["assumptions"]:
            fam = a["node"].split(":")[1] if a["node"].startswith("assume:") \
                else a["node"]
            family_size = sum(
                1 for x in stab["assumptions"]
                if (x["node"].split(":")[1] if x["node"].startswith("assume:")
                    else x["node"]) == fam)
            if family_size > 3:
                if fam in shown_fam:
                    continue
                shown_fam.add(fam)
                w(f"    assume:{fam}:* [{family_size}x, identical profile] "
                  f"[{a['basis']}, stability {a['stability_band']}]\n")
            else:
                w(f"    {a['node']}  [{a['basis']}, stability "
                  f"{a['stability_band']}]\n")
            w(f"       survival: held across {a['survival']['captures_examined']}"
              f" capture(s) — {a['survival']['strength']}\n")
            inv = a["would_invalidate"]
            w(f"       perturbation: title={inv['different_title']}, "
              f"sessions={inv['more_sessions']}, player={inv['player_config']}, "
              f"workflow={inv['workflow']}\n")
            w(f"       scope: {a['scope']}\n")

    contra = draft_d.get("contradictions", [])
    w("\nCONSISTENCY PASS — cross-layer contradiction check\n")
    if not contra:
        w("  no contradictions found — the layers agree\n")
    else:
        for c in contra:
            w(f"  [{c['severity']}] {c['check']}\n")
            w(f"     {c['detail']}\n")
            w(f"     nodes: {', '.join(c['nodes'])}\n")

    br = draft_d.get("blast_radius")
    if br and br.get("by_assumption"):
        w("\nFAILURE BLAST RADIUS — cost of each assumption being wrong\n")
        for r in br["by_assumption"][:6]:
            f = r["if_it_fails"]
            w(f"  {r['assumption']}  -> {r['nodes_to_reconsider']} node(s), "
              f"~{r['fraction_of_draft_pct']}% of the draft\n")
            if f["collapsed_impact"]:
                w(f"     impact collapses: {', '.join(f['collapsed_impact'])}\n")
            if f["changed_recommendations"]:
                w(f"     recommendations change: "
                  f"{', '.join(f['changed_recommendations'])}\n")
            if f["invalid_findings"]:
                w(f"     findings invalidated: "
                  f"{', '.join(f['invalid_findings'])}\n")

    gen = draft_d.get("generalization")
    if gen:
        w("\nGENERALIZATION — reusable vs site-specific\n")
        w(f"  {gen['summary']}\n")
        if gen.get("framework_level"):
            w("  directly reusable (transfers to other detectors as-is):\n")
            for g in gen["framework_level"]:
                w(f"    - {g['label']}\n")
        if gen.get("reusable_classes"):
            w("  reusable rule-classes (the rule transfers; instances local):\n")
            for g in gen["reusable_classes"]:
                w(f"    - {g['class']} (x{g['instances']}) — {g['why']}\n")
        if gen.get("site_specific"):
            w("  site-specific (this capture/host/title only):\n")
            for g in gen["site_specific"][:8]:
                w(f"    - {g['label']}\n")
            if len(gen["site_specific"]) > 8:
                w(f"    ... +{len(gen['site_specific']) - 8} more\n")

    notes = draft_d.get("notes", [])
    if notes:
        w("\nNOTES\n")
        for n in notes:
            w(f"  - {n}\n")
    w("\n")


def run(argv=None) -> int:
    ap = argparse.ArgumentParser(description="capture(s) -> detector draft")
    ap.add_argument("capture_a", help="first capture (.json or .wacz)")
    ap.add_argument("capture_b", help="second capture of the SAME action")
    ap.add_argument("--json", dest="json_out",
                    help="also write the draft as JSON to this path")
    args = ap.parse_args(argv)

    from bulk_downloader.capture_synth import synthesize
    from bulk_downloader.capture_workbench import build_workbench

    cap_a = _load_capture(args.capture_a)
    cap_b = _load_capture(args.capture_b)
    synth = synthesize(cap_a, cap_b)
    draft = build_workbench(synth, captures=(cap_a, cap_b))
    draft_d = draft.to_dict()

    _print_report(draft_d)
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(draft_d, indent=2, ensure_ascii=False),
            encoding="utf-8")
        print(f"  draft JSON written to {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
