"""Row 532: a mutant anchor that resolves only into a COMMENT is not a mutant.

bd-mutate finds its anchor by TEXT. Nothing in the pipeline asks whether the
text it found is executable, so an anchor can resolve "exactly once" onto a line
of prose. Such a mutant edits a comment, the subject's behaviour is unchanged,
the catcher passes, and the battery reports a caught regression it never caused.
That is the fail-open shape CLAUDE.md A7 names: a gate that cannot see its
subject and reports OK anyway.

WHY NOW. v3.66.1381 retired a hand-bumped literal and left a comment explaining
the retirement, and that comment necessarily NAMES the constant with its old
value. Twice in that cut a text scan matched its own explanation -- once in this
repository's gates, once in the operator harness. The general lesson is A7's;
this file is the mechanical version of it for mutant anchors specifically.

WHAT ROWS 567, 568 AND 572 THEN FOUND IN THIS FILE, which is the same fail-open
shape one level up: a gate cannot see the subject it claims to judge.

  567/568  The survey decided "is this Python?" by file SUFFIX. bd-mutate --
           the tool that actually applies these mutants -- decides by suffix OR
           by a python shebang on an extensionless file, so every
           toolchain/bin/bd-* subject sat outside this ratchet's denominator.
           A comment-only mutant there would be applied, change nothing, and be
           graded CAUGHT, and the old `examined >= 500` floor could never
           surface the blindness because 536 dotted anchors cleared it alone.
  572      _comment_spans built its character-offset table with
           str.splitlines(), which breaks on nine separators the tokenizer
           does not -- a lone CR among them, while CR LF, the form everyone
           pictures, is not one of the nine. The misalignment is
           BIDIRECTIONAL and the fail-open half is real: a comment anchor is
           reported CODE.

MEASURED ON THIS TREE, because a ratchet on a dirty population is a ratchet
nobody can keep. Every number here was counted from tests/mutants at the cut
that widened the predicate, never quoted from prose:

  1168 mutant entries across 212 tracked specs, partitioned exactly
   993 python-subject anchors over 153 distinct subjects
         536 by .py suffix          <- the whole denominator before row 567
         457 by python shebang      <- toolchain/bin/bd-*, previously invisible
     0 resolve comment-only          <- the ratchet stays clean at the new size
     0 resolve partly in a comment
     0 unresolved, absent, or fileless python subjects
   175 non-python anchors over 40 subjects (.sh, .ts/.tsx, .md, .yml, ci.yml,
         corpus data, and toolchain/bin/bd-venv, whose shebang says bash)

The broader rule -- "no comment may contain assignment-shaped text" -- was
measured and REJECTED: 446 occurrences across 226 tracked files, nearly all of
them ordinary prose like `nargs='+'` or `exit=1`. Narrow is what makes this
enforceable.

A PARTITION, NOT A FLOOR (following rows 569/570, which retired exactly this
shape in tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py). The old
`examined >= 500` literal was a chore with slack: it never rose on growth, so
the window between it and the truth widened with every spec added, and it read
as clean while 457 of its subject were missing. It is replaced by a
reconciliation -- every spec entry lands in exactly one bucket and the buckets
sum to the corpus -- plus an identity pin on the one closed sub-population that
can hide a predicate collapse: the extensionless subjects this gate declines.
Adding a mutant never edits a number here.

THE ORIGINAL RATCHET HAS NO RED-FIRST PROVENANCE against a defective base
because there was no defect to replay. What stands in for it is the negative
controls below. The 567/568/572 corrections DO have it, replayed against this
file's own defective predicate and offset table.
"""
from __future__ import annotations

import io
import json
import pathlib
import re
import tokenize
from typing import NamedTuple

import pytest

BD_GATE_SCOPE = "repo-wide"

_REPO = pathlib.Path(__file__).resolve().parents[1]
_SPEC_DIR = _REPO / "tests" / "mutants"

