"""A reverted container reported a healthy tree, and the hook only ever said so.

@873. This container reverts to a 2026-07-28 base image on restart. FOUR times
now the checkout has reappeared at an old commit -- most recently at
v3.66.850 while origin/main carried v3.66.872, twenty-two versions later. Once,
a source read against that stale tree produced a confidently WRONG conclusion
about a fix that was present on main all along.

`.claude/hooks/session-start.sh` already detected it and printed a warning. It
deliberately never repaired, and the reason given was sound as far as it went:
the hook also fires on `resume` and `compact`, where the checkout is
legitimately ahead of origin and carries uncommitted mid-cut work, and a reset
there would destroy exactly what the session is doing.

But that put two different situations under one refusal. "The tree holds work I
would lose" and "the tree is a reverted image with nothing of its own" are
distinguishable, and only the first is dangerous. The second is the one that
keeps happening.

So the repair is now gated on PROVABLE LOSSLESSNESS rather than on the hook's
trigger source: no modified TRACKED files, and zero commits ahead of
origin/main. Both true and every byte a reset would discard is reachable from
origin/main by construction. Either false and it refuses and says WHICH -- a
repair that could eat a cut is worse than the rollback it fixes.

Untracked files are deliberately NOT counted. `git reset --hard` does not touch
them, so they cannot be lost by the repair, and counting them would refuse the
common case for no safety gain.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / ".claude" / "hooks" / "session-start.sh"


def _git(*args, cwd, check=True):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, check=check, timeout=120)


def _origin_and_clone(tmp_path: Path, behind_by: int = 3):
    """A bare 'origin' with N commits, and a clone parked at the FIRST one.

    PARTIAL, and @879 measured how. This rewinds HEAD but leaves
    refs/remotes/origin/main at the true tip, so the clone already knows where
    main is and the hook's `git fetch` is unconstrained -- delete the fetch and
    every test below still passes. A real image reversion takes the whole .git
    directory back, tracking refs included, which is precisely the state in
    which a swallowed fetch failure makes a rollback invisible.

    tests/test_v3_66_879_provision_trigger_sees_its_subject.py carries the
    faithful fixture (`faithful=True` also does update-ref on the tracking ref)
    and the assertions that constrain the fetch. Left partial here rather than
    changed, so the two files cover different states on purpose.
    """
    origin = tmp_path / "origin"
    work = tmp_path / "seed"
    work.mkdir(parents=True)
    _git("init", "-q", "-b", "main", ".", cwd=work)
    _git("config", "user.email", "a@b.c", cwd=work)
    _git("config", "user.name", "t", cwd=work)
    (work / "src.py").write_text("VERSION = 0\n")
    _git("add", "-A", cwd=work)
    _git("commit", "-qm", "c0", cwd=work)
    first = _git("rev-parse", "HEAD", cwd=work).stdout.strip()
    for i in range(1, behind_by + 1):
        (work / "src.py").write_text(f"VERSION = {i}\n")
        _git("commit", "-qam", f"c{i}", cwd=work)
    _git("clone", "-q", "--bare", str(work), str(origin), cwd=tmp_path)

    clone = tmp_path / "clone"
    _git("clone", "-q", str(origin), str(clone), cwd=tmp_path)
    _git("config", "user.email", "a@b.c", cwd=clone)
    _git("config", "user.name", "t", cwd=clone)
    _git("reset", "--hard", "-q", first, cwd=clone)      # the revert
    return origin, clone


def _run_hook(clone: Path, source: str = "startup"):
    env = dict(os.environ)
    env["CLAUDE_CODE_REMOTE"] = "true"
    env["CLAUDE_PROJECT_DIR"] = str(clone)
    env.pop("CLAUDE_ENV_FILE", None)
    return subprocess.run(["bash", str(HOOK)], cwd=str(clone), input='{"source":"%s"}' % source,
                          capture_output=True, text=True, timeout=300, env=env)


def _head(clone: Path) -> str:
    return _git("rev-parse", "HEAD", cwd=clone).stdout.strip()


def _tip(clone: Path) -> str:
    return _git("rev-parse", "origin/main", cwd=clone).stdout.strip()


def test_a_reverted_clean_checkout_is_repaired(tmp_path):
    """THE DEFECT. A checkout parked at an old commit with nothing local of its
    own is the reverted-image signature, and fast-forwarding it loses nothing.

    RED before the fix: the hook printed the warning and left HEAD where it was,
    so every subsequent source read in the session was against stale code.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    assert _head(clone) != _tip(clone), "the fixture is not actually behind"
    r = _run_hook(clone)
    assert _head(clone) == _tip(clone), (
        "a clean, strictly-behind checkout was left stale. stderr=%r" % r.stderr)
    assert "REPAIRED" in r.stderr, r.stderr
    assert (clone / "src.py").read_text().strip() == "VERSION = 3", (
        "the working tree still holds the old content")


