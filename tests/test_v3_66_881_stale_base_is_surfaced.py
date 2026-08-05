"""A session branching from a snapshot-stale base produces a PR that reverts main.

@881, and it is the recurrence the operator identified: the cache mechanism in
CLAUDE.md section 5 does not fire once. The panel snapshots the filesystem after
a cache build and every later session starts from that snapshot, so ~7 days after
each rebuild the checkout is stale again. The rebuild resets content to today and
then re-bakes it, so this is a CYCLE, not an incident.

@873 and @879 already reconverge the repo when the checkout is on `main`, clean,
and strictly behind -- that is the common case and it is handled. What was NOT
handled is the case that silently destroys work:

    HEAD is a snapshot-era commit, an agent branches from it, commits, and opens
    a PR. GitHub diffs that branch against current main, so every commit merged
    since the snapshot appears in the diff as a REMOVAL. The PR looks like a
    feature and reads as a revert of a week's work.

The hook refused to move a topic branch or a detached HEAD -- correctly, that is
@879's deliberate protection of a chosen position -- but it said only "NOT
repairing", one line among the session's startup noise. A refusal that does not
name its consequence is a refusal the reader scrolls past.

So the refusal now emits a distinct STALE BASE block naming the number of commits
and the specific harm. And the repair uses `merge --ff-only` rather than
`reset --hard`: `--ff-only` REFUSES on divergence instead of moving the tree
anyway, so the guard is enforced by git rather than only by the predicate that
selected the branch. That swap is NOT behaviour-preserving -- it opened one
corridor, closed here in the same cut: an untracked file at a path origin/main
tracks makes the fast-forward refuse where reset would have succeeded by
deleting it, and the refusal must be NAMED or "Repairing." reads as success.
"""
from __future__ import annotations

from pathlib import Path

from shell_source import shell_code_only

# The @879 suite owns the origin/clone fixture and the hook runner; importing
# them keeps ONE definition of "a faithfully rolled-back checkout". A third
# hand-rolled copy is how the shell-reader helpers went wrong three times.
from test_v3_66_879_provision_trigger_sees_its_subject import (
    _git, _head, _origin_and_clone, _run_hook, _tip,
)

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / ".claude" / "hooks" / "session-start.sh"

_MARKER = "STALE BASE"


def test_a_topic_branch_on_a_stale_base_is_loudly_surfaced(tmp_path):
    """THE DEFECT. This is the state that produces a reverting PR, and the hook
    is the only thing that sees it before work begins."""
    _origin, clone = _origin_and_clone(tmp_path)
    _git("checkout", "-q", "-b", "feature-x", cwd=clone)
    parked = _head(clone)
    r = _run_hook(clone, source="startup")
    assert _head(clone) == parked, (
        "@879's protection regressed -- the topic branch was moved")
    assert _MARKER in r.stderr, (
        "a topic branch on a stale base produced no distinct warning, so the "
        "session proceeds and its PR reverts everything merged since the "
        "snapshot. stderr=%r" % r.stderr)
    assert "revert" in r.stderr.lower(), (
        "the warning does not name the CONSEQUENCE. 'behind origin/main' reads "
        "as routine; 'your PR will revert N commits' does not. stderr=%r"
        % r.stderr)


def test_a_detached_stale_base_is_loudly_surfaced(tmp_path):
    """Section 2b tells agents to detach before measuring, so a detached HEAD is
    routine -- which is exactly why a stale one must be named rather than moved."""
    _origin, clone = _origin_and_clone(tmp_path)
    _git("checkout", "-q", "--detach", cwd=clone)
    parked = _head(clone)
    r = _run_hook(clone, source="startup")
    assert _head(clone) == parked, "a detached HEAD was moved"
    assert _MARKER in r.stderr, r.stderr