# EXTENSIONLESS SUBJECTS THIS GATE DECLINES, PINNED BY IDENTITY rather than by
# count -- the same device _NON_DERIVABLE_DECLARED uses in the 939 gate, and for
# the same reason. The dotted half of the population grows every week and must
# never require an edit here. The dotless half is closed and tiny, and it is the
# only place a collapsed predicate can hide: if _is_python_subject were narrowed
# back to a suffix test, all 457 toolchain/bin/bd-* subjects would appear here
# and be NAMED, where a count floor would only have gone quiet. This set may
# grow only when a genuinely non-Python extensionless subject is added, which is
# a deliberate edit visible in the diff.
_DOTLESS_NON_PYTHON_SUBJECTS = {
    "toolchain/bin/bd-venv",          # "#!/bin/bash"
}


def _first_line(text: str) -> str:
    """The first line as a loader reads it: everything up to the first "\\n".

    Deliberately NOT str.splitlines()[0]. See _comment_spans: splitlines breaks
    on nine separators no line-oriented reader does, so a subject carrying one
    could present a different first line here than to the kernel. bd-mutate's
    own shebang predicate has that shape; this is the corrected form.
    """
    return text.split("\n", 1)[0]


def _is_python_subject(target: str, text: str) -> bool:
    """Is this mutant subject Python? bd-mutate's predicate, not a .py glob.

    toolchain/bin/bd-mutate::_validate decides Python by `.py` suffix OR by a
    python shebang on an extensionless file, and it is the tool that actually
    applies every anchor in tests/mutants. A gate that decides by suffix alone
    judges a strictly smaller population than the tool it exists to constrain,
    which is rows 567 and 568.
    """
    if target.endswith(".py"):
        return True
    if pathlib.PurePosixPath(target).suffix:
        return False                  # .sh, .ts, .tsx, .md, .yml, corpus data
    return text.startswith("#!") and "python" in _first_line(text)


def _comment_spans(source: str) -> list[tuple[int, int]] | None:
    """Character spans of every COMMENT token, or None if the file cannot tokenize.

    A docstring or any other STRING is deliberately NOT included: a string
    literal is executable source, and mutating one is a real mutation. Only a
    comment is inert.
    """
    # THE TOKENIZER'S LINES, NOT str.splitlines()' LINES (row 572).
    #
    # tokenize reads through io.StringIO(source).readline, which ends a line
    # only at "\n" (and, identically, at "\r\n"). str.splitlines breaks on
    # NINE more -- a lone \r, plus \v \f \x1c \x1d \x1e \x85 \u2028 \u2029 --
    # so a subject carrying any of them
    # gives this table MORE entries than the tokenizer has lines, every later
    # token.start[0] indexes one entry too early, and the comment span slides
    # backwards by the length of the line the extra entry skipped. MEASURED:
    # the inversion is BIDIRECTIONAL -- a real assignment is reported
    # COMMENT_ONLY (a legitimate mutant refused) and a real comment is reported
    # CODE (the fail-open half, which is the whole point of this file).
    line_starts = [0]
    for line in source.split("\n")[:-1]:
        line_starts.append(line_starts[-1] + len(line) + 1)
    spans: list[tuple[int, int]] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                spans.append((line_starts[token.start[0] - 1] + token.start[1],
                              line_starts[token.end[0] - 1] + token.end[1]))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None
    return spans


def _offsets(mutant: dict, source: str) -> list[int] | None:
    if "old" in mutant:
        return [m.start() for m in re.finditer(re.escape(mutant["old"]), source)]
    if "old_regex" in mutant:
        try:
            return [m.start() for m in re.finditer(mutant["old_regex"], source)]
        except re.error:
            return None
    return None


def _mutants(document) -> list[dict]:
    if isinstance(document, dict) and "mutants" in document:
        return document["mutants"]
    if isinstance(document, list):
        return document
    return []


def classify(mutant: dict, source: str) -> str:
    """CODE, COMMENT_ONLY, PARTLY_COMMENT, or UNRESOLVED."""
    spans = _comment_spans(source)
    if spans is None:
        return "UNRESOLVED"
    offsets = _offsets(mutant, source)
    if not offsets:
        return "UNRESOLVED"
    in_comment = [any(a <= o < b for a, b in spans) for o in offsets]
    if all(in_comment):
        return "COMMENT_ONLY"
    if any(in_comment):
        return "PARTLY_COMMENT"
    return "CODE"


