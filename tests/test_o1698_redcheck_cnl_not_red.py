import hashlib
import json
import os
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_REDCHECK_CNL_NOT_RED_CANDIDATE", "")
BASELINE = os.environ.get("BD_O1698_REDCHECK_CNL_NOT_RED_BASELINE", "")
BASE_SHA256 = "d8d3f02d5ba7f6e905334c8d484e8b7ca51d7dd0e4db9de81f90663ae2c7e981"
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


@pytest.fixture
def scripts():
    candidate = Path(CANDIDATE)
    assert candidate.is_file() and os.access(candidate, os.X_OK), "candidate must exist and be executable"
    assert BASELINE, "baseline opt-in required for CUT 57 controls"
    baseline = Path(BASELINE)
    assert baseline.is_file() and os.access(baseline, os.X_OK), "baseline must exist and be executable"
    assert hashlib.sha256(baseline.read_bytes()).hexdigest() == BASE_SHA256, "CUT 57 baseline drift"
    return candidate, baseline


def _fixture(tmp_path, framework, body):
    if framework == "pytest":
        source = "def test_behavior():\n" + textwrap.indent(body, "    ") + "\n"
    else:
        source = (
            "import unittest\nclass Behavior(unittest.TestCase):\n"
            "    def test_behavior(self):\n" + textwrap.indent(body, "        ")
            + "\nif __name__ == '__main__':\n    unittest.main()\n"
        )
    fixture = tmp_path / "test_behavior.py"
    fixture.write_text(source)
    assert fixture.read_text() == source and fixture.stat().st_size > 0, "nonempty framework fixture"
    command = [sys.executable]
    if framework == "pytest":
        command += ["-m", "pytest", "-q", "-p", "no:cacheprovider"]
    return shlex.join([*command, str(fixture)])


def _observe(script, tmp_path, command, name):
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1")
    for key in ("PYTEST_PLUGINS", "PYTHONPATH", "BD_INSTALL_DIR"):
        env.pop(key, None)
    out = tmp_path / (name + ".json")
    result = subprocess.run(
        [str(script), "--base", str(tmp_path), "--test-cmd", command,
         "--scratch-root", str(tmp_path / name), "--out", str(out), "--timeout", "20"],
        capture_output=True, text=True, env=env, timeout=30, check=False,
    )
    assert out.is_file(), f"framework report missing: {result.stderr}"
    header, payload = out.read_text().split("\n", 1)
    report = json.loads(payload)
    assert json.loads(result.stdout) == report, "stdout/report disagree"
    assert header == "# RED-CHECK: " + report["state"], "report state/header disagree"
    return result.returncode, report


def _census(report, *, failed, passed):
    assert {key: report[key] for key in ("collected", "failed", "passed", "skipped", "errors")} == {
        "collected": 1, "failed": failed, "passed": passed, "skipped": 0, "errors": 0,
    }, "framework execution census"
    assert report["rc"] == (1 if failed else 0), "observed framework exit"


@pytest.mark.parametrize("framework", ["pytest", "unittest"])
@pytest.mark.parametrize("diagnostic", ["UNKNOWN: environment probe unavailable", "  UNKNOWN: environment probe unavailable"])
def test_unknown_assertion_is_could_not_look(scripts, tmp_path, framework, diagnostic):
    candidate, _ = scripts
    command = _fixture(tmp_path, framework, f"value = 'environment probe unavailable'\nraise AssertionError({diagnostic!r})")
    rc, report = _observe(candidate, tmp_path, command, "candidate")
    _census(report, failed=1, passed=0)
    assert (rc, report["state"]) == (5, "COULD-NOT-LOOK"), "UNKNOWN assertion must never certify RED"
    assert report["reason"] == "UNKNOWN assertion diagnostic: environment could not be judged"
    assert len(report["failures"]) == 1 and report["failures"][0]["diagnostic"] == diagnostic


@pytest.mark.parametrize("framework", ["pytest", "unittest"])
@pytest.mark.parametrize("body", [
    "value = 'actual'\nassert value == 'expected', 'behavior mismatch'",
    "value = 'actual'\nassert (\n    value\n    == 'expected'\n), 'multiline behavior mismatch'",
    "value = 'actual'\nraise AssertionError('behavior mismatch mentions UNKNOWN: as data')",
])
def test_real_red_and_cut57_reports_unchanged(scripts, tmp_path, framework, body):
    candidate, baseline = scripts
    command = _fixture(tmp_path, framework, body)
    candidate_rc, candidate_report = _observe(candidate, tmp_path, command, "candidate")
    baseline_rc, baseline_report = _observe(baseline, tmp_path, command, "baseline")
    _census(candidate_report, failed=1, passed=0)
    assert candidate_rc == baseline_rc == 0, "real assertion failure must certify RED"
    assert candidate_report["state"] == baseline_report["state"] == "PASS"
    assert len(candidate_report["failures"]) == 1
    if "multiline" in body:
        assert "value\n" in candidate_report["failures"][0]["source"], "CUT 57 multiline span preserved"
    for report in (candidate_report, baseline_report):
        for key in ("written_at", "evidence_dir"):
            report.pop(key)
    assert candidate_report == baseline_report, "CUT 57 report bytes differ beyond run identity"


@pytest.mark.parametrize("framework", ["pytest", "unittest"])
def test_green_fixture_cannot_certify_red(scripts, tmp_path, framework):
    candidate, _ = scripts
    command = _fixture(tmp_path, framework, "value = 'expected'\nassert value == 'expected', 'behavior mismatch'")
    rc, report = _observe(candidate, tmp_path, command, "candidate")
    _census(report, failed=0, passed=1)
    assert (rc, report["state"]) == (1, "NOT RED")
    assert report["reason"] == "requires rc=1, collected>=1, failed>=1, errors=0, no expected/unexpected outcomes"


def test_non_assertion_unknown_cannot_certify_red(scripts, tmp_path):
    candidate, _ = scripts
    command = _fixture(tmp_path, "pytest", "value = 'environment'\nraise ValueError('UNKNOWN: environment probe unavailable')")
    rc, report = _observe(candidate, tmp_path, command, "candidate")
    _census(report, failed=1, passed=0)
    assert (rc, report["state"]) == (1, "NOT RED")
    assert report["reason"] == "non-assertion failure"
