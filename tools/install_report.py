#!/usr/bin/env python3
"""install_report.py — installer/kit presence report (I). Read-only.
Thin composer over offline_pack_report (installers + asset summary). --json"""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import offline_pack_report as OP  # type: ignore


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--root", default="."); ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv); d = OP.report(a.root)
    s = {"installers": d["installers"], "summary": d["summary"]}
    print(json.dumps(s, indent=2) if a.json else
          f"installers: {d['installers']}\nsummary: {d['summary']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
