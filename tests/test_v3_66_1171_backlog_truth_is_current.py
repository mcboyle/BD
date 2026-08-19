"""Cut A: stale programs cannot remain current task or evidence authorities."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path


BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
BACKLOG = ROOT / "project-knowledge/IMPROVEMENT_BACKLOG.md"
LEDGER = ROOT / "project-knowledge/AUDIT_COMPLETION_LEDGER.json"
DISCOVERY_SHA = "3e8de4ff763a4c0942547ca39322e54ae2cc14c8"
DISCOVERY_TREE = "058af611c010dbadef926468db2c3d2daef37969"

_ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*(.*?)\s*\|$")
_FINDING = re.compile(r"\bF-[A-Z_]+\d+-[A-Za-z0-9_-]+\b")

_TERMINAL = {
    112: "CLOSED",
    134: "MOOT",
    135: "MOOT",
    136: "MOOT",
    137: "MOOT",
    138: "CLOSED",
    140: "CLOSED",
    141: "CLOSED",
    143: "CLOSED",
}

_RETIRED = {
    "ARCHITECTURE_INVENTORY.md",
    "tools/architecture_inventory.py",
    "tests/test_architecture_inventory.py",
    "project-knowledge/BD_IMPROVEMENT_PROGRAM_PLAN.md",
    "project-knowledge/AUDIT_PLAN_v3_66_539.md",
    "kb/decomp/DECOMP_TOOLS_README.md",
}


def _strict_json(path: Path) -> object:
    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in values:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r} in {path}")
            result[key] = value
        return result

    return json.loads(path.read_text(), object_pairs_hook=pairs)


def _rows(text: str | None = None) -> dict[int, tuple[str, str]]:
    found: dict[int, tuple[str, str]] = {}
    for line in (BACKLOG.read_text() if text is None else text).splitlines():
        match = _ROW.match(line)
        if not match or not match.group(1).isdigit():
            continue
        row_id = int(match.group(1))
        assert row_id not in found, f"duplicate backlog row {row_id}"
        found[row_id] = (match.group(2).strip(), match.group(3).strip())
    return found


def _tracked() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    )
    paths = {p.decode() for p in result.stdout.split(b"\0") if p}
    assert len(paths) > 1000
    return paths


def _planned_batches() -> set[str]:
    groups = {
        "FE": 10,
        "CAP": 1,
        "REC": 4,
        "RUN": 3,
        "AUTH": 2,
        "APP": 6,
        "CORE_BD": 19,
        "COCKPIT": 3,
        "TOOLS_BUILD": 2,
        "TOOLS_OTHER": 8,
    }
    result = {
        f"{prefix}-{number:02d}"
        for prefix, count in groups.items()
        for number in range(1, count + 1)
    }
    assert len(result) == 58
    return result


def _finding_ids() -> set[str]:
    roots = ["bulk_downloader", "tools", "toolchain", "scripts", "tests", "frontend/src"]
    result = subprocess.run(
        ["git", "ls-files", "-z", *roots], cwd=ROOT, check=True, capture_output=True
    )
    found: set[str] = set()
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        path = ROOT / raw.decode()
        if path.is_file():
            found.update(_FINDING.findall(path.read_text(errors="ignore")))
    assert found, "finding scan has an empty denominator"
    return found


def test_cut_a_rows_have_exact_terminal_statuses_and_atomic_remainder() -> None:
    rows = _rows()
    for row_id, expected in _TERMINAL.items():
        status, evidence = rows[row_id]
        assert status.startswith(expected + " @1171"), (row_id, status)
        assert evidence

    status, text = rows[165]
    assert status == "CLOSED @1191"
    assert "singleton systemd unit" in text
    assert "remainder -> backlog 175" in rows[5][1]
    assert rows[175][0] == "OPEN"


def test_status_parser_cannot_be_satisfied_by_prose_or_duplicates() -> None:
    fixture = "\n".join(
        [
            "| 1 | OPEN | prose says MOOT @1171 but status is open |",
            "| 2 | MOOT @1171 | terminal |",
        ]
    )
    parsed = _rows(fixture)
    assert parsed[1][0] == "OPEN"
    assert parsed[2][0].startswith("MOOT @1171")

    duplicate = fixture + "\n| 2 | CLOSED @1171 | duplicate |"
    try:
        _rows(duplicate)
    except AssertionError as exc:
        assert "duplicate backlog row 2" in str(exc)
    else:
        raise AssertionError("duplicate row was accepted")


def test_the_eight_bare_closed_remainders_are_terminal_or_transferred() -> None:
    rows = _rows()
    for row_id in (5, 13, 25, 99, 101, 102, 105, 144):
        status, text = rows[row_id]
        assert status.startswith("CLOSED")
        assert not any(
            marker in text
            for marker in (
                "STILL OPEN",
                "WHAT IS NOT CLOSED",
                "WHAT IT STILL CANNOT SEE",
                "NOT COVERED",
                "NOT DONE",
                "UNTESTED CANDIDATE",
                "remains OPEN",
            )
        ), row_id
    assert "remainder -> backlog 175" in rows[5][1]
    assert rows[165][0] == "CLOSED @1191"
    assert rows[175][0] == "OPEN"
    assert "transferred to backlog 133" in rows[99][1]
    assert rows[133][0] == "CLOSED @1173"
    assert "exact 24 pre-policy repository gates" in rows[133][1]
    assert "transferred to backlog 106" in rows[105][1]
    assert rows[106][0].startswith("CLOSED @1172")


def test_retired_programs_and_false_inventory_are_physically_absent() -> None:
    tracked = _tracked()
    for relative in _RETIRED:
        assert relative not in tracked
        assert not os.path.lexists(ROOT / relative), relative

    for relative in (
        "DEPENDENCY_GRAPH.json",
        "DEPENDENCY_GRAPH.md",
        "project-knowledge/ARCHITECTURE_MAP.md",
        "project-knowledge/IMPROVEMENT_BACKLOG.md",
    ):
        assert relative in tracked
        assert (ROOT / relative).is_file()


def test_lexists_control_detects_a_dangling_retired_path(tmp_path: Path) -> None:
    subject = tmp_path / "ARCHITECTURE_INVENTORY.md"
    subject.symlink_to(tmp_path / "missing")
    assert os.path.lexists(subject)
    assert not subject.exists()


def test_audit_completion_ledger_is_an_exact_non_circular_census() -> None:
    payload = _strict_json(LEDGER)
    assert isinstance(payload, dict)
    assert payload["schema_version"] == "bd-audit-completion/1"
    assert payload["source_sha"] == DISCOVERY_SHA
    assert payload["source_tree"] == DISCOVERY_TREE

    batches = payload["batches"]
    assert isinstance(batches, list)
    identities = [row["batch"] for row in batches]
    assert len(identities) == len(set(identities)) == 58
    assert set(identities) == _planned_batches()

    scanned = _finding_ids()
    assert len(scanned) == 72
    recorded = set(payload["finding_ids"])
    assert recorded == scanned
    assigned = [finding for row in batches for finding in row["finding_ids"]]
    assert len(assigned) == len(set(assigned)) == 72
    assert set(assigned) == recorded
    assert payload["legacy_finding_ids"] == ["F0001"]

    aliases = payload["batch_aliases"]
    assert aliases == {
        "CBD": "CORE_BD",
        "COREBD": "CORE_BD",
        "CORE_BD": "CORE_BD",
        "TO": "TOOLS_OTHER",
        "TOOLSO": "TOOLS_OTHER",
        "TOOLS_OTHER": "TOOLS_OTHER",
    }
    for row in batches:
        namespace, number = row["batch"].rsplit("-", 1)
        accepted_namespaces = {namespace}
        accepted_namespaces.update(
            alias for alias, canonical in aliases.items() if canonical == namespace
        )
        accepted_prefixes = {
            spelling + number
            for alias in accepted_namespaces
            for spelling in {alias, alias.replace("_", "")}
        }
        for finding in row["finding_ids"]:
            token = finding.removeprefix("F-").split("-", 1)[0]
            assert token in accepted_prefixes, (row["batch"], finding)

    exact_artifacts = {
        "CAP-01": ["docs/audit/AUDIT_CAP-01_v3_66_532.json"],
        "RUN-01": ["docs/audit/AUDIT_RUN-01_v3_66_532.json"],
    }
    assert {
        row["batch"]: row["artifacts"] for row in batches if row["artifacts"]
    } == exact_artifacts
    allowed = {"artifact_backed", "finding_citations_only", "no_surviving_evidence"}
    assert {row["evidence_class"] for row in batches} <= allowed
    assert all(row["completion_verdict"] in {"unknown", "partial_unknown"} for row in batches)
    assert sum(row["evidence_class"] == "artifact_backed" for row in batches) == 2
    assert sum(row["evidence_class"] == "no_surviving_evidence" for row in batches) == 19
    for row in batches:
        if row["evidence_class"] == "artifact_backed":
            assert row["artifacts"] and row["finding_ids"]
            assert all((ROOT / artifact).is_file() for artifact in row["artifacts"])
        elif row["evidence_class"] == "finding_citations_only":
            assert not row["artifacts"] and row["finding_ids"]
        else:
            assert row["evidence_class"] == "no_surviving_evidence"
            assert not row["artifacts"] and not row["finding_ids"]
            assert row["completion_verdict"] == "unknown"


def test_strict_json_rejects_duplicate_machine_evidence(tmp_path: Path) -> None:
    subject = tmp_path / "duplicate.json"
    subject.write_text('{"schema_version":1,"schema_version":2}')
    try:
        _strict_json(subject)
    except ValueError as exc:
        assert "duplicate JSON key" in str(exc)
    else:
        raise AssertionError("duplicate-key evidence was accepted")


def test_code_intelligence_documents_describe_current_tools() -> None:
    program = (ROOT / "project-knowledge/CODE_INTELLIGENCE_PROGRAM.md").read_text()
    architecture = (ROOT / "project-knowledge/CODE_INTELLIGENCE_ARCHITECTURE.md").read_text()
    frontends = (ROOT / "docs/code-intelligence/ANALYSIS_FRONTENDS.md").read_text()

    assert "toolchain/bin/bd-coverage-map" in program
    assert "bd-coverage-map remains\n`[PLANNED]`" not in program
    assert "differential_oracle.py` `[PLANNED]`" not in architecture
    assert "differential_oracle` above remain genuinely\n  unbuilt" not in architecture
    assert "**`differential_oracle`** `[LIVE as tools/differential_oracle.py]`" in architecture
    assert "bd-finding` and `bd-invariant` command names are absent" in architecture
    assert "auto-emits a RED-test stub" not in architecture
    assert "`bd-audit-gate`** `[BUILT, STANDALONE]`" in architecture
    assert "blocks the build" not in architecture
    assert "bd-audit-gate queries it on every cut" not in architecture
    assert "bd-audit-gate queries it when explicitly invoked" in architecture
    assert "does not run `semantic_diff`" in architecture
    for absent in ("bd-review-next", "bd-finding", "bd-invariant", "bd-dup"):
        assert absent in program
    assert "standalone" in frontends.lower()
    assert "not wired" in frontends.lower()


def test_all_nine_stale_current_documents_have_a_real_disposition() -> None:
    subjects = {
        "UI_TOKENS.md": ("frontend/src/index.css", "m_ops.html"),
        "project-knowledge/KNOWN_FLAKES.md": ("machine-consumed", "test_v3_66_146_nav_guard.py"),
        "project-knowledge/CODE_INTELLIGENCE_PROGRAM.md": ("toolchain/bin/bd-coverage-map", "work/tools/"),
        "project-knowledge/OPERATOR_POLICY_DECISIONS.md": ("BEFORE (retired)", "**CURRENT (charter"),
        "docs/template_health_cockpit.md": ("/cockpit/api/template/autopilot", "cockpit route count is 83"),
        "project-knowledge/DEFECT_PATTERN_CATALOG.md": ("DP-18", "[PLANNED]"),
        "project-knowledge/DECOMP_HAZARD_REGISTER.md": ("H-14", "overlay can't delete"),
        "project-knowledge/KB_JUDGMENT.md": ("loop-read scanner residual closed", "loop-read scanner residual is still open"),
    }
    tracked = _tracked()
    for relative, (current, stale) in subjects.items():
        assert relative in tracked
        text = (ROOT / relative).read_text()
        assert current in text, relative
        assert stale not in text, relative

    retired = "kb/decomp/DECOMP_TOOLS_README.md"
    assert retired not in tracked
    assert not os.path.lexists(ROOT / retired)


def test_template_health_document_matches_current_route_index() -> None:
    routes = _strict_json(ROOT / "ROUTE_INDEX.json")["routes"]
    expected = {
        row["path"]
        for row in routes
        if row["path"].startswith("/cockpit/api/template/")
    }
    assert len(expected) == 16
    assert all(
        row["method"] == "GET"
        for row in routes
        if row["path"] in expected
    )
    text = (ROOT / "docs/template_health_cockpit.md").read_text()
    documented = set(re.findall(
        r"`(/cockpit/api/template/[a-z0-9-]+)`",
        text,
    ))
    assert documented == expected
    assert "never submits\n  credentials and never reads or echoes credential values" in text
    assert "permitted only through the existing approved login path" in text


def test_cut_a_gate_is_directly_wired_once() -> None:
    relative = "tests/test_v3_66_1171_backlog_truth_is_current.py"
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    shard = (ROOT / "tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py").read_text()
    assert workflow.count(relative) == 1
    assert shard.count(f'"{relative}"') == 1
    assert relative not in (ROOT / "tests/gate_scope_baseline.txt").read_text().splitlines()
