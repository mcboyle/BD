#!/usr/bin/env python3
"""Command-line interface for evidence-preserving route reachability."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.code_intelligence.paths import discover_repo_root
from tools.code_intelligence.reachability_service import run_reachability_cli
from tools.code_intelligence.results import exit_code


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reachability.py")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--app", required=True, help="module:attribute")
    parser.add_argument("--authenticated-fixture", help="module:function")
    parser.add_argument("--security-surface", type=Path, required=True)
    parser.add_argument("--call-graph", type=Path, required=True)
    parser.add_argument("--deferrals", type=Path)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--out", type=Path, default=Path("REACHABILITY.json"))
    parser.add_argument("--check", type=Path)
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = args.root or discover_repo_root(Path.cwd())
    result = run_reachability_cli(args=args, repo_root=root)
    if args.json:
        print(
            json.dumps(
                asdict(result),
                sort_keys=True,
                default=lambda value: value.value,
            )
        )
    else:
        print(f"{result.state.value.upper()}: {result.summary}")
    return exit_code([result], gate=args.gate)


if __name__ == "__main__":
    raise SystemExit(main())
