"""Cut C: retire the exact pre-policy gate-scope debt.

These 24 tests already guard repository-wide properties and already run in the
CI gate shards.  Their legacy-baseline entries were the remaining ambiguity:
the executable policy knew they were gates while the scope policy called them
unclassified.  Pin the measured migration, not merely a count.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path


BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests" / "gate_scope_baseline.txt"
BASELINE_IDS_SHA256 = "2895a0209b7cd28a2430f87df2d1c356c88e66334b674ea9ad38d0ec750459bc"

MIGRATED = (
    "tests/test_v3_66_1018_registrable_domain_drain.py",
    "tests/test_census_file_size_drift.py",
    "tests/test_zip_era_tools_stay_retired.py",
    "tests/test_v3_66_1013_registrable_domain.py",
    "tests/test_history_records_whether_bytes_were_fetched.py",
    "tests/test_v3_66_972_library_missing_stays_retired.py",
    "tests/test_v3_66_820_share_tools_saw_no_session_keys.py",
    "tests/test_history_file_size_is_the_size_on_disk.py",
    "tests/test_playwright_engines_single_source.py",
    "tests/test_v3_66_938_atomic_write_sidecars_are_ignored.py",
    "tests/test_v3_66_935_scan_wait_reports_non_convergence.py",
    "tests/test_history_columns_go_through_migrations.py",
    "tests/test_v3_66_1059_recorder_derives_its_blind_spot_counts.py",
    "tests/test_v3_66_968_anchor_gate_sees_frontend_citations.py",
    "tests/test_codex_handoff_stays_retired.py",
    "tests/test_sandbox_home_stays_retired.py",
    "tests/test_deploy_" + "manifest_stays_retired.py",
    "tests/test_gitignore_rules_actually_match.py",
    "tests/test_task_tracker_stays_retired.py",
    "tests/test_v3_66_918_tracked_source_denominator.py",
    "tests/test_v3_66_944_static_kb_manifest_describes_the_tree.py",
    "tests/test_generated_artifact_workflow.py",
    "tests/test_git_deploy_gaps_are_documented.py",
    "tests/test_desandbox_tool_verifiers.py",
)


def _scope(path: Path) -> str | None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == "BD_GATE_SCOPE"
                   for target in targets):
                value = node.value
                return value.value if isinstance(value, ast.Constant) else None
    return None


def _baseline_entries() -> set[str]:
    return {
        line.strip()
        for line in BASELINE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def _identity_digest(entries: set[str]) -> str:
    body = "\n".join(sorted(entries)) + "\n"
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def test_the_exact_twenty_four_pre_policy_gates_are_now_explicit() -> None:
    assert len(MIGRATED) == len(set(MIGRATED)) == 24
    assert not (_baseline_entries() & set(MIGRATED))
    wrong = {rel: _scope(ROOT / rel) for rel in MIGRATED
             if _scope(ROOT / rel) != "repo-wide"}
    assert not wrong, f"pre-policy repository gates lack an honest marker: {wrong}"


def test_the_legacy_baseline_shrank_by_the_measured_population() -> None:
    entries = _baseline_entries()
    assert len(entries) == 1249, (
        "gate_scope_baseline must contain the 1,290 pre-Cut-C entries minus "
        "the exact 24 migrated gates and the later classified defect-precision and "
        "template-identity, frontend-secret, capture-vault, and capture-runtime "
        "gates, the three v3.66.1205 capture-posture gates, and the five row-259 "
        "safety gates, the row-298 regen-idempotence gate, and the row-297 "
        "corpus-credential gate, the row-310 secret-runtime-route gate, and "
        "the row-292 capture-lane census gate; "
        "do not trade one unclassified path for another"
    )
    assert _identity_digest(entries) == BASELINE_IDS_SHA256, (
        "the legacy baseline identities changed; a stable count cannot detect a swap"
    )


def test_the_migration_predicate_has_a_nonempty_negative_control(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text('BD_GATE_SCOPE = "module"\n', encoding="utf-8")
    assert _scope(probe) == "module"
    assert _scope(probe) != "repo-wide"
    before = {"tests/a.py", "tests/b.py"}
    after_swap = {"tests/a.py", "tests/c.py"}
    assert len(before) == len(after_swap)
    assert _identity_digest(before) != _identity_digest(after_swap)


def test_the_concrete_scope_misclassification_cannot_return() -> None:
    target = ROOT / "tests" / "test_config_parity_ratchet.py"
    assert _scope(target) == "repo-wide", (
        "config parity inventories the repository root; module scope silently "
        "narrows the execution contract found by the 29-file audit"
    )
