import json
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from tools.pytest_capture_results import _read_lane, _read_version, convert_junit


BD_GATE_SCOPE = "repo-wide"


def test_read_version_uses_package_initializer_without_importing_app(tmp_path) -> None:
    package = tmp_path / "bulk_downloader"
    package.mkdir()
    (package / "__init__.py").write_text(
        '__version__ = "3.66.fixture"\n', encoding="utf-8"
    )

    assert _read_version(tmp_path) == "3.66.fixture"


def test_convert_junit_writes_capture_schema_and_summary(tmp_path) -> None:
    junit = tmp_path / "pytest.xml"
    json_path = tmp_path / "results.json"
    summary_path = tmp_path / "SUMMARY.txt"
    junit.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites tests="3" failures="1" errors="0" skipped="1" time="0.6">
  <testsuite name="pytest" tests="3" failures="1" errors="0" skipped="1">
    <testcase classname="tests.test_example" name="test_pass" time="0.1" />
    <testcase classname="tests.test_example" name="test_fail" time="0.2">
      <failure message="assert 1 == 2">AssertionError: assert 1 == 2</failure>
    </testcase>
    <testcase classname="tests.test_example" name="test_skip" time="0.3">
      <skipped message="fixture unavailable" />
    </testcase>
  </testsuite>
</testsuites>
""",
        encoding="utf-8",
    )

    payload = convert_junit(junit, json_path, summary_path, version="3.66.test")

    assert payload["schema_version"] == 2
    assert payload["version"] == "3.66.test"
    assert payload["total"] == 3
    assert payload["passed"] == 1
    assert payload["failed"] == 1
    assert payload["errors"] == 0
    assert payload["skipped"] == 1
    assert payload["ok"] is False
    assert [row["status"] for row in payload["tests"]] == [
        "pass",
        "fail",
        "skip",
    ]
    assert payload["failures"][0]["test"] == "test_fail"
    assert payload["skips"][0]["reason"] == "fixture unavailable"
    assert json.loads(json_path.read_text(encoding="utf-8")) == payload
    assert (
        "3 total | 1 passed | 1 failed | 0 errors | 1 skipped"
        in summary_path.read_text(encoding="utf-8")
    )


def test_convert_junit_preserves_collection_errors_with_a_stable_fallback_identity(tmp_path) -> None:
    junit = tmp_path / "pytest.xml"
    json_path = tmp_path / "results.json"
    summary_path = tmp_path / "SUMMARY.txt"
    junit.write_text(
        """<testsuite name="pytest" tests="1" failures="0" errors="1" skipped="0">
  <testcase classname="" name="tests/test_broken.py" file="reported/tests/test_broken.py">
    <error message="collection failure">ImportError: broken module</error>
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )

    payload = convert_junit(junit, json_path, summary_path, version="unknown")

    assert (payload["total"], payload["passed"], payload["failed"],
            payload["errors"], payload["skipped"]) == (1, 0, 0, 1, 0)
    assert payload["ok"] is False
    expected = {
        "identity": "<collection>::tests/test_broken.py",
        "file": "reported/tests/test_broken.py",
        "test": "tests/test_broken.py",
        "status": "error",
        "duration_seconds": 0.0,
        "error": "ImportError: broken module",
    }
    assert payload["tests"] == [expected]
    assert payload["error_details"] == [{
        key: expected[key] for key in ("identity", "file", "test", "error")
    }]
    assert json.loads(json_path.read_text(encoding="utf-8")) == payload
    summary = summary_path.read_text(encoding="utf-8")
    assert "1 total | 0 passed | 0 failed | 1 errors | 0 skipped" in summary
    assert "ERROR <collection>::tests/test_broken.py" in summary
    assert "ImportError: broken module" in summary


