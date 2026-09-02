"""bd-precut must MEASURE version/pin/surface, not report it UNKNOWN forever.

THE DEFECT. precut_check needs a snapshot of the tree the cut is measured
against, and reads that snapshot from a zip. The release zip flow is retired
here, so in a git checkout there was never a baseline: bd-precut printed

    [precut_check NOT RUN -- no baseline zip (pass --baseline; the zip flow is
    retired here)]
    RESULT: cut-ready for what RAN ... 1 check(s) NOT RUN, so UNKNOWN, not OK

on EVERY cut, forever. Reporting an unmeasured check as UNKNOWN is right (A2,
A7). Being unable to ever measure it is not: that UNKNOWN held v3.66.1360 for
hours and blocked v3.66.1378, and a verdict nobody can satisfy is one people
learn to step over -- which costs more than the check was ever worth.

origin/main IS the snapshot, and git can produce it exactly. These tests pin
the three things that make deriving it honest rather than convenient:

  1. it is DERIVED, so the check actually runs and reports a measurement;
  2. it still FAILS CLOSED -- no origin/main means UNKNOWN, never a pass;
  3. BOTH SIDES USE ONE DENOMINATOR. A git archive holds the tracked set;
     build_release's walk holds the tracked set plus the generated artifacts it
     would package. Comparing one against the other reports every generated
     file as newly ADDED, which is a surface report about the tooling rather
     than about the cut -- a measurement that runs and says nothing is not an
     improvement on an honest UNKNOWN.
"""
from __future__ import annotations

import importlib.machinery
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "toolchain" / "bin" / "bd-precut"
CHECK = REPO / "tools" / "precut_check.py"
IMPORTS = REPO / "toolchain" / "bin" / "bd-imports"
IMPORT_GATE = REPO / "tools" / "decomp" / "import_graph_gate.py"
DEPENDENCY_GRAPH = REPO / "tools" / "dependency_graph.py"


def _load():
    return importlib.machinery.SourceFileLoader("bd_precut_under_test", str(TOOL)).load_module()


def git(repo, *a, check=True):
    p = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
    if check and p.returncode:
        raise RuntimeError(f"git {' '.join(a)}: {p.stderr}")
    return p.stdout.strip()


