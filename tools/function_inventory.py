#!/usr/bin/env python3
"""function_inventory.py — AST inventory of functions/classes/methods (F).
Read-only. Walks the given roots (default bulk_downloader + tools) and counts
module-level functions, classes, and methods. CLI: --json
"""
import argparse, ast, json, os, sys

_ROOTS = ["bulk_downloader", "tools"]


def _count_file(path):
    try:
        tree = ast.parse(open(path, encoding="utf-8", errors="replace").read())
    except (OSError, SyntaxError):
        return None
    funcs = classes = methods = 0
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs += 1
        elif isinstance(node, ast.ClassDef):
            classes += 1
            methods += sum(1 for n in node.body
                           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return {"functions": funcs, "classes": classes, "methods": methods,
            "defs": funcs + methods}


def inventory(root=".", roots=None):
    roots = roots or _ROOTS
    files = []
    for r in roots:
        base = os.path.join(root, r)
        for dirpath, _, names in os.walk(base):
            if "__pycache__" in dirpath:
                continue
            for n in names:
                if n.endswith(".py"):
                    p = os.path.join(dirpath, n)
                    c = _count_file(p)
                    if c:
                        c["file"] = os.path.relpath(p, root)
                        files.append(c)
    totals = {"files": len(files),
              "functions": sum(f["functions"] for f in files),
              "classes": sum(f["classes"] for f in files),
              "methods": sum(f["methods"] for f in files)}
    largest = sorted(files, key=lambda f: f["defs"], reverse=True)[:15]
    return {"totals": totals, "largest_by_defs": largest}


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--root", default="."); ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv); d = inventory(a.root)
    print(json.dumps(d, indent=2) if a.json else f"totals: {d['totals']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
