import os
import shlex
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1675_STALE_GATE_COULD_NOT_LOOK_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


@pytest.fixture
def run_gate(tmp_path):
    candidate = Path(CANDIDATE)
    assert candidate.is_absolute() and candidate.is_file() and os.access(candidate, os.X_OK)
    source = candidate.read_text()
    body = source[source.index("run(){"):source.index('if [ "$MODE" = selftest ]; then')]
    parser = source[source.index("failure_causes(){"):source.index("live_services_present(){")]
    save = source[source.index("save_hist(){"):source.index("# Fold every landed train")]
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests/test_fixture.py").write_text("def test_fixture(): pass\n")
    report = tmp_path / "report.md"
    hist = tmp_path / "hist.tsv"
    remote = tmp_path / "remote.sh"
    prefix = "set -u\nMODE=nightly; STALE_TRAINS=3; SAY=true; PM_SEAT_FILE=/dev/null\n"
    for key, value in (("REPO", repo), ("REPORT", report), ("HIST", hist), ("REMOTE", remote)):
        prefix += key + "=" + shlex.quote(str(value)) + "\n"
    prefix += "load_hist(){ declare -gA LAST=(); }; absorb_trains(){ return 0; }; pin_worktree(){ echo \"$REPO\"; }; git(){ printf '%s\\n' " + "a" * 40 + "; }; sha12(){ printf %.12s \"$1\"; }; live_services_present(){ return 0; }\n"

    def run(rc, output=""):
        remote.write_text("#!/bin/bash\nprintf '%s' " + shlex.quote(output) + "\nexit " + str(rc) + "\n")
        remote.chmod(0o755)
        result = subprocess.run(["bash", "-c", prefix + save + parser + body + "run\n"], capture_output=True, text=True, timeout=10, check=False)
        assert (repo / "tests/test_fixture.py").is_file()
        return result, report, hist

    return run


@pytest.mark.parametrize("rc", [126, 127, 255, 2, 130, 89])
def test_runner_error_without_failure_evidence_is_unknown(run_gate, rc):
    result, report, hist = run_gate(rc, "runner could not exec: fixture\n")
    assert result.returncode == 4, "STALE-GATE-FALSE-RED: unrunnable band became RED"
    assert not report.exists(), "STALE-GATE-FALSE-RED: report written without failed tests"
    assert "COULD-NOT-LOOK" in Path(str(report) + ".log").read_text()
    assert "tests/test_fixture.py" not in hist.read_text(), "STALE-GATE-FALSE-COVERAGE"


@pytest.mark.parametrize("rc,output", [(1, "FAILED tests/test_fixture.py::test_fixture - AssertionError\n"), (126, "FAILED tests/test_fixture.py::test_fixture - AssertionError\n"), (126, "INTERNALERROR fixture real pytest internal failure\n"),
    # B1 lens: pytest's default -rfE summary names collection/setup errors ERROR, not FAILED (rc 1, a broken main).
    (1, "ERROR tests/test_fixture.py - ImportError while importing test module\n"),
    (1, "ERROR tests/test_fixture.py::test_fixture - RuntimeError: setup broke\n")])
def test_real_failures_still_write_red(run_gate, rc, output):
    result, report, _ = run_gate(rc, output)
    assert result.returncode == 1, "STALE-GATE-HIDDEN-FAILURE"
    assert report.read_text().startswith("VERDICT: RED\n"), "STALE-GATE-HIDDEN-FAILURE"
    assert output.strip() in report.read_text()


@pytest.mark.parametrize("rc,expected", [(0, 0), (5, 4), (64, 4), (90, 4)])
def test_existing_special_codes_keep_behavior(run_gate, rc, expected):
    result, report, hist = run_gate(rc)
    assert result.returncode == expected
    assert not report.exists()
    assert ("tests/test_fixture.py" in hist.read_text()) == (rc == 0)
