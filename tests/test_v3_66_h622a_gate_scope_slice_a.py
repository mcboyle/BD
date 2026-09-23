"""H622 slice A: the tests/test_[a-f]*.py acceptance files answer the scope question.

WHY THIS FILE EXISTS. `test_v3_66_939_ci_gate_shards_cover_every_gate.py`
already forces every tracked test file to be classified OR baselined, and
`tests/gate_scope_baseline.txt` is the frozen legacy population that may only
SHRINK. "Or baselined" is the hole H622 measured: 1206 tracked test files had
no marker and answered the question only by sitting in that frozen list, so
the policy held vacuously over them. This slice pays the [a-f] share of that
debt by IDENTITY -- every path leaves the baseline and carries its own marker.

THE DECISION RULE, MEASURED RATHER THAN GUESSED. `repo-wide` means the gate's
subject is the tree, so it must also run in CI. Row 46 measured that repo-wide
scope is NOT derivable from syntax (3 of 8 historical misses), so this cut does
not derive it: the candidates were the slice-A files that build a population by
walking the repository (`rglob`/`os.walk` rooted at the repo, never at a tmp
dir), read individually, plus the one slice-A file CI already schedules. Every
other slice-A file asserts about a module and is marked `module`.

WHAT A `module` ANSWER DOES NOT CLAIM, stated because 939 states the same
limit: nothing here verifies that a `module` answer is HONEST. What it does
claim is strictly more than the baseline entry it replaces -- the file now
declares a scope the shard-coverage gate can police, and the frozen legacy
population shrank by the exact set named here rather than by a count.
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip(
    "yaml",
    reason="PyYAML is declared in requirements-test.txt; a missing import here "
           "means the test environment is unprovisioned")

# Its subject is which tracked test files declare a scope, which is a property
# of the tree.
BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
_BASELINE = _REPO / "tests" / "gate_scope_baseline.txt"
_MARKER = "BD_GATE_SCOPE"
_VALID = {"repo-wide", "module"}

# The slice-A files whose subject is the tree. Pinned by IDENTITY, not count:
# a count cannot tell this migration from someone quietly swapping a different
# path into the repo-wide set. Each must also be named in a CI shard, which is
# what "repo-wide" obliges and what the union assertion in 939 then polices.
REPO_WIDE = (
    "tests/test_all_sources_parse.py",
    "tests/test_confirm_tiers_209.py",
    "tests/test_cut40_dashboard_today_iso.py",
    "tests/test_extraction_core.py",
    "tests/test_fts_external_content_delete.py",
)


# The slice-A files that ALREADY declared repo-wide before this cut, read from
# the base commit. Named rather than counted so the two-direction check below
# cannot be satisfied by swapping one of them for a new claim.
PRE_EXISTING_REPO_WIDE = (
    "tests/test_authority_documents.py",
    "tests/test_bd_tool_lint_unreadable_modes.py",
    "tests/test_capture_csrf_diag_redacts_cookies.py",
    "tests/test_capture_execution_lanes.py",
    "tests/test_capture_provides_a_display.py",
    "tests/test_capture_shell_runtime.py",
    "tests/test_capture_vault_is_isolated.py",
    "tests/test_census_file_size_drift.py",
    "tests/test_changelog_draft_placeholder_is_refused.py",
    "tests/test_child_test_install_dir_isolation.py",
    "tests/test_cockpit_navigation_contract.py",
    "tests/test_cockpit_route_contract.py",
    "tests/test_codex_handoff_stays_retired.py",
    "tests/test_config_parity_ratchet.py",
    "tests/test_csrf_contract_reachability.py",
    "tests/test_csrf_session_bootstrap.py",
    "tests/test_csrf_tool_contracts.py",
    "tests/test_ct1_corpus_validation.py",
    "tests/test_cut_quality_permits.py",
    "tests/test_defect_scan_precision.py",
    "tests/test_deploy_" + "manifest_stays_retired.py",
    "tests/test_desandbox_tool_verifiers.py",
    "tests/test_endpoint_catalog_in_sync.py",
    "tests/test_footgun_detectors_are_executed_by_ci.py",
    "tests/test_frame_hierarchy.py",
    "tests/test_framework_gui.py",
    "tests/test_frontend_dependency_security_floor.py",
    "tests/test_frontend_secret_keys_in_sync.py",
    "tests/test_function_index_in_sync.py",
)

# Slice-A files that declared repo-wide AFTER this cut landed, each named with
# the cut that added it. A new tree-subject gate in [a-f] registers here (and,
# because it is repo-wide, tools/ci_shards.py must place it in a shard) rather
# than being back-dated into PRE_EXISTING_REPO_WIDE.
ADDED_SINCE_REPO_WIDE = (
    # O1264(d): the gate over tools/ci_shards.py itself (names == matrix, the
    # partition is exact, every declared gate is in exactly one shard).
    "tests/test_ci_shards.py",
)


def _slice_a() -> list[str]:
    out = subprocess.run(["git", "ls-files", "--", "tests/test_[a-f]*.py"],
                         cwd=str(_REPO), capture_output=True, text=True, check=True)
    return sorted(out.stdout.split())


def _declared_scope(path: Path):
    """The module-level BD_GATE_SCOPE value, or None. AST, module scope only.

    A docstring or comment naming the marker answers nothing, which matters
    here because this file names it repeatedly and runs over its own slice.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    if _MARKER not in text:
        return None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    scope = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if not any(isinstance(t, ast.Name) and t.id == _MARKER for t in targets):
            continue
        scope = node.value.value if isinstance(node.value, ast.Constant) else "<non-literal>"
    return scope


