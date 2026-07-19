#!/usr/bin/env python3
"""temporal_benchmark.py — measure drift + introspection stability for a
same-title capture collected LATER than the template was built.

  run:       temporal_benchmark.py run TEMPLATE.json LATER.wacz \\
                 [--baseline-pair A.wacz B.wacz] [--baseline-json base.json] \\
                 [--json out.json]
             Diff the later capture against the template, classify the churn
             (signing / rendition / cdn / structural / breakage), and — if a
             baseline is supplied — report how confidence, sensitivity, and
             per-assumption stability moved from before to later.

  snapshot:  temporal_benchmark.py snapshot A.wacz B.wacz [--out baseline.json]
             Build the BEFORE workbench from the original same-title pair and
             write it, so a future `run` has a baseline without re-supplying the
             original captures.

Recognition-only (see bulk_downloader/temporal_benchmark.py POSTURE): never
reconstructs, computes, or replays signed material; signing is marker-name only.
"""
import argparse
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bulk_downloader.temporal_benchmark import (  # noqa: E402
    temporal_run, snapshot_baseline,
    SIGNING_CHURN, RENDITION_CHURN, CDN_CHURN, STRUCTURAL_DRIFT_CHURN,
    BREAKAGE, NO_CHURN)


def _load_capture(path):
    """Load a capture from a .wacz (archive/capture.json) or a plain .json."""
    if path.endswith(".wacz"):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.endswith("capture.json")]
            if not names:
                raise SystemExit(f"no capture.json inside {path}")
            return json.loads(z.read(names[0]))
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _print_run(res):
    churn = res["churn"]
    print(f"TEMPORAL BENCHMARK — {res['template_host']}")
    print(f"  diff verdict : {res['diff'].get('verdict')}")
    print(f"  churn        : {', '.join(churn['churn_categories'])}")
    print(f"  expected-only: {churn['expected_only']}   "
          f"real drift: {churn['real_drift']}")
    for n in churn["notes"]:
        print(f"     - {n}")
    t = res.get("transition")
    if t is None:
        print(f"\n  transition   : {res['transition_status']}")
        return
    s = t["summary"]
    print(f"\n  ASSUMPTION TRANSITIONS (before -> later):")
    print(f"     stable={s['stable']} strengthened={s['strengthened']} "
          f"weakened={s['weakened']} changed_category={s['changed_category']}")
    for a in t["assumptions"]:
        if a["transition"] not in ("stable",):
            print(f"     [{a['transition']:16s}] {a['assumption']}")
    print(f"  CONFIDENCE (decision band before -> later):")
    for c in t["confidence"]:
        if c["transition"] != "stable":
            print(f"     [{c['transition']:13s}] {c['decision']}: "
                  f"{c['before_confidence']} -> {c['later_confidence']}")
    print(f"  SENSITIVITY (ordering robustness before -> later):")
    print(f"     became_robust={s['became_robust']} "
          f"became_contingent={s['became_contingent']}")
    for sv in t["sensitivity"]:
        if sv["transition"] in ("became_robust", "became_contingent"):
            print(f"     [{sv['transition']:16s}] {sv['assumption']}")


def _cmd_run(args):
    template = json.load(open(args.template, "r", encoding="utf-8"))
    later = _load_capture(args.later)
    pair = None
    if args.baseline_pair:
        pair = (_load_capture(args.baseline_pair[0]),
                _load_capture(args.baseline_pair[1]))
    base_json = None
    if args.baseline_json:
        base_json = json.load(open(args.baseline_json, "r", encoding="utf-8"))
    res = temporal_run(template, later, baseline_pair=pair,
                       baseline_json=base_json)
    _print_run(res)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=2)
        print(f"\n[json written to {args.json}]")


def _cmd_snapshot(args):
    a = _load_capture(args.cap_a)
    b = _load_capture(args.cap_b)
    base = snapshot_baseline(a, b)
    out = args.out or "baseline.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(base, fh, indent=2)
    print(f"baseline workbench written to {out} (host {base.get('host')})")


def main(argv=None):
    p = argparse.ArgumentParser(description="temporal drift + stability benchmark")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="diff + churn + transition")
    r.add_argument("template")
    r.add_argument("later")
    r.add_argument("--baseline-pair", nargs=2, metavar=("CAP_A", "CAP_B"))
    r.add_argument("--baseline-json")
    r.add_argument("--json")
    r.set_defaults(func=_cmd_run)
    s = sub.add_parser("snapshot", help="build a baseline workbench from the pair")
    s.add_argument("cap_a")
    s.add_argument("cap_b")
    s.add_argument("--out")
    s.set_defaults(func=_cmd_snapshot)
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
