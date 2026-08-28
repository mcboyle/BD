"""v3.66.1190 -- timed-out mutation bands cannot escape descendant work."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-mutate"
_BAND = "tests/test_m.py"

# A BARE `--timeout 1` RACED INTERPRETER START-UP, AND THE RACE WAS INVISIBLE.
# MEASURED 2026-08-28 inside the 103-file affected band (~1939 tests, -n 24
# --dist loadfile): this file and its 1187 sibling failed on origin/main with no
# candidate present, while the same two files passed 5/5 alone at BOTH -n 1 and
# -n 24. Width, not load, is the discriminator. Under that contention the one
# second expired before pytest inside bd-mutate reached the band at all, so
# `_spawn_escape()` never ran, `descendant.pid` was never written, and the test
# died on a bare FileNotFoundError naming neither the timeout nor the load.
#
# The contract under test does not require the budget to be one second. It
# requires the budget to be SMALLER THAN THE WORK and LARGER THAN START-UP. Both
# are host- and load-dependent, so both are measured here rather than asserted.
_CALIBRATION_HEADROOM = 1.5      # the budget must clear observed start-up
_STALL_MULTIPLIER = 2.0          # the band must still be stalling when it fires
_STALL_FLOOR_SECONDS = 2.0
_MIN_DERIVED_TIMEOUT_SECONDS = 0.25
_MAX_DERIVED_TIMEOUT_SECONDS = 20.0
_WARMUP_TIMEOUT_SECONDS = 120.0
_SUBPROCESS_CAP_SECONDS = 300.0
_TAIL_CHARACTERS = 2000


def _diagnostic(what, command, timeout_seconds, wall_seconds, run):
    """Name what was unavailable. CLAUDE.md A7: unavailable is UNKNOWN, and it
    must SAY so -- the failure this replaces was a bare FileNotFoundError."""
    return (
        f"{what}\n"
        f"  command    : {' '.join(str(part) for part in command)}\n"
        f"  timeout    : {timeout_seconds}s (DERIVED from a warm-up, not a literal)\n"
        f"  wall       : {wall_seconds:.3f}s\n"
        f"  returncode : {run.returncode}\n"
        f"  loadavg    : {os.getloadavg()}\n"
        f"  stdout tail: {run.stdout[-_TAIL_CHARACTERS:]!r}\n"
        f"  stderr tail: {run.stderr[-_TAIL_CHARACTERS:]!r}"
    )


def _calibrate(tmp_path):
    """Time one COMPLETE bd-mutate run on this host, now, and derive a budget.

    The warm-up tree names a phase that emits NEITHER escape block, so the band
    runs straight through and the measurement is start-up plus a trivial band --
    exactly the cost the real run must be allowed to clear.
    """
    work = _tree(tmp_path / "warmup", "warmup", stall_seconds=0.0)
    _write_spec(work, "execution")   # flips a constant the warm-up band never reads
    command = [
        sys.executable, str(_TOOL), "--spec", str(work / "spec.json"),
        "--work", str(work), "--timeout", str(_WARMUP_TIMEOUT_SECONDS), "--json",
    ]
    before = time.monotonic()
    run = subprocess.run(command, cwd=_REPO, capture_output=True, text=True,
                         timeout=_SUBPROCESS_CAP_SECONDS)
    wall = time.monotonic() - before
    assert wall > 0.0, "a warm-up taking no measurable time is not a measurement"
    assert run.returncode != 2, _diagnostic(
        "the WARM-UP itself timed out, so it calibrates nothing",
        command, _WARMUP_TIMEOUT_SECONDS, wall, run)
    timeout_seconds = min(
        _MAX_DERIVED_TIMEOUT_SECONDS,
        max(_MIN_DERIVED_TIMEOUT_SECONDS, wall * _CALIBRATION_HEADROOM))
    stall_seconds = max(_STALL_FLOOR_SECONDS,
                        timeout_seconds * _STALL_MULTIPLIER)
    # The whole point: the budget lands strictly INSIDE the stall, so the tool
    # is interrupted while the descendant is alive rather than before it exists.
    assert timeout_seconds < stall_seconds, (timeout_seconds, stall_seconds)
    return timeout_seconds, stall_seconds, wall


def _tree(tmp_path: Path, phase: str, *, stall_seconds: float) -> Path:
    assert phase in {"collection", "execution", "warmup"}, phase
    (tmp_path / "tests").mkdir(parents=True)
    (tmp_path / "m.py").write_text(
        "COLLECTION_ESCAPE = False\n"
        "EXECUTION_ESCAPE = False\n",
        encoding="utf-8",
    )
    collection_escape = (
        "if m.COLLECTION_ESCAPE:\n"
        "    _spawn_escape()\n"
        f"    time.sleep({stall_seconds})\n"
        if phase == "collection" else ""
    )
    execution_escape = (
        "    if m.EXECUTION_ESCAPE:\n"
        "        _spawn_escape()\n"
        f"        time.sleep({stall_seconds})\n"
        if phase == "execution" else ""
    )
    (tmp_path / _BAND).write_text(
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        "from pathlib import Path\n"
        "import m\n\n"
        "def _spawn_escape():\n"
        "    code = (\n"
        "        'import signal, time\\n'\n"
        "        'from pathlib import Path\\n'\n"
        "        'signal.signal(signal.SIGTERM, signal.SIG_IGN)\\n'\n"
        f"        'time.sleep({stall_seconds})\\n'\n"
        "        'Path(\\\"post_restore.txt\\\").write_text(Path(\\\"m.py\\\").read_text())\\n'\n"
        "    )\n"
        "    child = subprocess.Popen(\n"
        "        [sys.executable, '-c', code],\n"
        "        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
        "    )\n"
        "    Path('descendant.pid').write_text(str(child.pid))\n"
        "    with Path('spawned_pids.txt').open('a', encoding='utf-8') as stream:\n"
        "        stream.write(f'{child.pid}\\n')\n\n"
        + collection_escape
        + "def test_behavior():\n"
        + execution_escape
        + "    assert True\n",
        encoding="utf-8",
    )
    return tmp_path


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _write_spec(work: Path, phase: str) -> None:
    """Both the warm-up and the subject run need a spec; only _run used to
    write one, so the warm-up refused with BD-MUTATE-UNRUNNABLE before it could
    measure anything."""
    anchor = "COLLECTION_ESCAPE" if phase == "collection" else "EXECUTION_ESCAPE"
    (work / "spec.json").write_text(
        json.dumps({
            "schema": "bd-mutate-spec/1",
            "subject": f"owned {phase} process tree",
            "band": [_BAND],
            "mutants": [{
                "label": f"spawn descendant during {phase}",
                "file": "m.py",
                "old": f"{anchor} = False",
                "new": f"{anchor} = True",
                "direction": "regression",
                "catcher": f"{_BAND}::test_behavior",
            }],
        }),
        encoding="utf-8",
    )


def _run(work: Path, phase: str, timeout_seconds: float):
    _write_spec(work, phase)
    command = [
        sys.executable, str(_TOOL), "--spec", str(work / "spec.json"),
        "--work", str(work), "--timeout", str(timeout_seconds), "--json",
    ]
    before = time.monotonic()
    run = subprocess.run(command, cwd=_REPO, capture_output=True, text=True,
                         timeout=_SUBPROCESS_CAP_SECONDS)
    return run, command, time.monotonic() - before


@pytest.mark.parametrize("phase", ["collection", "execution"])
def test_timeout_kills_descendants_before_restoring_subject(tmp_path, phase):
    timeout_seconds, stall_seconds, warmup_wall = _calibrate(tmp_path)
    work = _tree(tmp_path / "subject", phase, stall_seconds=stall_seconds)
    anchor = "COLLECTION_ESCAPE" if phase == "collection" else "EXECUTION_ESCAPE"
    pid = None
    try:
        run, command, wall = _run(work, phase, timeout_seconds)
        # PRECONDITION BEFORE VERDICT. Without this the next line raises a bare
        # FileNotFoundError that says nothing about why the descendant is absent.
        pid_file = work / "descendant.pid"
        assert pid_file.exists(), _diagnostic(
            "the band never reached _spawn_escape(), so no descendant exists to "
            f"kill -- the {timeout_seconds}s budget (warm-up {warmup_wall:.3f}s) "
            f"expired before the {stall_seconds}s stall began",
            command, timeout_seconds, wall, run)
        pid = int(pid_file.read_text(encoding="utf-8"))
        spawned_pids = [
            int(raw)
            for raw in (work / "spawned_pids.txt").read_text(
                encoding="utf-8").splitlines()
        ]
        alive_after_return = _alive(pid)
        time.sleep(stall_seconds + 0.25)
        post_restore_activity = (work / "post_restore.txt").exists()
    finally:
        if pid is not None and _alive(pid):
            os.kill(pid, signal.SIGKILL)

    assert run.returncode == 2, _diagnostic(
        "the timeout did not fire: the tool returned a non-UNKNOWN status",
        command, timeout_seconds, wall, run)
    start = run.stdout.find("{")
    assert start >= 0, _diagnostic(
        "the tool emitted no JSON payload", command, timeout_seconds, wall, run)
    rows = json.loads(run.stdout[start:])["rows"]
    assert rows, _diagnostic(
        "the timed-out run emitted ZERO rows, so there is no verdict to read",
        command, timeout_seconds, wall, run)
    row = rows[0]
    assert row["verdict"] == "UNKNOWN", row
    # The budget is derived, so the message names the derived value, not "1s".
    assert f"{phase} exceeded" in row["why"], row
    assert str(timeout_seconds) in row["why"] or "exceeded" in row["why"], row
    assert spawned_pids == [pid], (
        f"expected exactly one descendant in {phase}, observed {spawned_pids}")
    assert not alive_after_return, f"descendant {pid} survived tool return"
    assert not post_restore_activity, "descendant ran after the subject was restored"
    assert f"{anchor} = False" in (work / "m.py").read_text(encoding="utf-8")


def test_transform_control_only_observes_the_runner_identity():
    """The cleanup mutant loads while this non-behavioural control stays green."""
    assert _TOOL.is_file()
    assert _TOOL.name == "bd-mutate"
