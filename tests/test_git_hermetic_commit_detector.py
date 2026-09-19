"""tests/test_git_hermetic_commit_detector.py -- regression suite for bd-git-hermetic-check.

Verifies detection of non-hermetic git commit calls in test fixtures and scripts,
ensuring compliance with Fleet Rule 41 (Hermetic Git Fixtures) and Fleet Rule 42
(Static Census Substring Guards).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

REPO_ROOT = Path(__file__).resolve().parent.parent
DETECTOR = REPO_ROOT / "toolchain" / "bin" / "bd-git-hermetic-check"


def _run_detector(*args: str | Path) -> subprocess.CompletedProcess[str]:
    """Execute bd-git-hermetic-check with python interpreter."""
    cmd = [sys.executable, str(DETECTOR)] + [str(a) for a in args]
    return subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def test_detector_executable_exists():
    """Verify toolchain/bin/bd-git-hermetic-check exists and is executable."""
    assert DETECTOR.is_file(), f"Detector missing at {DETECTOR}"
    assert os.access(DETECTOR, os.X_OK), f"Detector is not executable: {DETECTOR}"


def test_detector_internal_selftest():
    """Verify the detector's internal self-test battery executes and passes."""
    cp = _run_detector("--selftest")
    assert cp.returncode == 0, f"Selftest failed:\n{cp.stdout}\n{cp.stderr}"
    assert "SELFTEST PASS" in cp.stdout


# --------------------------------------------------------------------------- #
# Negative Mutant Tests (Unhermetic git commit invocations must be flagged)    #
# --------------------------------------------------------------------------- #


def test_negative_mutant_author_without_committer_identity(tmp_path: Path):
    """PR #900 failure signature: --author provided without -c user.name/email or committer env."""
    mutant = tmp_path / "mutant_author.py"
    mutant.write_text(
        'import subprocess\n\n'
        'def _detached_gate_clone(dest):\n'
        '    subprocess.run([\n'
        '        "git", "commit", "-q", "-m", "init",\n'
        '        "--author=Test <test@example.com>"\n'
        '    ], cwd=str(dest), check=True)\n',
        encoding="utf-8",
    )

    cp = _run_detector(mutant)
    assert cp.returncode == 1, (
        f"Expected non-zero exit code 1 for unhermetic --author commit, got {cp.returncode}.\n"
        f"Stdout:\n{cp.stdout}\nStderr:\n{cp.stderr}"
    )
    combined = cp.stdout + cp.stderr
    assert "VIOLATION [FG-GIT-HERMETIC-COMMIT]" in combined
    assert "--author" in combined or "committer identity" in combined
    assert "mutant_author.py:4" in combined


def test_negative_mutant_bare_subprocess_commit(tmp_path: Path):
    """Bare git commit without any committer identity flags or environment."""
    mutant = tmp_path / "mutant_bare.py"
    mutant.write_text(
        'import subprocess\n\n'
        'def test_repo_setup(repo):\n'
        '    subprocess.run(["git", "commit", "-m", "unhermetic commit"], cwd=repo)\n',
        encoding="utf-8",
    )

    cp = _run_detector(mutant)
    assert cp.returncode == 1, f"Expected detector failure, got exit code {cp.returncode}"
    combined = cp.stdout + cp.stderr
    assert "VIOLATION [FG-GIT-HERMETIC-COMMIT]" in combined
    assert "mutant_bare.py:4" in combined


def test_negative_mutant_string_command_shell(tmp_path: Path):
    """String command executed in shell without explicit committer configuration."""
    mutant = tmp_path / "mutant_shell.py"
    mutant.write_text(
        'import subprocess\n\n'
        'def test_shell(repo):\n'
        '    subprocess.run("git commit -m \\"shell unhermetic\\"", shell=True, cwd=repo)\n',
        encoding="utf-8",
    )

    cp = _run_detector(mutant)
    assert cp.returncode == 1, f"Expected detector failure for shell command, got {cp.returncode}"
    combined = cp.stdout + cp.stderr
    assert "VIOLATION [FG-GIT-HERMETIC-COMMIT]" in combined
    assert "mutant_shell.py:4" in combined


def test_negative_mutant_assigned_command_list(tmp_path: Path):
    """Command defined in a local variable then passed to subprocess.run."""
    mutant = tmp_path / "mutant_cmd_var.py"
    mutant.write_text(
        'import subprocess\n\n'
        'def helper(target):\n'
        '    cmd = ["git", "commit", "-qm", "checkpoint"]\n'
        '    subprocess.check_call(cmd, cwd=target)\n',
        encoding="utf-8",
    )

    cp = _run_detector(mutant)
    assert cp.returncode == 1, f"Expected detector failure for assigned command, got {cp.returncode}"
    combined = cp.stdout + cp.stderr
    assert "VIOLATION [FG-GIT-HERMETIC-COMMIT]" in combined