class Survey(NamedTuple):
    """A PARTITION of the mutant corpus. Every entry lands in exactly one count.

    The buckets are mutually exclusive and their sum is asserted against the
    independently counted corpus size, so an entry can never leave the
    denominator without the reconciliation naming it.
    """

    specs: int
    total: int
    no_target: int              # a mutant with neither `file` nor a usable one
    absent: int                 # subject not in the tree -- bd-anchorcheck's
    non_python: int             # subject present, not Python by _is_python_subject
    unresolved: int             # python subject, anchor did not resolve -- row 357
    examined_dotted: int        # python by .py suffix
    examined_dotless: int       # python by shebang, extensionless
    offenders: list[str]
    dotless_python_subjects: set[str]
    dotless_other_subjects: set[str]

    @property
    def examined(self) -> int:
        return self.examined_dotted + self.examined_dotless

    @property
    def accounted(self) -> int:
        return (self.no_target + self.absent + self.non_python
                + self.unresolved + self.examined)


def _survey(repo: pathlib.Path = _REPO,
            spec_dir: pathlib.Path | None = None) -> Survey:
    """Classify every mutant entry under spec_dir against its subject in repo."""
    spec_dir = (repo / "tests" / "mutants") if spec_dir is None else spec_dir
    assert spec_dir.is_dir(), f"no mutant spec directory at {spec_dir}"
    specs = sorted(spec_dir.glob("*.json"))
    assert specs, "zero mutant specs -- UNKNOWN, not a clean population"
    sources: dict[str, str | None] = {}
    total = no_target = absent = non_python = unresolved = 0
    dotted = dotless = 0
    offenders: list[str] = []
    dotless_python: set[str] = set()
    dotless_other: set[str] = set()
    for spec_path in specs:
        try:
            rel_spec = str(spec_path.relative_to(repo))
        except ValueError:
            rel_spec = spec_path.name
        document = json.loads(spec_path.read_text(encoding="utf-8"))
        for mutant in _mutants(document):
            total += 1
            target = mutant.get("file")
            if not target:
                no_target += 1
                continue
            if target not in sources:
                path = repo / target
                sources[target] = (path.read_text(encoding="utf-8",
                                                  errors="surrogateescape")
                                   if path.is_file() else None)
            source = sources[target]
            if source is None:
                absent += 1           # absent subject is bd-anchorcheck's question
                continue
            is_dotless = not pathlib.PurePosixPath(target).suffix
            if not _is_python_subject(target, source):
                non_python += 1
                if is_dotless:
                    dotless_other.add(target)
                continue
            if is_dotless:
                dotless_python.add(target)
            verdict = classify(mutant, source)
            if verdict == "UNRESOLVED":
                unresolved += 1       # resolution is row 357's / bd-anchorcheck's
                continue
            if is_dotless:
                dotless += 1
            else:
                dotted += 1
            if verdict != "CODE":
                offenders.append(
                    f"{rel_spec}::{mutant.get('label', '<unlabelled>')} -> {target} "
                    f"({verdict})")
    return Survey(len(specs), total, no_target, absent, non_python, unresolved,
                  dotted, dotless, offenders, dotless_python, dotless_other)


def _corpus_entry_count(spec_dir: pathlib.Path) -> int:
    """An INDEPENDENT denominator: the corpus size, read without the classifier.

    Deliberately re-derived here rather than taken from the artifact under test,
    so the reconciliation below compares two separate readings of the corpus.
    """
    return sum(len(_mutants(json.loads(p.read_text(encoding="utf-8"))))
               for p in sorted(spec_dir.glob("*.json")))