def test_untracked_files_do_not_block_the_repair_and_survive_it(tmp_path):
    """`git reset --hard` does not touch untracked paths, so they are not part
    of the question. Counting them would refuse the common case -- a session
    almost always has scratch files -- for no safety gain at all."""
    _origin, clone = _origin_and_clone(tmp_path)
    (clone / "scratch.txt").write_text("agent scratch\n")
    r = _run_hook(clone)
    assert _head(clone) == _tip(clone), (
        "an untracked scratch file blocked a lossless repair. stderr=%r" % r.stderr)
    assert (clone / "scratch.txt").read_text() == "agent scratch\n", (
        "the repair destroyed an untracked file")


# --------------------------------------------------------------------------- #
# the over-sensitive direction: a repair that eats a cut is worse than the      #
# rollback it fixes. These are the assertions that keep the fix narrow.        #
# --------------------------------------------------------------------------- #

def test_modified_tracked_files_block_the_repair(tmp_path):
    """Uncommitted mid-cut work must never be reset away."""
    _origin, clone = _origin_and_clone(tmp_path)
    stale = _head(clone)
    (clone / "src.py").write_text("VERSION = 0\nhalf-written work\n")
    r = _run_hook(clone)
    assert _head(clone) == stale, (
        "the hook reset a tree with uncommitted tracked changes")
    assert "half-written work" in (clone / "src.py").read_text(), (
        "uncommitted work was destroyed by the repair")
    assert "NOT repairing" in r.stderr and "modified tracked files" in r.stderr, (
        "the refusal does not say WHY; an operator seeing only 'behind' will "
        "re-run the reset by hand and lose the work. stderr=%r" % r.stderr)


def test_local_commits_ahead_block_the_repair(tmp_path):
    """A committed-but-unpushed cut is exactly what `resume` looks like."""
    _origin, clone = _origin_and_clone(tmp_path)
    (clone / "mine.py").write_text("local cut\n")
    _git("add", "-A", cwd=clone)
    _git("commit", "-qm", "my unpushed cut", cwd=clone)
    mine = _head(clone)
    r = _run_hook(clone)
    assert _head(clone) == mine, "the hook discarded an unpushed local commit"
    assert (clone / "mine.py").exists(), "the local commit's file is gone"
    assert "NOT repairing" in r.stderr and "ahead" in r.stderr, r.stderr


def test_a_current_checkout_is_silent(tmp_path):
    """Silence is the signal. A hook that speaks when nothing is wrong trains
    the reader to skip it, and then the one real warning goes unread."""
    _origin, clone = _origin_and_clone(tmp_path)
    _git("reset", "--hard", "-q", "origin/main", cwd=clone)
    r = _run_hook(clone)
    assert "behind origin/main" not in r.stderr, r.stderr
    assert "REPAIRED" not in r.stderr, (
        "the hook 'repaired' a checkout that was already current: %r" % r.stderr)


def test_the_repair_is_not_gated_on_the_hook_source(tmp_path):
    """The predicate is losslessness, not the trigger.

    A rollback can land at any point in a session, so gating the repair on
    source=startup would leave the compact/resume case -- which is when the
    container actually restarted this session -- permanently unrepaired. The
    mid-session safety concern is already covered by the two blocks above:
    real mid-cut work is either modified or committed, and both refuse.
    """
    for source in ("compact", "resume", "clear"):
        sub = tmp_path / source
        sub.mkdir()
        _origin, clone = _origin_and_clone(sub)
        r = _run_hook(clone, source=source)
        assert _head(clone) == _tip(clone), (
            "source=%s left a lossless rollback unrepaired. stderr=%r"
            % (source, r.stderr))
