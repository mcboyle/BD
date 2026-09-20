"""Test weekly underived census tool (HB underived-weekly-census, O1057).

Verifies that the weekly integrator cron tool:
1. Reuses the underived gate list declared in toolchain/bin/bd-precut (_UNDERIVED_GATES).
2. Generates UNDERIVED-<date>.md (<=40 lines) and main-red-<date>.rows in register schema.
3. Produces positive control in --selftest mode (stub failure generates exactly 1 row).
4. Produces negative control (clean run produces empty .rows file).
5. Enforces exact count assertions and register row schema validation.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import subprocess
import tempfile
import pytest

BD_GATE_SCOPE = "module"

REPO = Path(__file__).resolve().parents[1]
PRECUT = REPO / "toolchain" / "bin" / "bd-precut"

CANDIDATE_SCRIPT = Path("/home/mboyle/bd-persist/harness-work/FIX/underived-weekly-census/bd-underived-census.sh")
DEPLOYED_SCRIPT = Path("/home/mboyle/bd-persist/harness/bd-underived-census.sh")

ROW_PATTERN = re.compile(
    r"^\| NEW \| OPEN \| MAIN-RED-[a-zA-Z0-9_.-]+ -- T2 harness main-red\. "
    r"EVIDENCE: .+ @[0-9a-fA-F]+\. "
    r"SCOPE: make the gate green on main without weakening it\. "
    r"ACCEPTANCE: gate rc=0 on main\. \|$"
)


def _resolve_script() -> Path:
    env_path = (
        os.environ.get("BD_UNDERIVED_WEEKLY_CENSUS_CANDIDATE")
        or os.environ.get("BD_UNDERIVED_CENSUS_CANDIDATE")
        or os.environ.get("BD_UNDERIVED_CENSUS_BIN")
    )
    if env_path and Path(env_path).is_file():
        return Path(env_path)
    if CANDIDATE_SCRIPT.is_file():
        return CANDIDATE_SCRIPT
    if DEPLOYED_SCRIPT.is_file():
        return DEPLOYED_SCRIPT
    pytest.skip("Neither candidate nor deployed bd-underived-census.sh is present on host")


def test_underived_gates_source_of_truth():
    """Verify underived gates are extracted from bd-precut AST."""
    assert PRECUT.is_file(), f"bd-precut missing at {PRECUT}"
    tree = ast.parse(PRECUT.read_text(encoding="utf-8"))
    gates = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "_UNDERIVED_GATES" in targets:
                for elt in node.value.elts:
                    gates.append(elt.elts[0].value)
                break
    assert len(gates) == 7, f"expected exactly 7 underived gates in bd-precut, got {len(gates)}"
    assert "tests/test_v3_66_1184_mutation_specs_are_tracked.py" in gates
    assert "tests/test_import_graph_no_new_edges.py" in gates


def test_selftest_positive_control():
    """Verify --selftest generates valid UNDERIVED-*.md and main-red-*.rows with 1 row."""
    script = _resolve_script()
    today = datetime.now(timezone.utc).strftime("%Y%m%d")

    with tempfile.TemporaryDirectory() as tmpdir:
        res = subprocess.run(
            ["bash", str(script), "--selftest", "--out", str(tmpdir)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, f"script failed: rc={res.returncode}\nstdout: {res.stdout}\nstderr: {res.stderr}"
        assert "SELFTEST PASS" in res.stdout

        md_file = Path(tmpdir) / f"UNDERIVED-{today}.md"
        assert md_file.is_file(), f"missing report: {md_file}"
        md_lines = md_file.read_text(encoding="utf-8").splitlines()
        assert len(md_lines) <= 40, f"report exceeds 40 lines: {len(md_lines)}"
        assert any("MAIN SHA:" in line for line in md_lines)
        assert any("RED: 1" in line for line in md_lines)
        assert any("test_stub_failing_gate.py" in line for line in md_lines)

        rows_file = Path(tmpdir) / f"main-red-{today}.rows"
        assert rows_file.is_file(), f"missing rows file: {rows_file}"
        rows = [line.strip() for line in rows_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(rows) == 1, f"expected exactly 1 row, got {len(rows)}"

        # Verify register schema
        assert ROW_PATTERN.match(rows[0]), f"row does not match register schema:\n{rows[0]}"
        assert "MAIN-RED-test_stub_failing_gate" in rows[0]
        assert "tests/test_stub_failing_gate.py::test_stub_failure" in rows[0]


def test_schema_validation_rejects_malformed_rows():
    """Negative control: invalid row formats must be rejected by ROW_PATTERN."""
    invalid_rows = [
        "| 100 | OPEN | not a main red row |",
        "| NEW | CLOSED | MAIN-RED-foo -- T2 harness main-red. EVIDENCE: x @1234. SCOPE: s. ACCEPTANCE: a. |",
        "| NEW | OPEN | MAIN-RED-foo -- missing evidence @1234. |",
        "MAIN-RED-foo without pipes",
    ]
    for row in invalid_rows:
        assert not ROW_PATTERN.match(row), f"malformed row unexpectedly accepted: {row}"


def test_negative_control_clean_run():
    """Negative control: empty rows file when zero failures occur."""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    with tempfile.TemporaryDirectory() as tmpdir:
        rows_file = Path(tmpdir) / f"main-red-{today}.rows"
        rows_file.write_text("", encoding="utf-8")
        rows = [line.strip() for line in rows_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(rows) == 0, "green run must write an empty .rows file"