def test_the_survey_partitions_the_whole_mutant_corpus():
    """PRECONDITION, asserted before any verdict: nothing silently leaves.

    This is the assertion the retired `examined >= 500` floor could not make.
    A floor goes quiet when the denominator collapses; a partition names it.
    """
    survey = _survey()
    independent_total = _corpus_entry_count(_SPEC_DIR)
    assert survey.specs > 0 and independent_total > 0, (
        "zero mutant specs or zero mutant entries -- UNKNOWN, never a clean "
        "population")
    assert survey.total == independent_total, (
        f"the survey walked {survey.total} entries but the corpus holds "
        f"{independent_total}")
    assert survey.accounted == survey.total, (
        f"{survey.total - survey.accounted} of {survey.total} mutant entries "
        f"fell out of every bucket: {survey}")
    assert survey.no_target == 0 and survey.absent == 0, (
        f"{survey.no_target} fileless and {survey.absent} absent-subject "
        "mutant entries -- a subject that vanished is bd-anchorcheck's to fix, "
        "but it may not silently shrink this gate's denominator")
    assert survey.unresolved == 0, (
        f"{survey.unresolved} python-subject anchors did not resolve; "
        "exactly-once resolution belongs to "
        "tests/test_row357_mutant_anchors_are_not_fragile.py and "
        "bd-anchorcheck, and this gate names the drop rather than skipping it")


def test_the_survey_covers_extensionless_python_subjects():
    """Rows 567/568. The denominator must be bd-mutate's, not a .py glob."""
    survey = _survey()
    assert survey.examined_dotted > 0, "no .py-suffixed anchors examined"
    assert survey.examined_dotless > 0, (
        "ZERO extensionless python subjects examined. bd-mutate applies "
        "anchors to toolchain/bin/bd-* scripts by shebang, so a suffix-only "
        "predicate leaves every one of them outside this ratchet: a "
        "comment-only mutant there is applied, changes nothing, and is graded "
        "CAUGHT")
    assert survey.dotless_python_subjects, (
        "no extensionless subject was classified Python at all")
    assert survey.dotless_other_subjects <= _DOTLESS_NON_PYTHON_SUBJECTS, (
        "extensionless subject(s) this gate declines are not in the pinned "
        "closed set, so the Python predicate has collapsed: "
        + ", ".join(sorted(survey.dotless_other_subjects
                           - _DOTLESS_NON_PYTHON_SUBJECTS)))
    for target in sorted(survey.dotless_other_subjects):
        text = (_REPO / target).read_text(encoding="utf-8",
                                          errors="surrogateescape")
        assert "python" not in _first_line(text), (
            f"{target} is declined although its shebang names python: "
            f"{_first_line(text)!r}")


def test_every_python_mutant_anchor_resolves_into_executable_source():
    survey = _survey()
    assert survey.examined > 0, (
        "zero python-subject anchors were examined; a collapsed denominator "
        "must never read as a clean population")
    assert not survey.offenders, (
        "mutant anchor(s) resolve only into a COMMENT, so bd-mutate would edit "
        "prose, leave the subject's behaviour untouched, and record a caught "
        "regression it never caused:\n  " + "\n  ".join(survey.offenders))


# ── row 572: the offset table must be the TOKENIZER's lines ──────────────────
#
# str.splitlines() breaks on \v \f \x1c \x1d \x1e \x85 \u2028 \u2029 as
# \n; io.StringIO(source).readline, which is what feeds tokenize, breaks only on
# \n. One extra break shifts every later index by one entry, and the comment
# span slides backwards by the length of the line it skipped.
# MEASURED ON THIS INTERPRETER, because "the separators str.splitlines breaks
# on" is a claim about str.splitlines and NOT about what a Python subject can
# actually contain. There are NINE beyond "\n", not the eight the obvious list
# gives: a LONE "\r" is the ninth and the easiest to drop, because "\r\n" -- the
# form everyone pictures -- is the one sequence both readers agree on and so is
# NOT a member. Dropping it would have been this cut's own defect committed
# inside this cut's own fix.
#
#   inverting     the file still tokenizes, so a verdict can be INVERTED
#   untokenizable CPython refuses the file, which this gate already calls
#                 UNRESOLVED -- a refusal, not a silent pass
#
# Both halves are named rather than one being dropped: a separator that moves
# between them on an interpreter upgrade fails the test that owns it instead of
# quietly leaving the denominator.
_INVERTING_SEPARATORS = ["\r", "\x0c", "\x85", "\u2028", "\u2029"]
_UNTOKENIZABLE_SEPARATORS = ["\x0b", "\x1c", "\x1d", "\x1e"]
_SPLITLINES_SEPARATORS = _INVERTING_SEPARATORS + _UNTOKENIZABLE_SEPARATORS
_AGREED_SEPARATORS = ["\n", "\r\n"]


