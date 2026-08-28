"""v3.66.1187 -- a wedged mutation measurement is UNKNOWN, never unbounded."""
from __future__ import annotations

import hashlib
import json
import math
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest


BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-mutate"
_BAND = "tests/test_m.py"
_STALL_SECONDS = 8.0
_WARMUP_TIMEOUT_SECONDS = _STALL_SECONDS
_STARTUP_HEADROOM = 1.0
_MIN_DERIVED_TIMEOUT_SECONDS = 0.25
_MAX_DERIVED_TIMEOUT_SECONDS = _STALL_SECONDS * 0.9
_SUBPROCESS_CAP_SECONDS = 30.0
_TAIL_CHARACTERS = 2000


@dataclass(frozen=True)
class _RunObservation:
    args: tuple[str, ...]
    returncode: int
    stdout: str | None
    stderr: str | None
    timeout_seconds: float | None
    wall_seconds: float | None


@dataclass(frozen=True)
class _TimingProfile:
    warmup_work: Path
    warmup: _RunObservation
    warmup_row: dict
    timeout_seconds: float
    stall_seconds: float

    @property
    def wall_bound_seconds(self) -> float:
        # A timeout run consists of a clean baseline plus the bounded mutant
        # phase. Three complete warm-up envelopes leave observed scheduling
        # headroom without turning the subprocess's 30s cap into the claim.
        assert self.warmup.wall_seconds is not None
        return self.timeout_seconds + 3 * self.warmup.wall_seconds


def _tree(tmp_path: Path, phase: str, *, initially_stalled: bool = False) -> Path:
    assert phase in {"collection", "execution", "warmup"}, phase
    (tmp_path / "tests").mkdir(parents=True)
    collection_value = initially_stalled and phase == "collection"
    execution_value = initially_stalled and phase == "execution"
    (tmp_path / "m.py").write_text(
        f"COLLECTION_STALL = {collection_value!r}\n"
        f"EXECUTION_STALL = {execution_value!r}\n"
        "VALUE = 1\n"
        f"STALL_SECONDS = {_STALL_SECONDS!r}\n",
        encoding="utf-8",
    )
    (tmp_path / _BAND).write_text(
        "import time\n"
        "import m\n"
        "if m.COLLECTION_STALL:\n"
        "    time.sleep(m.STALL_SECONDS)\n"
        "def test_behavior():\n"
        "    if m.EXECUTION_STALL:\n"
        "        time.sleep(m.STALL_SECONDS)\n"
        "    assert m.VALUE == 1\n",
        encoding="utf-8",
    )
    return tmp_path


def _phase_transform(phase: str) -> tuple[str, str, str]:
    if phase == "collection":
        return "COLLECTION_STALL = False", "COLLECTION_STALL = True", "timeout"
    if phase == "execution":
        return "EXECUTION_STALL = False", "EXECUTION_STALL = True", "timeout"
    if phase == "warmup":
        return "VALUE = 1", "VALUE = 2", "fast assertion"
    raise AssertionError(f"unmeasured phase {phase!r}")


def _run(work: Path, phase: str, timeout_seconds: float) -> _RunObservation:
    old, new, behavior = _phase_transform(phase)
    spec = work / "spec.json"
    spec.write_text(
        json.dumps({
            "schema": "bd-mutate-spec/1",
            "subject": f"bounded {phase}",
            "band": [_BAND],
            "mutants": [{
                "label": f"{behavior} during pytest {phase}",
                "file": "m.py",
                "old": old,
                "new": new,
                "direction": "regression",
                "catcher": f"{_BAND}::test_behavior",
            }],
        }),
        encoding="utf-8",
    )
    command = (
        sys.executable,
        str(_TOOL),
        "--spec",
        str(spec),
        "--work",
        str(work),
        "--timeout",
        f"{timeout_seconds:g}",
        "--json",
    )
    before = time.monotonic()
    run = subprocess.run(
        command,
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_CAP_SECONDS,
    )
    return _RunObservation(
        args=command,
        returncode=run.returncode,
        stdout=run.stdout,
        stderr=run.stderr,
        timeout_seconds=timeout_seconds,
        wall_seconds=time.monotonic() - before,
    )


