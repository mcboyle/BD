"""The replication lifecycle lock must exclude processes and never re-enter.

Row 261's original band drove only an uncontended start and stop.  These tests
exercise the two properties that an uncontended call cannot establish:

* two independent Python processes cannot overlap lifecycle transactions; and
* every direct and HTTP start/stop entry point acquires at depth exactly one.

All coordination is confined to ``tmp_path``.  Every child-side rendezvous and
every parent-side process wait has an explicit deadline.
"""
from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


BD_GATE_SCOPE = "module"

_SERIALIZED_TRACE = ["begin-A", "end-A", "begin-B", "end-B"]
_BYPASSED_TRACE = ["begin-A", "begin-B", "end-B", "end-A"]
_ENTRY_POINTS = [
    "db_replication.start_replication",
    "db_replication.stop_replication",
    "POST /api/replication/start",
    "POST /api/replication/stop",
]


def _subject_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _probe_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if key != "BD_INSTALL_DIR"}
    env.update({
        "BD_DISABLE_KEEPALIVE": "1",
        "LC_ALL": "C",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(_subject_root()),
    })
    return env


_CONTENTION_WORKER = r'''
from contextlib import contextmanager
import os
from pathlib import Path
import sys
import time

from bulk_downloader import db_replication as replication

role = sys.argv[1]
bypass = sys.argv[2] == "bypass"
base = Path(".")
trace = Path("trace.log")

def wait_for(path, description):
    deadline = time.monotonic() + 10.0
    while not path.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("timed out waiting for " + description)
        time.sleep(0.01)

def mark(value):
    fd = os.open(trace, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    try:
        os.write(fd, (value + "\n").encode("ascii"))
    finally:
        os.close(fd)

if bypass:
    @contextmanager
    def no_lifecycle_lock(_base_dir=None):
        yield
    replication._lifecycle_lock = no_lifecycle_lock

if role == "A":
    def operation():
        mark("begin-A")
        Path("a-entered").touch()
        wait_for(Path("release-a"), "the parent to release A")
        mark("end-A")
        return {"ok": True, "role": "A"}
else:
    Path("b-attempted").touch()
    wait_for(Path("allow-b-attempt"), "the parent to allow B's acquisition")
    def operation():
        mark("begin-B")
        mark("end-B")
        Path("b-finished").touch()
        return {"ok": True, "role": "B"}

result = replication._run_lifecycle_locked(base, role, operation)
expected = {"ok": True, "role": role}
if result != expected:
    raise RuntimeError("unexpected lifecycle result: %r" % (result,))
'''


def _wait_for_path(path: Path, description: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)
    return True


def _stop_children(children: list[subprocess.Popen[str]]) -> None:
    for child in children:
        if child.poll() is None:
            child.kill()
    for child in children:
        try:
            child.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            child.kill()


def _finish_children(children: list[subprocess.Popen[str]], timeout: float) -> list[int]:
    deadline = time.monotonic() + timeout
    failures = []
    exitcodes = []
    for child in children:
        remaining = max(0.01, deadline - time.monotonic())
        try:
            stdout, stderr = child.communicate(timeout=remaining)
        except subprocess.TimeoutExpired:
            _stop_children(children)
            pytest.fail(
                f"lifecycle contention child {child.pid} exceeded the "
                f"{timeout:.1f}s completion deadline")
        exitcodes.append(child.returncode)
        if child.returncode != 0:
            failures.append(
                f"pid={child.pid} rc={child.returncode} "
                f"stdout={stdout[-500:]!r} stderr={stderr[-1000:]!r}")
    assert failures == [], "lifecycle contention children failed: " + "; ".join(failures)
    return exitcodes


