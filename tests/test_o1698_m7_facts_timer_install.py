"""o1698-m7-facts-timer-install: the bd-mcp-facts user-unit installer candidate.

Hermetic: fixture HOME, fixture source dir, shim `systemctl` on PATH that logs argv.
The candidate dir comes from BD_O1698_M7_FACTS_TIMER_INSTALL_CANDIDATE (harness-work/FIX/<row>).
"""
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
ROOT = os.environ.get("BD_O1698_M7_FACTS_TIMER_INSTALL_CANDIDATE", "")
pytestmark = pytest.mark.skipif(
    os.environ.get("BD_TEST_O1698_M7_FACTS_INSTALL") != "1" or not ROOT,
    reason="external harness candidate opt-in required",
)

# Byte copies of the BOARDED units (cx49 r2, lens A1); the pins below are their sha256.
UNITS = {
    "bd-mcp-facts.service": (
        b"[Unit]\nDescription=Bounded BD MCP fact snapshots\n\n[Service]\nType=oneshot\n"
        b"ExecStart=%h/bd-persist/harness/bd-mcp/bd-mcp-facts.sh\nNice=19\n"
        b"IOSchedulingClass=idle\nTimeoutStartSec=90s\n"
    ),
    "bd-mcp-facts.timer": (
        b"[Unit]\nDescription=Refresh BD MCP fact snapshots every minute\n\n[Timer]\n"
        b"OnBootSec=60s\nOnUnitActiveSec=60s\nUnit=bd-mcp-facts.service\n\n"
        b"[Install]\nWantedBy=timers.target\n"
    ),
}
PINS = {
    "bd-mcp-facts.service": "b8186b3b9fed2aab63c8f40c8baf00e87396edd4083d97a18794153a9540886a",
    "bd-mcp-facts.timer": "7ff703f669bbe6c1b33dc138eddf12dd62eedf9d1840ca1665c0e56013f6fbc3",
}

SHIM = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ['FIX_CALLS'], 'a') as out:
    out.write(json.dumps(sys.argv[1:]) + '\\n')
sys.exit(int(os.environ.get('FIX_SYSTEMCTL_RC', '0')))
"""


@pytest.fixture
def fix(tmp_path):
    home, src, bindir = tmp_path / "home", tmp_path / "src", tmp_path / "bin"
    for d in (home, src, bindir):
        d.mkdir()
    for name, data in UNITS.items():
        (src / name).write_bytes(data)
    shim = bindir / "systemctl"
    shim.write_text(SHIM)
    shim.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "HOME": str(home),
        "BD_FACTS_SRC_DIR": str(src),
        "FIX_CALLS": str(calls),
    }

    def run(*args, **extra):
        return subprocess.run(
            ["bash", str(Path(ROOT) / "install-facts-units.sh"), *args],
            env={**env, **extra}, capture_output=True, text=True, timeout=30,
        )

    def systemctl_calls():
        if not calls.exists():
            return []
        return [json.loads(line) for line in calls.read_text().splitlines()]

    return {"home": home, "src": src, "dest": home / ".config/systemd/user",
            "run": run, "calls": systemctl_calls}


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_fixture_bytes_are_the_boarded_units():
    for name, data in UNITS.items():
        assert hashlib.sha256(data).hexdigest() == PINS[name], name


def test_positive_control_installs_0644_and_reloads_exactly_once(fix):
    r = fix["run"]()
    assert r.returncode == 0, r.stderr
    for name in UNITS:
        target = fix["dest"] / name
        assert target.is_file(), f"MISSING-UNIT {name}"
        assert _sha(target) == PINS[name], f"WRONG-SHA {name}"
        assert stat.S_IMODE(target.stat().st_mode) == 0o644, f"WRONG-MODE {name}"
        assert f"SHA-BEFORE {name} absent" in r.stdout
        assert f"SHA-AFTER {name} {PINS[name]}" in r.stdout
    assert fix["calls"]() == [["--user", "daemon-reload"]], f"SYSTEMCTL-CALLS {fix['calls']()}"
    assert "enabled=no started=no" in r.stdout


@pytest.mark.parametrize("flag", ["--enable", "--now", "enable", "--start", "start", "--enable-now"])
def test_refuses_any_enable_or_start_flag(fix, flag):
    r = fix["run"](flag)
    assert r.returncode == 2, f"ENABLE-NOT-REFUSED {flag} rc={r.returncode}"
    assert "REFUSE-ENABLE" in r.stderr and flag in r.stderr
    assert not fix["dest"].exists()
    assert fix["calls"]() == []


@pytest.mark.parametrize("name", sorted(UNITS))
def test_refuses_source_unit_that_differs_from_boarded(fix, name):
    (fix["src"] / name).write_bytes(UNITS[name] + b"# drift\n")
    r = fix["run"]()
    assert r.returncode == 1, f"SHA-MISMATCH-NOT-REFUSED {name} rc={r.returncode}"
    assert f"UNIT-SHA-MISMATCH {name}" in r.stderr
    assert not fix["dest"].exists() or not any(fix["dest"].iterdir()), "PARTIAL-INSTALL"
    assert fix["calls"]() == []


def test_refuses_existing_target_that_differs_and_keeps_its_bytes(fix):
    fix["dest"].mkdir(parents=True)
    local = fix["dest"] / "bd-mcp-facts.timer"
    local.write_bytes(b"[Timer]\nOnUnitActiveSec=5s\n")
    r = fix["run"]()
    assert r.returncode == 1, f"TARGET-OVERWRITE rc={r.returncode}"
    assert "TARGET-DIFFERS bd-mcp-facts.timer" in r.stderr
    assert local.read_bytes() == b"[Timer]\nOnUnitActiveSec=5s\n"
    assert not (fix["dest"] / "bd-mcp-facts.service").exists(), "PARTIAL-INSTALL"
    assert fix["calls"]() == []


def test_rerun_is_idempotent(fix):
    first = fix["run"]()
    assert first.returncode == 0, first.stderr
    mtimes = {n: (fix["dest"] / n).stat().st_mtime_ns for n in UNITS}
    second = fix["run"]()
    assert second.returncode == 0, second.stderr
    assert "ALREADY-INSTALLED" in second.stdout
    for name in UNITS:
        assert _sha(fix["dest"] / name) == PINS[name]
        assert (fix["dest"] / name).stat().st_mtime_ns == mtimes[name], f"REWRITTEN {name}"
    assert fix["calls"]() == [["--user", "daemon-reload"]], f"SYSTEMCTL-CALLS {fix['calls']()}"


def test_daemon_reload_failure_is_named(fix):
    r = fix["run"](FIX_SYSTEMCTL_RC="1")
    assert r.returncode == 1
    assert "DAEMON-RELOAD-FAILED" in r.stderr
