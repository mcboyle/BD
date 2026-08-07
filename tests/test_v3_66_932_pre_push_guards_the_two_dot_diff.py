"""v3.66.932 (item 30): a pre-push hook enforcing CLAUDE.md section 7's
two-dot diff before a force-push can discard unmerged work.

THE FAILURE IT GUARDS. A squash merge writes a NEW commit on main, so a topic
branch never follows it and `origin/<branch>` still points at the pre-squash
commit. The next ordinary push is rejected non-fast-forward, and the tempting
reflex -- `--force` -- is the one that can discard someone else's work.
Section 7's rule is that `git diff --stat origin/main origin/<branch>` must be
EMPTY first: empty means the remote branch carries nothing main lacks, so
replacing it loses nothing. Non-empty means stop.

SCOPED TIGHTLY ON PURPOSE. GitHub's auto-delete-head-branches has been on
since 2026-08-01, so the stale-ref case is largely gone and a hook that fired
on ordinary pushes would be trained past within a day -- CLAUDE.md section 0
counts over-sensitivity as a soundness bug equal to a false clean. It
therefore says nothing at all unless the push is NON-FAST-FORWARD, which is
exactly the case where --force is the only way through.

ANCESTRY IN A SHALLOW CLONE IS THE TRAP, and this container is one (depth 50).
CLAUDE.md section 5: only `--is-ancestor` exit 0 is trustworthy -- a 1 means
"not in this history" and a 128 means "I cannot see it", and conflating them
is a gate firing on its own blindness. The polarity here is deliberate: exit 0
short-circuits to ALLOW because a found path cannot be faked, and every
nonzero falls through to the more careful two-dot check rather than being read
as a verdict.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_HOOK = _REPO / ".githooks" / "pre-push"

_ZERO = "0" * 40


def _git(cwd, *args, check=True):
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                       text=True)
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} -> {r.returncode}\n"
                             f"{r.stdout}{r.stderr}")
    return r.stdout.strip()


def _repo(tmp_path):
    """A real repo with a real refs/remotes/origin/main.

    update-ref rather than a bare remote and a push: the hook consults local
    refs and its stdin, so a fabricated remote-tracking ref exercises exactly
    what it reads, with no network and no second repository to keep in sync.
    """
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / "a.txt").write_text("base\n")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-qm", "base")
    _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    return root


def _run_hook(root, lines, env=None):
    """Drive the hook exactly as git does: refs on stdin, remote in argv."""
    e = dict(os.environ)
    e.pop("BD_SKIP_PREPUSH_CHECK", None)
    e.update(env or {})
    return subprocess.run(
        ["bash", str(_HOOK), "origin", "git@github.com:mcboyle/BD.git"],
        cwd=str(root), input="\n".join(lines) + "\n",
        capture_output=True, text=True, env=e, timeout=60)


def test_the_hook_exists_and_is_executable():
    assert _HOOK.is_file(), "no .githooks/pre-push"
    assert os.access(_HOOK, os.X_OK), "pre-push is not executable"


# ── the case it exists for ────────────────────────────────────────────

def test_a_force_push_over_unmerged_work_is_refused(tmp_path):
    """origin/<branch> carries a commit main does not have. Overwriting it
    loses that work, which is precisely what section 7 forbids."""
    root = _repo(tmp_path)
    _git(root, "checkout", "-qb", "topic")
    (root / "unmerged.txt").write_text("work nobody merged\n")
    _git(root, "add", "unmerged.txt")
    _git(root, "commit", "-qm", "unmerged work")
    remote_tip = _git(root, "rev-parse", "HEAD")
    _git(root, "update-ref", "refs/remotes/origin/topic", remote_tip)

    # Now rewrite history so the push is non-fast-forward.
    _git(root, "reset", "-q", "--hard", "HEAD~1")
    (root / "other.txt").write_text("different work\n")
    _git(root, "add", "other.txt")
    _git(root, "commit", "-qm", "replacement")
    local_tip = _git(root, "rev-parse", "HEAD")

    r = _run_hook(root, [f"refs/heads/topic {local_tip} "
                         f"refs/heads/topic {remote_tip}"])

    assert r.returncode != 0, (
        "a force-push that would discard an unmerged commit was allowed\n"
        + r.stdout + r.stderr)
    assert "topic" in (r.stdout + r.stderr)


# ── everything else must stay silent ──────────────────────────────────

def test_a_fast_forward_push_is_allowed(tmp_path):
    """The overwhelmingly common case. It must cost nothing and say
    nothing, or the hook gets switched off."""
    root = _repo(tmp_path)
    _git(root, "checkout", "-qb", "topic")
    remote_tip = _git(root, "rev-parse", "HEAD")
    _git(root, "update-ref", "refs/remotes/origin/topic", remote_tip)
    (root / "b.txt").write_text("more\n")
    _git(root, "add", "b.txt")
    _git(root, "commit", "-qm", "more")
    local_tip = _git(root, "rev-parse", "HEAD")

    r = _run_hook(root, [f"refs/heads/topic {local_tip} "
                         f"refs/heads/topic {remote_tip}"])

    assert r.returncode == 0, (r.stdout + r.stderr)
    assert (r.stdout + r.stderr).strip() == "", (
        f"a fast-forward push produced output: {r.stdout + r.stderr!r}")


def test_adding_commits_to_an_open_branch_is_allowed(tmp_path):
    """THE EVERYDAY CASE, and the one most likely to be broken by an
    over-eager fix: pushing more commits to a topic branch that already
    carries unmerged work. The two-dot diff against main is NON-empty here
    -- that is normal for any open PR -- but the push is a fast-forward and
    discards nothing, so it must be allowed. A hook that consulted the diff
    without checking ancestry first would refuse every push to every open
    branch in the repository."""
    root = _repo(tmp_path)
    _git(root, "checkout", "-qb", "topic")
    (root / "feature.txt").write_text("unmerged feature\n")
    _git(root, "add", "feature.txt")
    _git(root, "commit", "-qm", "feature")
    remote_tip = _git(root, "rev-parse", "HEAD")
    _git(root, "update-ref", "refs/remotes/origin/topic", remote_tip)
    (root / "feature2.txt").write_text("more of it\n")
    _git(root, "add", "feature2.txt")
    _git(root, "commit", "-qm", "more feature")
    local_tip = _git(root, "rev-parse", "HEAD")

    # Precondition: the diff main..origin/topic is genuinely non-empty, so
    # this test cannot pass for the trivial reason.
    assert _git(root, "diff", "--stat", "refs/remotes/origin/main",
                remote_tip) != ""

    r = _run_hook(root, [f"refs/heads/topic {local_tip} "
                         f"refs/heads/topic {remote_tip}"])

    assert r.returncode == 0, (
        "a fast-forward push to an open branch was refused; this would block "
        "every push to every open PR\n" + r.stdout + r.stderr)


def test_a_force_push_whose_content_is_already_merged_is_allowed(tmp_path):
    """The squash-merge case section 7 describes. The remote branch's
    content is identical to main, so replacing it loses nothing -- and
    refusing here would make the hook useless noise."""
    root = _repo(tmp_path)
    _git(root, "checkout", "-qb", "topic")
    # Same tree as main, different commit -- exactly what a squash produces.
    _git(root, "commit", "-q", "--allow-empty", "-m", "squashed already")
    remote_tip = _git(root, "rev-parse", "HEAD")
    _git(root, "update-ref", "refs/remotes/origin/topic", remote_tip)
    _git(root, "reset", "-q", "--hard", "refs/remotes/origin/main")
    (root / "new.txt").write_text("fresh\n")
    _git(root, "add", "new.txt")
    _git(root, "commit", "-qm", "fresh work")
    local_tip = _git(root, "rev-parse", "HEAD")

    r = _run_hook(root, [f"refs/heads/topic {local_tip} "
                         f"refs/heads/topic {remote_tip}"])

    assert r.returncode == 0, (
        "the two-dot diff was EMPTY -- the remote branch carried nothing main "
        "lacks -- and the push was still refused\n" + r.stdout + r.stderr)


def test_a_brand_new_branch_is_allowed(tmp_path):
    """No remote ref yet; there is nothing that could be discarded."""
    root = _repo(tmp_path)
    _git(root, "checkout", "-qb", "topic")
    local_tip = _git(root, "rev-parse", "HEAD")
    r = _run_hook(root, [f"refs/heads/topic {local_tip} "
                         f"refs/heads/topic {_ZERO}"])
    assert r.returncode == 0, (r.stdout + r.stderr)


def test_a_branch_deletion_is_allowed(tmp_path):
    """Deleting is the operator saying so explicitly.

    The branch deliberately carries UNMERGED work: with content identical to
    main the two-dot diff is empty and the push is allowed whether or not
    deletions are skipped, so the test would pass over a hook that had lost
    the skip entirely. Measured -- that exact escape was reported by
    bd-mutate against the first version of this test.
    """
    root = _repo(tmp_path)
    _git(root, "checkout", "-qb", "topic")
    (root / "unmerged.txt").write_text("never merged\n")
    _git(root, "add", "unmerged.txt")
    _git(root, "commit", "-qm", "unmerged")
    tip = _git(root, "rev-parse", "HEAD")
    _git(root, "update-ref", "refs/remotes/origin/topic", tip)
    assert _git(root, "diff", "--stat", "refs/remotes/origin/main", tip) != ""

    r = _run_hook(root, [f"(delete) {_ZERO} refs/heads/topic {tip}"])
    assert r.returncode == 0, (
        "deleting a branch was refused; deletion is explicit operator "
        "intent, not an accidental overwrite\n" + r.stdout + r.stderr)


def test_the_override_is_honoured(tmp_path):
    """One named escape hatch, matching pre-commit's BD_SKIP_CLAIM_CHECK."""
    root = _repo(tmp_path)
    _git(root, "checkout", "-qb", "topic")
    (root / "unmerged.txt").write_text("work\n")
    _git(root, "add", "unmerged.txt")
    _git(root, "commit", "-qm", "unmerged")
    remote_tip = _git(root, "rev-parse", "HEAD")
    _git(root, "update-ref", "refs/remotes/origin/topic", remote_tip)
    _git(root, "reset", "-q", "--hard", "HEAD~1")
    _git(root, "commit", "-q", "--allow-empty", "-m", "replacement")
    local_tip = _git(root, "rev-parse", "HEAD")

    r = _run_hook(root,
                  [f"refs/heads/topic {local_tip} "
                   f"refs/heads/topic {remote_tip}"],
                  env={"BD_SKIP_PREPUSH_CHECK": "1"})
    assert r.returncode == 0, (r.stdout + r.stderr)


