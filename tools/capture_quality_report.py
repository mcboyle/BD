#!/usr/bin/env python3
"""capture_quality_report.py — capture quality metrics + report (B).

Read-only (except writing the report). Composes capture_analytics + the artifact
contents to report:
  * WACZ count, capture-json count
  * DOM event count, snapshot count, rrweb coverage, snapdom coverage
    (parsed from capture_*.json when present; rrweb/snapdom detected by marker keys)
  * template generation success rate (gate-ready candidates / total capture yield)

Capture artifacts live on the operator host, so in a clean sandbox the artifact
metrics are zero/none and only the yield-based success rate is meaningful — this
is stated in the report rather than hidden.

Writes reports/capture_analytics.md.

CLI:  python3 tools/capture_quality_report.py [--root .] [--captures-dir DIR ...] [--outdir reports]
"""
import os as _os_rc, sys as _sys_rc
_sys_rc.path.insert(0, _os_rc.path.dirname(_os_rc.path.abspath(__file__)))
import report_core as _RC  # shared write/render helpers

import argparse
import glob
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import capture_analytics as CA  # type: ignore  # noqa: E402
import capture_statistics as CS  # type: ignore  # noqa: E402

# marker keys we look for inside capture_*.json payloads
_RRWEB_KEYS = ("rrweb", "rrweb_events", "rrwebEvents")
_SNAPDOM_KEYS = ("snapdom", "snapDom", "snapshots")
_DOM_KEYS = ("dom_events", "domEvents", "events")


def _scan_payloads(root, dirs):
    """Parse capture_*.json artifacts for DOM/rrweb/snapdom metrics."""
    wacz = dom_events = snapshots = rrweb_hits = snapdom_hits = json_caps = 0
    for d in dirs:
        base = os.path.join(root, d)
        if not os.path.isdir(base):
            continue
        wacz += len(glob.glob(os.path.join(base, "*.wacz")))
        for p in glob.glob(os.path.join(base, "capture_*.json")):
            json_caps += 1
            try:
                data = json.load(open(p))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            for k in _DOM_KEYS:
                v = data.get(k)
                if isinstance(v, list):
                    dom_events += len(v)
            for k in _SNAPDOM_KEYS:
                v = data.get(k)
                if isinstance(v, list):
                    snapshots += len(v)
                if data.get(k):
                    snapdom_hits += 1
                    break
            if any(data.get(k) for k in _RRWEB_KEYS):
                rrweb_hits += 1
    return {"wacz": wacz, "capture_json": json_caps, "dom_events": dom_events,
            "snapshots": snapshots,
            "rrweb_coverage": (round(rrweb_hits / json_caps, 3) if json_caps else None),
            "snapdom_coverage": (round(snapdom_hits / json_caps, 3) if json_caps else None)}


def build(root=".", dirs=None):
    dirs = dirs or CA._DEFAULT_DIRS
    metrics = _scan_payloads(root, dirs)
    stats = CS.statistics(root)
    total_yield = stats["drafts"] + stats["candidates"]
    success = (round(stats["gate_ready"] / total_yield, 3) if total_yield else None)
    return {"root": os.path.abspath(root), "artifact_metrics": metrics,
            "yield": {"drafts": stats["drafts"], "candidates": stats["candidates"],
                      "gate_ready": stats["gate_ready"]},
            "template_generation_success_rate": success,
            "artifacts_present": bool(metrics["wacz"] or metrics["capture_json"])}


def _md(d):
    m = d["artifact_metrics"]
    L = ["# Capture analytics", "", f"- root: `{d['root']}`", ""]
    if not d["artifacts_present"]:
        L += ["_No capture artifacts found locally — expected in a clean sandbox; "
              "captures live on the operator host. Artifact metrics below are zero; "
              "the success rate is computed from draft/candidate yield._", ""]
    L += ["## Artifact metrics", "",
          f"- WACZ: {m['wacz']}", f"- capture JSON: {m['capture_json']}",
          f"- DOM events: {m['dom_events']}", f"- snapshots: {m['snapshots']}",
          f"- rrweb coverage: {m['rrweb_coverage']}",
          f"- snapdom coverage: {m['snapdom_coverage']}", "",
          "## Yield + generation", "",
          f"- drafts: {d['yield']['drafts']}", f"- candidates: {d['yield']['candidates']}",
          f"- gate-ready: {d['yield']['gate_ready']}",
          f"- template generation success rate: **{d['template_generation_success_rate']}**", ""]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--captures-dir", action="append", dest="dirs")
    ap.add_argument("--outdir", default="reports")
    args = ap.parse_args(argv)
    dirs = CA._DEFAULT_DIRS + (args.dirs or [])
    d = build(args.root, dirs)
    mp = _RC.write_report(args.outdir, "capture_analytics.md", _md(d))
    print(f"wrote {mp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
