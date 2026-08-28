"""Capture pruning is confined to the root named by its public glob.

The destructive fixture never places a capture-shaped path directly in /tmp.
Both the requested tree and the same-named decoy tree live under ``tmp_path``.
The PATH-local find shim maps only the historical hard-coded ``find /tmp`` to
the decoy fixture, reproducing the production defect without making any real
capture directory reachable by this test.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_LIB = _REPO / "scripts" / "lib" / "capture_run_dir.sh"


def _capture(root: Path, name: str, mtime: int) -> Path:
    directory = root / name
    directory.mkdir()
    (directory / "fixture-owner.txt").write_text(
        f"row344 fixture: {root.name}/{name}\n", encoding="utf-8"
    )
    archive = Path(f"{directory}.tar.gz")
    archive.write_text(f"row344 archive: {root.name}/{name}\n", encoding="utf-8")
    os.utime(directory, (mtime, mtime))
    return directory


def _snapshot(root: Path) -> dict[str, str]:
    observed: dict[str, str] = {}
    for path in sorted(root.iterdir()):
        if path.is_dir():
            observed[path.name] = (path / "fixture-owner.txt").read_text("utf-8")
        else:
            observed[path.name] = path.read_text("utf-8")
    return observed


def _run_prune(keep: int, glob: str, *, env: dict[str, str] | None = None):
    return subprocess.run(
        [
            "bash",
            "-c",
            '. "$1"\nshift\nbd_capture_prune "$@"',
            "row344-prune",
            str(_LIB),
            str(keep),
            glob,
        ],
        cwd=_REPO,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )


def test_prune_uses_the_requested_root_and_is_idempotent(tmp_path: Path):
    requested = tmp_path / "requested"
    decoy = tmp_path / "decoy"
    shadow = tmp_path / "bin"
    requested.mkdir()
    decoy.mkdir()
    shadow.mkdir()

    names = ("bd_capture-row344-old", "bd_capture-row344-new")
    for root in (requested, decoy):
        _capture(root, names[0], 1_700_000_000)
        _capture(root, names[1], 1_700_000_060)

    before_requested = _snapshot(requested)
    before_decoy = _snapshot(decoy)
    assert len(before_requested) == len(before_decoy) == 4, (
        "precondition: two directories and their two archives must exist in "
        f"each fixture root; requested={before_requested}; decoy={before_decoy}"
    )
    assert before_requested != before_decoy, (
        "precondition: owner payloads must distinguish requested objects from decoys"
    )

    audit = tmp_path / "find-roots.txt"
    find_shim = shadow / "find"
    find_shim.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$1\" >>\"$BD_ROW344_FIND_AUDIT\" || exit 71\n"
        "if [ \"$1\" = /tmp ]; then\n"
        "  shift\n"
        "  exec /usr/bin/find \"$BD_ROW344_DECOY_ROOT\" \"$@\"\n"
        "fi\n"
        "exec /usr/bin/find \"$@\"\n",
        encoding="utf-8",
    )
    find_shim.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{shadow}:{os.environ['PATH']}",
        "BD_ROW344_DECOY_ROOT": str(decoy),
        "BD_ROW344_FIND_AUDIT": str(audit),
    }
    glob = str(requested / "bd_capture-row344-*")

    first = _run_prune(1, glob, env=env)
    requested_after = _snapshot(requested)
    decoy_after = _snapshot(decoy)
    expected_requested = {
        names[1]: before_requested[names[1]],
        f"{names[1]}.tar.gz": before_requested[f"{names[1]}.tar.gz"],
    }
    assert first.returncode == 0, first.stderr
    assert (requested_after, decoy_after) == (expected_requested, before_decoy), (
        "prune was not bound to the advertised root: "
        f"requested={requested_after}; decoy={decoy_after}; stdout={first.stdout!r}"
    )
    assert first.stdout == f"pruned {requested / names[0]}\n"
    audited_roots = (audit.read_text("utf-8").splitlines()
                     if audit.exists() else [])
    assert audited_roots in ([], [str(requested)]), (
        "an external find inspected a root other than the requested fixture: "
        f"{audited_roots}"
    )

    second = _run_prune(1, glob, env=env)
    assert second.returncode == 0, second.stderr
    assert second.stdout == "", f"an idempotent second prune acted: {second.stdout!r}"
    assert (_snapshot(requested), _snapshot(decoy)) == (
        expected_requested,
        before_decoy,
    )


def test_prune_retention_bound_still_refuses_zero_without_acting(tmp_path: Path):
    requested = tmp_path / "requested"
    requested.mkdir()
    _capture(requested, "bd_capture-row344-old", 1_700_000_000)
    _capture(requested, "bd_capture-row344-new", 1_700_000_060)
    before = _snapshot(requested)
    assert len(before) == 4, "precondition: the refusal fixture is nonempty"

    result = _run_prune(0, str(requested / "bd_capture-row344-*"))

    assert result.returncode == 2
    assert "retention of zero" in result.stderr
    assert _snapshot(requested) == before, (
        "the keep=0 refusal changed its fixture tree before returning"
    )


def test_prune_reports_unknown_when_the_requested_root_is_not_provable(
    tmp_path: Path,
):
    requested = tmp_path / "requested"
    requested.mkdir()
    _capture(requested, "bd_capture-row344-old", 1_700_000_000)
    _capture(requested, "bd_capture-row344-new", 1_700_000_060)
    before = _snapshot(requested)
    alias = tmp_path / "mutable-root-name"
    alias.symlink_to(requested, target_is_directory=True)
    assert alias.is_dir() and len(before) == 4, (
        "precondition: the unresolved-root fixture did not contain two captures"
    )

    result = _run_prune(1, str(alias / "bd_capture-row344-*"))

    assert result.returncode == 3, result.stderr
    assert "UNKNOWN" in result.stderr and "advertised root" in result.stderr
    assert result.stdout == ""
    assert _snapshot(requested) == before, (
        "an unprovable root still permitted destructive work"
    )


def test_prune_refuses_an_unowned_archive_before_removing_its_directory(
    tmp_path: Path,
):
    requested = tmp_path / "requested"
    requested.mkdir()
    old = _capture(requested, "bd_capture-row344-old", 1_700_000_000)
    new = _capture(requested, "bd_capture-row344-new", 1_700_000_060)
    external = tmp_path / "foreign-archive"
    external.write_text("foreign fixture payload\n", encoding="utf-8")
    old_archive = Path(f"{old}.tar.gz")
    old_archive.unlink()
    old_archive.symlink_to(external)
    assert old.is_dir() and new.is_dir() and old_archive.is_symlink(), (
        "precondition: the candidate directory and foreign archive link must exist"
    )

    result = _run_prune(1, str(requested / "bd_capture-row344-*"))

    assert result.returncode == 3, result.stderr
    assert "UNKNOWN" in result.stderr and "not a regular file" in result.stderr
    assert result.stdout == ""
    assert old.is_dir() and new.is_dir(), (
        "archive ownership failed only after a capture directory was removed"
    )
    assert old_archive.is_symlink() and external.read_text("utf-8") == (
        "foreign fixture payload\n"
    )


def test_transform_control_only_sources_the_pruner():
    """Mutation control: loadability alone constrains no target behavior."""
    result = subprocess.run(
        ["bash", "-c", '. "$1" && declare -F bd_capture_prune', "row344", str(_LIB)],
        cwd=_REPO,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "bd_capture_prune" in result.stdout