@pytest.fixture()
def tiny_repo(tmp_path):
    """A checkout with an origin/main, one tracked file and one gitignored
    GENERATED file -- the shape that exposes a mismatched denominator."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    r = tmp_path / "r"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@t")
    git(r, "config", "user.name", "t")
    (r / "bulk_downloader").mkdir()
    (r / "bulk_downloader" / "__init__.py").write_text('__version__ = "3.66.1"\n')
    (r / ".gitignore").write_text("GENERATED.json\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "base")
    git(r, "remote", "add", "origin", str(origin))
    git(r, "push", "-q", "origin", "main")
    git(r, "fetch", "-q", "origin")
    # the generated artifact: present in the working tree, never tracked
    (r / "GENERATED.json").write_text('{"generated": true}\n')
    return r


def test_a_baseline_is_derived_from_origin_main(tiny_repo, tmp_path):
    m = _load()
    out = tmp_path / "td"
    out.mkdir()
    path, reason = m._derive_baseline(str(tiny_repo), str(out))
    assert reason is None, reason
    assert path and os.path.getsize(path) > 0
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
    # PRECONDITION, asserted rather than assumed: the archive really is the
    # tracked set of origin/main, at repo-relative paths precut_check compares.
    assert "bulk_downloader/__init__.py" in names, names
    assert "GENERATED.json" not in names, "a git archive must not carry an untracked artifact"


def test_no_origin_main_stays_unknown_and_never_passes(tiny_repo, tmp_path):
    """FAIL CLOSED. The point of the change is to replace an unmeasurable
    UNKNOWN with a measurement, NOT to remove the UNKNOWN state."""
    m = _load()
    git(tiny_repo, "remote", "remove", "origin")
    git(tiny_repo, "update-ref", "-d", "refs/remotes/origin/main", check=False)
    out = tmp_path / "td"
    out.mkdir()
    path, reason = m._derive_baseline(str(tiny_repo), str(out))
    assert path is None
    assert reason and "origin/main" in reason, reason


def test_a_non_git_directory_stays_unknown(tmp_path):
    m = _load()
    plain = tmp_path / "plain"
    plain.mkdir()
    out = tmp_path / "td"
    out.mkdir()
    path, reason = m._derive_baseline(str(plain), str(out))
    assert path is None
    assert reason and "git" in reason.lower(), reason


def test_the_two_sides_share_one_denominator(tiny_repo, tmp_path):
    """THE NEGATIVE CONTROL THAT MATTERS.

    Run precut_check against the derived baseline twice: once with
    --tracked-only and once without. Without it the gitignored generated file
    is reported as ADDED -- a check that runs and describes the tooling. With
    it, the two sides reconcile and the surface report is about the cut.
    """
    m = _load()
    out = tmp_path / "td"
    out.mkdir()
    path, reason = m._derive_baseline(str(tiny_repo), str(out))
    assert reason is None

    def added(*extra):
        r = subprocess.run(
            [sys.executable, str(CHECK), "--baseline", path, "--root", str(tiny_repo),
             "--json", *extra],
            capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        import json
        return json.loads(r.stdout)["added"]

    mismatched = added()
    reconciled = added("--tracked-only")
    assert "GENERATED.json" in mismatched, (
        "precondition failed: the mismatched denominator must actually show the "
        "generated file as added, or this test proves nothing")
    assert "GENERATED.json" not in reconciled, reconciled
    assert reconciled == [], reconciled


def test_tracked_only_refuses_when_git_cannot_answer(tmp_path):
    """An unmeasurable denominator is UNKNOWN, never an empty one.

    _git_tracked_files returning None must REFUSE, not silently compare an
    empty tree against the baseline -- which would report every file removed.
    """
    plain = tmp_path / "plain"
    plain.mkdir()
    z = tmp_path / "b.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("bulk_downloader/__init__.py", '__version__ = "3.66.1"\n')
    r = subprocess.run(
        [sys.executable, str(CHECK), "--baseline", str(z), "--root", str(plain),
         "--tracked-only"], capture_output=True, text=True)
    assert r.returncode != 0, r.stdout
    assert "UNKNOWN" in (r.stdout + r.stderr), r.stdout + r.stderr


def test_bd_imports_executes_the_shrink_remedy_its_refusal_names(tmp_path):
    """Row 632: the wrapper must execute the underlying gate's remedy.

    Build one removed edge AND one new edge.  The new edge makes bd-imports'
    initial --check nonzero, while the removed edge makes its --update reach
    BaselineShrinkError.  Without both preconditions the wrapper can return
    early and manufacture green without ever testing the refusal.
    """
    root = tmp_path / "imports-root"
    (root / "bulk_downloader").mkdir(parents=True)
    (root / "tools" / "decomp").mkdir(parents=True)
    (root / "bulk_downloader" / "alpha.py").write_text(
        "from . import beta\n", encoding="utf-8")
    (root / "bulk_downloader" / "beta.py").write_text(
        "VALUE = 1\n", encoding="utf-8")
    shutil.copy2(DEPENDENCY_GRAPH, root / "tools" / "dependency_graph.py")
    shutil.copy2(IMPORT_GATE, root / "tools" / "decomp" / "import_graph_gate.py")
    gate = root / "tools" / "decomp" / "import_graph_gate.py"
    baseline = root / "tools" / "decomp" / "import_graph_baseline.json"

    seeded = subprocess.run(
        [sys.executable, str(gate), "--update"], capture_output=True, text=True)
    assert seeded.returncode == 0, seeded.stdout + seeded.stderr
    before = baseline.read_bytes()
    seeded_map = json.loads(before)["edges"]
    assert seeded_map == {
        "bulk_downloader/alpha.py": ["bulk_downloader/beta.py"]
    }, f"precondition: expected exactly one frozen edge, got {seeded_map!r}"

    (root / "bulk_downloader" / "alpha.py").write_text(
        "VALUE = 2\n", encoding="utf-8")
    (root / "bulk_downloader" / "gamma.py").write_text(
        "from . import beta\n", encoding="utf-8")
    checked = subprocess.run(
        [sys.executable, str(gate), "--check"], capture_output=True, text=True)
    check_output = checked.stdout + checked.stderr
    assert checked.returncode == 1, check_output
    assert "note: 1 baseline edge(s) no longer present" in check_output, check_output
    assert "FAIL: 1 NEW import edge(s)" in check_output, check_output
    assert "bulk_downloader/alpha.py -> bulk_downloader/beta.py" in check_output
    assert "bulk_downloader/gamma.py -> bulk_downloader/beta.py" in check_output

    refused = subprocess.run(
        [sys.executable, str(IMPORTS), "--work", str(root), "--update"],
        capture_output=True, text=True)
    refusal_output = refused.stdout + refused.stderr
    assert refused.returncode == 1, refusal_output
    assert "refusing to shrink the baseline: 1 frozen edge(s)" in refusal_output
    assert "Re-run with --shrink" in refusal_output, refusal_output
    assert baseline.read_bytes() == before, "the refused update rewrote the baseline"

    remedy = subprocess.run(
        [sys.executable, str(IMPORTS), "--work", str(root), "--update", "--shrink"],
        capture_output=True, text=True)
    remedy_output = remedy.stdout + remedy.stderr
    assert remedy.returncode == 0, (
        "bd-imports named --shrink as the remedy but rejected or failed that "
        f"same wrapper invocation:\n{remedy_output}")
    assert baseline.read_bytes() != before, "the successful remedy rewrote nothing"
    updated_map = json.loads(baseline.read_bytes())["edges"]
    assert updated_map == {
        "bulk_downloader/gamma.py": ["bulk_downloader/beta.py"]
    }, f"the remedy froze the wrong edge population: {updated_map!r}"

    negative = subprocess.run(
        [sys.executable, str(IMPORTS), "--work", str(root), "--shrink"],
        capture_output=True, text=True)
    negative_output = negative.stdout + negative.stderr
    assert negative.returncode == 2, negative_output
    assert "--shrink is only meaningful with --update" in negative_output
