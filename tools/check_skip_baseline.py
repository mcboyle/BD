#!/usr/bin/env python3
"""Reconcile permitted skip identities across complete pytest JUnit lanes.

Exit 0 means every baseline identity executed once as PASS or as its exact
permitted SKIP. Exit 1 is skip-policy drift. Exit 2 means the supplied evidence
cannot support a verdict.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:  # Script execution places tools/ first; package-style tests use the root.
    from pytest_capture_results import _read_lane
except ImportError:  # pragma: no cover - selected by the test import path
    from tools.pytest_capture_results import _read_lane


DEFAULT_BASELINE = Path("tests/SKIP_BASELINE.json")


class EvidenceError(ValueError):
    """The supplied test evidence cannot support a skip verdict."""


def _read_junits(paths: list[Path]) -> dict:
    """Read complete, disjoint JUnit lanes and retain every terminal state."""
    if not paths:
        raise EvidenceError("missing JUnit evidence")
    states: dict[str, dict] = {}
    try:
        for path in paths:
            for record in _read_lane(path):
                identity = record["identity"]
                if identity in states:
                    raise EvidenceError(f"duplicate testcase identity: {identity}")
                states[identity] = record
    except EvidenceError:
        raise
    except ValueError as exc:
        raise EvidenceError(str(exc)) from exc
    if not states:
        raise EvidenceError("zero tests executed")
    failures = sum(row["status"] == "fail" for row in states.values())
    errors = sum(row["status"] == "error" for row in states.values())
    if failures:
        raise EvidenceError(f"JUnit reports {failures} failure(s)")
    if errors:
        raise EvidenceError(f"JUnit reports {errors} error(s)")
    skipped = {
        identity: row["reason"]
        for identity, row in states.items()
        if row["status"] == "skip"
    }
    return {"executed": len(states), "skipped": skipped, "states": states}


def _read_junit(path: Path) -> dict:
    """Compatibility wrapper for callers that validate one complete lane."""
    return _read_junits([path])


def _compare_skips(expected: dict[str, str], observed: dict[str, str]) -> list[str]:
    """Return exact identity/reason differences in diagnostic order."""
    differences = [f"missing: {key}" for key in sorted(expected.keys() - observed)]
    differences.extend(
        f"unexpected: {key}" for key in sorted(observed.keys() - expected))
    differences.extend(
        f"reason changed: {key}"
        for key in sorted(expected.keys() & observed)
        if expected[key] != observed[key])
    return differences


def _compare_baseline(expected: dict[str, str], result: dict) -> list[str]:
    """Reconcile every permitted identity as a PASS or its exact SKIP."""
    states = result["states"]
    skipped = result["skipped"]
    differences = [
        f"missing: {identity}"
        for identity in sorted(expected)
        if identity not in states
    ]
    differences.extend(
        f"unexpected: {identity}"
        for identity in sorted(set(skipped) - set(expected))
    )
    differences.extend(
        f"reason changed: {identity}"
        for identity in sorted(set(expected) & set(skipped))
        if expected[identity] != skipped[identity]
    )
    return differences


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_identity_baseline(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"),
                             object_pairs_hook=_reject_duplicate_keys)
    except EvidenceError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"missing or malformed skip baseline: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != "bd-skip-baseline/1":
        raise EvidenceError("skip baseline has wrong schema")
    rows = payload.get("skips")
    if not isinstance(rows, list):
        raise EvidenceError("skip baseline has no skips list")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise EvidenceError("skip baseline row is not an object")
        if set(row) != {"identity", "reason"}:
            raise EvidenceError("skip baseline row has unknown or missing fields")
        identity = row.get("identity")
        reason = row.get("reason")
        if not isinstance(identity, str) or not identity.strip():
            raise EvidenceError("skip baseline row has no identity")
        if not isinstance(reason, str) or not reason.strip():
            raise EvidenceError(f"skip baseline reason missing for {identity}")
        if identity in result:
            raise EvidenceError(f"duplicate skip baseline identity: {identity}")
        result[identity] = reason
    return result


def _write_identity_baseline(path: Path, skips: dict[str, str]) -> None:
    payload = {
        "schema": "bd-skip-baseline/1",
        "skips": [
            {"identity": identity, "reason": skips[identity]}
            for identity in sorted(skips)
        ],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        if _read_identity_baseline(path) != skips:
            raise EvidenceError(f"written skip baseline did not verify: {path}")
    except OSError as exc:
        raise EvidenceError(f"cannot write skip baseline: {path}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE,
                        help=f"baseline file (default {DEFAULT_BASELINE})")
    parser.add_argument("--junit", type=Path, required=True, action="append",
                        help="complete real-pytest JUnit XML; repeat for each lane")
    parser.add_argument("--update", action="store_true",
                        help="retired: baseline updates require hand adjudication")
    args = parser.parse_args(argv)

    lane_paths = {path.resolve(strict=False) for path in args.junit}
    if len(args.junit) != 2 or len(lane_paths) != 2:
        print("REFUSED: exactly two distinct JUnit lanes are required", file=sys.stderr)
        return 2
    if args.update:
        print("REFUSED: --update is disabled; baseline changes require hand adjudication",
              file=sys.stderr)
        return 2

    try:
        result = _read_junits(args.junit)
        expected = _read_identity_baseline(args.baseline)
    except EvidenceError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    differences = _compare_baseline(expected, result)
    if differences:
        print("FAIL: skip identities or reasons changed:\n  "
              + "\n  ".join(differences), file=sys.stderr)
        return 1
    noun = "identity" if len(expected) == 1 else "identities"
    print(f"OK: {len(expected)} baseline {noun} executed as PASS or exact "
          f"permitted SKIP across {result['executed']} executed tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