# --------------------------------------------------------------------------- #
# Positive Control Tests (Properly hermetic git commits pass cleanly)        #
# --------------------------------------------------------------------------- #


def test_positive_control_explicit_inline_config(tmp_path: Path):
    """PR #900 permanent remedy: passing explicit -c user.name and -c user.email in git argv."""
    control = tmp_path / "control_inline.py"
    control.write_text(
        'import subprocess\n\n'
        'def _detached_gate_clone(dest):\n'
        '    subprocess.run([\n'
        '        "git", "-c", "user.name=Test", "-c", "user.email=test@example.com",\n'
        '        "commit", "-q", "-m", "init"\n'
        '    ], cwd=str(dest), check=True)\n',
        encoding="utf-8",
    )

    cp = _run_detector(control)
    assert cp.returncode == 0, (
        f"Expected exit code 0 for hermetic inline config, got {cp.returncode}.\n"
        f"Stdout:\n{cp.stdout}\nStderr:\n{cp.stderr}"
    )
    assert "0 non-hermetic git commit violations found" in cp.stdout


def test_positive_control_environment_committer_vars(tmp_path: Path):
    """Setting GIT_COMMITTER_NAME and GIT_COMMITTER_EMAIL in passed env dict."""
    control = tmp_path / "control_env.py"
    control.write_text(
        'import os, subprocess\n\n'
        'def test_repo_fixture(repo):\n'
        '    env = dict(os.environ,\n'
        '               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",\n'
        '               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")\n'
        '    subprocess.run(["git", "commit", "-m", "hermetic env"], env=env, cwd=repo)\n',
        encoding="utf-8",
    )

    cp = _run_detector(control)
    assert cp.returncode == 0, (
        f"Expected exit code 0 for hermetic env vars, got {cp.returncode}.\n"
        f"Stdout:\n{cp.stdout}\nStderr:\n{cp.stderr}"
    )


def test_positive_control_git_plumbing_commit_tree(tmp_path: Path):
    """Plumbing command git commit-tree does not require commit flags and must not be flagged."""
    control = tmp_path / "control_plumbing.py"
    control.write_text(
        'import subprocess\n\n'
        'def make_tree(repo, tree_sha):\n'
        '    commit = subprocess.run(\n'
        '        ["git", "commit-tree", tree_sha, "-m", "fixture"],\n'
        '        cwd=repo, check=True, capture_output=True, text=True\n'
        '    )\n'
        '    return commit.stdout.strip()\n',
        encoding="utf-8",
    )

    cp = _run_detector(control)
    assert cp.returncode == 0, f"git commit-tree should not be flagged as unhermetic: {cp.stderr}"


# --------------------------------------------------------------------------- #
# Rule 42 Guard & Boundary Tests                                             #
# --------------------------------------------------------------------------- #


def test_rule_42_raw_substring_guard_skips_ast_overhead(tmp_path: Path):
    """Fleet Rule 42 requires cheap raw substring pre-filter before AST parsing.
    
    A file lacking 'commit' should bypass AST parsing entirely even if it contains
    broken Python syntax.
    """
    broken_syntax_file = tmp_path / "broken_syntax_no_commit.py"
    broken_syntax_file.write_text('def this_is_invalid_python_syntax(:\n', encoding="utf-8")

    cp = _run_detector(broken_syntax_file)
    assert cp.returncode == 0, (
        f"Rule 42 substring guard should skip file without 'commit' before AST parsing.\n"
        f"Got exit {cp.returncode}:\n{cp.stderr}"
    )


def test_unrelated_prose_containing_commit_is_not_flagged(tmp_path: Path):
    """Mentions of the word 'commit' in docstrings, variables, or assertions are not false positives."""
    clean_file = tmp_path / "clean_prose.py"
    clean_file.write_text(
        '"""This module manages transaction commit logic."""\n\n'
        'def test_transaction():\n'
        '    action = "commit"\n'
        '    log_message = "commit succeeded without errors"\n'
        '    assert action == "commit"\n',
        encoding="utf-8",
    )

    cp = _run_detector(clean_file)
    assert cp.returncode == 0, f"Unrelated prose containing 'commit' should not trigger violation: {cp.stderr}"


# --------------------------------------------------------------------------- #
# Self-Test Across Current Repository Target                                 #
# --------------------------------------------------------------------------- #


def test_self_test_desandbox_tool_verifiers_passes():
    """Verify that tests/test_desandbox_tool_verifiers.py in the current repo passes cleanly."""
    target = REPO_ROOT / "tests" / "test_desandbox_tool_verifiers.py"
    assert target.is_file(), f"Target test file not found at {target}"

    cp = _run_detector(target)
    assert cp.returncode == 0, (
        f"Expected tests/test_desandbox_tool_verifiers.py to pass hermetic check.\n"
        f"Exit code: {cp.returncode}\n"
        f"Stdout:\n{cp.stdout}\n"
        f"Stderr:\n{cp.stderr}"
    )
    assert "0 non-hermetic git commit violations found" in cp.stdout
