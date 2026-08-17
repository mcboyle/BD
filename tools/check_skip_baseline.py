#!/usr/bin/env python3
"""Check exact skip identities and reasons in complete real-pytest JUnit.

Usage: ``python tools/check_skip_baseline.py --junit result.xml``.
Use ``--update`` only after every changed skip has been adjudicated.

Exit 0 means exact match, 1 means identity/reason drift, and 2 means the
evidence or baseline cannot support a verdict. Incomplete, zero-test, failed,
errored, malformed, or internally inconsistent JUnit always returns 2.
"""

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

DEFAULT_BASELINE = Path("tests/SKIP_BASELINE.json")


class EvidenceError(ValueError):
    """The supplied test evidence cannot support a skip verdict."""


def _read_junit(path: Path) -> dict:
    """Read complete passing JUnit and retain exact skip identities/reasons."""
    if not path.is_file():
        raise EvidenceError(f"missing JUnit evidence: {path}")
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise EvidenceError(f"malformed JUnit evidence: {exc}") from exc

    suites = [suite for suite in root.iter("testsuite")
              if not suite.findall("testsuite")]
    if not suites:
        raise EvidenceError("missing test summary")
    try:
        declared = sum(int(suite.attrib["tests"]) for suite in suites)
        failures = sum(int(suite.attrib["failures"]) for suite in suites)
        errors = sum(int(suite.attrib["errors"]) for suite in suites)
        declared_skips = sum(int(suite.attrib["skipped"]) for suite in suites)
    except (KeyError, TypeError, ValueError) as exc:
        raise EvidenceError("missing or malformed test summary") from exc
    if declared <= 0:
        raise EvidenceError("zero tests executed")
    if failures:
        raise EvidenceError(f"JUnit reports {failures} failure(s)")
    if errors:
        raise EvidenceError(f"JUnit reports {errors} error(s)")

    cases = [case for suite in suites for case in suite.findall("testcase")]
    if len(cases) != declared:
        raise EvidenceError(
            f"testcase population is incomplete: declared {declared}, "
            f"observed {len(cases)}")

    skipped = {}
    identities = set()
    for case in cases:
        classname = (case.get("classname") or "").strip()
        name = (case.get("name") or "").strip()
        if not classname or not name:
            raise EvidenceError("testcase has no stable identity")
        identity = f"{classname}::{name}"
        if identity in identities:
            raise EvidenceError(f"duplicate testcase identity: {identity}")
        identities.add(identity)
        skip = case.find("skipped")
        if skip is None:
            continue
        reason = (skip.get("message") or "").strip()
        if not reason:
            raise EvidenceError(f"skip reason missing for {identity}")
        skipped[identity] = reason
    if len(skipped) != declared_skips:
        raise EvidenceError(
            f"skip summary disagrees: declared {declared_skips}, "
            f"observed {len(skipped)}")
    return {"executed": declared, "skipped": skipped}


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
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE,
                        help=f"baseline file (default {DEFAULT_BASELINE})")
    parser.add_argument("--junit", type=Path, required=True,
                        help="complete passing real-pytest JUnit XML")
    parser.add_argument("--update", action="store_true",
                        help="write observed identities/reasons after adjudication")
    args = parser.parse_args()

    try:
        result = _read_junit(args.junit)
        if args.update:
            _write_identity_baseline(args.baseline, result["skipped"])
            print(f"OK: baseline updated with {len(result['skipped'])} "
                  f"exact skip identities in {args.baseline}")
            return 0
        expected = _read_identity_baseline(args.baseline)
    except EvidenceError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    differences = _compare_skips(expected, result["skipped"])
    if differences:
        print("FAIL: skip identities or reasons changed:\n  "
              + "\n  ".join(differences), file=sys.stderr)
        return 1
    noun = "identity" if len(expected) == 1 else "identities"
    print(f"OK: {len(expected)} exact skip {noun} and reasons match "
          f"across {result['executed']} executed tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
