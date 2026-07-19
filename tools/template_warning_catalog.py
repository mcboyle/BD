#!/usr/bin/env python3
"""template_warning_catalog.py — catalog of template warnings across the tree (A).

Read-only. Buckets every warning the scorer/gate can raise:
  missing_trigger, missing_row_selectors, missing_api_base, missing_resolutions,
  missing_network_patterns, blocked_terms, not_promotion_ready, sanity violations.

CLI:  python3 tools/template_warning_catalog.py [--root .] [--json]
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import template_core as _TC  # type: ignore  # noqa: E402


def catalog(root="."):
    # thin wrapper over the shared core (single scan)
    return _TC.warnings(_TC.scan(root))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    c = catalog(args.root)
    if args.json:
        print(json.dumps(c, indent=2))
    else:
        print(f"total warnings: {c['total']}")
        for w, n in c["by_warning"].items():
            print(f"  {n:>3}  {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
