#!/usr/bin/env python3
"""
template_analytics.py — aggregate analytics over the template tree (#13 / P9).

Read-only reporting. Reuses tools/template_inventory.scan() for per-template facts
and tools/template_drift_report.diff_*() for draft/candidate ⇄ gold drift, then
rolls them up:

  * counts by dir + status
  * completeness-score distribution (min/mean/median/max) overall and per dir
  * gate-ready rate
  * blocked-term frequency across the tree
  * resolution-ladder coverage (which rungs appear, ladder lengths)
  * selector-group coverage (download/login/player/quality, …)
  * api.base presence rate
  * drift summary: per draft/candidate with a reviewed gold, drift items by category

stdlib-only; never writes templates, promotes, or fetches anything.

CLI:
    python3 tools/template_analytics.py [--root .] [--json] [--md OUT.md]
"""
import argparse
import json
import os
import statistics
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

import template_core as _TC  # type: ignore  # noqa: E402

# Logic now lives in template_core; keep these names as back-compat aliases.
_STANDARD_RUNGS = _TC._STANDARD_RUNGS
_dist = _TC._dist
_drift_counts = _TC._drift_counts


def analyze(root="."):
    # thin wrapper over the shared core (single scan + single scorer)
    return _TC.analytics(root)


def _md(a):
    L = ["# Template analytics", "",
         f"- root: `{a['root']}`",
         f"- templates: **{a['total_templates']}** "
         f"(reviewed {a['counts']['reviewed']}, enabled {a['counts']['enabled']}, "
         f"drafts {a['counts']['drafts']}, candidates {a['counts']['review_candidates']})",
         f"- gate-ready: **{a['gate_ready']['count']}/{a['gate_ready']['total']}** "
         f"(rate {a['gate_ready']['rate']})",
         f"- api.base present: {a['api_base_present']['count']}/{a['api_base_present']['total']}",
         ""]
    c = a["completeness"]["overall"]
    L += ["## Completeness", "",
          f"overall: n={c['n']} min={c['min']} mean={c['mean']} "
          f"median={c['median']} max={c['max']}", ""]
    L += ["## Resolution coverage", "",
          f"standard rungs covered: {a['resolution_coverage']['standard_covered']}",
          f"all rungs seen: {a['resolution_coverage']['rungs_seen']}", ""]
    L += ["## Selector-group coverage", ""]
    for g, n in a["selector_group_coverage"].items():
        L.append(f"- {g}: {n}")
    L += ["", "## Blocked-term frequency", ""]
    if a["blocked_term_frequency"]:
        for t, n in a["blocked_term_frequency"].items():
            L.append(f"- `{t}`: {n}")
    else:
        L.append("- none")
    d = a["drift"]
    L += ["", "## Drift vs gold", ""]
    if d.get("available"):
        L.append(f"compared {d['compared']} draft(s)/candidate(s); "
                 f"drift by category: {d['by_category'] or 'none'}")
    else:
        L.append("drift tool unavailable")
    if a["sanity"]:
        L += ["", "## Sanity violations", ""] + [f"- {s}" for s in a["sanity"]]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Template analytics (read-only).")
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--md", metavar="OUT", help="write a markdown report to OUT")
    args = ap.parse_args(argv)
    a = analyze(args.root)
    if args.md:
        with open(args.md, "w") as fh:
            fh.write(_md(a))
        print(f"wrote {args.md}")
    if args.json:
        print(json.dumps(a, indent=2, default=str))
    elif not args.md:
        sys.stdout.write(_md(a))
    return 0


if __name__ == "__main__":
    sys.exit(main())
