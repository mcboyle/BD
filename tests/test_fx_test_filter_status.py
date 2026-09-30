"""fx-test-filter-status (ESC-TEST-FILTER-hook-0710Z): the BD-TEST-FILTER status follows the test result.

The hook runs every test command as ``bd-test-filter.sh bash -o pipefail -c '<command>'`` and printed
``status=PASS`` whenever the WHOLE expression exited 0. A builder ran ``pytest ... > RED.log; tail RED.log``:
the log said "2 failed", tail exited 0, the hook said "status=PASS rc=0" (fx-harden-kellymadisonmedia RED).
Pipes were already right (pipefail); a ``;`` list reports its last command.

Each case runs REAL pytest on a two-test fixture under the candidate filter. Candidate directory (holding
scripts/bd-test-filter.sh) via BD_FX_TEST_FILTER_STATUS_CANDIDATE; the integrator deploys it to the plugin.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_FX_TEST_FILTER_STATUS_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

RED = 'def test_ok():\n    assert True\n\n\ndef test_red():\n    assert 1 == 2, "FIXTURE-RED"\n'
GREEN = 'def test_ok():\n    assert True\n'


@pytest.fixture
def run(tmp_path):
    tool = Path(CANDIDATE) / "scripts" / "bd-test-filter.sh"
    assert tool.is_file(), f"candidate filter missing: {tool}"
    (tmp_path / "test_red_fixture.py").write_text(RED, encoding="utf-8")
    (tmp_path / "test_green_fixture.py").write_text(GREEN, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    env["BD_RUN_ARTIFACTS"] = str(tmp_path / "artifacts")
    env["LC_ALL"] = "C"
    pytest_cmd = (f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider -p no:randomly "
                  f"--rootdir {shlex.quote(str(tmp_path))}")

    def _run(shell):
        shell = shell.replace("PYTEST", pytest_cmd)
        return subprocess.run([sys.executable, str(tool), "bash", "-o", "pipefail", "-c", shell],
                              cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    return _run


def _status(res):
    return res.stdout.splitlines()[0] if res.stdout else res.stderr


def test_a_redirected_red_run_read_back_with_tail_is_a_fail(run):
    res = run("PYTEST test_red_fixture.py > RED.log 2>&1; tail -1 RED.log")
    assert "1 failed, 1 passed" in res.stdout, res.stdout
    assert _status(res).startswith("[BD-TEST-FILTER] status=FAIL rc=1") and res.returncode == 1, (
        f"FX-TEST-FILTER-STATUS: pytest said '1 failed' but the filter reported {_status(res)!r} rc={res.returncode}")


def test_a_redirected_green_run_read_back_with_tail_is_a_pass(run):
    res = run("PYTEST test_green_fixture.py > GREEN.log 2>&1; tail -1 GREEN.log")
    assert _status(res) == "[BD-TEST-FILTER] status=PASS rc=0" and res.returncode == 0, _status(res)


def test_a_result_that_never_reaches_the_output_is_unknown_not_pass(run):
    res = run("PYTEST test_red_fixture.py > RED.log 2>&1; true")
    assert _status(res).startswith("[BD-TEST-FILTER] status=UNKNOWN rc=4") and res.returncode == 4, (
        f"FX-TEST-FILTER-STATUS: an unread result was reported as {_status(res)!r} rc={res.returncode}")


def test_red_then_green_in_one_command_is_a_fail(run):
    res = run("PYTEST test_red_fixture.py | tail -1; PYTEST test_green_fixture.py | tail -1")
    assert _status(res).startswith("[BD-TEST-FILTER] status=FAIL") and res.returncode != 0, _status(res)


def test_control_a_piped_red_run_was_and_stays_a_fail(run):
    res = run("PYTEST test_red_fixture.py | tail -1")
    assert _status(res).startswith("[BD-TEST-FILTER] status=FAIL rc=1") and res.returncode == 1, _status(res)
