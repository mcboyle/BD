#!/usr/bin/env python3
"""multi_site_benchmark.py — test the generalization layer across unrelated
ecosystems by building a workbench per site and comparing the labels.

  run --site NAME capA capB [--site NAME capA capB ...] [--json out.json]

Each --site takes a name and its same-title capture PAIR. Reports which reusable
classes reproduced across sites (confirmed reusable), which framework-level
method properties held on all sites, which site-specific outcomes varied
(label held, no over-reach), and any anomalies (e.g. segment-role over-split on
a CDN-sharded path).

Recognition-only (see bulk_downloader/multi_site_benchmark.py POSTURE): reads
recognition-only workbench output; never reassembles HLS or computes signing.
"""
import argparse
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bulk_downloader.multi_site_benchmark import multi_site_run  # noqa: E402


def _load(path):
    if path.endswith(".wacz"):
        with zipfile.ZipFile(path) as z:
            n = [x for x in z.namelist() if x.endswith("capture.json")]
            if not n:
                raise SystemExit(f"no capture.json inside {path}")
            return json.loads(z.read(n[0]))
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _cmd_run(args):
    pairs = {}
    for spec in args.site:
        name, a, b = spec[0], spec[1], spec[2]
        pairs[name] = (_load(a), _load(b))
    res = multi_site_run(pairs)
    c = res["comparison"]
    print(f"MULTI-SITE GENERALIZATION — sites: {', '.join(c['sites'])}\n")
    for name, p in res["profiles"].items():
        print(f"  {name}: goal={p['goal_classification']} "
              f"host={p['goal_host']}")
        print(f"     signing(names)={p['signing_params']}  "
              f"identity_slots={p['identity_slot_count']} "
              f"rendition_slots={p['rendition_slot_count']}")
        print(f"     reusable_classes={p['reusable_classes']}  "
              f"robust={p['sensitivity_robust']}")
    print("\n  CONFIRMED reusable (reproduced >=2 sites):",
          c["reusable_classes_reproduced"])
    print("  CONFIRMED framework-level (robust all sites):",
          c["sensitivity_robust_across_all"])
    print("  CONFIRMED site-specific (outcome varies):",
          c["verdict"]["confirmed_site_specific"],
          "->", c["goal_classification_by_site"])
    print("  signing-opacity tested across",
          c["distinct_signing_schemes"], "distinct schemes")
    if c["verdict"]["anomalies"]:
        print("  ANOMALIES:")
        for a in c["verdict"]["anomalies"]:
            print(f"     - {a}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=2)
        print(f"\n[json written to {args.json}]")


def main(argv=None):
    p = argparse.ArgumentParser(description="multi-site generalization benchmark")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--site", nargs=3, action="append", required=True,
                   metavar=("NAME", "CAP_A", "CAP_B"),
                   help="a site name and its same-title capture pair")
    r.add_argument("--json")
    r.set_defaults(func=_cmd_run)
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
