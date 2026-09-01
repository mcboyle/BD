#!/usr/bin/env python3
"""bd-mutate must decide a subject's shebang from the line a LOADER reads.

THE DEFECT. `toolchain/bin/bd-mutate::_validate` decided "is this subject
Python?" and "is this subject shell?" from `text.splitlines()[0]`.
`str.splitlines()` breaks on NINE separators beyond "\\n" -- a lone "\\r", plus
\\x0b \\x0c \\x1c \\x1d \\x1e \\x85 \\u2028 \\u2029 -- while every line-oriented
reader that actually matters here (the kernel's `#!` handling, CPython's own
source reader, `bash`) ends the first line only at "\\n".

`splitlines()[0]` is therefore always a PREFIX of `split("\\n", 1)[0]`, and the
failure is one-directional: a `python` (or `sh`) marker sitting AFTER an exotic
separator but BEFORE the first newline is INVISIBLE to the predicate. The
subject then matches neither the Python branch nor the shell branch nor a
TypeScript loader, falls through `_validate`'s closing `return True, ""` --
"unknown type: not our place to guess" -- and a mutant that does not even PARSE
is recorded VALID.

WHY THAT IS THE EXPENSIVE DIRECTION. This is backlog row 239's shape exactly:
an invalid mutant recorded VALID is handed to `_grade_mutant`, whose catcher
then fails because the subject is broken rather than because any assertion
fired, and the battery scores CAUGHT on a regression it never caused. bd-mutate
is the tool that applies EVERY mutant in tests/mutants, so a battery reporting
CAUGHT over a mutant that never landed is a battery that proves nothing.

RELATION TO ROW 572. Row 572 was this same `str.splitlines()` mismatch one
level up, in tests/test_row532_a_mutant_anchor_must_resolve_into_code.py's
`_comment_spans`; it shipped as v3.66.1406. That file's `_first_line` helper
names bd-mutate's predicate as "the shape this is the corrected form of" -- and
bd-mutate itself still carried it. This file closes it in the mutator.

ONE DIFFERENCE FROM ROW 572, MEASURED HERE AND NOT INHERITED. Row 572 had to
partition the nine separators, because four of them (\\x0b \\x1c \\x1d \\x1e)
make CPython refuse the file outright when they open a line in code position.
Here the separator lives inside the `#!` COMMENT, so all nine tokenize
identically and all nine reach `ast.parse`; the population needs no partition
and none is invented. The membership of each of the nine is DERIVED per member
below rather than asserted, so an interpreter that changes `str.splitlines`
fails the denominator test that owns it instead of quietly shrinking it.

MEASURED BLAST RADIUS at this base, from tests/mutants and `git ls-files`:
  22 extensionless mutant subjects carrying 459 mutant entries, every one of
     them classified by this shebang predicate rather than by suffix;
   0 of them -- and 0 of 3,786 tracked files tree-wide -- currently have a
     first line on which `str.splitlines()` and the loaders disagree.
So the fail-open is LATENT rather than live today. The predicate is still
wrong, and it is wrong in the direction that manufactures false CAUGHT.
"""
import ast
import importlib.machinery
import importlib.util
import os
import pathlib

import pytest

BD_GATE_SCOPE = "module"

REPO = pathlib.Path(__file__).resolve().parents[1]
MUTATE = REPO / "toolchain" / "bin" / "bd-mutate"


