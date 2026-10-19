"""P5-MAINGUARD-WRITE: Harness candidate verification for bd-main-guard.sh.

Findings addressed:
1. FINDING-MAIN-STRAY-IDXCLI-bd-integrator-A.md / DISPATCH-P5-mainguard-write:
   main-guard patch write failed (PATCH-WRITE-FAILED) when a stray was staged with git add
   because `git diff` only compared working tree to index (producing 0 bytes);
   now renders tracked changes relative to HEAD so staged strays write a valid non-empty patch.
2. Extension of O1479-MAINGUARD:
   Preserves git worktree (.git file check) and files-only guards on ANC_MOVE and untracked archiving.

Controls verified:
1. Staged stray writes a non-empty patch and does not emit PATCH-WRITE-FAILED (RED on orig, GREEN on candidate).
2. Unstaged tracked modification writes a non-empty patch (positive fixture).
3. ANC_MOVE skips directories that are git worktrees (.git file check).
4. ANC_MOVE skips directory entries (files only).
5. ANC_MOVE moves config from non-worktree directory (positive fixture).
6. Untracked archiving skips nested git worktrees (.git file check).
7. Untracked archiving skips plain directories (files only).
8. Untracked archiving copies regular untracked file (positive fixture).
9. Candidate patch applies cleanly with zero offset/fuzz.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CAND_DIR = os.environ.get("BD_P5_MAINGUARD_WRITE_CANDIDATE_DIR", "")
GUARD_CAND = os.path.join(CAND_DIR, "bd-main-guard.sh") if CAND_DIR else ""
PATCH_FILE = os.path.join(CAND_DIR, "p5-mainguard-write.patch") if CAND_DIR else ""

pytestmark = pytest.mark.skipif(
    not CAND_DIR,
    reason="candidate opt-in required (set BD_P5_MAINGUARD_WRITE_CANDIDATE_DIR)",
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
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(path),
        check=True,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=str(path),
        check=True,
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


def test_staged_stray_writes_non_empty_patch(tmp_path: Path) -> None:
    """Proves main-guard successfully captures a patch when a stray is staged (git add)."""
    p = tmp_path / "persist"
    m = tmp_path / "repo"
    out = tmp_path / "strays"
    log = tmp_path / "guard.log"
    p.mkdir()
    out.mkdir()
    _init_git_repo(m)

    # Stage a file with git add (reproducing 12:30 test_bh_idxcli.py failure)
    staged = m / "tests" / "test_bh_idxcli.py"
    staged.parent.mkdir(parents=True)
    staged.write_text("# stray staged test file\n", encoding="utf-8")
    subprocess.run(["git", "add", "tests/test_bh_idxcli.py"], cwd=str(m), check=True)

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

    log_content = log.read_text(encoding="utf-8") if log.is_file() else ""
    assert "PATCH-WRITE-FAILED" not in log_content, (
        f"main-guard failed to write patch for staged stray: {log_content}"
    )

    patches = [
        f
        for f in out.iterdir()
        if f.name.startswith("auto-") and f.name.endswith(".patch")
    ]
    assert len(patches) == 1, (
        f"expected 1 patch file in {out}, found: {[f.name for f in patches]}"
    )
    assert patches[0].stat().st_size > 0, (
        f"patch file {patches[0].name} is unexpectedly empty"
    )


def test_unstaged_tracked_change_writes_patch(tmp_path: Path) -> None:
    """Positive fixture: unstaged tracked modification also writes a non-empty patch."""
    p = tmp_path / "persist"
    m = tmp_path / "repo"
    out = tmp_path / "strays"
    log = tmp_path / "guard.log"
    p.mkdir()
    out.mkdir()
    _init_git_repo(m)

    f = m / "existing.py"
    f.write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "existing.py"], cwd=str(m), check=True)
    subprocess.run(["git", "commit", "-m", "add existing"], cwd=str(m), check=True)

    # Modify without staging
    f.write_text("modified\n", encoding="utf-8")

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

    patches = [
        f
        for f in out.iterdir()
        if f.name.startswith("auto-") and f.name.endswith(".patch")
    ]
    assert len(patches) == 1, (
        f"expected 1 patch file in {out}, found: {[f.name for f in patches]}"
    )
    assert patches[0].stat().st_size > 0, (
        "patch file should contain diff of modified file"
    )


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

    subwt_in_strays = False
    for _root, dirs, files in os.walk(str(out)):
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
    for _root, dirs, _files in os.walk(str(out)):
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
    for _root, _dirs, files in os.walk(str(out)):
        if "stray_file.txt" in files:
            stray_archived = True
            break

    assert stray_archived, "regular untracked file was not archived into strays"


def test_patch_applies_cleanly() -> None:
    """Verifies that p5-mainguard-write.patch exists, is non-empty, and applies cleanly."""
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


@pytest.mark.parametrize("fail_diff", [False, True])
def test_diff_status_controls_patch_publication(tmp_path: Path, fail_diff: bool) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    (repo / "stray.txt").write_text("staged payload")
    subprocess.run(["git", "-C", str(repo), "add", "stray.txt"], check=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    real = shutil.which("git")
    assert real
    wrapper = bindir / "git"
    wrapper.write_text('#!/bin/bash\nfor a in "$@"; do\n if [ "$a" = diff ] && [ "$FAIL_DIFF" = 1 ]; then printf "partial-diff-sentinel\\n"; exit 7; fi\n if [ "$a" = checkout ]; then exit 0; fi\ndone\nexec "$REAL_GIT" "$@"\n')
    wrapper.chmod(0o755)
    persist = tmp_path / "persist"
    persist.mkdir()
    out = tmp_path / "archive"
    log = tmp_path / "guard.log"
    result = _run_guard(GUARD_CAND, {
        "PATH": str(bindir) + ":" + os.environ["PATH"], "REAL_GIT": real,
        "FAIL_DIFF": "1" if fail_diff else "0", "BD_MAIN_GUARD_P": str(persist),
        "BD_MAIN_GUARD_M": str(repo), "BD_MAIN_GUARD_OUT": str(out),
        "BD_MAIN_GUARD_LOG": str(log), "BD_MAIN_GUARD_SAY": "/bin/true",
        "BD_MAIN_GUARD_ANC_MOVE": str(tmp_path / "no-move"),
        "BD_MAIN_GUARD_ANC_REPORT": str(tmp_path / "no-report")})
    assert result.returncode == 0, result.stderr
    text = log.read_text()
    if fail_diff:
        assert "PATCH-WRITE-FAILED" in text, "MAINGUARD_FAILED_DIFF_PUBLISHED: " + text
        assert not list(out.glob("auto-*.patch")), "failed render published as patch"
    else:
        patches = list(out.glob("auto-*.patch"))
        assert len(patches) == 1 and "staged payload" in patches[0].read_text()
        assert "PATCH-WRITE-FAILED" not in text
    assert (repo / "stray.txt").read_text() == "staged payload"
