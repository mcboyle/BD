#!/usr/bin/env python3
"""capture_statistics.py — aggregate capture stats (B). Read-only.
Composes capture_analytics; reports artifact totals + per-host yield rollup.
CLI: --root, --json
"""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import capture_analytics as CA  # type: ignore


def statistics(root="."):
    a = CA.analyze(root)
    y = a["yield"]
    drafts = candidates = gate_ready = 0
    if y.get("available"):
        for v in y["per_host"].values():
            drafts += v["drafts"]; candidates += v["candidates"]; gate_ready += v["gate_ready"]
    return {"artifact_count": a["artifacts"]["count"],
            "artifact_bytes": a["artifacts"]["total_bytes"],
            "hosts_with_yield": len(y.get("per_host", {})) if y.get("available") else 0,
            "drafts": drafts, "candidates": candidates, "gate_ready": gate_ready}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    s = statistics(args.root)
    print(json.dumps(s, indent=2) if args.json else
          "\n".join(f"{k}: {v}" for k, v in s.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
