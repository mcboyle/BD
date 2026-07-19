#!/usr/bin/env python3
"""template_completeness_score.py — per-template + aggregate completeness (A).

Thin composer over tools/template_inventory.assess() (single source of the 0-100
score). Read-only.

CLI:  python3 tools/template_completeness_score.py [--root .] [--json]
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import template_core as _TC  # type: ignore  # noqa: E402


def score_tree(root="."):
    # thin wrapper over the shared core (single scan + single scorer)
    return _TC.completeness(_TC.scan(root))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    d = score_tree(args.root)
    if args.json:
        print(json.dumps(d, indent=2))
    else:
        for r in d["rows"]:
            print(f"{r['dir']}/{r['host']:<24} {r['score']:>3}/100 "
                  f"{'ready' if r['promotion_ready'] else 'needs-review'} "
                  f"missing={r['missing']}")
        a = d["aggregate"]
        print(f"\naggregate: n={a['n']} mean={a['mean']} min={a['min']} "
              f"max={a['max']} fully_complete={a['fully_complete']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
