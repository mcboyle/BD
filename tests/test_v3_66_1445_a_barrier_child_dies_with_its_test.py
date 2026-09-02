"""A child a test launches at a barrier must not outlive the test that launched it.

MEASURED 2026-09-02 on test5: a `bd-register-append` child of
tests/test_register_append.py was found with ppid 1, 3.3 hours old, sleeping in
the injected barrier loop of a pytest run that had been killed. The loop waits
for a `release` file that only the test writes, so once the test died nothing
could ever release it, and nothing bound its lifetime to the launcher's. It
held the fixture register's directory open the whole time.

Two-sided repair, the same shape as test_v3_66_1054: every process the register
tests start is bound to its launcher with PR_SET_PDEATHSIG (tests/_child_guard.py),
so a killed test takes its child with it; and the injected barrier wait carries
a budget, so a child whose release never arrives exits by itself with a
distinctive diagnostic (exit 97) rather than sleeping until someone finds it.

The first test exercises the REAL helper, `_start`, from inside a launcher
process that is then SIGKILLed -- the exact shape of the orphan -- and asserts
the child is gone. The precondition (the child is alive and AT the barrier) is
asserted before the kill so that a child which never started cannot pass.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

ROOT = Path(__file__).resolve().parents[1]
REGISTER_TESTS = ROOT / "tests" / "test_register_append.py"


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _alive(pid: int) -> bool:
    try:
        state = (Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0])
    except (FileNotFoundError, ProcessLookupError, IndexError):
        return False
    return state != "Z"


def _reap_if_alive(pid: int) -> None:
    if _alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


# Runs the real fixture and the real `_start` helper, parks the child at the
# barrier, reports its pid, then idles: the launcher that will be killed.
LAUNCHER = r"""
import importlib.util, os, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("register_append_tests", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
work = Path(sys.argv[2])
repo, register = m._fixture_repo(work)
m._install_lock_barrier(repo)
barrier = work / "barrier"; barrier.mkdir()
request = work / "append.json"
m._write_request(request, m._request(repo, register, ["| 403 | OPEN | orphan candidate | "]))
env = os.environ.copy(); env["BD_REGISTER_APPEND_TEST_BARRIER"] = str(barrier)
child = m._start(repo, request, env)
m._wait_for(barrier / f"arrived-{child.pid}", child)
print(child.pid, flush=True)
time.sleep(120)
"""


def test_a_child_parked_at_the_barrier_dies_when_its_launcher_is_killed(tmp_path: Path) -> None:
    launcher = subprocess.Popen(
        [sys.executable, "-c", LAUNCHER, str(REGISTER_TESTS), str(tmp_path)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    child_pid = 0
    try:
        ready, _, _ = select.select([launcher.stdout], [], [], 30)
        assert ready, "launcher never reported a child pid"
        line = launcher.stdout.readline().strip()
        assert line.isdigit(), f"launcher wrote {line!r}, stderr={launcher.stderr.read()!r}"
        child_pid = int(line)

        # PRECONDITIONS: the child exists, is the register tool, and is parked.
        cmdline = Path(f"/proc/{child_pid}/cmdline").read_bytes()
        assert b"bd-register-append" in cmdline, cmdline
        assert (tmp_path / "barrier" / f"arrived-{child_pid}").exists()
        assert _alive(child_pid), "child was not alive before the launcher died"
        assert launcher.poll() is None

        os.kill(launcher.pid, signal.SIGKILL)
        launcher.wait(timeout=10)
        assert launcher.returncode == -signal.SIGKILL

        deadline = time.monotonic() + 5
        while _alive(child_pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not _alive(child_pid), (
            f"child {child_pid} survived its launcher: a killed test would leave "
            "it sleeping at the barrier forever"
        )
    finally:
        if launcher.poll() is None:
            launcher.kill()
            launcher.wait(timeout=10)
        if child_pid:
            _reap_if_alive(child_pid)


def test_an_unreleased_barrier_expires_by_itself_with_a_named_exit(tmp_path: Path) -> None:
    m = _load(REGISTER_TESTS)
    repo, register = m._fixture_repo(tmp_path)
    m._install_lock_barrier(repo)
    barrier = tmp_path / "barrier"
    barrier.mkdir()
    request = tmp_path / "append.json"
    m._write_request(request, m._request(repo, register, ["| 403 | OPEN | never released | "]))
    env = os.environ.copy()
    env["BD_REGISTER_APPEND_TEST_BARRIER"] = str(barrier)
    env["BD_REGISTER_APPEND_TEST_BARRIER_BUDGET"] = "1"
    process = m._start(repo, request, env)
    try:
        m._wait_for(barrier / f"arrived-{process.pid}", process)
        assert not (barrier / "release").exists()
        try:
            out, err = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            pytest.fail("an unreleased barrier never expired: the child would outlive the test")
    finally:
        _reap_if_alive(process.pid)
    assert process.returncode == 97, (process.returncode, out, err)
    assert "barrier release never arrived" in err, err
    # The register was not published by a child that gave up.
    assert "never released" not in register.read_text(encoding="ascii")


# Every process the register tests start goes through the guard. The
# denominator is the three files that inject a barrier; the count is exact so
# that a new unguarded Popen in any of them is a failure, not a drift.
GUARDED_SITES = {
    "tests/test_register_append.py": 3,
    "tests/test_register_content_amend.py": 1,
    "tests/test_row407_candidate_replay.py": 2,
}


@pytest.mark.parametrize("relative,expected", sorted(GUARDED_SITES.items()))
def test_every_register_test_launcher_is_guarded(relative: str, expected: int) -> None:
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    bare = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Popen"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
    ]
    guarded = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "guarded_popen"
    ]
    assert not bare, f"{relative}: bare subprocess.Popen at lines {bare}"
    assert len(guarded) == expected, f"{relative}: guarded_popen at {guarded}, expected {expected}"
