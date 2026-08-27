"""``bd-claim`` claim-file transactions exclude separate processes.

Row 283 covers two distinct data-loss races in the claim registry:

* a reader can unlink a claim after observing it between truncate and write;
* two adders for one durable owner can replace rather than combine their paths.

The processes here are deliberately not threads.  ``flock`` ownership is tied
to open file descriptions, and the production shape is separate CLI processes.
The filesystem barriers make both destructive interleavings deterministic; no
probabilistic retry is used to turn a missed race into a pass.
"""
from __future__ import annotations

from collections import Counter
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


BD_GATE_SCOPE = "module"

REPO = Path(__file__).resolve().parents[1]
CLAIM = REPO / "toolchain" / "bin" / "bd-claim"
_SERIALIZED_TRACE = ["begin-A", "end-A", "begin-B", "end-B"]
_DESTRUCTIVE_TRACE = ["begin-A", "begin-B", "end-B", "end-A"]


def _probe_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if key not in {"BD_CLAIM_OWNER", "BD_INSTALL_DIR"}}
    env.update({
        "BD_DISABLE_KEEPALIVE": "1",
        "LC_ALL": "C",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return env


_RACE_WORKER = r'''
from contextlib import contextmanager
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

subject_path = Path(sys.argv[1])
repo = Path(sys.argv[2])
scenario = sys.argv[3]
role = sys.argv[4]
bypass = sys.argv[5] == "bypass"
claim_file = repo / ".git" / "bd-claims" / "shared-owner.json"
trace = repo / "trace.log"

loader = importlib.machinery.SourceFileLoader("row283_bd_claim", str(subject_path))
spec = importlib.util.spec_from_loader(loader.name, loader)
subject = importlib.util.module_from_spec(spec)
loader.exec_module(subject)

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
    def no_claims_lock(_repo):
        yield
    subject._claims_lock = no_claims_lock

if role == "A":
    real_publish = subject._publish_claim
    write_fires = 0

    def controlled_publish(path, record):
        global write_fires
        if path != claim_file:
            return real_publish(path, record)
        write_fires += 1
        mark("begin-A")
        (repo / "a-entered").touch()
        wait_for(repo / "release-a", "the parent to release publisher A")
        published = real_publish(path, record)
        mark("end-A")
        return published

    subject._publish_claim = controlled_publish
    try:
        rc = subject.add(repo, ["adder-a.py" if scenario == "adder"
                                else "mid-write.py"], "A",
                         owner="shared-owner", ttl=0)
    finally:
        subject._publish_claim = real_publish
    (repo / "a-result.json").write_text(json.dumps({
        "rc": rc,
        "write_fires": write_fires,
    }), encoding="utf-8")
else:
    (repo / "b-attempted").touch()
    wait_for(repo / "allow-b-attempt", "the parent to allow process B")

    if scenario == "reader":
        real_read_text = Path.read_text
        real_live_claims_locked = subject._live_claims_locked
        read_fires = 0

        def controlled_read(self, *args, **kwargs):
            global read_fires
            if self == claim_file:
                read_fires += 1
            return real_read_text(self, *args, **kwargs)

        def controlled_live_claims_locked(*args, **kwargs):
            mark("begin-B")
            (repo / "b-entered").touch()
            return real_live_claims_locked(*args, **kwargs)

        Path.read_text = controlled_read
        subject._live_claims_locked = controlled_live_claims_locked
        reaped = []
        try:
            live = subject.live_claims(repo, reaped=reaped)
        finally:
            Path.read_text = real_read_text
            subject._live_claims_locked = real_live_claims_locked
        mark("end-B")
        result = {
            "live": live,
            "read_fires": read_fires,
            "reaped": reaped,
        }
    else:
        real_publish = subject._publish_claim
        write_fires = 0

        def controlled_publish(path, record):
            global write_fires
            if path != claim_file:
                return real_publish(path, record)
            write_fires += 1
            mark("begin-B")
            (repo / "b-entered").touch()
            published = real_publish(path, record)
            mark("end-B")
            return published

        subject._publish_claim = controlled_publish
        try:
            rc = subject.add(repo, ["adder-b.py"], "B",
                             owner="shared-owner", ttl=0)
        finally:
            subject._publish_claim = real_publish
        result = {"rc": rc, "write_fires": write_fires}

    (repo / "b-result.json").write_text(json.dumps(result), encoding="utf-8")
    (repo / "b-finished").touch()
'''


def _wait_for_path(path: Path, timeout: float) -> bool:
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
                f"bd-claim race child {child.pid} exceeded the "
                f"{timeout:.1f}s completion deadline")
        exitcodes.append(child.returncode)
        if child.returncode != 0:
            failures.append(
                f"pid={child.pid} rc={child.returncode} "
                f"stdout={stdout[-500:]!r} stderr={stderr[-1500:]!r}")
    assert failures == [], "bd-claim race children failed: " + "; ".join(failures)
    return exitcodes


def _exercise_race(tmp_path: Path, *, scenario: str, bypass: bool) -> dict:
    assert scenario in {"reader", "adder"}
    repo = tmp_path / scenario
    (repo / ".git").mkdir(parents=True)
    children: list[subprocess.Popen[str]] = []
    mode = "bypass" if bypass else "locked"
    command = [sys.executable, "-c", _RACE_WORKER,
               str(CLAIM), str(repo), scenario]
    popen_kwargs = {
        "cwd": repo,
        "env": _probe_env(),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    try:
        first = subprocess.Popen(command + ["A", mode], **popen_kwargs)
        children.append(first)
        assert _wait_for_path(repo / "a-entered", 5.0), (
            f"process A did not reach the {scenario} write seam within 5.0s")

        claim_file = repo / ".git" / "bd-claims" / "shared-owner.json"
        precondition = {
            "claim_exists_while_a_paused": claim_file.exists(),
            "claim_bytes_while_a_paused": (
                claim_file.read_bytes().decode("utf-8")
                if claim_file.exists() else None),
        }

        second = subprocess.Popen(command + ["B", mode], **popen_kwargs)
        children.append(second)
        assert _wait_for_path(repo / "b-attempted", 5.0), (
            f"process B did not reach the {scenario} transaction within 5.0s")
        (repo / "allow-b-attempt").touch()

        b_finished_while_a_held = _wait_for_path(repo / "b-finished", 1.0)
        b_entered_while_a_held = (repo / "b-entered").exists()
        (repo / "release-a").touch()
        exitcodes = _finish_children(children, 8.0)
    finally:
        (repo / "release-a").touch(exist_ok=True)
        (repo / "allow-b-attempt").touch(exist_ok=True)
        _stop_children(children)

    trace = (repo / "trace.log").read_text(encoding="ascii").splitlines()
    claim_file = repo / ".git" / "bd-claims" / "shared-owner.json"
    final_bytes = claim_file.read_text(encoding="utf-8") if claim_file.exists() else None
    final_record = json.loads(final_bytes) if final_bytes is not None else None
    return {
        **precondition,
        "a_result": json.loads((repo / "a-result.json").read_text(encoding="utf-8")),
        "b_entered_while_a_held": b_entered_while_a_held,
        "b_finished_while_a_held": b_finished_while_a_held,
        "b_result": json.loads((repo / "b-result.json").read_text(encoding="utf-8")),
        "bypass": bypass,
        "exitcodes": exitcodes,
        "final_bytes": final_bytes,
        "final_record": final_record,
        "marker_counts": dict(sorted(Counter(trace).items())),
        "scenario": scenario,
        "trace": trace,
    }


def _assert_common_preconditions(observation: dict, scenario: str) -> None:
    assert observation["scenario"] == scenario
    assert observation["exitcodes"] == [0, 0]
    assert observation["a_result"] == {"rc": 0, "write_fires": 1}
    expected_b = ({"read_fires": 1, "reaped": [], "live": [observation["final_record"]]}
                  if scenario == "reader"
                  else {"rc": 0, "write_fires": 1})
    if scenario == "reader" and observation["b_finished_while_a_held"]:
        expected_b = {
            "live": [],
            "read_fires": 0,
            "reaped": [],
        }
    assert observation["b_result"] == expected_b
    assert observation["marker_counts"] == {
        "begin-A": 1,
        "begin-B": 1,
        "end-A": 1,
        "end-B": 1,
    }
    assert observation["claim_exists_while_a_paused"] is False
    assert observation["claim_bytes_while_a_paused"] is None


def _assert_serialized(observation: dict) -> None:
    assert observation["trace"] == _SERIALIZED_TRACE, (
        f"claim transaction interleaved: {observation['trace']!r}")
    assert observation["b_entered_while_a_held"] is False
    assert observation["b_finished_while_a_held"] is False


def _assert_reader_preserved_claim(observation: dict) -> None:
    assert observation["final_record"] is not None, (
        "concurrent reader erased the claim that writer A was still writing; "
        f"trace={observation['trace']!r} reader={observation['b_result']!r}")
    assert observation["b_result"]["live"] == [observation["final_record"]], (
        "concurrent reader crossed the registry transaction before claim "
        f"publication; trace={observation['trace']!r} "
        f"reader={observation['b_result']!r}")
    assert observation["final_record"]["paths"] == ["mid-write.py"]
    assert set(observation["final_record"]) == {
        "owner", "owner_pid", "owner_start", "expires_at", "label",
        "paths", "pid", "ppid",
    }
    assert observation["final_record"]["owner"] == "shared-owner"
    assert observation["final_record"]["label"] == "A"
    assert observation["final_record"]["expires_at"] is None
    _assert_serialized(observation)


def _assert_adders_preserved_both_paths(observation: dict) -> None:
    actual = None if observation["final_record"] is None else observation["final_record"]["paths"]
    assert actual == ["adder-a.py", "adder-b.py"], (
        "concurrent adder overwrote the other adder's path set; "
        f"final paths={actual!r} trace={observation['trace']!r}")
    assert set(observation["final_record"]) == {
        "owner", "owner_pid", "owner_start", "expires_at", "label",
        "paths", "pid", "ppid",
    }
    assert observation["final_record"]["owner"] == "shared-owner"
    assert observation["final_record"]["label"] == "B"
    assert observation["final_record"]["expires_at"] is None
    _assert_serialized(observation)


def test_concurrent_reader_cannot_erase_a_claim_mid_write(tmp_path):
    observation = _exercise_race(tmp_path, scenario="reader", bypass=False)
    assert observation["bypass"] is False
    _assert_common_preconditions(observation, "reader")
    _assert_reader_preserved_claim(observation)


def test_concurrent_adders_preserve_both_path_sets(tmp_path):
    observation = _exercise_race(tmp_path, scenario="adder", bypass=False)
    assert observation["bypass"] is False
    _assert_common_preconditions(observation, "adder")
    _assert_adders_preserved_both_paths(observation)


def test_reader_race_negative_control_interleaves_before_publish(tmp_path):
    observation = _exercise_race(tmp_path, scenario="reader", bypass=True)
    assert observation["bypass"] is True
    _assert_common_preconditions(observation, "reader")
    assert observation["trace"] == _DESTRUCTIVE_TRACE
    assert observation["b_entered_while_a_held"] is True
    assert observation["b_finished_while_a_held"] is True
    assert observation["final_record"]["paths"] == ["mid-write.py"]
    assert observation["b_result"] == {
        "live": [], "read_fires": 0, "reaped": []}
    with pytest.raises(AssertionError, match="reader crossed the registry transaction"):
        _assert_reader_preserved_claim(observation)


def test_adder_race_negative_control_interleaves_and_overwrites(tmp_path):
    observation = _exercise_race(tmp_path, scenario="adder", bypass=True)
    assert observation["bypass"] is True
    _assert_common_preconditions(observation, "adder")
    assert observation["trace"] == _DESTRUCTIVE_TRACE
    assert observation["b_entered_while_a_held"] is True
    assert observation["b_finished_while_a_held"] is True
    assert observation["final_record"]["paths"] == ["adder-a.py"]
    with pytest.raises(AssertionError, match="concurrent adder overwrote"):
        _assert_adders_preserved_both_paths(observation)


def test_transform_control_imports_bd_claim_without_judging_transactions():
    """Mutation transform control: importability alone constrains no lock."""
    loader = importlib.machinery.SourceFileLoader("row283_import_control", str(CLAIM))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    subject = importlib.util.module_from_spec(spec)
    loader.exec_module(subject)
    assert callable(subject.add)
    assert callable(subject.live_claims)