def _baseline_entries() -> set[str]:
    return {ln.strip() for ln in _BASELINE.read_text("utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")}


def _ci_named_files() -> set[str]:
    """The slice-A files CI's gate-suites job actually schedules.

    O1264(d): ci.yml carries shard NAMES only and its run step resolves each
    shard's files through tools/ci_shards.py, so "named in a CI shard" is
    answered by the resolver over this tree, never by the workflow's text.
    """
    from tools import ci_shards
    scheduled = {rel for files in ci_shards.shards(_REPO).values() for rel in files}
    return set(_slice_a()) & scheduled


def test_the_slice_is_not_empty() -> None:
    """Every assertion below passes vacuously over an empty slice."""
    assert len(_slice_a()) >= 300, "slice A collapsed; the probe lost its population"


def test_the_reader_can_say_no(tmp_path) -> None:
    """Positive control: the marker reader discriminates, so a zero means something."""
    unmarked = tmp_path / "test_unmarked.py"
    unmarked.write_text("def test_x():\n    assert True\n", encoding="utf-8")
    assert _declared_scope(unmarked) is None
    commented = tmp_path / "test_commented.py"
    commented.write_text("# BD_GATE_SCOPE = 'repo-wide'\n", encoding="utf-8")
    assert _declared_scope(commented) is None, "a comment was read as a declaration"
    real = tmp_path / "test_real.py"
    real.write_text("BD_GATE_SCOPE = 'module'\n", encoding="utf-8")
    assert _declared_scope(real) == "module"


def test_every_slice_a_file_declares_a_valid_scope() -> None:
    scopes = {rel: _declared_scope(_REPO / rel) for rel in _slice_a()}
    missing = sorted(rel for rel, s in scopes.items() if s is None)
    assert not missing, (
        f"{len(missing)} tracked tests/test_[a-f]*.py file(s) declare no {_MARKER}:\n  "
        + "\n  ".join(missing))
    bad = sorted(f"{rel} = {s!r}" for rel, s in scopes.items() if s not in _VALID)
    assert not bad, f"{_MARKER} must be one of {sorted(_VALID)}:\n  " + "\n  ".join(bad)


def test_no_slice_a_file_remains_in_the_frozen_baseline() -> None:
    """The debt is paid by identity: the baseline may only shrink, and it
    shrank by exactly the slice-A paths, which are derived here from
    `git ls-files` rather than from a literal that could drift."""
    left = sorted(set(_slice_a()) & _baseline_entries())
    assert not left, (
        f"{len(left)} slice-A path(s) still sit in {_BASELINE.name}:\n  "
        + "\n  ".join(left))


def test_the_repo_wide_slice_a_gates_are_named_and_scheduled() -> None:
    """A repo-wide declaration costs a CI shard entry; that is what makes it
    expensive to declare falsely."""
    assert len(REPO_WIDE) == len(set(REPO_WIDE)) == 5
    wrong = {rel: _declared_scope(_REPO / rel) for rel in REPO_WIDE
             if _declared_scope(_REPO / rel) != "repo-wide"}
    assert not wrong, f"declared tree-subject gates lack an honest marker: {wrong}"
    unscheduled = sorted(set(REPO_WIDE) - _ci_named_files())
    assert not unscheduled, (
        "repo-wide slice-A gate(s) in no CI shard:\n  " + "\n  ".join(unscheduled))


def test_no_other_slice_a_file_claims_repo_wide() -> None:
    """The partition is exact in both directions.

    An undeclared widening would add a gate to `_DECLARED` that no shard runs,
    which 939 would then fail. PRE_EXISTING_REPO_WIDE is the slice-A population
    that already carried the marker before this cut, measured at the base
    commit; it is named here only so this assertion can be exact about what
    THIS cut added.
    """
    claimed = sorted(rel for rel in _slice_a()
                     if _declared_scope(_REPO / rel) == "repo-wide")
    expected = set(REPO_WIDE) | set(PRE_EXISTING_REPO_WIDE) | set(ADDED_SINCE_REPO_WIDE)
    assert claimed == sorted(expected), (
        f"the repo-wide slice-A set moved without this pin: {claimed}")
    assert not (set(REPO_WIDE) & set(PRE_EXISTING_REPO_WIDE)), (
        "a pre-existing gate was counted as a slice-A migration")
    assert not (set(ADDED_SINCE_REPO_WIDE) & (set(REPO_WIDE) | set(PRE_EXISTING_REPO_WIDE))), (
        "a later addition was double-counted against this cut's pins")
