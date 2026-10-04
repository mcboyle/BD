"""o1698-nfs-share-ln-T: bd-nfs-share vm-home-links links with `ln -s -T`, and usage prints the whole help block.

Lens F1 (o1698-vm-home-symlinks C1-r1): a dir re-created at ~/<name> after the mv-aside made `ln -s -- target link`
put the link INSIDE that dir and print "<name> MOVED" as success. With -T (the REPO-ALIAS form) ln must fail instead.
The tool is deployed from bd-persist, not this repo: BD_O1698_NFS_SHARE_LN_T_CANDIDATE is the absolute path of the
candidate script.
Hermetic (O1641): no live host. ssh is a fake (BD_NFS_SSH) that runs the "VM" side locally under a fake HOME, with a
findmnt shim (every mount root reads as nfs4) and an ln shim that can re-create the link path as a dir just before the
real ln runs (the race window between the post-mv check and ln). Every path lives in tmp_path.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_O1698_NFS_SHARE_LN_T_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

NAMES = ("bd-persist", "bd-local-wt", "bd-review-wt", "bd-cuts", "bin")
VM = "10.9.9.1"

FAKE_SSH = r"""#!/bin/bash
while [ $# -gt 0 ]; do case "$1" in -n) shift ;; -o) shift 2 ;; -*) shift ;; *) break ;; esac; done
shift; cmd="$*"
HOME="$FAKE_HOME" PATH="$FAKE_BIN:$PATH" exec bash -c "$cmd"
"""
FINDMNT = "#!/bin/sh\necho nfs4\n"
LN = r"""#!/bin/bash
[ -n "${FAKE_RACE_LINK:-}" ] && [ "${!#}" = "$FAKE_RACE_LINK" ] && mkdir -p -- "$FAKE_RACE_LINK"
exec "$REAL_LN" "$@"
"""


def _exe(p: Path, body: str) -> Path:
    p.write_text(body)
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


@pytest.fixture
def fx(tmp_path: Path) -> dict:
    cand = Path(CANDIDATE)
    assert cand.is_file() and os.access(cand, os.X_OK), f"candidate supplied but not executable: {cand}"
    real_ln = shutil.which("ln")
    assert real_ln, "no ln on PATH"
    root = tmp_path / "mnt"
    for name in NAMES:
        (root / name).mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _exe(fake_bin / "findmnt", FINDMNT)
    _exe(fake_bin / "ln", LN)
    hosts = tmp_path / "hosts"
    hosts.write_text(f"test1 {VM} linux\n")
    env = dict(
        os.environ,
        BD_HOSTS_FILE=str(hosts),
        BD_NFS_SEAT_VMS="test1",
        BD_NFS_SERVER="10.0.0.1",
        BD_NFS_MOUNT_ROOT=str(root),
        BD_NFS_SOURCE_ROOT=str(root),
        BD_NFS_LOG=str(tmp_path / "nfs-share.log"),
        BD_NFS_SSH=str(_exe(tmp_path / "fake-ssh", FAKE_SSH)),
        FAKE_HOME=str(home),
        FAKE_BIN=str(fake_bin),
        REAL_LN=real_ln,
    )
    env.pop("FAKE_RACE_LINK", None)
    return {"env": env, "root": root, "home": home}


def _run(fx: dict, *args: str, **extra: str) -> subprocess.CompletedProcess:
    env = dict(fx["env"], **extra)
    return subprocess.run([CANDIDATE, *args], env=env, capture_output=True, text=True, timeout=60, check=False)


def test_dir_recreated_at_link_path_makes_ln_fail_not_nest(fx: dict) -> None:
    home, root = fx["home"], fx["root"]
    local = home / "bd-cuts"
    local.mkdir()
    (local / "local-data").write_text("keep\n")
    r = _run(fx, "vm-home-links", "test1", FAKE_RACE_LINK=str(local))
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "bd-cuts COULD NOT LOOK LINK-FAILED" in r.stdout, out
    assert "bd-cuts MOVED" not in r.stdout, out
    assert not (local / "bd-cuts").exists() and not (local / "bd-cuts").is_symlink(), f"nested link created: {out}"
    assert local.is_dir() and not local.is_symlink(), out
    asides = sorted(home.glob("bd-cuts.local-*"))
    assert len(asides) == 1 and (asides[0] / "local-data").read_text() == "keep\n", out
    assert os.readlink(home / "bd-persist") == str(root / "bd-persist"), out


def test_absent_link_paths_are_linked(fx: dict) -> None:
    home, root = fx["home"], fx["root"]
    r = _run(fx, "vm-home-links", "test1")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    for name in NAMES:
        assert f"{name} LINKED" in r.stdout, out
        assert os.readlink(home / name) == str(root / name), out
    assert "NONCE OK" in r.stdout, out


def test_usage_prints_the_full_help_block(fx: dict) -> None:
    r = _run(fx)
    assert r.returncode == 2, r.stdout + r.stderr
    lines = Path(CANDIDATE).read_text().splitlines(keepends=True)
    exit_at = next((i for i, line in enumerate(lines) if line.startswith("#   Exit:")), None)
    assert exit_at is not None and exit_at > 3, "no '#   Exit:' line after the usage entries"
    help_block = "".join(lines[3:exit_at])
    assert r.stderr == help_block, r.stderr
    assert "bd-nfs-share rollback <host>|--hub [--apply]" in r.stderr, r.stderr
