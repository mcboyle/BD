"""Contract tests for the standalone analysis-frontend operator guide."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "code-intelligence" / "ANALYSIS_FRONTENDS.md"
FRONTENDS = (
    "toolchain/bin/bd-coverage-map",
    "tools/semantic_diff.py",
    "tools/reachability.py",
    "tools/differential_oracle.py",
    "tools/fuzz_harness.py",
)
GATES = (
    "tools/bd-audit-gate.py",
    # @943: the project-knowledge copy retired with the mirrors.
    "toolchain/bin/bd-audit-gate.py",
)
ANALYZERS = (
    "semantic_diff.py",
    "reachability.py",
    "differential_oracle.py",
    "fuzz_harness.py",
)


def _document() -> str:
    return DOC.read_text(encoding="utf-8")


def test_every_public_frontend_help_exposes_json_and_gate() -> None:
    """Catch a frontend that is not usable through its public CLI boundary."""
    for frontend in FRONTENDS:
        run = subprocess.run(
            [sys.executable, frontend, "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert run.returncode == 0, (frontend, run.stderr)
        assert "--json" in run.stdout, frontend
        assert "--gate" in run.stdout, frontend


def test_documentation_names_every_command_and_schema_version() -> None:
    """Catch a guide that misidentifies an artifact producer or schema."""
    text = _document()

    assert "python3 toolchain/bin/bd-coverage-map" in text
    for command, schema, version in (
        ("bd-coverage-map", "bd.coverage-gaps", 2),
        ("semantic_diff.py", "bd.semantic-diff", 1),
        ("reachability.py", "bd.reachability", 1),
        ("differential_oracle.py", "bd.differential-oracle", 1),
        ("fuzz_harness.py", "bd.fuzz-results", 1),
    ):
        assert command in text
        assert f"Schema: `{schema}` version {version}." in text
        if command != "bd-coverage-map":
            assert f"python3 tools/{command}" in text


def test_documentation_defines_result_states_gate_behavior_and_exit_codes() -> None:
    """Catch a guide that lets operators treat unknown analysis as passing."""
    text = _document()

    assert "`pass`, `fail`, `advisory`, `unknown`, `timeout`, and `error` are distinct." in text
    assert "Unknown is never a synonym for pass." in text
    assert (
        "`--gate` converts a required unknown or a blocking policy violation into exit 1."
        in text
    )
    assert "| 0 | completed with pass, advisory, or non-gating unknown |" in text
    assert "| 1 | blocking failure, gate-required unknown, timeout, or execution error |" in text
    assert "| 2 | CLI usage or input-validation error |" in text


def test_documentation_locks_standalone_and_analyzer_boundaries() -> None:
    """Catch documentation that hides compatibility or evidence boundaries."""
    text = _document()

    for required in (
        "These commands are standalone.",
        "not wired into `bd-audit-gate`",
        "Omit `--coverage` only to record an explicit `unknown` artifact.",
        "Tree and snapshot inputs are mutually exclusive per side.",
        "`--cst-adapter libcst` is optional and cannot change the standard-library policy verdict.",
        "`--authenticated-fixture module:function`",
        "Operator wiring, navigation, auth probes, graph paths, and deferrals remain separate evidence fields.",
        "Allowed divergences remain visible and do not hide forbidden divergences.",
        "The standard-library replay runner is always available.",
        "Built-in adapters own their internal corpora and reject `--corpus`.",
        "`--generator hypothesis` is optional",
        "Importing the module does not execute fuzzing.",
        "Standard-library execution works without `libcst`, `hypothesis`, or `radon`.",
    ):
        assert required in text


def test_documentation_locks_safe_corpus_paths_and_secret_redaction() -> None:
    """Catch unsafe examples or output claims that permit sensitive capture data."""
    text = _document()

    assert "/path/to/oracle-corpus" in text
    assert "/path/to/oracle-cases.json" not in text
    assert "/path/to/fuzz-corpus.json" not in text
    assert "normalized relative paths" in text
    for prohibited in (
        "credentials",
        "cookies",
        "authorization headers",
        "signed queries",
        "raw captured bodies",
    ):
        assert prohibited in text


def test_no_composite_gate_names_standalone_analyzers() -> None:
    """Catch accidental promotion of standalone analyzers into any audit gate."""
    for gate_path in GATES:
        gate = (ROOT / gate_path).read_text(encoding="utf-8")
        for analyzer in ANALYZERS:
            assert analyzer not in gate, (gate_path, analyzer)
