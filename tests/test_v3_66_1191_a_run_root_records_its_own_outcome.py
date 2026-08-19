"""v3.66.1191 -- a test root carries a durable retention decision.

The session hook cannot survive SIGKILL.  The durable marker plus a
kernel-owned lock distinguish a live run, a failed run kept for forensics,
and a process that disappeared before it could record a terminal outcome.
"""
from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parent.parent
POLICY = REPO / "project-knowledge" / "TEST_ARTIFACT_RETENTION_POLICY.md"


def _child(script: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONPATH=str(REPO / "tests"))
    env.pop("KEEP_TEST_TMPDIRS", None)
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )


def _root(stdout: str) -> Path:
    line = next(line for line in stdout.splitlines() if line.startswith("ROOT "))
    return Path(line.split(" ", 1)[1])


def test_a_root_kept_after_a_failing_run_says_why_on_disk():
    result = _child(
        "import json, pathlib, _tmproot\n"
        "root = _tmproot.install()\n"
        "removed = _tmproot.finish(1)\n"
        "print('ROOT', root)\n"
        "print('REMOVED', removed)\n"
    )
    assert result.returncode == 0, result.stderr
    root = _root(result.stdout)
    try:
        assert "REMOVED False" in result.stdout
        marker = json.loads((root / ".bd-testrun").read_text())
        assert marker["state"] == "KEPT_FOR_FORENSICS"
        assert marker["exitstatus"] == 1
    finally:
        if root.exists():
            import shutil
            shutil.rmtree(root)


def test_a_killed_run_is_distinguishable_from_a_kept_one():
    result = _child(
        "import os, signal, sys, _tmproot\n"
        "root = _tmproot.install()\n"
        "print('ROOT', root, flush=True)\n"
        "os.kill(os.getpid(), signal.SIGKILL)\n"
    )
    assert result.returncode == -signal.SIGKILL
    root = _root(result.stdout)
    try:
        marker = json.loads((root / ".bd-testrun").read_text())
        assert marker["state"] == "RUNNING"
        assert "exitstatus" not in marker
    finally:
        if root.exists():
            import shutil
            shutil.rmtree(root)


def test_a_live_run_holds_a_lock_that_sigkill_releases(tmp_path):
    ready = tmp_path / "ready"
    env = dict(os.environ, PYTHONPATH=str(REPO / "tests"), READY=str(ready))
    env.pop("KEEP_TEST_TMPDIRS", None)
    child = subprocess.Popen(
        [sys.executable, "-c",
         "import os, pathlib, time, _tmproot\n"
         "root = _tmproot.install()\n"
         "pathlib.Path(os.environ['READY']).write_text(root)\n"
         "time.sleep(30)\n"],
        cwd=REPO,
        env=env,
    )
    try:
        for _ in range(300):
            if ready.exists():
                break
            import time
            time.sleep(0.01)
        assert ready.exists(), "child never published its root"
        root = Path(ready.read_text())
        lock_fd = os.open(root / ".bd-testrun.lock", os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            child.kill()
            assert child.wait(timeout=10) == -signal.SIGKILL
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(lock_fd)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        if "root" in locals() and root.exists():
            import shutil
            shutil.rmtree(root)


def test_the_lock_is_takeable_after_a_normal_failing_exit():
    result = _child(
        "import _tmproot\n"
        "root = _tmproot.install()\n"
        "_tmproot.finish(2)\n"
        "print('ROOT', root)\n"
    )
    assert result.returncode == 0, result.stderr
    root = _root(result.stdout)
    try:
        fd = os.open(root / ".bd-testrun.lock", os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
    finally:
        if root.exists():
            import shutil
            shutil.rmtree(root)


def test_a_clean_run_leaves_no_marker_because_it_leaves_no_root():
    result = _child(
        "import pathlib, _tmproot\n"
        "root = _tmproot.install()\n"
        "removed = _tmproot.finish(0)\n"
        "print('ROOT', root)\n"
        "print('REMOVED', removed)\n"
        "print('EXISTS', pathlib.Path(root).exists())\n"
    )
    assert result.returncode == 0, result.stderr
    assert "REMOVED True" in result.stdout
    assert "EXISTS False" in result.stdout


def test_the_policy_names_every_durable_state():
    text = POLICY.read_text()
    for state in (
        "LIVE",
        "KEPT_FOR_FORENSICS",
        "RECLAIMABLE",
        "ABANDONED",
        "UNKNOWN",
    ):
        assert state in text
