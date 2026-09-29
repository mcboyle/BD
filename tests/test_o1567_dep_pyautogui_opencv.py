"""o1567 dep-pyautogui-opencv: the desktop-automation stack is provisioned.

The fleet VMs run pyautogui 0.9.54 + opencv-python 5.0.0.93 (installed by hand,
22:3xZ) but nothing in the tree declared them, so a fresh install lacked them.
Three layers, each measured on VM bd (10.0.70.50, Xvfb :99 -ac):

- requirements.txt must declare pyautogui and opencv-python (Pillow is already
  there; numpy stays 2.x).
- bd_system_pkgs must name python3-tk (pyautogui's MouseInfo imports tkinter)
  and python3-dev (headers for any sdist build in the stack).
- ``import pyautogui`` under DISPLAY=:99 with no ~/.Xauthority raises
  ``Xlib.error.XauthError: ~/.Xauthority: [Errno 2] No such file or directory``
  when python3-xlib 0.15's files own the shared ``Xlib`` dir (the app venv:
  pyautogui installed after pystray's python-xlib 0.33); with an EMPTY
  ~/.Xauthority it imports (Xvfb -ac needs no cookie). So the installer must
  create the file when absent -- and never truncate a real one.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parents[1]
_FRAGMENT = _REPO / "scripts" / "lib" / "system_deps.sh"
_INSTALL = _REPO / "install_linux.sh"


def _requirements() -> dict[str, Requirement]:
    reqs: dict[str, Requirement] = {}
    for raw in (_REPO / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        req = Requirement(line)
        reqs[req.name.lower()] = req
    return reqs


def _bash(script: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. "{_FRAGMENT}"; {script}'],
        capture_output=True, text=True, timeout=30,
        env={**os.environ, **env},
    )


def test_requirements_declare_pyautogui_and_opencv_in_the_fleet_ranges():
    reqs = _requirements()
    assert "pyautogui" in reqs, "o1567 RED: requirements.txt does not declare pyautogui"
    assert "opencv-python" in reqs, "o1567 RED: requirements.txt does not declare opencv-python"
    pag, cv = reqs["pyautogui"].specifier, reqs["opencv-python"].specifier
    # the fleet's installed versions are inside; the next major is not.
    assert Version("0.9.54") in pag and Version("1.0") not in pag and Version("0.9.53") not in pag
    assert Version("5.0.0.93") in cv and Version("6.0") not in cv and Version("4.12.0.88") not in cv
    # numpy stays 2.x (opencv-python 5 is built against numpy 2).
    assert Version("2.5.2") in reqs["numpy"].specifier
    assert "pillow" in reqs


def test_system_pkgs_all_names_python3_tk_and_python3_dev():
    res = _bash("bd_system_pkgs all")
    assert res.returncode == 0, res.stderr
    names = res.stdout.split()
    assert "xvfb" in names  # positive control: the probe reads the real list
    missing = {"python3-tk", "python3-dev"} - set(names)
    assert not missing, f"o1567 RED: bd_system_pkgs all lacks {sorted(missing)}"


def test_ensure_xauthority_creates_an_empty_private_file(tmp_path):
    res = subprocess.run(
        ["bash", "-c", f'. "{_FRAGMENT}"; bd_ensure_xauthority "$1"', "_", str(tmp_path)],
        capture_output=True, text=True, timeout=30,
    )
    assert res.returncode == 0, f"o1567 RED: {res.stderr.strip()}"
    xa = tmp_path / ".Xauthority"
    assert xa.is_file() and xa.stat().st_size == 0
    assert stat.S_IMODE(xa.stat().st_mode) == 0o600


def test_ensure_xauthority_never_truncates_an_existing_file(tmp_path):
    xa = tmp_path / ".Xauthority"
    xa.write_bytes(b"real-cookie-bytes")
    xa.chmod(0o640)
    res = subprocess.run(
        ["bash", "-c", f'. "{_FRAGMENT}"; bd_ensure_xauthority "$1"', "_", str(tmp_path)],
        capture_output=True, text=True, timeout=30,
    )
    assert res.returncode == 0, res.stderr
    assert xa.read_bytes() == b"real-cookie-bytes"
    assert stat.S_IMODE(xa.stat().st_mode) == 0o640


def test_ensure_xauthority_refuses_a_missing_home(tmp_path):
    ghost = tmp_path / "no-such-home"
    res = subprocess.run(
        ["bash", "-c", f'. "{_FRAGMENT}"; bd_ensure_xauthority "$1"', "_", str(ghost)],
        capture_output=True, text=True, timeout=30,
    )
    assert res.returncode != 0
    assert not ghost.exists()


def test_install_linux_calls_ensure_xauthority_for_the_run_user():
    code = [
        ln for ln in _INSTALL.read_text(encoding="utf-8").splitlines()
        if not ln.lstrip().startswith("#")
    ]
    calls = [ln for ln in code if "bd_ensure_xauthority" in ln]
    assert calls, "o1567 RED: install_linux.sh never creates ~/.Xauthority"
    assert any("bd_as_run_user" in ln for ln in calls), calls
