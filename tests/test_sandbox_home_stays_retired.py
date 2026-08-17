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

REPO = Path(__file__).resolve().parent.parent

# Assembled, never spelled -- see the module docstring.
NEEDLE = "/" + "home" + "/" + "claude"

# Every tracked file permitted to carry the reference, measured at 0f3e435.
# Entries are removed as carriers are retired; none may be added without an
# explicit operator decision recorded in the canonical improvement backlog.
ALLOWLIST: frozenset[str] = frozenset({
    ".superpowers/sdd/wacz-processing-report.md",
    "CHANGELOG.md",
    "SANDBOX.md",
    "VERSION.txt",
    "conf/README.md",
    "conf/supervisord.conf",
    "docs/UX_IMPROVEMENT_PLAN.md",
    "docs/archive/2026-07-22-doc-hygiene/docs/audit/CODE_INTELLIGENCE_DELIVERABLES.md",
    "docs/archive/2026-07-22-doc-hygiene/kb/decomp/CROSS_MONOLITH_IMPORT_GRAPH.md",
    "docs/repo/SANDBOX_SPEC_AND_LAYOUT_v3_66_805.md",
    "docs/repo/TOOLCHAIN_PORTABILITY.md",
    "kb/decomp/DECOMP_TOOLS_README.md",
    "kb/decomp/app_py_F5.1/APP_DECOMP_MAP.json",
    "project-knowledge/10_SANDBOX_SHELL_PREFLIGHT.md",
    "project-knowledge/ADVANCED_PROJECT_KNOWLEDGE.md",
    "project-knowledge/AUDIT_PLAN_v3_66_539.md",
    "project-knowledge/BD_TOOLCHAIN_REFERENCE.md",
    "project-knowledge/CODE_INTELLIGENCE_TOOLING.md",
    "project-knowledge/KB_JUDGMENT.md",
    "project-knowledge/KB_SYNC_WORKFLOW.md",
    "project-knowledge/PRESTAGE_GUIDE.md",
    "project-knowledge/README.md",
    "project-knowledge/RELEASE_DISCIPLINE_TIERS.md",
    "project-knowledge/RENDER_CAPTURE_AUDIT_GUIDE.md",
    "project-knowledge/SANDBOX.md",
    "project-knowledge/SANDBOX_CAPABILITY_LAYER.md",
    "project-knowledge/IMPROVEMENT_BACKLOG.md",
    "FOOTGUNS.json",
    "tests/test_v3_66_1167_safety_authorities_are_single_source.py",
    "project-knowledge/build_montage.py",
    "project-knowledge/build_navigator.py",
    "project-knowledge/capture_all.py",
    "project-knowledge/mobile_drawer_probe.py",
    "project-knowledge/mobile_more_probe.py",
    "project-knowledge/render_check.py",
    "project-knowledge/round.sh",
    "project-knowledge/spa_render.sh",
    "project-knowledge/spa_serve.py",
    "project-knowledge/spa_shot.py",
    "project-knowledge/spa_tabs.py",
    "project-knowledge/subtabs_cap.py",
    "project-knowledge/subtabs_click.py",
    "scripts/build_release.sh",
    "scripts/classify_toolchain.py",
    "scripts/classify_toolchain_verdict.py",
    "scripts/refine_degraded.py",
    "slowest_tests.sh",
    "tests/test_bd_doctor_probes_the_real_environment.py",
    "tests/test_capture_fixture_roots.py",
    "tests/test_desandbox_tool_verifiers.py",
    "tests/test_element_pick_bridge.py",
    "tests/test_element_pick_selector.py",
    "tests/test_env_parity_sees_the_real_browser_pool.py",
    "tests/test_fixture_recognizer_loop.py",
    "tests/test_fresh_install_gui_smoke.py",
    "tests/test_generated_artifact_workflow.py",
    "tests/test_guardcheck_fails_closed.py",
    "tests/test_route_index_in_sync.py",
    "tests/test_toolchain_534.py",
    "tests/test_u27_security_cluster.py",
    "tests/test_v3_66_245_floor_signed_url_nondom.py",
    "tests/test_v3_66_252_dom_excerpt.py",
    "tests/test_v3_66_276_row_selector_robust.py",
    "tests/test_v3_66_527_numeric_integer_backstop.py",
    "tests/test_v3_66_653_dep_freshness.py",
    "tests/test_v3_66_799_audit_tool_selftests.py",
    "toolchain/bdenv.sh",
    "toolchain/bin/bd",
    "toolchain/bin/bd-bandcheck",
    "toolchain/bin/bd-checkpoint",
    "toolchain/bin/bd-consumer-graph",
    "toolchain/bin/bd-corpus",
    "toolchain/bin/bd-cut",
    "toolchain/bin/bd-deep-capture",
    "toolchain/bin/bd-defect-scan",
    "toolchain/bin/bd-docstale",
    "toolchain/bin/bd-doctor",
    "toolchain/bin/bd-env-parity",
    "toolchain/bin/bd-factcheck",
    "toolchain/bin/bd-fixture-lint",
    "toolchain/bin/bd-footguns",
    "toolchain/bin/bd-freshest",
    "toolchain/bin/bd-fullsuite",
    "toolchain/bin/bd-golden",
    "toolchain/bin/bd-guard-declare",
    "toolchain/bin/bd-guardcheck",
    "toolchain/bin/bd-kb-sync",
    "toolchain/bin/bd-lsp",
    "toolchain/bin/bd-mkauditstate",
    "toolchain/bin/bd-mkbdsuite",
    "toolchain/bin/bd-mutation-test",
    "toolchain/bin/bd-opv",
    "toolchain/bin/bd-pack",
    "toolchain/bin/bd-parband",
    "toolchain/bin/bd-parity-scan",
    "toolchain/bin/bd-path-scan",
    "toolchain/bin/bd-pk-mirror",
    "toolchain/bin/bd-precut",
    "toolchain/bin/bd-prestage",
    "toolchain/bin/bd-ready",
    "toolchain/bin/bd-reindex",
    "toolchain/bin/bd-release-note",
    "toolchain/bin/bd-render-env",
    "toolchain/bin/bd-rev",
    "toolchain/bin/bd-rollback",
    "toolchain/bin/bd-rollback-oracle",
    "toolchain/bin/bd-sbcap",
    "toolchain/bin/bd-ship",
    "toolchain/bin/bd-since",
    "toolchain/bin/bd-snapshot",
    "toolchain/bin/bd-state",
    "toolchain/bin/bd-status",
    "toolchain/bin/bd-sweep",
    "toolchain/bin/bd-sym",
    "toolchain/bin/bd-tool-smoke",
    "toolchain/bin/bd-treecheck",
    "toolchain/bin/bd-venv",
    "toolchain/bin/bd-worktree-check",
    "toolchain/bin/bdtools_sec.py",
    "toolchain/install_bdsuite.sh",
    "tools/audit/witnesses/cap01_witnesses.py",
    "tools/audit/witnesses/run01_witnesses.py",
    "tools/bd-scan.py",
    "tools/bd_decomp_lib.py",
    "tools/code_intelligence/oracle_adapters.py",
    "tools/constraint_incidence.py",
    "tools/consumer_agreement.py",
    "tools/coverage_map.py",
    "tools/decomp_lint.py",
    "tools/decomp_regen.py",
    "tools/defect_patterns.py",
    "tools/endpoint_reachability.py",
    "tools/reachability_ledger.py",
    "tools/read_coverage.py",
    "tools/render_advanced_kb.py",
    "tools/review_merge.py",
    "tools/risk_score.py",
    "tools/run_witnesses.py",
    "tools/seed_review_state.py",
    "tools/staleness.py",
    "tools/verify_audit.py",
    "tools/witness_drift.py",
})


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


def _verdict(carriers: set[str], allowlist: frozenset[str]) -> tuple[set[str], set[str]]:
    """(new carriers, stale allowlist entries). Pure, so the synthetic tests
    below can drive it with a fabricated population instead of the real tree."""
    return (carriers - allowlist), (allowlist - carriers)


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
