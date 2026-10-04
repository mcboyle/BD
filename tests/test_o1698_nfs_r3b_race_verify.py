import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_NFS_SHARE_R3B_RACE_VERIFY_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


def executable(path, body):
    path.write_text(body)
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def fx(tmp_path):
    candidate = Path(CANDIDATE)
    assert candidate.is_absolute() and candidate.is_file() and os.access(candidate, os.X_OK)
    hosts = tmp_path / "hosts"
    hosts.write_text("vm 10.9.9.1\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture", "--allow-empty"], check=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable(bin_dir / "mountpoint", '#!/bin/sh\n[ "${TEST_REPO_ABSENT:-0}" != 1 ]\n')
    executable(bin_dir / "findmnt", '#!/bin/sh\nprintf "%s\\n" "${TEST_REPO_SERVER:-10.0.0.1}:$BD_NFS_REPO_SOURCE"\n')
    remote_bin = tmp_path / "remote-bin"
    remote_bin.mkdir()
    executable(remote_bin / "flock", '''#!/usr/bin/env python3
import fcntl, subprocess, sys
args = sys.argv[1:]
nonblock = "-n" in args
args = [arg for arg in args if arg not in ("-n", "-x")]
with open(args[0], "a+") as lock:
    try:
        fcntl.lockf(lock, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblock else 0))
    except BlockingIOError:
        sys.exit(1)
    sys.exit(subprocess.run(args[1:], check=False).returncode)
''')
    ssh = executable(tmp_path / "ssh", '''#!/bin/bash
while [ $# -gt 0 ]; do case "$1" in -n) shift ;; -o) shift 2 ;; *) break ;; esac; done
shift
cmd=$*
printf '%s\\n' "$cmd" >> "$TEST_SSH_LOG"
if [[ "$cmd" == *NFS_RACE_CONTENDER* ]] && [ "${TEST_RACE_TRANSPORT:-0}" = 1 ]; then exit 255; fi
if [[ "$cmd" == *NFS_LISTING_OBSERVER* ]] && [ "${TEST_LAG_FAIL:-0}" = 1 ]; then echo 2001; exit 0; fi
if [[ "$cmd" == *NFS_LISTING_OBSERVER* ]] && [ "${TEST_LAG_MALFORMED:-0}" = 1 ]; then echo UNKNOWN; exit 0; fi
cmd=${cmd//"/mnt/bd-repo"/"$BD_NFS_REPO_SOURCE"}
if [ "${TEST_VM_BSD:-0}" != 1 ]; then export PATH="$TEST_REMOTE_BIN:$PATH"; fi
exec bash -c "$cmd"
''')
    env = {k: v for k, v in os.environ.items() if not k.startswith(("BD_", "TEST_"))}
    env.update(PATH=f"{bin_dir}:{os.environ['PATH']}", LC_ALL="C", BD_HOSTS_FILE=str(hosts),
               BD_NFS_SERVER="10.0.0.1", BD_NFS_SEAT_VMS="vm", BD_NFS_SSH=ssh,
               BD_NFS_VERIFY_DIR=str(tmp_path / "verify"), BD_NFS_REPO_SOURCE=str(repo),
               BD_NFS_LOG=str(tmp_path / "nfs.log"), BD_NFS_HOLD="0.8", BD_NFS_REMOTE_SETTLE="0.3",
               TEST_SSH_LOG=str(tmp_path / "ssh.log"), TEST_REMOTE_BIN=str(remote_bin))
    return tmp_path, env


def verify(fx, **extra):
    root, env = fx
    result = subprocess.run([CANDIDATE, "verify", "vm"], env={**env, **extra}, check=False, capture_output=True, text=True, timeout=100)
    assert (root / "ssh.log").stat().st_size > 0
    return result


def test_race_exactly_twenty_trials_and_repo(fx):
    result = verify(fx)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "race=20/20" in result.stdout
    assert result.stdout.count("RACE trial=") == 20
    assert result.stdout.count("winners=1") == 20
    assert "listing_lag_ms=" in result.stdout and "lease_seconds=" in result.stdout and "grace_seconds=" in result.stdout
    assert "repo=/mnt/bd-repo repo_check=OK" in result.stdout
    assert "RESULT=PASS" in result.stdout
    assert "RESULT=PASS" in (fx[0] / "nfs.log").read_text()