def test_convert_junit_reconciles_repeated_module_collection_skip(tmp_path) -> None:
    parallel = tmp_path / "02_pytest_parallel.xml"
    serial = tmp_path / "02_pytest_serial.xml"
    json_path = tmp_path / "results.json"
    summary_path = tmp_path / "SUMMARY.txt"
    collection_skip = """<testcase classname="" name="tests.test_optional_module" time="0.000">
    <skipped message="collection skipped">('/tmp/run/tests/test_optional_module.py', 7, "Skipped: optional dependency absent")</skipped>
  </testcase>"""
    parallel.write_text(
        """<testsuite name="parallel" tests="2" failures="0" errors="0" skipped="1">
  <testcase classname="tests.test_parallel" name="test_runs" time="0.1" />
  %s
</testsuite>
""" % collection_skip,
        encoding="utf-8",
    )
    serial.write_text(
        """<testsuite name="serial" tests="2" failures="0" errors="0" skipped="1">
  <testcase classname="tests.test_serial" name="test_runs" time="0.1" />
  %s
</testsuite>
""" % collection_skip,
        encoding="utf-8",
    )

    payload = convert_junit(
        [parallel, serial], json_path, summary_path, version="3.66.fixture"
    )

    assert payload["total"] == 3
    assert payload["total"] == (
        payload["passed"] + payload["failed"] + payload["errors"]
        + payload["skipped"]
    )
    assert payload["passed"] == 2
    assert payload["failed"] == 0
    assert payload["errors"] == 0
    assert payload["skipped"] == 1
    collection = [
        row for row in payload["tests"]
        if row["identity"] == "<collection>::tests.test_optional_module"
    ]
    assert collection == [{
        "identity": "<collection>::tests.test_optional_module",
        "file": "tests.test_optional_module",
        "test": "tests.test_optional_module",
        "status": "skip",
        "duration_seconds": 0.0,
        "reason": "optional dependency absent",
    }]
    assert payload["skips"] == [{
        "identity": "<collection>::tests.test_optional_module",
        "file": "tests.test_optional_module",
        "test": "tests.test_optional_module",
        "reason": "optional dependency absent",
    }]
    assert json.loads(json_path.read_text(encoding="utf-8")) == payload
    summary = summary_path.read_text(encoding="utf-8")
    assert "3 total | 2 passed | 0 failed | 0 errors | 1 skipped" in summary
    assert summary.count("SKIPS (1):") == 1
    assert summary.count(
        "  <collection>::tests.test_optional_module :: optional dependency absent"
    ) == 1


def _ordinary_duplicate_lanes(
    tmp_path, state, failures, errors, skipped,
) -> list:
    case = (
        '<testcase classname="tests.test_same" name="test_case" time="0.1">'
        f"{state}</testcase>"
    )
    lanes = []
    for name in ("parallel.xml", "serial.xml"):
        lane = tmp_path / name
        lane.write_text(
            f'<testsuite tests="1" failures="{failures}" errors="{errors}" '
            f'skipped="{skipped}">'
            f"{case}</testsuite>",
            encoding="utf-8",
        )
        lanes.append(lane)
    return lanes


@pytest.mark.parametrize("state, failures, errors, skipped", [
    pytest.param("", 0, 0, 0, id="pass"),
    pytest.param('<failure message="ordinary failure"/>', 1, 0, 0, id="failure"),
    pytest.param('<error message="ordinary error"/>', 0, 1, 0, id="error"),
])
def test_convert_junit_refuses_exact_duplicate_ordinary_identity_across_lanes(
    tmp_path, state, failures, errors, skipped,
) -> None:
    lanes = _ordinary_duplicate_lanes(
        tmp_path, state, failures, errors, skipped
    )

    with pytest.raises(ValueError, match="duplicate testcase identity across"):
        convert_junit(
            lanes, tmp_path / "results.json", tmp_path / "SUMMARY.txt",
            version="3.66.fixture",
        )


