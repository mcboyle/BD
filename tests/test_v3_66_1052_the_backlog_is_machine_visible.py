"""The improvement backlog must be readable by a machine, not just by a reader.

BACKLOG ITEM 85, WHICH WAS ITEM 85 OF ITSELF. The backlog was produced by a
review that called it the most valuable artifact of its session, and it then
lived in an untracked file in the operator's home directory that no gate read.
CLAUDE.md section 1 already stated the general rule:

    "A DEFERRAL THAT LIVES ONLY IN PROSE HAS NOT BEEN DEFERRED -- IT HAS BEEN
    DROPPED. The ITEM LEDGER works for exactly one reason: a test reads it."

The demonstration arrived 2026-08-12: a session with full authority to work the
backlog could not find it, and the operator had to say where it was. The list
was not wrong; nothing could see it. This file is the "a test reads it" half.

WHAT THIS GATE DOES NOT DO, deliberately. It does not judge whether a row's
STATUS is true -- no test can know whether an OPEN item is really still open,
and a gate that pretended to would be asserting over a subject it cannot see.
It checks the properties a machine CAN check: that the file parses, that ids are
unique, that every status is a known one, and that every terminal status carries
evidence. That is a narrower promise than "the backlog is accurate", and saying
so is the point.

THE NUMBERING IS NOT THE ITEM LEDGER'S. These ids are the backlog's own
namespace; backlog 21 is the row whose subject is ledger item 48. Three
numbering schemes were once reconciled at 15.35/15.36 after exactly this
confusion, so this file asserts nothing across the two.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Its subject is a REGISTER, not a module -- the backlog is the whole project's
# open-work surface, and a cut that touches no source at all can invalidate it.
# Wired into CI at @1116, backlog row 105: until then this gate and
# test_register_promises_resolve appeared ZERO times in ci.yml, zero times in
# _DECLARED, and both sat in the frozen baseline. The two checks guarding the
# registers were the two checks nothing ran, which is section 7's own rule --
# a gate CI does not run is a gate that does not exist -- landing on the
# registers that exist to stop work being lost.
BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parents[1]
BACKLOG = REPO / "project-knowledge" / "IMPROVEMENT_BACKLOG.md"
GAP_ALLOWLIST_REL = "project-knowledge/REGISTER_GAP_ALLOWLIST.json"
GAP_ALLOWLIST = REPO / GAP_ALLOWLIST_REL

# PARKED IS A STATE THE OPERATOR ALREADY USED AND THE SCHEMA COULD NOT EXPRESS.
# Row 127 was parked by operator ruling at v3.66.1195 pending a two-week soak.
# With only OPEN / CLOSED / MOOT available it had to be recorded as OPEN, which
# tells every reader -- and every agent working this list -- that it is
# available work. It is not: it is waiting on wall-clock time and a decision
# that is not the reader's to make. Forcing a state into a vocabulary that
# cannot hold it makes the register lie in the direction of MORE work, which is
# the one direction nobody audits.
#
# PARKED is TERMINAL for the purpose of "do not pick this up", and it cites its
# evidence like any other terminal state, so a park is as checkable as a close.
# It is NOT terminal for the item's life: a park can be unparked, and that is
# the operator's call rather than this gate's.
VALID_STATUS = {"OPEN", "CLOSED", "MOOT", "PARKED"}
TERMINAL = {"CLOSED", "MOOT", "PARKED"}

# | 21 | CLOSED @1049 | text |
_ROW = re.compile(
    r"^\|\s*(?P<id>\d+)\s*\|\s*(?P<status>[A-Z]+)(?P<evidence>[^|]*)\|\s*(?P<text>.+?)\s*\|\s*$"
)


def _rows():
    rows = []
    for line in BACKLOG.read_text(encoding="utf-8").splitlines():
        m = _ROW.match(line)
        if not m:
            continue
        rows.append(
            {
                "id": int(m.group("id")),
                "status": m.group("status"),
                "evidence": m.group("evidence").strip(),
                "text": m.group("text").strip(),
                "line": line,
            }
        )
    return rows


def test_the_backlog_file_exists():
    assert BACKLOG.is_file(), (
        f"{BACKLOG} is missing. The backlog living somewhere a gate cannot read "
        "is the exact condition backlog item 85 exists to end."
    )


def test_the_parser_finds_at_least_one_row():
    """NON-EMPTY DENOMINATOR, asserted before any per-row verdict.

    Every assertion below iterates the parsed rows, so a regex that stopped
    matching -- a changed column, a renamed header -- would make all of them
    pass over an empty list. A gate reporting OK because it examined nothing is
    worse than no gate (CLAUDE.md section 0), and this is the shape that
    produces it.

    Exact identity and OPEN denominators are published and checked by the
    one-task-authority gate.  This local parser assertion only prevents an
    empty-regex false pass; it must not create a second count authority.
    """
    rows = _rows()
    assert rows, (
        f"parsed zero backlog rows from {BACKLOG}; the row format "
        "and the parser have diverged, so every check in this file would be "
        "asserting over nothing"
    )


def test_every_id_is_unique():
    """A duplicate id makes two different items indistinguishable in every
    later reference, which is how a closed item's number gets reused and a
    genuinely open item reads as done."""
    rows = _rows()
    seen: dict[int, str] = {}
    dupes = []
    for r in rows:
        if r["id"] in seen:
            dupes.append((r["id"], seen[r["id"]], r["text"]))
        seen[r["id"]] = r["text"]
    assert not dupes, f"duplicate backlog ids: {dupes}"


def test_every_status_is_a_known_one():
    rows = _rows()
    bad = [(r["id"], r["status"]) for r in rows if r["status"] not in VALID_STATUS]
    assert not bad, (
        f"unknown status values {bad}; expected one of {sorted(VALID_STATUS)}. "
        "An unrecognised status reads as neither open nor done and will be "
        "skipped by every human triaging this list."
    )


def test_every_terminal_row_cites_evidence():
    """A close nobody can check is a claim, not a record.

    CLOSED and MOOT both end an item's life, so both must name the version or
    commit that ended it. OPEN carries none by construction -- there is nothing
    yet to cite.
    """
    rows = _rows()
    missing = [
        (r["id"], r["status"], r["text"][:60])
        for r in rows
        if r["status"] in TERMINAL and not r["evidence"]
    ]
    assert not missing, (
        f"terminal rows with no evidence: {missing}. Add @<version> or a commit "
        "-- otherwise the row asserts the work happened and nothing can confirm it."
    )


def test_parked_is_a_state_the_register_can_hold_and_must_evidence(monkeypatch):
    """PARKED must be BOTH recognised and disciplined, and this drives each.

    The gap was measured while reconciling the register at v3.66.1228: row 127
    was parked by operator ruling at v3.66.1195 pending a two-week soak, and the
    only statuses available were OPEN, CLOSED and MOOT. It had to be written
    OPEN, which told every reader it was available work. A register whose
    vocabulary cannot hold a state the operator actually used will lie, and it
    lies in the direction of MORE open work -- the direction nobody audits.

    Adding a status is worthless if it is merely TOLERATED, so both directions
    are driven here: a parked row must be accepted AND must cite the version
    that parked it, exactly like any other terminal state.
    """
    import textwrap

    def _with(body):
        f = tmp = Path(str(BACKLOG) + ".parked-control")
        f.write_text(textwrap.dedent(body), encoding="utf-8")
        monkeypatch.setattr(sys.modules[__name__], "BACKLOG", f)
        return f

    good = _with("""\
        | 1 | OPEN | still to do |
        | 2 | PARKED @1195 | waiting on an operator soak |
        """)
    try:
        rows = _rows()
        assert [r["status"] for r in rows] == ["OPEN", "PARKED"], rows
        assert "PARKED" in VALID_STATUS
        assert "PARKED" in TERMINAL, (
            "a PARKED row that is not TERMINAL would be required to carry NO "
            "evidence, which is the opposite of what a park needs")
        bad = [r["id"] for r in rows if r["status"] not in VALID_STATUS]
        assert not bad, bad
        missing = [r["id"] for r in rows
                   if r["status"] in TERMINAL and not r["evidence"]]
        assert not missing, missing

        # NEGATIVE CONTROL: a park with no evidence is a claim, not a record.
        _with("""\
            | 1 | PARKED | parked by nobody, at no version |
            """)
        rows = _rows()
        assert rows and rows[0]["status"] == "PARKED", rows
        assert not rows[0]["evidence"].strip(), rows
        assert [r["id"] for r in rows
                if r["status"] in TERMINAL and not r["evidence"].strip()], (
            "a PARKED row citing nothing was accepted; a park nobody can check "
            "is indistinguishable from an item that was quietly dropped")
    finally:
        good.unlink(missing_ok=True)


def test_open_rows_carry_no_evidence_marker():
    """The inverse, so the two states stay distinguishable.

    Without this, an OPEN row carrying a version would read as closed to a
    skimmer and as open to the parser -- the register disagreeing with itself,
    which is the failure the ledger gate at @1035 was written for.
    """
    rows = _rows()
    odd = [(r["id"], r["evidence"]) for r in rows if r["status"] == "OPEN" and r["evidence"]]
    assert not odd, (
        f"OPEN rows carrying an evidence marker: {odd}. Either the work is done "
        "-- in which case the status is wrong -- or the marker is."
    )


def test_every_row_has_text():
    rows = _rows()
    empty = [r["id"] for r in rows if not r["text"]]
    assert not empty, f"backlog rows with no description: {empty}"


def test_the_file_is_ascii():
    """The canonical backlog is ASCII-only for every machine reader."""
    raw = BACKLOG.read_bytes()
    try:
        raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise AssertionError(f"{BACKLOG} is not ASCII-only: {exc}") from exc


# ---------------------------------------------------------------------------
# A HOLE IN THE ID SEQUENCE IS DECLARED, OR IT IS A FAILURE.
#
# WHAT WAS MEASURED, and by three independent instruments. 57 ids in 20 blocks
# are absent from the register between 1 and its highest id: 15, 40-42, 236,
# 270-279, 282, 288, 301-307, 352, 359, 362, 364-365, 369-370, 379, 382-383,
# 387, 392-396, 398-401, 403-407, 409-410 and 457-462. Only the first four are
# documented -- by the register's own "FOUR IDS HAVE NEVER EXISTED" section --
# so 53 absences had no recorded reason at all. Nothing was deleted: a walk of
# 266 generations of the file found zero removals, and a sweep of 2,626 register
# blobs (2,271 of them dangling) found that none of the 53 ever existed.
#
# THE MECHANISM. `toolchain/bin/bd-register-append` enforces monotonic increase
# and uniqueness and never contiguity. Ids were handed to concurrent workers out
# of order and a row is filed only on merge, so every cut that did not land left
# a permanent hole. Ids 463-647 are fully contiguous, which is the block-intake
# pattern that replaced one-row-per-cut: this is a bounded historical class, not
# an ongoing bleed. That is exactly why it must be FROZEN now -- an unbounded
# hole population could never be seeded.
#
# WHY NOTHING CAUGHT IT, which is the part worth keeping. Every existing check
# over this file is SELF-CONSISTENT: `derive_backlog` hashes ",".join(ids) of
# the current file and looks for duplicates, `marker_matches` compares that hash
# to a header derived from the same text, and append's `expected_ids_sha256` is
# compare-and-swap against the bytes it just read. 590 rows with 57 holes
# satisfies all three perfectly. Uniqueness and contiguity are the two halves of
# one question about the id population and only one half was ever asked; this is
# the other half, sitting beside test_every_id_is_unique because that is its
# subject too.
#
# BOTH DIRECTIONS, because an allowlist that only forbids new holes rots into
# permanent permission. An entry for an id that is now PRESENT is a failure, and
# so is an entry above the register's highest id -- that one bank permission for
# a hole that does not exist yet, and it also catches a register truncated below
# the allowlist's frontier.
# ---------------------------------------------------------------------------

GAP_ALLOWLIST_SCHEMA = "bd-register-gap-allowlist/v1"
GAP_ALLOWLIST_KEYS = frozenset({"schema", "register", "notes", "gaps"})
GAP_ENTRY_KEYS = frozenset({"id", "status", "reason"})
# The ONLY status any tool may write. bd-register-append --allow-gap writes this
# and nothing else.
MACHINE_WRITABLE_GAP_STATUS = "DECLARED"
# Measured absent, never explained. It carries no reason on purpose: none was
# ever recorded, and inventing one would launder a hole into a decision. Only a
# human may clear an entry in this state.
HUMAN_ONLY_GAP_STATUS = "UNADJUDICATED"
# The register explains these itself, so the entry quotes it rather than
# paraphrasing.
DOCUMENTED_GAP_STATUS = "DOCUMENTED"
REASONED_GAP_STATUS = frozenset({DOCUMENTED_GAP_STATUS, MACHINE_WRITABLE_GAP_STATUS})
VALID_GAP_STATUS = frozenset(
    {DOCUMENTED_GAP_STATUS, HUMAN_ONLY_GAP_STATUS, MACHINE_WRITABLE_GAP_STATUS}
)

# The register's own words about the four documented absences, so the seeded
# reason can be proven to QUOTE the register rather than to have been invented.
_DOCUMENTED_QUOTE = (
    "15, 40, 41 and 42 are absent and their content is unrecoverable"
)

# Independent of _ROW above: it accepts any line opening with a numeric table
# cell, whatever follows. Two parsers that disagree mean the id population is
# UNKNOWN, and a contiguity verdict over an UNKNOWN population is worthless.
_ROW_ID_LINE = re.compile(r"^\|\s*(\d+)\s*\|")

_UNDECLARED_PREFIX = "REGISTER GAP UNDECLARED:"
_STALE_PREFIX = "REGISTER GAP ALLOWLIST STALE:"
_PREMATURE_PREFIX = "REGISTER GAP ALLOWLIST PREMATURE:"
_DENOMINATOR_UNKNOWN_PREFIX = "REGISTER GAP DENOMINATOR UNKNOWN:"
_ALLOWLIST_UNKNOWN_PREFIX = "REGISTER GAP ALLOWLIST UNKNOWN:"


class RegisterGapUnknown(AssertionError):
    """A required gap measurement could not be taken.

    UNKNOWN is a failing third state (CLAUDE.md A2). Every path that cannot
    measure the id population or read the declaration raises this instead of
    returning an empty error list, because an empty error list is indis-
    tinguishable from OK.
    """


def _measured_register_ids(text: str) -> list[int]:
    """The register's id population, measured twice and reconciled.

    The strict row parser is the one every other assertion in this file uses;
    the line scanner is deliberately looser. Equality of the two is the
    reconciliation A7 asks for, and a zero from either is UNKNOWN rather than
    "no holes".
    """
    strict = [
        int(match.group("id"))
        for line in text.splitlines()
        if (match := _ROW.match(line))
    ]
    scanned = [
        int(match.group(1))
        for line in text.splitlines()
        if (match := _ROW_ID_LINE.match(line))
    ]
    if not strict or not scanned:
        raise RegisterGapUnknown(
            f"{_DENOMINATOR_UNKNOWN_PREFIX} the strict row parser found {len(strict)} "
            f"ids and the independent line scanner found {len(scanned)}; a contiguity "
            "verdict over zero rows would report OK having examined nothing"
        )
    if sorted(strict) != sorted(scanned):
        raise RegisterGapUnknown(
            f"{_DENOMINATOR_UNKNOWN_PREFIX} two independent parses of the same register "
            f"disagree on the id population ({len(strict)} strict against {len(scanned)} "
            f"scanned; symmetric difference {sorted(set(strict) ^ set(scanned))})"
        )
    return sorted(strict)


def _validated_gap_entries(payload: object, origin: str) -> list[dict]:
    """Structure and status discipline for the declaration, or UNKNOWN."""

    def unknown(detail: str) -> RegisterGapUnknown:
        return RegisterGapUnknown(f"{_ALLOWLIST_UNKNOWN_PREFIX} {origin} {detail}")

    if not isinstance(payload, dict) or frozenset(payload) != GAP_ALLOWLIST_KEYS:
        raise unknown(
            f"must be a JSON object with exactly the keys {sorted(GAP_ALLOWLIST_KEYS)}"
        )
    if payload["schema"] != GAP_ALLOWLIST_SCHEMA:
        raise unknown(f"must declare schema {GAP_ALLOWLIST_SCHEMA!r}")
    if payload["register"] != "project-knowledge/IMPROVEMENT_BACKLOG.md":
        raise unknown(
            "must name the register it declares gaps in, so it cannot be pointed at "
            "a different file than the gate reads"
        )
    if not isinstance(payload["notes"], list) or not all(
        isinstance(note, str) for note in payload["notes"]
    ):
        raise unknown("notes must be a list of strings")
    entries = payload["gaps"]
    if not isinstance(entries, list):
        raise unknown("gaps must be a list of entries")

    seen: set[int] = set()
    for entry in entries:
        if not isinstance(entry, dict) or frozenset(entry) != GAP_ENTRY_KEYS:
            raise unknown(
                f"entry {entry!r} must be an object with exactly {sorted(GAP_ENTRY_KEYS)}"
            )
        identity, status, reason = entry["id"], entry["status"], entry["reason"]
        if isinstance(identity, bool) or not isinstance(identity, int) or identity < 1:
            raise unknown(f"entry id {identity!r} must be a positive integer")
        if identity in seen:
            raise unknown(
                f"declares id {identity} more than once, so the two declarations "
                "cannot both be audited"
            )
        seen.add(identity)
        if status not in VALID_GAP_STATUS:
            raise unknown(
                f"entry {identity} has status {status!r}; expected one of "
                f"{sorted(VALID_GAP_STATUS)}"
            )
        if not isinstance(reason, str):
            raise unknown(f"entry {identity} reason must be a string")
        if status in REASONED_GAP_STATUS and not reason.strip():
            raise unknown(
                f"entry {identity} is {status} with an empty reason; a declared gap "
                "whose reason is blank is an undeclared gap wearing a status"
            )
        if status == HUMAN_ONLY_GAP_STATUS and reason:
            raise unknown(
                f"entry {identity} is {HUMAN_ONLY_GAP_STATUS} and carries a reason; no "
                "reason was ever recorded for these and writing one would launder an "
                "unexplained hole into a decision"
            )
    return entries


def _load_gap_allowlist(path: Path) -> list[dict]:
    try:
        raw = path.read_bytes().decode("ascii")
    except (OSError, UnicodeDecodeError) as exc:
        raise RegisterGapUnknown(
            f"{_ALLOWLIST_UNKNOWN_PREFIX} {path} could not be read as ASCII: {exc}"
        ) from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RegisterGapUnknown(
            f"{_ALLOWLIST_UNKNOWN_PREFIX} {path} is not valid JSON: {exc}"
        ) from exc
    return _validated_gap_entries(payload, str(path))


def _gap_errors(register_ids: list[int], entries: list[dict]) -> list[str]:
    """Every declared id is present, above the frontier, or a real hole.

    The three arms are total over the declaration and disjoint, so each failure
    names the one condition that produced it. A shared message here would be the
    collapsed diagnostic A7 names -- "the register has a gap problem" leads to
    the wrong action half the time, because restoring a row and deleting a stale
    entry are opposite repairs.
    """
    present = set(register_ids)
    highest = max(present)
    holes = set(range(1, highest + 1)) - present
    declared = {entry["id"] for entry in entries}
    errors: list[str] = []

    undeclared = sorted(holes - declared)
    if undeclared:
        errors.append(
            f"{_UNDECLARED_PREFIX} id(s) {undeclared} are absent from 1..{highest} of the "
            f"register and carry no entry in {GAP_ALLOWLIST_REL}. A hole is an id a cut "
            "was handed and never landed; either restore the row or declare it with "
            "'bd-register-append --allow-gap <reason>', which writes the reason and the "
            "row together."
        )

    stale_present = sorted(declared & present)
    if stale_present:
        errors.append(
            f"{_STALE_PREFIX} id(s) {stale_present} are declared absent in "
            f"{GAP_ALLOWLIST_REL} but are PRESENT in the register. Delete those entries: "
            "an allowlist that keeps declarations for ids that came back is no longer a "
            "record of what is missing, it is standing permission."
        )

    premature = sorted(identity for identity in declared if identity > highest)
    if premature:
        errors.append(
            f"{_PREMATURE_PREFIX} id(s) {premature} are declared absent in "
            f"{GAP_ALLOWLIST_REL} but exceed the register's highest id {highest}, so they "
            "are not holes. Either the register was truncated below them, or permission "
            "was banked for a gap that does not exist yet."
        )
    return errors


def _fixture_allowlist(gaps: list[dict]) -> dict:
    return {
        "schema": GAP_ALLOWLIST_SCHEMA,
        "register": "project-knowledge/IMPROVEMENT_BACKLOG.md",
        "notes": ["fixture"],
        "gaps": gaps,
    }


def test_the_gap_allowlist_exists_and_declares_its_own_schema():
    """PRECONDITION for every verdict below, asserted before any of them."""
    assert GAP_ALLOWLIST.is_file(), (
        f"{GAP_ALLOWLIST} is missing. Without it every hole in the register is "
        "undeclared and this gate cannot distinguish 'no holes' from 'no declaration'."
    )
    payload = json.loads(GAP_ALLOWLIST.read_bytes().decode("ascii"))
    assert payload["schema"] == GAP_ALLOWLIST_SCHEMA


def test_every_register_gap_is_declared_and_no_declaration_is_stale():
    """THE OVER-SENSITIVITY CONTROL: the real register and the real allowlist.

    It is not vacuous and asserts so: this register HAS holes today, so a gate
    that could not tell a declared hole from an undeclared one would fail here.
    """
    text = BACKLOG.read_text(encoding="ascii")
    register_ids = _measured_register_ids(text)
    assert len(register_ids) == len(set(register_ids)) > 0, (
        f"{len(register_ids)} parsed ids, {len(set(register_ids))} distinct"
    )
    entries = _load_gap_allowlist(GAP_ALLOWLIST)

    highest = max(register_ids)
    holes = sorted(set(range(1, highest + 1)) - set(register_ids))
    assert holes, (
        "this control is only meaningful while the register still has holes; if it "
        "became contiguous, the control must be replaced by a synthetic one rather "
        "than left to pass over an empty hole set"
    )
    assert {entry["id"] for entry in entries} == set(holes), (
        f"the declaration and the measurement disagree: declared "
        f"{sorted({entry['id'] for entry in entries})}, measured {holes}"
    )
    assert _gap_errors(register_ids, entries) == []


def test_the_seeded_declaration_quotes_the_register_and_invents_nothing():
    """The four DOCUMENTED entries cite the register; the other 53 stay blank.

    This is the half that stops the seed being a laundering step. A reason that
    is not in the register is an invented one, and 53 absences that nothing ever
    explained must keep saying so.
    """
    entries = _load_gap_allowlist(GAP_ALLOWLIST)
    register_text = BACKLOG.read_text(encoding="ascii")
    assert _DOCUMENTED_QUOTE in register_text, (
        "the register no longer contains the sentence the DOCUMENTED entries quote; "
        "the citation has gone stale and must be re-derived, not left standing"
    )

    documented = [e for e in entries if e["status"] == DOCUMENTED_GAP_STATUS]
    unadjudicated = [e for e in entries if e["status"] == HUMAN_ONLY_GAP_STATUS]
    assert sorted(e["id"] for e in documented) == [15, 40, 41, 42], documented
    assert all(_DOCUMENTED_QUOTE in e["reason"] for e in documented), (
        "a DOCUMENTED entry must quote the register's own explanation"
    )
    assert unadjudicated, "the 53 unexplained absences must still be visible as such"
    assert all(e["reason"] == "" for e in unadjudicated)
    assert len(documented) + len(unadjudicated) == len(entries), (
        "the seed carries a status that is neither documented nor unadjudicated"
    )


def test_an_undeclared_gap_fails_for_its_own_named_reason():
    """RED CONTROL (a). The gap is real and the declaration is empty."""
    fixture = "| 1 | OPEN | first |\n| 2 | OPEN | second |\n| 4 | OPEN | fourth |\n"
    register_ids = _measured_register_ids(fixture)
    assert register_ids == [1, 2, 4], register_ids
    assert sorted(set(range(1, 5)) - set(register_ids)) == [3]

    empty = _validated_gap_entries(_fixture_allowlist([]), "fixture")
    assert empty == []
    errors = _gap_errors(register_ids, empty)
    assert len(errors) == 1, errors
    assert errors[0].startswith(_UNDECLARED_PREFIX), errors[0]
    assert "[3]" in errors[0], errors[0]
    assert _STALE_PREFIX not in errors[0] and _PREMATURE_PREFIX not in errors[0]

    # The later refusal conditions are made to PASS so this verdict cannot be
    # laundered by one of them (CLAUDE.md A5).
    declared = _validated_gap_entries(
        _fixture_allowlist(
            [{"id": 3, "status": MACHINE_WRITABLE_GAP_STATUS, "reason": "cut never landed"}]
        ),
        "fixture",
    )
    assert _gap_errors(register_ids, declared) == []


def test_a_stale_declaration_fails_with_a_different_message():
    """RED CONTROL (b). The mirror case: an id declared absent that came back.

    The fixture is deliberately CONTIGUOUS, so this failure cannot be the
    undeclared-hole arm firing under another name.
    """
    fixture = "| 1 | OPEN | first |\n| 2 | OPEN | second |\n| 3 | OPEN | third |\n"
    register_ids = _measured_register_ids(fixture)
    assert register_ids == [1, 2, 3], register_ids
    assert not set(range(1, 4)) - set(register_ids), "the fixture must have no holes"

    entries = _validated_gap_entries(
        _fixture_allowlist(
            [{"id": 2, "status": MACHINE_WRITABLE_GAP_STATUS, "reason": "landed later"}]
        ),
        "fixture",
    )
    errors = _gap_errors(register_ids, entries)
    assert len(errors) == 1, errors
    assert errors[0].startswith(_STALE_PREFIX), errors[0]
    assert not errors[0].startswith(_UNDECLARED_PREFIX)
    assert "[2]" in errors[0], errors[0]

    # Removing the rotted entry is the repair, and it is sufficient.
    assert _gap_errors(register_ids, _validated_gap_entries(_fixture_allowlist([]), "fixture")) == []


def test_a_declaration_above_the_frontier_fails_with_its_own_message():
    """RED CONTROL (b2). Permission may not be banked for a hole that is not one.

    This arm also catches a register truncated below the allowlist's frontier,
    which presents identically and is the more dangerous of the two.
    """
    fixture = "| 1 | OPEN | first |\n| 2 | OPEN | second |\n"
    register_ids = _measured_register_ids(fixture)
    assert register_ids == [1, 2]

    entries = _validated_gap_entries(
        _fixture_allowlist(
            [{"id": 9, "status": MACHINE_WRITABLE_GAP_STATUS, "reason": "banked"}]
        ),
        "fixture",
    )
    errors = _gap_errors(register_ids, entries)
    assert len(errors) == 1, errors
    assert errors[0].startswith(_PREMATURE_PREFIX), errors[0]
    assert "[9]" in errors[0] and "highest id 2" in errors[0], errors[0]


def test_the_three_refusals_are_simultaneously_reachable_and_separable():
    """No shared message, and each arm fires on its own condition only.

    All three at once, so a reader repairing one is not left guessing whether
    the other two were the same finding restated.
    """
    fixture = "| 1 | OPEN | first |\n| 3 | OPEN | third |\n"
    register_ids = _measured_register_ids(fixture)
    assert register_ids == [1, 3]
    entries = _validated_gap_entries(
        _fixture_allowlist(
            [
                {"id": 1, "status": MACHINE_WRITABLE_GAP_STATUS, "reason": "stale"},
                {"id": 7, "status": MACHINE_WRITABLE_GAP_STATUS, "reason": "premature"},
            ]
        ),
        "fixture",
    )
    errors = _gap_errors(register_ids, entries)
    prefixes = [error.split(":")[0] + ":" for error in errors]
    assert prefixes == [_UNDECLARED_PREFIX, _STALE_PREFIX, _PREMATURE_PREFIX], errors
    assert len(set(prefixes)) == 3, "two refusals share a diagnostic"
    assert "[2]" in errors[0] and "[1]" in errors[1] and "[7]" in errors[2], errors


def test_an_unmeasurable_register_is_unknown_and_never_ok():
    """A2: zero ids, or two parsers that disagree, is a FAILING third state."""
    for text, why in (
        ("", "an empty register"),
        ("no table rows at all\n", "prose with no rows"),
        ("| id | status | item |\n| --- | --- | --- |\n", "a header with no data rows"),
    ):
        try:
            _measured_register_ids(text)
        except RegisterGapUnknown as exc:
            assert str(exc).startswith(_DENOMINATOR_UNKNOWN_PREFIX), (why, str(exc))
        else:
            raise AssertionError(f"{why} returned a verdict instead of UNKNOWN")

    # A line the strict parser rejects and the loose scanner accepts: the two
    # populations differ, so the id set is UNKNOWN rather than "1, 2".
    divergent = "| 1 | OPEN | first |\n| 5 | open | lowercase status |\n"
    assert sum(bool(_ROW_ID_LINE.match(line)) for line in divergent.splitlines()) == 2
    assert sum(bool(_ROW.match(line)) for line in divergent.splitlines()) == 1
    try:
        _measured_register_ids(divergent)
    except RegisterGapUnknown as exc:
        assert str(exc).startswith(_DENOMINATOR_UNKNOWN_PREFIX), str(exc)
        assert "[5]" in str(exc), str(exc)
    else:
        raise AssertionError("disagreeing parsers returned a verdict instead of UNKNOWN")


def test_a_malformed_declaration_is_unknown_and_never_an_empty_error_list():
    """Every structural defect refuses; none of them reads as 'no gaps found'."""
    cases = {
        "wrong top-level shape": ["not", "an", "object"],
        "missing key": {"schema": GAP_ALLOWLIST_SCHEMA, "gaps": []},
        "wrong schema": {**_fixture_allowlist([]), "schema": "something/v9"},
        "wrong register": {**_fixture_allowlist([]), "register": "docs/other.md"},
        "notes not strings": {**_fixture_allowlist([]), "notes": [7]},
        "gaps not a list": {**_fixture_allowlist([]), "gaps": {}},
        "entry not an object": _fixture_allowlist([3]),
        "entry missing reason": _fixture_allowlist([{"id": 3, "status": "DECLARED"}]),
        "boolean id": _fixture_allowlist(
            [{"id": True, "status": "DECLARED", "reason": "x"}]
        ),
        "zero id": _fixture_allowlist([{"id": 0, "status": "DECLARED", "reason": "x"}]),
        "duplicate id": _fixture_allowlist(
            [
                {"id": 3, "status": "DECLARED", "reason": "one"},
                {"id": 3, "status": "DECLARED", "reason": "two"},
            ]
        ),
        "unknown status": _fixture_allowlist(
            [{"id": 3, "status": "PROBABLY", "reason": "x"}]
        ),
        "declared with a blank reason": _fixture_allowlist(
            [{"id": 3, "status": "DECLARED", "reason": "   "}]
        ),
        "documented with a blank reason": _fixture_allowlist(
            [{"id": 3, "status": "DOCUMENTED", "reason": ""}]
        ),
        "unadjudicated carrying an invented reason": _fixture_allowlist(
            [{"id": 3, "status": "UNADJUDICATED", "reason": "probably fine"}]
        ),
    }
    for why, payload in cases.items():
        try:
            _validated_gap_entries(payload, "fixture")
        except RegisterGapUnknown as exc:
            assert str(exc).startswith(_ALLOWLIST_UNKNOWN_PREFIX), (why, str(exc))
        else:
            raise AssertionError(f"{why} was accepted; a malformed declaration must refuse")

    # POSITIVE CONTROL: the validator is not simply rejecting everything.
    accepted = _validated_gap_entries(
        _fixture_allowlist(
            [
                {"id": 3, "status": "DECLARED", "reason": "cut 9 never landed"},
                {"id": 4, "status": "UNADJUDICATED", "reason": ""},
                {"id": 5, "status": "DOCUMENTED", "reason": "the register says so"},
            ]
        ),
        "fixture",
    )
    assert [entry["id"] for entry in accepted] == [3, 4, 5]


def test_an_unreadable_declaration_file_is_unknown(tmp_path):
    """The file itself, not just its parsed shape: missing and non-JSON both refuse."""
    missing = tmp_path / "absent.json"
    try:
        _load_gap_allowlist(missing)
    except RegisterGapUnknown as exc:
        assert str(exc).startswith(_ALLOWLIST_UNKNOWN_PREFIX), str(exc)
    else:
        raise AssertionError("a missing declaration file returned a verdict")

    broken = tmp_path / "broken.json"
    broken.write_bytes(b'{"schema": ')
    try:
        _load_gap_allowlist(broken)
    except RegisterGapUnknown as exc:
        assert "not valid JSON" in str(exc), str(exc)
    else:
        raise AssertionError("a truncated declaration file returned a verdict")

    non_ascii = tmp_path / "utf8.json"
    non_ascii.write_bytes('{"schema": "é"}'.encode("utf-8"))
    try:
        _load_gap_allowlist(non_ascii)
    except RegisterGapUnknown as exc:
        assert "ASCII" in str(exc), str(exc)
    else:
        raise AssertionError("a non-ASCII declaration file returned a verdict")
