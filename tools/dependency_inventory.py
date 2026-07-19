#!/usr/bin/env python3
"""dependency_inventory.py — internal import graph + codebase report (F).

Read-only (except the report). Parses imports across bulk_downloader + tools, builds
the internal dependency graph (who imports which bulk_downloader.* submodule),
ranks most-imported modules (hotspots), and composes function_inventory +
module_inventory into reports/codebase_inventory.md.

CLI:  python3 tools/dependency_inventory.py [--root .] [--outdir reports] [--json]
"""
import os as _os_rc, sys as _sys_rc
_sys_rc.path.insert(0, _os_rc.path.dirname(_os_rc.path.abspath(__file__)))
import report_core as _RC  # shared write/render helpers

import argparse
import ast
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import function_inventory as FI  # type: ignore  # noqa: E402
import module_inventory as MI  # type: ignore  # noqa: E402

_ROOTS = ["bulk_downloader", "tools"]


def _imports(path):
    try:
        tree = ast.parse(open(path, encoding="utf-8", errors="replace").read())
    except (OSError, SyntaxError):
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
        elif isinstance(node, ast.Import):
            out += [a.name for a in node.names]
    return out


def graph(root=".", roots=None):
    roots = roots or _ROOTS
    importers = {}
    imported = Counter()
    for r in roots:
        base = os.path.join(root, r)
        for dirpath, _, names in os.walk(base):
            if "__pycache__" in dirpath:
                continue
            for n in names:
                if not n.endswith(".py"):
                    continue
                p = os.path.join(dirpath, n)
                rel = os.path.relpath(p, root)
                mods = [m for m in _imports(p)
                        if "bulk_downloader" in m or m.startswith("tools")]
                importers[rel] = sorted(set(mods))
                for m in set(mods):
                    imported[m] += 1
    return {"most_imported": dict(imported.most_common(20)),
            "internal_edges": sum(len(v) for v in importers.values()),
            "files_scanned": len(importers)}


def build(root="."):
    return {"functions": FI.inventory(root), "modules": MI.inventory(root),
            "dependencies": graph(root)}


def _md(d):
    ft, mt = d["functions"]["totals"], d["modules"]
    L = ["# Codebase inventory", "",
         f"- files: **{ft['files']}** · functions {ft['functions']} · "
         f"classes {ft['classes']} · methods {ft['methods']}",
         f"- total LOC: **{mt['total_loc']}** across {mt['count']} modules",
         f"- internal import edges: {d['dependencies']['internal_edges']}", "",
         "## Largest modules (LOC)", ""]
    for m in mt["largest"]:
        L.append(f"- `{m['file']}`: {m['loc']}")
    L += ["", "## Largest by defs", ""]
    for f in d["functions"]["largest_by_defs"]:
        L.append(f"- `{f['file']}`: {f['defs']} defs ({f['functions']} fn, {f['methods']} meth)")
    L += ["", "## Most-imported internal modules (hotspots)", ""]
    for m, n in d["dependencies"]["most_imported"].items():
        L.append(f"- `{m}`: {n}")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--outdir", default="reports")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    d = build(args.root)
    if args.json:
        print(json.dumps(d, indent=2))
        return 0
    p = _RC.write_report(args.outdir, "codebase_inventory.md", _md(d))
    print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
