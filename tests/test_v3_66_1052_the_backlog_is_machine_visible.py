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