def _tail(value: str | None) -> str:
    if value is None:
        return "UNKNOWN"
    stripped = value.rstrip()
    if not stripped:
        return "<EMPTY>"
    return stripped[-_TAIL_CHARACTERS:]


def _command_text(run) -> str:
    args = getattr(run, "args", None)
    if isinstance(args, str):
        return args
    if isinstance(args, (list, tuple)):
        return shlex.join(str(part) for part in args)
    return "UNKNOWN"


def _timeout_text(run) -> str:
    timeout_seconds = getattr(run, "timeout_seconds", None)
    if isinstance(timeout_seconds, (int, float)) and math.isfinite(timeout_seconds):
        return f"{timeout_seconds:g}s"
    return "UNKNOWN"


def _wall_text(run) -> str:
    wall_seconds = getattr(run, "wall_seconds", None)
    if isinstance(wall_seconds, (int, float)) and math.isfinite(wall_seconds):
        return f"{wall_seconds:.3f}s"
    return "UNKNOWN"


def _load_average_text() -> str:
    try:
        values = os.getloadavg()
    except (AttributeError, OSError):
        return "UNKNOWN"
    if len(values) != 3 or not all(math.isfinite(value) for value in values):
        return "UNKNOWN"
    return " ".join(f"{value:.2f}" for value in values)


def _zero_row_diagnostic(run) -> str:
    return "\n".join((
        "BD-MUTATE ZERO-ROW RESULT: the mutation runner returned no verdict rows",
        f"command: {_command_text(run)}",
        f"timeout passed: {_timeout_text(run)}",
        f"child wall time: {_wall_text(run)}",
        f"process return code: {getattr(run, 'returncode', 'UNKNOWN')}",
        f"host load average: {_load_average_text()}",
        f"stdout tail: {_tail(getattr(run, 'stdout', None))}",
        f"stderr tail: {_tail(getattr(run, 'stderr', None))}",
    ))


def _row(run) -> dict:
    stdout = getattr(run, "stdout", None)
    start = stdout.find("{") if isinstance(stdout, str) else -1
    assert start >= 0, _zero_row_diagnostic(run)
    document = json.loads(stdout[start:])
    rows = document.get("rows")
    assert isinstance(rows, list) and rows, _zero_row_diagnostic(run)
    assert isinstance(rows[0], dict), f"bd-mutate emitted a non-object row: {rows[0]!r}"
    return rows[0]


def _derive_timeout(warmup_wall_seconds: float) -> float:
    assert math.isfinite(warmup_wall_seconds) and warmup_wall_seconds > 0, (
        "bd-mutate warm-up wall time is impossible; expected a positive finite "
        f"measurement, observed {warmup_wall_seconds!r}")
    derived = warmup_wall_seconds * _STARTUP_HEADROOM
    assert _MIN_DERIVED_TIMEOUT_SECONDS <= derived <= _MAX_DERIVED_TIMEOUT_SECONDS, (
        "bd-mutate warm-up produced an impossible timeout calibration: "
        f"warm-up={warmup_wall_seconds:.3f}s, multiplier={_STARTUP_HEADROOM:g}, "
        f"derived={derived:.3f}s, sane band="
        f"[{_MIN_DERIVED_TIMEOUT_SECONDS:g}, {_MAX_DERIVED_TIMEOUT_SECONDS:g}]s; "
        "refusing to clamp or silently widen the timeout")
    return derived


