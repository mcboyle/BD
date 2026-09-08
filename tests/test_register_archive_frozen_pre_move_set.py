"""A laundered swap cannot walk through: the expected set is FROZEN IN HISTORY.

WHY THIS FILE EXISTS AND WHY IT IS NOT A SECOND COPY OF ITS SIBLING.
`tests/test_register_archive_holds_the_moved_rows.py` proves the archive move was
honest, and it is correct about everything it asserts. It is defeated by ONE
edit, and that edit is a thing an in-window resolver makes routinely. MEASURED
against the sibling module on this tree, four runs, restoring between each:

    (0) baseline, honest tree                              13 passed
    (1) SWAP: `| 391 |` dropped from the archive, its line   2 failed / 11 passed
        renumbered `| 392 |` and appended to the register    (holes-vs-allowlist RED,
        -- the union COUNT is still 743 and still passes      marker RED: rows 135 -> 136)
    (2) the same SWAP, plus the gap allowlist's entry        ** 1 failed / 12 passed,
        `{"id": 392}` co-edited to `{"id": 391}`                and after the resolver
                                                                restamps the marker with
                                                                the register's own tool,
                                                                ** 13 passed. FULLY GREEN. **
    (3) PLAIN LOSS control: 391 dropped, nothing added        2 failed (union RED, holes RED)

Row 391 is then in NEITHER file, and every assertion in the module is satisfied.
The reason is structural, not a bug in any single test: the sibling's expected
population is a COUNT (743 rows, 57 holes) plus a set derived from
`project-knowledge/REGISTER_GAP_ALLOWLIST.json`. A swap preserves the count, and
the allowlist is a file the same commit can edit. ** A GATE WHOSE EXPECTED SET IS
DERIVED FROM A FILE THE SAME EDIT CAN LAUNDER REPAIRS ITS SUBJECT BEFORE
MEASURING IT. **

WHERE THE FROZEN SET LIVES, AND WHY IT IS NOT ANOTHER LAUNDERABLE FILE.
It is not a new file at all. It is the register blob itself, as it stood at the
commit immediately before the move -- `PRE_MOVE_COMMIT` below, which is cut D's
declared base and the commit at which `IMPROVEMENT_BACKLOG_ARCHIVE.md` does not
yet exist (asserted, so a later re-point at a post-move commit cannot pass
silently). A commit cannot edit an ancestor commit's blob.

WHAT STOPS THE SAME COMMIT EDITING IT, AND WHAT TURNS RED IF SOMEONE TRIES.
Three things must change together, across two systems, for a swap to pass here:
  * the register/archive pair, as before -- one ordinary edit; and
  * `PRE_MOVE_IDS_SHA256` in THIS file, or the derivation goes red because the
    digest of the ids recovered from history no longer matches the literal; and
  * main's history itself, force-pushed, or the recovered ids still contain the
    dropped row and `test_every_frozen_pre_move_id_is_still_reachable...` goes red.
Editing the digest alone turns the derivation test red. Editing the files alone
turns the reachability test red. There is no single edit, and no pair of edits
inside one commit, that leaves this module green while a row is gone. That is the
bar the brief set: not "harder to launder" but "cannot be laundered in two edits".

FAIL-CLOSED, NEVER SKIP. If git cannot produce the pre-move blob -- shallow
clone, missing object, no git at all -- that is COULD NOT LOOK, which is UNKNOWN,
which is never permission. These tests FAIL with the reason named rather than
skip, because a gate that goes quiet exactly when it cannot see is the failure
mode this whole cut is about. CI checks out with `fetch-depth: 0`, so the object
is present in the lane that runs this.

WHAT THIS FILE DOES NOT DO. It does not re-assert anything the sibling asserts --
no union count, no move/copy check, no marker check, no A1 second-register check.
Those legs are load-bearing and stay where they are; in particular the sibling's
holes-vs-allowlist test is what catches the BARE swap (run 1) and must not be
weakened or folded into anything here.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

# The subject is a pair of register files plus a blob in history, and no diff
# selects that: a cut touching neither file can still invalidate this gate.
BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
REGISTER_REL = "project-knowledge/IMPROVEMENT_BACKLOG.md"
ARCHIVE_REL = "project-knowledge/IMPROVEMENT_BACKLOG_ARCHIVE.md"
ALLOWLIST_REL = "project-knowledge/REGISTER_GAP_ALLOWLIST.json"
REGISTER = ROOT / REGISTER_REL
ARCHIVE = ROOT / ARCHIVE_REL
GAP_ALLOWLIST = ROOT / ALLOWLIST_REL

# CUT D's DECLARED BASE. The register at this commit is the pre-move population,
# and the archive does not exist here yet -- both asserted below, so re-pointing
# this constant at a post-move commit fails loudly instead of freezing the
# already-split state as if it were the original.
PRE_MOVE_COMMIT = "aa386213883d8f778738161b1f6838d37288c6f4"

# sha256 over the comma-joined, ascending, de-duplicated ids recovered from that
# blob. Recomputed by the first test from history on every run; a mismatch means
# either this literal or that history moved, and both are the thing we are
# watching for. Measured 2026-09-07: 743 ids, 1..800, 57 absent.
PRE_MOVE_IDS_SHA256 = "53c8822019b9cbd2ad4b141449fa0d6af70af74caa4b35f954c76b224c6f2017"
PRE_MOVE_ID_COUNT = 743
PRE_MOVE_MAX_ID = 800
PRE_MOVE_HOLE_COUNT = 57

# The same grammar the sibling gate and `build_current_overlay.py` use. A second
# grammar here would make the two sides of the comparison incomparable, which is
# the class of defect this gate exists downstream of.
_ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*([^|]+)\|", re.MULTILINE)


def _ids(text: str) -> set[int]:
    return {int(match.group(1)) for match in _ROW.finditer(text)}


def _digest(ids) -> str:
    return hashlib.sha256(",".join(str(i) for i in sorted(ids)).encode("ascii")).hexdigest()


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ("git", "-C", str(ROOT)) + args,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _pre_move_ids() -> set[int]:
    """The frozen set, recovered from history. Raises AssertionError, never skips."""
    blob = _git("show", f"{PRE_MOVE_COMMIT}:{REGISTER_REL}")
    assert blob.returncode == 0, (
        "COULD NOT LOOK, which is not permission: the pre-move register blob "
        f"{PRE_MOVE_COMMIT}:{REGISTER_REL} could not be read. This is a shallow "
        "clone, a missing object or a non-git tree -- the frozen set is UNMEASURED "
        "and this gate refuses rather than passing over nothing. CI checks out "
        f"with fetch-depth: 0. git said: {blob.stderr.strip()!r}"
    )
    ids = _ids(blob.stdout)
    assert ids, (
        "the row grammar matched NOTHING in the pre-move register blob; the "
        "frozen set would be empty and every subset assertion below would pass "
        "over an empty population"
    )
    return ids


def test_the_frozen_pre_move_id_set_is_recoverable_from_history_and_matches_its_digest() -> None:
    """THE ANCHOR. Everything else is a subset assertion against this set.

    Two independent records must agree: the blob in history, and the digest
    literal in this file. Editing either one alone turns this red, which is what
    makes the pair un-launderable by a single commit.
    """
    absent = _git("cat-file", "-e", f"{PRE_MOVE_COMMIT}:{ARCHIVE_REL}")
    assert absent.returncode != 0, (
        f"{ARCHIVE_REL} already exists at {PRE_MOVE_COMMIT}, so that commit is NOT "
        "pre-move and the set frozen from it is the already-split population. "
        "PRE_MOVE_COMMIT must name the commit before the archive was created."
    )
    ids = _pre_move_ids()
    holes = set(range(1, PRE_MOVE_MAX_ID + 1)) - ids
    assert len(ids) == PRE_MOVE_ID_COUNT, (
        f"the pre-move register holds {len(ids)} ids, not {PRE_MOVE_ID_COUNT}"
    )
    assert max(ids) == PRE_MOVE_MAX_ID, f"pre-move max id is {max(ids)}"
    assert len(holes) == PRE_MOVE_HOLE_COUNT
    assert _digest(ids) == PRE_MOVE_IDS_SHA256, (
        "the ids recovered from history do not hash to PRE_MOVE_IDS_SHA256. Either "
        "this literal was edited, or main's history was rewritten. Both are the "
        f"thing this gate watches. recovered={_digest(ids)} pinned={PRE_MOVE_IDS_SHA256}"
    )


def test_every_frozen_pre_move_id_is_still_reachable_from_one_of_the_two_files() -> None:
    """** THE PRIMARY CONTROL. ** Element-wise, so a swap cannot balance it.

    The sibling asserts the union has the right SIZE. A swap that drops one id and
    adds another keeps the size and walks through. This asserts membership of every
    frozen id individually, so dropping 391 is red no matter what is appended and
    no matter what any allowlist declares.
    """
    frozen = _pre_move_ids()
    union = _ids(REGISTER.read_text(encoding="utf-8")) | _ids(
        ARCHIVE.read_text(encoding="utf-8")
    )
    lost = sorted(frozen - union)
    assert not lost, (
        "these row ids existed in the register at "
        f"{PRE_MOVE_COMMIT} and are now in NEITHER {REGISTER_REL} nor "
        f"{ARCHIVE_REL}: {lost}. A row that falls out of both files is lost work, "
        "and no count over either file alone can see it."
    )


def test_the_gap_allowlist_never_declares_an_id_that_provably_had_a_row() -> None:
    """The second, independent leg -- it kills the LAUNDERING EDIT by itself.

    The laundered attack works by re-pointing a declared gap at the id it just
    deleted. History says that id had a row, so declaring it a gap is a provable
    falsehood and this fails whether or not the reachability test above does.
    Deliberately two legs: either alone is sufficient, and a future edit that
    weakens one still meets the other.
    """
    import json

    payload = json.loads(GAP_ALLOWLIST.read_text(encoding="ascii"))
    declared = {entry["id"] for entry in payload["gaps"]}
    assert declared, f"{ALLOWLIST_REL} declares no gaps; the comparison would be vacuous"
    frozen = _pre_move_ids()
    impossible = sorted(declared & frozen)
    assert not impossible, (
        f"{ALLOWLIST_REL} declares these ids as gaps that never had a row, but the "
        f"register at {PRE_MOVE_COMMIT} contains a row for each of them: "
        f"{impossible}. An allowlist cannot un-write history."
    )


def test_the_honest_archive_move_is_not_flagged() -> None:
    """A GATE THAT FIRES ON THE HONEST CASE IS WORSE THAN NONE. Proved explicitly.

    Partition the real frozen set arbitrarily into two synthetic files -- which is
    exactly what cut D does -- and assert the reachability predicate passes. This
    is a property of ANY partition, not of the particular split that shipped, so
    it also holds for the next archiving pass.

    DELIBERATELY SYNTHETIC AND DELIBERATELY NOT READING THE LIVE FILES. A test
    that asserted the shipped tree is honest would be a fourth copy of the legs
    above and would go red on a dishonest tree -- which tells a reader nothing
    about whether an honest move is safe. This one stays green on ANY tree,
    honest or attacked, because its subject is the PREDICATE, not the tree.
    """
    frozen = sorted(_pre_move_ids())
    for stride in (2, 3, 7):
        live = set(frozen[::stride])
        archived = set(frozen) - live
        assert live and archived, f"stride {stride} put everything on one side"
        assert not (live & archived), f"stride {stride} is a copy, not a move"
        assert not (set(frozen) - (live | archived)), (
            f"an honest partition at stride {stride} was flagged as having lost a row"
        )


def test_the_laundered_swap_is_detected_on_a_synthetic_pair() -> None:
    """NEGATIVE CONTROL for both legs, on synthetic inputs, so the controls do not
    depend on the real tree being wrong. Reproduces run (2) in miniature: drop an
    id, append a renumbered one, and co-edit the declared gaps to match."""
    frozen = {1, 2, 391}
    declared_before = {392}
    live, archived = {1}, {2, 391}
    assert not (frozen - (live | archived)), "precondition: the honest pair is complete"
    assert not (declared_before & frozen), "precondition: the honest allowlist is clean"

    swapped_archive = {2}
    swapped_live = {1, 392}
    union = swapped_live | swapped_archive
    assert len(union) == len(live | archived), (
        "the control did not preserve the union SIZE, so it is not the swap that "
        "defeats a count-based gate"
    )
    assert sorted(frozen - union) == [391], "leg 1 did not detect the dropped id"

    declared_after = {391}
    assert sorted(declared_after & frozen) == [391], "leg 2 did not detect the laundered gap"


def test_a_plain_loss_is_detected_and_an_empty_frozen_set_cannot_pass() -> None:
    """NEGATIVE CONTROLS for the loss case and for the vacuous-population case."""
    frozen = {1, 2, 3}
    assert sorted(frozen - ({1} | {2})) == [3], "the loss control detected nothing"
    # a frozen set that came back empty must never satisfy a subset assertion by
    # construction; this is the shape _pre_move_ids() refuses with an assert.
    empty: set[int] = set()
    assert not (empty - {1}), "sanity: an empty set is a subset of anything"
    assert not empty, "which is exactly why _pre_move_ids() refuses an empty recovery"


def test_the_grammar_reads_the_table_and_not_the_prose() -> None:
    """NEGATIVE CONTROL for the parser this file shares with its sibling."""
    assert _ids("Row 391 is CLOSED @1351 and row 392 is a gap.\n") == set()
    assert _ids("| 391 |  CLOSED @1351 | real row |\n") == {391}