def test_the_warning_states_how_far_behind(tmp_path):
    """Magnitude decides the response. Two commits behind is a nuisance; sixty
    is a session that must stop and re-derive before touching anything."""
    _origin, clone = _origin_and_clone(tmp_path, behind_by=3)
    _git("checkout", "-q", "-b", "feature-y", cwd=clone)
    r = _run_hook(clone, source="startup")
    # Scoped to the BLOCK, not to stderr. The unscoped version passed on
    # pristine source, because an unrelated pre-existing line already said
    # "3 commit(s) behind" -- a test that is green the moment it is written has
    # proven nothing (CLAUDE.md section 2).
    block = [l for l in r.stderr.splitlines() if _MARKER in l]
    assert block, "no STALE BASE block at all. stderr=%r" % r.stderr
    # "3 commit", not a bare "3". The bare-digit version ESCAPED its mutant:
    # the block prints two short SHAs, which are hex and very often contain the
    # digit, so the assertion passed on a coincidental sha match with the count
    # removed from both lines. An assertion whose subject can be satisfied by
    # unrelated text in the same line is not measuring what it names.
    assert any("3 commit" in l for l in block), (
        "the STALE BASE block does not say how far behind the base is; "
        "magnitude is what decides whether a session stops. block=%r" % block)


# --------------------------------------------------------------------------- #
# the over-sensitive direction                                                 #
# --------------------------------------------------------------------------- #

def test_a_current_checkout_emits_no_stale_base_block(tmp_path):
    """Silence is the signal. A block that appears on every session start is one
    the reader learns to skip, and then the real one goes unread."""
    _origin, clone = _origin_and_clone(tmp_path)
    _git("reset", "--hard", "-q", "origin/main", cwd=clone)
    _git("checkout", "-q", "-b", "feature-z", cwd=clone)
    r = _run_hook(clone, source="startup")
    assert _MARKER not in r.stderr, (
        "a topic branch on a CURRENT base was reported stale. stderr=%r" % r.stderr)


def test_a_repaired_main_does_not_emit_the_block(tmp_path):
    """When the hook fixes it, there is nothing for the operator to act on."""
    _origin, clone = _origin_and_clone(tmp_path)
    r = _run_hook(clone, source="startup")
    assert _head(clone) == _tip(clone), "precondition: main was not repaired"
    assert _MARKER not in r.stderr, (
        "the block fired on a checkout the hook had just repaired. stderr=%r"
        % r.stderr)


# --------------------------------------------------------------------------- #
# the repair is enforced by git, not only by the predicate                     #
# --------------------------------------------------------------------------- #

def test_the_repair_is_fast_forward_only():
    """`reset --hard` moves the tree whatever the relationship between the two
    commits; `merge --ff-only` refuses unless the move is genuinely a
    fast-forward. With `ahead == 0` they agree EXCEPT where an untracked file
    sits at a path origin/main tracks -- reset deleted it and succeeded, ff
    refuses. See test_a_failed_repair_is_named_not_silent, which measures both.
    The swap makes git enforce the property the surrounding predicate asserts,
    so a future edit to that predicate cannot silently license a destructive
    move.
    """
    code = shell_code_only(HOOK)
    assert "merge --ff-only" in code, (
        "the repair does not use --ff-only, so the no-divergence guarantee "
        "rests entirely on the predicate that chose this branch")
    assert "reset --hard" not in code, (
        "a `reset --hard` survives on the repair path; it would move the tree "
        "even where a fast-forward is impossible")


def test_a_stale_but_dirty_main_gets_the_repair_refusal_not_the_branch_warning(tmp_path):
    """Closes a mutation escape. The block lives in the else-branch, so a mutant
    firing it unconditionally is invisible to every test that exercises the
    REPAIR path -- on main + clean the else-branch never runs at all.

    On main the right message is the existing "NOT repairing, a reset would
    discard work". The revert warning is about branching FROM a stale base and
    would be noise here, and noise is what makes the real block unreadable.
    """
    _origin, clone = _origin_and_clone(tmp_path)
    (clone / "src.py").write_text("VERSION = 0\nuncommitted\n")
    r = _run_hook(clone, source="startup")
    assert "NOT repairing" in r.stderr, r.stderr
    assert _MARKER not in r.stderr, (
        "the branch-revert warning fired on main, where it does not apply. "
        "stderr=%r" % r.stderr)


