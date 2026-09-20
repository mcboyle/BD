"""Verify that census tests derive their exact-count pins from manifest files.

Prevents cross-cut train collisions (CLASS: HARNESS) where multiple census cuts
edit the same hardcoded literal integer pins (e.g. test_row805 sites==N,
test_row824 ACCOUNTED==N, test_rowssrf consumers==N), resulting in REBASE-INTRAIN.
"""
from __future__ import annotations

import ast
from pathlib import Path

BD_GATE_SCOPE = "module"

REPO_ROOT = Path(__file__).resolve().parents[1]
CENSUS_TEST_FILES = (
    REPO_ROOT / "tests/test_row805_ssrf_census_covers_every_transport.py",
    REPO_ROOT / "tests/test_row824_guardrails_safety_filter.py",
    REPO_ROOT / "tests/test_rowssrf_loopback_reason_is_structured.py",
)


def _has_literal_equality_pin(func_node: ast.FunctionDef, target_expr_substr: str) -> bool:
    """Return True if func_node contains an assert checking `target_expr == <int literal>`."""
    for stmt in ast.walk(func_node):
        if isinstance(stmt, ast.Assert) and isinstance(stmt.test, ast.Compare):
            compare = stmt.test
            for op, comparator in zip(compare.ops, compare.comparators):
                if isinstance(op, ast.Eq) and isinstance(comparator, ast.Constant) and isinstance(comparator.value, int):
                    left_src = ast.unparse(compare.left)
                    if target_expr_substr in left_src:
                        return True
    return False


def test_row805_pin_is_derived_from_manifest():
    path = REPO_ROOT / "tests/test_row805_ssrf_census_covers_every_transport.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    func = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "test_the_declaration_accounts_for_exactly_the_tree_derived_population"
    )
    assert not _has_literal_equality_pin(func, "len(result.sites)"), (
        "test_row805 must not hardcode a literal integer pin for len(result.sites); "
        "it must derive the expected count from result.declared_keys (the ssrf_egress_exemptions manifest)"
    )


def test_row824_pin_does_not_pin_global_accounted_literal():
    path = REPO_ROOT / "tests/test_row824_guardrails_safety_filter.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    func = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "test_exact_count_of_accounted_egress_and_max_metadata_chars"
    )
    assert not _has_literal_equality_pin(func, "ACCOUNTED"), (
        "test_row824 must not hardcode a literal integer pin on global len(ssrf_egress_exemptions.ACCOUNTED); "
        "it must verify guardrails' own scoped entries in ACCOUNTED"
    )


def test_rowssrf_pin_is_derived_from_manifest():
    path = REPO_ROOT / "tests/test_rowssrf_loopback_reason_is_structured.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assigns = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_EXPECTED_TOTAL_SITES" for target in node.targets)
    ]
    assert len(assigns) == 1, (
        "test_rowssrf must define _EXPECTED_TOTAL_SITES derived from _EXPECTED_RUNTIME_CONSUMERS"
    )
    func = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "test_runtime_consumer_census_judges_every_site_without_english_decisions"
    )
    assert not _has_literal_equality_pin(func, "judged"), (
        "test_rowssrf must not assert judged == <literal int>; it must assert against _EXPECTED_TOTAL_SITES"
    )


def test_negative_control_detects_literal_integer_pin_regression():
    synthetic_code = "def test_foo():\n    assert len(result.sites) == 27\n"
    tree = ast.parse(synthetic_code)
    func = tree.body[0]
    assert _has_literal_equality_pin(func, "len(result.sites)") is True


def test_exact_count_of_census_files_guarded():
    """Exact count assertion: exactly 3 cross-cut census test files guarded against literal pin collisions."""
    assert len(CENSUS_TEST_FILES) == 3
    for path in CENSUS_TEST_FILES:
        assert path.is_file(), f"guarded census test file does not exist: {path}"
