"""Backlog-id references in canonical row prose resolve to real rows.

The subject is every numeric Markdown-table row in IMPROVEMENT_BACKLOG.md,
not a remembered list of phrases.  References use a deliberately lexical
grammar: ``row N``, ``rows N/M``, ``backlog N`` and their list, range, or
renumber-arrow forms.  A hyphen immediately after ``row`` is excluded because
this register also uses cut labels such as ``row-1204``.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path


BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
BACKLOG = ROOT / "project-knowledge" / "IMPROVEMENT_BACKLOG.md"
ARCHIVE = ROOT / "project-knowledge" / "IMPROVEMENT_BACKLOG_ARCHIVE.md"

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

_ROW_FILENAME = re.compile(r"^test_row(\d+)_.+\.py$")

# row654: OWED TO THE INTEGRATOR (O544, TRIO RULE) -- none of these ids have a
# register or archive row; this worker never edits IMPROVEMENT_BACKLOG.md.
# test_row362_templates_are_resolvable.py, test_row379_byte_safe_remote_source_transport.py,
# test_row387_ast_version_pin_guard.py and
# test_row399_a_photo_gallery_is_not_a_failed_video_page.py.
# PM-FIX O806: row395's file was the open A2/row654 ownership question; row700-r5
# renamed it to test_row700_captcha_egress_disclosure.py, so 395 is no longer an
# absent citation and is dropped from the pending set below.
# each need one new row under their own id. The four test_row407_*.py files span two
# distinct subjects (candidate adopt/replay vs. watchdog/integration verdict) and need
# TWO new rows, not one. Shrink this set only once the integrator lands the matching row.
#
# test_row1435_band_verdict_transfer.py and test_row1459_the_verified_binary_is_the_executed_binary.py
# are excluded by _is_cut_slug_not_a_row_citation below: both docstrings self-identify as
# "Cut <N>", a cut/commit slug, not a backlog row citation (1459's says so explicitly:
# "is this cut's slug, not a register row").
_PENDING_NEW_BACKLOG_ROW_IDS = frozenset({362, 379, 387, 399, 407})


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


def _is_cut_slug_not_a_row_citation(path: Path, row_id: int) -> bool:
    """A test_row<N>_*.py module whose docstring self-identifies as ``Cut N``
    is naming a cut/commit slug, not citing backlog row N."""
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstring = ast.get_docstring(module) or ""
    return f"Cut {row_id}" in docstring


def _row_filename_ids(root: Path) -> dict[int, list[str]]:
    by_id: dict[int, list[str]] = {}
    for path in sorted((root / "tests").glob("test_row*_*.py")):
        match = _ROW_FILENAME.match(path.name)
        if match is None:
            continue
        row_id = int(match.group(1))
        if _is_cut_slug_not_a_row_citation(path, row_id):
            continue
        by_id.setdefault(row_id, []).append(path.name)
    return by_id


def _absent_filename_row_ids(
    by_id: dict[int, list[str]], row_ids: set[int]
) -> dict[int, list[str]]:
    return {row_id: names for row_id, names in by_id.items() if row_id not in row_ids}


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
    # THE ROW POPULATION IS THE UNION OF THE REGISTER AND ITS ARCHIVE. The 608
    # CLOSED rows moved into the archive at v3.66.1525 still carry references in
    # their prose, and a reference is only dangling if it resolves against
    # NEITHER file. Reading both makes this gate strictly stronger than it was:
    # it now judges 743 rows of prose where it judged 135.
    text = BACKLOG.read_text(encoding="ascii") + "\n" + ARCHIVE.read_text(encoding="ascii")
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


def test_every_test_row_filename_id_resolves_or_is_a_declared_pending_entry() -> None:
    """row654: a tests/test_row<N>_*.py filename is a backlog-id citation too,
    not just prose. The contiguity gate (v3.66.1441) only covers the register;
    this is the filename half."""
    text = BACKLOG.read_text(encoding="ascii") + "\n" + ARCHIVE.read_text(encoding="ascii")
    row_ids = set(_parse_rows(text))
    by_id = _row_filename_ids(ROOT)
    assert by_id, "found zero tests/test_row<N>_*.py files: prove the scan built the shape first"

    absent = _absent_filename_row_ids(by_id, row_ids)
    assert set(absent) == _PENDING_NEW_BACKLOG_ROW_IDS, (
        f"tests/test_row<N>_*.py filenames cite absent backlog id(s) {sorted(absent)}; "
        f"declared pending set is {sorted(_PENDING_NEW_BACKLOG_ROW_IDS)} -- a mismatch "
        f"means either a new absent id appeared (extend the declaration) or one of the "
        f"declared ids now has a row (shrink the declaration)"
    )
    assert len(by_id[407]) == 4, (
        f"row 407 must be cited by exactly four test files (it spans two subjects), "
        f"got {len(by_id[407])}"
    )


def test_a_valid_row_filename_id_produces_no_absence() -> None:
    by_id = {654: ["test_row654_x.py"], 700: ["test_row700_y.py"]}
    row_ids = {654, 700, 1}
    assert not _absent_filename_row_ids(by_id, row_ids)


def test_an_absent_row_filename_id_fires_once_for_the_intended_reason() -> None:
    by_id = {999999: ["test_row999999_ghost.py"]}
    row_ids = {1, 2}
    absent = _absent_filename_row_ids(by_id, row_ids)
    assert absent == {999999: ["test_row999999_ghost.py"]}
    assert len(absent) == 1


def test_prose_discussion_of_an_absent_id_is_not_a_filename_citation() -> None:
    # A backlog row may legitimately discuss an id no row owns without that id
    # ever appearing as a tests/test_row<N>_*.py filename; the filename scan
    # must never treat prose text as a citation.
    assert _ROW_FILENAME.match("not_a_row_file.py") is None
    assert _ROW_FILENAME.match("test_row_bad_shape.py") is None
    match = _ROW_FILENAME.match("test_row407_candidate_adopt.py")
    assert match is not None and match.group(1) == "407"


def test_a_cut_slug_filename_is_a_legitimate_absent_id_discussion(tmp_path: Path) -> None:
    # test_row1459_the_verified_binary_is_the_executed_binary.py names its own
    # id "this cut's slug, not a register row" -- a real, live example of the
    # exact "legitimate absent-id discussion" the row's ACCEPTANCE controls for.
    cut_slug = tmp_path / "test_row999999_a_cut_not_a_row.py"
    cut_slug.write_text('"""Cut 999999: a slug, not a backlog row."""\n', encoding="utf-8")
    assert _is_cut_slug_not_a_row_citation(cut_slug, 999999)

    row_citation = tmp_path / "test_row999999_a_real_row.py"
    row_citation.write_text('"""Row 999999: a real backlog citation."""\n', encoding="utf-8")
    assert not _is_cut_slug_not_a_row_citation(row_citation, 999999)
