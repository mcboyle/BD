#!/usr/bin/env python3
"""capture_inventory.py — list capture artifacts + yield (B). Read-only.
Thin composer over tools/capture_analytics. CLI: --root, --captures-dir, --json
"""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import capture_analytics as CA  # type: ignore


def inventory(root=".", dirs=None):
    a = CA.analyze(root, dirs)
    return {"artifacts": a["artifacts"]["items"],
            "count": a["artifacts"]["count"],
            "total_bytes": a["artifacts"]["total_bytes"],
            "by_host": a["artifacts"]["by_host"],
            "yield": a["yield"]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--captures-dir", action="append", dest="dirs")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    d = inventory(args.root, (CA._DEFAULT_DIRS + (args.dirs or [])) if args.dirs else None)
    if args.json:
        print(json.dumps(d, indent=2, default=str))
    else:
        print(f"artifacts: {d['count']} ({d['total_bytes']} bytes); by_host={d['by_host']}")
        for it in d["artifacts"]:
            print(f"  {it['path']}  host={it['host']}  {it['bytes']}b")
    return 0


if __name__ == "__main__":
    sys.exit(main())