# --------------------------------------------------------------------------- #
# the corridor the --ff-only swap itself opened                                #
# --------------------------------------------------------------------------- #

def test_a_failed_repair_is_named_not_silent(tmp_path):
    """The swap is NOT behaviour-preserving, and this is the state that proves it.

    `dirty` is measured with --untracked-files=no, so untracked files are
    invisible to the predicate BY CONSTRUCTION. Let the snapshot carry an
    untracked, non-ignored file at a path a later main commit adds -- routine
    here, because the snapshot bakes generated files and committing one at that
    path later is ordinary. Then: stale, clean-by-predicate, on main, so the
    repair path is entered and "Repairing." prints.

    Measured: `merge --ff-only` REFUSES ("untracked working tree files would be
    overwritten"), HEAD does not move, the file survives. In the identical state
    `reset --hard` exits 0 -- by DELETING the untracked file, which is its
    documented behaviour. So the divergence favours --ff-only: reset succeeded
    lossily, ff refuses correctly.

    But correctly and SILENTLY, which is 881's own thesis extended one step. A
    refusal that does not name its consequence gets scrolled past; a repair that
    does not name its FAILURE gets read as success. The last relevant line the
    reader saw was "Repairing." -- REPAIRED never printed, cloud-setup never ran,
    and no STALE BASE fired because branch == main.
    """
    _origin, clone = _origin_and_clone(tmp_path, adds_late_file="gen.txt")
    (clone / "gen.txt").write_text("baked into the snapshot, untracked\n")
    stale = _head(clone)

    r = _run_hook(clone, source="startup")

    # The refusal itself is CORRECT -- this assertion is not the RED one.
    assert _head(clone) == stale, "the repair moved HEAD despite the collision"
    assert (clone / "gen.txt").read_text().startswith("baked"), (
        "the untracked file was destroyed -- that is reset --hard's behaviour, "
        "not --ff-only's")

    # These two carry the RED.
    assert "REPAIR FAILED" in r.stderr, (
        "the merge refused and said nothing: 'Repairing.' printed, REPAIRED did "
        "not, and the session proceeds on the snapshot base believing it was "
        "repaired. stderr=%r" % r.stderr)
    assert "gen.txt" in r.stderr, (
        "git named the colliding path and the hook discarded it, so the "
        "operator cannot act on the failure. stderr=%r" % r.stderr)


def test_the_failed_repair_block_is_distinct_from_stale_base(tmp_path):
    """Different states, different consequences, different markers.

    Off main, the harm is that your PR reverts. On main, the harm is that the
    auto-repair did not happen. Reusing one marker would make the two
    indistinguishable at exactly the moment the reader needs to tell them apart.
    """
    _origin, clone = _origin_and_clone(tmp_path, adds_late_file="gen.txt")
    (clone / "gen.txt").write_text("baked\n")
    r = _run_hook(clone, source="startup")
    assert _MARKER not in r.stderr, (
        "the off-main STALE BASE marker fired on main. stderr=%r" % r.stderr)


def test_a_repair_with_no_collision_still_succeeds_silently(tmp_path):
    """The over-sensitive direction: the new block must not fire on the ordinary
    repair, which is the common case and already works."""
    _origin, clone = _origin_and_clone(tmp_path)
    r = _run_hook(clone, source="startup")
    assert _head(clone) == _tip(clone), "precondition: the ordinary repair broke"
    assert "REPAIR FAILED" not in r.stderr, (
        "the failure block fired on a successful repair. stderr=%r" % r.stderr)