def _inversion_source(separator: str) -> str:
    """A subject where `separator` gives str.splitlines exactly ONE break that
    the tokenizer's own reader does not have.

    The separator opens its own splitlines-line and is deliberately NOT
    followed by a newline: appending one would turn a lone CR into CR LF, which
    both readers agree on, and the fixture would silently stop exercising the
    defect for exactly the member most likely to be forgotten. Every caller
    asserts the extra break exists before reading any verdict.
    """
    return ("X = 1\n" + separator
            + "MARKER_LINE_PADDING = 8\n# note RETIRED = 235\n")


def _reader_line_count(source: str) -> int:
    """Lines as tokenize sees them, counted through the SAME reader it uses."""
    return len(list(iter(io.StringIO(source).readline, "")))


def _assert_one_extra_break(source: str, separator: str) -> None:
    extra = len(source.splitlines()) - _reader_line_count(source)
    assert extra == 1, (
        f"{separator!r} gives str.splitlines {extra} extra break(s) over the "
        "tokenizer's reader, not 1, so this fixture cannot exercise the defect")


@pytest.mark.parametrize("separator", _INVERTING_SEPARATORS)
def test_an_exotic_line_separator_does_not_invert_code_and_comment(separator):
    """Row 572, both directions. The fail-open half is the second assertion."""
    source = _inversion_source(separator)
    # PRECONDITIONS: the fixture really does build the shape under test.
    _assert_one_extra_break(source, separator)
    assert _comment_spans(source) is not None, (
        f"{separator!r} makes the fixture untokenizable, so the verdicts below "
        "would be UNRESOLVED for the wrong reason")
    assert source.count("MARKER_LINE_PADDING = 8") == 1
    assert source.count("RETIRED = 235") == 1
    comment_start = source.index("# note RETIRED = 235")
    assert _comment_spans(source) == [
        (comment_start, comment_start + len("# note RETIRED = 235"))], (
        "the comment span does not match the comment's true offsets")

    assert classify({"old": "MARKER_LINE_PADDING = 8"}, source) == "CODE", (
        "a real assignment was reported as prose, so a genuine mutant would be "
        "refused")
    assert classify({"old": "RETIRED = 235"}, source) == "COMMENT_ONLY", (
        "a comment-only anchor was reported CODE -- the fail-open half: "
        "bd-mutate would edit prose and grade the result CAUGHT")


@pytest.mark.parametrize("separator", _UNTOKENIZABLE_SEPARATORS)
def test_a_separator_cpython_refuses_is_UNRESOLVED_not_clean(separator):
    """The other half of the nine, kept in the denominator on purpose.

    These four do make str.splitlines disagree with the tokenizer, so a naive
    reading would list them alongside the five above -- but CPython refuses the
    file, and this gate must say UNRESOLVED rather than reporting a verdict it
    cannot support. If a future interpreter starts accepting one, this test
    fails and the separator moves to _INVERTING_SEPARATORS with evidence."""
    source = _inversion_source(separator)
    _assert_one_extra_break(source, separator)
    assert _comment_spans(source) is None, (
        f"{separator!r} now tokenizes; move it to _INVERTING_SEPARATORS")
    assert classify({"old": "RETIRED = 235"}, source) == "UNRESOLVED"