def test_plain_control_reports_two_winners_and_fails(fx):
    result = verify(fx, BD_NFS_RACE_MODE="plain")
    assert result.returncode == 4, (result.stdout, result.stderr)
    assert result.stdout.count("winners=2") == 20
    assert "race=0/20" in result.stdout and "RESULT=FAIL" in result.stdout
    assert "RESULT=FAIL" in (fx[0] / "nfs.log").read_text()


def test_listing_lag_fails_distinctly(fx):
    result = verify(fx, TEST_LAG_FAIL="1")
    assert result.returncode == 4, (result.stdout, result.stderr)
    assert "race=20/20" in result.stdout
    assert "listing_lag_ms=2001 listing=FAIL" in result.stdout and "RESULT=FAIL" in result.stdout


def test_missing_repo_mount_fails_distinctly(fx):
    result = verify(fx, TEST_REPO_ABSENT="1")
    assert result.returncode == 4, (result.stdout, result.stderr)
    assert "repo=/mnt/bd-repo repo_check=FAIL" in result.stdout
    assert "race=20/20" in result.stdout and "RESULT=FAIL" in result.stdout


def test_remote_race_error_is_not_a_winner(fx):
    result = verify(fx, TEST_RACE_TRANSPORT="1")
    assert result.returncode == 4, (result.stdout, result.stderr)
    assert "RACE-ERROR" in result.stdout and "race=0/20" in result.stdout


def test_malformed_listing_is_unknown_failure(fx):
    result = verify(fx, TEST_LAG_MALFORMED="1")
    assert result.returncode == 4, (result.stdout, result.stderr)
    assert "listing_lag_ms=UNKNOWN listing=FAIL" in result.stdout and "RESULT=FAIL" in result.stdout


def test_wrong_repo_export_fails_distinctly(fx):
    result = verify(fx, TEST_REPO_SERVER="10.0.0.2")
    assert result.returncode == 4, (result.stdout, result.stderr)
    assert "repo=/mnt/bd-repo repo_check=FAIL" in result.stdout
    assert "race=20/20" in result.stdout


def test_hub_posix_and_vm_posix_conflict_both_ways(fx):
    result = verify(fx)
    assert "vm_blocked_while_hub_holds=OK" in result.stdout, result.stdout
    assert "hub_blocked_while_vm_holds=OK" in result.stdout, result.stdout
    assert result.returncode == 0 and "RESULT=PASS" in result.stdout


def test_mixed_bsd_vm_and_posix_hub_fails_distinctly(fx):
    result = verify(fx, TEST_VM_BSD="1")
    assert result.returncode == 4, result.stdout
    assert "vm_blocked_while_hub_holds=FAIL" in result.stdout
    assert "hub_blocked_while_vm_holds=FAIL" in result.stdout
    assert "race=20/20" in result.stdout and "RESULT=FAIL" in result.stdout


def race_code(name):
    match = re.search(r"^" + name + r"='(.*?)'\n", Path(CANDIDATE).read_text(), re.MULTILINE | re.DOTALL)
    assert match, name
    return match.group(1)


def test_stale_go_listing_and_exists_cannot_release_contender(tmp_path):
    prefix = '''import os, time
exists = os.path.exists
os.listdir = lambda path: ["go"]
os.path.exists = lambda path: True if path.endswith("/go") else exists(path)
ticks = iter([0, 16])
time.monotonic = lambda: next(ticks, 16)
time.sleep = lambda delay: None
'''
    result = subprocess.run(["python3", "-c", prefix + race_code("RACE_CONTENDER"), str(tmp_path), "vm", "exclusive"], check=False, capture_output=True, text=True, timeout=10)
    assert result.returncode == 2 and "ERROR:TimeoutError" in result.stdout, (result.stdout, result.stderr)
    assert not (tmp_path / "winner").exists()


