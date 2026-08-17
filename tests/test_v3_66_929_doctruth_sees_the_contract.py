"""v3.66.929: bd-doc-truth could not see CLAUDE.md, the document that
outranks every document it did check.

`default_docs()` returned `<work>/project-knowledge` and `scan()` globbed
`*.md` there, so the corpus was 65 documents and the operating contract was
in none of them. CLAUDE.md section 0 records the consequence in its own
words: "THE FRESHNESS GATE CANNOT SEE THIS FILE, WHICH IS WHY THIS FILE WENT
STALE" -- section 5 asserted for weeks that check_requirements.py discarded
its version result, after the tool had already grown specifier comparison.

The widening is derived, not hardcoded to one filename: every root-level
`*.md` joins the corpus. A gate that can see the contract only because
someone typed its name rots the next time a root document appears.

CHANGELOG.md is excluded, and the exclusion is REPORTED rather than silent.
Measured at v3.66.928: it carries 53 backticked references of which 2 no
longer resolve -- `bulk_downloader/deep_detect.py` and
`bulk_downloader/dev_suite.py`, both since split into packages. Those
entries were true when written, and rewriting them to satisfy a gate would
falsify the record. A silent exclusion is how a denominator rots, so the
count and the reason are both printed.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-doc-truth"


def _load():
    """bd-doc-truth is extensionless, so it needs an explicit loader."""
    spec = importlib.util.spec_from_loader(
        "_bd_doc_truth",
        importlib.machinery.SourceFileLoader("_bd_doc_truth", str(_TOOL)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(args, cwd=None):
    return subprocess.run([sys.executable, str(_TOOL)] + args,
                          cwd=cwd or str(_REPO),
                          capture_output=True, text=True, timeout=180)


def _fake_tree(td: str, claude_body: str, changelog_body: str = "") -> str:
    """A minimal tree that satisfies sec.require_source_tree()."""
    root = Path(td) / "tree"
    (root / "bulk_downloader").mkdir(parents=True)
    (root / "bulk_downloader" / "real_module.py").write_text("x = 1\n")
    (root / "project-knowledge").mkdir()
    (root / "project-knowledge" / "note.md").write_text(
        "A clean note about `bulk_downloader/real_module.py`.\n")
    (root / "CLAUDE.md").write_text(claude_body)
    if changelog_body:
        (root / "CHANGELOG.md").write_text(changelog_body)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    return str(root)


# ── the contract must be inside the denominator ───────────────────────

def test_default_scan_includes_the_contract():
    """The wired invocation -- bd-freshcheck runs this with NO arguments."""
    mod = _load()
    included, _excluded = mod.contract_docs(str(_REPO))
    names = {os.path.basename(p) for p in included}
    assert "CLAUDE.md" in names, (
        "CLAUDE.md is not in the corpus; the one document that outranks "
        f"everything this gate checks. Got: {sorted(names)}")


def test_a_dead_contract_reference_is_caught():
    """The property, not the plumbing: a claim CLAUDE.md makes about a file
    that does not exist must be reported STALE."""
    with tempfile.TemporaryDirectory() as td:
        root = _fake_tree(td, "See `bulk_downloader/does_not_exist.py`.\n")
        r = _run(["--work", root], cwd=root)
        assert r.returncode == 1, (
            f"a dead reference in CLAUDE.md exited {r.returncode}, not 1\n"
            + (r.stdout + r.stderr)[-600:])
        assert "does_not_exist" in r.stdout
        assert "CLAUDE.md" in r.stdout


def test_a_root_document_other_than_the_contract_also_joins_the_corpus():
    """The anti-rot property. Keying the widening on the name 'CLAUDE.md'
    would pass every other test here and go blind the next time a root
    document appears -- which is the defect being fixed, one level up."""
    with tempfile.TemporaryDirectory() as td:
        root = _fake_tree(td, "See `bulk_downloader/real_module.py`.\n")
        Path(root, "ARCHITECTURE.md").write_text(
            "Described in `bulk_downloader/never_existed.py`.\n")
        subprocess.run(["git", "add", "ARCHITECTURE.md"], cwd=root, check=True)
        r = _run(["--work", root], cwd=root)
        assert r.returncode == 1, (
            "a dead reference in a non-CLAUDE root document was not seen; "
            "the corpus is keyed on a filename rather than derived\n"
            + (r.stdout + r.stderr)[-600:])
        assert "never_existed" in r.stdout


def test_a_live_contract_reference_is_not_flagged():
    """Over-sensitivity guard. A detector that fires on everything gets
    switched off, which section 0 counts as the equal of a false clean."""
    with tempfile.TemporaryDirectory() as td:
        root = _fake_tree(td, "See `bulk_downloader/real_module.py`.\n")
        r = _run(["--work", root], cwd=root)
        assert r.returncode == 0, (r.stdout + r.stderr)[-600:]


# ── the historical record is excluded, and it SAYS so ─────────────────

def test_changelog_is_excluded_from_the_corpus():
    """Its dead references are historical and true as written."""
    mod = _load()
    included, excluded = mod.contract_docs(str(_REPO))
    assert "CHANGELOG.md" not in {os.path.basename(p) for p in included}
    assert "CHANGELOG.md" in {os.path.basename(p) for p, _why in excluded}


def test_a_dead_changelog_reference_does_not_fail_the_gate():
    """The measured case: CHANGELOG.md carries 2 references that no longer
    resolve, both to modules since split into packages."""
    with tempfile.TemporaryDirectory() as td:
        root = _fake_tree(
            td,
            "See `bulk_downloader/real_module.py`.\n",
            changelog_body="## v1 - removed `bulk_downloader/long_gone.py`\n")
        r = _run(["--work", root], cwd=root)
        assert r.returncode == 0, (
            "a historical CHANGELOG reference failed the gate; rewriting it "
            "to pass would falsify the record\n" + (r.stdout + r.stderr)[-600:])
        assert "long_gone" not in r.stdout


def test_the_exclusion_is_reported_not_silent():
    """A silent exclusion is how a denominator rots. The count a reader sees
    must not imply coverage the scan does not have."""
    r = _run([])
    out = r.stdout + r.stderr
    assert "CHANGELOG.md" in out, (
        "the corpus silently drops CHANGELOG.md. Whatever is excluded must "
        "be named in the output.\n" + out[-600:])
    assert "excluded" in out.lower()


# ── the empty-denominator contract survives the widening ──────────────

def test_an_explicit_empty_docs_dir_is_still_unknown():
    """@850's negative control. Adding root documents from --work must not
    let an explicitly empty --docs corpus read as clean: that is the exact
    shape of the defect this tool was fixed for."""
    with tempfile.TemporaryDirectory() as td:
        empty = os.path.join(td, "empty")
        os.makedirs(empty)
        r = _run(["--docs", empty, "--work", str(_REPO)])
        assert r.returncode == 2, (
            f"an empty --docs corpus exited {r.returncode}; zero documents "
            "scanned is UNKNOWN, not clean\n" + (r.stdout + r.stderr)[-400:])


def test_an_absent_docs_dir_is_still_unknown():
    with tempfile.TemporaryDirectory() as td:
        r = _run(["--docs", os.path.join(td, "nope"), "--work", str(_REPO)])
        assert r.returncode == 2, (r.stdout + r.stderr)[-400:]


def test_scanned_count_covers_both_roots():
    """The count is the denominator, so it must include what was added."""
    mod = _load()
    pk_only = mod.scan(mod.default_docs(str(_REPO)), str(_REPO))
    included, _ = mod.contract_docs(str(_REPO))
    both = mod.scan(mod.default_docs(str(_REPO)), str(_REPO), extra=included)
    assert both["docs_scanned"] == pk_only["docs_scanned"] + len(included)
    assert len(included) > 0, "non-empty denominator assertion"


def test_the_real_tree_is_clean():
    """Measured at v3.66.928: all 7 of CLAUDE.md's backticked references
    resolve, so widening the gate does not fail the tree it ships with."""
    r = _run([])
    assert r.returncode == 0, (
        "widening the corpus made the shipped tree fail\n"
        + (r.stdout + r.stderr)[-800:])


def test_selftest_still_passes():
    r = _run(["--selftest"])
    assert r.returncode == 0, (r.stdout + r.stderr)[-800:]
