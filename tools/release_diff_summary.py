#!/usr/bin/env python3
"""release_diff_summary.py — summarize changes across a version range + write
reports/release_history.md (E). Read-only except the report.
Composes changelog_analyzer. CLI: --root, --since vX.Y.Z, --until vX.Y.Z, --outdir
"""
import os as _os_rc, sys as _sys_rc
_sys_rc.path.insert(0, _os_rc.path.dirname(_os_rc.path.abspath(__file__)))
import report_core as _RC  # shared write/render helpers

import argparse, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import changelog_analyzer as CL  # type: ignore


def diff(root=".", since=None, until=None):
    rels = CL.parse(root)["releases"]
    def norm(v): return v.lstrip("v") if v else v
    since, until = norm(since), norm(until)
    sel = rels
    if until:
        for i, r in enumerate(rels):
            if r["version"] == until:
                sel = rels[i:]; break
    if since:
        out = []
        for r in sel:
            out.append(r)
            if r["version"] == since:
                break
        sel = out
    return {"selected": sel,
            "features": sum(r["features"] for r in sel),
            "fixes": sum(r["fixes"] for r in sel)}


def write_report(root=".", outdir="reports"):
    d = CL.parse(root)
    L = ["# Release history", "",
         f"- releases: **{d['count']}**",
         f"- total features: **{d['totals']['features']}**, "
         f"fixes: **{d['totals']['fixes']}**", "",
         "## Module impact (most-touched, by changelog mentions)", ""]
    for m, n in d["module_impact_top"].items():
        L.append(f"- `{m}`: {n}")
    L += ["", "## Recent releases", "",
          "| version | date | feats | fixes | title |",
          "|---------|------|------:|------:|-------|"]
    for r in d["releases"][:25]:
        L.append(f"| {r['version']} | {r['date']} | {r['features']} | {r['fixes']} | "
                 f"{(r['title'] or '')[:60]} |")
    return _RC.write_report(outdir, "release_history.md", "\n".join(L) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="."); ap.add_argument("--since"); ap.add_argument("--until")
    ap.add_argument("--outdir", default="reports"); ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    if a.report or not (a.since or a.until):
        print("wrote", write_report(a.root, a.outdir))
    else:
        d = diff(a.root, a.since, a.until)
        print(f"{len(d['selected'])} releases | features {d['features']} fixes {d['fixes']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
