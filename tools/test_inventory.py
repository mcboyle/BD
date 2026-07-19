#!/usr/bin/env python3
"""test_inventory.py — count test files + test functions via AST (G). Read-only. --json"""
import argparse, ast, json, os, sys


def inventory(root="."):
    base = os.path.join(root, "tests")
    files = []
    total_tests = 0
    for n in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        if not (n.startswith("test_") and n.endswith(".py")):
            continue
        p = os.path.join(base, n)
        try:
            tree = ast.parse(open(p, encoding="utf-8", errors="replace").read())
        except (OSError, SyntaxError):
            continue
        tests = sum(1 for node in ast.walk(tree)
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name.startswith("test_"))
        files.append({"file": n, "tests": tests})
        total_tests += tests
    return {"test_files": len(files), "total_tests": total_tests,
            "largest": sorted(files, key=lambda f: f["tests"], reverse=True)[:15]}


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--root", default="."); ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv); d = inventory(a.root)
    print(json.dumps(d, indent=2) if a.json else
          f"test files: {d['test_files']} | total tests: {d['total_tests']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
