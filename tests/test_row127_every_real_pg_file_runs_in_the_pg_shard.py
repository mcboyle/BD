"""Row 127 (O1507 cutover): every real-PG test file runs in CI's postgres-integration job.

A real-PG file imports tests/mod3_pg_isolation (the helper whose real_dsn() arms
the lane). Outside that job MOD3_PG_TEST_DSN is unset, so such a file only ever
SKIPS in CI: test_v3_66_1011_mod3_pg_isolation.py (the per-module schema
isolation proof and the zero-skip shard census) was scheduled only in a gate
shard and never executed against a database.

The denominator is derived from the tree (every tracked tests/test_*.py whose
AST imports mod3_pg_isolation), not from a hand list.
"""
from __future__ import annotations

import ast
import shlex
import subprocess
from pathlib import Path

import yaml

BD_GATE_SCOPE = "repo-wide"
ROOT = Path(__file__).resolve().parents[1]
HELPER = "mod3_pg_isolation"


def _imports_helper(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name == HELPER for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == HELPER:
            return True
    return False


def _real_pg_files() -> set[str]:
    out = subprocess.run(["git", "ls-files", "--", "tests/test_*.py"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout.split()
    return {p for p in out if _imports_helper(ROOT / p)}


def _pg_job_files() -> set[str]:
    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))["jobs"]
    runs = [str(s.get("run", "")) for s in jobs["postgres-integration"]["steps"]
            if "python -m pytest" in str(s.get("run", ""))]
    assert len(runs) == 1, runs
    return {a for a in shlex.split(runs[0].replace("\\\n", "")) if a.startswith("tests/")}


def test_the_denominator_is_not_empty_and_sees_a_known_real_pg_file():
    found = _real_pg_files()
    assert "tests/test_v3_66_804_mod3_cutover.py" in found, sorted(found)
    assert "tests/test_h622_ci_test_census.py" not in found, "prose mention counted as an import"


def test_every_real_pg_file_runs_in_the_postgres_integration_job():
    missing = sorted(_real_pg_files() - _pg_job_files())
    assert not missing, (
        f"real-PG test file(s) import {HELPER} but are not in ci.yml postgres-integration, "
        f"so they only ever skip in CI: {missing}")
