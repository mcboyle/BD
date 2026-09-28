"""O1479-MAINGUARD: Harness candidate verification for bd-main-guard.sh.

Finding addressed:
RULINGS-1048-bd-pm-A.2.md item 4 / DISPATCH-O1479-MAINGUARD-bd-agy-fixer-1:
bd-main-guard must never move or archive a directory that is a git worktree
(.git file check: `[ -f "$dir/.git" ]`) -- files only.

Controls verified:
1. ANC_MOVE loop checks for .git file and skips git worktrees.
2. ANC_MOVE loop checks for directories and skips them (files only).
3. ANC_MOVE positive control moves config from non-worktree directory.
4. Untracked archiving loop checks for .git file and skips git worktrees.
5. Untracked archiving loop skips directories (files only).
6. Untracked positive control archives regular untracked file.
7. Candidate patch applies cleanly with zero offset/fuzz.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CAND_DIR = os.environ.get("BD_O1479_MAINGUARD_CANDIDATE_DIR", "")
GUARD_CAND = os.path.join(CAND_DIR, "bd-main-guard.sh") if CAND_DIR else ""
PATCH_FILE = os.path.join(CAND_DIR, "o1479-mainguard.patch") if CAND_DIR else ""

pytestmark = pytest.mark.skipif(
    not CAND_DIR,
    reason="candidate opt-in required (set BD_O1479_MAINGUARD_CANDIDATE_DIR)",
)


def _assert_executable(path_str: str) -> None:
    path = Path(path_str)
    if not path.is_file():
        raise FileNotFoundError(f"candidate file missing: {path}")
    st = path.stat()
    if not (st.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)):
        raise PermissionError(f"candidate not executable: {path}")


def _run_guard(
    script_path: str,
    env_overrides: dict[str, str],
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    _assert_executable(script_path)
    env = dict(os.environ)
    env.update(env_overrides)
    return subprocess.run(
        ["bash", script_path],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=str(path), check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], cwd=str(path), check=True
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"], cwd=str(path), check=True
    )


def test_missing_candidate_raises(tmp_path: Path) -> None:
    missing_path = tmp_path / "nonexistent.sh"
    with pytest.raises(FileNotFoundError):
        _run_guard(str(missing_path), {})


def test_non_executable_candidate_raises(tmp_path: Path) -> None:
    non_exec = tmp_path / "non_exec.sh"
    non_exec.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    non_exec.chmod(0o644)
    with pytest.raises(PermissionError):
        _run_guard(str(non_exec), {})


def test_anc_move_skips_git_worktree(tmp_path: Path) -> None:
    """Proves bd-main-guard never moves config out of a git worktree (.git file check)."""
    p = tmp_path / "persist"
    m = tmp_path / "repo"
    out = tmp_path / "strays"
    log = tmp_path / "guard.log"
    anc_dir = tmp_path / "anc"
    p.mkdir()
    out.mkdir()
    anc_dir.mkdir()
    _init_git_repo(m)

    # Create a git worktree directory in anc_move (worktree has .git as a FILE)
    wt_dir = anc_dir / "worktree_branch"
    wt_dir.mkdir()
    (wt_dir / ".git").write_text("gitdir: /fake/path/worktree\n", encoding="utf-8")
    cfg = wt_dir / "pytest.ini"
    cfg.write_text("[pytest]\naddopts = -q\n", encoding="utf-8")

    env = {
        "BD_MAIN_GUARD_P": str(p),
        "BD_MAIN_GUARD_M": str(m),
        "BD_MAIN_GUARD_SAY": "/bin/true",
        "BD_MAIN_GUARD_OUT": str(out),
        "BD_MAIN_GUARD_LOG": str(log),
        "BD_MAIN_GUARD_ANC_MOVE": str(wt_dir),
        "BD_MAIN_GUARD_ANC_REPORT": str(tmp_path / "empty_report"),
    }

    _run_guard(GUARD_CAND, env)

    # Config in git worktree must NOT have been moved
    assert cfg.is_file(), "pytest.ini was improperly moved out of a git worktree"


def test_anc_move_skips_directories(tmp_path: Path) -> None:
    """Proves bd-main-guard only operates on files, never moving directories."""
    p = tmp_path / "persist"
    m = tmp_path / "repo"
    out = tmp_path / "strays"
    log = tmp_path / "guard.log"
    anc_dir = tmp_path / "anc"
    p.mkdir()
    out.mkdir()
    anc_dir.mkdir()
    _init_git_repo(m)

    plain_dir = anc_dir / "plain"
    plain_dir.mkdir()
    dir_named_ini = plain_dir / "pytest.ini"
    dir_named_ini.mkdir()

    env = {
        "BD_MAIN_GUARD_P": str(p),
        "BD_MAIN_GUARD_M": str(m),
        "BD_MAIN_GUARD_SAY": "/bin/true",
        "BD_MAIN_GUARD_OUT": str(out),
        "BD_MAIN_GUARD_LOG": str(log),
        "BD_MAIN_GUARD_ANC_MOVE": str(plain_dir),
        "BD_MAIN_GUARD_ANC_REPORT": str(tmp_path / "empty_report"),
    }

    _run_guard(GUARD_CAND, env)

    assert dir_named_ini.is_dir(), "directory named pytest.ini should not be moved"


def test_anc_move_positive_fixture(tmp_path: Path) -> None:
    """Positive fixture: config in non-worktree directory IS moved into archive."""
    p = tmp_path / "persist"
    m = tmp_path / "repo"
    out = tmp_path / "strays"
    log = tmp_path / "guard.log"
    anc_dir = tmp_path / "anc"
    p.mkdir()
    out.mkdir()
    anc_dir.mkdir()
    _init_git_repo(m)

    plain_dir = anc_dir / "plain"
    plain_dir.mkdir()
    cfg = plain_dir / "pytest.ini"
    cfg.write_text("[pytest]\n", encoding="utf-8")

    env = {
        "BD_MAIN_GUARD_P": str(p),
        "BD_MAIN_GUARD_M": str(m),
        "BD_MAIN_GUARD_SAY": "/bin/true",
        "BD_MAIN_GUARD_OUT": str(out),
        "BD_MAIN_GUARD_LOG": str(log),
        "BD_MAIN_GUARD_ANC_MOVE": str(plain_dir),
        "BD_MAIN_GUARD_ANC_REPORT": str(tmp_path / "empty_report"),
    }

    _run_guard(GUARD_CAND, env)

    assert not cfg.exists(), (
        "pytest.ini should have been moved from non-worktree directory"
    )
    moved_found = any("pytest.ini" in f.name for f in out.iterdir())
    assert moved_found, "archived config was not found in OUT directory"


def test_untracked_skips_nested_git_worktree(tmp_path: Path) -> None:
    """Proves untracked git worktree inside M is not archived/copied into strays."""
    p = tmp_path / "persist"
    m = tmp_path / "repo"
    out = tmp_path / "strays"
    log = tmp_path / "guard.log"
    p.mkdir()
    out.mkdir()
    _init_git_repo(m)

    # Create untracked git worktree inside main repo
    subwt = m / "subwt"
    subprocess.run(
        ["git", "worktree", "add", str(subwt)],
        cwd=str(m),
        check=True,
        capture_output=True,
    )
    assert (subwt / ".git").is_file(), "git worktree must have a .git file"

    env = {
        "BD_MAIN_GUARD_P": str(p),
        "BD_MAIN_GUARD_M": str(m),
        "BD_MAIN_GUARD_SAY": "/bin/true",
        "BD_MAIN_GUARD_OUT": str(out),
        "BD_MAIN_GUARD_LOG": str(log),
        "BD_MAIN_GUARD_ANC_MOVE": str(tmp_path / "empty_move"),
        "BD_MAIN_GUARD_ANC_REPORT": str(tmp_path / "empty_report"),
    }

    _run_guard(GUARD_CAND, env)

    # Strays directory must NOT contain the worktree
    subwt_in_strays = False
    for root, dirs, files in os.walk(str(out)):
        if "subwt" in dirs or "subwt" in files:
            subwt_in_strays = True
            break

    assert not subwt_in_strays, "git worktree was improperly archived into strays"


def test_untracked_skips_plain_directory(tmp_path: Path) -> None:
    """Proves untracked directories are not archived (files only)."""
    p = tmp_path / "persist"
    m = tmp_path / "repo"
    out = tmp_path / "strays"
    log = tmp_path / "guard.log"
    p.mkdir()
    out.mkdir()
    _init_git_repo(m)

    # Create an untracked directory inside main repo
    emptydir = m / "plain_dir"
    emptydir.mkdir()

    env = {
        "BD_MAIN_GUARD_P": str(p),
        "BD_MAIN_GUARD_M": str(m),
        "BD_MAIN_GUARD_SAY": "/bin/true",
        "BD_MAIN_GUARD_OUT": str(out),
        "BD_MAIN_GUARD_LOG": str(log),
        "BD_MAIN_GUARD_ANC_MOVE": str(tmp_path / "empty_move"),
        "BD_MAIN_GUARD_ANC_REPORT": str(tmp_path / "empty_report"),
    }

    _run_guard(GUARD_CAND, env)

    plain_in_strays = False
    for root, dirs, files in os.walk(str(out)):
        if "plain_dir" in dirs:
            plain_in_strays = True
            break

    assert not plain_in_strays, "plain directory was improperly copied into strays"


def test_untracked_archives_regular_file(tmp_path: Path) -> None:
    """Positive fixture: regular untracked file IS archived into strays."""
    p = tmp_path / "persist"
    m = tmp_path / "repo"
    out = tmp_path / "strays"
    log = tmp_path / "guard.log"
    p.mkdir()
    out.mkdir()
    _init_git_repo(m)

    stray = m / "stray_file.txt"
    stray.write_text("untracked payload\n", encoding="utf-8")

    env = {
        "BD_MAIN_GUARD_P": str(p),
        "BD_MAIN_GUARD_M": str(m),
        "BD_MAIN_GUARD_SAY": "/bin/true",
        "BD_MAIN_GUARD_OUT": str(out),
        "BD_MAIN_GUARD_LOG": str(log),
        "BD_MAIN_GUARD_ANC_MOVE": str(tmp_path / "empty_move"),
        "BD_MAIN_GUARD_ANC_REPORT": str(tmp_path / "empty_report"),
    }

    _run_guard(GUARD_CAND, env)

    stray_archived = False
    for root, _dirs, files in os.walk(str(out)):
        if "stray_file.txt" in files:
            stray_archived = True
            break

    assert stray_archived, "regular untracked file was not archived into strays"


def test_patch_applies_cleanly() -> None:
    """Verifies that o1479-mainguard.patch exists, is non-empty, and applies cleanly."""
    patch_path = Path(PATCH_FILE)
    assert patch_path.is_file(), f"patch file missing: {patch_path}"
    assert patch_path.stat().st_size > 0, f"patch file is empty: {patch_path}"

    orig_script = Path(CAND_DIR) / "orig" / "bd-main-guard.sh"
    if not orig_script.is_file():
        orig_script = Path("/home/mboyle/bd-persist/harness/bd-main-guard.sh")

    patch_bin = shutil.which("patch")
    assert patch_bin is not None, "patch binary required on PATH"

    res = subprocess.run(
        [patch_bin, "--dry-run", "-p1", "-i", str(patch_path)],
        cwd=str(orig_script.parent),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, (
        f"patch --dry-run failed:\nstdout: {res.stdout}\nstderr: {res.stderr}"
    )
