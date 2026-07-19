#!/usr/bin/env python3
"""dependency_report.py — python + npm dependency report (I). Read-only.
Thin composer over environment_report. --json"""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import environment_report as ER  # type: ignore


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--root", default="."); ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv); d = ER.report(a.root)
    s = {"python_package_count": d["python_package_count"],
         "key_python_packages": d["key_python_packages"],
         "npm": d["npm"]}
    print(json.dumps(s, indent=2) if a.json else
          f"python pkgs: {d['python_package_count']} | npm deps: "
          f"{len((d['npm'].get('dependencies') or {}))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