def test_a_missing_origin_main_is_unknown_and_refuses(tmp_path):
    """Section 0's third state. Without a remote-tracking main the two-dot
    diff cannot be computed, and "the checker could not run" is not "there
    is nothing to lose". The push must be NON-fast-forward for this to be
    reached at all -- otherwise the ancestry short-circuit allows it and the
    test would pass over a hook that never ran its own check."""
    root = tmp_path / "nomain"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / "a.txt").write_text("x\n")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-qm", "base")
    base = _git(root, "rev-parse", "HEAD")

    _git(root, "checkout", "-qb", "topic")
    _git(root, "commit", "-q", "--allow-empty", "-m", "remote side")
    remote_tip = _git(root, "rev-parse", "HEAD")
    _git(root, "update-ref", "refs/remotes/origin/topic", remote_tip)
    # Diverge: remote_tip is NOT an ancestor of local_tip.
    _git(root, "reset", "-q", "--hard", base)
    _git(root, "commit", "-q", "--allow-empty", "-m", "local side")
    local_tip = _git(root, "rev-parse", "HEAD")
    # No refs/remotes/origin/main anywhere in this repo.

    r = _run_hook(root, [f"refs/heads/topic {local_tip} "
                         f"refs/heads/topic {remote_tip}"])

    assert r.returncode != 0, (
        "the two-dot diff could not be computed and the push was allowed "
        "anyway; unknown is not safe\n" + r.stdout + r.stderr)
    assert "UNKNOWN" in (r.stdout + r.stderr)


def test_the_hook_is_valid_bash():
    r = subprocess.run(["bash", "-n", str(_HOOK)], capture_output=True,
                       text=True)
    assert r.returncode == 0, r.stderr