def _load_bd_mutate():
    """Import toolchain/bin/bd-mutate, which is extensionless by design."""
    assert MUTATE.is_file(), "toolchain/bin/bd-mutate is missing"
    name = "bd_mutate_first_line_under_test_%s" % os.getpid()
    spec = importlib.util.spec_from_loader(
        name, importlib.machinery.SourceFileLoader(name, str(MUTATE)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "_validate"), (
        "bd-mutate no longer exposes _validate; this gate's subject moved and "
        "a green result here would be about nothing")
    return mod


# ── the separator population, derived rather than asserted ──────────────────
#
# Each member is admitted only by a live measurement: str.splitlines() breaks on
# it and "\n"-splitting does not. A separator that stops satisfying that fails
# the denominator test rather than silently leaving the population.
#
# THE LONE "\r" IS THE MEMBER MOST EASILY DROPPED, and row 572 recorded why:
# "\r\n" -- the form everyone pictures -- is the one sequence both readers agree
# on, so writing the list from memory yields eight and omits the ninth. It is
# first here deliberately.
_EXOTIC_SEPARATORS = ["\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e",
                      "\x85", "\u2028", "\u2029"]
# Both readers agree on these, so they can never move a verdict. They are the
# negative half of the denominator, named so neither half can be dropped.
_AGREED_SEPARATORS = ["\n", "\r\n"]


def _broken_python_subject(separator: str) -> str:
    """An extensionless python subject that does NOT parse.

    The shebang says python only AFTER `separator`, so `splitlines()[0]` cannot
    see it while the loader's first line can. The body is deliberately
    unparseable: a correct predicate must reach ast.parse and REFUSE it, which
    is what distinguishes "classified Python" from "fell through to the
    unknown-type return True".
    """
    return "#!/usr/bin/env" + separator + "python3\nX = (\n"


def _broken_shell_subject(separator: str) -> str:
    """An extensionless shell subject `bash -n` refuses, marker after `separator`."""
    return "#!/bin/" + separator + "sh\nif then\n"


def _assert_predicate_seam(text: str, separator: str, marker: str) -> None:
    """PRECONDITION. The fixture really does present two different first lines,
    and the marker really is visible to only one of them."""
    naive = text.splitlines()[0]
    loader = text.split("\n", 1)[0]
    assert naive != loader, (
        f"{separator!r} does not make str.splitlines disagree with the loader "
        f"on this fixture ({naive!r} == {loader!r}), so it cannot exercise the "
        "defect")
    assert naive == loader[:len(naive)], (
        "splitlines()[0] is expected to be a PREFIX of the loader's first line; "
        f"it is not ({naive!r} vs {loader!r}) and this fixture's reasoning does "
        "not hold")
    assert marker not in naive, (
        f"{marker!r} is visible to str.splitlines()[0] ({naive!r}); the fixture "
        "would pass on the unfixed tree for the wrong reason")
    assert marker in loader, (
        f"{marker!r} is not in the loader's first line ({loader!r}); the fixture "
        "does not describe a real python/shell subject at all")


def _subject_path(tmp_path: pathlib.Path, text: str) -> pathlib.Path:
    """An extensionless on-disk subject. `_validate` reads path.suffix, so the
    EMPTY suffix is a precondition, not an incidental."""
    path = tmp_path / "bd-probe-subject"
    path.write_text(text, encoding="utf-8", newline="")
    assert path.suffix == "", "the fixture subject must be extensionless"
    return path


# ── the denominator ─────────────────────────────────────────────────────────
def test_the_exotic_separator_population_is_exactly_the_splitlines_surplus():
    """DERIVED membership, both halves, nothing hand-waved.

    Nine separators beyond "\\n" break str.splitlines and not "\\n"-splitting;
    the two agreed forms break both. Neither half may be dropped."""
    assert len(_EXOTIC_SEPARATORS) == len(set(_EXOTIC_SEPARATORS)) == 9, (
        "the exotic population is not the nine distinct members measured on "
        "this interpreter: %r" % (_EXOTIC_SEPARATORS,))
    assert "\r" in _EXOTIC_SEPARATORS, (
        "a lone CR is a str.splitlines separator and CR LF is not; dropping it "
        "is this cut's own defect committed inside this cut's own fix")
    assert not set(_EXOTIC_SEPARATORS) & set(_AGREED_SEPARATORS)
    for separator in _EXOTIC_SEPARATORS:
        probe = "a" + separator + "b"
        assert len(probe.splitlines()) == 2, (
            f"{separator!r} is not a str.splitlines separator on this "
            "interpreter at all")
        assert len(probe.split("\n")) == 1, (
            f"{separator!r} is honoured by \"\\n\"-splitting too, so it cannot "
            "move a first-line verdict and does not belong in this population")
    for separator in _AGREED_SEPARATORS:
        probe = "a" + separator + "b"
        assert len(probe.splitlines()) == len(probe.split("\n")) == 2, (
            f"{separator!r} is no longer agreed on by both readers; it belongs "
            "in _EXOTIC_SEPARATORS with evidence")


def test_bd_mutate_carries_no_naive_first_line_subscript():
    """The static half, PARSED rather than grepped.

    A7's inverse rule, and this cut committed its own defect against it once
    already: the first draft of this test scanned source TEXT for
    "splitlines()[0]" and then matched the docstring of the very helper that
    fixes the defect. A gate whose denominator includes its own explanation
    cannot go green for the right reason. So the subject here is the parse
    tree: a `<expr>.splitlines()[0]` SUBSCRIPT in executable source. Comments
    and docstrings are not in that population by construction.

    KNOWN AND NAMED LIMIT, because a silent one is the shape this repository
    keeps finding: this sees the direct subscript only, not
    `lines = t.splitlines()` followed by `lines[0]` two lines later. The
    runtime parametrized tests above are the real gate; this one exists so a
    direct reintroduction is visible rather than silent."""
    tree = ast.parse(MUTATE.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        if not (isinstance(node.slice, ast.Constant) and node.slice.value == 0):
            continue
        call = node.value
        if (isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "splitlines"):
            offenders.append(node.lineno)
    assert not offenders, (
        "toolchain/bin/bd-mutate takes a first line as `.splitlines()[0]` at "
        "line(s) %s; str.splitlines breaks on nine separators no loader honours"
        % sorted(offenders))


def test_the_parsed_scan_would_catch_the_defect_it_claims_to():
    """NEGATIVE CONTROL for the scan above. A gate that finds nothing must be
    shown capable of finding something -- including that it does NOT count a
    comment or a docstring, which is precisely how the first draft failed."""
    def offenders(source):
        found = []
        for node in ast.walk(ast.parse(source)):
            if (isinstance(node, ast.Subscript)
                    and isinstance(node.slice, ast.Constant)
                    and node.slice.value == 0
                    and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Attribute)
                    and node.value.func.attr == "splitlines"):
                found.append(node.lineno)
        return found

    assert offenders("first = text.splitlines()[0]\n") == [1], (
        "the scan cannot see the defect it exists to find")
    assert offenders('"""Deliberately NOT text.splitlines()[0]."""\n') == [], (
        "the scan counts a DOCSTRING mentioning the defect, which is the "
        "first draft of this very test and the reason it is parsed now")
    assert offenders("# note: text.splitlines()[0] was wrong\n") == [], (
        "the scan counts a COMMENT mentioning the defect")
    assert offenders('first = text.split("\\n", 1)[0]\n') == [], (
        "the scan counts the CORRECTED form as an offender")


def test_both_shebang_predicates_call_the_loader_first_line_helper():
    """The positive half: the fix is WIRED, not merely present.

    _validate's two shebang predicates must each call _first_line. Asserting
    the helper exists proves nothing about whether _validate uses it, and an
    orphaned helper beside an unchanged predicate is the shape where a fix
    reads as landed and is not."""
    tree = ast.parse(MUTATE.read_text(encoding="utf-8"))
    functions = {node.name: node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef)}
    assert "_first_line" in functions, (
        "bd-mutate has no _first_line helper; the fix is absent")
    assert "_validate" in functions, (
        "bd-mutate has no _validate; this gate's subject moved")
    calls = [node for node in ast.walk(functions["_validate"])
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name)
             and node.func.id == "_first_line"]
    assert len(calls) == 2, (
        "_validate calls _first_line %d time(s); it has exactly TWO shebang "
        "predicates (python and sh) and each must read the loader's first line"
        % len(calls))