def test_go_visible_after_six_seconds_is_within_budget(tmp_path):
    prefix = '''import os, sys, time
ticks = iter([0, 6])
time.monotonic = lambda: next(ticks, 6)
def publish(delay):
    with open(sys.argv[1] + "/go", "wb") as stamp:
        stamp.write(b"go\\n")
        stamp.flush()
        os.fsync(stamp.fileno())
time.sleep = publish
'''
    result = subprocess.run(["python3", "-c", prefix + race_code("RACE_CONTENDER"), str(tmp_path), "vm", "exclusive"], check=False, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and result.stdout.strip() == "WON", (result.stdout, result.stderr)
    assert (tmp_path / "winner").is_file()


def test_go_stamp_is_fsynced_and_contains_release_token(tmp_path):
    for side in ("hub", "vm"):
        (tmp_path / ("ready." + side)).touch()
    prefix = '''import os
fsync = os.fsync
def sync(fd):
    print("FSYNC=" + os.readlink("/proc/self/fd/" + str(fd)))
    fsync(fd)
os.fsync = sync
'''
    result = subprocess.run(["python3", "-c", prefix + race_code("RACE_START"), str(tmp_path)], check=False, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "FSYNC=" + str(tmp_path / "go") in result.stdout
    assert (tmp_path / "go").read_bytes() == b"go\n"


@pytest.mark.parametrize("suffix", ["bd-root-protected.sh", "bd-checkout-guard-hook.py"])
def test_protected_mount_and_symlink_roots(tmp_path, suffix):
    candidate = Path(CANDIDATE).parent / suffix
    assert candidate.is_file(), str(candidate)
    root = tmp_path / "home"
    root.mkdir()
    mount = tmp_path / "mounted-repo"
    mount.mkdir()
    link = root / "BulkDownloader"
    link.symlink_to(mount, target_is_directory=True)
    local_checkout = root / "local-clone"
    local_checkout.mkdir()
    harness = tmp_path / "harness"
    harness.mkdir()
    (harness / "reader.sh").write_text(f"echo {root}/bd-persist\n")
    env = {**os.environ, "BD_ROOT": str(root), "BD_HARNESS": str(harness), "BD_CHECKOUT": str(local_checkout),
           "BD_NFS_REPO_TARGET": str(mount), "BD_SEAT": "fixture", "BD_CHECKOUT_GUARD_LOG": str(tmp_path / "guard.log")}
    for path in (mount, mount / "new.txt", link, link / "new.txt"):
        if suffix.endswith(".sh"):
            result = subprocess.run(["bash", str(candidate), "check", str(path)], env=env, check=False, capture_output=True, text=True)
            assert result.returncode == 1 and "PROTECTED" in result.stdout, (path, result.stdout, result.stderr)
        else:
            event = {"tool_name": "Bash", "tool_input": {"command": "touch " + shlex.quote(str(path))}, "cwd": str(tmp_path)}
            result = subprocess.run(["python3", str(candidate)], input=json.dumps(event), env=env, check=False, capture_output=True, text=True)
            assert result.returncode == 2 and "checkout-guard" in result.stderr, (path, result.stdout, result.stderr)
    free = tmp_path / "free"
    free.mkdir()
    if suffix.endswith(".sh"):
        result = subprocess.run(["bash", str(candidate), "check", str(free)], env=env, check=False, capture_output=True, text=True)
    else:
        event = {"tool_name": "Bash", "tool_input": {"command": "touch " + shlex.quote(str(free / "ok.txt"))}}
        result = subprocess.run(["python3", str(candidate)], input=json.dumps(event), env=env, check=False, capture_output=True, text=True)
    assert result.returncode == 0, (result.stdout, result.stderr)
    if suffix.endswith(".py"):
        event = {"tool_name": "Write", "tool_input": {"file_path": str(mount / "new.txt")}}
        result = subprocess.run(["python3", str(candidate)], input=json.dumps(event), env=env, check=False, capture_output=True, text=True)
        assert result.returncode == 0 and not result.stderr, (result.stdout, result.stderr)
