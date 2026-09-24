"""H193 (HARNESS_BACKLOG, REOPENED O1364): a working bd-precut must be tellable from a hung one.

Base defect: main() printed its `== bd-precut` header only AFTER _derive_baseline (a git archive of
the whole tree) and without flush, so with stdout on a pipe/file nothing was visible until the
block buffer filled or the process exited; and the two git calls in the baseline derivation ran
with no timeout at all. Fix: header + `HEARTBEAT: step n/9 <name>` lines flushed as each step
starts, line-buffered stdout, and a wall on both git calls.

Probe: git is shimmed so `archive` sleeps; a healthy tool shows the header and step-1 heartbeat
while that sleep is still running. RED on 00c81775: nothing appears until archive returns.
"""
from __future__ import annotations

import importlib.machinery
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "toolchain" / "bin" / "bd-precut"
HEADER = "== bd-precut"
HEARTBEAT = "HEARTBEAT: step 1/9 baseline discovery"


def _load():
    return importlib.machinery.SourceFileLoader(
        "bd_precut_h193_under_test", str(TOOL)).load_module()


@pytest.fixture()
def slow_archive_git(tmp_path, monkeypatch):
    """PATH-first git whose `archive` sleeps FAKE_GIT_SLEEP seconds, then execs the real git."""
    real = shutil.which("git")
    assert real, "real git needed for the shim"
    shim = tmp_path / "bin" / "git"
    shim.parent.mkdir()
    shim.write_text("#!/usr/bin/env bash\n"
                    "if [ \"${3:-}\" = archive ]; then sleep \"${FAKE_GIT_SLEEP:-30}\"; fi\n"
                    f"exec {real} \"$@\"\n")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim.parent}{os.pathsep}{os.environ['PATH']}")
    return shim


def _wait_for(path: Path, needle: str, proc, deadline_s: float) -> bool:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if needle in path.read_text(errors="replace"):
            return True
        if proc.poll() is not None:
            return False
        time.sleep(0.1)
    return False


def test_header_and_heartbeat_visible_while_archive_still_running(tmp_path, slow_archive_git):
    out = tmp_path / "stdout"
    env = dict(os.environ, FAKE_GIT_SLEEP="30")
    with out.open("wb") as fh:
        proc = subprocess.Popen(
            [sys.executable, str(TOOL), "--root", str(REPO), "--no-insync", "--no-envscan",
             "--no-coretest"], stdout=fh, stderr=subprocess.STDOUT, env=env)
    try:
        header = _wait_for(out, HEADER, proc, 8.0)
        beat = _wait_for(out, HEARTBEAT, proc, 2.0)
        alive = proc.poll() is None
    finally:
        proc.kill()
        proc.wait()
    text = out.read_text(errors="replace")
    assert header and alive, (
        f"H193: `{HEADER}` not visible within 8s while git archive was still sleeping "
        f"(alive={alive}); output so far:\n{text[:800]}")
    assert beat, f"H193: `{HEARTBEAT}` not visible while working; output so far:\n{text[:800]}"


def test_positive_control_shim_actually_delays_archive(tmp_path, slow_archive_git):
    """The probe can say YES: with the sleep at 0 the header appears and the shim ran git."""
    env = dict(os.environ, FAKE_GIT_SLEEP="0")
    t0 = time.monotonic()
    r = subprocess.run(["git", "-C", str(REPO), "archive", "--format=zip", "-o",
                        str(tmp_path / "b.zip"), "HEAD", "--", "README.md"],
                       env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "b.zip").stat().st_size > 0
    env["FAKE_GIT_SLEEP"] = "2"
    t0 = time.monotonic()
    subprocess.run(["git", "-C", str(REPO), "archive", "--format=zip", "-o",
                    str(tmp_path / "c.zip"), "HEAD", "--", "README.md"],
                   env=env, capture_output=True, text=True, timeout=60)
    assert time.monotonic() - t0 >= 2.0, "shim did not delay archive: probe cannot say YES"


def test_derive_baseline_git_calls_carry_a_wall(tmp_path, slow_archive_git, monkeypatch):
    mod = _load()
    monkeypatch.setenv("FAKE_GIT_SLEEP", "5")
    monkeypatch.setattr(mod, "_BASELINE_GIT_TIMEOUT_S", 1)
    t0 = time.monotonic()
    path, reason = mod._derive_baseline(str(REPO), str(tmp_path))
    took = time.monotonic() - t0
    assert path is None
    assert "timed out after 1" in (reason or ""), reason
    assert took < 4.0, f"wall not honoured: _derive_baseline took {took:.1f}s"
