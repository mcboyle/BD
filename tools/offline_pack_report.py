#!/usr/bin/env python3
"""offline_pack_report.py — verify offline-install assets are present (N).
Read-only; NO release packaging. Looks for wheels, npm assets, vendor/browser
assets, and checks that files referenced by installer scripts exist.
Writes reports/offline_pack_report.md. CLI: --root, --outdir, --json
"""
import os as _os_rc, sys as _sys_rc
_sys_rc.path.insert(0, _os_rc.path.dirname(_os_rc.path.abspath(__file__)))
import report_core as _RC  # shared write/render helpers

import argparse, glob, json, os, re, sys


def _find(root, patterns):
    hits = []
    for pat in patterns:
        hits += glob.glob(os.path.join(root, pat), recursive=True)
    return sorted(os.path.relpath(h, root) for h in hits)


def report(root="."):
    wheels = _find(root, ["**/*.whl", "wheels/*", "vendor/**/*.whl"])
    npm = _find(root, ["frontend/package.json", "frontend/package-lock.json",
                       "frontend/node_modules"])
    vendor = _find(root, ["**/static/vendor/*", "vendor/*", "frontend/dist/*"])
    installers = _find(root, ["setup.sh", "install*.sh", "bd-install", "bdenv.sh", "bd"])
    # validate file references inside installer scripts (best-effort)
    missing_refs = []
    for ins in installers:
        p = os.path.join(root, ins)
        try:
            text = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for m in re.findall(r"(?:\./|/)?([A-Za-z0-9_./-]+\.(?:zip|whl|sh|json|txt))", text):
            cand = os.path.join(root, m)
            if "/" in m and not os.path.exists(cand) and not m.startswith(("http", "/tmp", "/mnt")):
                if len(missing_refs) < 40:
                    missing_refs.append({"script": ins, "ref": m})
    return {"wheels": wheels, "npm_assets": npm, "vendor_assets": vendor[:50],
            "installers": installers, "unresolved_refs": missing_refs,
            "summary": {"wheels": len(wheels), "npm_assets": len(npm),
                        "vendor_assets": len(vendor), "installers": len(installers),
                        "unresolved_refs": len(missing_refs)}}


def _md(d):
    s = d["summary"]
    L = ["# Offline pack report", "",
         f"- wheels: {s['wheels']} · npm assets: {s['npm_assets']} · "
         f"vendor assets: {s['vendor_assets']} · installers: {s['installers']}",
         f"- unresolved installer file references: {s['unresolved_refs']}", ""]
    if s["wheels"] == 0:
        L += ["_No wheels found in the work tree — offline wheels live in the "
              "install kit on the operator host, not the source tree._", ""]
    L += ["## Installers", ""] + [f"- `{i}`" for i in d["installers"]]
    if d["unresolved_refs"]:
        L += ["", "## Unresolved references", ""]
        for r in d["unresolved_refs"]:
            L.append(f"- `{r['script']}` -> `{r['ref']}`")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="."); ap.add_argument("--outdir", default="reports")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv); d = report(a.root)
    if a.json:
        print(json.dumps(d, indent=2)); return 0
    p = _RC.write_report(a.outdir, "offline_pack_report.md", _md(d))
    print("wrote", p); return 0


if __name__ == "__main__":
    sys.exit(main())
