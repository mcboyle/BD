"""Consecutive captures stop overwriting each other.

BACKLOG 5, the capture.sh half. `bd-run` got per-run logs at v3.66.1060; this is
the row's other half, which that cut explicitly recorded as still open:
`capture.sh` wrote a FIXED /tmp/bd_capture, so a second round destroyed the
first in place.

MEASURED COST OF THAT, 2026-08-13: two capture rounds in one day were preserved
only because somebody copied /tmp/bd_capture out by hand before the next round
started. The round that FAILED -- test4 at 3.66.1096 -- would have been the most
expensive one to lose, and nothing in the tooling would have stopped that.

WHY PRUNING HAPPENS ON THE WAY IN. A run that crashes leaves the most valuable
directory on the box. Pruning on the way out deletes it as part of the failure,
which is the one moment nobody wants tidiness.

WHAT THIS DOES NOT COVER: the vault directory (/tmp/bd_capture_vault) is still
fixed. It is torn down by the run that creates it rather than accumulating, so
it is not this row's subject -- but two SIMULTANEOUS captures on one host would
still collide there, and nothing runs two on one host today.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

# Its subject is one script's output path and one library's retention, not an
# invariant over the tree.
BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_LIB = _REPO / "scripts" / "lib" / "capture_run_dir.sh"
_CAPTURE = _REPO / "capture.sh"


def _sh(script: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f". {_LIB}\n{script}"],
        capture_output=True, text=True, cwd=str(cwd or _REPO),
    )


# ------------------------------------------------------------------ run id --

def test_the_run_id_is_stable_within_a_run_and_carries_the_commit():
    r = _sh('bd_capture_run_id "$PWD"')
    assert r.returncode == 0, r.stderr
    rid = r.stdout.strip()
    assert rid, "run id is empty"

    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                         cwd=str(_REPO), capture_output=True, text=True).stdout.strip()
    assert sha and sha in rid, (
        f"the run id {rid!r} does not carry the commit {sha!r}, so `ls /tmp` "
        "cannot tell you which tree a bundle describes")
    assert "T" in rid and "Z" in rid, (
        f"the run id {rid!r} carries no UTC timestamp")


def test_two_runs_do_not_collide(tmp_path):
    """The whole point of the row. Same second, same commit, same host."""
    made = []
    try:
        for _ in range(2):
            rid = _sh('bd_capture_run_id "$PWD"').stdout.strip()
            d = Path(f"/tmp/bd_capture-{rid}")
            assert not d.exists(), (
                f"{d} already existed, so this run would overwrite it -- which "
                "is the defect backlog 5 describes")
            d.mkdir()
            made.append(d)
        assert made[0] != made[1], (
            "two consecutive run ids produced the SAME directory; consecutive "
            "captures would still overwrite each other")
    finally:
        for d in made:
            shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------- prune --

def _make_dirs(n: int, prefix: str) -> list[Path]:
    """n capture-shaped dirs with strictly increasing mtimes."""
    made = []
    for i in range(n):
        d = Path(f"/tmp/{prefix}{i:02d}")
        d.mkdir(parents=True, exist_ok=True)
        (d / "10_VERDICT.txt").write_text(f"run {i}\n", encoding="utf-8")
        tgz = Path(str(d) + ".tar.gz")
        tgz.write_text("archive\n", encoding="utf-8")
        # Distinct mtimes, oldest first. Set explicitly rather than sleeping:
        # a test that depends on wall-clock resolution is a test that fails on
        # a fast filesystem.
        os.utime(d, (1_700_000_000 + i * 60, 1_700_000_000 + i * 60))
        made.append(d)
    return made


def _cleanup(prefix: str) -> None:
    for p in Path("/tmp").glob(prefix + "*"):
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)


def test_prune_keeps_the_newest_and_removes_the_rest():
    prefix = "bd_capture-zzprune-"
    _cleanup(prefix)
    try:
        made = _make_dirs(8, prefix)
        assert all(d.exists() for d in made), "precondition: 8 dirs created"

        r = _sh(f'bd_capture_prune 5 "/tmp/{prefix}*"')
        assert r.returncode == 0, r.stderr

        survivors = sorted(p.name for p in Path("/tmp").glob(prefix + "*") if p.is_dir())
        assert len(survivors) == 5, (
            f"expected 5 survivors, got {len(survivors)}: {survivors}")
        # The newest five are indices 3..7 by the mtimes set above.
        assert survivors == [f"{prefix}{i:02d}" for i in range(3, 8)], survivors
    finally:
        _cleanup(prefix)


def test_prune_removes_the_matching_tarball_too():
    """A bundle whose directory is gone is not evidence anybody can read, and
    a tarball nothing removes is the leak CLAUDE.md section 0 names -- creating
    a path is a promise to remove it."""
    prefix = "bd_capture-zztar-"
    _cleanup(prefix)
    try:
        _make_dirs(3, prefix)
        _sh(f'bd_capture_prune 1 "/tmp/{prefix}*"')

        left = sorted(p.name for p in Path("/tmp").glob(prefix + "*"))
        assert left == [f"{prefix}02", f"{prefix}02.tar.gz"], (
            f"prune left the wrong set behind: {left}")
    finally:
        _cleanup(prefix)


def test_prune_says_what_it_removed():
    """A silent deletion is indistinguishable from a directory that was never
    written, which is how evidence goes missing without anybody noticing."""
    prefix = "bd_capture-zzsay-"
    _cleanup(prefix)
    try:
        _make_dirs(3, prefix)
        r = _sh(f'bd_capture_prune 1 "/tmp/{prefix}*"')
        assert r.stdout.count("pruned") == 2, (
            f"prune removed 2 directories but reported: {r.stdout!r}")
    finally:
        _cleanup(prefix)


def test_prune_refuses_a_retention_of_zero():
    """The over-sensitivity control, inverted: a retention of zero deletes the
    evidence this whole row exists to preserve, so it is refused rather than
    obeyed."""
    prefix = "bd_capture-zzzero-"
    _cleanup(prefix)
    try:
        _make_dirs(2, prefix)
        r = _sh(f'bd_capture_prune 0 "/tmp/{prefix}*"')

        assert r.returncode != 0, "keep=0 was obeyed instead of refused"
        left = [p for p in Path("/tmp").glob(prefix + "*") if p.is_dir()]
        assert len(left) == 2, f"a refused prune still deleted something: {left}"
    finally:
        _cleanup(prefix)


# ------------------------------------------------------------------- shell --

def _capture_code() -> str:
    import sys
    sys.path.insert(0, str(_REPO / "tests"))
    from shell_source import shell_code_only
    return shell_code_only(_CAPTURE)


def test_capture_sh_uses_a_run_keyed_directory():
    code = _capture_code()
    assert 'OUT="/tmp/bd_capture"' not in code, (
        "capture.sh still writes the FIXED path, so consecutive captures "
        "overwrite each other -- backlog 5 is not closed")
    assert "bd_capture_run_id" in code, (
        "capture.sh does not derive a run id")
    assert "bd_capture_prune" in code, (
        "capture.sh never prunes, so run directories accumulate without bound "
        "-- creating a path is a promise to remove it")


def test_the_archive_follows_the_run_directory():
    """An archive still named /tmp/bd_capture.tar.gz would be overwritten by
    the next run even though its directory was not -- half a fix reads as a
    whole one."""
    code = _capture_code()
    assert 'ARCHIVE="/tmp/bd_capture.tar.gz"' not in code, (
        "the archive name is still fixed while the directory is not")
