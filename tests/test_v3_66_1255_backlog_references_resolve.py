"""Backlog-id references in canonical row prose resolve to real rows.

The subject is every numeric Markdown-table row in IMPROVEMENT_BACKLOG.md,
not a remembered list of phrases.  References use a deliberately lexical
grammar: ``row N``, ``rows N/M``, ``backlog N`` and their list, range, or
renumber-arrow forms.  A hyphen immediately after ``row`` is excluded because
this register also uses cut labels such as ``row-1204``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
BACKLOG = ROOT / "project-knowledge" / "IMPROVEMENT_BACKLOG.md"

_ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*(.*?)\s*\|$")
_ROW_LINE = re.compile(r"^\|\s*\d+\s*\|")
_REFERENCE = re.compile(
    r"\b(?:backlog(?:\s+rows?)?|rows?)\s+"
    r"(?P<body>\d+(?:\s*\([^)]*\))?"
    r"(?:\s*(?:/|,|->|-|\band\b|\bor\b)\s*"
    r"\d+(?:\s*\([^)]*\))?)*)",
    re.IGNORECASE,
)
_REFERENCE_PART = re.compile(r"\d+|->|/|,|-|and|or", re.IGNORECASE)
_INDEPENDENT_HEAD = re.compile(
    r"\b(?:backlog(?:\s+rows?)?|rows?)\s+(\d+)", re.IGNORECASE
)
_INDEPENDENT_SEPARATOR = re.compile(
    r"(?:\s*\([^)]*\))?\s*(/|,|->|-|\band\b|\bor\b)\s*(\d+)",
    re.IGNORECASE,
)
_SEPARATORS = frozenset({"/", ",", "-", "->", "and", "or"})


@dataclass(frozen=True)
class BacklogRow:
    row_id: int
    status: str
    prose: str


@dataclass(frozen=True)
class BacklogReference:
    source_row: int
    target_row: int
    spelling: str


def _parse_rows(text: str) -> dict[int, BacklogRow]:
    rows: dict[int, BacklogRow] = {}
    for line in text.splitlines():
        match = _ROW.fullmatch(line)
        if match is None:
            continue
        row_id = int(match.group(1))
        assert row_id not in rows, f"duplicate backlog row {row_id}"
        rows[row_id] = BacklogRow(row_id, match.group(2).strip(), match.group(3).strip())
    return rows


def _physical_row_count(text: str) -> int:
    """Count candidate row lines independently of the strict row parser."""
    return sum(bool(_ROW_LINE.match(line)) for line in text.splitlines())


def _targets_from_body(body: str) -> list[int]:
    body_without_annotations = re.sub(r"\([^)]*\)", "", body)
    parts = _REFERENCE_PART.findall(body_without_annotations)
    assert parts and parts[0].isdigit(), body
    targets = [int(parts[0])]
    assert len(parts) % 2 == 1, parts
    for index in range(1, len(parts), 2):
        separator, raw_target = parts[index], parts[index + 1]
        assert separator.casefold() in _SEPARATORS and raw_target.isdigit(), parts
        target = int(raw_target)
        if separator == "-":
            assert targets[-1] <= target, f"descending backlog row range: {body!r}"
            targets.extend(range(targets[-1] + 1, target + 1))
        else:
            targets.append(target)
    return targets


def _references(rows: dict[int, BacklogRow]) -> list[BacklogReference]:
    found: list[BacklogReference] = []
    for row in rows.values():
        for match in _REFERENCE.finditer(row.prose):
            found.extend(
                BacklogReference(row.row_id, target, match.group(0))
                for target in _targets_from_body(match.group("body"))
            )
    return found


def _independent_reference_count(rows: dict[int, BacklogRow]) -> int:
    """Count via iterative head/separator scanning, not _REFERENCE bodies."""
    count = 0
    for row in rows.values():
        for head in _INDEPENDENT_HEAD.finditer(row.prose):
            previous = int(head.group(1))
            count += 1
            offset = head.end()
            while separator_match := _INDEPENDENT_SEPARATOR.match(row.prose, offset):
                separator = separator_match.group(1).casefold()
                target = int(separator_match.group(2))
                if separator == "-":
                    assert previous <= target, (
                        f"descending backlog row range in row {row.row_id}: {row.prose!r}"
                    )
                    count += target - previous
                else:
                    count += 1
                previous = target
                offset = separator_match.end()
    return count


def _missing_reference_errors(rows: dict[int, BacklogRow]) -> list[str]:
    row_ids = set(rows)
    errors: list[str] = []
    for reference in _references(rows):
        if reference.target_row not in row_ids:
            errors.append(
                f"backlog row {reference.source_row} references absent backlog row "
                f"{reference.target_row} via {reference.spelling!r}"
            )
    return errors


def test_every_backlog_reference_resolves_over_the_exact_row_population() -> None:
    text = BACKLOG.read_text(encoding="ascii")
    rows = _parse_rows(text)
    physical_rows = _physical_row_count(text)
    assert len(rows) == physical_rows > 0, (
        f"parsed {len(rows)} of {physical_rows} numeric backlog row lines"
    )

    references = _references(rows)
    independent_count = _independent_reference_count(rows)
    assert len(references) == independent_count > 0, (
        f"extractor found {len(references)} references; independent scanner found "
        f"{independent_count}"
    )
    assert not _missing_reference_errors(rows)


def test_an_absent_backlog_id_reference_fires_once_for_the_intended_reason() -> None:
    fixture = "\n".join(
        (
            "| 1 | OPEN | the retained owner is row 999999 |",
            "| 2 | OPEN | control row with no references |",
        )
    )
    rows = _parse_rows(fixture)
    assert len(rows) == _physical_row_count(fixture) == 2
    references = _references(rows)
    assert references == [BacklogReference(1, 999999, "row 999999")]
    assert _independent_reference_count(rows) == len(references) == 1

    errors = _missing_reference_errors(rows)
    assert errors == [
        "backlog row 1 references absent backlog row 999999 via 'row 999999'"
    ]


def test_versions_counts_ports_and_hyphenated_cut_labels_are_not_row_ids() -> None:
    fixture = "\n".join(
        (
            "| 7 | OPEN | v3.66.1250 counted 14 items on port 8080 in row-1204; row 8 owns it |",
            "| 8 | OPEN | target |",
        )
    )
    rows = _parse_rows(fixture)
    assert len(rows) == _physical_row_count(fixture) == 2
    references = _references(rows)
    assert references == [BacklogReference(7, 8, "row 8")]
    assert _independent_reference_count(rows) == len(references) == 1
    assert not _missing_reference_errors(rows)


def test_plural_lists_ranges_arrows_and_annotations_have_exact_targets() -> None:
    fixture = "\n".join(
        (
            "| 1 | OPEN | rows 2/3, 4-5 and 6; row 7 -> 8 |",
            "| 2 | OPEN | Rows 7 (CLOSED @1250) and 8 (OPEN) are controls |",
            *(f"| {row_id} | OPEN | target |" for row_id in range(3, 9)),
        )
    )
    rows = _parse_rows(fixture)
    assert len(rows) == _physical_row_count(fixture) == 8
    references = _references(rows)
    assert [reference.target_row for reference in references] == [
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        7,
        8,
    ]
    assert 1250 not in {reference.target_row for reference in references}
    assert _independent_reference_count(rows) == len(references) == 9
    assert not _missing_reference_errors(rows)


def test_transform_control_only_imports_the_gate_without_asserting_existence() -> None:
    assert callable(_missing_reference_errors)
