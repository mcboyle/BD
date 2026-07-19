#!/usr/bin/env python3
"""check_skip_baseline.py — PT9 release-time skip-count gate.

The runner reports the skipped count in its summary line but does NOT
gate on it: a refactor that mass-skips tests (broken conftest, import
error, path change) passes the runner's `Failed: 0` check while
quietly hiding test coverage. This script makes that visible at
release time.

Usage:
    # 1. Generate a SUMMARY.txt (the runner already does this with --summary)
    BD_DISABLE_KEEPALIVE=1 python3 run_tests.py --summary

    # 2. Check the skip count in that summary against the pinned baseline
    python3 tools/check_skip_baseline.py

    # 3. To reset the baseline (after intentionally adding/removing skips):
    python3 tools/check_skip_baseline.py --update

Exit 0 = skip count matches baseline.
Exit 1 = mismatch (current count differs from baseline).
Exit 2 = couldn't read the baseline or the summary.

Pinned to `tests/SKIP_BASELINE.txt`. Format:
    <int>\\n
    # comment lines start with '#'

The baseline is intentionally a single integer, not a list of test
names. Rationale: any list would either need re-sorting whenever the
suite grows (noisy diffs) or would silently lose entries when a test
file is renamed. A count is the actual thing PT9 wants to gate on.

A list-of-names check would also have to know which skips are
intentional. The whole point is that the operator audits any drift —
the file is small enough that re-reading SUMMARY.txt during a release
is sufficient to verify what's been added/removed.

Parser note. The runner's SUMMARY.txt writes a summary line of the
shape:

    result  : 4443 total | 4443 passed | 0 failed | 0 skipped

The runner's stdout one-liner uses the older shape:

    Total: N | Passed: N | Failed: 0 | Skipped: N

Both are supported. v3.63.9 shipped a version of this tool that only
recognised the second; on a fresh stash deploy the first call to
`--update` failed with "could not read Skipped count from SUMMARY.txt"
because SUMMARY.txt only ever contains the first shape. The test
`tests/test_skip_baseline.py::test_parser_reads_real_runner_summary`
round-trips real runner output through this parser to pin both
shapes.
"""

import argparse
import re
import sys
from pathlib import Path

DEFAULT_BASELINE = Path("tests/SKIP_BASELINE.txt")
DEFAULT_SUMMARY = Path("SUMMARY.txt")

# Match the SUMMARY.txt "result :" shape: `N skipped` (lowercase, the
# count comes BEFORE the word). The 1st capture group is the count.
_RE_RESULT_LINE = re.compile(
    r"(\d+)\s+skipped\b",
    re.IGNORECASE,
)

# Match the runner's stdout one-liner shape: `Skipped: N`. Kept as a
# secondary fallback so older summary captures still parse, and so any
# future change that moves to this shape doesn't silently break.
_RE_LABEL_COLON = re.compile(
    r"\bSkipped\s*:\s*(\d+)\b",
)


def _read_baseline(path: Path) -> int | None:
    """Return the pinned count, or None if the file doesn't exist /
    is malformed."""
    if not path.is_file():
        return None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            return int(line)
    except (OSError, ValueError):
        return None
    return None


def _read_summary_skipped(path: Path) -> int | None:
    """Find the skipped count in a runner SUMMARY.txt or in the
    one-line summary the runner prints. Returns None if not found.

    Two formats are recognised:
      * `result  : N total | N passed | N failed | N skipped`
        (the SUMMARY.txt shape — count BEFORE the word, lowercase)
      * `... | Skipped: N`
        (the runner's stdout one-liner — label-colon-count)
    The first is the canonical case; the second is the fallback.
    """
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    # Primary: the "N skipped" shape from SUMMARY.txt's result line.
    m = _RE_RESULT_LINE.search(text)
    if m is not None:
        try:
            return int(m.group(1))
        except (TypeError, ValueError):
            pass

    # Fallback: the "Skipped: N" shape from the runner's stdout
    # one-liner. Kept so an operator can pipe stdout to a file and
    # point --summary at it.
    m = _RE_LABEL_COLON.search(text)
    if m is not None:
        try:
            return int(m.group(1))
        except (TypeError, ValueError):
            pass

    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE,
                    help=f"baseline file (default {DEFAULT_BASELINE})")
    ap.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY,
                    help=f"runner summary to parse (default {DEFAULT_SUMMARY})")
    ap.add_argument("--update", action="store_true",
                    help="rewrite the baseline with the current count")
    args = ap.parse_args()

    current = _read_summary_skipped(args.summary)
    if current is None:
        print(f"FAIL: could not read skip count from {args.summary}\n"
              f"  generate it first: "
              f"BD_DISABLE_KEEPALIVE=1 python3 run_tests.py --summary",
              file=sys.stderr)
        return 2

    if args.update:
        # Preserve any leading comment block in the existing file.
        header_lines = []
        if args.baseline.is_file():
            for line in args.baseline.read_text(
                    encoding="utf-8").splitlines():
                if line.strip().startswith("#") or not line.strip():
                    header_lines.append(line)
                else:
                    break
        if not header_lines:
            header_lines = [
                "# tests/SKIP_BASELINE.txt — PT9 skip-count gate.",
                "# Pinned skip count. Update only when adding or removing a",
                "# legitimate skip; mass-skip regressions show up as a",
                "# mismatch against this number.",
                "# Update with: python3 tools/check_skip_baseline.py --update",
                "",
            ]
        body = "\n".join(header_lines) + f"\n{current}\n"
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(body, encoding="utf-8")
        print(f"OK: baseline updated to {current} in {args.baseline}")
        return 0

    baseline = _read_baseline(args.baseline)
    if baseline is None:
        print(f"WARN: no baseline at {args.baseline}; current count is "
              f"{current}. Initialize with --update.", file=sys.stderr)
        return 2

    if current == baseline:
        print(f"OK: skip count matches baseline ({current})")
        return 0

    direction = "UP" if current > baseline else "DOWN"
    print(f"FAIL: skip count {direction} (baseline {baseline}, "
          f"current {current})", file=sys.stderr)
    print(f"  Investigate the change. If intentional, update the "
          f"baseline:", file=sys.stderr)
    print(f"    python3 tools/check_skip_baseline.py --update",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