def test_convert_junit_refuses_exact_duplicate_ordinary_skip_across_lanes(
    tmp_path,
) -> None:
    lanes = _ordinary_duplicate_lanes(
        tmp_path, '<skipped message="ordinary skip"/>', 0, 0, 1
    )

    with pytest.raises(ValueError, match="duplicate testcase identity across"):
        convert_junit(
            lanes, tmp_path / "results.json", tmp_path / "SUMMARY.txt",
            version="3.66.fixture",
        )


def test_convert_junit_refuses_conflicting_repeated_collection_skip(tmp_path) -> None:
    lanes = []
    for name, reason in (
        ("parallel.xml", "collection skipped"),
        ("serial.xml", "different collection reason"),
    ):
        lane = tmp_path / name
        lane.write_text(
            '<testsuite tests="1" failures="0" errors="0" skipped="1">'
            '<testcase classname="" name="tests.test_optional_module" time="0.000">'
            '<skipped message="collection skipped">'
            f"('/tmp/run/tests/test_optional_module.py', 7, \"Skipped: {reason}\")"
            "</skipped>"
            "</testcase></testsuite>",
            encoding="utf-8",
        )
        lanes.append(lane)

    with pytest.raises(ValueError, match="duplicate testcase identity across"):
        convert_junit(
            lanes, tmp_path / "results.json", tmp_path / "SUMMARY.txt",
            version="3.66.fixture",
        )


def test_convert_junit_refuses_malformed_collection_skip_body(tmp_path) -> None:
    lane = tmp_path / "parallel.xml"
    lane.write_text(
        '<testsuite tests="1" failures="0" errors="0" skipped="1">'
        '<testcase classname="" name="tests.test_optional_module" time="0">'
        '<skipped message="collection skipped">not a pytest location tuple</skipped>'
        "</testcase></testsuite>",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="malformed pytest collection skip"):
        convert_junit(
            lane, tmp_path / "results.json", tmp_path / "SUMMARY.txt",
            version="3.66.fixture",
        )


def test_convert_junit_refuses_collection_file_or_name_drift(tmp_path) -> None:
    body = (
        "('/tmp/run/tests/test_optional_module.py', 7, "
        '\"Skipped: optional dependency absent\")'
    )
    file_lanes = []
    for lane_name, reported_file in (
        ("parallel", "tests/a/test_optional_module.py"),
        ("serial", "tests/b/test_optional_module.py"),
    ):
        lane = tmp_path / f"{lane_name}.xml"
        lane.write_text(
            '<testsuite tests="1" failures="0" errors="0" skipped="1">'
            '<testcase classname="" name="tests.test_optional_module" '
            f'file="{reported_file}" time="0"><skipped message="collection skipped">'
            f"{body}</skipped></testcase></testsuite>",
            encoding="utf-8",
        )
        file_lanes.append(lane)
    with pytest.raises(ValueError, match="duplicate testcase identity across"):
        convert_junit(
            file_lanes, tmp_path / "file.json", tmp_path / "file.txt",
            version="3.66.fixture",
        )

    name_drift = tmp_path / "name-drift.xml"
    name_drift.write_text(
        '<testsuite tests="1" failures="0" errors="0" skipped="1">'
        '<testcase classname="" name="tests.test_optional_module" time="0">'
        '<skipped message="collection skipped">'
        "('/tmp/run/tests/test_other_module.py', 7, "
        '"Skipped: optional dependency absent")'
        "</skipped></testcase></testsuite>",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="identity disagrees with its module"):
        convert_junit(
            name_drift, tmp_path / "name.json", tmp_path / "name.txt",
            version="3.66.fixture",
        )


