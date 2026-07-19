#!/usr/bin/env python3
"""template.py — build a detector template from captures, or diff a fresh
capture against a stored template to detect drift.

  build:  template.py build CAP_A CAP_B [--out tmpl.json]
          Synthesize two captures into a confirmed draft, then distil the
          durable predictions into a reusable template.

  diff:   template.py diff TMPL.json CAP [--json out.json]
          Run ONE fresh capture against a stored template and report which
          predictions HELD and which DRIFTED (drift detection).

Recognition-only (see bulk_downloader/capture_template.py POSTURE): this never
reconstructs, computes, or replays signed material. Signing is checked for
marker presence only.
"""
import argparse
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bulk_downloader.capture_synth import synthesize           # noqa: E402
from bulk_downloader.capture_workbench import build_workbench   # noqa: E402
from bulk_downloader.capture_template import (                  # noqa: E402
    build_template, diff_template, HELD)


def _load_capture(path):
    """Load a capture from a .wacz (reads archive/capture.json) or a plain
    .json capture file."""
    if path.endswith(".wacz"):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.endswith("capture.json")]
            if not names:
                raise SystemExit(f"no capture.json inside {path}")
            return json.loads(z.read(names[0]))
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _cmd_build(args):
    cap_a = _load_capture(args.cap_a)
    cap_b = _load_capture(args.cap_b)
    draft = build_workbench(synthesize(cap_a, cap_b), captures=(cap_a, cap_b))
    template = build_template(draft)
    out = args.out or "template.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(template, fh, indent=2)
    g = template["goal"]
    print(f"template written to {out}")
    print(f"  host:           {template['host']}")
    print(f"  goal shape:     {g['path_template']}")
    print(f"  classification: {g['classification']}")
    print(f"  slots:          {', '.join(s['name'] for s in g['slots']) or '-'}")
    print(f"  signing:        {', '.join(g['signing_expected']) or '-'}")
    print(f"  goal_selection: {template['confirmed']['goal_selection']['status']}"
          " (flip to 'confirmed' once a maintainer verifies the pick)")


def _cmd_diff(args):
    with open(args.template, "r", encoding="utf-8") as fh:
        template = json.load(fh)
    capture = _load_capture(args.capture)
    result = diff_template(template, capture)

    lines = []
    w = lines.append
    w(f"DRIFT CHECK — {result['host']}  ->  {result['drift_verdict'].upper()}\n")
    if result["decayed"]:
        w(f"  decayed predictions: {', '.join(result['decayed'])}\n")
    w("\n  checks:\n")
    for c in result["checks"]:
        mark = "OK  " if c["status"] == HELD else f"{c['status'].upper()}"
        w(f"    [{mark:7}] {c['prediction']}\n")
        w(f"              {c.get('detail', '')}\n")
        if c["prediction"] == "signing":
            if c.get("observed_absent"):
                w(f"              absent: {', '.join(c['observed_absent'])}\n")
            if c.get("observed_new"):
                w(f"              NEW signing-like params: "
                  f"{', '.join(c['observed_new'])}\n")
    gs = result["goal_selection_check"]
    w("\n  goal selection: "
      f"{'human-confirmed' if gs['human_confirmed'] else 'heuristic'}, "
      f"shape {'present' if gs['shape_still_present'] else 'ABSENT'}\n")
    w(f"     {gs['note']}\n")
    report = "".join(lines)
    print(report)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
        print(f"[json written to {args.json}]")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build a template from two captures")
    b.add_argument("cap_a")
    b.add_argument("cap_b")
    b.add_argument("--out")
    b.set_defaults(func=_cmd_build)

    d = sub.add_parser("diff", help="diff a capture against a template")
    d.add_argument("template")
    d.add_argument("capture")
    d.add_argument("--json")
    d.set_defaults(func=_cmd_diff)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
