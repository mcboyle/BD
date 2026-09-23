"""H419: the row407 barrier git shim must not outlive the test that parked it.

MEASURED 2026-09-14: two barrier-bin/git shims from
test_concurrent_same_output_has_one_owner_and_loser_never_removes_winner were
found reparented to init hours after their pytest session died. guarded_popen
binds the replay CHILD to the test; the shim is the replay's git GRANDCHILD and
waited on a release marker only the dead test could write, with no deadline.

The shim is read out of the row407 test by AST (the literal handed to
``wrapper.write_text`` inside that test function), so this file judges the shim
that test actually installs, not a copy.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
ROW407 = ROOT / "tests" / "test_row407_candidate_replay.py"
BARRIER_TEST = "test_concurrent_same_output_has_one_owner_and_loser_never_removes_winner"
BD_GATE_SCOPE = "module"

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="reparenting semantics are Linux"
)


def _shim_source() -> str:
    tree = ast.parse(ROW407.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == BARRIER_TEST:
            for call in ast.walk(node):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "write_text"
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == "wrapper"
                    and call.args
                    and isinstance(call.args[0], ast.Constant)
                    and isinstance(call.args[0].value, str)
                ):
                    return call.args[0].value
    raise AssertionError(f"no wrapper.write_text(<literal>) in {BARRIER_TEST}")


def _install(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    shim = tmp_path / "barrier-bin" / "git"
    shim.parent.mkdir()
    shim.write_text(_shim_source())
    shim.chmod(0o755)
    markers = tmp_path / "markers"
    markers.mkdir()
    real_git = tmp_path / "real-git"
    real_git.write_text(
        "#!/bin/sh\necho exec > \"$BD_BARRIER_ROOT/real-git-ran\"\n"
    )
    real_git.chmod(0o755)
    env = dict(os.environ)
    env.update(
        BD_BARRIER_ROOT=str(markers),
        BD_GIT_ARGV_LOG=str(tmp_path / "argv.jsonl"),
        BD_REAL_GIT=str(real_git),
        BD_BARRIER_TIMEOUT="60",
    )
    return shim, markers, env


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except OSError:
        return False
    return state != "Z"


def _wait(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_shim_exits_when_the_replay_that_spawned_it_is_killed(tmp_path: Path) -> None:
    shim, markers, env = _install(tmp_path)
    env["BD_BARRIER_TIMEOUT"] = "600"  # only the orphan check may end it here
    # Stand-in for the replay child: spawns the shim, prints its pid, then waits.
    launcher = (
        "import subprocess, sys\n"
        "p = subprocess.Popen([sys.argv[1], 'worktree', 'add', 'x'])\n"
        "print(p.pid, flush=True)\n"
        "p.wait()\n"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", launcher, str(shim)],
        env=env,
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    shim_pid = 0
    try:
        assert parent.stdout is not None
        shim_pid = int(parent.stdout.readline())
        assert _wait(lambda: bool(list(markers.glob("add-*"))), 10), "shim never parked"
        assert _pid_alive(shim_pid), "positive control: parked shim is alive"
        os.kill(parent.pid, signal.SIGKILL)
        parent.wait(timeout=10)
        assert _wait(lambda: not _pid_alive(shim_pid), 5), (
            f"barrier shim {shim_pid} survived its parent's death"
        )
        assert not (markers / "real-git-ran").exists(), "orphaned shim ran real git"
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait()
        if shim_pid and _pid_alive(shim_pid):
            os.kill(shim_pid, signal.SIGKILL)


def test_shim_gives_up_at_its_deadline_without_running_git(tmp_path: Path) -> None:
    shim, markers, env = _install(tmp_path)
    env["BD_BARRIER_TIMEOUT"] = "1"
    proc = subprocess.Popen([str(shim), "worktree", "add", "x"], env=env)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        pytest.fail("barrier shim waited past its deadline")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert list(markers.glob("add-*")), "positive control: shim reached the barrier"
    assert proc.returncode != 0
    assert not (markers / "real-git-ran").exists(), "timed-out shim ran real git"


def test_released_shim_still_execs_real_git(tmp_path: Path) -> None:
    shim, markers, env = _install(tmp_path)
    (markers / "release").write_text("go\n")
    result = subprocess.run([str(shim), "worktree", "add", "x"], env=env, timeout=20)
    assert result.returncode == 0
    assert (markers / "real-git-ran").exists()
