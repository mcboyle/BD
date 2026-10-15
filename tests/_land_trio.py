"""Shared vocabulary of the bd-land-trio rows: real clones, no stubbed generator.

tests/test_row_version_at_land.py holds every refusal arm; it stays fast enough
for a fleet runner because a refusal never runs the generator chain.
tests/test_row_version_at_land_stamp.py holds the two positive arms, each of
which needs one real ``--write`` stamp (the full bd-regen-order chain).  Both
modules import these helpers so the subject, the environment and the
byte-level snapshot are measured the same way on either side of the split.

Neither bd-bump nor bd-regen-order is stubbed here, in particular the positive
case cannot pass over fake generator output.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "toolchain/bin/bd-land-trio"
RELEASE_PATHS = {
    "bulk_downloader/__init__.py",
    "tests/test_settings_center_slice4.py",
    "CHANGELOG.md",
    "PIN_INDEX.json",
    "project-knowledge/STATIC_KB_MANIFEST.json",
}
TITLE = "Land the reviewed feature"
# The two whole-tree steps (bd-regen-order in the baseline, the first bd-land-trio
# --write) ran 98-298 s in a loaded -n 12 battery on a 64-core host (load 28-36),
# against ~100 s idle; 180 s cut them off. Each gets 2x the loaded worst (298 s),
# rounded up to a minute. The item bound sits above both, so a hung step is named by
# its own TimeoutExpired before pytest-timeout fires. Every other step keeps 180 s.
_WHOLE_TREE_STEP_S = 600
_STAMP_ITEM_S = 2 * _WHOLE_TREE_STEP_S + 60
# Hashing ~5600 small files is per-file call overhead, not CPU. coreutils
# sha256sum over argv chunks runs beside the interpreter without forking it
# (a fork inside a pytest session with live threads hung); each chunk's output
# stays under one pipe buffer so no child blocks. The serial Python result is
# asserted equal in test_snapshot_digests_equal_serial_sha256_over_every_listed_file.
_HASH_CHUNK = 256


def _env(work):
    env = dict(os.environ)
    for key in ("BD_INSTALL_DIR", "PYTHONPATH", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(key, None)
    env.update(BD_HOME=str(work.parent / (work.name + "-home")),
               BD_DISABLE_KEEPALIVE="1", PYTHONDONTWRITEBYTECODE="1",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_AUTHOR_NAME="Trio fixture", GIT_AUTHOR_EMAIL="trio@example.invalid",
               GIT_COMMITTER_NAME="Trio fixture", GIT_COMMITTER_EMAIL="trio@example.invalid")
    return env


def _run(work, argv, *, ok=False, timeout=180):
    cp = subprocess.run(argv, cwd=work, env=_env(work), capture_output=True,
                        text=True, timeout=timeout)
    if ok:
        assert cp.returncode == 0, cp.stdout + cp.stderr
    return cp


def _git(work, *args):
    return _run(work, ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args], ok=True).stdout.strip()


def _stamp(work, *args, title=TITLE, timeout=180):
    return _run(work, [sys.executable, str(TOOL), "--work", str(work),
                       "--title", title, *args], timeout=timeout)


def _version(work):
    text = (work / "bulk_downloader/__init__.py").read_text()
    return re.search(r'__version__ = "(3\.66\.\d+)"', text).group(1)


def _next(version, amount=1):
    return "3.66." + str(int(version.rsplit(".", 1)[1]) + amount)


def _receipt(work):
    path = Path(_git(work, "rev-parse", "--git-path", "bd-land-trio.json"))
    return path if path.is_absolute() else work / path


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _file_digests(work, names):
    """{name: sha256} of every regular file in names; one entry per file, no more."""
    names = [name for name in names if (work / name).is_file()]
    tool = shutil.which("sha256sum")
    if tool is None or len(names) <= _HASH_CHUNK:
        return {name: _digest(work / name) for name in names}
    children = [subprocess.Popen([tool, "--zero", "--", *names[at:at + _HASH_CHUNK]],
                                 cwd=work, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                for at in range(0, len(names), _HASH_CHUNK)]
    digests = {}
    for child in children:
        out, err = child.communicate()
        assert child.returncode == 0, err.decode(errors="replace")
        for record in out.split(b"\0"):
            if record:
                digest, name = record.decode().split("  ", 1)
                digests[name] = digest
    assert sorted(digests) == sorted(names), "a hashed name must map to exactly one listed file"
    return digests


def _snapshot(work):
    """Tracked/untracked bytes plus index and refs; exclude Git's read caches."""
    names = _git(work, "ls-files", "--cached", "--others", "--exclude-standard").splitlines()
    files = _file_digests(work, names)
    index = Path(_git(work, "rev-parse", "--git-path", "index"))
    if not index.is_absolute():
        index = work / index
    return (files, index.read_bytes(), _git(work, "rev-parse", "HEAD"),
            _git(work, "rev-parse", "origin/main"),
            _receipt(work).read_bytes() if _receipt(work).exists() else None)


