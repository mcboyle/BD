"""Cut 6: historical plans are evidence, never a second work queue."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


BD_GATE_SCOPE = "repo-wide"
ROOT = Path(__file__).resolve().parents[1]
_ROW = re.compile(r"^\|\s*\d+\s*\|\s*(?P<status>[A-Z]+)[^|]*\|\s*(?P<text>.+?)\s*\|\s*$")

HISTORICAL_KEEP = {
    "docs/archive/2026-07-22-doc-hygiene/README.md",
    "docs/archive/2026-07-22-doc-hygiene/docs/audit/CODE_INTELLIGENCE_DELIVERABLES.md",
    "docs/archive/2026-07-22-doc-hygiene/docs/framework/PHASE2_SELECTOR_DESIGN.md",
    "docs/archive/2026-07-22-doc-hygiene/docs/repo/BUNDLE_VALIDATION_v3_66_805.md",
    "docs/archive/2026-07-22-doc-hygiene/docs/repo/MOD1_ARCH_B_STATUS.md",
    "docs/archive/2026-07-22-doc-hygiene/docs/repo/MOD1_C4_C8_SANDBOX_PROBE.md",
    "docs/archive/2026-07-22-doc-hygiene/kb/decomp/CROSS_MONOLITH_IMPORT_GRAPH.md",
    "docs/archive/2026-07-22-doc-hygiene/kb/decomp/app_py_F5.1/F5.1_KERNEL_CONTRACT.md",
    "docs/archive/2026-07-22-doc-hygiene/kb/decomp/runner/DECOMPOSITION_LOG.md",
    "docs/archive/2026-07-22-doc-hygiene/project-knowledge/BD_SYSTEM_DEEP_DIVE_30_PHASES.md",
    "docs/archive/2026-07-22-doc-hygiene/project-knowledge/DECOMPOSITION_PROGRAM_ROADMAP.md",
    "docs/archive/2026-07-22-doc-hygiene/root/CAPTURE_CONVERGENCE_MAP.md",
    "docs/archive/2026-07-22-doc-hygiene/root/DARK_CLUSTER_ADJUDICATION_v3_66_753.md",
}
REMOVED_ARCHIVE = {
    "docs/archive/2026-07-22-doc-hygiene/docs/PHASE4_RETIREMENT_PLAN.md",
    "docs/archive/2026-07-22-doc-hygiene/duplicates/kb/decomp/DECOMPOSITION_PROGRAM_ROADMAP_UNMARKED.md",
    "docs/archive/2026-07-22-doc-hygiene/duplicates/root/AUTOMATION_POLICY.md",
    "docs/archive/2026-07-22-doc-hygiene/duplicates/toolchain/BDSUITE_CHANGELOG.md",
    "docs/archive/2026-07-22-doc-hygiene/kb/decomp/app_py_F5.1/F5.1_CUT_RUNBOOK.md",
    "docs/archive/2026-07-22-doc-hygiene/kb/decomp/app_py_F5.1/F5.1_DECOMPOSITION_PLAN.md",
    "docs/archive/2026-07-22-doc-hygiene/kb/decomp/app_py_F5.1/F5.1_LEDGER.md",
    "docs/archive/2026-07-22-doc-hygiene/kb/decomp/runner/README.md",
    "docs/archive/2026-07-22-doc-hygiene/kb/decomp/runner/RUNNER_DECOMPOSITION_PLAN.md",
    "docs/archive/2026-07-22-doc-hygiene/project-knowledge/AUTOMATION_PROGRAM_PLAN.md",
    "docs/archive/2026-07-22-doc-hygiene/project-knowledge/CHANGELOG_RECENT_v3_66_732_753.md",
    "docs/archive/2026-07-22-doc-hygiene/project-knowledge/OPERATOR_VERIFICATION_GUIDE.md",
    "docs/archive/2026-07-22-doc-hygiene/project-knowledge/OPV_AUDIT_AND_GUIDE_v3_66_267.md",
    "docs/archive/2026-07-22-doc-hygiene/project-knowledge/OPV_OPERATOR_RUNBOOK_v3_66_266.md",
    "docs/archive/2026-07-22-doc-hygiene/project-knowledge/PLUGIN_V3_PLAN.md",
}


def _tracked(root: Path = ROOT) -> set[str]:
    proc = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    paths = {p.decode() for p in proc.stdout.split(b"\0") if p}
    assert len(paths) > 1000
    return paths


def test_only_adjudicated_historical_evidence_remains():
    tracked = _tracked()
    archived = {p for p in tracked if p.startswith("docs/archive/")}
    assert archived == HISTORICAL_KEEP
    assert not any(p.startswith("docs/superpowers/plans/") for p in tracked)
    assert not any(p.startswith("docs/superpowers/specs/") for p in tracked)
    assert not any(p.startswith("project-knowledge/pending-specs/") for p in tracked)


def test_removed_document_names_cannot_survive_as_dangling_paths():
    tracked = _tracked()
    removed = {
        p
        for p in tracked
        if p.startswith(("docs/superpowers/plans/", "docs/superpowers/specs/"))
    }
    assert not removed
    for parent in (ROOT / "docs/superpowers/plans", ROOT / "docs/superpowers/specs"):
        assert not os.path.lexists(parent)
    returned_archive = sorted(rel for rel in REMOVED_ARCHIVE if os.path.lexists(ROOT / rel))
    assert not returned_archive, f"retired archive paths returned: {returned_archive}"


def _open_label_counts(text: str, labels: set[str]) -> dict[str, int]:
    counts = {label: 0 for label in labels}
    for line in text.splitlines():
        match = _ROW.match(line)
        if not match or match.group("status") != "OPEN":
            continue
        subject = match.group("text")
        for label in labels:
            if subject.startswith(label + " --"):
                counts[label] += 1
    return counts


def test_live_residuals_are_owned_by_exactly_one_open_backlog_row_each():
    backlog = (ROOT / "project-knowledge/IMPROVEMENT_BACKLOG.md").read_text()
    required = {
        "AUTH-SCENE",
        "AI-BOOT-OBS",
        "CI-GOVERNANCE",
        "AUDIT-KNOWLEDGE-HYGIENE",
        "CI-FRONTENDS",
        "DEFECT-SUPPRESS",
    }
    assert _open_label_counts(backlog, required) == {label: 1 for label in required}


def test_closed_rows_and_free_prose_cannot_launder_a_live_residual():
    labels = {"AUTH-SCENE", "AI-BOOT-OBS"}
    adversary = "\n".join(
        (
            "AUTH-SCENE -- prose is not a row",
            "| 1 | CLOSED @1 | AUTH-SCENE -- closed is not live |",
            "| 2 | MOOT @1 | AI-BOOT-OBS -- moot is not live |",
            "| 3 | OPEN | AI-BOOT-OBS -- exactly one live row |",
        )
    )
    assert _open_label_counts(adversary, labels) == {
        "AUTH-SCENE": 0,
        "AI-BOOT-OBS": 1,
    }


def test_current_policy_is_self_contained_after_archive_pruning():
    policy = (ROOT / "project-knowledge/OPERATOR_POLICY_DECISIONS.md").read_text()
    assert "AUTOMATION_PROGRAM_PLAN.md" not in policy
    assert "PLUGIN_V3_PLAN.md" not in policy
    assert "gold backup/restore" in policy
    assert "AR4" in policy and "rate" in policy.lower()
    assert "operator-pinned key" in policy
    assert "never auto-apply" in policy
