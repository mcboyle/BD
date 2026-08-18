#!/usr/bin/env python3
"""Convert complete pytest JUnit lanes into trustworthy capture artifacts."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from pathlib import Path


_RESULT_TAGS = {"failure", "error", "skipped"}
_BUDGET_THRESHOLD_S = 30.0
_SLOWEST_LIMIT = 20


def _read_version(repo_root: Path) -> str:
    try:
        source = (repo_root / "bulk_downloader" / "__init__.py").read_text(
            encoding="utf-8"
        )
    except OSError:
        return "unknown"
    match = re.search(
        r"""^__version__\s*=\s*["']([^"']+)["']""", source, re.MULTILINE
    )
    return match.group(1) if match else "unknown"


def _detail(node: ET.Element) -> str:
    text = (node.text or "").strip()
    return text or node.get("message", "").strip()


def _identity(classname: str, test_name: str) -> str:
    return f"{classname}::{test_name}"


def _record(testcase: ET.Element) -> dict:
    """Parse one testcase, refusing ambiguous or untrustworthy state."""
    classname = (testcase.get("classname") or "").strip()
    test_name = (testcase.get("name") or "").strip()
    if not test_name:
        raise ValueError("testcase has no stable identity")
    raw_duration = testcase.get("time", "0") or "0"
    try:
        duration = float(raw_duration)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid testcase duration for {_identity(classname, test_name)}") from exc
    if not math.isfinite(duration) or duration < 0:
        raise ValueError(f"invalid testcase duration for {_identity(classname, test_name)}")

    children = list(testcase)
    unknown = [node.tag for node in children if node.tag not in _RESULT_TAGS]
    if unknown:
        raise ValueError(
            f"unknown testcase result element for {_identity(classname, test_name)}: "
            f"{unknown[0]}"
        )
    if len(children) > 1:
        raise ValueError(
            f"testcase has multiple result states: {_identity(classname, test_name)}"
        )
    if not classname:
        # pytest collection errors have no testcase classname, but their
        # filename-shaped name is still an actionable, collision-safe identity.
        if len(children) != 1 or children[0].tag != "error":
            raise ValueError("testcase has no stable identity")
        classname = "<collection>"
        file_name = testcase.get("file") or test_name
    else:
        file_name = testcase.get("file") or classname.replace(".", "/")

    record = {
        "identity": _identity(classname, test_name),
        "file": file_name,
        "test": test_name,
        "status": "pass",
        "duration_seconds": duration,
    }
    if not children:
        return record
    node = children[0]
    if node.tag == "failure":
        record["status"] = "fail"
        record["error"] = _detail(node)
    elif node.tag == "error":
        record["status"] = "error"
        record["error"] = _detail(node)
    else:
        # pytest's xunit2 body commonly holds a location-prefixed expansion;
        # the canonical skip reason is the stable ``message`` attribute.
        reason = (node.get("message") or "").strip()
        if not reason:
            raise ValueError(f"skip reason missing for {record['identity']}")
        record["status"] = "skip"
        record["reason"] = reason
    return record


def _summary(node: ET.Element, *, label: str) -> dict[str, int] | None:
    names = ("tests", "failures", "errors", "skipped")
    present = [name for name in names if name in node.attrib]
    if not present:
        return None
    if len(present) != len(names):
        raise ValueError(f"incomplete JUnit summary for {label}")
    result = {}
    for name in names:
        try:
            value = int(node.attrib[name])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"malformed JUnit {name} summary for {label}") from exc
        if value < 0:
            raise ValueError(f"negative JUnit {name} summary for {label}")
        result[name] = value
    return result


def _leaf_suites(root: ET.Element) -> list[ET.Element]:
    for suite in root.iter("testsuite"):
        if suite.findall("testsuite") and suite.findall("testcase"):
            raise ValueError("mixed direct and nested JUnit suite testcases")
    suites = [suite for suite in root.iter("testsuite") if not suite.findall("testsuite")]
    if not suites:
        raise ValueError("missing JUnit test summary")
    return suites


def _read_lane(path: Path) -> list[dict]:
    if not path.is_file():
        raise ValueError(f"missing JUnit evidence: {path}")
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ValueError(f"malformed JUnit evidence: {path}: {exc}") from exc

    records: list[dict] = []
    for number, suite in enumerate(_leaf_suites(root), start=1):
        summary = _summary(suite, label=f"{path} suite {number}")
        if summary is None:
            raise ValueError(f"missing JUnit summary for {path} suite {number}")
        suite_records = [_record(case) for case in suite.findall("testcase")]
        observed = {
            "tests": len(suite_records),
            "failures": sum(row["status"] == "fail" for row in suite_records),
            "errors": sum(row["status"] == "error" for row in suite_records),
            "skipped": sum(row["status"] == "skip" for row in suite_records),
        }
        if summary != observed:
            raise ValueError(
                f"JUnit summary disagrees for {path} suite {number}: "
                f"declared {summary}, observed {observed}"
            )
        records.extend(suite_records)

    if not records:
        raise ValueError(f"zero tests executed in JUnit lane: {path}")
    root_summary = _summary(root, label=str(path))
    if root_summary is not None:
        observed_root = {
            "tests": len(records),
            "failures": sum(row["status"] == "fail" for row in records),
            "errors": sum(row["status"] == "error" for row in records),
            "skipped": sum(row["status"] == "skip" for row in records),
        }
        if root_summary != observed_root:
            raise ValueError(
                f"JUnit root summary disagrees for {path}: "
                f"declared {root_summary}, observed {observed_root}"
            )
    return records


def _diagnostic_row(record: dict) -> dict:
    return {
        "identity": record["identity"],
        "file": record["file"],
        "test": record["test"],
        "status": record["status"],
        "duration_seconds": record["duration_seconds"],
    }


def _sorted_by_duration(records: Iterable[dict]) -> list[dict]:
    return sorted(records, key=lambda row: (-row["duration_seconds"], row["identity"]))


def convert_junit(
    junit_paths: str | Path | Iterable[str | Path],
    json_path: str | Path,
    summary_path: str | Path,
    *,
    version: str | None = None,
) -> dict:
    """Write one schema-v2 capture result from complete, disjoint JUnit lanes."""
    if isinstance(junit_paths, (str, Path)):
        paths = [Path(junit_paths)]
    else:
        paths = [Path(path) for path in junit_paths]
    if not paths:
        raise ValueError("at least one JUnit XML path is required")

    records: list[dict] = []
    identities: set[str] = set()
    for path in paths:
        for record in _read_lane(path):
            identity = record["identity"]
            if identity in identities:
                raise ValueError(f"duplicate testcase identity across JUnit lanes: {identity}")
            identities.add(identity)
            records.append(record)
    if not records:
        raise ValueError("zero tests executed")

    passed = sum(row["status"] == "pass" for row in records)
    failed = sum(row["status"] == "fail" for row in records)
    errors = sum(row["status"] == "error" for row in records)
    skipped = sum(row["status"] == "skip" for row in records)

    if version is None:
        version = _read_version(Path(__file__).resolve().parents[1])

    stamp = dt.datetime.now().isoformat(timespec="seconds")
    failures = [
        {"identity": row["identity"], "file": row["file"], "test": row["test"],
         "error": row["error"]}
        for row in records if row["status"] == "fail"
    ]
    error_details = [
        {"identity": row["identity"], "file": row["file"], "test": row["test"],
         "error": row["error"]}
        for row in records if row["status"] == "error"
    ]
    skips = [
        {"identity": row["identity"], "file": row["file"], "test": row["test"],
         "reason": row["reason"]}
        for row in sorted(records, key=lambda row: row["identity"])
        if row["status"] == "skip"
    ]
    slowest = [_diagnostic_row(row) for row in _sorted_by_duration(records)[:_SLOWEST_LIMIT]]
    over = [_diagnostic_row(row) for row in _sorted_by_duration(records)
            if row["duration_seconds"] > _BUDGET_THRESHOLD_S]
    payload = {
        "schema_version": 2,
        "version": version,
        "timestamp": stamp,
        "total": len(records),
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
        "ok": failed == 0 and errors == 0,
        "tests": records,
        "failures": failures,
        "error_details": error_details,
        "skips": skips,
        "budget": {
            "threshold_s": _BUDGET_THRESHOLD_S,
            "over": over,
            "slowest": slowest,
        },
    }
    Path(json_path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    lines = [
        "BulkDownloader test summary",
        f"version : {version}",
        f"run at  : {stamp}",
        f"result  : {len(records)} total | {passed} passed | "
        f"{failed} failed | {errors} errors | {skipped} skipped",
        f"BUDGET: >{_BUDGET_THRESHOLD_S:.1f}s = {len(over)}",
        "",
    ]
    if over:
        lines.append(f"OVER BUDGET ({len(over)}):")
        lines.extend(f"  {row['duration_seconds']:.4f}s {row['identity']}" for row in over)
        lines.append("")
    lines.append(f"SLOWEST (top {len(slowest)}):")
    lines.extend(f"  {row['duration_seconds']:.4f}s {row['identity']}" for row in slowest)
    lines.append("")
    if failures:
        lines.append(f"FAILURES ({len(failures)}):")
        for failure in failures:
            lines.append(f"  FAIL {failure['identity']}")
            for error_line in failure["error"].splitlines()[-3:]:
                lines.append(f"       {error_line}")
        lines.append("")
    if error_details:
        lines.append(f"ERRORS ({len(error_details)}):")
        for error in error_details:
            lines.append(f"  ERROR {error['identity']}")
            for error_line in error["error"].splitlines()[-3:]:
                lines.append(f"        {error_line}")
        lines.append("")
    if skips:
        lines.append(f"SKIPS ({len(skips)}):")
        lines.extend(f"  {row['identity']} :: {row['reason']}" for row in skips)
        lines.append("")
    Path(summary_path).write_text("\n".join(lines), encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--junit", type=Path, required=True, action="append",
        help="JUnit XML input; repeat once per complete capture execution lane",
    )
    parser.add_argument("--json", required=True)
    parser.add_argument("--summary", required=True)
    args = parser.parse_args(argv)
    lane_paths = {path.resolve(strict=False) for path in args.junit}
    if len(args.junit) != 2 or len(lane_paths) != 2:
        parser.exit(2, "pytest result conversion failed: exactly two distinct JUnit lanes are required\n")
    try:
        payload = convert_junit(args.junit, args.json, args.summary)
    except (OSError, ET.ParseError, ValueError) as exc:
        parser.exit(2, f"pytest result conversion failed: {exc}\n")
    print(
        "pytest artifacts: "
        f"{payload['passed']} pass/{payload['failed']} fail/"
        f"{payload['errors']} error/"
        f"{payload['skipped']} skip"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
