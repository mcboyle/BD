"""v3.66.1172: freshness sees nested authority and retirements cannot self-certify.

This is a repository contract, not a module contract.  It protects the tracked
documentation denominator, the migration of the 2026-07-29 audit, and the
complete historically reconstructed twelve-tool retirement as one transition.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import ast
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

_RETIRED_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_-])(?:" + "|".join(map(re.escape, sorted(RETIRED)))
    + r")(?![A-Za-z0-9_-]|\.[A-Za-z0-9])"
)

MIGRATED_AUDIT_LABELS = {
    "FRONTEND-SECRET-REGEN": "CLOSED @1179",
    "TEMPLATE-SNAPSHOT-COVERAGE": "CLOSED @1176",
    "PYTEST-CAPTURE-DIAGNOSTICS": "CLOSED @1181",
    "SKIP-BASELINE-ENFORCEMENT": "CLOSED @1181",
    "AI-BOOT-OBS": "CLOSED @1177",
}

AUDIT_OWNER_ROWS = {
    "#6": (110, "CLOSED"),
    "#17": (111, "CLOSED"),
    "#19d": (166, "CLOSED @1179"),
    "#19e": (167, "CLOSED @1176"),
    "#21": (168, "CLOSED @1181"),
    "#22": (169, "CLOSED @1181"),
    "#24": (164, "CLOSED @1177"),
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


def _retired_code_invocations(root: Path) -> tuple[int, list[str]]:
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z", "toolchain", "scripts", "tools", ".github"],
        cwd=root,
    ).decode().split("\0")
    offenders = []
    denominator = 0
    for rel in tracked:
        if not rel:
            continue
        path = root / rel
        if not path.is_file():
            continue
        denominator += 1
        text = path.read_text(errors="replace")
        lines = text.splitlines()
        is_python = path.suffix == ".py" or (lines and "python" in lines[0])
        if is_python:
            try:
                tree = ast.parse(text)
            except SyntaxError:
                tree = None
            if tree is not None:
                docstrings = {
                    id(node.body[0].value)
                    for node in ast.walk(tree)
                    if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    and node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)
                }
                for child in ast.walk(tree):
                    if (isinstance(child, ast.Constant) and isinstance(child.value, str)
                            and id(child) not in docstrings
                            and _RETIRED_TOKEN.search(child.value)):
                        offenders.append(
                            f"{rel}:{getattr(child, 'lineno', 0)}:{child.value}"
                        )
        for line_no, line in enumerate(lines, 1):
            if line.lstrip().startswith("#"):
                continue
            if not is_python and _RETIRED_TOKEN.search(line):
                offenders.append(f"{rel}:{line_no}:{line.strip()}")
    return denominator, offenders


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


# Row 531: a monotonic floor, not a hand-bumped total. Lower it only when
# documents are deliberately retired; adding one must never edit this file.
_CURRENT_MARKDOWN_FLOOR = 139


def test_current_markdown_denominator_is_explicit_and_nonzero():
    sec = _load("bdtools_sec_v1172", BIN / "bdtools_sec.py")
    current, historical = sec.tracked_markdown_corpus(REPO)
    deleted = set(
        subprocess.check_output(["git", "ls-files", "--deleted"], cwd=REPO, text=True).splitlines()
    )
    current = [p for p in current if p not in deleted]
    # Row 531: this was `== 139`, hand-bumped on every cut that added a
    # document, and it turned a green candidate red twice on 2026-08-31 for no
    # defect at all. NONZERO, UNIQUE, DISJOINT and a monotonic FLOOR are what
    # the gate was actually for. The exact denominator is still checked -- in
    # test_row531_denominators_are_derived_not_pinned, against a SECOND
    # independent derivation of the same rule rather than against a number
    # somebody typed.
    assert current, "the current Markdown denominator collapsed to zero"
    assert len(current) >= _CURRENT_MARKDOWN_FLOOR, (
        f"the current Markdown corpus fell from at least "
        f"{_CURRENT_MARKDOWN_FLOOR} to {len(current)}; document(s) were removed")
    assert len(historical) == 14
    assert len(current) == len(set(current))
    assert not set(current) & set(historical)


def test_the_audit_is_retired_only_after_every_live_finding_has_an_owner():
    assert not AUDIT.exists() and not os.path.lexists(AUDIT)
    text = BACKLOG.read_text(encoding="ascii")
    for label, status in sorted(MIGRATED_AUDIT_LABELS.items()):
        rows = [line for line in text.splitlines() if f"{label} --" in line]
        assert len(rows) == 1, (label, rows)
        assert f"| {status} |" in rows[0], rows[0]
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
    sec = _load("bdtools_sec_retired_consumers_v1172", BIN / "bdtools_sec.py")
    current_docs, _historical = sec.tracked_markdown_corpus(REPO)
    current_docs = tuple(
        rel for rel in current_docs
        if rel != "project-knowledge/IMPROVEMENT_BACKLOG.md"
    )
    offenders = {}
    # Row 531: the same retired literal, one document smaller. What this scan
    # needs is a nonzero denominator it can prove it covered -- not a total.
    assert current_docs, "the retired-tool scan has nothing to scan"
    assert len(current_docs) >= _CURRENT_MARKDOWN_FLOOR - 1, (
        f"the scanned document population fell to {len(current_docs)}, below "
        f"the floor {_CURRENT_MARKDOWN_FLOOR - 1}")
    for rel in current_docs:
        matches = sorted(set(
            _RETIRED_TOKEN.findall((REPO / rel).read_text(encoding="utf-8"))
        ))
        if matches:
            offenders[rel] = matches
    assert not offenders, offenders
    code_denominator, code_offenders = _retired_code_invocations(REPO)
    assert code_denominator > 100
    assert not code_offenders, code_offenders
    for rel in (
        "project-knowledge/BD_TOOLCHAIN_REFERENCE.md",
        "project-knowledge/BD_TOOLCHAIN_WHEN_TO_USE.md",
        "docs/repo/TOOLCHAIN_PORTABILITY.md",
    ):
        assert rel not in subprocess.check_output(
            ["git", "ls-files"], cwd=REPO, text=True
        ).splitlines()
        assert not os.path.lexists(REPO / rel)


def test_retired_executable_consumer_scan_has_a_positive_control(tmp_path: Path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "new-close.sh").write_text(
        "#!/bin/sh\nbd-pack --out /tmp/result\n", encoding="utf-8"
    )
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "new_close.py").write_text(
        'import subprocess\nCOMMAND = [\n    "bd-pack",\n    "--out",\n]\nsubprocess.run(COMMAND)\n',
        encoding="utf-8",
    )
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    denominator, offenders = _retired_code_invocations(tmp_path)
    assert denominator == 2
    assert len(offenders) == 2
    assert all("bd-pack" in offender for offender in offenders)


def test_retired_token_distinguishes_a_live_script_suffix_from_a_retired_name():
    assert "bd-ship" in RETIRED
    live_mentions = "Use bd-ship.sh or bd-ship.py from the operator harness."
    retired_mention = "The retired in-repo command was bd-ship."
    assert live_mentions.count("bd-ship") == 2
    assert retired_mention.count("bd-ship") == 1
    assert _RETIRED_TOKEN.findall(live_mentions) == []
    assert _RETIRED_TOKEN.findall(retired_mention) == ["bd-ship"]


def test_row655_transform_control_imports_without_judging_boundary_behavior():
    module = _load("row655_transform_control", Path(__file__))
    assert module.RETIRED == RETIRED


def test_bd_versync_docstring_documents_its_release_preflight_contract():
    path = BIN / "bd-versync"
    assert path.is_file(), "bd-versync must exist before its documentation can be checked"
    docstring = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8")))
    assert docstring, "bd-versync must have a module docstring"
    numbered_checks = re.findall(r"(?m)^[1-4]\. ", docstring)
    assert len(numbered_checks) == 4, docstring
    for required in (
        "release-trio preflight",
        "bulk_downloader/__init__.py",
        "CHANGELOG.md",
        "tools/build_pin_index.py",
        "settings_center_slice4",
        "Exit 0",
        "Exit 1",
        "Exit 2",
        "bdtools_sec",
    ):
        assert required in docstring, (required, docstring)


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
