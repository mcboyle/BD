"""o1671-a15 (AUDIT-15 MED SECURITY): toolchain/bdenv.sh put /tmp/prestaged_site_packages first on PYTHONPATH
unconditionally. /tmp is shared, so any local user could create that directory and plant modules every fleet python
sourcing bdenv.sh would import first. The sandbox-era directory is now trusted only when it is a real directory (not a
symlink), owned by the sourcing user, and not group- or other-writable. BD_PRESTAGED_SITE_PACKAGES names the directory
so this test can use a fixture instead of the real /tmp path.
"""

import os
import subprocess
from pathlib import Path

BD_GATE_SCOPE = "module"

REPO = Path(__file__).resolve().parents[1]
LEGACY = "/tmp/prestaged_site_packages"


def _pythonpath(tmp_path, prestaged):
    env = {
        **os.environ,
        "BD_ENV_NO_SERVICES": "1",
        "BD_WORK_TREE": str(REPO),
        "BD_HOME": str(tmp_path / "state"),
        "PYTHONPATH": "/sentinel",
        "BD_PRESTAGED_SITE_PACKAGES": str(prestaged),
    }
    # A bdenv.sh that returns early (e.g. no Git checkout) never reaches PYTHONPATH: fail, never read that as "safe".
    code = (
        f". {REPO / 'toolchain' / 'bdenv.sh'} || {{ echo BDENV_SOURCE_FAILED >&2; exit 97; }}; "
        "printf '%s\\n' \"$PYTHONPATH\""
    )
    result = subprocess.run(
        ["bash", "-c", code],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    return result.stdout.splitlines()[-1].split(":")


def _dir(path, mode):
    path.mkdir()
    path.chmod(mode)
    return path


def test_own_private_prestaged_dir_is_prepended(tmp_path):
    safe = _dir(tmp_path / "prestaged", 0o755)
    entries = _pythonpath(tmp_path, safe)
    assert entries[0] == str(safe), f"BDENV_PRESTAGED_SAFE_DIR_DROPPED: {entries}"
    assert entries[-1] == "/sentinel"


def test_world_writable_prestaged_dir_is_not_trusted(tmp_path):
    unsafe = _dir(tmp_path / "prestaged", 0o777)
    entries = _pythonpath(tmp_path, unsafe)
    assert str(unsafe) not in entries and LEGACY not in entries, (
        f"BDENV_PRESTAGED_UNSAFE_ON_PYTHONPATH: {entries}"
    )


def test_group_writable_prestaged_dir_is_not_trusted(tmp_path):
    unsafe = _dir(tmp_path / "prestaged", 0o775)
    entries = _pythonpath(tmp_path, unsafe)
    assert str(unsafe) not in entries and LEGACY not in entries, (
        f"BDENV_PRESTAGED_UNSAFE_ON_PYTHONPATH: {entries}"
    )


def test_symlinked_prestaged_dir_is_not_trusted(tmp_path):
    target = _dir(tmp_path / "real", 0o755)
    link = tmp_path / "prestaged"
    link.symlink_to(target)
    entries = _pythonpath(tmp_path, link)
    assert str(link) not in entries and LEGACY not in entries, (
        f"BDENV_PRESTAGED_UNSAFE_ON_PYTHONPATH: {entries}"
    )


def test_absent_prestaged_dir_adds_nothing(tmp_path):
    entries = _pythonpath(tmp_path, tmp_path / "absent")
    assert entries == ["/sentinel"], f"BDENV_PRESTAGED_UNSAFE_ON_PYTHONPATH: {entries}"