def make_tree(tmp_path):
    """A detached, --shared clone of ROOT HEAD with origin/main at HEAD."""
    work = tmp_path / "work"
    _run(tmp_path, ["git", "clone", "--quiet", "--shared", "--no-checkout",
                    str(ROOT), str(work)], ok=True)
    head = _git(ROOT, "rev-parse", "HEAD")
    _git(work, "checkout", "--quiet", "--detach", head)
    _git(work, "update-ref", "refs/remotes/origin/main", head)
    # The host interpreter carries runtime dependencies; no shared env is edited.
    # Git excludes this link and every generator's private runtime output.
    (work / ".git/info/exclude").write_text("\n/venv\n")
    (work / "venv").symlink_to(Path(sys.executable).parent.parent, target_is_directory=True)
    assert _git(work, "status", "--porcelain") == ""
    return work


def discard_tree(work):
    """Remove a fixture tree and its private BD_HOME as soon as the test is over.

    Left in place, every ~5600-file tree is reclaimed by the session-end
    retention walk in tests/_tmproot.py, which proves each entry's identity as
    it goes; on a 24-tree file that walk alone measured 94 s against 0.3 s per
    tree here. Nothing judged is removed: every snapshot is taken before this.
    """
    shutil.rmtree(work)
    home = work.parent / (work.name + "-home")
    if home.is_dir():
        shutil.rmtree(home)


def _clean_generated_baseline(work):
    """A real reviewed tree has its derived artifacts current before stamping."""
    _run(work, [sys.executable, str(work / "toolchain/bin/bd-regen-order"),
                "--work", str(work)], ok=True, timeout=_WHOLE_TREE_STEP_S)
    _git(work, "add", "--all")
    if _git(work, "diff", "--cached", "--name-only"):
        _git(work, "commit", "--quiet", "-m", "Fixture reviewed generated baseline")
    assert _git(work, "status", "--porcelain") == ""


def _bump_committed(work, version):
    _run(work, [sys.executable, str(work / "toolchain/bin/bd-bump"), version,
                "--work", str(work), "--title", "Fixture prior release", "--write"], ok=True)
    _git(work, "add", "--all")
    _git(work, "commit", "--quiet", "-m", "Fixture prior release")


def _refused_without_writes(work, reason, *args, **kwargs):
    before = _snapshot(work)
    cp = _stamp(work, *args, **kwargs)
    assert cp.returncode == 3, cp.stdout + cp.stderr
    assert cp.stderr.splitlines()[0] == "TRIO-REFUSED " + reason, cp.stderr
    assert not any(line.startswith("TRIO-STAMPED ") for line in cp.stdout.splitlines())
    assert _snapshot(work) == before, "refusal changed file bytes, index, HEAD or main"
