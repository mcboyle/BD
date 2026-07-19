#!/usr/bin/env python3
"""environment_report.py — environment + dependency report (I). Read-only.
Reports installed Python packages (importlib.metadata), frontend npm deps
(frontend/package.json), browser assets (Playwright cache), and Python version.
Writes reports/environment.md. CLI: --root, --outdir, --json
Also serves install_report / dependency_report via --mode.
"""
import os as _os_rc, sys as _sys_rc
_sys_rc.path.insert(0, _os_rc.path.dirname(_os_rc.path.abspath(__file__)))
import report_core as _RC  # shared write/render helpers

import argparse, json, os, sys
from pathlib import Path


def _python_packages():
    try:
        import importlib.metadata as md
        return sorted((d.metadata["Name"], d.version) for d in md.distributions()
                      if d.metadata.get("Name"))
    except Exception:
        return []


def _npm_deps(root):
    p = os.path.join(root, "frontend", "package.json")
    try:
        pj = json.load(open(p))
        return {"dependencies": pj.get("dependencies", {}),
                "devDependencies": pj.get("devDependencies", {}),
                "version": pj.get("version")}
    except (OSError, ValueError):
        return {}


def _browser_assets():
    cands = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
             os.path.expanduser("~/.cache/ms-playwright")]
    found = []
    for c in cands:
        if c and os.path.isdir(c):
            try:
                found += [os.path.join(c, n) for n in os.listdir(c)]
            except OSError:
                pass
    return found[:30]


def report(root="."):
    pkgs = _python_packages()
    key = {n: v for n, v in pkgs if n.lower() in
           ("flask", "playwright", "requests", "apprise", "pillow", "lxml",
            "beautifulsoup4", "websockets", "cryptography")}
    return {"python_version": sys.version.split()[0],
            "python_package_count": len(pkgs),
            "key_python_packages": key,
            "npm": _npm_deps(root),
            "browser_assets": _browser_assets()}


def _md(d):
    L = ["# Environment report", "",
         f"- Python: {d['python_version']} · packages installed: {d['python_package_count']}",
         f"- frontend version: {d['npm'].get('version')}",
         f"- browser assets: {len(d['browser_assets'])} entry(ies)", "",
         "## Key Python packages", ""]
    for n, v in d["key_python_packages"].items():
        L.append(f"- {n} {v}")
    L += ["", "## Frontend dependencies", ""]
    for n, v in (d["npm"].get("dependencies") or {}).items():
        L.append(f"- {n} {v}")
    L += ["", "## Frontend devDependencies", ""]
    for n, v in (d["npm"].get("devDependencies") or {}).items():
        L.append(f"- {n} {v}")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="."); ap.add_argument("--outdir", default="reports")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv); d = report(a.root)
    if a.json:
        print(json.dumps(d, indent=2)); return 0
    p = _RC.write_report(a.outdir, "environment.md", _md(d))
    print("wrote", p); return 0


if __name__ == "__main__":
    sys.exit(main())
