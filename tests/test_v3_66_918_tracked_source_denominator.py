"""The retirement gates' denominator excluded most of this repo's source.

Item 16 / 7a, second half. The three *_stays_retired gates asked "does anything
still execute against this dead thing?" over

    git ls-files -z -- '*.py' '*.sh'

MEASURED at v3.66.918: that glob returns 2180 tracked files, and a further 473
tracked files are python- or shell-shebang scripts with NO EXTENSION -- the
whole toolchain/bin bd-* suite and its project-knowledge mirror. Every one sat
outside every gate that used it, and the gates reported clean. v3.66.917
deleted three retired tools that had survived 59 releases in exactly that blind
spot.

WHAT THIS FILE PINS, and why it is two properties rather than one.

The obvious repair -- widen the denominator -- is only half, and shipping just
that half makes the gates WORSE. Each one branches on `path.suffix == ".py"` to
choose between an AST walk (which excludes docstrings) and a line scan (which
cannot). Let an extensionless python script in without fixing that predicate
and it gets read as if it were shell, so its DOCSTRINGS count as executable
references and the gate fails honest tools for their file extension.

Measured both ways at v3.66.918: routing naively flags 4 live tools across 2
gates; routing on CONTENT flags 2 tools on 1 gate -- and both of those turned
out to be real stale references (bd-footguns and bd-lost-symbol still told the
operator to run a tool deleted at v3.66.917, using a zip-overlay deploy that no
longer exists either), so they were fixed rather than allowlisted. The widened
gates need NO allowlist at all.

That second measurement is the reason `kind` exists. CLAUDE.md section 0's
inverse defect: a gate made over-sensitive is not a safer gate, it is one that
gets switched off.

THE REGISTER OVERSTATED THE COST, twice. It says this "turns three gates red on
four LIVE tools". After v3.66.917 and with content routing it is ONE gate and
TWO tools, and the codex_handoff gate cannot go red under any widening -- zero
newly-entering files mention its subject.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from tracked_source import source_kind, tracked_source_files

REPO_ROOT = Path(__file__).resolve().parents[1]


def _old_glob() -> set[str]:
    """What the gates used to enumerate, so the comparison is measured."""
    proc = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py", "*.sh"],
        cwd=str(REPO_ROOT), capture_output=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[:400]
    return {p for p in proc.stdout.decode("utf-8", "replace").split("\0") if p}


def test_the_widened_denominator_is_a_strict_superset():
    """Nothing the old glob saw may be dropped, and it must see more.

    A "widening" that swapped one blind spot for another would satisfy a bare
    count assertion. Both directions are checked.
    """
    old = _old_glob()
    new = {rel for rel, _k in tracked_source_files(REPO_ROOT)}
    assert not (old - new), (
        "the widened enumerator LOST files the old glob saw: %r"
        % (sorted(old - new)[:20],))
    gained = new - old
    assert len(gained) > 200, (
        # @943: was > 300, when project-knowledge/ still carried 234
        # byte-identical mirrors of toolchain/bin. Retiring them halved the
        # extensionless population; MEASURED 241 after. The threshold guards
        # "the bd-* suite is visible", not a headcount, so it moves with the
        # tree rather than pinning a number that goes stale on the next tool.
        "expected the extensionless shebang scripts to enter the "
        "denominator; got %d. If this dropped, the bd-* suite is invisible "
        "again." % (len(gained),))


def test_the_extensionless_tool_suite_is_actually_in_it():
    """Name real files, not just a count.

    A count can be satisfied by any 300 files. These are the population the
    blind spot actually hid -- the operator tool suite CLAUDE.md section 8
    calls its own population.
    """
    got = dict(tracked_source_files(REPO_ROOT))
    for rel in ("toolchain/bin/bd-band-derive", "toolchain/bin/bd-guardcheck",
                "toolchain/bin/bd-mutate"):
        assert got.get(rel) == "python", (
            "%s is a tracked python-shebang tool but the enumerator typed it "
            "%r" % (rel, got.get(rel)))


def test_an_extensionless_python_script_is_typed_python_not_shell():
    """THE ROUTING PROPERTY, and the reason widening alone is not enough.

    If this returns anything but "python", the gates line-scan a python file:
    its docstrings become "executable references" and live tools fail for
    their lack of a file extension. This is the assertion that fails a
    naive widening.
    """
    p = REPO_ROOT / "toolchain" / "bin" / "bd-mutate"
    assert p.is_file(), "fixture moved; pick another extensionless bd-* tool"
    assert source_kind(p, "toolchain/bin/bd-mutate") == "python"


def test_prose_is_still_not_source(tmp_path):
    """OVER-CORRECTION GUARD. The gates permit prose deliberately.

    History, tombstones and CHANGELOG entries are expected to name retired
    tools. If .md or .json started counting as source, every tombstone would
    become an offender and three gates would fail on the documentation written
    to explain them.
    """
    for name in ("NOTES.md", "data.json", "page.html"):
        f = tmp_path / name
        f.write_text("#!/usr/bin/env python3\nnot source\n", encoding="utf-8")
        assert source_kind(f, name) is None, (
            "%s was typed as source; prose must stay outside the denominator "
            "even when its first line looks like a shebang" % (name,))


def test_a_shell_shebang_is_typed_shell():
    """The other branch. A shell script must keep line-scanning.

    Routing a shell script to the AST walk would make it unparseable and
    `except SyntaxError: continue` would silently drop it -- a file leaving the
    denominator through the error path, which is the original defect wearing a
    different hat.
    """
    got = dict(tracked_source_files(REPO_ROOT))
    shells = [r for r, k in got.items() if k == "shell"]
    assert shells, "no shell source found at all -- the classifier collapsed"
    assert any(r.endswith(".sh") for r in shells), sorted(shells)[:10]


def test_an_unreadable_denominator_is_not_a_pass():
    """git unavailable must yield EMPTY, so callers skip rather than pass.

    Returning [] is the "unknown" third state. The gates check it and skip;
    a helper that returned a partial list on failure would let a gate assert
    over nothing and report clean.
    """
    empty = tracked_source_files(Path("/nonexistent-repo-for-918"))
    assert empty == [], empty
