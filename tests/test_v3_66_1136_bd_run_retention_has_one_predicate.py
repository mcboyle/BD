"""bd-run's retention bound and the check that asserts it must agree.

MEASURED ON THE BOX, not hypothesised. A six-host capture round at v3.66.1134
failed on TWO hosts -- test6 and test2, verified identical -- with:

    FAILED tests/test_toolchain_534.py::test_the_tools_this_cut_added_are_wired_and_selftest_clean
    SELFTEST FAIL: prune left more logs than --keep allows

That is roughly a 40% failure rate on a gate that has nothing to do with the
tree, and `bd-run` was untouched by every cut in that session (it last changed
at @1060). The tree was clean; the gate was not.

THE DEFECT IS IN THE ASSERTION, NOT IN prune. `prune` deliberately excludes
symlinks -- its docstring says the `<label>.log` alias "is a pointer, not an
artifact, and counting it as one would let a directory holding N runs plus N
aliases keep only N/2 actual logs while reporting that it kept N". So prune
bounds REAL FILES to `keep`. The selftest counted `glob("*.log")`, which
INCLUDES those aliases -- asserting a property prune explicitly does not have.

WHY IT ONLY SOMETIMES FIRED, reproduced deterministically at v3.66.1136:

    mtime tie=False   real kept=2 (correct)   glob=2   -> passes
    mtime tie=True    real kept=2 (correct)   glob=4   -> fails

The selftest writes three run logs (each with an alias) and then five fillers.
Normally the fillers are newest, prune keeps two of THOSE, no alias survives,
and the naive count happens to be right. When all eight land in one filesystem
tick -- a fast box under `-n 48` capture load -- prune's newest-two can be run
logs whose aliases then legitimately survive, and the count sees four.

So the gate failed CORRECT WORK, at 40%, under exactly the conditions the box
runs. CLAUDE.md section 0 calls that a soundness bug rather than a safe default:
a gate that cries wolf gets switched off, and then it protects nothing.

THE FIX IS ONE PREDICATE, NOT ONE PATCHED LINE. Two places decided what "a log"
means and they disagreed. `_real_logs()` is now the single definition, used by
prune to choose what to delete and by the selftest to count what survived, so
they cannot drift apart again. Patching only the assertion would have left the
same two-definitions shape that produced this.
"""

from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import os
import pathlib
import tempfile
import time

# Its subject is one tool's retention predicate, not the tree.
BD_GATE_SCOPE = "module"

REPO = pathlib.Path(__file__).resolve().parent.parent
TOOL = REPO / "toolchain" / "bin" / "bd-run"


def _load():
    spec = importlib.util.spec_from_loader(
        "bd_run_under_test",
        importlib.machinery.SourceFileLoader("bd_run_under_test", str(TOOL)))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_dir(tie: bool) -> pathlib.Path:
    """The selftest's own step-6 shape: 3 run logs + aliases, then 5 fillers."""
    p = pathlib.Path(tempfile.mkdtemp())
    for label in ("ok", "bad", "keep"):
        real = p / f"{label}-20260814T212153-443381.log"
        real.write_text("x")
        (p / f"{label}.log").symlink_to(real.name)
    for i in range(5):
        (p / f"filler{i}.log").write_text("x")
    if tie:
        t = time.time()
        for f in p.iterdir():
            if not f.is_symlink():
                os.utime(f, (t, t))
    return p


def test_the_tool_exists_and_parses():
    """PRECONDITION -- without it everything below is vacuous."""
    assert TOOL.is_file(), f"no bd-run at {TOOL}"
    ast.parse(TOOL.read_text(encoding="utf-8"))


def test_there_is_exactly_one_definition_of_a_real_log():
    """THE FIX. Two places decided what counts, and they disagreed."""
    mod = _load()
    assert hasattr(mod, "_real_logs"), (
        "bd-run has no _real_logs(). prune and its selftest each decided "
        "separately what 'a log' means -- prune excluded symlinks, the "
        "assertion counted them -- and the disagreement failed correct work on "
        "2 of 6 hosts. One predicate, used by both.")
    d = _make_dir(tie=False)
    got = mod._real_logs(d)
    names = sorted(p.name for p in got)
    assert all(not p.is_symlink() for p in got), (
        f"_real_logs returned a symlink: {names}")
    assert len(got) == 8, (
        f"_real_logs should see the 8 real files and none of the 3 aliases, "
        f"got {len(got)}: {names}")


def test_prune_bounds_real_files_even_when_every_mtime_ties():
    """prune was always correct; this pins that so the fix cannot 'fix' it."""
    mod = _load()
    for tie in (False, True):
        d = _make_dir(tie=tie)
        mod.prune(str(d), 2)
        real = [p for p in d.glob("*.log")
                if p.is_file() and not p.is_symlink()]
        assert len(real) == 2, (
            f"with mtime tie={tie}, prune left {len(real)} real logs, "
            "expected exactly 2")


def test_the_retention_check_agrees_with_prune_under_a_tie():
    """THE RED CASE, deterministic -- this is what failed on test6 and test2.

    Counting via the shared predicate must give the bound; counting via a naive
    glob must NOT be what the tool relies on. The second assertion is the
    mechanism, kept so a future reader sees why the naive form was wrong rather
    than having to rediscover it under capture load at 40%.
    """
    mod = _load()
    d = _make_dir(tie=True)
    mod.prune(str(d), 2)

    assert len(mod._real_logs(d)) <= 2, (
        "the shared predicate disagrees with prune's own bound")

    naive = len(list(pathlib.Path(d).glob("*.log")))
    assert naive > 2, (
        "the tie fixture no longer reproduces the surviving-alias condition, "
        f"so this test has stopped exercising the defect (naive={naive}). "
        "Re-derive the fixture rather than deleting the assertion.")


def test_the_selftest_no_longer_counts_aliases():
    """Structural backstop: the assertion must not use a bare glob count.

    Asserted over the parsed source so a comment mentioning glob cannot satisfy
    or break it -- a comment is inside the denominator of every gate that reads
    source text (CLAUDE.md section 0, four recorded instances).
    """
    tree = ast.parse(TOOL.read_text(encoding="utf-8"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "selftest"), None)
    assert fn is not None, "selftest is not a module-level function any more"
    body = ast.unparse(fn)
    assert "prune left more logs" in body, (
        "the retention check vanished entirely -- that is not a fix, it is a "
        "deleted assertion")
    assert "_real_logs" in body, (
        "the retention check does not use the shared predicate, so it can "
        "drift from prune again exactly as it did before")


def test_the_selftest_passes_repeatedly():
    """End to end: the thing the box actually runs."""
    mod = _load()
    import argparse
    for _ in range(3):
        assert mod.selftest(argparse.Namespace()) == 0, (
            "bd-run --selftest failed on a clean tree")