# ── RED: the fail-open, one case per separator ──────────────────────────────
@pytest.mark.parametrize("separator", _EXOTIC_SEPARATORS)
def test_a_broken_python_subject_is_refused_whatever_its_shebang_separator(
        separator, tmp_path):
    """The whole defect. On the unfixed tree every one of these returns
    (True, "") -- an unparseable mutant recorded VALID, which is row 239's
    fail-open reached by a different door."""
    text = _broken_python_subject(separator)
    _assert_predicate_seam(text, separator, "python")
    mutate = _load_bd_mutate()

    valid, why = mutate._validate(_subject_path(tmp_path, text), text)

    assert valid is False, (
        f"bd-mutate recorded an UNPARSEABLE python subject as valid={valid!r} "
        f"because {separator!r} hid 'python' from str.splitlines()[0]; an "
        "invalid mutant graded VALID is scored CAUGHT on a regression it never "
        "caused")
    assert "ast.parse" in why, (
        "the refusal must come from the PYTHON branch (ast.parse), not from an "
        "unrelated later refusal that would launder the verdict; got %r" % why)


@pytest.mark.parametrize("separator", _EXOTIC_SEPARATORS)
def test_a_broken_shell_subject_is_refused_whatever_its_shebang_separator(
        separator, tmp_path):
    """The same defect on `_validate`'s second predicate (the `sh` branch)."""
    text = _broken_shell_subject(separator)
    _assert_predicate_seam(text, separator, "sh")
    mutate = _load_bd_mutate()

    valid, why = mutate._validate(_subject_path(tmp_path, text), text)

    assert valid is False, (
        f"bd-mutate recorded a shell subject `bash -n` refuses as valid="
        f"{valid!r} because {separator!r} hid 'sh' from str.splitlines()[0]")
    assert "bash -n" in why, (
        "the refusal must come from the SHELL branch, not from an unrelated "
        "later refusal; got %r" % why)