def test_the_two_separator_halves_are_the_complete_splitlines_population():
    """DENOMINATOR. Nine separators beyond "\n", partitioned, none dropped.

    The membership test is DERIVED per member -- str.splitlines breaks on it and
    the tokenizer's reader does not -- rather than asserted against a count I
    typed, so a member cannot be listed here without earning its place.
    """
    assert len(_SPLITLINES_SEPARATORS) == len(set(_SPLITLINES_SEPARATORS)) == 9
    assert not set(_INVERTING_SEPARATORS) & set(_UNTOKENIZABLE_SEPARATORS)
    for separator in _SPLITLINES_SEPARATORS:
        probe = "a" + separator + "b"
        assert len(probe.splitlines()) == 2, (
            f"{separator!r} is not a str.splitlines separator at all")
        assert _reader_line_count(probe) == 1, (
            f"{separator!r} is honoured by the tokenizer's reader too, so it "
            "cannot misalign the offset table and does not belong here")
    for separator in _AGREED_SEPARATORS:
        probe = "a" + separator + "b"
        assert len(probe.splitlines()) == _reader_line_count(probe) == 2, (
            f"{separator!r} is NOT agreed on by both readers after all; it "
            "belongs in _SPLITLINES_SEPARATORS")


def test_the_offset_table_is_unchanged_for_ordinary_source():
    """NEGATIVE CONTROL for the offset fix: it must not move ordinary files.

    A subject with no exotic separator must produce exactly the spans a plain
    reading gives, so the correction cannot be a licence to shift anything.
    """
    source = "a = 1\n# one\nb = 2  # two\n"
    assert _comment_spans(source) == [
        (source.index("# one"), source.index("# one") + len("# one")),
        (source.index("# two"), source.index("# two") + len("# two")),
    ]
    assert classify({"old": "b = 2"}, source) == "CODE"
    assert classify({"old": "# two"}, source) == "COMMENT_ONLY"


# ── negative controls: the widened population is CLASSIFIED, not waved through ─


def _write_fixture(tmp_path: pathlib.Path, subject: str, shebang: str,
                   body: str, mutants: list[dict]) -> tuple[pathlib.Path,
                                                            pathlib.Path]:
    repo = tmp_path / "repo"
    target = repo / subject
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(shebang + "\n" + body, encoding="utf-8")
    spec_dir = repo / "tests" / "mutants"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "fixture.json").write_text(
        json.dumps({"mutants": mutants}), encoding="utf-8")
    return repo, spec_dir


def test_a_comment_anchor_on_an_extensionless_script_is_REFUSED(tmp_path):
    """NEGATIVE CONTROL for rows 567/568. Without this, the green verdict on the
    live tree would only prove the population got bigger, not that it is judged.
    """
    subject = "toolchain/bin/bd-fixture"
    repo, spec_dir = _write_fixture(
        tmp_path, subject, "#!/usr/bin/env python3",
        "# the retired constant was RETIRED_CONST = 235\n"
        "LIVE_CONST = 235\n",
        [{"file": subject, "label": "M1 comment anchor",
          "old": "RETIRED_CONST = 235"},
         {"file": subject, "label": "M2 code anchor", "old": "LIVE_CONST = 235"}])

    # PRECONDITIONS asserted before the verdict.
    text = (repo / subject).read_text(encoding="utf-8")
    assert (repo / subject).is_file() and not pathlib.PurePosixPath(subject).suffix
    assert _first_line(text) == "#!/usr/bin/env python3"
    assert _is_python_subject(subject, text), (
        "the fixture subject is not even recognised as Python, so the survey "
        "below would be measuring the wrong thing")
    assert text.count("RETIRED_CONST = 235") == 1
    assert classify({"old": "RETIRED_CONST = 235"}, text) == "COMMENT_ONLY"

    survey = _survey(repo=repo, spec_dir=spec_dir)
    assert survey.total == 2 and survey.accounted == 2
    assert survey.examined_dotless == 2 and survey.examined_dotted == 0
    assert survey.non_python == 0 and survey.unresolved == 0
    assert len(survey.offenders) == 1, survey.offenders
    assert "M1 comment anchor" in survey.offenders[0]
    assert "(COMMENT_ONLY)" in survey.offenders[0]
    assert subject in survey.offenders[0]


