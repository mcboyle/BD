"""Row 531: a denominator that must be hand-bumped is a chore, not a gate.

Three literals in this tree had to be edited by hand every time the repository
grew in a perfectly ordinary way:

  _EXPECTED_DECLARED_GATE_COUNT            235, and twenty lines of bump comments
  _EXPECTED_CONFIRMED_SAFETY_GATE_COUNT      7
  len(current) == 139 / len(current_docs) == 138   the tracked Markdown corpus

None of them asks a question the surrounding assertions do not already answer.
The gate module already proves a one-to-one declared/executed correspondence
with no duplicates and no strays; the Markdown gate can derive its own corpus
from `git ls-files`. What the literals added was a second CI round-trip, and on
2026-08-31 they cost exactly that twice in one day: a new repo-wide test file
and a new `docs/repo/FLEET_TOPOLOGY.md` each turned a green candidate red for
no defect at all.

Removing a ratchet is a soundness change, so this file holds the line that the
removal must not cross. The properties CLAUDE.md A7 actually requires --
NONZERO, MEMBERSHIP, UNIQUENESS -- are asserted here directly against the live
helpers, each with a negative control proving its intended failure is still
reachable. Only the arbitrary total is gone.

Scope note: the subject is these two modules' helpers, not the tree, so this is
a module gate. The tree-wide questions it protects are still asked by the
modules themselves.
"""
from __future__ import annotations

import ast
import pathlib
import subprocess

import pytest

BD_GATE_SCOPE = "module"

REPO = pathlib.Path(__file__).resolve().parents[1]
GATE_MODULE = REPO / "tests" / "test_v3_66_939_ci_gate_shards_cover_every_gate.py"
DOCS_MODULE = REPO / "tests" / "test_v3_66_1172_nested_freshness_and_legacy_retirement.py"

# A corpus-sized literal. Small integers (a two-element control set, the 14
# historical documents) are not the chore this row is about, so the rule is
# scoped by magnitude as well as by shape.
_CORPUS_SCALE = 20


