"""Row 496 -- a deferral intake is independent of, and reconciles to, the backlog.

The intake is not a second task register: it cannot carry work descriptions or
status.  It records the decision identity and either points a deferred decision
at the sole canonical register, or records a terminal non-deferral disposition
with evidence.  Missing, unreadable, malformed, and empty intake evidence are
UNKNOWN rather than an empty successful iteration.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

import pytest


BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
BACKLOG = ROOT / "project-knowledge" / "IMPROVEMENT_BACKLOG.md"
INTAKE = ROOT / "project-knowledge" / "DEFERRAL_INTAKE.json"
AUDIT_LEDGER = ROOT / "project-knowledge" / "AUDIT_COMPLETION_LEDGER.json"
SCHEMA = "bd-deferral-intake/v1"
REGISTER = "project-knowledge/IMPROVEMENT_BACKLOG.md"
ENTRY_KEYS = frozenset(
    {"id", "disposition", "register_row", "register_title", "evidence"}
)
NON_DEFERRAL = frozenset({"complete", "obsolete", "already-represented"})

_ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*(.*?)\s*\|$")
_META = re.compile(
    r"<!-- canonical-task-register schema=1 rows=\d+ open=\d+ "
    r"ids-sha256=[0-9a-f]{64} -->"
)


class DeferralIntakeUnknown(AssertionError):
    """The intake or its denominator cannot support a verdict."""


def _strict_json(path: Path) -> object:
    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in values:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        raw = path.read_bytes().decode("ascii")
    except (OSError, UnicodeDecodeError) as exc:
        raise DeferralIntakeUnknown(
            f"DEFERRAL INTAKE UNKNOWN: {path} is missing or unreadable ASCII: {exc}"
        ) from exc
    try:
        return json.loads(raw, object_pairs_hook=pairs)
    except (json.JSONDecodeError, ValueError) as exc:
        raise DeferralIntakeUnknown(
            f"DEFERRAL INTAKE UNKNOWN: {path} is not strict JSON: {exc}"
        ) from exc


def _validated_entries(payload: object, origin: str) -> list[dict[str, object]]:
    if not isinstance(payload, dict) or set(payload) != {"schema", "register", "entries"}:
        raise DeferralIntakeUnknown(
            f"DEFERRAL INTAKE UNKNOWN: {origin} must have exactly schema, register, entries"
        )
    if payload["schema"] != SCHEMA or payload["register"] != REGISTER:
        raise DeferralIntakeUnknown(
            f"DEFERRAL INTAKE UNKNOWN: {origin} does not name {SCHEMA} and {REGISTER}"
        )
    entries = payload["entries"]
    if not isinstance(entries, list) or not entries:
        count = len(entries) if isinstance(entries, list) else "non-list"
        raise DeferralIntakeUnknown(
            f"DEFERRAL INTAKE UNKNOWN: intake denominator is {count}; zero decisions cannot be OK"
        )

    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
            raise DeferralIntakeUnknown(
                f"DEFERRAL INTAKE UNKNOWN: entry must have exactly {sorted(ENTRY_KEYS)}"
            )
        identity = entry["id"]
        if not isinstance(identity, str) or not identity.strip() or identity in seen:
            raise DeferralIntakeUnknown(
                f"DEFERRAL INTAKE UNKNOWN: duplicate or empty decision id {identity!r}"
            )
        seen.add(identity)
        disposition = entry["disposition"]
        if disposition not in NON_DEFERRAL | {"deferred"}:
            raise DeferralIntakeUnknown(
                f"DEFERRAL INTAKE UNKNOWN: {identity} has unknown disposition {disposition!r}"
            )
        if not isinstance(entry["evidence"], str) or not entry["evidence"].strip():
            raise DeferralIntakeUnknown(
                f"DEFERRAL INTAKE UNKNOWN: {identity} has no disposition evidence"
            )
        if disposition == "deferred":
            if (
                isinstance(entry["register_row"], bool)
                or not isinstance(entry["register_row"], int)
                or entry["register_row"] < 1
                or not isinstance(entry["register_title"], str)
                or not entry["register_title"].strip()
            ):
                raise DeferralIntakeUnknown(
                    f"DEFERRAL INTAKE UNKNOWN: deferred {identity} has no exact register owner"
                )
        elif entry["register_row"] is not None or entry["register_title"] is not None:
            raise DeferralIntakeUnknown(
                f"DEFERRAL INTAKE UNKNOWN: non-deferral {identity} must demand zero register rows"
            )
    deferred = [entry for entry in entries if entry["disposition"] == "deferred"]
    if not deferred:
        raise DeferralIntakeUnknown(
            "DEFERRAL INTAKE UNKNOWN: deferred denominator is 0; "
            "non-deferral dispositions cannot prove that deferrals were recorded"
        )
    return entries


def _load_entries(path: Path) -> list[dict[str, object]]:
    return _validated_entries(_strict_json(path), str(path))


def _backlog_rows(text: str) -> dict[int, tuple[str, str]]:
    rows: dict[int, tuple[str, str]] = {}
    physical = sum(line.startswith("|") and line.lstrip("| ").split("|", 1)[0].strip().isdigit()
                   for line in text.splitlines())
    for line in text.splitlines():
        match = _ROW.fullmatch(line)
        if match is None:
            continue
        identity = int(match.group(1))
        if identity in rows:
            raise DeferralIntakeUnknown(f"BACKLOG UNKNOWN: duplicate row {identity}")
        rows[identity] = (match.group(2).strip(), match.group(3).strip())
    if not rows or len(rows) != physical:
        raise DeferralIntakeUnknown(
            f"BACKLOG UNKNOWN: parsed {len(rows)} of {physical} physical numeric rows"
        )
    return rows


def _deferred_errors(
    entry: dict[str, object], rows: dict[int, tuple[str, str]]
) -> list[str]:
    errors: list[str] = []
    identity = str(entry["id"])
    row_id = int(entry["register_row"])
    owner = rows.get(row_id)
    if owner is None:
        errors.append(f"{identity}: deferred decision has no backlog row {row_id}")
    else:
        title = str(entry["register_title"])
        matches = int(owner[1].startswith(title + " --"))
        if matches != 1:
            errors.append(
                f"{identity}: backlog row {row_id} does not have exact title {title!r}"
            )
    return errors


def _reconciliation_errors(
    entries: list[dict[str, object]], backlog_text: str
) -> list[str]:
    rows = _backlog_rows(backlog_text)
    errors: list[str] = []
    for entry in entries:
        if entry["disposition"] == "deferred":
            errors.extend(_deferred_errors(entry, rows))
    return errors


def _audit_batch_errors(
    entries: list[dict[str, object]], audit_payload: object, backlog_text: str
) -> list[str]:
    if not isinstance(audit_payload, dict) or not isinstance(audit_payload.get("batches"), list):
        raise DeferralIntakeUnknown("AUDIT CENSUS UNKNOWN: batches are unavailable")
    batches = audit_payload["batches"]
    expected = {row.get("batch") for row in batches if isinstance(row, dict)}
    if len(expected) != len(batches) or None in expected or not expected:
        raise DeferralIntakeUnknown("AUDIT CENSUS UNKNOWN: batch identities are empty or duplicate")
    prefix = "audit-completion-batch:"
    observed = {
        str(entry["id"]).removeprefix(prefix)
        for entry in entries
        if str(entry["id"]).startswith(prefix)
    }
    errors = [] if observed == expected else [
        f"audit batch intake differs: missing={sorted(expected - observed)} extra={sorted(observed - expected)}"
    ]
    rows = _backlog_rows(backlog_text)
    owner = rows.get(140)
    if owner is None or not owner[0].startswith("CLOSED @1171") or "AUDIT_COMPLETION_LEDGER.json" not in owner[1]:
        errors.append("audit completion census is not represented by closed backlog row 140")
    return errors


def _load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _tracked_deferred_entry() -> dict[str, object]:
    deferred = [
        entry for entry in _load_entries(INTAKE)
        if entry["disposition"] == "deferred"
    ]
    assert len(deferred) == 1, (
        f"tracked deferred denominator changed: {len(deferred)}")
    return deferred[0]


def test_tracked_intake_has_a_nonzero_reconciled_decision_population() -> None:
    entries = _load_entries(INTAKE)
    assert len(entries) > 0
    deferred = [entry for entry in entries if entry["disposition"] == "deferred"]
    assert len(deferred) == 1
    errors = _reconciliation_errors(entries, BACKLOG.read_text(encoding="ascii"))
    assert not errors, errors


def test_missing_and_empty_intake_are_unknown_before_any_verdict(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(DeferralIntakeUnknown, match="missing or unreadable ASCII"):
        _load_entries(missing)
    unreadable = tmp_path / "not-a-file"
    unreadable.mkdir()
    with pytest.raises(DeferralIntakeUnknown, match="missing or unreadable ASCII"):
        _load_entries(unreadable)
    with pytest.raises(DeferralIntakeUnknown, match="denominator is 0"):
        _validated_entries({"schema": SCHEMA, "register": REGISTER, "entries": []}, "fixture")


def test_nondeferrals_cannot_mask_a_zero_deferral_denominator() -> None:
    payload = _strict_json(INTAKE)
    assert isinstance(payload, dict)
    entries = payload["entries"]
    assert isinstance(entries, list)
    deferred = [entry for entry in entries if entry["disposition"] == "deferred"]
    retained = [entry for entry in entries if entry["disposition"] != "deferred"]
    assert len(deferred) == 1
    assert len(retained) == 58

    stripped = dict(payload)
    stripped["entries"] = retained
    with pytest.raises(DeferralIntakeUnknown, match="deferred denominator is 0"):
        _validated_entries(stripped, "tracked intake with every deferral stripped")


def test_deleted_row_is_the_one_failure_the_existing_four_predicates_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = _tracked_deferred_entry()
    row_id = int(entry["register_row"])
    original = BACKLOG.read_text(encoding="ascii")
    retained = [
        line for line in original.splitlines()
        if not re.match(rf"^\|\s*{row_id}\s*\|", line)
    ]
    assert len(original.splitlines()) - len(retained) == 1
    modified = "\n".join(retained) + "\n"
    rows = _backlog_rows(modified)
    ids = list(rows)
    digest = hashlib.sha256(",".join(map(str, ids)).encode("ascii")).hexdigest()
    marker = (
        f"<!-- canonical-task-register schema=1 rows={len(rows)} "
        f"open={sum(status == 'OPEN' for status, _ in rows.values())} ids-sha256={digest} -->"
    )
    modified, substitutions = _META.subn(marker, modified)
    assert substitutions == 1

    subject = tmp_path / "IMPROVEMENT_BACKLOG.md"
    subject.write_text(modified, encoding="ascii")
    authority = _load_module("_row496_authority", "tests/test_v3_66_1164_one_task_authority.py")
    monkeypatch.setattr(authority, "BACKLOG", subject)
    authority.test_the_backlog_publishes_and_matches_its_exact_denominator()
    visible = _load_module("_row496_visible", "tests/test_v3_66_1052_the_backlog_is_machine_visible.py")
    monkeypatch.setattr(visible, "BACKLOG", subject)
    visible.test_the_parser_finds_at_least_one_row()
    closed = _load_module("_row496_closed", "tests/test_register_closed_versions_exist.py")
    closed._assert_closed_versions(modified, closed.CHANGELOG.read_text(encoding="utf-8"))
    references = _load_module("_row496_refs", "tests/test_v3_66_1255_backlog_references_resolve.py")
    assert references._missing_reference_errors(references._parse_rows(modified)) == []

    errors = _reconciliation_errors([entry], modified)
    assert errors == [
        f"{entry['id']}: deferred decision has no backlog row {row_id}"]


def test_unmodified_register_is_the_zero_failure_negative_control() -> None:
    entries = [_tracked_deferred_entry()]
    assert len(entries) == 1
    assert _reconciliation_errors(entries, BACKLOG.read_text(encoding="ascii")) == []


def test_all_58_historical_audit_batches_are_non_deferrals_demanding_zero_rows() -> None:
    entries = _load_entries(INTAKE)
    audit_entries = [entry for entry in entries if str(entry["id"]).startswith("audit-completion-batch:")]
    assert len(audit_entries) == 58
    assert {entry["disposition"] for entry in audit_entries} == {"already-represented"}
    assert sum(entry["register_row"] is not None for entry in audit_entries) == 0
    assert sum(entry["register_title"] is not None for entry in audit_entries) == 0
    audit = _strict_json(AUDIT_LEDGER)
    assert _audit_batch_errors(entries, audit, BACKLOG.read_text(encoding="ascii")) == []


def test_one_missing_audit_batch_is_one_named_failure() -> None:
    entries = _load_entries(INTAKE)
    retained = [entry for entry in entries if entry["id"] != "audit-completion-batch:FE-01"]
    assert len(entries) - len(retained) == 1
    errors = _audit_batch_errors(
        retained, _strict_json(AUDIT_LEDGER), BACKLOG.read_text(encoding="ascii")
    )
    assert errors == ["audit batch intake differs: missing=['FE-01'] extra=[]"]


def test_transform_control_only_imports_the_gate() -> None:
    assert callable(_reconciliation_errors)
