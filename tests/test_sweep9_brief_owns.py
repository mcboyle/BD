import os
from pathlib import Path
import subprocess

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_SWEEP9_BRIEF_OWNS_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, text=True,
        capture_output=True,
    ).stdout.strip()


@pytest.fixture
def base_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "known.py").write_text("value = 1\n")
    git(repo, "add", "known.py")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com",
        "commit", "-qm", "fixture")
    base = git(repo, "rev-parse", "HEAD")
    assert git(repo, "ls-tree", "--name-only", base) == "known.py"
    (repo / "untracked.py").write_text("value = 2\n")
    return repo, base


def lint(base_repo, tmp_path, owns, base=None, bold=False):
    candidate = Path(CANDIDATE)
    assert candidate.is_absolute() and candidate.is_file()
    assert os.access(candidate, os.X_OK)
    repo, original_base = base_repo
    base = base or original_base
    labels = ("**Base**:", "**OWNS**:") if bold else ("BASE:", "OWNS:")
    brief = tmp_path / "brief.md"
    brief.write_text(f"{labels[0]} {base}\n{labels[1]} {owns}\nAcceptance: fixtures pass.\n")
    return subprocess.run(
        ["bash", str(candidate), str(brief)], cwd=repo,
        env={**os.environ, "BD_BRIEF_REPO": str(repo)},
        text=True, capture_output=True,
    )


@pytest.mark.parametrize("owns", ["missing.py", "untracked.py", "known.py; missing.py"])
def test_absent_at_base_is_refused(base_repo, tmp_path, owns):
    result = lint(base_repo, tmp_path, owns)
    assert result.returncode == 3, f"OWNS-MISSING expected rc3: {result.stdout} {result.stderr}"
    assert "OWNS path absent at BASE:" in result.stderr


@pytest.mark.parametrize("owns", ["known.py", "known.py; new.py (new)", "NEW new.py", "`known.py`", "new.py (NEW)"])
def test_existing_and_explicit_new_are_accepted(base_repo, tmp_path, owns):
    result = lint(base_repo, tmp_path, owns)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("BRIEF-LINT OK") == 1


def test_bold_fields_keep_base_validation(base_repo, tmp_path):
    result = lint(base_repo, tmp_path, "missing.py", bold=True)
    assert result.returncode == 3, f"OWNS-MISSING bold fields: {result.stdout}"
    assert "OWNS path absent at BASE: missing.py" in result.stderr


def test_base_is_not_worktree_or_head(base_repo, tmp_path):
    repo, base = base_repo
    git(repo, "add", "untracked.py")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com",
        "commit", "-qm", "later")
    result = lint(base_repo, tmp_path, "untracked.py", base=base)
    assert result.returncode == 3, f"OWNS-MISSING historical BASE: {result.stdout}"
    assert "OWNS path absent at BASE: untracked.py" in result.stderr


def test_invalid_base_is_refused(base_repo, tmp_path):
    result = lint(base_repo, tmp_path, "known.py", base="0" * 40)
    assert result.returncode == 3, f"BASE-UNKNOWN expected rc3: {result.stdout}"
    assert "BASE cannot resolve:" in result.stderr


def test_new_marker_does_not_cover_other_paths(base_repo, tmp_path):
    result = lint(base_repo, tmp_path, "new.py (new); missing.py")
    assert result.returncode == 3, f"OWNS-MISSING per-path NEW: {result.stdout}"
    assert "OWNS path absent at BASE: missing.py" in result.stderr


def test_empty_owns_is_refused(base_repo, tmp_path):
    result = lint(base_repo, tmp_path, "")
    assert result.returncode == 3, f"OWNS-EMPTY expected rc3: {result.stdout}"
    assert "OWNS has no paths" in result.stderr


@pytest.mark.parametrize("owns", ["../escape.py (new)", "/tmp/escape.py (new)", "known.py unexpected"])
def test_invalid_path_is_refused(base_repo, tmp_path, owns):
    result = lint(base_repo, tmp_path, owns)
    assert result.returncode == 3
    assert "OWNS invalid path:" in result.stderr


def test_explicit_sha_after_origin_main_is_used(base_repo, tmp_path):
    result = lint(base_repo, tmp_path, "known.py", base=f"origin/main {base_repo[1]}")
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("BRIEF-LINT OK") == 1


def test_missing_owns_keeps_original_floor(base_repo, tmp_path):
    result = lint(base_repo, tmp_path, "known.py")
    assert result.returncode == 0
    brief = tmp_path / "brief.md"
    brief.write_text(f"BASE: {base_repo[1]}\nAcceptance: fixtures pass.\n")
    result = subprocess.run(["bash", CANDIDATE, str(brief)], cwd=base_repo[0],
                            text=True, capture_output=True)
    assert result.returncode == 3
    assert "brief missing OWNS section" in result.stderr
