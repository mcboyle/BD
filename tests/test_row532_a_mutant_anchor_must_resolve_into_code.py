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

MEASURED BEFORE ADOPTING, because a ratchet on a dirty population is a ratchet
nobody can keep:

  536 python-subject anchors across the tracked specs
    0 resolve comment-only          <- the ratchet starts clean
    0 resolve partly in a comment

The broader rule -- "no comment may contain assignment-shaped text" -- was
measured and REJECTED: 446 occurrences across 226 tracked files, nearly all of
them ordinary prose like `nargs='+'` or `exit=1`. Narrow is what makes this
enforceable.

THIS IS A NEW RATCHET ON A CLEAN POPULATION, NOT A DEFECT FIX, and it has no
RED-first provenance against a defective base because there is no defect to
replay. What stands in for it is the negative control below: a synthetic spec
anchored on a comment must be REFUSED, so the check is proven capable of failing
before its green verdict on the live tree means anything.
"""
from __future__ import annotations

import io
import json
import pathlib
import re
import tokenize

import pytest

BD_GATE_SCOPE = "repo-wide"

_REPO = pathlib.Path(__file__).resolve().parents[1]
_SPEC_DIR = _REPO / "tests" / "mutants"


def _comment_spans(source: str) -> list[tuple[int, int]] | None:
    """Character spans of every COMMENT token, or None if the file cannot tokenize.

    A docstring or any other STRING is deliberately NOT included: a string
    literal is executable source, and mutating one is a real mutation. Only a
    comment is inert.
    """
    line_starts = [0]
    for line in source.splitlines(keepends=True):
        line_starts.append(line_starts[-1] + len(line))
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


def _survey() -> tuple[int, list[str]]:
    """(python-subject anchors examined, offending descriptions)."""
    assert _SPEC_DIR.is_dir(), f"no mutant spec directory at {_SPEC_DIR}"
    specs = sorted(_SPEC_DIR.glob("*.json"))
    assert specs, "zero mutant specs -- UNKNOWN, not a clean population"
    sources: dict[str, str | None] = {}
    examined = 0
    offenders: list[str] = []
    for spec_path in specs:
        rel_spec = str(spec_path.relative_to(_REPO))
        document = json.loads(spec_path.read_text(encoding="utf-8"))
        for mutant in _mutants(document):
            target = mutant.get("file")
            if not target or not target.endswith(".py"):
                continue                      # ci.yml, shell, JSON: not tokenizable here
            if target not in sources:
                path = _REPO / target
                sources[target] = (path.read_text(encoding="utf-8", errors="surrogateescape")
                                   if path.is_file() else None)
            source = sources[target]
            if source is None:
                continue                      # absent subject is bd-anchorcheck's question
            verdict = classify(mutant, source)
            if verdict == "UNRESOLVED":
                continue                      # resolution is bd-anchorcheck's question
            examined += 1
            if verdict != "CODE":
                offenders.append(
                    f"{rel_spec}::{mutant.get('label', '<unlabelled>')} -> {target} "
                    f"({verdict})")
    return examined, offenders


def test_every_python_mutant_anchor_resolves_into_executable_source():
    examined, offenders = _survey()
    assert examined > 0, (
        "zero python-subject anchors were examined; a collapsed denominator "
        "must never read as a clean population")
    assert examined >= 500, (
        f"only {examined} python-subject anchors examined, against 536 measured "
        "at row 532 -- the survey stopped seeing most of its subject")
    assert not offenders, (
        "mutant anchor(s) resolve only into a COMMENT, so bd-mutate would edit "
        "prose, leave the subject's behaviour untouched, and record a caught "
        "regression it never caused:\n  " + "\n  ".join(offenders))


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
