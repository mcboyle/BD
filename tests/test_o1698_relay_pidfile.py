import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_RELAY_PIDFILE_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")
BASE_SHA256 = "5873dd52db9d8f5c870af1fc0a420485b568697474e0c61cf0066cb29f44a6db"
STUB = """class RelayDaemon:
    def __init__(self, **kw):
        self.batch_size, self.flush_interval = kw["batch_size"], kw["flush_interval"]
    def enqueue(self, **kw):
        return {"status": "queued", "target": kw["target"], "pending_count": 1, "message_id": 7}
    def get_status(self, target=None):
        return {"db_path": "stub", "batch_size": 5, "flush_interval": 120.0}
"""
# Output shapes of the real systemctl (is-active: one word, rc 0 only when active; show --value: bare MainPID).
SHIM = """#!/bin/sh
echo "$* XDG=${XDG_RUNTIME_DIR:-}" >> "$SHIM_LOG"
case "$2" in
  is-active) echo "$SHIM_STATE"; [ "$SHIM_STATE" = active ] ;;
  show) echo "$SHIM_MAINPID" ;;
esac
"""


def _dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", ""])
    proc.wait()
    return proc.pid


@pytest.fixture
def scripts():
    candidate = Path(CANDIDATE)
    assert candidate.is_file() and os.access(candidate, os.X_OK), (
        "candidate absent/non-executable"
    )
    base = candidate.with_name("bd-batch-relay.pre")
    assert hashlib.sha256(base.read_bytes()).hexdigest() == BASE_SHA256, (
        "wrong harness base"
    )
    return candidate, base


@pytest.fixture
def relay(tmp_path, scripts):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "relay_daemon.py").write_text(STUB)
    (tmp_path / "bin").mkdir()
    (tmp_path / "shim").mkdir()
    (tmp_path / "shim" / "systemctl").write_text(SHIM)
    (tmp_path / "shim" / "systemctl").chmod(0o755)
    (tmp_path / "home").mkdir()
    (tmp_path / "run").mkdir()
    log = tmp_path / "systemctl.log"

    def run(*argv, state="inactive", main_pid=0, script=None, xdg=True):
        # The script resolves lib/ from its own grandparent, so the stub is the RelayDaemon it imports.
        shutil.copy(script or scripts[0], tmp_path / "bin" / "bd-batch-relay")
        env = {
            "PATH": f"{tmp_path / 'shim'}:{os.defpath}",
            "HOME": str(tmp_path / "home"),
            "SHIM_LOG": str(log),
            "SHIM_STATE": state,
            "SHIM_MAINPID": str(main_pid),
        }
        if xdg:
            env["XDG_RUNTIME_DIR"] = str(tmp_path / "run")
        return subprocess.run(
            [sys.executable, str(tmp_path / "bin" / "bd-batch-relay"), *argv],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    return run, tmp_path, log


def _enqueue(pidfile=None):
    return ["enqueue", "--target", "bd-fixture", "--message", "m"] + (
        ["--pidfile", str(pidfile)] if pidfile else []
    )


def test_pidfile_present_and_alive_enqueues(relay):
    run, tmp_path, log = relay
    pidfile = tmp_path / "relay.pid"
    pidfile.write_text(str(os.getpid()))
    result = run(*_enqueue(pidfile))
    assert (result.returncode, result.stdout) == (
        0,
        "[QUEUED] Buffered for bd-fixture (pending: 1, id: 7)\n",
    )
    assert not log.exists(), "live pidfile must not consult systemd"


def test_missing_pidfile_with_active_unit_enqueues_and_recreates(relay, scripts):
    run, tmp_path, log = relay
    pidfile = tmp_path / "relay.pid"
    base = run(
        *_enqueue(pidfile), state="active", main_pid=os.getpid(), script=scripts[1]
    )
    assert (base.returncode, base.stderr) == (1, "Daemon dead (no pidfile)\n"), (
        "RED control: base ignores systemd"
    )
    result = run(*_enqueue(pidfile), state="active", main_pid=os.getpid())
    assert result.returncode == 0, (
        "RELAY-PIDFILE-ACTIVE-UNIT-REPORTED-DEAD " + result.stderr
    )
    assert pidfile.read_text() == str(os.getpid()), (
        "pidfile not re-created from MainPID"
    )
    assert "--user is-active bd-batch-relay.service" in log.read_text()


def test_missing_pidfile_with_inactive_unit_is_dead(relay, scripts):
    run, tmp_path, _ = relay
    pidfile = tmp_path / "relay.pid"
    for script in scripts:
        result = run(*_enqueue(pidfile), script=script)
        assert (result.returncode, result.stdout, result.stderr) == (
            1,
            "",
            "Daemon dead (no pidfile)\n",
        )
    assert not pidfile.exists()


def test_stale_pid_inactive_is_dead_active_recovers(relay):
    run, tmp_path, _ = relay
    pidfile = tmp_path / "relay.pid"
    pidfile.write_text(str(_dead_pid()))
    result = run(*_enqueue(pidfile))
    assert (result.returncode, result.stderr) == (1, "Daemon dead (process missing)\n")
    result = run(*_enqueue(pidfile), state="active", main_pid=os.getpid())
    assert result.returncode == 0 and pidfile.read_text() == str(os.getpid())


def test_default_pidfile_is_xdg_runtime_dir_not_tmp(relay, scripts):
    run, tmp_path, _ = relay
    pidfile = tmp_path / "run" / "bd-batch-relay.pid"
    result = run(*_enqueue(), state="active", main_pid=os.getpid())
    assert result.returncode == 0, result.stderr
    assert pidfile.read_text() == str(os.getpid()), (
        "RELAY-PIDFILE-NOT-UNDER-XDG-RUNTIME-DIR"
    )
    assert '"/tmp/' not in scripts[0].read_text(), "liveness truth still under /tmp"


def test_xdg_unset_falls_back_to_local_state(relay):
    run, tmp_path, log = relay
    pidfile = (
        tmp_path / "home" / ".local" / "state" / "bd-batch-relay" / "bd-batch-relay.pid"
    )
    result = run(*_enqueue(), state="active", main_pid=os.getpid(), xdg=False)
    assert result.returncode == 0, result.stderr
    assert pidfile.read_text() == str(os.getpid()), "RELAY-PIDFILE-FALLBACK-NOT-USED"
    assert f"XDG=/run/user/{os.getuid()}" in log.read_text(), (
        "systemctl --user cannot reach the bus from cron"
    )


def test_status_reports_active_unit_running(relay):
    run, tmp_path, _ = relay
    result = run(
        "status",
        "--json",
        "--pidfile",
        str(tmp_path / "relay.pid"),
        state="active",
        main_pid=os.getpid(),
    )
    assert result.returncode == 0, result.stderr
    status = json.loads(result.stdout)
    assert (status["daemon_running"], status["daemon_pid"]) == (True, os.getpid())
