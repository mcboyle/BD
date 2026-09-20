"""Tests for Row 885: Automatic untracked file and leaked FD cleanup trap.

Verifies:
1. Registered cleanup trap closes open test file descriptors and sockets on exit.
2. Temporary test directories in /tmp or workspace are unlinked cleanly.
3. Working tree remains porcelain clean after purge of test-created artifacts.
4. Signal and atexit trap lifecycle handling under process termination.
"""
from __future__ import annotations

import atexit
import errno
import os
import pathlib
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Gate scope marker: strictly 'module' or 'repo-wide'
BD_GATE_SCOPE = "module"

import conftest


def _trap_api():
    """Resolve the row-885 trap at call time: on a base without it every test
    FAILS at its own assertion instead of the module erroring at collection
    (rule 44 / lens remedy) -- the RED replay stays behavioural."""
    missing = [n for n in ("CLEANUP_TRAP", "ProcessExitCleanupTrap", "get_cleanup_trap") if not hasattr(conftest, n)]
    assert not missing, f"row 885 cleanup trap not present in tests/conftest.py: {missing}"
    return conftest.CLEANUP_TRAP, conftest.ProcessExitCleanupTrap, conftest.get_cleanup_trap


def test_cleanup_trap_closes_open_fds_and_sockets_on_exit():
    """Verify registered cleanup trap closes open test file descriptors and sockets."""
    trap = _trap_api()[1]()

    # Create temporary file and get an open OS file descriptor
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name
        tmp.write(b"row885 test data")
    
    fd = os.open(tmp_path, os.O_RDONLY)
    # Confirm descriptor is open
    assert os.fstat(fd).st_size > 0

    # Create a test socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    assert sock.fileno() > 0

    # Track both resources
    trap.track_fd(fd)
    trap.track_socket(sock)
    trap.track_temp_path(tmp_path)

    # Execute cleanup
    summary = trap.run_cleanup()

    # Verify fd is closed (os.fstat raises OSError EBADF)
    with pytest.raises(OSError) as excinfo:
        os.fstat(fd)
    assert excinfo.value.errno in (errno.EBADF, 9)

    # Verify socket is closed (fileno is -1)
    assert sock.fileno() == -1

    # Verify temp file was removed
    assert not os.path.exists(tmp_path)

    assert fd in summary["closed_fds"]
    assert sock in summary["closed_sockets"]


def test_cleanup_trap_unlinks_temporary_directories():
    """Verify temporary test directories and nested contents are unlinked cleanly."""
    trap = _trap_api()[1]()

    temp_dir = tempfile.mkdtemp(prefix="test_row885_cleanup_dir_")
    sub_dir = os.path.join(temp_dir, "nested_sub")
    os.makedirs(sub_dir, exist_ok=True)
    file_path = os.path.join(sub_dir, "artifact.bin")
    with open(file_path, "wb") as f:
        f.write(b"temporary artifact bytes" * 100)

    assert os.path.isdir(temp_dir)
    assert os.path.isfile(file_path)

    trap.track_temp_path(temp_dir)
    summary = trap.run_cleanup()

    assert not os.path.exists(temp_dir)
    assert not os.path.exists(file_path)
    assert Path(temp_dir) in summary["purged_temp_paths"]


def test_cleanup_trap_preserves_working_tree_porcelain_clean():
    """Verify test-created artifacts in repository working tree are purged so git status is clean."""
    repo_root = Path(__file__).resolve().parent.parent
    trap = _trap_api()[1](repo_root=repo_root)

    # Create an untracked temporary test artifact in repo root
    untracked_rel = "tmp_test_row885_artifact.dat"
    untracked_file = repo_root / untracked_rel
    untracked_file.write_text("temporary untracked file generated during test", encoding="utf-8")

    try:
        # Check that git status sees untracked file
        status_before = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain", "--", untracked_rel],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert untracked_rel in status_before

        # Track and purge artifact
        trap.track_artifact(untracked_file)
        summary = trap.run_cleanup()

        # Check that file is deleted and git status is porcelain clean
        assert not untracked_file.exists()
        assert untracked_file in summary["purged_artifacts"]

        status_after = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain", "--", untracked_rel],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert status_after == ""
    finally:
        if untracked_file.exists():
            untracked_file.unlink()


