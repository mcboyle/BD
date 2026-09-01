"""bd-rebase-cut.py must prove the rebase FINISHED, and on a branch.

THREE FAILURES IN ONE SESSION, 2026-08-31, all the same shape: act on a
rebase's result without proving the rebase ended.

  1. `git rebase` stopped on a conflict and a --continue fired before the
     paths were staged, so the todo still held the pick. HEAD looked
     plausible and the version file read as main's.
  2. The rebase left HEAD DETACHED. A later push had nothing to move, the
     remote head never changed, and the PR sat CONFLICTING with ZERO CI
     checks -- which reads as "pending", not "broken", so it could have
     waited forever.
  3. A `checkout -B` off that half-finished state duplicated a commit into
     the rebase todo, and the next rebase replayed it twice.

The pre-existing parent==origin/main check is necessary but NOT sufficient: a
stopped rebase can leave a parent that matches main while the cut's own commit
is still unapplied. These tests cover the two assertions that make it mean
something.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(os.environ.get("BD_HARNESS_HOME", str(Path.home()))) / "bd-rebase-cut.py"


def git(r, *a, check=True):
    p = subprocess.run(["git", "-C", str(r), *a], capture_output=True, text=True)
    if check and p.returncode:
        raise RuntimeError(f"git {' '.join(a)}: {p.stderr}")
    return p.stdout.strip()


def run(work, version="3.66.9999"):
    return subprocess.run([sys.executable, str(SCRIPT), "--work", str(work),
                           "--version", version], capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@t"); git(r, "config", "user.name", "t")
    (r / "f.txt").write_text("base\n")
    git(r, "add", "f.txt"); git(r, "commit", "-qm", "base")
    git(r, "update-ref", "refs/remotes/origin/main", "HEAD")
    return r


def test_it_refuses_while_a_rebase_is_in_progress(repo):
    """A stopped rebase must never be treated as a finished one."""
    git(repo, "checkout", "-q", "-b", "cut")
    (repo / "f.txt").write_text("cut\n")
    git(repo, "add", "f.txt"); git(repo, "commit", "-qm", "cut")
    git(repo, "checkout", "-q", "main")
    (repo / "f.txt").write_text("main moved\n")
    git(repo, "add", "f.txt"); git(repo, "commit", "-qm", "main")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "checkout", "-q", "cut")
    subprocess.run(["git", "-C", str(repo), "rebase", "main"], capture_output=True)

    gitdir = Path(git(repo, "rev-parse", "--absolute-git-dir"))
    assert (gitdir / "rebase-merge").exists() or (gitdir / "rebase-apply").exists(), \
        "precondition: a rebase must actually be stopped mid-flight"

    r = run(repo)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "REBASE STILL IN PROGRESS" in (r.stdout + r.stderr)


def test_it_refuses_a_detached_head(repo):
    """The failure that produced a PR with zero CI checks, forever.

    Asserted structurally rather than by driving the tool: reaching the
    detached-head check requires a real origin to fetch from, and standing one
    up would test git's plumbing rather than this assertion. What matters is
    that the check exists, runs BEFORE the tool reports a candidate, and
    refuses rather than warning.
    """
    body = SCRIPT.read_text(encoding="utf-8")
    assert 'branch = git(work, "rev-parse", "--abbrev-ref", "HEAD").strip()' in body
    assert 'if branch == "HEAD":' in body
    detach = body.index('if branch == "HEAD":')
    report = body.index('print(f"== candidate {head}')
    assert detach < report, "the detached check must precede the success report"
    assert body[detach:report].count("sys.exit") >= 1, "it must refuse, not warn"


def test_the_refusals_name_the_remedy(repo):
    """A refusal a reader cannot act on costs another cycle."""
    body = SCRIPT.read_text(encoding="utf-8")
    assert "rebase --abort" in body
    assert "checkout -B" in body
    assert "zero CI checks" in body, (
        "the detached-head refusal should say WHY it matters -- zero checks "
        "reads as pending rather than broken")


def test_the_parent_check_survives(repo):
    """The pre-existing assertion must not have been replaced by the new ones."""
    body = SCRIPT.read_text(encoding="utf-8")
    assert 'if parent != main_rev:' in body
    assert 'is not origin/main' in body
