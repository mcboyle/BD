"""IA-06: toolchain/install_bdsuite.sh must run for an ordinary user on a fresh box.

spare8 (fresh clone, user mboyle): `./toolchain/install_bdsuite.sh` -> "Permission
denied" rc126 (tracked 100644), and `bash toolchain/install_bdsuite.sh` -> rc2
"mktemp: failed to create directory via template
'/usr/local/bin/.bdsuite-txn.XXXXXX': Permission denied": the default public-link
directory is root-owned and the script neither uses sudo nor says what to set.
The default is swapped for a tmp directory in a copied toolchain so the
unwritable case is real without touching /usr/local/bin.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

REPO = Path(__file__).resolve().parents[1]
_DEFAULT = "/usr/local/bin"

unprivileged = pytest.mark.skipif(os.geteuid() == 0, reason="root can write any directory")


def test_the_installer_is_executable_in_git():
    staged = subprocess.run(["git", "-C", str(REPO), "ls-files", "-s", "toolchain/install_bdsuite.sh"],
                            capture_output=True, text=True, check=True).stdout
    assert staged.startswith("100755 "), f"tracked mode is not executable: {staged!r}"


def _run(tmp_path: Path, default_dir: Path) -> tuple[subprocess.CompletedProcess, Path]:
    source = tmp_path / "toolchain"
    shutil.copytree(REPO / "toolchain", source)
    script = source / "install_bdsuite.sh"
    text = script.read_text(encoding="utf-8")
    assert _DEFAULT in text, "UNKNOWN: the installer no longer names its default link dir"
    script.write_text(text.replace(_DEFAULT, str(default_dir)), encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("BD_")}
    env.update({"LC_ALL": "C", "HOME": str(home), "BD_WORK_TREE": str(REPO)})
    proc = subprocess.run(["bash", str(script)], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=60)  # measured 2.4-5.1s
    return proc, home


@unprivileged
def test_unwritable_default_link_dir_falls_back_to_home_bin(tmp_path):
    default_dir = tmp_path / "usr-local-bin"
    default_dir.mkdir()
    default_dir.chmod(0o555)
    try:
        proc, home = _run(tmp_path, default_dir)
    finally:
        default_dir.chmod(0o755)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert (home / "bin" / "bd").is_symlink(), out
    assert (home / ".local" / "bin" / "bd").is_file(), out
    assert list(default_dir.iterdir()) == [], out
    assert "BD_SUITE_LINK_BIN" in out, out


@unprivileged
def test_unwritable_default_that_already_holds_the_suite_is_refused(tmp_path):
    # Publishing elsewhere would leave the old links shadowing the new ones.
    default_dir = tmp_path / "usr-local-bin"
    default_dir.mkdir()
    (default_dir / "bd").symlink_to(tmp_path / "home" / ".local" / "bin" / "bd")
    default_dir.chmod(0o555)
    try:
        proc, home = _run(tmp_path, default_dir)
    finally:
        default_dir.chmod(0o755)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, out
    assert "not writable" in out and "BD_SUITE_LINK_BIN" in out, out
    assert not (home / "bin").exists() and not (home / ".local").exists(), out


def test_writable_default_link_dir_is_still_used(tmp_path):
    default_dir = tmp_path / "usr-local-bin"
    default_dir.mkdir()
    proc, home = _run(tmp_path, default_dir)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert (default_dir / "bd").is_symlink(), out
    assert not (home / "bin").exists(), out
