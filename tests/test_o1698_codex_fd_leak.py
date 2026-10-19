"""O1698 codex-fd-leak: fd headroom tools for the shared codex app-server daemon.

Harness cut (RULING-harness-cut-repo-wt.md). BD_O1698_CODEX_FD_LEAK_CANDIDATE names the candidate directory:
  /home/mboyle/bd-persist/harness-work/FIX/o1698-codex-fd-leak
It holds bd-codex-fd-watch.sh (read-only, warns at 80 % of the soft NOFILE), bd-codex-nofile-raise.sh (prlimit raise,
DRY_RUN by default) and the patched bd-launch-codex-role.sh. Every test runs against a throwaway child process this test
starts with its own small soft limit and fd count. The live daemon is never named, read or signalled.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_O1698_CODEX_FD_LEAK_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

HOLDER = """
import os, resource, sys, time
resource.setrlimit(resource.RLIMIT_NOFILE, (int(sys.argv[1]), resource.getrlimit(resource.RLIMIT_NOFILE)[1]))
held = [os.open(os.devnull, os.O_RDONLY) for _ in range(int(sys.argv[2]))]
print("ready", flush=True)
time.sleep(120)
"""


def _tool(name: str) -> Path:
    path = Path(CANDIDATE) / name
    assert path.is_file() and os.access(path, os.X_OK), f"candidate tool absent or not executable: {path}"
    return path


@pytest.fixture
def holder():
    """A child process with soft NOFILE `soft` holding about `n` fds; killed afterwards."""
    procs = []

    def start(soft: int, n: int) -> int:
        p = subprocess.Popen([sys.executable, "-c", HOLDER, str(soft), str(n)], stdout=subprocess.PIPE, text=True)
        procs.append(p)
        assert p.stdout is not None and p.stdout.readline().strip() == "ready"
        return p.pid

    yield start
    for p in procs:
        p.kill()
        p.wait()


def _soft(pid: int) -> str:
    line = next(ln for ln in Path(f"/proc/{pid}/limits").read_text().splitlines() if ln.startswith("Max open files"))
    return line.split()[3]


def _run(tool: str, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(_tool(tool)), *args], capture_output=True, text=True, timeout=30, check=False,
                          env={**os.environ, **env})


def test_watch_warns_at_80_percent_of_the_soft_limit(holder):
    pid = holder(100, 85)
    r = _run("bd-codex-fd-watch.sh", str(pid))
    assert r.returncode == 1, r.stdout + r.stderr
    assert r.stdout.startswith(f"CODEX-FD WARN pid={pid} ") and " soft=100 " in r.stdout, r.stdout


def test_watch_ok_below_the_threshold_and_counts_the_fds(holder):
    pid = holder(100, 10)
    r = _run("bd-codex-fd-watch.sh", str(pid))
    assert r.returncode == 0, r.stdout + r.stderr
    fds = len(os.listdir(f"/proc/{pid}/fd"))
    assert r.stdout.startswith(f"CODEX-FD OK pid={pid} fds={fds} soft=100 "), r.stdout


def test_watch_threshold_is_configurable(holder):
    pid = holder(100, 10)
    r = _run("bd-codex-fd-watch.sh", str(pid), BD_CODEX_FD_WARN_PCT="5")
    assert r.returncode == 1 and "CODEX-FD WARN" in r.stdout, r.stdout


def test_watch_warns_at_exactly_the_threshold(holder):
    pid = holder(100, 10)
    pct = len(os.listdir(f"/proc/{pid}/fd"))  # soft 100: the fd count is the percentage
    r = _run("bd-codex-fd-watch.sh", str(pid), BD_CODEX_FD_WARN_PCT=str(pct))
    assert r.returncode == 1 and f" pct={pct} " in r.stdout, r.stdout
    r = _run("bd-codex-fd-watch.sh", str(pid), BD_CODEX_FD_WARN_PCT=str(pct + 1))
    assert r.returncode == 0, r.stdout


def test_watch_cannot_look_is_exit_2_not_ok():
    r = _run("bd-codex-fd-watch.sh", "999999999")
    assert r.returncode == 2 and r.stdout.startswith("CODEX-FD COULD-NOT-LOOK: pid 999999999"), r.stdout


def test_watch_with_no_daemon_found_is_exit_2(tmp_path):
    # a pgrep that finds nothing stands for "no daemon running"; the live daemon is never looked up
    (tmp_path / "pgrep").write_text("#!/bin/sh\nexit 1\n")
    (tmp_path / "pgrep").chmod(0o755)
    r = _run("bd-codex-fd-watch.sh", PATH=f"{tmp_path}:{os.environ['PATH']}")
    assert r.returncode == 2, r.stdout
    assert r.stdout.startswith("CODEX-FD COULD-NOT-LOOK: no codex app-server --remote-control process"), r.stdout


def test_raise_dry_run_by_default_changes_nothing(holder):
    pid = holder(256, 5)
    r = _run("bd-codex-nofile-raise.sh", str(pid), BD_CODEX_NOFILE="4096")
    assert r.returncode == 0 and f"DRY-RUN would run: prlimit --pid {pid} --nofile=4096:" in r.stdout, r.stdout
    assert _soft(pid) == "256"


def test_raise_live_raises_the_soft_limit_and_keeps_the_process(holder):
    pid = holder(256, 5)
    r = _run("bd-codex-nofile-raise.sh", str(pid), BD_CODEX_NOFILE="4096", DRY_RUN="0")
    assert r.returncode == 0 and f"RAISED pid={pid} soft 256 -> 4096" in r.stdout, r.stdout + r.stderr
    assert _soft(pid) == "4096"
    assert Path(f"/proc/{pid}").exists()


def test_raise_refuses_above_the_hard_limit_and_a_missing_pid(holder):
    pid = holder(256, 5)
    hard = Path(f"/proc/{pid}/limits").read_text().split("Max open files")[1].split()[1]
    if hard != "unlimited":
        r = _run("bd-codex-nofile-raise.sh", str(pid), BD_CODEX_NOFILE=str(int(hard) + 1), DRY_RUN="0")
        assert r.returncode == 3 and "REFUSE" in r.stdout, r.stdout
        assert _soft(pid) == "256"
    r = _run("bd-codex-nofile-raise.sh", "999999999", DRY_RUN="0")
    assert r.returncode == 3 and "REFUSE: pid 999999999" in r.stdout, r.stdout


def test_launcher_raises_soft_nofile_before_it_starts_the_daemon():
    launcher = (Path(CANDIDATE) / "bd-launch-codex-role.sh").read_text()
    subprocess.run(["bash", "-n", str(Path(CANDIDATE) / "bd-launch-codex-role.sh")], check=True)
    ulimit_at = launcher.find('\nulimit -Sn "${BD_CODEX_NOFILE:-65536}"')
    start_at = launcher.find('"$CODEX" remote-control start')
    assert 0 < ulimit_at < start_at, "M-CODEX-FD: launcher does not raise soft NOFILE before remote-control start"
    line = launcher[ulimit_at + 1:].splitlines()[0]
    r = subprocess.run(["prlimit", "--nofile=1024:", "bash", "-c", f"{line}; ulimit -Sn"],
                       capture_output=True, text=True, timeout=30, check=False)
    assert r.stdout.strip() == "65536", r.stdout + r.stderr
