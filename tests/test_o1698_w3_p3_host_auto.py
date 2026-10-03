"""O1698 W3 P3: `operations.py launch ... --host auto` (bd-dispatch-ops plugin, harness candidate under FIX/).

The candidate's own plugin checks (tests/checks.py --host-placement) carry the fixtures; this runs them against the
candidate operations.py in a scratch plugin tree and pins the exact check population, so a dropped check fails too.
"""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_W3_P3_HOST_AUTO_CANDIDATE", "")
CHECKS = os.environ.get("BD_O1698_W3_P3_HOST_AUTO_CHECKS", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

AUTO = [
    "auto RED no P3-HOSTS refused NO-AUTO-HOST",
    "auto empty P3-HOSTS refused NO-AUTO-HOST",
    "auto malformed P3-HOSTS refused",
    "auto RED one ACTIVE .195 esxi02 launches --host .195, INTENT host=.195",
    "auto dry-run route ends --host .195",
    "auto RED esxi04 row refused ESXI04-HOST",
    "auto UNKNOWN esxi refused ESXI04-HOST",
    "auto esxi04 row dropped, next ACTIVE picked",
    "auto RED 15 live remote seats refused REMOTE-CAP",
    "auto cap counts latest row per seat, rc=0, off hub (13 live admits)",
    "auto BD_REMOTE_SEAT_CAP lowers the cap",
    "auto RED ACTIVE host NO-VERIFY refused, no hub fallback",
    "auto HOLD/DONE rows never picked",
    "auto ranks seats/cap: .195 0/4 before .183 2/4",
    "auto ranks tie by ip: .183 2/4 before .195 1/2",
    "auto full host skipped: .183 4/4 -> .195",
    "auto all hosts full refused HOST-CAP, never the hub",
]
PARITY = [
    "base parity --host <ip> dry-run",
    "base parity --host <ip> launch argv+journal",
    "base parity plain launch (no --host)",
    "base parity --host <ip> refusal",
    "auto pool codex keeps the existing refusal",
    "auto pool kimi keeps the existing refusal",
    "auto pool agy keeps the existing refusal",
    "auto pool agy-claude keeps the existing refusal",
    "auto pool grok keeps the existing refusal",
]


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    candidate = Path(CANDIDATE)
    checks = Path(CHECKS) if CHECKS else candidate.parent.parent / "tests" / "checks.py"
    assert candidate.is_file() and checks.is_file(), (candidate, checks)
    root = tmp_path_factory.mktemp("host-auto-plugin")
    (root / "scripts").mkdir()
    (root / "tests").mkdir()
    shutil.copy(candidate, root / "scripts" / "operations.py")
    shutil.copy(checks, root / "tests" / "checks.py")
    env = {k: v for k, v in os.environ.items() if not k.startswith("BD_")}
    env["LC_ALL"] = "C"
    proc = subprocess.run(
        [sys.executable, str(root / "tests" / "checks.py"), "--host-placement"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    lines = proc.stdout.splitlines()
    return {
        "rc": proc.returncode,
        "pass": {line[5:] for line in lines if line.startswith("PASS ")},
        "fail": [line for line in lines if line.startswith("FAIL ")],
        "summary": [line for line in lines if line.startswith("HOST-PLACEMENT checks")],
        "stderr": proc.stderr[-2000:],
    }


@pytest.mark.parametrize("label", AUTO)
def test_host_auto_check(results, label):
    assert label in results["pass"], (label, results["fail"], results["stderr"])


@pytest.mark.parametrize("label", PARITY)
def test_base_parity_check(results, label):
    assert label in results["pass"], (label, results["fail"], results["stderr"])


def test_whole_host_placement_population_passes(results):
    assert results["fail"] == [] and results["rc"] == 0, (
        results["fail"],
        results["stderr"],
    )
    assert results["summary"] == [
        "HOST-PLACEMENT checks pass=44 fail=0 total=44; shims only"
    ], results["summary"]
