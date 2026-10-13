import configparser
import os
from pathlib import Path
import shlex
import subprocess

import pytest


BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_REAP_TIMER_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")
ADAPTER = str(Path.home() / "bd-persist/plugins/bd-dispatch-ops/scripts/operations.py")
HALT = str(Path.home() / "bd-persist/state/FLEET-HALT")


def units():
    root = Path(CANDIDATE)
    result = []
    for name in ("bd-reap.service", "bd-reap.timer"):
        path = root / name
        assert path.is_file(), f"REAP-UNIT-MISSING: {path}"
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        parser.optionxform = str
        parser.read(path)
        result.append(parser)
    return result


def test_units_verify_and_schedule():
    service, timer = units()
    assert service["Service"]["Type"] == "oneshot", "REAP-NOT-ONESHOT"
    assert service["Service"]["TimeoutStartSec"] == "240", "REAP-TIMEOUT"
    assert timer["Timer"]["OnUnitActiveSec"] == "5min", "REAP-CADENCE"
    assert timer["Timer"]["OnBootSec"] == "5min", "REAP-FIRST-RUN"
    assert timer["Timer"]["Unit"] == "bd-reap.service", "REAP-TIMER-TARGET"
    assert timer["Install"]["WantedBy"] == "timers.target", "REAP-TIMER-INSTALL"
    verified = subprocess.run(
        ["systemd-analyze", "--user", "verify", str(Path(CANDIDATE) / "bd-reap.service"),
         str(Path(CANDIDATE) / "bd-reap.timer")], capture_output=True, text=True,
    )
    assert verified.returncode == 0, f"REAP-UNIT-INVALID: {verified.stderr}"


@pytest.mark.parametrize("halted,expected", [(False, 1), (True, 0)])
def test_reap_execution_respects_halt(tmp_path, halted, expected):
    service, _ = units()
    settings = service["Service"]
    assert set(settings) == {"Type", "TimeoutStartSec", "ExecCondition", "ExecStart",
                             "StandardOutput", "StandardError"}, "REAP-EXTRA-ACTION"
    start = shlex.split(settings["ExecStart"])
    assert start == ["/usr/bin/python3", ADAPTER, "reap"], "REAP-COMMAND-NOT-REAP"
    condition = shlex.split(settings["ExecCondition"])
    assert condition[:2] == ["/bin/sh", "-c"], "REAP-CONDITION-SHELL"
    assert condition[2:] == [f"! test -e {HALT}"], "REAP-HALT-GUARD"
    marker = tmp_path / "FLEET-HALT"
    if halted:
        marker.write_text("operator halted\n")
    calls = tmp_path / "calls"
    shim = tmp_path / "operations.py"
    shim.write_text(
        "import pathlib, sys\n"
        "assert sys.argv[1:] == ['reap'], 'SHIM-NOT-REAP'\n"
        f"with pathlib.Path({str(calls)!r}).open('a') as stream: stream.write('reap\\n')\n"
    )
    condition[2] = condition[2].replace(HALT, shlex.quote(str(marker)))
    check = subprocess.run(condition, capture_output=True, text=True)
    assert check.returncode == (1 if halted else 0), "REAP-CONDITION-EXIT"
    if check.returncode == 0:
        start[1] = str(shim)
        executed = subprocess.run(start, capture_output=True, text=True)
        assert executed.returncode == 0, f"REAP-SHIM-FAILED: {executed.stderr}"
    count = len(calls.read_text().splitlines()) if calls.exists() else 0
    assert count == expected, f"REAP-CALL-COUNT: expected={expected} actual={count}"
    assert marker.exists() == halted, "REAP-HALT-REMOVED"
