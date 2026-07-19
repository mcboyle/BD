#!/usr/bin/env python3
"""module_inventory.py — per-module LOC + size ranking (F). Read-only. --json"""
import argparse, json, os, sys

_ROOTS = ["bulk_downloader", "tools"]


def inventory(root=".", roots=None):
    roots = roots or _ROOTS
    mods = []
    for r in roots:
        base = os.path.join(root, r)
        for dirpath, _, names in os.walk(base):
            if "__pycache__" in dirpath:
                continue
            for n in names:
                if n.endswith(".py"):
                    p = os.path.join(dirpath, n)
                    try:
                        loc = sum(1 for _ in open(p, encoding="utf-8", errors="replace"))
                    except OSError:
                        continue
                    mods.append({"file": os.path.relpath(p, root), "loc": loc})
    mods.sort(key=lambda m: m["loc"], reverse=True)
    return {"count": len(mods), "total_loc": sum(m["loc"] for m in mods),
            "largest": mods[:15]}


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--root", default="."); ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv); d = inventory(a.root)
    print(json.dumps(d, indent=2) if a.json else
          f"modules: {d['count']} total_loc: {d['total_loc']}\nlargest: " +
          ", ".join(f"{m['file']}({m['loc']})" for m in d['largest'][:5]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
