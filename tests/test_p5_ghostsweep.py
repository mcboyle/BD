import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_P5_GHOSTSWEEP_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


@pytest.fixture
def sweep(tmp_path):
    candidate = Path(CANDIDATE)
    assert candidate.is_file() and os.access(candidate, os.X_OK)
    review = tmp_path / "review"
    scratch = tmp_path / "scratch"
    review.mkdir()
    scratch.mkdir()
    content = review / "worktree"
    content.mkdir()
    (content / "valuable").write_text("retained content")
    (content / ".git").write_text("gitdir: /missing/metadata")
    (review / "dangling").symlink_to(tmp_path / "absent", target_is_directory=True)
    (review / "alive").symlink_to(content, target_is_directory=True)
    (review / "ordinary").write_text("preserve file")
    group = scratch / "group"
    group.mkdir()
    (group / "dangling").symlink_to("absent")
    (scratch / "alias").symlink_to(content, target_is_directory=True)
    (content / "inner-dangling").symlink_to("absent")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tmux = bin_dir / "tmux"
    tmux.write_text('#!/bin/sh\nprintf "%s\\n" "$TEST_PM_SEAT"\n')
    tmux.chmod(0o755)
    pm_file = tmp_path / "PM-SEAT"
    pm_file.write_text("fixture-pm\n")
    env = dict(os.environ, GHOST_SWEEP_REVIEW_WT=str(review),
               GHOST_SWEEP_SCRATCH=str(scratch), GHOST_SWEEP_PM_SEAT_FILE=str(pm_file),
               TEST_PM_SEAT="fixture-pm", TMUX="fixture", TMUX_PANE="%fixture", PATH=f"{bin_dir}:{os.environ['PATH']}")

    def invoke(*args, **overrides):
        return subprocess.run([str(candidate), *args], env={**env, **overrides},
                              text=True, capture_output=True, check=False)

    return invoke, review, scratch


def test_dry_run_discovers_without_mutation(sweep):
    invoke, review, scratch = sweep
    result = invoke()
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("WOULD-UNLINK ") == 2, "GHOST_DANGLING_NOT_DISCOVERED"
    assert (review / "dangling").is_symlink()
    assert (scratch / "group/dangling").is_symlink()


def test_pm_apply_unlinks_only_dangling_symlinks(sweep):
    invoke, review, scratch = sweep
    result = invoke("--apply")
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("UNLINKED ") == 2, "GHOST_DANGLING_NOT_REMOVED"
    assert not os.path.lexists(review / "dangling")
    assert not os.path.lexists(scratch / "group/dangling")
    assert (review / "worktree/valuable").read_text() == "retained content"
    assert (review / "ordinary").read_text() == "preserve file"
    assert (review / "worktree/.git").read_text() == "gitdir: /missing/metadata"
    assert (review / "alive").is_symlink()
    assert (review / "worktree/inner-dangling").is_symlink()
    assert (scratch / "alias").is_symlink()


def test_non_pm_apply_refused(sweep):
    invoke, review, _ = sweep
    result = invoke("--apply", TEST_PM_SEAT="fixture-worker")
    assert result.returncode == 2 and "REFUSED: PM-only apply" in result.stderr
    assert (review / "dangling").is_symlink()


@pytest.mark.parametrize("unknown", ["missing-file", "no-pane"])
def test_unknown_pm_refused(sweep, tmp_path, unknown):
    invoke, review, _ = sweep
    overrides = {"GHOST_SWEEP_PM_SEAT_FILE": str(tmp_path / "missing-pm")} if unknown == "missing-file" else {"TMUX_PANE": ""}
    result = invoke("--apply", **overrides)
    assert result.returncode == 2 and "REFUSED: PM-only apply" in result.stderr
    assert (review / "dangling").is_symlink()


def test_root_alias_refused_before_mutation(sweep, tmp_path):
    invoke, review, scratch = sweep
    alias = tmp_path / "root-alias"
    alias.symlink_to(scratch, target_is_directory=True)
    result = invoke("--apply", GHOST_SWEEP_SCRATCH=str(alias))
    assert result.returncode == 1 and "REFUSED: root" in result.stderr
    assert (review / "dangling").is_symlink()


def test_loop_is_unknown_not_dangling(sweep):
    invoke, review, _ = sweep
    (review / "loop").symlink_to("loop")
    result = invoke("--apply")
    assert result.returncode == 1 and "UNKNOWN:" in result.stderr
    assert (review / "loop").is_symlink()


def test_empty_population_reports_zero(sweep):
    invoke, _, _ = sweep
    assert invoke("--apply").returncode == 0
    result = invoke()
    assert result.returncode == 0
    assert "FOUND 0 dangling links" in result.stdout


def test_invalid_option_refused(sweep):
    invoke, review, _ = sweep
    result = invoke("--recursive")
    assert result.returncode == 2 and "usage:" in result.stderr
    assert (review / "dangling").is_symlink()
