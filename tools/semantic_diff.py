"""Command-line interface for the standard-library semantic diff frontend."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

# Direct execution from outside the checkout must not depend on the cwd.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.code_intelligence.results import exit_code
from tools.code_intelligence.semantic_service import run_semantic_diff


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="semantic_diff.py")
    parser.add_argument("--before-tree", type=Path)
    parser.add_argument("--after-tree", type=Path)
    parser.add_argument("--before-snapshot", type=Path)
    parser.add_argument("--after-snapshot", type=Path)
    parser.add_argument("--out", type=Path, default=Path("SEMANTIC_DIFF.json"))
    parser.add_argument("--check", type=Path)
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--cst-adapter", choices=("none", "libcst"), default="none")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if sum(value is not None for value in (args.before_tree, args.before_snapshot)) != 1:
        parser.error("one before source is required")
    if sum(value is not None for value in (args.after_tree, args.after_snapshot)) != 1:
        parser.error("one after source is required")
    result = run_semantic_diff(
        before_tree=args.before_tree, after_tree=args.after_tree,
        before_snapshot=args.before_snapshot, after_snapshot=args.after_snapshot,
        output_path=args.out, check_path=args.check, gate=args.gate,
        cst_adapter=args.cst_adapter,
    )
    if args.json:
        print(json.dumps(asdict(result), sort_keys=True, default=lambda value: value.value))
    else:
        print(f"{result.state.value.upper()}: {result.summary}")
    return exit_code([result], gate=args.gate)


if __name__ == "__main__":
    raise SystemExit(main())
