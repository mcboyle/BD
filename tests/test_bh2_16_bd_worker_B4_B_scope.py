"""BH2-16: integrator seat names the launcher really issues must pass T16 / the O1473 ff-only merge.

bd-launch-role.sh names an integrator bd-integrator[-<lane>][-<pool>] (bd-integrator-A, -B2, -R-B, -S2-B, -c-B).
The live regex ^bd-integrator(-[A-Z])?$ refused every lane+pool name, so those integrators were blocked from
their own UniversalSwarmOS lane (findings/BH2-tripwire-bd-agy-audit-2.md#protected_roots).
Candidate dir opt-in: BD_BH2_16_CANDIDATE=<dir holding bd-tripwire-any.py and bd-checkout-guard-hook.py>.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_BH2_16_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

USOS = "/home/mboyle/UniversalSwarmOS/bh2-16-probe.txt"
MAIN_FILE = "/home/mboyle/BulkDownloader/bh2-16-probe.txt"
MERGE = "git -C /home/mboyle/BulkDownloader merge --ff-only origin/main"
T16 = "T16 write into the MAIN CHECKOUT or UniversalSwarmOS from a non-integrator seat"

LAUNCHER_NAMES = [
    "bd-integrator",
    "bd-integrator-A",
    "bd-integrator-B2",
    "bd-integrator-R-B",
    "bd-integrator-S2-B",
    "bd-integrator-c-B",
    "bd-integrator-A2u5-A",
    "bd-cx-integrator",
]
NOT_INTEGRATORS = [
    "bd-worker-B4-B",
    "bd-pm-C-B",
    "bd-integrator-",
    "bd-integratorX",
    "bd-integrator-1",
    "bd-integrator-R-B-x",
    "bd-integrator-R-C",
]


def _script(name: str) -> Path:
    path = Path(CANDIDATE) / name
    assert path.is_file(), f"candidate {path} missing"
    return path


def _run(
    name: str, seat: str, event: dict, tmp_path: Path
) -> subprocess.CompletedProcess:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "PWD": str(tmp_path),
        "BD_SEAT": seat,
        "BD_TRIPWIRE_LOG": str(tmp_path / "tripwire.log"),
    }
    return subprocess.run(
        [sys.executable, str(_script(name))],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=60,
        check=False,
    )


def _write(path: str) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": path, "content": "x"}}


def _bash(cmd: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


@pytest.mark.parametrize("seat", LAUNCHER_NAMES)
def test_integrator_may_write_its_usos_lane(seat: str, tmp_path: Path) -> None:
    r = _run("bd-tripwire-any.py", seat, _write(USOS), tmp_path)
    assert r.returncode == 0, (
        f"{seat} refused its own lane: rc={r.returncode} {r.stderr[-300:]}"
    )


@pytest.mark.parametrize("seat", NOT_INTEGRATORS)
def test_non_integrator_usos_write_refused(seat: str, tmp_path: Path) -> None:
    r = _run("bd-tripwire-any.py", seat, _write(USOS), tmp_path)
    assert r.returncode == 2 and T16 in r.stderr, (
        f"{seat}: rc={r.returncode} {r.stderr[-300:]}"
    )


@pytest.mark.parametrize("seat", ["bd-integrator-R-B", "bd-integrator-A"])
def test_integrator_main_checkout_write_still_refused(
    seat: str, tmp_path: Path
) -> None:
    r = _run("bd-tripwire-any.py", seat, _write(MAIN_FILE), tmp_path)
    assert r.returncode == 2 and T16 in r.stderr, (
        f"{seat}: rc={r.returncode} {r.stderr[-300:]}"
    )


@pytest.mark.parametrize("seat", LAUNCHER_NAMES)
def test_integrator_ff_only_merge_allowed(seat: str, tmp_path: Path) -> None:
    r = _run("bd-checkout-guard-hook.py", seat, _bash(MERGE), tmp_path)
    assert r.returncode == 0, (
        f"{seat} refused O1473 merge: rc={r.returncode} {r.stderr[-300:]}"
    )


@pytest.mark.parametrize("seat", NOT_INTEGRATORS)
def test_non_integrator_ff_only_merge_refused(seat: str, tmp_path: Path) -> None:
    r = _run("bd-checkout-guard-hook.py", seat, _bash(MERGE), tmp_path)
    assert r.returncode == 2 and "main checkout" in r.stderr, (
        f"{seat}: rc={r.returncode} {r.stderr[-300:]}"
    )
