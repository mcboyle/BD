import json

from tools.pytest_capture_results import _read_version, convert_junit


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
    assert "3 total | 1 passed | 1 failed | 1 skipped" in summary_path.read_text(
        encoding="utf-8"
    )


def test_convert_junit_treats_collection_errors_as_failures(tmp_path) -> None:
    junit = tmp_path / "pytest.xml"
    json_path = tmp_path / "results.json"
    summary_path = tmp_path / "SUMMARY.txt"
    junit.write_text(
        """<testsuite name="pytest" tests="1" failures="0" errors="1" skipped="0">
  <testcase classname="" name="tests/test_broken.py">
    <error message="collection failure">ImportError: broken module</error>
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )

    payload = convert_junit(junit, json_path, summary_path, version="unknown")

    assert payload["total"] == 1
    assert payload["failed"] == 1
    assert payload["ok"] is False
    assert payload["tests"][0]["status"] == "fail"
    assert "ImportError" in payload["tests"][0]["error"]


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
        """<testsuite name="serial" tests="2" failures="1" errors="0" skipped="0">
  <testcase classname="tests.test_global" name="test_serial_pass" time="0.3" />
  <testcase classname="tests.test_global" name="test_serial_fail" time="0.4">
    <failure message="serial failure">AssertionError: serial failure</failure>
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
    assert payload["failed"] == 1
    assert payload["skipped"] == 1
    assert payload["ok"] is False
    assert {row["test"] for row in payload["tests"]} == {
        "test_pass",
        "test_skip",
        "test_serial_pass",
        "test_serial_fail",
    }
    assert "4 total | 2 passed | 1 failed | 1 skipped" in summary_path.read_text(
        encoding="utf-8"
    )
