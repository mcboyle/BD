"""v3.66.1172: freshness sees nested authority and retirements cannot self-certify.

This is a repository contract, not a module contract.  It protects the tracked
documentation denominator, the migration of the 2026-07-29 audit, and the
complete historically reconstructed twelve-tool retirement as one transition.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path


BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parents[1]
BACKLOG = REPO / "project-knowledge" / "IMPROVEMENT_BACKLOG.md"
AUDIT = REPO / "project-knowledge" / "AUDIT_2026_07_29.md"
BIN = REPO / "toolchain" / "bin"

RETIRED = {
    "bd-pack",
    "bd-handoff",
    "bd-checkpoint",
    "bd-zipcheck",
    "bd-mkauditstate",
    "bd-mkbdsuite",
    "bd-freshest",
    "bd-optpack",
    "bd-prestage",
    "bd-ship",
    "bd-snapshot",
    "bd-since",
}

MIGRATED_AUDIT_LABELS = {
    "FRONTEND-SECRET-REGEN",
    "TEMPLATE-SNAPSHOT-COVERAGE",
    "PYTEST-CAPTURE-DIAGNOSTICS",
    "SKIP-BASELINE-ENFORCEMENT",
    "AI-BOOT-OBS",
}

AUDIT_OWNER_ROWS = {
    "#6": (110, "CLOSED"),
    "#17": (111, "CLOSED"),
    "#19d": (166, "OPEN"),
    "#19e": (167, "OPEN"),
    "#21": (168, "OPEN"),
    "#22": (169, "OPEN"),
    "#24": (164, "OPEN"),
}


def _load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _source_tree(root: Path) -> None:
    (root / "bulk_downloader").mkdir(parents=True)
    (root / "bulk_downloader" / "__init__.py").write_text(
        '__version__ = "3.66.1172"\n', encoding="utf-8"
    )


def test_doc_truth_recurses_over_tracked_current_authority(tmp_path: Path):
    """A nested task-bearing document must be inside the real CLI denominator."""
    _source_tree(tmp_path)
    (tmp_path / "project-knowledge" / "nested").mkdir(parents=True)
    (tmp_path / "project-knowledge" / "top.md").write_text("top\n", encoding="utf-8")
    bad = tmp_path / "project-knowledge" / "nested" / "task.md"
    bad.write_text("See `bulk_downloader/does_not_exist.py`.\n", encoding="utf-8")
    (tmp_path / "docs" / "deep").mkdir(parents=True)
    (tmp_path / "docs" / "deep" / "guide.md").write_text("guide\n", encoding="utf-8")
    (tmp_path / "docs" / "archive").mkdir(parents=True)
    (tmp_path / "docs" / "archive" / "old.md").write_text(
        "See `bulk_downloader/historical_missing.py`.\n", encoding="utf-8"
    )
    (tmp_path / "README.md").write_text("root\n", encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("historical\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")

    cp = subprocess.run(
        [sys.executable, str(BIN / "bd-doc-truth"), "--work", str(tmp_path), "--json"],
        text=True,
        capture_output=True,
        timeout=30,
    )
    payload = json.loads(cp.stdout)
    assert cp.returncode == 1
    assert payload["docs_scanned"] == 4
    assert payload["current_docs"] == 4
    assert payload["historical_excluded"] == 2
    assert any(x["doc"] == "project-knowledge/nested/task.md" for x in payload["stale"])
    assert not any("historical_missing" in x["claim"] for x in payload["stale"])


def test_freshcheck_anchor_denominator_includes_nested_docs(tmp_path: Path):
    """The aggregate gate must use the same recursive corpus as bd-doc-truth."""
    _source_tree(tmp_path)
    (tmp_path / "CLAUDE.md").write_text(
        "See bulk_downloader/__init__.py:1.\n", encoding="utf-8"
    )
    (tmp_path / "project-knowledge").mkdir(exist_ok=True)
    (tmp_path / "project-knowledge" / "IMPROVEMENT_BACKLOG.md").write_text(
        "Backlog.\n", encoding="utf-8"
    )
    (tmp_path / "docs" / "deep").mkdir(parents=True)
    (tmp_path / "docs" / "deep" / "bad.md").write_text(
        "See bulk_downloader/__init__.py:99.\n", encoding="utf-8"
    )
    for i in range(100):
        (tmp_path / f"filler-{i:03d}.txt").write_text("x\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")

    fresh = _load("bd_freshcheck_v1172", BIN / "bd-freshcheck")
    result = fresh.check_anchors(tmp_path)
    assert result["status"] == fresh.STALE
    assert "docs/deep/bad.md" in result["detail"]
    assert "3 document(s)" in result["detail"]


def test_freshcheck_refuses_an_ambiguous_basename_anchor(tmp_path: Path):
    _source_tree(tmp_path)
    for parent in ("one", "two"):
        target = tmp_path / parent / "same.py"
        target.parent.mkdir()
        target.write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("See same.py:1.\n", encoding="utf-8")
    for i in range(100):
        (tmp_path / f"filler-{i:03d}.txt").write_text("x\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    fresh = _load("bd_freshcheck_ambiguous_v1172", BIN / "bd-freshcheck")
    result = fresh.check_anchors(tmp_path)
    assert result["status"] == fresh.STALE
    assert "ambiguous basename" in result["detail"]


def test_current_markdown_denominator_is_explicit_and_nonzero():
    sec = _load("bdtools_sec_v1172", BIN / "bdtools_sec.py")
    current, historical = sec.tracked_markdown_corpus(REPO)
    deleted = set(
        subprocess.check_output(["git", "ls-files", "--deleted"], cwd=REPO, text=True).splitlines()
    )
    current = [p for p in current if p not in deleted]
    assert len(current) == 132
    assert len(historical) == 14
    assert len(current) == len(set(current))


def test_the_audit_is_retired_only_after_every_live_finding_has_an_owner():
    assert not AUDIT.exists() and not os.path.lexists(AUDIT)
    text = BACKLOG.read_text(encoding="ascii")
    for label in sorted(MIGRATED_AUDIT_LABELS):
        rows = [line for line in text.splitlines() if f"{label} --" in line]
        assert len(rows) == 1, (label, rows)
        assert "| OPEN |" in rows[0], rows[0]
    row114 = [line for line in text.splitlines() if line.startswith("| 114 |")]
    assert len(row114) == 1 and "| CLOSED @1172 |" in row114[0]
    assert "#23 and #25 are fixed" in row114[0]
    rows = {
        int(parts[1].strip()): parts
        for line in text.splitlines()
        if line.startswith("|") and len(parts := line.split("|")) >= 4
        and parts[1].strip().isdigit()
    }
    assert len(AUDIT_OWNER_ROWS) + 2 == 9
    for finding, (row_id, status) in AUDIT_OWNER_ROWS.items():
        assert rows[row_id][2].strip().startswith(status), (finding, rows[row_id])
        assert finding in row114[0], finding


def test_all_twelve_reconstructed_legacy_tools_are_physically_retired():
    assert len(RETIRED) == 12
    offenders = []
    tracked = set(
        subprocess.check_output(["git", "ls-files"], cwd=REPO, text=True).splitlines()
    )
    deleted = set(
        subprocess.check_output(["git", "ls-files", "--deleted"], cwd=REPO, text=True).splitlines()
    )
    tracked -= deleted
    for name in sorted(RETIRED):
        for rel in (f"toolchain/bin/{name}", f"project-knowledge/{name}"):
            path = REPO / rel
            if rel in tracked or os.path.lexists(path):
                offenders.append(rel)
    assert not offenders, offenders


def test_retired_tools_have_no_live_operator_or_executable_consumers():
    token = re.compile(
        r"(?<![A-Za-z0-9_-])(?:" + "|".join(map(re.escape, sorted(RETIRED)))
        + r")(?![A-Za-z0-9_-])"
    )
    current_docs = (
        "project-knowledge/README.md",
        "project-knowledge/KB_SYNC_WORKFLOW.md",
        "project-knowledge/RELEASE_DISCIPLINE_TIERS.md",
        "project-knowledge/CODE_REVIEW_INDEX.md",
        "project-knowledge/PROJECT_KNOWLEDGE_IS_STATIC.md",
        "project-knowledge/GLOSSARY.md",
        "project-knowledge/KB_ACTIVE_INDEX.md",
        "docs/repo/ENVIRONMENT_PROVISIONING.md",
    )
    live_code = (
        "toolchain/bin/bd-coretest",
        "toolchain/bin/bd-consumer-graph",
        "tools/build_pin_index.py",
        ".github/workflows/ci.yml",
    )
    offenders = {}
    for rel in current_docs + live_code:
        matches = sorted(set(token.findall((REPO / rel).read_text(encoding="utf-8"))))
        if matches:
            offenders[rel] = matches
    assert not offenders, offenders
    for rel in (
        "project-knowledge/BD_TOOLCHAIN_REFERENCE.md",
        "project-knowledge/BD_TOOLCHAIN_WHEN_TO_USE.md",
        "docs/repo/TOOLCHAIN_PORTABILITY.md",
    ):
        assert rel not in subprocess.check_output(
            ["git", "ls-files"], cwd=REPO, text=True
        ).splitlines()
        assert not os.path.lexists(REPO / rel)


def test_coretest_refuses_to_certify_a_missing_tool(tmp_path: Path):
    """A missing executable is UNKNOWN, never a successful fault injection."""
    cp = subprocess.run(
        [sys.executable, str(BIN / "bd-coretest"), "--bin", str(tmp_path), "--only", "cut"],
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert cp.returncode == 2
    assert "missing" in (cp.stdout + cp.stderr).lower()
    assert "SELFTEST PASS" not in cp.stdout


def test_tool_lint_refuses_a_shrunken_critical_core_denominator():
    lint = _load("bd_tool_lint_v1172", BIN / "bd-tool-lint")
    result = lint.run(str(BIN), do_runtime=False)
    assert result["critical_core"]["present"] == len(lint.CRITICAL_CORE)
    assert result["critical_core"]["missing"] == []
    assert not result["critical_core"]["regressed"]
    lint.CRITICAL_CORE = [*lint.CRITICAL_CORE, "bd-intentionally-missing-control"]
    negative = lint.run(str(BIN), do_runtime=False)
    assert negative["critical_core"]["missing"] == ["bd-intentionally-missing-control"]
    assert negative["critical_core"]["regressed"]
