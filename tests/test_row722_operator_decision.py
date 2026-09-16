"""Row 722: shared campaign egress and keeper volume have explicit decisions."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
_DECISIONS_RELATIVE = Path("project-knowledge/FLEET_EGRESS_DECISIONS.md")
_DECISIONS = _REPO / _DECISIONS_RELATIVE
_REQUIRED_QUESTIONS = ("Distinct per-host egress", "Keeper re-login cap")
_RECOGNISED_STATUSES = frozenset({"DEFAULT", "DECIDED", "SUPERSEDED"})
_MIN_REASON_LENGTH = 20


def _row722_section(text: str) -> str:
    starts = list(re.finditer(r"(?m)^## Row 722 -- .+$", text))
    if len(starts) != 1:
        raise AssertionError(
            "row 722 decision evidence must contain exactly one row section; "
            f"found {len(starts)}"
        )
    start = starts[0].start()
    following = re.search(r"(?m)^## ", text[starts[0].end():])
    end = starts[0].end() + following.start() if following else len(text)
    return text[start:end]


def _question_records(section: str) -> dict[str, dict[str, str]]:
    headings = list(re.finditer(r"(?m)^### (?P<name>[^\n]+)$", section))
    records: dict[str, dict[str, str]] = {}
    for index, heading in enumerate(headings):
        question = heading.group("name")
        if question in records:
            raise AssertionError(f"row 722 duplicate question key: {question}")
        end = headings[index + 1].start() if index + 1 < len(headings) else len(section)
        body = section[heading.end():end]
        fields: dict[str, str] = {}
        for match in re.finditer(
            r"(?m)^(?P<field>Decision|Reason):[ \t]*(?P<value>[^\n]*)$", body
        ):
            field = match.group("field")
            if field in fields:
                raise AssertionError(
                    f"row 722 {question.lower()} duplicate field: {field}"
                )
            fields[field] = match.group("value")
        records[question] = fields
    return records


def _independent_question_headings(text: str) -> list[str]:
    """Second parser for the row-722 denominator: every level-3 heading in the
    row-722 section, read with a different regex than _question_records so the
    gate's count is checked against a list it did not produce."""
    section = _row722_section(text)
    return re.findall(r"(?m)^###[ \t]+(?P<q>\S[^\n]*)$", section)


def _assert_row722_contract(text: str) -> int:
    section = _row722_section(text)
    statuses = re.findall(r"(?m)^Status:[ \t]*(?P<value>[^\n]*)$", section)
    assert len(statuses) == 1, (
        "row 722 must contain exactly one status; "
        f"found {len(statuses)}"
    )
    status = statuses[0].strip()
    if status not in _RECOGNISED_STATUSES:
        raise AssertionError(f"row 722 status is not recognised: {status}")

    records = _question_records(section)
    assert records, "row 722 must contain at least one judged question"
    for required in _REQUIRED_QUESTIONS:
        if required not in records:
            raise AssertionError(f"row 722 required question missing: {required}")

    for question, observed in records.items():
        if "Decision" not in observed:
            raise AssertionError(f"row 722 {question.lower()} decision missing")
        decision = observed["Decision"].strip()
        if not decision:
            raise AssertionError(f"row 722 {question.lower()} decision empty")

        if "Reason" not in observed:
            raise AssertionError(f"row 722 {question.lower()} reason missing")
        reason = observed["Reason"].strip()
        if not reason:
            raise AssertionError(f"row 722 {question.lower()} reason empty")
        if len(reason) < _MIN_REASON_LENGTH:
            raise AssertionError(
                f"row 722 {question.lower()} reason too short; "
                f"minimum is {_MIN_REASON_LENGTH} characters"
            )
    return len(records)


def test_row722_records_both_operator_decisions_with_reasons() -> None:
    assert _DECISIONS.is_file(), (
        f"row 722 requires {_DECISIONS_RELATIVE} as tracked decision evidence"
    )
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", str(_DECISIONS_RELATIVE)],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tracked.returncode == 0, f"{_DECISIONS_RELATIVE} must be tracked"

    root_ledger = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", "OPERATOR_DECISIONS.md"],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert root_ledger.returncode != 0, (
        "root OPERATOR_DECISIONS.md is reserved for the operator ledger"
    )

    text = _DECISIONS.read_text(encoding="utf-8")
    headings = _independent_question_headings(text)
    assert len(headings) >= len(_REQUIRED_QUESTIONS)
    # Exact count against an independently parsed list, never a literal
    # (adjudication 2026-09-09 04:09Z: a pinned 2 refused every later question).
    assert _assert_row722_contract(text) == len(headings)


def _valid_record() -> str:
    return (
        "# Tracked evidence fixture\n\n"
        "## Row 722 -- fixture\n\n"
        "Status: DEFAULT\n\n"
        "### Distinct per-host egress\n\n"
        "Decision: Use the measured campaign route.\n\n"
        "Reason: This measured reason is long enough for the shape contract.\n\n"
        "### Keeper re-login cap\n\n"
        "Decision: Apply the measured campaign login cap.\n\n"
        "Reason: This separate reason is also long enough for the shape contract.\n"
    )


def test_transform_control_imports_gate_without_judging_decisions() -> None:
    assert callable(_assert_row722_contract)


