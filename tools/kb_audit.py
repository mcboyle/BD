#!/usr/bin/env python3
"""kb_audit.py — umbrella KB health audit (D). Read-only except the report.

Composes kb_link_validator + kb_duplicate_detector + kb_staleness_report +
check_doc_drift into one view and writes reports/kb_health.md. Detects: broken
references, duplicate guidance, stale handoffs, and (heuristically) conflicting
instructions — flagged as duplicate/near-duplicate pairs for human review, since
true conflict detection is a judgement call.

CLI:  python3 tools/kb_audit.py [--root .] [--outdir reports] [--json]
"""
import os as _os_rc, sys as _sys_rc
_sys_rc.path.insert(0, _os_rc.path.dirname(_os_rc.path.abspath(__file__)))
import report_core as _RC  # shared write/render helpers

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb_core as _KC  # type: ignore  # noqa: E402
import check_doc_drift as CDD  # type: ignore  # noqa: E402


def audit(root="."):
    # one docs walk + one drift scan, shared across links/duplicates/staleness
    collected = _KC.collect(root)
    drift = CDD.scan(root)
    return {"links": _KC.links(collected),
            "duplicates": _KC.duplicates(collected),
            "staleness": _KC.staleness(collected, drift=drift),
            "required_docs": drift["required"]}


def _md(d):
    links, dups, stale = d["links"], d["duplicates"], d["staleness"]
    missing_required = [k for k, v in d["required_docs"].items() if not v]
    L = ["# KB health audit", "",
         f"- docs scanned: {links['docs_scanned']}",
         f"- broken links: **{links['broken_count']}**",
         f"- duplicate/near-duplicate pairs (>= {dups['threshold']}): **{dups['pair_count']}**",
         f"- archival-candidate handoffs: **{stale['archival_count']}**",
         f"- stale 'current version' references: **{len(stale['stale_version_refs'])}**",
         f"- missing required docs: {missing_required or 'none'}", ""]
    if links["broken"]:
        L += ["## Broken references", ""]
        for b in links["broken"]:
            L.append(f"- `{b['doc']}` -> `{b['target']}`")
    if dups["duplicate_pairs"]:
        L += ["", "## Possible duplicate guidance (review for conflicts)", ""]
        for p in dups["duplicate_pairs"][:30]:
            L.append(f"- {p['similarity']}: `{p['a']}` ⇄ `{p['b']}`")
    if stale["stale_version_refs"]:
        L += ["", "## Stale version references", ""]
        for s in stale["stale_version_refs"][:30]:
            L.append(f"- `{s['doc']}:{s['line']}` found {s['found']}: {s['text']}")
    L += ["", "## Archival candidates (historical handoffs)", ""]
    for c in stale["archival_candidates"]:
        L.append(f"- `{c}`")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--outdir", default="reports")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    d = audit(args.root)
    if args.json:
        print(json.dumps(d, indent=2))
        return 0
    p = _RC.write_report(args.outdir, "kb_health.md", _md(d))
    print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