@pytest.mark.parametrize("case, failures, errors, skipped, match", [
    pytest.param(
        '<testcase classname="" name="tests.test_optional_module"/>',
        0, 0, 0, "stable identity", id="no-result-child",
    ),
    pytest.param(
        '<testcase classname="" name="tests.test_optional_module">'
        '<failure message="collection failure"/></testcase>',
        1, 0, 0, "stable identity", id="failure-child",
    ),
    pytest.param(
        '<testcase classname="" name="tests.test_optional_module">'
        '<skipped message="ordinary skip">'
        "('/tmp/test_optional_module.py', 1, \"Skipped: absent\")"
        "</skipped></testcase>",
        0, 0, 1, "malformed pytest collection skip", id="wrong-message",
    ),
    pytest.param(
        '<testcase classname="" name="tests.test_optional_module">'
        "<skipped>('/tmp/test_optional_module.py', 1, \"Skipped: absent\")"
        "</skipped></testcase>",
        0, 0, 1, "malformed pytest collection skip", id="missing-message",
    ),
    pytest.param(
        '<testcase classname="" name="tests.test_optional_module">'
        '<skipped message="collection skipped"></skipped></testcase>',
        0, 0, 1, "malformed pytest collection skip", id="empty-detail",
    ),
    pytest.param(
        '<testcase classname="" name="test_name">'
        '<skipped message="collection skipped">'
        "('/tmp/test_other.py', 1, \"Skipped: absent\")"
        "</skipped></testcase>",
        0, 0, 1, "identity disagrees", id="generic-name",
    ),
    pytest.param(
        '<testcase classname="" name="   ">'
        '<skipped message="collection skipped">'
        "('/tmp/test_module.py', 1, \"Skipped: absent\")"
        "</skipped></testcase>",
        0, 0, 1, "stable identity", id="whitespace-name",
    ),
    pytest.param(
        '<testcase classname="" name="tests.test_optional_module">'
        '<error message="one"/><skipped message="collection skipped">'
        "('/tmp/test_optional_module.py', 1, \"Skipped: absent\")"
        "</skipped></testcase>",
        0, 1, 1, "multiple result states", id="multiple-results",
    ),
])
def test_convert_junit_refuses_malformed_empty_class_collection_candidates(
    tmp_path, case, failures, errors, skipped, match,
) -> None:
    lane = tmp_path / "malformed.xml"
    lane.write_text(
        f'<testsuite tests="1" failures="{failures}" errors="{errors}" '
        f'skipped="{skipped}">{case}</testsuite>',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=match):
        convert_junit(
            lane, tmp_path / "results.json", tmp_path / "SUMMARY.txt",
            version="3.66.fixture",
        )


