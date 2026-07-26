#!/usr/bin/env python3
"""Convert pytest's JUnit XML into BulkDownloader capture artifacts."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from pathlib import Path


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


def _record(testcase: ET.Element) -> dict:
    classname = testcase.get("classname", "")
    file_name = testcase.get("file") or classname.replace(".", "/")
    test_name = testcase.get("name", "<unknown>")
    try:
        duration = round(float(testcase.get("time", "0") or 0), 4)
    except ValueError:
        duration = 0.0

    failure = testcase.find("failure")
    error = testcase.find("error")
    skipped = testcase.find("skipped")

    record = {
        "file": file_name,
        "test": test_name,
        "status": "pass",
        "duration_seconds": duration,
    }
    if failure is not None:
        record["status"] = "fail"
        record["error"] = _detail(failure)
    elif error is not None:
        record["status"] = "error"
        record["error"] = _detail(error)
    elif skipped is not None:
        record["status"] = "skip"
        record["reason"] = _detail(skipped)
    return record


def convert_junit(
    junit_paths: str | Path | Iterable[str | Path],
    json_path: str | Path,
    summary_path: str | Path,
    *,
    version: str | None = None,
) -> dict:
    """Write one schema-v2 capture result from one or more pytest XML files."""
    if isinstance(junit_paths, (str, Path)):
        paths = [Path(junit_paths)]
    else:
        paths = [Path(path) for path in junit_paths]
    if not paths:
        raise ValueError("at least one JUnit XML path is required")

    records = []
    for path in paths:
        root = ET.parse(path).getroot()
        records.extend(_record(node) for node in root.iter("testcase"))
    passed = sum(row["status"] == "pass" for row in records)
    failed = sum(row["status"] == "fail" for row in records)
    errors = sum(row["status"] == "error" for row in records)
    skipped = sum(row["status"] == "skip" for row in records)

    if version is None:
        version = _read_version(Path(__file__).resolve().parents[1])

    stamp = dt.datetime.now().isoformat(timespec="seconds")
    failures = [
        {"file": row["file"], "test": row["test"], "error": row["error"]}
        for row in records
        if row["status"] == "fail"
    ]
    error_details = [
        {"file": row["file"], "test": row["test"], "error": row["error"]}
        for row in records
        if row["status"] == "error"
    ]
    skips = [
        {"file": row["file"], "test": row["test"], "reason": row["reason"]}
        for row in records
        if row["status"] == "skip"
    ]
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
        "budget": {"threshold_s": 0, "over": []},
    }
    Path(json_path).write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )

    lines = [
        "BulkDownloader test summary",
        f"version : {version}",
        f"run at  : {stamp}",
        f"result  : {len(records)} total | {passed} passed | "
        f"{failed} failed | {errors} errors | {skipped} skipped",
        "",
    ]
    if failures:
        lines.append(f"FAILURES ({len(failures)}):")
        for failure in failures:
            lines.append(
                f"  FAIL {failure['file']} :: {failure['test']}"
            )
            for error_line in failure["error"].splitlines()[-3:]:
                lines.append(f"       {error_line}")
        lines.append("")
    if error_details:
        lines.append(f"ERRORS ({len(error_details)}):")
        for error in error_details:
            lines.append(f"  ERROR {error['file']} :: {error['test']}")
            for error_line in error["error"].splitlines()[-3:]:
                lines.append(f"        {error_line}")
        lines.append("")
    Path(summary_path).write_text("\n".join(lines), encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--junit",
        required=True,
        action="append",
        help="JUnit XML input; repeat once per capture execution lane",
    )
    parser.add_argument("--json", required=True)
    parser.add_argument("--summary", required=True)
    args = parser.parse_args(argv)
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
