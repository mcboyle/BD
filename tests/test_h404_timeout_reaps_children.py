"""H404 -- a test cut off by pytest-timeout must not leave its processes running.

WHAT WENT WRONG (B2-B LOOK-AT, 2026-09-14). The precut runs
``--timeout=240 --timeout-method=signal``. The alarm cuts the TEST off, not
the processes it started: a gate test that tripped the 240 s timeout left its
shelled child (bd-tool-lint) alive on the pool host, unreaped and burning CPU
on exactly the hosts that were already overloaded (H360). The LOOK-AT probe
(fleet-run-artifacts/h389-B2-B-20260914/lookat-alarm-vs-child.py) printed
``CHILD ALIVE AFTER THE ALARM: True``.

WHAT IS ASSERTED HERE. An inner pytest session (``--timeout=4
--timeout-method=signal``, the reaper loaded exactly when tests/conftest.py
registers it in ``pytest_plugins``) runs a subject module whose timed-out
tests leave:

  A1  a ``Popen`` child the test never waited for (the probe's shape);
  A2  a grandchild behind ``subprocess.run``, which kills only the shell it
      started, so the grandchild is orphaned to init (the bd-tool-lint shape);
  A3  a child started with a scrubbed environment (no token to find it by);

every one of them must be dead when the inner session ends. Two NEGATIVE
CONTROLS must still be running, so A1-A3 cannot pass by the reaper killing
everything the session ever started:

  A4  the child of a test that PASSED;
  A5  a child a fixture started during setup, before the call that timed out.

Every child records its own pid and start tick, so the check reads the
process the subject started, not a recycled pid; a missing record fails the
test as a probe that could not look. Whatever is still running afterwards is
killed here.
"""

from __future__ import annotations

import ast
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

TESTS = Path(__file__).resolve().parent
PLUGIN = "_timeout_reap"
REAPED = ("popen-child", "run-grandchild", "scrubbed-child")
KEPT = ("passing-child", "fixture-child")

SUBJECT = '''
import os, subprocess, sys, time
from pathlib import Path

import pytest

OUT = Path(os.environ["H404_OUT"])
CHILD = (
    "import os, sys, time\\n"
    "rest = open('/proc/self/stat', 'rb').read().rsplit(b')', 1)[1].split()\\n"
    "open(sys.argv[1] + '.tmp', 'w').write(f'{os.getpid()} {int(rest[19])}')\\n"
    "os.rename(sys.argv[1] + '.tmp', sys.argv[1])\\n"
    "time.sleep(300)\\n"
)


def child(name):
    return [sys.executable, "-c", CHILD, str(OUT / name)]


def recorded(name):
    deadline = time.monotonic() + 3
    while not (OUT / name).exists() and time.monotonic() < deadline:
        time.sleep(0.02)


@pytest.fixture
def fixture_child():
    subprocess.Popen(child("fixture-child"))  # no wait: the call may start in the same tick


def test_popen_child():
    subprocess.Popen(child("popen-child"))
    recorded("popen-child")
    time.sleep(60)


def test_run_grandchild():
    subprocess.run(["sh", "-c", '"$@" & wait', "sh"] + child("run-grandchild"))


def test_scrubbed_env_child():
    subprocess.Popen(child("scrubbed-child"), env={"PATH": os.environ.get("PATH", "")})
    recorded("scrubbed-child")
    time.sleep(60)


def test_passing_child():
    subprocess.Popen(child("passing-child"))
    recorded("passing-child")


def test_fixture_child_started_before_the_call(fixture_child):
    recorded("fixture-child")
    time.sleep(60)
'''


def _registered():
    """The plugin names tests/conftest.py hands pytest, read the way pytest reads them."""
    tree = ast.parse((TESTS / "conftest.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "pytest_plugins" for t in node.targets):
            return tuple(ast.literal_eval(node.value))
    return ()


def _alive(pid, start):
    try:
        rest = Path(f"/proc/{pid}/stat").read_bytes().rsplit(b")", 1)[1].split()
    except OSError:
        return False
    return int(rest[19]) == start and rest[0] not in (b"Z", b"X")


def _records(out):
    records = {}
    for name in REAPED + KEPT:
        path = out / name
        if path.exists():
            pid, start = path.read_text().split()
            records[name] = (int(pid), int(start))
    return records


def _inner_session(tmp_path):
    """Run the subject; return the reaped children still alive, the kept ones dead, and the log."""
    pytest.importorskip("pytest_timeout")
    out = tmp_path / "out"
    out.mkdir()
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_subject.py").write_text(SUBJECT)
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    env.update(H404_OUT=str(out), PYTHONDONTWRITEBYTECODE="1", LC_ALL="C",  # row 178
               PYTHONPATH=os.pathsep.join(filter(None, [str(TESTS), env.get("PYTHONPATH")])))
    plugin = ["-p", PLUGIN] if PLUGIN in _registered() else []
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist",
           "--timeout=4", "--timeout-method=signal", *plugin, "test_subject.py"]
    records = {}
    try:
        run = subprocess.run(cmd, cwd=tmp_path, env=env, capture_output=True, text=True,
                             timeout=150)
        log = f"{cmd}\nrc={run.returncode}\n{run.stdout[-6000:]}\n{run.stderr[-2000:]}"
        records = _records(out)
        assert "4 failed, 1 passed" in run.stdout, "the inner session did not time out 4 tests:\n" + log
        assert set(records) == set(REAPED + KEPT), (
            f"COULD NOT LOOK: children missing a record: {sorted(set(REAPED + KEPT) - set(records))}\n" + log)
        alive = [f"{name} pid {records[name][0]}" for name in REAPED if _alive(*records[name])]
        killed = [f"{name} pid {records[name][0]}" for name in KEPT if not _alive(*records[name])]
        return alive, killed, log
    finally:
        for pid, start in (records or _records(out)).values():
            if _alive(pid, start):
                os.kill(pid, signal.SIGKILL)


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="needs /proc")
def test_h404_a_timed_out_test_leaves_no_process_running(tmp_path):
    alive, killed, log = _inner_session(tmp_path)
    assert not alive, f"CHILD ALIVE AFTER THE ALARM: {alive}\n{log}"
    assert not killed, f"the reaper killed a process the timed-out call did not start: {killed}\n{log}"
