"""Row 705: published populations carry derivation and visible truncation."""

from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
_DOC_TRUTH = _REPO / "toolchain" / "bin" / "bd-doc-truth"
_FRESHCHECK = _REPO / "toolchain" / "bin" / "bd-freshcheck"


def _load(path: Path, name: str):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def _tracked_docs_repo(root: Path, text: str) -> Path:
    (root / "bulk_downloader").mkdir(parents=True)
    (root / "bulk_downloader" / "marker.py").write_text("MARKER = 1\n")
    (root / "project-knowledge").mkdir()
    (root / "project-knowledge" / "claim.md").write_text(text)
    subprocess.run(["git", "init", "-q", "."], cwd=root, check=True)
    subprocess.run(["git", "add", "bulk_downloader/marker.py",
                    "project-knowledge/claim.md"], cwd=root, check=True)
    tracked = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.md"], cwd=root,
        check=True, capture_output=True, text=True).stdout.split("\0")
    assert [item for item in tracked if item] == ["project-knowledge/claim.md"]
    return root


def _doc_truth(root: Path) -> tuple[subprocess.CompletedProcess, dict]:
    env = dict(os.environ)
    env.pop("BD_INSTALL_DIR", None)
    result = subprocess.run(
        [sys.executable, str(_DOC_TRUTH), "--work", str(root), "--json"],
        env=env, capture_output=True, text=True, timeout=30)
    return result, json.loads(result.stdout)


def test_the_real_paged_survey_population_is_refused_without_derivation(tmp_path):
    """The fixture is the row-705 incident, not an invented malformed claim."""
    claim = (
        "THE POPULATION: 12 modules containing direct httpx clients.\n"
        "This is the published survey result.\n"
    )
    root = _tracked_docs_repo(tmp_path, claim)
    assert claim.count("THE POPULATION") == 1
    assert claim.count("12 modules") == 1

    result, report = _doc_truth(root)

    assert result.returncode == 1, (
        "row705-real-paged-survey.md: THE POPULATION says 12 modules but "
        "states no derivation; bd-doc-truth returned %d with %d finding(s)"
        % (result.returncode, report.get("stale_count", -1)))
    assert report["published_population_claims"] == 1
    assert report["underived_population_claims"] == 1
    assert report["stale_count"] == 1
    assert report["stale"][0]["kind"] == "underived-population"
    assert report["stale"][0]["doc"] == "project-knowledge/claim.md"


def test_a_population_with_a_stated_tree_derivation_passes(tmp_path):
    claim = (
        "THE POPULATION, DERIVED by parsing every tracked Python file: "
        "31 constructions across 21 files.\n"
    )
    root = _tracked_docs_repo(tmp_path, claim)
    assert claim.count("THE POPULATION") == 1
    assert claim.count("DERIVED") == 1

    result, report = _doc_truth(root)

    assert result.returncode == 0, result.stderr
    assert report["published_population_claims"] == 1
    assert report["underived_population_claims"] == 0
    assert report["stale_count"] == 0


def test_an_unmeasurable_document_population_is_unknown(tmp_path):
    (tmp_path / "bulk_downloader").mkdir()
    (tmp_path / "bulk_downloader" / "marker.py").write_text("MARKER = 1\n")
    assert not (tmp_path / ".git").exists()

    env = dict(os.environ)
    env.pop("BD_INSTALL_DIR", None)
    result = subprocess.run(
        [sys.executable, str(_DOC_TRUTH), "--work", str(tmp_path), "--json"],
        env=env, capture_output=True, text=True, timeout=30)

    assert result.returncode == 2
    assert "UNEVALUABLE" in result.stderr


def test_live_publication_population_is_exact_and_nonzero():
    result, report = _doc_truth(_REPO)

    assert result.returncode == 0, result.stderr
    assert report["published_population_claims"] == 4
    assert report["underived_population_claims"] == 0
    assert report["docs_scanned"] > 0


def _bounded_join_sites() -> list[tuple[str, str]]:
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--", "tools", "toolchain/bin"],
        cwd=_REPO, check=True, capture_output=True, text=True).stdout
    sites: list[tuple[str, str]] = []
    for rel in (item for item in listing.split("\0") if item):
        path = _REPO / rel
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not (path.suffix == ".py" or source.startswith("#!/usr/bin/env python")):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Subscript)
                    and isinstance(node.slice, ast.Slice)
                    and isinstance(node.slice.upper, ast.Constant)
                    and isinstance(node.slice.upper.value, int)):
                continue
            ancestor = parents.get(node)
            while ancestor is not None and not isinstance(ancestor, ast.Call):
                ancestor = parents.get(ancestor)
            if not (isinstance(ancestor, ast.Call)
                    and isinstance(ancestor.func, ast.Attribute)
                    and ancestor.func.attr == "join"):
                continue
            function = next(
                (parent.name for parent in ast.walk(tree)
                 if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node in tuple(ast.walk(parent))), "<module>")
            sites.append((rel, function))
    return sorted(sites)


def test_the_tool_display_cap_population_is_measured():
    sites = _bounded_join_sites()
    assert len(sites) == 50
    assert sites.count(("toolchain/bin/bd-freshcheck", "check_anchors")) == 2


def test_freshcheck_names_the_hidden_tail_of_a_broken_anchor_list(tmp_path):
    root = tmp_path
    (root / "project-knowledge").mkdir()
    anchors = "\n".join("missing_%d.py:1" % index for index in range(8))
    (root / "project-knowledge" / "claims.md").write_text(anchors + "\n")
    for index in range(100):
        (root / ("tracked_%03d.txt" % index)).write_text("tracked\n")
    subprocess.run(["git", "init", "-q", "."], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    assert anchors.count(".py:1") == 8

    result = _load(_FRESHCHECK, "row705_freshcheck").check_anchors(root)

    assert result["status"] == "STALE"
    assert "0/8 anchors resolve" in result["detail"]
    assert "showing 6 of 8 broken anchors" in result["detail"], result["detail"]


def test_loading_the_row705_tools_alone_asserts_no_behavior():
    assert _load(_DOC_TRUTH, "row705_doc_truth_control")
    assert _load(_FRESHCHECK, "row705_freshcheck_control")