def _replace_once(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1
    changed = text.replace(old, new)
    assert changed != text
    return changed


def test_lifecycle_allows_a_replacement_decision() -> None:
    changed = _replace_once(
        _valid_record(),
        "Decision: Use the measured campaign route.",
        "Decision: Route campaign traffic through a later measured fleet path.",
    )
    assert _assert_row722_contract(changed) == len(_independent_question_headings(changed))


def test_lifecycle_allows_a_reworded_decision() -> None:
    changed = _replace_once(
        _valid_record(),
        "Decision: Apply the measured campaign login cap.",
        "Decision: Apply the campaign login cap to each keeper submit before launch.",
    )
    assert _assert_row722_contract(changed) == len(_independent_question_headings(changed))


def test_lifecycle_allows_an_expanded_reason() -> None:
    original = "Reason: This measured reason is long enough for the shape contract."
    changed = _replace_once(
        _valid_record(),
        original,
        original + " Additional measured context may be recorded here later.",
    )
    assert _assert_row722_contract(changed) == len(_independent_question_headings(changed))


def test_lifecycle_allows_an_additional_judged_question() -> None:
    changed = _valid_record() + (
        "\n### Follow-up review\n\n"
        "Decision: Revisit the evidence after the next measured campaign.\n\n"
        "Reason: A later measurement can justify replacing an earlier decision.\n"
    )
    assert changed.count("### ") == 3
    assert _assert_row722_contract(changed) == 3


def test_missing_required_question_has_a_distinct_diagnostic() -> None:
    text = _valid_record()
    start = text.index("### Keeper re-login cap")
    changed = text[:start]
    assert changed.count("### ") == 1

    with pytest.raises(AssertionError) as caught:
        _assert_row722_contract(changed)

    assert str(caught.value) == "row 722 required question missing: Keeper re-login cap"


def test_missing_row_reports_evidence_without_naming_the_operator_ledger() -> None:
    with pytest.raises(AssertionError) as caught:
        _assert_row722_contract("# No row decision evidence\n")

    assert str(caught.value) == (
        "row 722 decision evidence must contain exactly one row section; found 0"
    )


def test_empty_reason_has_a_distinct_diagnostic() -> None:
    original = "Reason: This measured reason is long enough for the shape contract."
    changed = _replace_once(
        _valid_record(), original, "Reason:"
    )

    with pytest.raises(AssertionError) as caught:
        _assert_row722_contract(changed)

    assert str(caught.value) == "row 722 distinct per-host egress reason empty"


def test_decision_without_reason_has_a_distinct_diagnostic() -> None:
    original = "Reason: This separate reason is also long enough for the shape contract."
    changed = _replace_once(
        _valid_record(), original + "\n", ""
    )

    with pytest.raises(AssertionError) as caught:
        _assert_row722_contract(changed)

    assert str(caught.value) == "row 722 keeper re-login cap reason missing"


def test_unrecognised_status_is_rejected() -> None:
    changed = _replace_once(
        _valid_record(),
        "Status: DEFAULT",
        "Status: UNKNOWN",
    )

    with pytest.raises(AssertionError) as caught:
        _assert_row722_contract(changed)

    assert str(caught.value) == "row 722 status is not recognised: UNKNOWN"


def test_nonempty_but_short_reason_reports_the_minimum() -> None:
    original = "Reason: This measured reason is long enough for the shape contract."
    changed = _replace_once(
        _valid_record(), original, "Reason: Brief."
    )

    with pytest.raises(AssertionError) as caught:
        _assert_row722_contract(changed)

    assert str(caught.value) == (
        "row 722 distinct per-host egress reason too short; minimum is 20 characters"
    )


def test_duplicate_question_key_is_rejected() -> None:
    changed = _valid_record() + (
        "\n### Distinct per-host egress\n\n"
        "Decision: A duplicate decision must not replace the first.\n\n"
        "Reason: A duplicate heading would make the judged denominator inaccurate.\n"
    )
    assert changed.count("### Distinct per-host egress") == 2

    with pytest.raises(AssertionError) as caught:
        _assert_row722_contract(changed)

    assert str(caught.value) == "row 722 duplicate question key: Distinct per-host egress"


def test_duplicate_reason_field_is_rejected() -> None:
    marker = "Reason: This measured reason is long enough for the shape contract."
    changed = _replace_once(
        _valid_record(),
        marker,
        marker + "\nReason: A duplicate reason must not replace the first.",
    )
    assert changed.count("Reason:") == 3

    with pytest.raises(AssertionError) as caught:
        _assert_row722_contract(changed)

    assert str(caught.value) == (
        "row 722 distinct per-host egress duplicate field: Reason"
    )


def test_tracked_record_count_is_not_pinned_to_a_number() -> None:
    # Adjudication 2026-09-09 04:09Z (row722-fixb): a third valid question in the
    # tracked record must not turn this tree gate RED. The exact count is the
    # independently parsed heading list, never a literal.
    text = _DECISIONS.read_text(encoding="utf-8")
    headings = _independent_question_headings(text)
    assert len(headings) >= len(_REQUIRED_QUESTIONS)
    assert _assert_row722_contract(text) == len(headings)
    with_third = text.rstrip("\n") + (
        "\n\n### Independent-count probe\n\n"
        "Decision: Revisit the evidence after the next measured campaign.\n\n"
        "Reason: A later measurement can justify replacing an earlier decision.\n"
    )
    assert len(_independent_question_headings(with_third)) == len(headings) + 1
    assert _assert_row722_contract(with_third) == len(headings) + 1
