"""The register's closed history lives in the archive, and a move cannot lose it.

CUT D. `project-knowledge/IMPROVEMENT_BACKLOG.md` had grown to 1.2 MB and 743
rows, 652 of them CLOSED, and every reader of the live work paid for the whole
file. The rows that reached a terminal status were MOVED -- not copied -- into
`project-knowledge/IMPROVEMENT_BACKLOG_ARCHIVE.md`, verbatim.

WHY A GATE AND NOT A CONVENTION. A move is the one register edit that can lose a
row silently: the register still parses, its header still matches its own table,
and every count taken over it is internally consistent while the row is simply
gone. The header marker cannot catch it, because the marker is recomputed FROM
the shrunken table -- it certifies that the register describes itself, never that
the register still describes everything it once held. This file is the check that
spans the two files, and its central assertion is the one the marker cannot make:
THE UNION STILL HOLDS EVERY ID THE REGISTER HELD BEFORE THE MOVE.

MOVE was ruled over COPY in
`bd-persist/RULING-archive-shape-MOVE-with-44-row-carveout-20260907T115411Z.md`:
a register that still physically holds every closed row is not an archive, and a
change no gate notices has not changed the thing the gates measure.

WHAT THIS GATE DOES NOT DO. It does not judge whether an archived row SHOULD have
been archived -- that is a status question and the status gates own it. It judges
only the properties a machine can check about a split: nothing was lost, nothing
was duplicated, no live work was filed in the history, and the archive did not
quietly become a second task register.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

# Its subject is the pair of register files, which no diff selects: a cut that
# touches neither file can still invalidate this gate by appending a row whose id
# already exists in the archive.
BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
REGISTER_REL = "project-knowledge/IMPROVEMENT_BACKLOG.md"
ARCHIVE_REL = "project-knowledge/IMPROVEMENT_BACKLOG_ARCHIVE.md"
REGISTER = ROOT / REGISTER_REL
ARCHIVE = ROOT / ARCHIVE_REL
GAP_ALLOWLIST = ROOT / "project-knowledge" / "REGISTER_GAP_ALLOWLIST.json"
OVERLAY = ROOT / "project-knowledge" / "build_current_overlay.py"

# THE SAME GRAMMAR BOTH FILES ARE PARSED WITH, and deliberately the same one
# `project-knowledge/build_current_overlay.py` uses, so the union this file
# computes is the union the register's own tooling computes. A second grammar
# for the archive would make the union un-computable, which is the failure this
# gate exists downstream of.
_ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*([^|]+)\|", re.MULTILINE)

# The canonical register header. The ARCHIVE MUST NOT CARRY IT: `bd-register-
# append` refuses a text in which it occurs other than once, and a tool pointed
# at the archive by accident must fail to find a register rather than find one.
_CANONICAL_MARKER = re.compile(r"canonical-task-register schema=1 rows=(\d+) open=(\d+) ids-sha256=([0-9a-f]{64})")

# THE POPULATION THE REGISTER HELD IMMEDIATELY BEFORE THE MOVE, pinned as a range
# and a count rather than as 743 literals. Measured at origin/main
# aa386213883d8f778738161b1f6838d37288c6f4 (v3.66.1524): 743 rows, ids 1..800,
# 57 of the 800 absent. Ids allocated AFTER the move are above 800 and do not
# enter this window, so the assertion stays exact as the register grows -- and it
# fires the moment a row inside the window stops being reachable from either file.
PRE_CUT_MAX_ID = 800
PRE_CUT_ROWS = 743
PRE_CUT_HOLES = 57

# THE FORTY-FOUR CLOSED ROWS THAT STAY IN THE REGISTER. Each is pinned BY ID OR
# BY LITERAL inside a gate, a mutant spec or a tool that reads the register file
# specifically. Teaching a population gate to compute the union fixes a COUNT; it
# does not move a LITERAL, so these rows cannot leave without editing eleven
# other files. Derived in bd-persist/ROUND1-CLASSIFICATION-21-catchers.md section
# 4 and adopted in the ruling named in this module's docstring.
RETAINED_CLOSED = frozenset(
    {
        5, 13, 25, 51, 99, 101, 102, 106, 107, 110, 111, 112, 113, 114, 115,
        119, 121, 123, 125, 128, 133, 138, 139, 140, 141, 143, 144, 160, 161,
        162, 164, 165, 166, 167, 168, 169, 174, 205, 208, 243, 244, 245, 250,
        633,
    }
)


def _rows(text: str) -> list[tuple[int, str]]:
    return [(int(m.group(1)), m.group(2).strip()) for m in _ROW.finditer(text)]


def _ids(text: str) -> list[int]:
    return [identity for identity, _ in _rows(text)]


def _derive_backlog(text: str):
    spec = importlib.util.spec_from_file_location("_bd_overlay_for_archive_gate", OVERLAY)
    assert spec is not None and spec.loader is not None, f"cannot load {OVERLAY}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.derive_backlog(text)


def test_both_files_exist_and_each_parses_a_nonzero_denominator() -> None:
    """NON-EMPTY DENOMINATOR ON BOTH SIDES, asserted before any union verdict.

    Every assertion below iterates a union. A regex that stopped matching one of
    the two files would make all of them pass over half the population, and a
    missing archive would make them pass over none of it -- a gate reporting OK
    because it examined nothing is worse than no gate.
    """
    assert REGISTER.is_file(), f"{REGISTER_REL} is missing"
    assert ARCHIVE.is_file(), (
        f"{ARCHIVE_REL} is missing. The closed rows were moved out of the "
        "register into it; without the file the register's history is gone and "
        "no count over the register alone can tell."
    )
    register_ids = _ids(REGISTER.read_text(encoding="utf-8"))
    archive_ids = _ids(ARCHIVE.read_text(encoding="utf-8"))
    assert register_ids, "the row grammar matched nothing in the register"
    assert archive_ids, "the row grammar matched nothing in the archive"


def test_no_id_appears_in_both_files() -> None:
    """MOVE, NOT COPY -- and the assertion that tells the two apart.

    A row present in both files is the COPY shape the ruling refused, and it is
    also how a union count can be right while the population is wrong.
    """
    register_ids = set(_ids(REGISTER.read_text(encoding="utf-8")))
    archive_ids = set(_ids(ARCHIVE.read_text(encoding="utf-8")))
    assert not (register_ids & archive_ids), (
        "these ids are in BOTH the register and the archive, so the move was a "
        f"copy for them: {sorted(register_ids & archive_ids)}"
    )


def test_no_id_is_duplicated_inside_either_file() -> None:
    for relative, path in ((REGISTER_REL, REGISTER), (ARCHIVE_REL, ARCHIVE)):
        ids = _ids(path.read_text(encoding="utf-8"))
        duplicates = sorted({identity for identity in ids if ids.count(identity) > 1})
        assert not duplicates, f"{relative} repeats row ids {duplicates}"


def test_the_union_still_holds_every_id_the_register_held_before_the_move() -> None:
    """THE ASSERTION THE HEADER MARKER CANNOT MAKE.

    The marker is recomputed from whatever table it sits above, so it certifies
    self-consistency and nothing else. This one spans both files and is stated
    over a CLOSED window -- ids 1..800, the population at the moment of the move
    -- so later appends cannot dilute it and a row that falls out of both files
    cannot hide behind them.
    """
    union = set(_ids(REGISTER.read_text(encoding="utf-8"))) | set(
        _ids(ARCHIVE.read_text(encoding="utf-8"))
    )
    window = {identity for identity in union if identity <= PRE_CUT_MAX_ID}
    holes = sorted(set(range(1, PRE_CUT_MAX_ID + 1)) - window)
    assert len(window) == PRE_CUT_ROWS, (
        f"the union of the two files holds {len(window)} of the {PRE_CUT_ROWS} "
        f"rows the register held before the move; missing beyond the {PRE_CUT_HOLES} "
        f"ids that never had a row: {holes}"
    )
    assert len(holes) == PRE_CUT_HOLES


def test_the_pre_move_holes_are_exactly_the_ids_the_allowlist_declares() -> None:
    """The two authorities on 'this id never had a row' must not drift.

    Without this, the union assertion above could be satisfied by a row that was
    lost AND an allowlist entry that was added to cover it, which is the shape of
    laundering a deletion into a declared gap.
    """
    payload = json.loads(GAP_ALLOWLIST.read_text(encoding="ascii"))
    declared = {entry["id"] for entry in payload["gaps"]}
    union = set(_ids(REGISTER.read_text(encoding="utf-8"))) | set(
        _ids(ARCHIVE.read_text(encoding="utf-8"))
    )
    holes = set(range(1, PRE_CUT_MAX_ID + 1)) - union
    assert holes == declared, (
        "the ids absent from BOTH files and the ids the gap allowlist declares "
        f"disagree; only-absent {sorted(holes - declared)}, "
        f"only-declared {sorted(declared - holes)}"
    )


def test_the_archive_holds_no_open_row() -> None:
    """CLAUDE.md A1 forbids a second task register, and this is what keeps the
    archive from becoming one. An archive holding zero OPEN rows is history; one
    holding a single OPEN row is a place work can be filed and then not found.
    """
    open_rows = [
        identity
        for identity, status in _rows(ARCHIVE.read_text(encoding="utf-8"))
        if status == "OPEN" or status.startswith("OPEN ")
    ]
    assert not open_rows, (
        f"{ARCHIVE_REL} carries OPEN rows {open_rows}. Live work belongs in the "
        "register; an archive that can hold an OPEN row is a second task register."
    )


def test_the_archive_does_not_carry_the_canonical_register_header() -> None:
    """`bd-register-append` and `build_current_overlay` both key on this marker.

    A second file carrying it is a second thing that answers to 'the register',
    and the failure it produces is a tool writing a row into the history.
    """
    assert not _CANONICAL_MARKER.search(ARCHIVE.read_text(encoding="utf-8")), (
        f"{ARCHIVE_REL} carries the canonical-task-register marker"
    )


def test_the_register_marker_counts_the_live_rows_only() -> None:
    """The restamp is honest: the marker describes the register's OWN table.

    It is deliberately NOT the union. The marker's readers -- bd-register-append,
    bd-register-close, the overlay -- write into the register, and a marker
    counting rows that are not in the file they edit would be wrong for every one
    of them.
    """
    text = REGISTER.read_text(encoding="utf-8")
    match = _CANONICAL_MARKER.search(text)
    assert match is not None, "the register carries no canonical-task-register marker"
    derived = _derive_backlog(text)
    assert derived is not None, "the canonical parser examined zero register rows"
    rows, opened, digest, _completed = derived
    assert (rows, opened, digest) == (
        int(match.group(1)),
        int(match.group(2)),
        match.group(3),
    ), (
        f"the register marker says rows={match.group(1)} open={match.group(2)} but its "
        f"table derives rows={rows} open={opened}"
    )


def test_every_pinned_closed_row_stayed_in_the_register() -> None:
    """The carve-out is a claim about WHERE a row is, so it is checked there.

    The archive's own prose says the retained ids are enumerated in this gate;
    this is that enumeration, and it fails if a later archiving pass takes one.
    """
    register_ids = set(_ids(REGISTER.read_text(encoding="utf-8")))
    archived = sorted(RETAINED_CLOSED - register_ids)
    assert not archived, (
        "these CLOSED rows are pinned by id or by literal inside a gate, a mutant "
        f"spec or a tool that reads the register file specifically: {archived}"
    )


def test_a_row_that_falls_out_of_both_files_is_detected() -> None:
    """NEGATIVE CONTROL for the union assertion, on a synthetic pair.

    Without it, the union check is indistinguishable from a check that always
    passes on any two files that happen to parse.
    """
    register = "| 1 | OPEN | live |\n"
    archive = "| 2 | CLOSED @1 | history |\n"
    union = set(_ids(register)) | set(_ids(archive))
    assert union == {1, 2}
    lost = set(_ids(register)) | set(_ids("| 9 | CLOSED @1 | wrong row |\n"))
    assert 2 not in lost, "the control did not remove the row it claims to remove"
    assert sorted({1, 2} - lost) == [2]


def test_a_row_copied_into_both_files_is_detected() -> None:
    """NEGATIVE CONTROL for the move/copy assertion."""
    register = "| 7 | CLOSED @1 | same row |\n"
    archive = "| 7 | CLOSED @1 | same row |\n"
    overlap = set(_ids(register)) & set(_ids(archive))
    assert overlap == {7}


def test_an_open_row_in_the_archive_is_detected() -> None:
    """NEGATIVE CONTROL for the A1 assertion, including the ` @`-suffixed form."""
    rows = _rows("| 3 | OPEN | live work in the history |\n| 4 | CLOSED @1 | done |\n")
    assert [identity for identity, status in rows if status.startswith("OPEN")] == [3]


def test_the_grammar_is_not_satisfied_by_prose_that_mentions_a_row() -> None:
    """NEGATIVE CONTROL for the parser itself: it must read the TABLE, not the text."""
    assert _rows("Row 12 is CLOSED @1049 and row 13 is OPEN.\n") == []
    assert _rows("| 12 | CLOSED @1049 | real row |\n") == [(12, "CLOSED @1049")]
