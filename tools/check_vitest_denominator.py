#!/usr/bin/env python3
"""Refuse a vitest run that judged fewer files than the repository tracks.

WHY THIS EXISTS AT ALL. `vitest run` EXITS 0 WHEN IT COLLECTS NOTHING. A config
typo, a moved directory, or an `include` pattern that stops matching produces a
green CI job over an empty denominator -- the "a gate CI does not run does not
exist" failure, arriving as a pass rather than as an error. Wiring vitest into
CI without this check would buy the appearance of 499 tests and the reality of
whatever survived.

THE FILE COUNT IS RECONCILED, NOT PINNED. The expected number is derived from
`git ls-files` at run time, so it cannot go stale the way a hard-coded 122
would, and a spec file that fails to LOAD (rather than fails to pass) is caught
too: vitest reports a file it could not import as absent from testResults, and
absent is exactly what this compares against.

THE GLOB IS THE DENOMINATOR, AND THE FIRST DRAFT OF IT WAS WRONG. Git's
`src/**/*.test.ts` requires at least one intermediate directory, so specs
sitting directly in `src/` were silently excluded and the count read 120 against
122 real files. Both shapes are listed below deliberately. CLAUDE.md A1: a glob
is a denominator choice.

WHICH NUMBER MEANS WHAT, because vitest offers three and two of them are traps:
  len(testResults)     FILES vitest actually ran        <- the reconcilable one
  numTotalTestSuites   describe() BLOCKS (274 today)    <- not files
  numTotalTests        individual test cases (499)      <- floors, not exact
Files are asserted EXACTLY. Tests are asserted against a floor, because tests
are added constantly and an exact pin would fail every cut that writes one.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

# Every shape a tracked frontend spec can take. BOTH the bare and the nested
# form are required -- see the glob note in the module docstring.
SPEC_GLOBS = (
    "frontend/src/*.test.ts", "frontend/src/*.test.tsx",
    "frontend/src/*.spec.ts", "frontend/src/*.spec.tsx",
    "frontend/src/**/*.test.ts", "frontend/src/**/*.test.tsx",
    "frontend/src/**/*.spec.ts", "frontend/src/**/*.spec.tsx",
)

# MEASURED at v3.66.1216: 122 files / 499 tests, all passing, in 14s. The floor
# is deliberately below the measurement so ordinary churn does not trip it,
# while a collapse to a fraction of the suite still does.
TEST_FLOOR = 450


def tracked_specs(root: pathlib.Path) -> set[str]:
    out = subprocess.run(["git", "ls-files", "--", *SPEC_GLOBS],
                         cwd=root, capture_output=True, text=True, check=True)
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def ran_specs(report: dict, root: pathlib.Path) -> set[str]:
    """The files vitest reported on, normalised to repo-relative paths."""
    seen = set()
    for entry in report.get("testResults", []):
        name = entry.get("name") or ""
        if not name:
            continue
        path = pathlib.Path(name)
        try:
            seen.add(str(path.resolve().relative_to(root.resolve())))
        except ValueError:
            seen.add(name)
    return seen


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("report", help="vitest --reporter=json output file")
    ap.add_argument("--root", default=".", help="repository root")
    args = ap.parse_args()

    root = pathlib.Path(args.root).resolve()
    path = pathlib.Path(args.report)
    if not path.is_file() or path.stat().st_size == 0:
        print("VITEST-DENOMINATOR-UNKNOWN: no report at %s. A missing report is "
              "not an empty one -- it is an unmeasured run." % path)
        return 2
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        print("VITEST-DENOMINATOR-UNKNOWN: unreadable report %s: %s" % (path, exc))
        return 2

    expected = tracked_specs(root)
    actual = ran_specs(report, root)
    total = report.get("numTotalTests")
    failed = report.get("numFailedTests")

    if not expected:
        print("VITEST-DENOMINATOR-UNKNOWN: the repository tracks NO frontend spec "
              "files. Either the globs stopped matching or the suite was deleted; "
              "both are findings, neither is a pass.")
        return 2

    problems = []
    missing = sorted(expected - actual)
    if missing:
        problems.append("%d tracked spec file(s) were NOT run: %s"
                        % (len(missing), ", ".join(missing[:8])))
    extra = sorted(actual - expected)
    if extra:
        problems.append("%d file(s) ran that git does not track: %s"
                        % (len(extra), ", ".join(extra[:8])))
    if not isinstance(total, int) or total < TEST_FLOOR:
        problems.append("numTotalTests=%r is below the floor %d"
                        % (total, TEST_FLOOR))
    if failed:
        problems.append("numFailedTests=%s" % failed)

    if problems:
        print("VITEST-DENOMINATOR-FAIL: " + "; ".join(problems))
        return 1

    print("VITEST-DENOMINATOR-OK: %d/%d tracked spec files ran, %d tests, 0 failed"
          % (len(actual), len(expected), total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