def test_convert_junit_refuses_repeated_collection_skip_within_one_lane(
    tmp_path,
) -> None:
    collection = (
        '<testcase classname="" name="tests.test_optional_module" time="0.000">'
        '<skipped message="collection skipped">'
        "('/tmp/run/tests/test_optional_module.py', 7, \"Skipped: optional absent\")"
        "</skipped></testcase>"
    )
    lane = tmp_path / "parallel.xml"
    lane.write_text(
        '<testsuite tests="2" failures="0" errors="0" skipped="2">'
        f"{collection}{collection}</testsuite>",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate testcase identity within"):
        convert_junit(
            lane, tmp_path / "results.json", tmp_path / "SUMMARY.txt",
            version="3.66.fixture",
        )


def test_convert_junit_refuses_repeated_collection_error_across_lanes(
    tmp_path,
) -> None:
    collection_error = (
        '<testcase classname="" name="tests/test_broken.py" time="0.000">'
        '<error message="collection failure">ImportError: broken module</error>'
        "</testcase>"
    )
    lanes = []
    for name in ("parallel.xml", "serial.xml"):
        lane = tmp_path / name
        lane.write_text(
            '<testsuite tests="1" failures="0" errors="1" skipped="0">'
            f"{collection_error}</testsuite>",
            encoding="utf-8",
        )
        lanes.append(lane)

    with pytest.raises(ValueError, match="duplicate testcase identity across"):
        convert_junit(
            lanes, tmp_path / "results.json", tmp_path / "SUMMARY.txt",
            version="3.66.fixture",
        )


def test_convert_junit_preserves_outputs_when_a_later_lane_is_malformed(
    tmp_path,
) -> None:
    valid = tmp_path / "parallel.xml"
    valid.write_text(
        '<testsuite tests="1" failures="0" errors="0" skipped="0">'
        '<testcase classname="tests.valid" name="test_runs"/>'
        "</testsuite>",
        encoding="utf-8",
    )
    malformed = tmp_path / "serial.xml"
    malformed.write_text("<testsuite>", encoding="utf-8")
    json_path = tmp_path / "results.json"
    summary_path = tmp_path / "SUMMARY.txt"
    json_path.write_bytes(b"JSON-SENTINEL\n")
    summary_path.write_bytes(b"SUMMARY-SENTINEL\n")

    with pytest.raises(ValueError, match="malformed JUnit evidence"):
        convert_junit(
            [valid, malformed], json_path, summary_path,
            version="3.66.fixture",
        )

    assert json_path.read_bytes() == b"JSON-SENTINEL\n"
    assert summary_path.read_bytes() == b"SUMMARY-SENTINEL\n"


def test_real_pytest_module_skip_has_the_collection_shape_we_parse(tmp_path) -> None:
    module = tmp_path / "test_real_collection_skip.py"
    junit = tmp_path / "real.xml"
    module.write_text(
        'import pytest\npytest.importorskip("bd_missing_optional_1205")\n',
        encoding="utf-8",
    )
    run = subprocess.run(
        [
            sys.executable, "-m", "pytest", str(module), "-q", "-p", "no:randomly",
            f"--junitxml={junit}",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert run.returncode == 5, run.stdout + run.stderr
    cases = list(ET.parse(junit).getroot().iter("testcase"))
    assert len(cases) == 1
    assert cases[0].get("classname") == ""
    assert cases[0].find("skipped") is not None
    assert cases[0].find("skipped").get("message") == "collection skipped"

    records = _read_lane(junit)

    assert len(records) == 1
    assert records[0]["identity"] == "<collection>::test_real_collection_skip"
    assert records[0]["status"] == "skip"
    assert records[0]["reason"] == (
        "could not import 'bd_missing_optional_1205': "
        "No module named 'bd_missing_optional_1205'"
    )


def test_convert_junit_aggregates_parallel_and_serial_lanes(tmp_path) -> None:
    parallel = tmp_path / "02_pytest_parallel.xml"
    serial = tmp_path / "02_pytest_serial.xml"
    json_path = tmp_path / "results.json"
    summary_path = tmp_path / "SUMMARY.txt"
    parallel.write_text(
        """<testsuite name="parallel" tests="2" failures="0" errors="0" skipped="1">
  <testcase classname="tests.test_safe" name="test_pass" time="0.1" />
  <testcase classname="tests.test_safe" name="test_skip" time="0.2">
    <skipped message="optional fixture" />
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )
    serial.write_text(
        """<testsuite name="serial" tests="2" failures="0" errors="1" skipped="0">
  <testcase classname="tests.test_global" name="test_serial_pass" time="0.3" />
  <testcase classname="tests.test_global" name="test_serial_error" time="0.4">
    <error message="serial error">ImportError: serial error</error>
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )

    payload = convert_junit(
        [parallel, serial],
        json_path,
        summary_path,
        version="3.66.lanes",
    )

    assert payload["total"] == 4
    assert payload["passed"] == 2
    assert payload["failed"] == 0
    assert payload["errors"] == 1
    assert payload["skipped"] == 1
    assert payload["ok"] is False
    assert {row["test"] for row in payload["tests"]} == {
        "test_pass",
        "test_skip",
        "test_serial_pass",
        "test_serial_error",
    }
    assert (
        "4 total | 2 passed | 0 failed | 1 errors | 1 skipped"
        in summary_path.read_text(encoding="utf-8")
    )
