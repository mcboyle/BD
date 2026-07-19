#!/usr/bin/env python3
"""technical_debt_report.py — technical-debt inventory (L). Read-only except report.
Scans bulk_downloader + tools for debt markers (TODO/FIXME/XXX/HACK/DEPRECATED),
compat/shim references, and duplicate function names across modules (a duplicate-
implementation heuristic). Writes reports/technical_debt.md.
CLI: --root, --outdir, --json
"""
import os as _os_rc, sys as _sys_rc
_sys_rc.path.insert(0, _os_rc.path.dirname(_os_rc.path.abspath(__file__)))
import report_core as _RC  # shared write/render helpers

import argparse, ast, json, os, re, sys
from collections import Counter, defaultdict

_ROOTS = ["bulk_downloader", "tools"]
_MARKER = re.compile(r"\b(TODO|FIXME|XXX|HACK|DEPRECATED|TEMP HACK|WORKAROUND)\b")
_SHIM = re.compile(r"\b(shim|compat|backward[- ]?compat|legacy|deprecated)\b", re.I)


def _walk_py(root, roots):
    for r in roots:
        for dp, _, names in os.walk(os.path.join(root, r)):
            if "__pycache__" in dp:
                continue
            for n in names:
                if n.endswith(".py"):
                    yield os.path.join(dp, n)


def scan(root=".", roots=None):
    roots = roots or _ROOTS
    markers = Counter()
    marker_hits, shim_hits = [], []
    func_locations = defaultdict(list)
    for p in _walk_py(root, roots):
        rel = os.path.relpath(p, root)
        try:
            text = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for m in _MARKER.finditer(line):
                markers[m.group(1)] += 1
                if len(marker_hits) < 200:
                    marker_hits.append({"file": rel, "line": i, "kind": m.group(1),
                                        "text": line.strip()[:100]})
            if _SHIM.search(line) and len(shim_hits) < 200:
                shim_hits.append({"file": rel, "line": i, "text": line.strip()[:100]})
        try:
            tree = ast.parse(text)
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    func_locations[node.name].append(rel)
        except SyntaxError:
            pass
    dup_names = {name: locs for name, locs in func_locations.items()
                 if len(locs) > 1 and not name.startswith("_") and name not in
                 ("main", "build", "scan", "analyze", "render", "inventory")}
    return {"markers_by_kind": dict(markers.most_common()),
            "marker_total": sum(markers.values()),
            "marker_hits": marker_hits,
            "shim_reference_count": len(shim_hits),
            "shim_hits": shim_hits[:60],
            "duplicate_function_names": dict(sorted(dup_names.items())[:60]),
            "duplicate_name_count": len(dup_names)}


def _md(d):
    L = ["# Technical debt inventory", "",
         f"- debt markers: **{d['marker_total']}** {d['markers_by_kind']}",
         f"- compat/shim/legacy references: **{d['shim_reference_count']}**",
         f"- duplicate top-level function names (heuristic): **{d['duplicate_name_count']}**",
         "", "## Marker samples", ""]
    for h in d["marker_hits"][:40]:
        L.append(f"- `{h['file']}:{h['line']}` [{h['kind']}] {h['text']}")
    L += ["", "## Duplicate function names (possible duplicate implementations)", ""]
    for name, locs in d["duplicate_function_names"].items():
        L.append(f"- `{name}`: {', '.join(locs)}")
    L += ["", "_Duplicate names and shim references are candidates for review, "
          "not confirmed dead code; consolidation is operator-judgement._"]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="."); ap.add_argument("--outdir", default="reports")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv); d = scan(a.root)
    if a.json:
        print(json.dumps(d, indent=2)); return 0
    p = _RC.write_report(a.outdir, "technical_debt.md", _md(d))
    print("wrote", p); return 0


if __name__ == "__main__":
    sys.exit(main())