def _exercise_contention(tmp_path: Path, *, bypass: bool) -> dict:
    children: list[subprocess.Popen[str]] = []
    mode = "bypass" if bypass else "locked"
    command = [sys.executable, "-c", _CONTENTION_WORKER]
    popen_kwargs = {
        "cwd": tmp_path,
        "env": _probe_env(),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    try:
        first = subprocess.Popen(command + ["A", mode], **popen_kwargs)
        children.append(first)
        assert _wait_for_path(tmp_path / "a-entered", "A to enter", 5.0), (
            "process A did not enter the lifecycle transaction within 5.0s")

        second = subprocess.Popen(command + ["B", mode], **popen_kwargs)
        children.append(second)
        assert _wait_for_path(tmp_path / "b-attempted", "B to attempt", 5.0), (
            "process B did not reach its lifecycle acquisition within 5.0s")
        (tmp_path / "allow-b-attempt").touch()

        # B has completed imports and is waiting one filesystem poll away from
        # the acquisition.  With the real lock it cannot finish until A exits;
        # with the no-op control it deterministically writes both markers first.
        b_finished_while_a_held = _wait_for_path(
            tmp_path / "b-finished", "B to finish while A is held", 1.0)
        (tmp_path / "release-a").touch()
        exitcodes = _finish_children(children, 8.0)
    finally:
        (tmp_path / "release-a").touch(exist_ok=True)
        (tmp_path / "allow-b-attempt").touch(exist_ok=True)
        _stop_children(children)

    trace = (tmp_path / "trace.log").read_text(encoding="ascii").splitlines()
    return {
        "b_finished_while_a_held": b_finished_while_a_held,
        "bypass": bypass,
        "exitcodes": exitcodes,
        "marker_counts": dict(sorted(Counter(trace).items())),
        "trace": trace,
    }


def _assert_lifecycle_serialized(observation: dict) -> None:
    assert observation["trace"] == _SERIALIZED_TRACE, (
        f"lifecycle transaction interleaved: {observation['trace']!r}")
    assert observation["marker_counts"] == {
        "begin-A": 1,
        "begin-B": 1,
        "end-A": 1,
        "end-B": 1,
    }
    assert observation["b_finished_while_a_held"] is False, (
        "process B completed its transaction while process A still held the lock")
    assert observation["exitcodes"] == [0, 0]


def test_lifecycle_lock_serializes_two_real_processes(tmp_path):
    observation = _exercise_contention(tmp_path, bypass=False)
    assert observation["bypass"] is False
    _assert_lifecycle_serialized(observation)


def test_lifecycle_contention_negative_control_interleaves_without_lock(tmp_path):
    """The positive test's ordering assertion must reject an absent lock."""
    observation = _exercise_contention(tmp_path, bypass=True)
    assert observation == {
        "b_finished_while_a_held": True,
        "bypass": True,
        "exitcodes": [0, 0],
        "marker_counts": {
            "begin-A": 1,
            "begin-B": 1,
            "end-A": 1,
            "end-B": 1,
        },
        "trace": _BYPASSED_TRACE,
    }
    with pytest.raises(AssertionError, match="lifecycle transaction interleaved"):
        _assert_lifecycle_serialized(observation)


_REENTRANCY_PROBE = r'''
from collections import Counter
from contextlib import contextmanager
import inspect
import json
from pathlib import Path

from flask import Flask
from bulk_downloader import app_replication
from bulk_downloader import db_replication as replication

base = Path(".")
(base / "app_config.json").write_text(
    json.dumps({"replication": {"enabled": True}}), encoding="utf-8")
(base / "queue.db").touch()

real_lock = replication._lifecycle_lock
current_entry = None
depth = 0
maximum_depth = 0
events = []
spawns = []
terminations = []
results = []

@contextmanager
def recording_lock(base_dir=None):
    global depth, maximum_depth
    depth += 1
    maximum_depth = max(maximum_depth, depth)
    stack = [frame.function for frame in inspect.stack()]
    events.append({"depth": depth, "entry": current_entry, "stack": stack})
    try:
        if depth > 1:
            raise RuntimeError(
                "re-entrant lifecycle lock acquisition: " + " <- ".join(stack))
        with real_lock(base_dir):
            yield
    finally:
        depth -= 1

class FakeProcess:
    def __init__(self, pid):
        self.pid = pid

    def terminate(self):
        raise AssertionError("a successfully identified fake process was terminated")

def fake_popen(*_args, **_kwargs):
    pid = 51001 + len(spawns)
    spawns.append(pid)
    return FakeProcess(pid)

def terminate_owned(row):
    terminations.append(dict(row))
    return True

def drive(entry, operation):
    global current_entry
    current_entry = entry
    try:
        value = operation()
        if isinstance(value, dict):
            status = None
            body = value
        else:
            status = value.status_code
            body = value.get_json()
        results.append({
            "entry": entry,
            "ok": body.get("ok"),
            "status": status,
            "stopped": body.get("stopped"),
        })
    except Exception as error:
        results.append({
            "entry": entry,
            "exception": type(error).__name__ + ": " + str(error),
        })
    finally:
        current_entry = None

replication._lifecycle_lock = recording_lock
replication._install_root = lambda: str(base)
replication.litestream_available = lambda: True
replication.subprocess.Popen = fake_popen
replication._proc_start = lambda pid: "start-%d" % pid
replication._terminate_owned = terminate_owned

drive("db_replication.start_replication",
      lambda: replication.start_replication(base_dir=base))
drive("db_replication.stop_replication",
      lambda: replication.stop_replication(base_dir=base))

app_replication._check_csrf = lambda *_args, **_kwargs: None
app = Flask("row261-lifecycle-probe")
app_replication.register_routes(app)
client = app.test_client()
drive("POST /api/replication/start",
      lambda: client.post("/api/replication/start", json={}))
drive("POST /api/replication/stop",
      lambda: client.post("/api/replication/stop", json={}))

print(json.dumps({
    "counts": dict(Counter(event["entry"] for event in events)),
    "depth_after": depth,
    "events": events,
    "maximum_depth": maximum_depth,
    "results": results,
    "spawns": spawns,
    "terminated_pids": [row["pid"] for row in terminations],
}, sort_keys=True))
'''


def _run_json_probe(probe: str, tmp_path: Path, timeout: float) -> dict:
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env=_probe_env(),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert completed.returncode == 0, (
        f"bounded lifecycle probe failed rc={completed.returncode}: "
        f"{completed.stderr[-1500:]}")
    lines = completed.stdout.strip().splitlines()
    assert lines, "bounded lifecycle probe emitted no result"
    return json.loads(lines[-1])


def test_all_lifecycle_entry_points_acquire_at_depth_exactly_one(tmp_path):
    observation = _run_json_probe(_REENTRANCY_PROBE, tmp_path, timeout=15.0)

    assert observation["counts"] == {entry: 1 for entry in _ENTRY_POINTS}
    assert observation["maximum_depth"] == 1, observation["events"]
    assert observation["depth_after"] == 0
    assert [event["entry"] for event in observation["events"]] == _ENTRY_POINTS
    assert [event["depth"] for event in observation["events"]] == [1, 1, 1, 1]

    expected_verbs = ["start_replication", "stop_replication"] * 2
    for event, verb in zip(observation["events"], expected_verbs):
        assert event["stack"][:4] == [
            "recording_lock", "__enter__", "_run_lifecycle_locked", verb]
    assert "api_replication_start" in observation["events"][2]["stack"]
    assert "api_replication_stop" in observation["events"][3]["stack"]

    assert observation["results"] == [
        {"entry": "db_replication.start_replication", "ok": True,
         "status": None, "stopped": None},
        {"entry": "db_replication.stop_replication", "ok": True,
         "status": None, "stopped": True},
        {"entry": "POST /api/replication/start", "ok": True,
         "status": 200, "stopped": None},
        {"entry": "POST /api/replication/stop", "ok": True,
         "status": 200, "stopped": True},
    ]
    assert observation["spawns"] == [51001, 51002]
    assert observation["terminated_pids"] == [51001, 51002]
