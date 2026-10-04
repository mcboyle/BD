"""O1698 test-filter-status (FR-6/FR-7 class): bd-test-filter.sh printed `status=PASS` whenever the wrapped SHELL
command exited 0, so `pytest ... > run.log; rc=$?; tail -1 run.log; echo rc=$rc` showed status=PASS beside
"1 failed, 1 passed". The status must come from the pytest summary; the exit code stays the command's rc.

Runs the candidate script named by BD_O1698_TEST_FILTER_STATUS_CANDIDATE against real pytest runs in tmp_path.
"""
import os
import subprocess
import sys

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_TEST_FILTER_STATUS_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

SPEC = """
def test_ok():
    assert 1 + 1 == 2

def test_bad():
    assert "o1698-filter" == "o1698-filtr"
"""
PY = f"{sys.executable} -m pytest -q -p no:cacheprovider -p no:randomly"


@pytest.fixture
def run(tmp_path):
    assert os.path.isfile(CANDIDATE), f"candidate missing: {CANDIDATE}"
    (tmp_path / "test_tf.py").write_text(SPEC)
    (tmp_path / "test_ok_only.py").write_text("def test_ok():\n    assert 2 * 2 == 4\n")
    (tmp_path / "test_skip_only.py").write_text("import pytest\n@pytest.mark.skip\ndef test_s():\n    pass\n")
    env = {**os.environ, "BD_RUN_ARTIFACTS": str(tmp_path / "artifacts"), "PYTHONDONTWRITEBYTECODE": "1"}

    def _run(shell, stdin=None, args=None):
        cmd = [sys.executable, CANDIDATE, *(args or ["bash", "-c", shell])]
        return subprocess.run(cmd, cwd=tmp_path, env=env, input=stdin, text=True, capture_output=True, timeout=120)
    return _run


def _status(r):
    return next(l for l in r.stdout.splitlines() if l.startswith("[BD-TEST-FILTER] "))


def test_compound_failing_command_is_fail_not_pass(run):
    r = run(f"{PY} test_tf.py > run.log; rc=$?; tail -1 run.log; echo rc=$rc")
    assert r.returncode == 0, "the exit code must stay the shell command's rc"
    line = _status(r)
    assert line.startswith("[BD-TEST-FILTER] status=FAIL (tests: 1 failed, 1 passed in "), line
    assert line.endswith(" cmd-rc=0"), line
    own = next(l for l in r.stdout.splitlines() if l.startswith("OWN-TESTS: "))
    assert "cmd-rc=0" in own and "tests=1_failed,_1_passed_in_" in own, own


def test_plain_failing_is_fail_with_pytest_rc(run):
    r = run(f"{PY} test_tf.py")
    assert r.returncode == 1
    assert _status(r).startswith("[BD-TEST-FILTER] status=FAIL (tests: 1 failed, 1 passed in "), r.stdout


def test_plain_passing_is_pass(run):
    r = run(f"{PY} test_ok_only.py")
    assert r.returncode == 0
    line = _status(r)
    assert line.startswith("[BD-TEST-FILTER] status=PASS (tests: 1 passed in ") and line.endswith(" cmd-rc=0"), line


def test_no_pytest_output_is_unknown(run):
    r = run("echo building; exit 0")
    assert r.returncode == 0
    assert _status(r) == "[BD-TEST-FILTER] status=UNKNOWN (no pytest summary) cmd-rc=0"


def test_only_skipped_is_not_pass(run):
    r = run(f"{PY} test_skip_only.py")
    assert r.returncode == 0
    assert _status(r).startswith("[BD-TEST-FILTER] status=UNKNOWN (tests: 1 skipped in "), r.stdout


def test_no_tests_ran_is_fail(run):
    r = run(f"{PY} -k nothing_matches test_ok_only.py; exit 0")
    assert r.returncode == 0
    assert _status(r).startswith("[BD-TEST-FILTER] status=FAIL (tests: 1 deselected in "), r.stdout


def test_internalerror_is_fail(run):
    r = run("echo 'INTERNALERROR> Traceback'; echo '1 passed in 0.01s'")
    assert r.returncode == 0
    assert _status(r).startswith("[BD-TEST-FILTER] status=FAIL (tests: INTERNALERROR"), r.stdout


def test_two_runs_failing_first_is_fail(run):
    """The failing run's summary is NOT the last line: every summary counts, not only the final one."""
    r = run(f"{PY} test_tf.py; {PY} test_ok_only.py; exit 0")
    assert r.returncode == 0
    assert _status(r).startswith("[BD-TEST-FILTER] status=FAIL (tests: 1 failed, 1 passed in "), r.stdout


def test_collection_error_is_fail(run, tmp_path):
    (tmp_path / "test_broken.py").write_text("def test_x(:\n    pass\n")
    r = run(f"{PY} test_broken.py; exit 0")
    assert r.returncode == 0
    assert _status(r).startswith("[BD-TEST-FILTER] status=FAIL (tests: 1 error in "), r.stdout


def test_stdin_mode_reads_the_summary_too(run):
    r = run(None, stdin="FAILED test_x.py::t\n1 failed in 0.02s\n", args=["--rc", "0"])
    assert r.returncode == 0
    assert _status(r) == "[BD-TEST-FILTER] status=FAIL (tests: 1 failed in 0.02s) cmd-rc=0"
