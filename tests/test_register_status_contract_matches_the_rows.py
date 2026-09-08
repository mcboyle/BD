"""The register's declared status contract must describe the rows it actually has.

WHAT THIS GUARDS. `project-knowledge/IMPROVEMENT_BACKLOG.md` is the one live task
register, and its "Format, which the gate depends on" section is the CONTRACT a
reader -- human or script -- writes a triage against. Nothing before this file
compared that contract to the data. The section said "Status is one of `OPEN`,
`CLOSED`, `MOOT`" while five rows carried `PARKED`, and `PARKED` is not a typo the
other gates would catch: it is already known to
tests/test_v3_66_1052_the_backlog_is_machine_visible.py,
tests/test_v3_66_1171_backlog_truth_is_current.py and
tests/mutants/v3_66_1228_register_reconciliation.json. So the rows were right and
the paragraph was stale, which is the worse direction: a reader who trusts the
contract writes a three-status triage and SILENTLY DROPS the operator-parked
items -- precisely the class most likely to be forgotten, because by definition
nobody is working on it.

THE CROSS-CHECK IS THE POINT, and it is why reading the contract out of the file
under test is not circular here. One side of every assertion is the declared
prose; the other side is the parsed status column of every row. Neither is
derived from the other, so a drift in either direction fails: a status the rows
use and the contract omits, a status the contract declares and no row uses, an
evidence rule the rows disobey.

THE ROWS ARE THE UNION OF TWO FILES SINCE v3.66.1525. The 608 terminal rows
nothing pins were MOVED into project-knowledge/IMPROVEMENT_BACKLOG_ARCHIVE.md, so
the population this contract describes no longer lives in one file. The CONTRACT
prose is still the register's, and the published `rows=` marker is still checked
against the REGISTER'S OWN table -- its readers write into that file. Only the row
population widens, and it must: a contract compared against 135 of 743 rows would
declare `MOOT` unused the day the last MOOT row was archived.

BD_GATE_SCOPE IS "module", DELIBERATELY, AND THE ARCHIVE DOES NOT CHANGE THAT. Its
subject is the internal consistency of a named pair of tracked documents. It still
makes no `git ls-files` call and still asserts nothing about the tree, so it is not
a repo-wide gate and does not belong in the CI shard union.
The three sibling register gates are repo-wide because they assert about retired
surfaces and tracked paths across the whole tree; this one does not. Per
tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py the scope call is the
author's and the gate only refuses to let it go unmade -- this is the call, made.

WHAT THIS DOES NOT CATCH. It does not check that a status is used CORRECTLY -- a
row parked when it should be open passes every assertion here. It compares the
vocabulary and the evidence discipline, not the judgement behind a status.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


BD_GATE_SCOPE = "module"

ROOT = Path(__file__).resolve().parents[1]
BACKLOG = ROOT / "project-knowledge" / "IMPROVEMENT_BACKLOG.md"
ARCHIVE = ROOT / "project-knowledge" / "IMPROVEMENT_BACKLOG_ARCHIVE.md"

# The row grammar is the one test_v3_66_1164_one_task_authority.py parses with.
# Copied rather than imported so a change there cannot silently redefine what
# this gate measures.
_ROW = re.compile(
    r"^\|\s*(?P<id>\d+)\s*\|\s*(?P<status>[A-Z]+)(?P<evidence>[^|]*)\|\s*(?P<text>.+?)\s*\|\s*$"
)
_META = re.compile(
    r"^<!-- canonical-task-register schema=1 rows=(?P<rows>\d+) open=(?P<open>\d+) "
    r"ids-sha256=(?P<digest>[0-9a-f]{64}) -->$",
    re.MULTILINE,
)

_FORMAT_HEADING = "## Format, which the gate depends on"

# "Status is one of `OPEN`, `CLOSED`, `PARKED`, `MOOT`."
_ENUM = re.compile(r"Status is one of ((?:`[A-Z]+`(?:,| and|\.| )*)+)")
# "Evidence is required for `CLOSED`, `MOOT` and `PARKED`, and forbidden for `OPEN`."
_EVIDENCE_RULE = re.compile(
    r"Evidence is required for (?P<required>(?:`[A-Z]+`(?:,| and| )*)+),"
    r" and forbidden for (?P<forbidden>(?:`[A-Z]+`(?:,| and| )*)+)\."
)
_TOKEN = re.compile(r"`([A-Z]+)`")

# The row count measured at d69ffcda (v3.66.1521), which is this cut's base. It
# is a FLOOR, not a pin: the register is appended to, and a literal equality here
# would fail on the next unrelated row and report it as a contract defect. The
# anti-vacuity property is carried by the floor together with the marker
# cross-check below -- a parse that matched nothing, or matched a subset, fails.
_ROWS_AT_BASE = 742


def _text() -> str:
    """The REGISTER alone: the contract prose and the published marker live here."""
    return BACKLOG.read_text(encoding="ascii")


def _population_text() -> str:
    """The register AND its archive: the rows the contract has to describe."""
    return BACKLOG.read_text(encoding="ascii") + "\n" + ARCHIVE.read_text(encoding="ascii")


def _rows(text: str) -> list[tuple[int, str, str, str]]:
    rows = []
    for line in text.splitlines():
        if match := _ROW.match(line):
            rows.append(
                (
                    int(match.group("id")),
                    match.group("status"),
                    match.group("evidence").strip(),
                    match.group("text"),
                )
            )
    return rows


def _format_section(text: str) -> str:
    start = text.find(_FORMAT_HEADING)
    assert start != -1, f"the register has no {_FORMAT_HEADING!r} section to read"
    rest = text[start + len(_FORMAT_HEADING):]
    end = rest.find("\n## ")
    return rest if end == -1 else rest[:end]


def _declared_enum(section: str) -> list[str]:
    match = _ENUM.search(section)
    assert match, (
        "the Format section declares no status enum: no 'Status is one of `X`, ...' "
        "sentence, so there is no contract to compare the rows against"
    )
    return _TOKEN.findall(match.group(1))


def _declared_evidence_rule(section: str) -> tuple[set[str], set[str]]:
    match = _EVIDENCE_RULE.search(section)
    assert match, (
        "the Format section states no machine-readable evidence rule: expected a "
        "sentence 'Evidence is required for `A`, `B` and `C`, and forbidden for "
        "`D`.' so every declared status says whether a reader may check it"
    )
    return set(_TOKEN.findall(match.group("required"))), set(
        _TOKEN.findall(match.group("forbidden"))
    )


def _parsed_rows_or_fail() -> list[tuple[int, str, str, str]]:
    """Every verdict below runs behind this precondition, never in front of it."""
    text = _text()
    register_rows = _rows(text)
    rows = _rows(_population_text())
    assert register_rows, "the register row parser matched zero rows, so it measures nothing"
    assert len(rows) > len(register_rows), (
        f"the archive contributed no rows: {len(rows)} in the union against "
        f"{len(register_rows)} in the register alone. Either it stopped being parsed "
        "or it stopped holding rows, and both make every verdict below narrower "
        "than it reads."
    )
    marker = _META.search(text)
    assert marker, "the register publishes no machine-visible rows= denominator"
    assert len(register_rows) == int(marker.group("rows")), (
        f"parsed {len(register_rows)} register rows but the published marker says "
        f"{marker.group('rows')}; one of the two is wrong and neither may be "
        "trusted until they agree"
    )
    # THE FLOOR IS OVER THE UNION AND THAT IS THE WHOLE POINT OF STATING IT HERE:
    # rows do not disappear, they MOVE, and a floor read against one file would
    # turn the move into a lost deferral -- or, once weakened to 135, would stop
    # noticing a real one.
    assert len(rows) >= _ROWS_AT_BASE, (
        f"the register and its archive carry {len(rows)} rows between them, fewer "
        f"than the {_ROWS_AT_BASE} measured at d69ffcda: rows do not disappear, so "
        "this is a lost deferral or a broken parse, and either way no verdict below "
        "is meaningful"
    )
    return rows


def test_the_declared_status_enum_names_every_status_the_rows_use():
    rows = _parsed_rows_or_fail()
    declared = _declared_enum(_format_section(_text()))
    assert declared, "the declared status enum parsed empty"

    used: dict[str, list[int]] = {}
    for row_id, status, _evidence, _text_ in rows:
        used.setdefault(status, []).append(row_id)

    undeclared = {
        status: ids for status, ids in sorted(used.items()) if status not in declared
    }
    assert not undeclared, (
        "the Format section's status enum "
        f"{declared} omits status(es) the rows actually use: "
        + "; ".join(f"{status} on row(s) {ids}" for status, ids in undeclared.items())
        + " -- a reader who trusts the contract silently drops those rows"
    )

    unused = [status for status in declared if status not in used]
    assert not unused, (
        f"the Format section declares status(es) no row uses: {unused}; over "
        f"{len(rows)} rows the live vocabulary is {sorted(used)}"
    )


def test_every_declared_status_has_an_evidence_rule_and_the_rows_obey_it():
    rows = _parsed_rows_or_fail()
    section = _format_section(_text())
    declared = set(_declared_enum(section))
    required, forbidden = _declared_evidence_rule(section)

    assert not (required & forbidden), (
        f"the evidence rule both requires and forbids evidence for {sorted(required & forbidden)}"
    )
    assert required | forbidden == declared, (
        "the evidence rule and the status enum disagree: enum "
        f"{sorted(declared)}, rule covers {sorted(required | forbidden)}; "
        f"unruled {sorted(declared - (required | forbidden))}, "
        f"undeclared {sorted((required | forbidden) - declared)}"
    )

    missing = [
        row_id
        for row_id, status, evidence, _ in rows
        if status in required and not evidence.startswith("@")
    ]
    assert not missing, (
        f"row(s) {missing} carry a status whose evidence the contract requires, "
        "with no @evidence: a close nobody can check is a claim, not a record"
    )
    carried = [
        row_id
        for row_id, status, evidence, _ in rows
        if status in forbidden and "@" in evidence
    ]
    assert not carried, (
        f"row(s) {carried} carry @evidence under a status the contract says "
        "carries none"
    )


def test_a_status_the_register_does_not_use_is_not_reported_as_present():
    """Negative control: the detector must not invent a status out of the prose.

    `WONTFIX` and `DEFERRED` appear nowhere in the status column. If either were
    reported the comparison above would be measuring the document's prose, or its
    row TEXT, rather than its status column -- and every verdict it reaches would
    be about the wrong bytes.
    """
    rows = _parsed_rows_or_fail()
    used = {status for _, status, _, _ in rows}
    for absent in ("WONTFIX", "DEFERRED", "TODO", "REOPENED"):
        assert absent not in used, (
            f"{absent} was reported as a live status; the status-column parser is "
            f"matching something else. Live vocabulary: {sorted(used)}"
        )
    assert "CLOSED" in used, (
        "positive control: CLOSED must be found, or the parser above proves "
        f"nothing by finding no WONTFIX. Live vocabulary: {sorted(used)}"
    )


@pytest.mark.parametrize(
    "section,expected",
    [
        ("Status is one of `OPEN`, `CLOSED`, `MOOT`.", ["OPEN", "CLOSED", "MOOT"]),
        (
            "Status is one of `OPEN`, `CLOSED`, `PARKED`, `MOOT`.",
            ["OPEN", "CLOSED", "PARKED", "MOOT"],
        ),
        ("Status is one of `OPEN` and `CLOSED`.", ["OPEN", "CLOSED"]),
    ],
)
def test_the_enum_parser_reads_a_synthetic_contract(section, expected):
    """The enum parser is driven with inputs whose answer is known independently.

    A parser whose only exercise is the live file cannot be distinguished from
    one that always returns what that file happens to say.
    """
    assert _declared_enum(section) == expected


def test_the_parsers_fail_closed_rather_than_returning_an_empty_contract():
    """No contract must never read as an empty contract, which nothing violates."""
    with pytest.raises(AssertionError, match="declares no status enum"):
        _declared_enum("One row per item, and the statuses are obvious.")
    with pytest.raises(AssertionError, match="no machine-readable evidence rule"):
        _declared_evidence_rule("`OPEN` carries no evidence.")
