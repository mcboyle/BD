"""EFF-E2: bd-attic-move.sh moves manifest-named uncalled harness FILES to an attic, and can refuse.

Harness candidate test (bd-harness-cut shape): opt in with
BD_EFF_E2_CANDIDATE=/home/mboyle/bd-persist/harness-work/FIX/eff-e2-bd-worker-A3-A (dir holding bd-attic-move.sh).
Hermetic: BD_ATTIC_SRC / BD_ATTIC_DIR / BD_ATTIC_LOG point into tmp_path.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_EFF_E2_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    (tmp_path / "harness").mkdir()
    return {
        **os.environ,
        "BD_ATTIC_SRC": str(tmp_path / "harness"),
        "BD_ATTIC_DIR": str(tmp_path / "attic"),
        "BD_ATTIC_LOG": str(tmp_path / "attic.log"),
    }


def _files(env: dict[str, str], *names: str) -> Path:
    src = Path(env["BD_ATTIC_SRC"])
    rows = []
    for name in names:
        (src / name).write_text(f"#!/bin/bash\necho {name}\n")
        rows.append(f"{name}\t{_sha(src / name)}\t{(src / name).stat().st_size}")
    manifest = src.parent / "manifest.tsv"
    manifest.write_text("# name\tsha256\tbytes\n" + "\n".join(rows) + "\n")
    return manifest


def _run(env: dict[str, str], *args: str, apply: bool = False) -> tuple[int, str]:
    run_env = {**env, "APPLY": "1"} if apply else env
    proc = subprocess.run(
        [str(Path(CANDIDATE) / "bd-attic-move.sh"), *args],
        env=run_env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_candidate_is_executable() -> None:
    path = Path(CANDIDATE) / "bd-attic-move.sh"
    assert path.is_file() and os.access(path, os.X_OK), f"candidate missing: {path}"


def test_default_is_dry_run_and_moves_nothing(env: dict[str, str]) -> None:
    manifest = _files(env, "a.py", "b.sh")

    rc, out = _run(env, str(manifest))

    assert rc == 0, out
    assert (Path(env["BD_ATTIC_SRC"]) / "a.py").is_file()
    assert not Path(env["BD_ATTIC_DIR"]).exists()
    assert "ATTIC DRY-RUN restore=0 entries=2 moved=0 refused=0" in out


def test_apply_moves_files_byte_identical_and_restore_brings_them_back(
    env: dict[str, str],
) -> None:
    manifest = _files(env, "a.py", "b.sh")
    src, attic = Path(env["BD_ATTIC_SRC"]), Path(env["BD_ATTIC_DIR"])
    before = {n: _sha(src / n) for n in ("a.py", "b.sh")}

    rc, out = _run(env, str(manifest), apply=True)

    assert rc == 0, out
    assert (
        not (src / "a.py").exists() and {n: _sha(attic / n) for n in before} == before
    )
    assert "ATTIC APPLY restore=0 entries=2 moved=2 refused=0" in out

    rc, out = _run(env, "--restore", str(manifest), apply=True)

    assert rc == 0, out
    assert {n: _sha(src / n) for n in before} == before and not any(attic.iterdir())


def test_changed_file_is_refused_and_left_in_place(env: dict[str, str]) -> None:
    manifest = _files(env, "a.py", "b.sh")
    src = Path(env["BD_ATTIC_SRC"])
    (src / "a.py").write_text("edited after the census\n")

    rc, out = _run(env, str(manifest), apply=True)

    assert rc == 1, out
    assert (src / "a.py").read_text() == "edited after the census\n"
    assert "changed since census" in out and "moved=1 refused=1" in out


@pytest.mark.parametrize("kind", ["directory", "symlink", "missing", "path"])
def test_non_plain_file_entries_are_refused(env: dict[str, str], kind: str) -> None:
    src = Path(env["BD_ATTIC_SRC"])
    name = "../escape" if kind == "path" else "thing"
    if kind == "directory":
        (src / name).mkdir()
    elif kind == "symlink":
        (src / "real").write_text("x")
        (src / name).symlink_to(src / "real")
    manifest = src.parent / "manifest.tsv"
    manifest.write_text(f"{name}\t{'0' * 64}\t1\n")

    rc, out = _run(env, str(manifest), apply=True)

    assert rc == 1, out
    assert "REFUSE" in out and "moved=0 refused=1" in out
    if kind == "directory":
        assert (src / name).is_dir()
    if kind == "symlink":
        assert (src / name).is_symlink()


def test_occupied_destination_is_refused_not_overwritten(env: dict[str, str]) -> None:
    manifest = _files(env, "a.py")
    attic = Path(env["BD_ATTIC_DIR"])
    attic.mkdir()
    (attic / "a.py").write_text("older attic copy\n")

    rc, out = _run(env, str(manifest), apply=True)

    assert rc == 1, out
    assert (attic / "a.py").read_text() == "older attic copy\n"
    assert (Path(env["BD_ATTIC_SRC"]) / "a.py").is_file()
    assert "destination" in out and "exists" in out


def test_empty_manifest_is_a_failure(env: dict[str, str]) -> None:
    manifest = Path(env["BD_ATTIC_SRC"]).parent / "manifest.tsv"
    manifest.write_text("# header only\n")

    rc, out = _run(env, str(manifest), apply=True)

    assert rc == 2, out
    assert "names no files" in out
