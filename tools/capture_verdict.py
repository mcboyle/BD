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
    r"^\s*(\d+)\s+pass\s*\|\s*(\d+)\s+warn\s*\|\s*"
    r"(\d+)\s+fail\s*\(\s*(\d+)\s+run\s*\)\s*$",
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


def _read_unit(path: Path) -> tuple[int, int, int, int]:
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
    skipped = _count(payload.get("skipped"), "skipped")
    if passed + failed + skipped != total:
        raise ValueError("unit artifact counts are inconsistent")
    if payload.get("ok") is not (failed == 0):
        raise ValueError("unit artifact ok flag is inconsistent")
    tests = payload.get("tests")
    if not isinstance(tests, list):
        raise ValueError("unit artifact tests is not a list")
    statuses = [
        row.get("status") if isinstance(row, dict) else None for row in tests
    ]
    expected = {"pass": passed, "fail": failed, "skip": skipped}
    actual = {status: statuses.count(status) for status in expected}
    if len(tests) != total or actual != expected:
        raise ValueError("unit artifact test records are inconsistent")
    return passed, failed, skipped, total


def _read_live(path: Path) -> tuple[int, int, int, int]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ValueError(f"live artifact unreadable: {exc}") from exc
    matches = _LIVE_SUMMARY.findall(text)
    if not matches:
        raise ValueError("live artifact has no parseable final summary")
    passed, warned, failed, total = (int(value) for value in matches[-1])
    if passed + warned + failed != total:
        raise ValueError("live artifact counts are inconsistent")
    return passed, warned, failed, total


def assess_capture(
    tests_json: str | Path,
    live_log: str | Path,
    *,
    suite_exit: int,
    live_exit: int,
    stage_exits: Iterable[tuple[str, int]] = (),
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
    if suite_exit:
        reasons.append(f"suite process exit={suite_exit}")
    if live_exit:
        reasons.append(f"live process exit={live_exit}")
    for name, exit_code in stage_exits:
        if exit_code:
            reasons.append(f"{name} exit={exit_code}")
    if unit_counts is not None and unit_counts[1]:
        reasons.append(f"unit failures={unit_counts[1]}")
    if live_counts is not None:
        if live_counts[1]:
            reasons.append(f"live warnings={live_counts[1]}")
        if live_counts[2]:
            reasons.append(f"live failures={live_counts[2]}")
    counts = []
    if unit_counts is not None:
        counts.append(
            f"unit {unit_counts[0]} pass/{unit_counts[1]} fail/"
            f"{unit_counts[2]} skip"
        )
    if live_counts is not None:
        counts.append(
            f"live {live_counts[0]} pass/{live_counts[1]} warn/"
            f"{live_counts[2]} fail"
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
    parser.add_argument(
        "--stage-exit", action="append", default=[], type=_stage_exit,
        help="additional required stage in NAME=CODE form (repeatable)",
    )
    args = parser.parse_args(argv)
    result = assess_capture(
        args.tests_json,
        args.live_log,
        suite_exit=args.suite_exit,
        live_exit=args.live_exit,
        stage_exits=args.stage_exit,
    )
    print(result.summary)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
