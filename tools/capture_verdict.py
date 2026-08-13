#!/usr/bin/env python3
"""Produce a fail-closed final verdict for ``capture.sh`` artifacts."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_LIVE_SUMMARY = re.compile(
    # The n/a bucket is OPTIONAL. live_tests prints it only when non-zero, and
    # every capture bundle written before the N/A verdict existed has no such
    # column -- an unconditional group would make each of those archives
    # unparseable, which reads as "live artifact has no parseable final summary"
    # and fails a verdict that was fine when it was recorded.
    r"^\s*(\d+)\s+pass\s*\|\s*(\d+)\s+warn\s*\|\s*"
    r"(\d+)\s+fail\s*(?:\|\s*(\d+)\s+n/a\s*)?\s*\(\s*(\d+)\s+run\s*\)\s*$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class CaptureVerdict:
    ok: bool
    exit_code: int
    summary: str


def _count(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"unit artifact {name} must be a non-negative integer")
    return value


def _read_unit(path: Path) -> tuple[int, int, int, int, int]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unit artifact unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("unit artifact root is not an object")
    if payload.get("schema_version") != 2:
        raise ValueError("unit artifact schema_version is not 2")
    total = _count(payload.get("total"), "total")
    passed = _count(payload.get("passed"), "passed")
    failed = _count(payload.get("failed"), "failed")
    errors = _count(payload.get("errors"), "errors")
    skipped = _count(payload.get("skipped"), "skipped")
    if passed + failed + errors + skipped != total:
        raise ValueError("unit artifact counts are inconsistent")
    if payload.get("ok") is not (failed == 0 and errors == 0):
        raise ValueError("unit artifact ok flag is inconsistent")
    tests = payload.get("tests")
    if not isinstance(tests, list):
        raise ValueError("unit artifact tests is not a list")
    statuses = [
        row.get("status") if isinstance(row, dict) else None for row in tests
    ]
    expected = {
        "pass": passed,
        "fail": failed,
        "error": errors,
        "skip": skipped,
    }
    actual = {status: statuses.count(status) for status in expected}
    if len(tests) != total or actual != expected:
        raise ValueError("unit artifact test records are inconsistent")
    return passed, failed, errors, skipped, total


def _read_live(path: Path) -> tuple[int, int, int, int]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ValueError(f"live artifact unreadable: {exc}") from exc
    matches = _LIVE_SUMMARY.findall(text)
    if not matches:
        raise ValueError("live artifact has no parseable final summary")
    raw_pass, raw_warn, raw_fail, raw_na, raw_total = matches[-1]
    passed, warned, failed, total = (
        int(raw_pass), int(raw_warn), int(raw_fail), int(raw_total))
    # Absent on a pre-N/A bundle, and on any run where nothing was unobservable.
    not_applicable = int(raw_na) if raw_na else 0
    if passed + warned + failed + not_applicable != total:
        raise ValueError("live artifact counts are inconsistent")
    # The returned shape stays a 4-tuple with the TOTAL at index 3: callers
    # index it positionally (live_counts[2] is failures, live_counts[3] is the
    # count compared against EXPECTED_LIVE_TESTS), and shifting those silently
    # would compare the wrong number against the registry.
    return passed, warned, failed, total


def _cap_note(exit_code: int) -> str:
    """Name exit 124 rather than reporting a bare number.

    scripts/lib/heartbeat.sh stops a stage that outruns CAPTURE_STAGE_CAP and
    returns 124, coreutils `timeout`'s convention. The verdict already FAILED on
    any non-zero exit, so this changes no grade -- it changes what the operator
    reads. `exit=124` alongside a dozen other numbers looks like a test failure;
    it is not one. An unfinished run is not a pass and not a failure of the
    code, and backlog 102 exists because the two were confusable.
    """
    if exit_code == 124:
        return (" -- STAGE CAP: the stage was stopped before it finished "
                "(CAPTURE_STAGE_CAP). This is an UNFINISHED run, not a test "
                "failure; the suite never reported a verdict")
    return ""


def assess_capture(
    tests_json: str | Path,
    live_log: str | Path,
    *,
    suite_exit: int,
    live_exit: int,
    stage_exits: Iterable[tuple[str, int]] = (),
    expected_live_tests: int | None = None,
    tree_drift_file: str | Path | None = None,
) -> CaptureVerdict:
    """Assess process statuses and artifacts; ambiguity is always a failure."""
    reasons: list[str] = []
    unit_counts = None
    live_counts = None
    try:
        unit_counts = _read_unit(Path(tests_json))
    except ValueError as exc:
        reasons.append(str(exc))
    try:
        live_counts = _read_live(Path(live_log))
    except ValueError as exc:
        reasons.append(str(exc))
    if unit_counts is not None and unit_counts[4] == 0:
        reasons.append("unit artifact contains zero tests")
    if (live_counts is not None and expected_live_tests is not None
            and live_counts[3] != expected_live_tests):
        reasons.append(
            f"live artifact ran {live_counts[3]} tests; "
            f"expected {expected_live_tests}"
        )
    if suite_exit:
        reasons.append(f"suite process exit={suite_exit}{_cap_note(suite_exit)}")
    if live_exit:
        reasons.append(f"live process exit={live_exit}{_cap_note(live_exit)}")
    for name, exit_code in stage_exits:
        if exit_code:
            reasons.append(f"{name} exit={exit_code}{_cap_note(exit_code)}")
    if unit_counts is not None and unit_counts[1]:
        reasons.append(f"unit failures={unit_counts[1]}")
    if unit_counts is not None and unit_counts[2]:
        reasons.append(f"unit errors={unit_counts[2]}")
    if live_counts is not None:
        # Live WARNs are INFORMATIONAL and deliberately do NOT fail the verdict.
        # A warn means "capability not exercisable", not "capability broken":
        # no completed downloads yet, no VPN tunnels configured, AI assist off.
        # live_tests itself exits 0 ("all clear") on a warn-only run, so gating
        # here made this tool stricter than the suite it reads, and reported
        # FAIL on a healthy box that no code change could ever turn green --
        # only real usage can. CLAUDE.md 0 counts that over-sensitivity as a
        # soundness bug: a gate that cries wolf gets switched off, and a
        # verdict the operator has learned to ignore protects nothing.
        # The count is still carried in `counts` below, on PASS and FAIL alike,
        # so an absent capability stays visible without blocking the run.
        if live_counts[2]:
            reasons.append(f"live failures={live_counts[2]}")
    counts = []
    if unit_counts is not None:
        counts.append(
            f"unit {unit_counts[0]} pass/{unit_counts[1]} fail/"
            f"{unit_counts[2]} error/{unit_counts[3]} skip"
        )
    if live_counts is not None:
        counts.append(
            f"live {live_counts[0]} pass/{live_counts[1]} warn/"
            f"{live_counts[2]} fail"
        )
    # INVALID OUTRANKS FAIL, AND THE ORDER IS LOAD-BEARING (backlog 100).
    #
    # A run whose tree moved cannot ATTRIBUTE its own results: every count below
    # describes a tree that no longer exists, so "these tests failed" is a claim
    # about something other than the subject. Grading it FAIL spends the word
    # this tool uses for "the software is broken" on "the measurement is void" --
    # which is precisely how the 1082 round was read, a green suite reported as
    # a code defect because the graph pin had drifted against nine files edited
    # mid-run.
    #
    # The counts stay in the line. Nothing is hidden; the reader is told the
    # numbers cannot be trusted, rather than being shown nothing.
    #
    # ABSENT IS NOT CLEAN. A missing file means "not recorded" -- the state every
    # bundle archived before this cut is in, and replaying one must not turn it
    # INVALID. Only a file that EXISTS and is NON-EMPTY invalidates.
    if tree_drift_file is not None:
        drift_path = Path(tree_drift_file)
        if drift_path.exists():
            drift = drift_path.read_text(encoding="utf-8", errors="replace").strip()
            if drift:
                changed = "; ".join(drift.split("\n"))
                suffix = f" ({'; '.join(counts)})" if counts else ""
                extra = f"; also: {'; '.join(reasons)}" if reasons else ""
                return CaptureVerdict(
                    False, 3,
                    "CAPTURE VERDICT: INVALID - the working tree changed "
                    f"during the run: {changed}. This run measured a tree that "
                    "no longer exists; it is NOT a pass and NOT a defect "
                    f"report{extra}{suffix}"
                )

    if reasons:
        suffix = f" ({'; '.join(counts)})" if counts else ""
        return CaptureVerdict(
            False, 1, f"CAPTURE VERDICT: FAIL - {'; '.join(reasons)}{suffix}"
        )
    return CaptureVerdict(True, 0, f"CAPTURE VERDICT: PASS - {'; '.join(counts)}")


def _stage_exit(value: str) -> tuple[str, int]:
    name, separator, code = value.partition("=")
    if not separator or not name:
        raise argparse.ArgumentTypeError("stage exit must be NAME=CODE")
    try:
        return name, int(code)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("stage exit code must be an integer") from exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tests-json", required=True)
    parser.add_argument("--live-log", required=True)
    parser.add_argument("--suite-exit", required=True, type=int)
    parser.add_argument("--live-exit", required=True, type=int)
    parser.add_argument("--expected-live-tests", required=True, type=int)
    parser.add_argument(
        "--stage-exit", action="append", default=[], type=_stage_exit,
        help="additional required stage in NAME=CODE form (repeatable)",
    )
    parser.add_argument(
        "--tree-drift-file", default=None,
        help="file listing paths that changed DURING the run; a non-empty one "
             "grades the capture INVALID (exit 3). Absent means not recorded, "
             "which is not the same as clean.",
    )
    args = parser.parse_args(argv)
    result = assess_capture(
        args.tests_json,
        args.live_log,
        suite_exit=args.suite_exit,
        live_exit=args.live_exit,
        stage_exits=args.stage_exit,
        expected_live_tests=args.expected_live_tests,
        tree_drift_file=args.tree_drift_file,
    )
    print(result.summary)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
