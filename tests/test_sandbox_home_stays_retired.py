"""A ratchet on references to the retired sandbox home directory.

WHY A RATCHET AND NOT A SWEEP. The path names five different subjects with
three different correct dispositions, and several tests exist *in order to*
assert it is retired -- ``test_bd_doctor_probes_the_real_environment`` keeps a
``DEAD`` map whose keys are exactly these paths. A blanket rewrite would delete
those tests' subject and leave them green over nothing, which is CLAUDE.md
section 0's defect manufactured by its own fix. So this gate freezes the
population instead: no NEW carrier may appear, and the list may only shrink.

WHY THE NEEDLE IS ASSEMBLED FROM PARTS. A gate that reads source text has its
own source inside its denominator. Spelling the literal here would make this
file a carrier of the thing it exists to count, so it is built at runtime and
this module stays out of its own population. Section 0 records four separate
occasions where a comment or a literal re-entered the ledger it was written to
describe.

BOTH DIRECTIONS ARE ASSERTED. A ratchet that only fails on a new carrier goes
quietly stale: an allowlist entry whose file no longer carries the reference is
a false claim, and it would let a future carrier reuse that slot silently. The
stale-entry direction is what keeps the list shrinking as tiers 1 and 2 land.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import AbstractSet, Mapping

REPO = Path(__file__).resolve().parent.parent

# Assembled, never spelled -- see the module docstring.
NEEDLE = "/" + "home" + "/" + "claude"

# Every tracked carrier has one machine-readable disposition. ``UNSWEPT`` is
# temporary and separately names the authority phase that owns its removal.
# Other reasons are durable exemptions. The vocabulary is deliberately closed.
ADVERSARIAL_FIXTURE = "adversarial_fixture"
HISTORICAL_RECORD = "historical_record"
PROVENANCE_ARTIFACT = "provenance_artifact"
SUBJECT_REFERENCE = "subject_reference"
UNSWEPT = "unswept"
VALID_REASONS = frozenset({
    ADVERSARIAL_FIXTURE,
    HISTORICAL_RECORD,
    PROVENANCE_ARTIFACT,
    SUBJECT_REFERENCE,
    UNSWEPT,
})
VALID_PHASES = frozenset({"live", "prose", "fixture"})

ALLOWLIST: Mapping[str, str] = {
    ".superpowers/sdd/wacz-processing-report.md": PROVENANCE_ARTIFACT,
    "CHANGELOG.md": HISTORICAL_RECORD,
    "FOOTGUNS.json": HISTORICAL_RECORD,
    "SANDBOX.md": HISTORICAL_RECORD,
    "VERSION.txt": PROVENANCE_ARTIFACT,
    "docs/archive/2026-07-22-doc-hygiene/docs/audit/CODE_INTELLIGENCE_DELIVERABLES.md": HISTORICAL_RECORD,
    "docs/archive/2026-07-22-doc-hygiene/kb/decomp/CROSS_MONOLITH_IMPORT_GRAPH.md": HISTORICAL_RECORD,
    "docs/repo/SANDBOX_SPEC_AND_LAYOUT_v3_66_805.md": PROVENANCE_ARTIFACT,
    "kb/decomp/app_py_F5.1/APP_DECOMP_MAP.json": PROVENANCE_ARTIFACT,
    "project-knowledge/IMPROVEMENT_BACKLOG.md": SUBJECT_REFERENCE,
    "project-knowledge/KB_JUDGMENT.md": HISTORICAL_RECORD,
    "project-knowledge/SANDBOX.md": HISTORICAL_RECORD,
    "scripts/classify_toolchain.py": ADVERSARIAL_FIXTURE,
    "scripts/classify_toolchain_verdict.py": ADVERSARIAL_FIXTURE,
    "scripts/refine_degraded.py": ADVERSARIAL_FIXTURE,
    "tests/test_bd_doctor_probes_the_real_environment.py": ADVERSARIAL_FIXTURE,
    "tests/test_capture_fixture_roots.py": ADVERSARIAL_FIXTURE,
    "tests/test_desandbox_tool_verifiers.py": ADVERSARIAL_FIXTURE,
    "tests/test_element_pick_bridge.py": ADVERSARIAL_FIXTURE,
    "tests/test_element_pick_selector.py": ADVERSARIAL_FIXTURE,
    "tests/test_env_parity_sees_the_real_browser_pool.py": ADVERSARIAL_FIXTURE,
    "tests/test_fixture_recognizer_loop.py": ADVERSARIAL_FIXTURE,
    "tests/test_fresh_install_gui_smoke.py": ADVERSARIAL_FIXTURE,
    "tests/test_generated_artifact_workflow.py": ADVERSARIAL_FIXTURE,
    "tests/test_guardcheck_fails_closed.py": ADVERSARIAL_FIXTURE,
    "tests/test_route_index_in_sync.py": ADVERSARIAL_FIXTURE,
    "tests/test_toolchain_534.py": ADVERSARIAL_FIXTURE,
    "tests/test_u27_security_cluster.py": ADVERSARIAL_FIXTURE,
    "tests/test_v3_66_1167_safety_authorities_are_single_source.py": ADVERSARIAL_FIXTURE,
    "tests/test_v3_66_245_floor_signed_url_nondom.py": ADVERSARIAL_FIXTURE,
    "tests/test_v3_66_252_dom_excerpt.py": ADVERSARIAL_FIXTURE,
    "tests/test_v3_66_276_row_selector_robust.py": ADVERSARIAL_FIXTURE,
    "tests/test_v3_66_527_numeric_integer_backstop.py": ADVERSARIAL_FIXTURE,
    "tests/test_v3_66_653_dep_freshness.py": ADVERSARIAL_FIXTURE,
    "tests/test_v3_66_799_audit_tool_selftests.py": ADVERSARIAL_FIXTURE,
    "toolchain/bin/bd": HISTORICAL_RECORD,
    "toolchain/bin/bd-bandcheck": HISTORICAL_RECORD,
    "toolchain/bin/bd-consumer-graph": HISTORICAL_RECORD,
    "toolchain/bin/bd-corpus": HISTORICAL_RECORD,
    "toolchain/bin/bd-cut": HISTORICAL_RECORD,
    "toolchain/bin/bd-deep-capture": HISTORICAL_RECORD,
    "toolchain/bin/bd-defect-scan": ADVERSARIAL_FIXTURE,
    "toolchain/bin/bd-docstale": HISTORICAL_RECORD,
    "toolchain/bin/bd-doctor": HISTORICAL_RECORD,
    "toolchain/bin/bd-env-parity": HISTORICAL_RECORD,
    "toolchain/bin/bd-factcheck": HISTORICAL_RECORD,
    "toolchain/bin/bd-fixture-lint": ADVERSARIAL_FIXTURE,
    "toolchain/bin/bd-footguns": HISTORICAL_RECORD,
    "toolchain/bin/bd-fullsuite": HISTORICAL_RECORD,
    "toolchain/bin/bd-golden": ADVERSARIAL_FIXTURE,
    "toolchain/bin/bd-guard-declare": HISTORICAL_RECORD,
    "toolchain/bin/bd-kb-sync": HISTORICAL_RECORD,
    "toolchain/bin/bd-lsp": HISTORICAL_RECORD,
    "toolchain/bin/bd-mutation-test": HISTORICAL_RECORD,
    "toolchain/bin/bd-opv": HISTORICAL_RECORD,
    "toolchain/bin/bd-parband": HISTORICAL_RECORD,
    "toolchain/bin/bd-parity-scan": HISTORICAL_RECORD,
    "toolchain/bin/bd-path-scan": ADVERSARIAL_FIXTURE,
    "toolchain/bin/bd-pk-mirror": HISTORICAL_RECORD,
    "toolchain/bin/bd-precut": HISTORICAL_RECORD,
    "toolchain/bin/bd-ready": HISTORICAL_RECORD,
    "toolchain/bin/bd-release-note": HISTORICAL_RECORD,
    "toolchain/bin/bd-render-env": HISTORICAL_RECORD,
    "toolchain/bin/bd-rev": HISTORICAL_RECORD,
    "toolchain/bin/bd-rollback-oracle": HISTORICAL_RECORD,
    "toolchain/bin/bd-sweep": ADVERSARIAL_FIXTURE,
    "toolchain/bin/bd-sym": HISTORICAL_RECORD,
    "toolchain/bin/bd-tool-smoke": ADVERSARIAL_FIXTURE,
    "toolchain/bin/bd-treecheck": HISTORICAL_RECORD,
    "tools/audit/witnesses/cap01_witnesses.py": HISTORICAL_RECORD,
    "tools/decomp_lint.py": HISTORICAL_RECORD,
}

UNSWEPT_PHASES: Mapping[str, str] = {
    path: (
        "fixture" if path == "tests/test_u27_security_cluster.py"
        else "prose" if path in {
            "conf/README.md",
            "docs/UX_IMPROVEMENT_PLAN.md",
            "project-knowledge/10_SANDBOX_SHELL_PREFLIGHT.md",
            "project-knowledge/ADVANCED_PROJECT_KNOWLEDGE.md",
            "project-knowledge/CODE_INTELLIGENCE_TOOLING.md",
            "project-knowledge/KB_SYNC_WORKFLOW.md",
            "project-knowledge/PRESTAGE_GUIDE.md",
            "project-knowledge/README.md",
            "project-knowledge/RELEASE_DISCIPLINE_TIERS.md",
            "project-knowledge/RENDER_CAPTURE_AUDIT_GUIDE.md",
            "project-knowledge/SANDBOX_CAPABILITY_LAYER.md",
        }
        else "live"
    )
    for path, reason in ALLOWLIST.items()
    if reason == UNSWEPT
}
CLOSED_PHASES: AbstractSet[str] = frozenset({"live", "prose", "fixture"})

FIXTURE_JUDGMENTS: Mapping[str, str] = {
    "tests/test_bd_doctor_probes_the_real_environment.py": "retired_path_detector",
    "tests/test_capture_fixture_roots.py": "retired_path_detector",
    "tests/test_desandbox_tool_verifiers.py": "retired_path_detector",
    "tests/test_element_pick_bridge.py": "runtime_fixture",
    "tests/test_element_pick_selector.py": "runtime_fixture",
    "tests/test_env_parity_sees_the_real_browser_pool.py": "retired_path_detector",
    "tests/test_fixture_recognizer_loop.py": "runtime_fixture",
    "tests/test_fresh_install_gui_smoke.py": "runtime_fixture",
    "tests/test_generated_artifact_workflow.py": "retired_path_detector",
    "tests/test_guardcheck_fails_closed.py": "retired_path_detector",
    "tests/test_route_index_in_sync.py": "runtime_fixture",
    "tests/test_toolchain_534.py": "historical_regression",
    "tests/test_u27_security_cluster.py": "security_boundary_fixture",
    "tests/test_v3_66_1167_safety_authorities_are_single_source.py": "retired_path_detector",
    "tests/test_v3_66_245_floor_signed_url_nondom.py": "runtime_fixture",
    "tests/test_v3_66_252_dom_excerpt.py": "runtime_fixture",
    "tests/test_v3_66_276_row_selector_robust.py": "runtime_fixture",
    "tests/test_v3_66_527_numeric_integer_backstop.py": "historical_regression",
    "tests/test_v3_66_653_dep_freshness.py": "historical_regression",
    "tests/test_v3_66_799_audit_tool_selftests.py": "historical_regression",
}

FIXTURE_OCCURRENCE_COUNTS: Mapping[str, int] = {
    "tests/test_bd_doctor_probes_the_real_environment.py": 3,
    "tests/test_capture_fixture_roots.py": 1,
    "tests/test_desandbox_tool_verifiers.py": 8,
    "tests/test_element_pick_bridge.py": 1,
    "tests/test_element_pick_selector.py": 1,
    "tests/test_env_parity_sees_the_real_browser_pool.py": 6,
    "tests/test_fixture_recognizer_loop.py": 2,
    "tests/test_fresh_install_gui_smoke.py": 1,
    "tests/test_generated_artifact_workflow.py": 1,
    "tests/test_guardcheck_fails_closed.py": 3,
    "tests/test_route_index_in_sync.py": 3,
    "tests/test_toolchain_534.py": 8,
    "tests/test_u27_security_cluster.py": 3,
    "tests/test_v3_66_1167_safety_authorities_are_single_source.py": 2,
    "tests/test_v3_66_245_floor_signed_url_nondom.py": 1,
    "tests/test_v3_66_252_dom_excerpt.py": 1,
    "tests/test_v3_66_276_row_selector_robust.py": 1,
    "tests/test_v3_66_527_numeric_integer_backstop.py": 1,
    "tests/test_v3_66_653_dep_freshness.py": 2,
    "tests/test_v3_66_799_audit_tool_selftests.py": 1,
}

# One load-bearing subject per fixture. Prefix with NEEDLE at evaluation time so
# this gate remains outside the carrier population it measures.
FIXTURE_ANCHORS: Mapping[str, str] = {
    "tests/test_bd_doctor_probes_the_real_environment.py": "/work",
    "tests/test_capture_fixture_roots.py": "/corpus/wacz",
    "tests/test_desandbox_tool_verifiers.py": "/bin",
    "tests/test_element_pick_bridge.py": "/.cache/ms-playwright/chromium-1223",
    "tests/test_element_pick_selector.py": "/.cache/ms-playwright/chromium-1223",
    "tests/test_env_parity_sees_the_real_browser_pool.py": "",
    "tests/test_fixture_recognizer_loop.py": "/.cache/ms-playwright",
    "tests/test_fresh_install_gui_smoke.py": "/.cache/ms-playwright/chromium-1223",
    "tests/test_generated_artifact_workflow.py": "/bin/bd-regen-order",
    "tests/test_guardcheck_fails_closed.py": "/nextsess/STATE.json",
    "tests/test_route_index_in_sync.py": "/work/bulk_downloader/app.py",
    "tests/test_toolchain_534.py": "/work/venv/bin/python",
    "tests/test_u27_security_cluster.py": "/bd/downloads",
    "tests/test_v3_66_1167_safety_authorities_are_single_source.py": "",
    "tests/test_v3_66_245_floor_signed_url_nondom.py": "/work",
    "tests/test_v3_66_252_dom_excerpt.py": "/.cache/ms-playwright/chromium-1223",
    "tests/test_v3_66_276_row_selector_robust.py": "/.cache/ms-playwright/chromium-1223",
    "tests/test_v3_66_527_numeric_integer_backstop.py": "/fixture_numeric_sites.json",
    "tests/test_v3_66_653_dep_freshness.py": "/capture",
    "tests/test_v3_66_799_audit_tool_selftests.py": "/bin",
}


def _tracked_carriers() -> set[str]:
    """Tracked files whose bytes contain the needle.

    `git ls-files -z` rather than a `*.py` glob: 231 of this repo's tracked
    Python files are extensionless `bd-*` scripts a glob cannot see, and the
    carriers span shell, markdown and JSON as well.
    """
    out = subprocess.run(["git", "ls-files", "-z"], cwd=str(REPO),
                         capture_output=True, text=True, check=True).stdout
    carriers = set()
    for rel in out.split("\0"):
        if not rel:
            continue
        p = REPO / rel
        try:
            if NEEDLE in p.read_text(encoding="utf-8", errors="replace"):
                carriers.add(rel)
        except (OSError, UnicodeDecodeError):
            continue
    return carriers


def _classification_errors(
    allowlist: Mapping[str, str],
    unswept_phases: Mapping[str, str],
    closed_phases: AbstractSet[str],
) -> list[str]:
    errors: list[str] = []
    for phase in sorted(VALID_PHASES - set(closed_phases)):
        errors.append(f"required phase is not closed: {phase}")
    for phase in sorted(set(closed_phases) - VALID_PHASES):
        errors.append(f"unknown closed phase: {phase}")
    for path, reason in sorted(allowlist.items()):
        if reason not in VALID_REASONS:
            errors.append(f"invalid reason for {path}: {reason}")
        if reason == UNSWEPT and path not in unswept_phases:
            errors.append(f"UNSWEPT entry has no phase: {path}")
    for path, phase in sorted(unswept_phases.items()):
        if path not in allowlist:
            errors.append(f"phase names an unclassified carrier: {path}")
        elif allowlist[path] != UNSWEPT:
            errors.append(f"non-UNSWEPT entry has a phase: {path}")
        if phase not in VALID_PHASES:
            errors.append(f"invalid phase for {path}: {phase}")
        elif phase in closed_phases:
            errors.append(f"closed {phase} phase still has UNSWEPT entry: {path}")
    return errors


def _fixture_preservation_errors(
    expected_counts: Mapping[str, int],
    anchors: Mapping[str, str],
    texts: Mapping[str, str],
) -> list[str]:
    errors: list[str] = []
    if set(expected_counts) != set(anchors):
        errors.append("fixture count and anchor denominators differ")
    for path, expected in sorted(expected_counts.items()):
        text = texts.get(path, "")
        actual = text.count(NEEDLE)
        if actual != expected:
            errors.append(f"fixture occurrence count moved for {path}: {actual} != {expected}")
        anchor = anchors.get(path)
        if anchor is not None and NEEDLE + anchor not in text:
            errors.append(f"fixture semantic anchor disappeared: {path}")
    return errors


def _verdict(
    carriers: set[str], allowlist: Mapping[str, str] | AbstractSet[str]
) -> tuple[set[str], set[str]]:
    """(new carriers, stale allowlist entries). Pure, so the synthetic tests
    below can drive it with a fabricated population instead of the real tree."""
    allowed = set(allowlist)
    return (carriers - allowed), (allowed - carriers)


def _require_population(carriers: set[str]) -> None:
    """Refuse to mint a verdict over an empty scan.

    Extracted rather than inlined so it can be driven with an empty set below.
    A guard whose failing branch no test can reach is the same unexercised
    assertion this gate exists to prevent.
    """
    assert carriers, (
        "BD-GATE-UNRUNNABLE: zero carriers found. Either the population really "
        "reached zero -- in which case delete this gate deliberately -- or the "
        "scan is blind and its clean verdict means nothing."
    )


def test_no_new_sandbox_home_carriers():
    carriers = _tracked_carriers()
    _require_population(carriers)
    errors = _classification_errors(ALLOWLIST, UNSWEPT_PHASES, CLOSED_PHASES)
    assert not errors, errors
    new, stale = _verdict(carriers, ALLOWLIST)
    assert not new, (
        f"{len(new)} tracked file(s) carry the retired sandbox home path and "
        f"are not allowlisted: {sorted(new)[:10]}"
    )
    assert not stale, (
        f"{len(stale)} allowlist entr(ies) no longer carry it -- the list is "
        f"claiming something false and must shrink: {sorted(stale)[:10]}"
    )


def test_the_ratchet_fires_on_a_new_carrier():
    """RED direction 1. A gate that cannot fail here proves nothing."""
    new, stale = _verdict({"a.py", "b.py"}, frozenset({"a.py"}))
    assert new == {"b.py"} and not stale


def test_the_ratchet_fires_on_a_stale_allowlist_entry():
    """RED direction 2. Without this the list silently rots as carriers go."""
    new, stale = _verdict({"a.py"}, frozenset({"a.py", "gone.py"}))
    assert stale == {"gone.py"} and not new


def test_the_population_guard_refuses_an_empty_scan():
    """RED direction 3. Without this the section-0 guard is never executed."""
    import pytest
    with pytest.raises(AssertionError, match="BD-GATE-UNRUNNABLE"):
        _require_population(set())
    _require_population({"a.py"})  # and does not fire when the scan found work


PRESERVED_NEEDLE = NEEDLE


def test_every_carrier_has_a_closed_reason_and_unswept_phase():
    assert isinstance(ALLOWLIST, dict), "carrier exemptions have no reasons"
    assert set(ALLOWLIST) == _tracked_carriers()
    assert set(ALLOWLIST.values()) <= VALID_REASONS
    errors = _classification_errors(ALLOWLIST, UNSWEPT_PHASES, CLOSED_PHASES)
    assert not errors, errors


def test_classification_rejects_an_invalid_reason():
    errors = _classification_errors(
        {"bad.py": "made_up"}, {}, VALID_PHASES
    )
    assert errors == ["invalid reason for bad.py: made_up"]


def test_classification_rejects_an_unphased_unswept_entry():
    errors = _classification_errors(
        {"left.py": "unswept"}, {}, VALID_PHASES
    )
    assert errors == ["UNSWEPT entry has no phase: left.py"]


def test_classification_rejects_unswept_work_after_its_phase_closes():
    errors = _classification_errors(
        {"left.py": "unswept"}, {"left.py": "live"}, VALID_PHASES
    )
    assert errors == ["closed live phase still has UNSWEPT entry: left.py"]


def test_classification_rejects_each_missing_closed_phase():
    for phase in sorted(VALID_PHASES):
        errors = _classification_errors({}, {}, VALID_PHASES - {phase})
        assert errors == [f"required phase is not closed: {phase}"]

    errors = _classification_errors({}, {}, VALID_PHASES | {"invented"})
    assert errors == ["unknown closed phase: invented"]


def test_intentional_adversarial_and_historical_literals_remain():
    sentinels = {
        "toolchain/bin/bd-defect-scan": "GHOST_DIR_DOES_NOT_EXIST",
        "toolchain/bin/bd-fixture-lint": "GHOST_NO_SUCH_CORPUS",
        "toolchain/bin/bd-golden": "GHOST_NO_TREE",
        "toolchain/bin/bd-path-scan": "GHOST_DIR_DOES_NOT_EXIST",
        "toolchain/bin/bd-sweep": "BD_SWEEP_GHOST_DIR_DOES_NOT_EXIST",
    }
    for relative, suffix in sentinels.items():
        path = REPO / relative
        assert path.is_file(), f"preserved sentinel disappeared: {relative}"
        text = path.read_text(encoding="utf-8")
        assert PRESERVED_NEEDLE + "/" + suffix in text, relative

    historical_counts = []
    for relative in ("SANDBOX.md", "project-knowledge/SANDBOX.md"):
        text = (REPO / relative).read_text(encoding="utf-8")
        assert "## 11." in text, f"historical/current boundary missing: {relative}"
        historical, current = text.split("## 11.", 1)
        assert "RETIRED ENVIRONMENT - HISTORICAL RECORD" in historical
        historical_counts.append(historical.count(PRESERVED_NEEDLE))
        assert PRESERVED_NEEDLE not in current, relative
    assert historical_counts[0] > 0
    assert historical_counts[0] == historical_counts[1]


def test_every_test_fixture_carrier_has_a_hand_adjudicated_role():
    fixture_carriers = {
        path for path in _tracked_carriers() if path.startswith("tests/")
    }
    assert fixture_carriers == set(FIXTURE_JUDGMENTS)
    assert set(FIXTURE_JUDGMENTS.values()) == {
        "historical_regression",
        "retired_path_detector",
        "runtime_fixture",
        "security_boundary_fixture",
    }
    assert all(ALLOWLIST[path] == ADVERSARIAL_FIXTURE for path in fixture_carriers)


def test_fixture_occurrences_and_semantic_anchors_are_exact():
    expected_counts = {
        "tests/test_bd_doctor_probes_the_real_environment.py": 3,
        "tests/test_capture_fixture_roots.py": 1,
        "tests/test_desandbox_tool_verifiers.py": 8,
        "tests/test_element_pick_bridge.py": 1,
        "tests/test_element_pick_selector.py": 1,
        "tests/test_env_parity_sees_the_real_browser_pool.py": 6,
        "tests/test_fixture_recognizer_loop.py": 2,
        "tests/test_fresh_install_gui_smoke.py": 1,
        "tests/test_generated_artifact_workflow.py": 1,
        "tests/test_guardcheck_fails_closed.py": 3,
        "tests/test_route_index_in_sync.py": 3,
        "tests/test_toolchain_534.py": 8,
        "tests/test_u27_security_cluster.py": 3,
        "tests/test_v3_66_1167_safety_authorities_are_single_source.py": 2,
        "tests/test_v3_66_245_floor_signed_url_nondom.py": 1,
        "tests/test_v3_66_252_dom_excerpt.py": 1,
        "tests/test_v3_66_276_row_selector_robust.py": 1,
        "tests/test_v3_66_527_numeric_integer_backstop.py": 1,
        "tests/test_v3_66_653_dep_freshness.py": 2,
        "tests/test_v3_66_799_audit_tool_selftests.py": 1,
    }
    assert globals().get("FIXTURE_OCCURRENCE_COUNTS") == expected_counts
    assert sum(FIXTURE_OCCURRENCE_COUNTS.values()) == 50
    assert set(FIXTURE_ANCHORS) == set(expected_counts)

    errors = _fixture_preservation_errors(
        FIXTURE_OCCURRENCE_COUNTS, FIXTURE_ANCHORS,
        {path: (REPO / path).read_text(encoding="utf-8") for path in expected_counts},
    )
    assert not errors, errors


def test_the_gate_does_not_reenter_its_own_carrier_population():
    assert "tests/test_sandbox_home_stays_retired.py" not in _tracked_carriers()


BD_GATE_SCOPE = "repo-wide"
