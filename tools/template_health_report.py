#!/usr/bin/env python3
"""template_health_report.py — umbrella template-health report (A).

Composes template_inventory / template_analytics / template_completeness_score /
template_warning_catalog into one health view and writes:
    reports/template_health.json
    reports/template_health.md

Per-template metrics: trigger present, row selectors present, api block present,
resolutions present, network patterns present, blocked-term findings, drift vs
gold, promotion readiness. (Drift *history* accrues only once per-run snapshots
are persisted — out of scope while away; current drift is reported and the
history slot is marked accordingly.)

Read-only except writing the two report files.

CLI:  python3 tools/template_health_report.py [--root .] [--outdir reports]
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
import template_core as _TC  # type: ignore  # noqa: E402


def build(root="."):
    # thin wrapper over the shared core: ONE scan threaded to analytics/warnings/completeness
    return _TC.health(root)


def _md(d):
    s = d["summary"]
    L = ["# Template health report", "",
         f"- root: `{d['root']}`",
         f"- templates: **{s['total']}** {s['counts']}",
         f"- gate-ready rate: **{s['gate_ready_rate']}**",
         f"- completeness: mean {s['completeness']['mean']} "
         f"(min {s['completeness']['min']}, max {s['completeness']['max']})", "",
         "## Per-template", "",
         "| dir | host | status | score | trig | rows | api | res | net | ready | blocked |",
         "|-----|------|--------|------:|:----:|:----:|:---:|:---:|:---:|:-----:|---------|"]
    def yn(b):
        return "✓" if b else "·"
    for t in d["per_template"]:
        L.append(f"| {t['dir']} | {t['host']} | {t['status']} | "
                 f"{t['completeness_score']} | {yn(t['trigger_present'])} | "
                 f"{yn(t['row_selectors_present'])} | {yn(t['api_block_present'])} | "
                 f"{yn(t['resolutions_present'])} | {yn(t['network_patterns_present'])} | "
                 f"{yn(t['promotion_ready'])} | {','.join(t['blocked_terms']) or '—'} |")
    L += ["", "## Warning catalog", ""]
    for w, n in d["warnings"].items():
        L.append(f"- {w}: {n}")
    dc = d["drift_current"]
    L += ["", "## Drift vs gold (current)", "",
          (f"compared {dc.get('compared', 0)} draft(s)/candidate(s); "
           f"by category: {dc.get('by_category', {})}"
           if dc.get("available") else "drift tool unavailable"),
          "", f"_history: {d['drift_history']['note']}_", ""]
    if d["sanity"]:
        L += ["## Sanity violations", ""] + [f"- {s}" for s in d["sanity"]]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--outdir", default="reports")
    args = ap.parse_args(argv)
    d = build(args.root)
    jp = _RC.write_json(os.path.join(args.outdir, "template_health.json"), d)
    mp = _RC.write_report(args.outdir, "template_health.md", _md(d))
    print(f"wrote {jp} and {mp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
