"""Fail-closed verdicts for the nine-step deployment capture."""

import json

from tools.capture_verdict import assess_capture, main


def _write_unit(path, *, passed=2, failed=0, skipped=1, ok=True):
    tests = (
        [{"status": "pass"} for _ in range(passed)]
        + [{"status": "fail"} for _ in range(failed)]
        + [{"status": "skip"} for _ in range(skipped)]
    )
    payload = {
        "schema_version": 2,
        "total": passed + failed + skipped,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "ok": ok,
        "tests": tests,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_live(path, *, passed=3, warned=0, failed=0):
    total = passed + warned + failed
    path.write_text(
        f"live output\n{passed} pass | {warned} warn | {failed} fail  "
        f"({total} run)\n",
        encoding="utf-8",
    )


def test_clean_unit_and_live_artifacts_pass(tmp_path):
    unit = tmp_path / "unit.json"
    live = tmp_path / "live.log"
    _write_unit(unit)
    _write_live(live)

    result = assess_capture(unit, live, suite_exit=0, live_exit=0)

    assert result.ok is True
    assert result.exit_code == 0
    assert "PASS" in result.summary


def test_unit_failure_fails_even_if_suite_exit_is_zero(tmp_path):
    unit = tmp_path / "unit.json"
    live = tmp_path / "live.log"
    _write_unit(unit, passed=1, failed=1, skipped=0, ok=False)
    _write_live(live)

    result = assess_capture(unit, live, suite_exit=0, live_exit=0)

    assert result.ok is False
    assert result.exit_code != 0
    assert "unit failures=1" in result.summary


def test_live_warning_fails_even_if_live_exit_is_zero(tmp_path):
    unit = tmp_path / "unit.json"
    live = tmp_path / "live.log"
    _write_unit(unit)
    _write_live(live, passed=2, warned=1)

    result = assess_capture(unit, live, suite_exit=0, live_exit=0)

    assert result.ok is False
    assert "live warnings=1" in result.summary


def test_live_failure_fails(tmp_path):
    unit = tmp_path / "unit.json"
    live = tmp_path / "live.log"
    _write_unit(unit)
    _write_live(live, passed=2, failed=1)

    result = assess_capture(unit, live, suite_exit=0, live_exit=0)

    assert result.ok is False
    assert "live failures=1" in result.summary


def test_nonzero_process_or_stage_exit_fails(tmp_path):
    unit = tmp_path / "unit.json"
    live = tmp_path / "live.log"
    _write_unit(unit)
    _write_live(live)

    result = assess_capture(
        unit,
        live,
        suite_exit=1,
        live_exit=0,
        stage_exits=[("http-smoke", 7)],
    )

    assert result.ok is False
    assert "suite process exit=1" in result.summary
    assert "http-smoke exit=7" in result.summary


def test_missing_or_malformed_artifact_fails_closed(tmp_path):
    bad = tmp_path / "bad.json"
    missing = tmp_path / "missing.log"
    bad.write_text("not json", encoding="utf-8")

    result = assess_capture(bad, missing, suite_exit=0, live_exit=0)

    assert result.ok is False
    assert "unit artifact" in result.summary
    assert "live artifact" in result.summary


def test_incoherent_unit_counts_fail_closed(tmp_path):
    unit = tmp_path / "unit.json"
    live = tmp_path / "live.log"
    _write_unit(unit)
    payload = json.loads(unit.read_text(encoding="utf-8"))
    payload["total"] += 1
    unit.write_text(json.dumps(payload), encoding="utf-8")
    _write_live(live)

    result = assess_capture(unit, live, suite_exit=0, live_exit=0)

    assert result.ok is False
    assert "counts are inconsistent" in result.summary


def test_partial_unit_test_records_fail_closed(tmp_path):
    unit = tmp_path / "unit.json"
    live = tmp_path / "live.log"
    _write_unit(unit)
    payload = json.loads(unit.read_text(encoding="utf-8"))
    payload["tests"].pop()
    unit.write_text(json.dumps(payload), encoding="utf-8")
    _write_live(live)

    result = assess_capture(unit, live, suite_exit=0, live_exit=0)

    assert result.ok is False
    assert "test records are inconsistent" in result.summary


def test_zero_unit_tests_and_partial_live_run_fail_closed(tmp_path):
    unit = tmp_path / "unit.json"
    live = tmp_path / "live.log"
    _write_unit(unit, passed=0, failed=0, skipped=0)
    _write_live(live, passed=2)

    result = assess_capture(
        unit,
        live,
        suite_exit=0,
        live_exit=0,
        expected_live_tests=35,
    )

    assert result.ok is False
    assert "unit artifact contains zero tests" in result.summary
    assert "live artifact ran 2 tests; expected 35" in result.summary


def test_incoherent_live_run_count_fails_closed(tmp_path):
    unit = tmp_path / "unit.json"
    live = tmp_path / "live.log"
    _write_unit(unit)
    live.write_text("3 pass | 0 warn | 0 fail (4 run)\n", encoding="utf-8")

    result = assess_capture(unit, live, suite_exit=0, live_exit=0)

    assert result.ok is False
    assert "counts are inconsistent" in result.summary


def test_cli_wires_artifacts_processes_and_stages(tmp_path):
    unit = tmp_path / "unit.json"
    live = tmp_path / "live.log"
    _write_unit(unit)
    _write_live(live)

    clean = main([
        "--tests-json", str(unit),
        "--live-log", str(live),
        "--suite-exit", "0",
        "--live-exit", "0",
        "--expected-live-tests", "3",
        "--stage-exit", "http-smoke=0",
    ])
    failed = main([
        "--tests-json", str(unit),
        "--live-log", str(live),
        "--suite-exit", "0",
        "--live-exit", "0",
        "--expected-live-tests", "3",
        "--stage-exit", "http-smoke=7",
    ])

    assert clean == 0
    assert failed != 0
