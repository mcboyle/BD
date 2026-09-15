"""Row 470 -- every corpus-taking tool refuses an absent or empty subject."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
_BIN = _REPO / "toolchain" / "bin"
_LINT_PATH = _BIN / "bd-tool-lint"

# Derived from bd-tool-lint --corpus-debt on the row's base, not copied from
# the stale register prose.  This is the independent seam denominator: the
# lint's zero baseline catches future additions, while this roster prevents a
# current guard from disappearing together with its classification.
_ROW470_CASES = (
    ("bd-ascii", "--work", ()),
    ("bd-audit-gate.py", "--root", ()),
    ("bd-brief", "--tree", ()),
    ("bd-bump", "--work", ()),
    ("bd-capsweep", "--tree", ("probe",)),
    ("bd-changelog", "--tree", ()),
    ("bd-coverage", "--work", ()),
    ("bd-decomp", "--work", ()),
    ("bd-dependency-license", "--work", ()),
    ("bd-deps", "--work", ()),
    ("bd-docstale", "--work", ()),
    ("bd-envscan", "--tree", ()),
    ("bd-equiv", "--work", ()),
    ("bd-evidence", "--work", ()),
    ("bd-evidence-chain", "--home", ()),
    ("bd-fe-dead-control", "--work", ()),
    ("bd-footguns", "--tree", ()),
    ("bd-freshcheck", "--root", ()),
    ("bd-fullsuite", "--work", ()),
    ("bd-guard-declare", "--root", ("--file", "bulk_downloader/extraction_core.py")),
    ("bd-gui-surface", "--work", ()),
    ("bd-imports", "--work", ()),
    ("bd-invariant-engine", "--work", ()),
    ("bd-lost-symbol", "--work", ()),
    ("bd-offline-proof", "--work", ()),
    ("bd-parband", "--work", ("tests/probe.py",)),
    ("bd-pin", "--tree", ()),
    ("bd-pinscan", "--tree", ()),
    ("bd-pk-mirror", "--work", ()),
    ("bd-precut", "--root", ()),
    ("bd-preflight", "--work", ()),
    ("bd-ratchet", "--tree", ()),
    ("bd-ready", "--work", ()),
    ("bd-regen", "--work", ()),
    ("bd-render", "--work", ()),
    ("bd-retest", "--work", ()),
    ("bd-rollback-oracle", "--work", ()),
    ("bd-route", "--tree", ()),
    ("bd-secrets", "--work", ()),
    ("bd-smoke", "--work", ()),
    ("bd-ssrf", "--work", ()),
    ("bd-surface-census", "--work", ()),
    ("bd-sym", "--work", ("probe",)),
    ("bd-treecheck", "--work", ()),
    ("bd-wacz-corpus", "--root", ("--all",)),
)

_DIRECT_CORPUS_TOOLS = {"bd-evidence-chain", "bd-wacz-corpus"}
_NESTED_CORPORA = {"bd-regen": "tools"}
_CORPUS_GUARD_TOOLS = {"bd-evidence-chain", "bd-regen", "bd-wacz-corpus", "bd-fullsuite",
                       "bd-equiv", "bd-footguns"}
_RUNTIME_CLASSIFIER_TOOLS = (
    "bd-dependency-license",
    "bd-equiv",
    "bd-invariant-engine",
    "bd-offline-proof",
    "bd-wacz-corpus",
)


def _load_lint():
    loader = importlib.machinery.SourceFileLoader("_row470_bd_tool_lint", str(_LINT_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _instrumented_tool(tmp_path: Path, tool: str) -> Path:
    fixture_root = tmp_path / "fixture-repo"
    fixture_bin = fixture_root / "toolchain" / "bin"
    fixture_bin.mkdir(parents=True)
    shutil.copy2(_REPO / "FOOTGUNS.json", fixture_root / "FOOTGUNS.json")
    copied = fixture_bin / tool
    shutil.copy2(_BIN / tool, copied)
    real_sec = _BIN / "bdtools_sec.py"
    shim = f'''import importlib.machinery
import importlib.util
import sys
_loader = importlib.machinery.SourceFileLoader("_row470_real_sec", {str(real_sec)!r})
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
_real = importlib.util.module_from_spec(_spec)
_loader.exec_module(_real)
for _name in dir(_real):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_real, _name)

def require_source_tree(work=DEFAULT_WORK, label="--work/--tree"):
    print("ROW470_GUARD source-tree " + label, file=sys.stderr)
    return _real.require_source_tree(work, label=label)

def require_corpus(path, min_files=1, label="--tree", patterns=None):
    print("ROW470_GUARD corpus " + label, file=sys.stderr)
    return _real.require_corpus(path, min_files=min_files, label=label, patterns=patterns)

def corpus_guard(path, min_files=1, label="--tree", patterns=None):
    print("ROW470_GUARD corpus " + label, file=sys.stderr)
    return _real.corpus_guard(path, min_files=min_files, label=label, patterns=patterns)
'''
    (fixture_bin / "bdtools_sec.py").write_text(shim, encoding="ascii")
    return copied


def _subject(tmp_path: Path, tool: str, state: str) -> Path:
    subject = tmp_path / state
    if state == "absent":
        assert not subject.exists(), "absent-corpus fixture unexpectedly exists"
        return subject
    subject.mkdir()
    corpus = subject
    if tool in _NESTED_CORPORA:
        corpus = subject / _NESTED_CORPORA[tool]
        corpus.mkdir()
    elif tool not in _DIRECT_CORPUS_TOOLS:
        corpus = subject / "bulk_downloader"
        corpus.mkdir()
    assert corpus.is_dir(), f"{tool} empty-corpus fixture did not build {corpus}"
    assert not any(p.is_file() for p in subject.rglob("*")), (
        "empty-corpus fixture unexpectedly contains a file")
    return subject


def _run_instrumented(tmp_path: Path, tool: str, flag: str, subject: Path,
                      extra: tuple[str, ...] = ()) -> subprocess.CompletedProcess[str]:
    copied = _instrumented_tool(tmp_path, tool)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_BIN)
    return subprocess.run(
        [sys.executable, str(copied), flag, str(subject), *extra],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=30,
    )


def _guard_lines(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [line for line in result.stderr.splitlines()
            if line.startswith("ROW470_GUARD ")]


def test_current_toolchain_has_no_unguarded_corpus_tools(tmp_path: Path):
    lint = _load_lint()
    result = lint.run(str(_BIN), do_runtime=True)
    debt = result["corpus_debt"]
    guarded = set(debt["guarded"])
    opted_out = set(debt["opted_out"])
    unguarded = set(debt["new"])
    unknown = set(debt["unknown"])
    judged = guarded | opted_out | unguarded | unknown

    row_cases = {name: (flag, extra) for name, flag, extra in _ROW470_CASES}
    assert len(_ROW470_CASES) == len(row_cases) == 45
    assert debt["with_corpus_arg"] > 0, "corpus-tool denominator collapsed to zero"
    assert len(judged) == debt["with_corpus_arg"], (
        "census must reconcile every current corpus tool to one verdict")
    assert sum(map(len, (guarded, opted_out, unguarded, unknown))) == len(judged)
    assert debt["baseline"] == 0
    # The shared lint deliberately will not execute mutators. Probe those six
    # from fixture-owned copies against an absent root, before any write seam.
    never_exec = set(row_cases) & set(lint.NEVER_EXEC)
    assert len(never_exec) == 6
    isolated_guarded = set()
    for tool in sorted(never_exec):
        flag, extra = row_cases[tool]
        root = tmp_path / tool
        subject = _subject(root, tool, "absent")
        result = _run_instrumented(root, tool, flag, subject, extra)
        lines = _guard_lines(result)
        assert result.returncode == 2
        assert len(lines) == 1, (
            f"{tool} isolated runtime proof fired {len(lines)} guards, "
            f"expected exactly 1 nonzero firing: {lines}")
        assert "CANNOT-EVALUATE" in result.stderr
        assert "reason=ABSENT" in result.stderr
        isolated_guarded.add(tool)

    row_names = set(row_cases)
    row_unknown = (unknown & row_names) - isolated_guarded
    assert len(row_unknown) == 0, (
        f"{len(row_unknown)} row470 tool(s) remain UNKNOWN: "
        f"{', '.join(sorted(row_unknown))}")
    assert (guarded & row_names) | isolated_guarded == row_names
    assert debt["unguarded_count"] == 0, (
        f"{debt['unguarded_count']} corpus tool(s) remain unguarded: "
        f"{', '.join(debt['new'])}")
    assert debt["new"] == []


@pytest.mark.parametrize("state", ("absent", "empty"))
def test_every_row470_tool_reaches_its_shared_guard_once(
        tmp_path: Path, state: str):
    assert len(_ROW470_CASES) == 45
    for tool, flag, extra in _ROW470_CASES:
        case_root = tmp_path / tool
        case_root.mkdir()
        subject = _subject(case_root, tool, state)
        result = _run_instrumented(case_root, tool, flag, subject, extra)
        lines = _guard_lines(result)

        assert result.returncode == 2, (
            f"{tool} {state} corpus returned {result.returncode}:\n"
            f"stdout={result.stdout}\nstderr={result.stderr}")
        assert len(lines) == 1, (
            f"{tool} {state} corpus fired the shared guard {len(lines)} times, "
            f"expected exactly 1 nonzero firing: {lines}")
        expected_kind = "corpus" if tool in _CORPUS_GUARD_TOOLS else "source-tree"
        assert lines[0].startswith(f"ROW470_GUARD {expected_kind} "), (
            f"{tool} reached the wrong guard seam: {lines[0]}")
        assert "CANNOT-EVALUATE" in result.stderr
        assert f"reason={'ABSENT' if state == 'absent' else 'EMPTY'}" in result.stderr


def test_runtime_classifier_executes_every_row470_special_case(tmp_path: Path):
    lint = _load_lint()
    assert len(_RUNTIME_CLASSIFIER_TOOLS) == 5
    for tool in _RUNTIME_CLASSIFIER_TOOLS:
        bindir = tmp_path / tool
        bindir.mkdir()
        shutil.copy2(_BIN / tool, bindir / tool)
        debt = lint.run(str(bindir), do_runtime=True)["corpus_debt"]
        assert debt["with_corpus_arg"] == 1
        assert debt["guarded"] == [tool], debt
        assert debt["unknown"] == []
        assert debt["new"] == []


@pytest.mark.parametrize("state", ("absent", "empty"))
def test_equiv_also_guards_its_optional_corpus(tmp_path: Path, state: str):
    corpus = tmp_path / "corpus"
    if state == "empty":
        corpus.mkdir()
    assert not list(corpus.glob("*.py"))

    result = _run_instrumented(
        tmp_path, "bd-equiv", "--work", _REPO,
        ("--corpus", str(corpus), "--old", "old", "--new", "new"),
    )
    lines = _guard_lines(result)
    assert result.returncode == 2
    assert lines == ["ROW470_GUARD corpus --work",
                     "ROW470_GUARD corpus --corpus"], (
        f"bd-equiv optional corpus must be the second of exactly 2 guard firings: {lines}")
    assert "CANNOT-EVALUATE" in result.stderr
    assert f"reason={'ABSENT' if state == 'absent' else 'EMPTY'}" in result.stderr


@pytest.mark.parametrize("state", ("absent", "empty"))
def test_audit_gate_also_guards_its_optional_corpus(tmp_path: Path, state: str):
    corpus = tmp_path / "audit-corpus"
    if state == "empty":
        corpus.mkdir()
    assert not list(corpus.glob("*_vuln.py"))

    result = _run_instrumented(
        tmp_path, "bd-audit-gate.py", "--root", _REPO,
        ("--corpus", str(corpus)),
    )
    lines = _guard_lines(result)
    assert result.returncode == 2
    assert lines == ["ROW470_GUARD source-tree --root",
                     "ROW470_GUARD corpus --corpus"], (
        "bd-audit-gate optional corpus must be the second of exactly 2 "
        f"guard firings: {lines}")
    assert "CANNOT-EVALUATE" in result.stderr
    assert f"reason={'ABSENT' if state == 'absent' else 'EMPTY'}" in result.stderr


def test_real_tree_positive_control_reaches_a_clean_verdict():
    marker = _REPO / "bulk_downloader" / "__init__.py"
    assert marker.is_file(), "positive-control tree lacks its package marker"
    result = subprocess.run(
        [sys.executable, str(_BIN / "bd-ascii"), "--work", str(_REPO),
         str(_BIN / "bd-ascii")],
        cwd=str(_REPO), capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "clean (ASCII-only)" in result.stdout


def test_negative_control_exposes_a_tool_that_never_calls_the_guard(tmp_path: Path):
    fake = tmp_path / "bd-no-guard"
    fake.write_text(
        "import argparse, bdtools_sec\n"
        "ap = argparse.ArgumentParser()\n"
        "ap.add_argument('--work')\n"
        "a = ap.parse_args()\n"
        "print('GREEN over nothing')\n",
        encoding="ascii",
    )
    real_sec = _BIN / "bdtools_sec.py"
    (tmp_path / "bdtools_sec.py").write_text(
        f'''import importlib.machinery, importlib.util, sys
_loader = importlib.machinery.SourceFileLoader("_row470_control_sec", {str(real_sec)!r})
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
_real = importlib.util.module_from_spec(_spec)
_loader.exec_module(_real)
for _name in dir(_real):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_real, _name)
def require_source_tree(work=DEFAULT_WORK, label="--work/--tree"):
    print("ROW470_GUARD source-tree " + label, file=sys.stderr)
    return _real.require_source_tree(work, label=label)
''', encoding="ascii")
    missing = tmp_path / "does-not-exist"
    result = subprocess.run(
        [sys.executable, str(fake), "--work", str(missing)],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    assert "GREEN over nothing" in result.stdout
    with pytest.raises(AssertionError, match="expected exactly 1 nonzero firing"):
        lines = _guard_lines(result)
        assert len(lines) == 1, (
            f"negative control fired {len(lines)} guards; expected exactly 1 "
            f"nonzero firing")


def test_zero_baseline_rejects_a_new_unguarded_tool(tmp_path: Path):
    fake = tmp_path / "bd-unguarded"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import argparse, bdtools_sec\n"
        "# --json --selftest\n"
        "def main():\n"
        "    ap = argparse.ArgumentParser()\n"
        "    ap.add_argument('--work')\n"
        "    return 0\n"
        "def selftest():\n"
        "    print('SELFTEST PASS')\n"
        "    return 0\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n",
        encoding="ascii",
    )
    lint = _load_lint()
    result = lint.run(str(tmp_path), do_runtime=True)
    debt = result["corpus_debt"]
    assert debt["with_corpus_arg"] == 1
    assert debt["unguarded_count"] == 1
    assert debt["new"] == ["bd-unguarded"]
    assert result["clean"] is False


def test_non_corpus_compatibility_flag_needs_an_explicit_scoped_exemption(
        tmp_path: Path):
    source = (
        "#!/usr/bin/env python3\n"
        "import argparse, bdtools_sec as sec\n"
        "# --json --selftest\n"
        "# lint: corpus-arg-not-corpus --home -- compatibility path is not a corpus\n"
        "def main():\n"
        "    ap = argparse.ArgumentParser()\n"
        "    ap.add_argument('--work', default='.')\n"
        "    ap.add_argument('--home')\n"
        "    a = ap.parse_args()\n"
        "    sec.require_source_tree(a.work)\n"
        "    return 0\n"
        "def selftest():\n"
        "    print('SELFTEST PASS')\n"
        "    return 0\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n"
    )
    (tmp_path / "bd-mixed").write_text(source, encoding="ascii")
    (tmp_path / "bd-mixed-without-exemption").write_text(
        source.replace(
            "# lint: corpus-arg-not-corpus --home -- compatibility path is not a corpus\n",
            ""),
        encoding="ascii",
    )

    lint = _load_lint()
    debt = lint.run(str(tmp_path), do_runtime=True)["corpus_debt"]

    assert debt["with_corpus_arg"] == 2
    assert debt["guarded"] == ["bd-mixed"]
    assert debt["unknown"] == ["bd-mixed-without-exemption"]
    assert debt["new"] == []
    assert debt["unguarded_count"] == 0
    assert debt["regressed"] is True


def test_transform_control_imports_the_census_without_judging_guard_behavior():
    _load_lint()
