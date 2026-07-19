#!/usr/bin/env python3
"""template_statistics.py — statistical view of the template tree (A).

Composes tools/template_analytics.analyze() and surfaces the distribution /
coverage tables. Read-only.

CLI:  python3 tools/template_statistics.py [--root .] [--json]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import template_analytics as TA  # type: ignore  # noqa: E402


def statistics(root="."):
    a = TA.analyze(root)
    return {
        "total": a["total_templates"],
        "counts_by_dir": a["counts"],
        "completeness": a["completeness"],
        "gate_ready_rate": a["gate_ready"]["rate"],
        "api_base_rate": a["api_base_present"]["rate"],
        "resolution_coverage": a["resolution_coverage"],
        "selector_group_coverage": a["selector_group_coverage"],
        "blocked_term_frequency": a["blocked_term_frequency"],
        "drift": a["drift"],
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    s = statistics(args.root)
    print(json.dumps(s, indent=2, default=str) if args.json else
          "\n".join(f"{k}: {v}" for k, v in s.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
