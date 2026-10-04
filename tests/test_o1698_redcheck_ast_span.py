import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_REDCHECK_AST_SPAN_CANDIDATE", "")
pytestmark = pytest.mark.skipif(
    os.environ.get("BD_TEST_O1698_REDCHECK_AST_SPAN") != "1" or not CANDIDATE,
    reason="candidate opt-in required",
)
BASE_SHA256 = "da4127c05944fb7e72f43ecede0578fcb24079a4fa566ccb6425676b6d209bb3"


@pytest.fixture
def scripts():
    candidate = Path(CANDIDATE)
    baseline = candidate.with_name("bd-red-check.sh.pre")
    assert candidate.is_file() and os.access(candidate, os.X_OK), "candidate unavailable"
    assert baseline.is_file(), "baseline receipt unavailable"
    assert hashlib.sha256(baseline.read_bytes()).hexdigest() == BASE_SHA256
    return baseline, candidate


def fixture_file(tmp_path, framework, body):
    if framework == "pytest":
        source = "def test_behavior():\n" + body
    else:
        source = "import unittest\n\nclass Behavior(unittest.TestCase):\n    def test_behavior(self):\n"
        source += "\n".join("    " + line for line in body.splitlines()) + "\n"
    path = tmp_path / "test_behavior.py"
    path.write_text(source)
    assert path.read_text() == source and len(source.splitlines()) >= 2
    return path


def run_check(script, path, framework, label):
    report_path = path.parent / (label + ".json")
    command = shlex.join([sys.executable, "-m", framework, path.name, "-q"])
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    env.pop("PYTEST_PLUGINS", None)
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [str(script), "--base", str(path.parent), "--test-cmd", command,
         "--out", str(report_path), "--scratch-root", str(path.parent / label)],
        capture_output=True, text=True, env=env, timeout=30, check=False,
    )
    assert report_path.is_file(), result.stdout + result.stderr
    report = json.loads(report_path.read_text().split("\n", 1)[1])
    return result, report


@pytest.mark.parametrize("framework", ["pytest", "unittest"])
@pytest.mark.parametrize("assertion", [
    '    assert (\n        actual == 2), (\n        "AST-SPAN-DISTINCTIVE")\n',
    '    assert actual == 2, (\n        "AST-SPAN-DISTINCTIVE"\n    )\n',
])
def test_multiline_assertion_is_behavioral_red(tmp_path, scripts, framework, assertion):
    path = fixture_file(tmp_path, framework, "    actual = 1\n" + assertion)
    baseline, candidate = scripts
    before, old = run_check(baseline, path, framework, "baseline")
    assert before.returncode == 1 and old["state"] == "NOT RED", before.stdout
    assert old["reason"] == "assertion source is not independently parseable", before.stdout
    assert (old["collected"], old["failed"], old["errors"]) == (1, 1, 0)
    after, new = run_check(candidate, path, framework, "candidate")
    assert after.returncode == 0 and new["state"] == "PASS", after.stdout
    assert "AST-SPAN-DISTINCTIVE" in after.stdout
    assert (new["collected"], new["failed"], new["errors"]) == (1, 1, 0)
    source = new["failures"][0]["source"]
    assert source.startswith("assert") and source.count("\n") == 2
    assert "actual == 2" in source and '"AST-SPAN-DISTINCTIVE"' in source


def test_multiline_unittest_call_is_behavioral_red(tmp_path, scripts):
    path = fixture_file(tmp_path, "unittest", '    actual = 1\n    self.assertEqual(\n        actual, 2,\n        "AST-CALL-DISTINCTIVE")\n')
    baseline, candidate = scripts
    before, old = run_check(baseline, path, "unittest", "baseline")
    assert before.returncode == 1 and old["reason"] == "assertion source is not independently parseable", before.stdout
    after, new = run_check(candidate, path, "unittest", "candidate")
    assert after.returncode == 0 and new["state"] == "PASS", after.stdout
    assert "AST-CALL-DISTINCTIVE" in after.stdout
    assert new["failures"][0]["source"].startswith("self.assertEqual(")
    assert new["failures"][0]["source"].count("\n") == 2


@pytest.mark.parametrize("framework", ["pytest", "unittest"])
@pytest.mark.parametrize("body", [
    '    actual = 1\n    assert actual == 2, "SINGLE-LINE-DISTINCTIVE"\n',
    '    raise AssertionError("SINGLE-LINE-DISTINCTIVE")\n',
])
def test_single_line_positive_control_unchanged(tmp_path, scripts, framework, body):
    path = fixture_file(tmp_path, framework, body)
    baseline, candidate = scripts
    before, old = run_check(baseline, path, framework, "baseline")
    after, new = run_check(candidate, path, framework, "candidate")
    assert before.returncode == after.returncode == 0, after.stdout
    assert old["state"] == new["state"] == "PASS"
    assert old["failures"] == new["failures"]
    assert old["reason"] == new["reason"]
    assert "SINGLE-LINE-DISTINCTIVE" in after.stdout


@pytest.mark.parametrize("framework", ["pytest", "unittest"])
@pytest.mark.parametrize("body, counts", [
    ('    actual = 1\n    assert actual == 1, "PASS-CONTROL"\n', (0, 0, 1, 0)),
    ('    raise ValueError("NON-ASSERT-DISTINCTIVE")\n', (1, 1, 0, 0)),
])
def test_passing_and_non_assertion_controls_unchanged(tmp_path, scripts, framework, body, counts):
    path = fixture_file(tmp_path, framework, body)
    baseline, candidate = scripts
    before, old = run_check(baseline, path, framework, "baseline")
    after, new = run_check(candidate, path, framework, "candidate")
    assert before.returncode == after.returncode == 1, after.stdout
    assert old["state"] == new["state"] == "NOT RED"
    assert old["reason"] == new["reason"]
    reason = "non-assertion failure" if framework == "pytest" and "ValueError" in body else "requires rc=1, collected>=1, failed>=1, errors=0, no expected/unexpected outcomes"
    assert new["reason"] == reason and reason in after.stdout
    assert old["collected"] == new["collected"] == 1
    measured = tuple(new[k] for k in ("rc", "failed", "passed", "errors"))
    expected = (1, 0, 0, 1) if framework == "unittest" and "ValueError" in body else counts
    assert measured == expected
    if "ValueError" in body:
        log = Path(new["evidence_dir"]) / "execution.log"
        assert "NON-ASSERT-DISTINCTIVE" in log.read_text()


@pytest.mark.parametrize("framework", ["pytest", "unittest"])
def test_unparseable_file_reports_single_line_fallback(tmp_path, scripts, framework):
    body = ('    from pathlib import Path\n'
            '    path = Path(__file__)\n'
            '    path.write_text(path.read_text() + "\\n(\\n")\n'
            '    actual = 1\n'
            '    assert actual == 2, "FALLBACK-DISTINCTIVE"\n')
    path = fixture_file(tmp_path, framework, body)
    result, report = run_check(scripts[1], path, framework, "fallback")
    assert result.returncode == 0 and report["state"] == "PASS", result.stdout
    assert "SyntaxError" in result.stdout and "single-line fallback" in result.stdout
    assert report["failures"][0]["source"] == 'assert actual == 2, "FALLBACK-DISTINCTIVE"'