def _equality_pins(path: pathlib.Path) -> list[tuple[int, str]]:
    """Every `len(...) == <big int or _..._COUNT name>` in a module.

    The rule is about the OPERATOR, not the number. An equality pin breaks every
    time the repository grows in an ordinary way; a `>=` floor never does, and
    still refuses the removal that an equality pin was really there to catch.
    So `>=` is deliberately allowed and `==` is not.

    Parsed, not grepped. A7: when a gate scans source text its own comments and
    examples fall inside the denominator -- and this module's docstring names all
    three retired literals, so a textual scan would report itself.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        if not isinstance(node.ops[0], ast.Eq):
            continue
        for a, b in ((node.left, node.comparators[0]),
                     (node.comparators[0], node.left)):
            if not (isinstance(a, ast.Call) and isinstance(a.func, ast.Name)
                    and a.func.id == "len"):
                continue
            if (isinstance(b, ast.Constant) and isinstance(b.value, int)
                    and not isinstance(b.value, bool) and b.value >= _CORPUS_SCALE):
                found.append((node.lineno, f"len(...) == {b.value}"))
            elif isinstance(b, ast.Name) and b.id.endswith("_COUNT"):
                found.append((node.lineno, f"len(...) == {b.id}"))
    return found


# --------------------------------------------------------------------------
# The literals are gone
# --------------------------------------------------------------------------
def test_the_markdown_corpus_is_not_pinned_to_a_hand_bumped_total():
    offenders = _equality_pins(DOCS_MODULE)
    assert not offenders, (
        "the tracked-Markdown gate compares a derived length against a literal "
        f"total at line(s) {offenders}. Adding one ordinary document then turns "
        "a green candidate red for no defect -- twice on 2026-08-31. Derive the "
        "expected corpus independently instead.")


def test_the_gate_denominator_is_not_pinned_to_a_hand_bumped_total():
    offenders = _equality_pins(GATE_MODULE)
    assert not offenders, (
        f"an exact gate-count equality pin survives at {offenders}. The "
        "declared/executed correspondence already proves membership and "
        "uniqueness, and a floor still catches a removal; the exact total only "
        "adds a second CI round-trip on every cut that declares a gate.")


# --------------------------------------------------------------------------
# ... and every property that mattered is still asserted
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def gate_mod():
    import importlib.util
    spec = importlib.util.spec_from_file_location("row531_gate_module", GATE_MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_coverage_accepts_a_grown_population_with_no_literal(gate_mod):
    """The whole point: three gates, three executions, no expected_count."""
    declared = {"tests/test_a.py", "tests/test_b.py", "tests/test_c.py"}
    shards = {"one": ["tests/test_a.py", "tests/test_b.py"], "two": ["tests/test_c.py"]}
    gate_mod._assert_exact_gate_coverage(declared, shards)


def test_coverage_still_refuses_a_declared_gate_that_no_shard_runs(gate_mod):
    """MEMBERSHIP. This is the failure the file exists for: a gate that is
    declared, is never executed, and leaves CI green."""
    with pytest.raises(AssertionError, match="missing from CI"):
        gate_mod._assert_exact_gate_coverage(
            {"tests/test_a.py", "tests/test_dropped.py"},
            {"one": ["tests/test_a.py"]})


def test_coverage_still_refuses_a_shard_entry_nobody_declared(gate_mod):
    """MEMBERSHIP, the other direction."""
    with pytest.raises(AssertionError, match="undeclared|extra in CI"):
        gate_mod._assert_exact_gate_coverage(
            {"tests/test_a.py"},
            {"one": ["tests/test_a.py", "tests/test_stray.py"]})


def test_coverage_still_refuses_a_repeated_gate_path(gate_mod):
    """UNIQUENESS. Two shards naming one suite would otherwise let the execution
    count match the declaration count while one declared gate never runs."""
    with pytest.raises(AssertionError, match="repeats|duplicate"):
        gate_mod._assert_exact_gate_coverage(
            {"tests/test_a.py", "tests/test_b.py"},
            {"one": ["tests/test_a.py"], "two": ["tests/test_a.py"]})


def test_coverage_still_refuses_an_empty_declaration(gate_mod):
    """NONZERO. A collapsed denominator must never read as full coverage."""
    with pytest.raises(AssertionError, match="nonzero|empty"):
        gate_mod._assert_exact_gate_coverage(set(), {})


# --------------------------------------------------------------------------
# The replacement denominator is independent, not the artifact under test
# --------------------------------------------------------------------------
def _independent_markdown_corpus() -> tuple[list[str], list[str]]:
    """Re-derive the corpus here, from git, WITHOUT importing bdtools_sec.

    A7: do not derive an expected set solely from the artifact under test. This
    is a second implementation of the same rule, so a change to either side is
    visible as a disagreement.
    """
    raw = subprocess.check_output(
        ["git", "ls-files", "-z", "--", "*.md"], cwd=str(REPO), text=True, timeout=60)
    tracked = [p for p in raw.split("\0") if p]
    deleted = set(subprocess.check_output(
        ["git", "ls-files", "--deleted"], cwd=str(REPO), text=True, timeout=60).splitlines())
    current, historical = [], []
    for rel in tracked:
        if rel in deleted:
            continue
        parts = pathlib.PurePosixPath(rel).parts
        if len(parts) != 1 and parts[0] not in ("project-knowledge", "docs"):
            continue
        if rel == "CHANGELOG.md" or parts[:2] == ("docs", "archive"):
            historical.append(rel)
        else:
            current.append(rel)
    return sorted(current), sorted(historical)


def test_the_independent_denominator_is_nonzero_and_unique():
    current, historical = _independent_markdown_corpus()
    assert current, "the independent Markdown denominator collapsed to zero"
    assert len(current) == len(set(current))
    assert len(historical) == len(set(historical))
    assert not set(current) & set(historical)


def test_the_two_implementations_agree_exactly():
    """This is what replaces the literal: an EXACT denominator that is still
    independent. The retired `== 139` compared the corpus against a number a
    human typed; this compares it against a second derivation of the same rule,
    so a change to either side shows up as a named disagreement rather than as
    an off-by-one to be bumped away."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "row531_bdtools_sec", REPO / "toolchain" / "bin" / "bdtools_sec.py")
    sec = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sec)

    deleted = set(subprocess.check_output(
        ["git", "ls-files", "--deleted"], cwd=str(REPO), text=True, timeout=60).splitlines())
    theirs_current, theirs_historical = sec.tracked_markdown_corpus(REPO)
    theirs_current = [p for p in theirs_current if p not in deleted]
    theirs_historical = [p for p in theirs_historical if p not in deleted]
    mine_current, mine_historical = _independent_markdown_corpus()

    assert set(theirs_current) == set(mine_current), (
        "the two Markdown corpus derivations disagree: only bdtools_sec "
        f"{sorted(set(theirs_current) - set(mine_current))}; only this file "
        f"{sorted(set(mine_current) - set(theirs_current))}")
    assert set(theirs_historical) == set(mine_historical), (
        "the two historical-corpus derivations disagree: only bdtools_sec "
        f"{sorted(set(theirs_historical) - set(mine_historical))}; only this "
        f"file {sorted(set(mine_historical) - set(theirs_historical))}")
    for rel in mine_current:
        assert (REPO / rel).is_file(), f"corpus names an absent file: {rel}"