def _calibrate(root: Path) -> _TimingProfile:
    warmup_work = _tree(root / "warmup", "warmup")
    source = (warmup_work / "m.py").read_text(encoding="utf-8")
    assert source.count("VALUE = 1") == 1, (
        "precondition: the trivial warm-up mutant must have one exact anchor")
    warmup = _run(warmup_work, "warmup", _WARMUP_TIMEOUT_SECONDS)
    warmup_row = _row(warmup)
    assert warmup_row["label"] == "fast assertion during pytest warmup", (
        "precondition: calibration did not execute the trivial warm-up mutant")
    assert warmup_row["catcher"] == f"{_BAND}::test_behavior", (
        "precondition: calibration did not grade the intended fast test")
    assert warmup.returncode == 0, (warmup.stdout or "") + (warmup.stderr or "")
    assert warmup_row["verdict"] == "CAUGHT", warmup_row
    assert warmup.wall_seconds is not None
    timeout_seconds = _derive_timeout(warmup.wall_seconds)
    assert timeout_seconds < _STALL_SECONDS, (
        "derived timeout cannot reach the subject's deliberate stall: "
        f"{timeout_seconds:.3f}s >= {_STALL_SECONDS:.3f}s")
    return _TimingProfile(
        warmup_work=warmup_work,
        warmup=warmup,
        warmup_row=warmup_row,
        timeout_seconds=timeout_seconds,
        stall_seconds=_STALL_SECONDS,
    )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _junit_residue(root: Path) -> list[str]:
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.name == "band.xml" or path.name.startswith("bd-mutate-junit-")
    )


@pytest.fixture(scope="module")
def timing_profile(tmp_path_factory: pytest.TempPathFactory) -> _TimingProfile:
    return _calibrate(tmp_path_factory.mktemp("row355-bounded-calibration"))


def test_a_collection_timeout_is_UNKNOWN_exit_2_and_restores_the_subject(
    tmp_path: Path, timing_profile: _TimingProfile,
):
    work = _tree(tmp_path / "work", "collection")
    subject = work / "m.py"
    before_digest = _digest(subject)
    assert subject.read_text(encoding="utf-8").count(
        "COLLECTION_STALL = False") == 1, (
        "precondition: the collection stall mutant has one exact inactive anchor")
    assert timing_profile.timeout_seconds < timing_profile.stall_seconds, (
        "precondition: collection work must exceed the derived timeout")

    run = _run(work, "collection", timing_profile.timeout_seconds)
    row = _row(run)
    assert row["label"] == "timeout during pytest collection", (
        "precondition: bd-mutate did not grade the collection-stall mutant")
    assert row["catcher"] == f"{_BAND}::test_behavior", (
        "precondition: the intended collected test was not the named catcher")
    assert run.returncode == 2, (run.stdout or "") + (run.stderr or "")
    assert row["verdict"] == "UNKNOWN", row
    assert f"collection exceeded {timing_profile.timeout_seconds:g}s" in row["why"], row
    assert run.wall_seconds is not None and run.wall_seconds < timing_profile.wall_bound_seconds, (
        f"the derived {timing_profile.timeout_seconds:.3f}s bound took "
        f"{run.wall_seconds!r}s; measured wall bound="
        f"{timing_profile.wall_bound_seconds:.3f}s")
    assert _digest(subject) == before_digest
    assert _junit_residue(tmp_path) == []


def test_an_execution_timeout_is_UNKNOWN_exit_2_and_leaves_no_junit(
    tmp_path: Path, timing_profile: _TimingProfile,
):
    work = _tree(tmp_path / "work", "execution")
    subject = work / "m.py"
    before_digest = _digest(subject)
    assert subject.read_text(encoding="utf-8").count(
        "EXECUTION_STALL = False") == 1, (
        "precondition: the execution stall mutant has one exact inactive anchor")
    assert timing_profile.timeout_seconds < timing_profile.stall_seconds, (
        "precondition: execution work must exceed the derived timeout")

    run = _run(work, "execution", timing_profile.timeout_seconds)
    row = _row(run)
    assert row["label"] == "timeout during pytest execution", (
        "precondition: bd-mutate did not grade the execution-stall mutant")
    assert row["catcher"] == f"{_BAND}::test_behavior", (
        "precondition: the intended executed test was not the named catcher")
    assert run.returncode == 2, (run.stdout or "") + (run.stderr or "")
    assert row["verdict"] == "UNKNOWN", row
    assert f"execution exceeded {timing_profile.timeout_seconds:g}s" in row["why"], row
    assert run.wall_seconds is not None and run.wall_seconds < timing_profile.wall_bound_seconds, (
        f"the derived {timing_profile.timeout_seconds:.3f}s bound took "
        f"{run.wall_seconds!r}s; measured wall bound="
        f"{timing_profile.wall_bound_seconds:.3f}s")
    assert _digest(subject) == before_digest
    assert _junit_residue(tmp_path) == []
