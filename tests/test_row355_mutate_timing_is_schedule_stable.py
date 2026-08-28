"""Row 355: mutation timeout evidence must survive host scheduling load."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import test_v3_66_1187_bd_mutate_band_is_bounded as bounded


BD_GATE_SCOPE = "repo-wide"


def test_a_zero_row_result_is_a_diagnostic_assertion_not_an_index_error(
    monkeypatch: pytest.MonkeyPatch,
):
    command = [
        "/repo/venv/bin/python",
        "/repo/toolchain/bin/bd-mutate",
        "--timeout",
        "2.5",
        "--json",
    ]
    observation = SimpleNamespace(
        args=command,
        returncode=2,
        stdout='{"exit": 2, "rows": []}\n',
        stderr="startup budget expired\n",
        timeout_seconds=2.5,
        wall_seconds=3.125,
    )
    monkeypatch.setattr(os, "getloadavg", lambda: (7.0, 5.0, 3.0))

    with pytest.raises(AssertionError) as caught:
        bounded._row(observation)

    assert type(caught.value) is AssertionError
    message = str(caught.value)
    assert "BD-MUTATE ZERO-ROW RESULT" in message
    assert "command: /repo/venv/bin/python /repo/toolchain/bin/bd-mutate --timeout 2.5 --json" in message
    assert "timeout passed: 2.5s" in message
    assert "child wall time: 3.125s" in message
    assert "process return code: 2" in message
    assert "host load average: 7.00 5.00 3.00" in message
    assert 'stdout tail: {"exit": 2, "rows": []}' in message
    assert "stderr tail: startup budget expired" in message

    unavailable = SimpleNamespace(
        args=command,
        returncode=2,
        stdout='{"exit": 2, "rows": []}\n',
        stderr=None,
        timeout_seconds=None,
        wall_seconds=None,
    )

    def unavailable_load():
        raise OSError("load average unavailable")

    monkeypatch.setattr(os, "getloadavg", unavailable_load)
    with pytest.raises(AssertionError) as unknown_caught:
        bounded._row(unavailable)
    unknown_message = str(unknown_caught.value)
    assert "timeout passed: UNKNOWN" in unknown_message
    assert "child wall time: UNKNOWN" in unknown_message
    assert "host load average: UNKNOWN" in unknown_message
    assert "stderr tail: UNKNOWN" in unknown_message


def test_transform_control_imports_helpers_without_exercising_a_timing_verdict():
    """Mutation control: loadability alone judges no timeout or restoration."""
    assert callable(bounded._row)
    assert callable(bounded._derive_timeout)
    assert callable(bounded._run)


@pytest.fixture(scope="module")
def timing_profile(tmp_path_factory: pytest.TempPathFactory):
    return bounded._calibrate(
        tmp_path_factory.mktemp("row355-schedule-stability-calibration"))


def _measure_deliberate_work(tmp_path: Path) -> tuple[float, subprocess.CompletedProcess[str]]:
    work = bounded._tree(
        tmp_path / "measured-work", "execution", initially_stalled=True)
    subject = (work / "m.py").read_text(encoding="utf-8")
    assert subject.count("EXECUTION_STALL = True") == 1, (
        "precondition: measured work must activate exactly one execution stall")
    command = (sys.executable, "-m", "pytest", "-q", bounded._BAND)
    environment = dict(os.environ)
    environment.pop("BD_INSTALL_DIR", None)
    before = time.monotonic()
    run = subprocess.run(
        command,
        cwd=work,
        env=environment,
        capture_output=True,
        text=True,
        timeout=bounded._STALL_SECONDS * 3,
    )
    return time.monotonic() - before, run


def test_the_derived_timeout_is_smaller_than_nonzero_measured_work(
    tmp_path: Path, timing_profile,
):
    assert timing_profile.warmup.wall_seconds is not None
    assert timing_profile.warmup.wall_seconds > 0, (
        "precondition: timeout derivation needs a nonzero warm-up measurement")
    assert timing_profile.timeout_seconds == pytest.approx(
        timing_profile.warmup.wall_seconds * bounded._STARTUP_HEADROOM), (
        "timeout must remain derived from the observed bd-mutate warm-up")
    assert (bounded._MIN_DERIVED_TIMEOUT_SECONDS
            <= timing_profile.timeout_seconds
            <= bounded._MAX_DERIVED_TIMEOUT_SECONDS), (
        "derived timeout escaped the asserted sane band")

    measured_work_seconds, run = _measure_deliberate_work(tmp_path)

    assert run.returncode == 0, run.stdout + run.stderr
    assert measured_work_seconds > 0, (
        "the deliberate work measurement was unavailable or zero")
    assert timing_profile.timeout_seconds < measured_work_seconds, (
        f"derived timeout {timing_profile.timeout_seconds:.3f}s does not fit "
        f"below measured work {measured_work_seconds:.3f}s")


def test_a_non_overrunning_run_is_not_reported_as_a_timeout(timing_profile):
    warmup_source = (timing_profile.warmup_work / "m.py").read_text(
        encoding="utf-8")
    assert warmup_source.count("VALUE = 1") == 1, (
        "precondition: the fast warm-up subject was restored")
    assert timing_profile.warmup_row["label"] == (
        "fast assertion during pytest warmup"), (
        "precondition: the negative control did not run the fast mutant")
    assert timing_profile.warmup_row["catcher"] == (
        f"{bounded._BAND}::test_behavior"), (
        "precondition: the negative control did not grade its intended test")

    assert timing_profile.warmup.returncode == 0, (
        (timing_profile.warmup.stdout or "")
        + (timing_profile.warmup.stderr or ""))
    assert timing_profile.warmup_row["verdict"] == "CAUGHT", (
        timing_profile.warmup_row)
    assert "exceeded" not in timing_profile.warmup_row["why"].lower(), (
        timing_profile.warmup_row)


def test_timeout_restores_the_exact_subject_and_leaves_no_junit(
    tmp_path: Path, timing_profile,
):
    work = bounded._tree(tmp_path / "work", "execution")
    subject = work / "m.py"
    before_digest = bounded._digest(subject)
    assert subject.read_text(encoding="utf-8").count(
        "EXECUTION_STALL = False") == 1, (
        "precondition: the timeout mutant has one exact inactive anchor")
    assert timing_profile.timeout_seconds < timing_profile.stall_seconds, (
        "precondition: the deliberate execution stall exceeds its timeout")

    run = bounded._run(work, "execution", timing_profile.timeout_seconds)
    row = bounded._row(run)
    assert row["label"] == "timeout during pytest execution", (
        "precondition: bd-mutate did not grade the execution timeout mutant")
    assert row["catcher"] == f"{bounded._BAND}::test_behavior", (
        "precondition: the intended execution test was not the named catcher")
    assert run.returncode == 2, (run.stdout or "") + (run.stderr or "")
    assert row["verdict"] == "UNKNOWN", row
    assert f"execution exceeded {timing_profile.timeout_seconds:g}s" in row["why"], row
    assert run.wall_seconds is not None and run.wall_seconds > 0, (
        "the timed-out child wall measurement was unavailable or zero")
    assert bounded._digest(subject) == before_digest
    assert bounded._junit_residue(tmp_path) == []