def test_a_bash_extensionless_script_is_not_swept_in(tmp_path):
    """NEGATIVE CONTROL, the other direction: widening by shebang must not
    widen to every extensionless file. A bash tool is not tokenizable Python and
    must be DECLINED by kind, not laundered into UNRESOLVED."""
    subject = "toolchain/bin/bd-shell-fixture"
    repo, spec_dir = _write_fixture(
        tmp_path, subject, "#!/bin/bash",
        "# RETIRED_CONST=235\nLIVE_CONST=235\n",
        [{"file": subject, "label": "M1", "old": "RETIRED_CONST=235"}])

    text = (repo / subject).read_text(encoding="utf-8")
    assert _first_line(text) == "#!/bin/bash"
    assert not _is_python_subject(subject, text)

    survey = _survey(repo=repo, spec_dir=spec_dir)
    assert survey.total == 1 and survey.accounted == 1
    assert survey.non_python == 1
    assert survey.examined == 0 and survey.unresolved == 0
    assert survey.dotless_other_subjects == {subject}
    assert survey.offenders == []


def test_an_absent_or_fileless_subject_is_counted_not_dropped(tmp_path):
    """A subject this gate cannot read must land in a NAMED bucket. The old
    survey `continue`d on both, so either could shrink the denominator with no
    reader ever seeing it."""
    subject = "toolchain/bin/bd-fixture"
    repo, spec_dir = _write_fixture(
        tmp_path, subject, "#!/usr/bin/env python3", "LIVE_CONST = 235\n",
        [{"file": subject, "label": "M1", "old": "LIVE_CONST = 235"},
         {"file": "toolchain/bin/bd-not-here", "label": "M2", "old": "x = 1"},
         {"label": "M3 no file at all", "old": "x = 1"},
         {"file": subject, "label": "M4 unresolvable", "old": "NOT_PRESENT"}])

    survey = _survey(repo=repo, spec_dir=spec_dir)
    assert survey.total == 4 and survey.accounted == 4
    assert survey.examined_dotless == 1
    assert survey.absent == 1
    assert survey.no_target == 1
    assert survey.unresolved == 1
    assert survey.offenders == []


def test_the_classifier_refuses_a_comment_anchor():
    """NEGATIVE CONTROL. Without this the green verdict above proves nothing."""
    source = (
        "# the retired constant was _EXPECTED_DECLARED_GATE_COUNT = 235\n"
        "_DECLARED_GATE_FLOOR = 235\n"
    )
    assert classify({"old": "_EXPECTED_DECLARED_GATE_COUNT = 235"}, source) == "COMMENT_ONLY"
    assert classify({"old_regex": r"_EXPECTED_DECLARED_GATE_COUNT = [0-9]+"},
                    source) == "COMMENT_ONLY"


def test_the_classifier_accepts_a_real_code_anchor():
    """POSITIVE CONTROL, in the same file, so a classifier that refused
    everything could not pass for a strict one."""
    source = "# a comment mentioning MARKER = 1\nMARKER = 1\n"
    assert classify({"old": "MARKER = 1"}, source) == "PARTLY_COMMENT"
    assert classify({"old": "\nMARKER = 1"}, source) == "CODE"
    assert classify({"old": "_DECLARED_GATE_FLOOR = 235"},
                    "_DECLARED_GATE_FLOOR = 235\n") == "CODE"


def test_a_string_literal_is_code_and_not_prose():
    """A docstring or string constant is executable source. Treating it as prose
    would have made this rule reject seven legitimate anchors that mutate real
    string values -- measured before the rule was narrowed to comments."""
    source = 'STATUS = "crashed"\n\n\ndef f():\n    """MARKER = 1 in a docstring."""\n'
    assert classify({"old": 'STATUS = "crashed"'}, source) == "CODE"
    assert classify({"old": "MARKER = 1 in a docstring"}, source) == "CODE"


def test_an_untokenizable_subject_is_not_silently_clean():
    assert _comment_spans("def f(:\n") is None
    assert classify({"old": "anything"}, "def f(:\n") == "UNRESOLVED"


@pytest.mark.parametrize("mutant", [
    {"new": "x"},                              # neither old nor old_regex
    {"old_regex": "([unclosed"},               # invalid regex
])
def test_a_mutant_this_gate_cannot_read_is_left_to_its_owner(mutant):
    """UNRESOLVED, never CODE. Anchor resolution belongs to
    tests/test_row357 and bd-anchorcheck; this gate must not launder a mutant it
    could not classify into a pass."""
    assert classify(mutant, "MARKER = 1\n") == "UNRESOLVED"