# ── negative controls: the fix must not widen classification ────────────────
#
# splitlines()[0] is a PREFIX of split("\n", 1)[0], so on any subject whose
# first line contains none of the nine the two predicates are the SAME string
# and no verdict can move. These pin that structurally rather than by hope.
def test_an_ordinary_py_subject_is_still_judged_by_suffix(tmp_path):
    """Control: `.py` never consulted a first line at all."""
    path = tmp_path / "ordinary.py"
    broken = "X = (\n"
    path.write_text(broken, encoding="utf-8")
    mutate = _load_bd_mutate()
    valid, why = mutate._validate(path, broken)
    assert valid is False and "ast.parse" in why, (why, valid)
    ok, why_ok = mutate._validate(path, "X = 1\n")
    assert ok is True and why_ok == "", (ok, why_ok)


def test_an_ordinary_python_shebang_subject_is_unchanged(tmp_path):
    """Control: the everyday extensionless toolchain/bin shape, both verdicts."""
    mutate = _load_bd_mutate()
    good = "#!/usr/bin/env python3\nX = 1\n"
    bad = "#!/usr/bin/env python3\nX = (\n"
    for text in (good, bad):
        assert text.splitlines()[0] == text.split("\n", 1)[0], (
            "this control must carry NO exotic separator; otherwise it is a "
            "second copy of the defect case, not a control")
    path = _subject_path(tmp_path, good)
    assert mutate._validate(path, good) == (True, "")
    valid, why = mutate._validate(path, bad)
    assert valid is False and "ast.parse" in why, (valid, why)


def test_an_ordinary_bash_shebang_subject_is_unchanged(tmp_path):
    """Control: toolchain/bin/bd-venv's real shape -- shell, not python."""
    mutate = _load_bd_mutate()
    good = "#!/bin/bash\ntrue\n"
    bad = "#!/bin/bash\nif then\n"
    path = _subject_path(tmp_path, good)
    assert mutate._validate(path, good) == (True, "")
    valid, why = mutate._validate(path, bad)
    assert valid is False and "bash -n" in why, (valid, why)


def test_a_genuine_non_python_subject_is_still_declined(tmp_path):
    """Control, and the one that matters most: the fix must not classify
    EVERYTHING as python. A subject with no shebang, and one whose shebang
    names neither python nor sh, must still reach the closing `return True, ""`
    -- unknown type, not our place to guess -- and NOT be ast.parsed."""
    mutate = _load_bd_mutate()
    unparseable_as_python = "X = (\n"
    no_shebang = _subject_path(tmp_path, unparseable_as_python)
    assert mutate._validate(no_shebang, unparseable_as_python) == (True, ""), (
        "an extensionless subject with no shebang must be DECLINED, not parsed")

    perl = "#!/usr/bin/perl\nmy $x = (\n"
    assert "python" not in perl.split("\n", 1)[0]
    assert "sh" not in perl.split("\n", 1)[0]
    path = _subject_path(tmp_path, perl)
    assert mutate._validate(path, perl) == (True, ""), (
        "a perl shebang must still be declined; widening classification to "
        "every subject is the over-correction this control exists to catch")


def test_the_real_toolchain_subjects_keep_the_verdict_they_have_today():
    """Seam control over the LIVE population, not a fixture.

    Every extensionless subject in toolchain/bin is classified, and the counts
    are asserted nonzero so a collapsed denominator cannot read as clean. These
    subjects carry no exotic separator today (that is the measured blast-radius
    finding), so the fix must leave every one of them exactly where it was."""
    binaries = sorted(p for p in (REPO / "toolchain" / "bin").iterdir()
                      if p.is_file() and p.suffix == "")
    assert len(binaries) > 100, (
        "toolchain/bin holds %d extensionless files; the population collapsed"
        % len(binaries))
    python_subjects = shell_subjects = agreeing = 0
    for path in binaries:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if not text:
            continue
        naive = text.splitlines()[0]
        loader = text.split("\n", 1)[0]
        if naive == loader:
            agreeing += 1
        if text.startswith("#!") and "python" in loader:
            python_subjects += 1
        elif text.startswith("#!") and "sh" in loader:
            shell_subjects += 1
    assert python_subjects > 100 and shell_subjects >= 1, (
        "python=%d shell=%d -- the live classification collapsed"
        % (python_subjects, shell_subjects))
    assert agreeing == len([p for p in binaries
                            if p.is_file() and _readable(p)]), (
        "a toolchain/bin subject now carries a first line the two readers "
        "disagree on; the fail-open this file closes has become LIVE, and this "
        "assertion is the notice that it did")


def _readable(path: pathlib.Path) -> bool:
    try:
        return bool(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, OSError):
        return False