def test_cleanup_trap_subprocess_exit_and_sigterm_handling():
    """Verify cleanup trap executes automatically on process exit and SIGTERM."""
    repo_root = str(Path(__file__).resolve().parent.parent)
    script = """
import os, sys, tempfile, socket, signal
repo_root = sys.argv[1]
tmp_file = sys.argv[2]
tmp_dir = sys.argv[3]
pid_file = sys.argv[4]

sys.path.insert(0, repo_root)
sys.path.insert(0, os.path.join(repo_root, "tests"))
from conftest import ProcessExitCleanupTrap

trap = ProcessExitCleanupTrap()
trap.install()

# Create and track FD
fd = os.open(tmp_file, os.O_RDWR | os.O_CREAT)
trap.track_fd(fd)

# Create and track socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.bind(('127.0.0.1', 0))
s.listen(1)
trap.track_socket(s)

# Track directory
trap.track_temp_path(tmp_dir)

# Notify parent that setup is complete: the pid file for the record, and a
# READY line on stdout the parent blocks on (no polling, no sleep).
with open(pid_file, 'w') as f:
    f.write(str(os.getpid()))
sys.stdout.write("READY\\n")
sys.stdout.flush()

# Wait for signal or exit
if len(sys.argv) > 5 and sys.argv[5] == 'sigterm':
    signal.pause()
else:
    sys.exit(0)
"""
    with tempfile.TemporaryDirectory() as base_tmp:
        tmp_file = os.path.join(base_tmp, "sub_file.tmp")
        tmp_dir = os.path.join(base_tmp, "sub_dir")
        os.makedirs(tmp_dir, exist_ok=True)
        pid_file = os.path.join(base_tmp, "child.pid")

        # Subtest 1: Normal process exit runs atexit hook
        proc = subprocess.run(
            [sys.executable, "-c", script, repo_root, tmp_file, tmp_dir, pid_file],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, (proc.returncode, proc.stderr[-1500:])
        # Temp dir was purged by cleanup trap
        assert not os.path.exists(tmp_dir)

        # Subtest 2: SIGTERM invokes cleanup trap
        os.makedirs(tmp_dir, exist_ok=True)
        if os.path.exists(pid_file):
            os.unlink(pid_file)

        proc2 = subprocess.Popen(
            [sys.executable, "-c", script, repo_root, tmp_file, tmp_dir, pid_file, "sigterm"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Block until the child reports its trap is installed (a readline on the
        # pipe, bounded by proc2.wait's timeout below rather than a sleep loop).
        ready = proc2.stdout.readline()
        assert ready == b"READY\n", (ready, proc2.stderr.read())
        assert os.path.getsize(pid_file) > 0

        # Send SIGTERM
        proc2.send_signal(signal.SIGTERM)
        proc2.wait(timeout=5)

        # Confirm cleanup executed on SIGTERM
        assert not os.path.exists(tmp_dir)


def test_negative_control_resources_remain_without_cleanup_trap():
    """Negative control: prove that without the cleanup trap, resources remain dangling."""
    temp_dir = tempfile.mkdtemp(prefix="test_row885_negative_control_")
    try:
        assert os.path.exists(temp_dir)
        # Without running trap.run_cleanup(), directory still exists
        assert os.path.isdir(temp_dir)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_conftest_global_cleanup_trap_helpers():
    """Verify conftest exports global cleanup trap instance and helper functions."""
    _singleton, _cls, _get = _trap_api()
    trap = _get()
    assert trap is _singleton
    assert isinstance(trap, _cls)

    # Test helper dispatch
    with tempfile.NamedTemporaryFile(delete=False) as f:
        tmp_p = f.name
    
    assert hasattr(conftest, "track_test_temp_path"), "row 885 helper track_test_temp_path missing from tests/conftest.py"
    p = conftest.track_test_temp_path(tmp_p)
    assert Path(tmp_p) in trap.tracked_temp_paths
    assert p == Path(tmp_p)

    trap.tracked_temp_paths.discard(Path(tmp_p))
    if os.path.exists(tmp_p):
        os.unlink(tmp_p)
