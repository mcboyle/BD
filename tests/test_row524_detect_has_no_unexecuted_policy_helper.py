"""Row 524 -- a documented admission policy that nothing calls is not a policy.

``bulk_downloader/detect.py`` carried the module's most authoritative statement
of the learned-selector admission contract -- a six-line policy docstring --
inside a module-level private helper with ZERO call sites anywhere in the tree.
The live policy is a different function with different behaviour, and no test
named either one, so the suite could not distinguish "this documented policy is
correct" from "this documented policy never runs".  A reviewer or a later cut
could verify, amend or test the wrong function and see the suite stay green
either way.

This gate derives the helper population from the tracked tree by PARSING the
module, and derives the reference population from ``git ls-files`` rather than
from the module under test, so it cannot be satisfied by the artifact it
judges.  An empty helper population is UNKNOWN, never OK.
"""

BD_GATE_SCOPE = "module"

import ast
import io
import re
import subprocess
import tokenize
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SUBJECT = "bulk_downloader/detect.py"


class Unknown(Exception):
    """A required denominator could not be measured."""


def _module_private_helpers(source):
    """Module-level ``def _name`` identifiers, with their def line numbers."""
    tree = ast.parse(source)
    return {
        node.name: node.lineno
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("_")
    }


def _identifiers(text):
    """Executable identifiers only -- comments and string literals excluded."""
    try:
        return [
            token.string
            for token in tokenize.generate_tokens(io.StringIO(text).readline)
            if token.type == tokenize.NAME
        ]
    except Exception:
        stripped = "\n".join(
            line.split("#", 1)[0] for line in text.splitlines())
        return re.findall(r"[A-Za-z_]\w*", stripped)


def _orphan_helpers(subject_source, reference_texts):
    """Helpers whose only occurrence is their own ``def`` line.

    ``reference_texts`` maps repository-relative path to file text and MUST
    include the subject itself; both populations are asserted nonzero here so
    an unmeasurable input can never read OK.
    """
    helpers = _module_private_helpers(subject_source)
    if not helpers:
        raise Unknown("zero module-level private helpers parsed from subject")
    if not reference_texts:
        raise Unknown("zero reference files -- the denominator is empty")
    counts = dict.fromkeys(helpers, 0)
    for path, text in reference_texts.items():
        for name in _identifiers(text):
            if name in counts:
                counts[name] += 1
    # Each helper's own ``def NAME`` line contributes exactly one identifier.
    for name in helpers:
        counts[name] -= 1
    negative = sorted(name for name, count in counts.items() if count < 0)
    if negative:
        raise Unknown(f"reference count fell below its own def: {negative}")
    return sorted(name for name, count in counts.items() if count == 0)


def _tracked_reference_texts():
    out = subprocess.run(
        ["git", "-C", str(_REPO), "ls-files", "-z"],
        capture_output=True, text=True, check=True).stdout
    paths = [p for p in out.split("\0") if p]
    if not paths:
        raise Unknown("git ls-files returned nothing")
    texts = {}
    for rel in paths:
        if not (rel.endswith(".py") or rel.startswith("toolchain/bin/")):
            continue
        try:
            texts[rel] = (_REPO / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    if not texts:
        raise Unknown("no Python reference files resolved from git ls-files")
    return texts


def test_precondition_both_populations_are_nonzero_and_independent():
    texts = _tracked_reference_texts()
    assert len(texts) > 500, len(texts)
    assert _SUBJECT in texts, "the subject is not tracked"
    helpers = _module_private_helpers(texts[_SUBJECT])
    assert len(helpers) >= 15, sorted(helpers)
    # The reference denominator comes from git, not from the module.
    assert any(rel.startswith("tests/") for rel in texts)
    assert any(rel.startswith("toolchain/bin/") for rel in texts)


def test_no_module_level_private_helper_of_detect_is_unexecuted():
    """RED on the defective parent: exactly one helper has no call site."""
    texts = _tracked_reference_texts()
    orphans = _orphan_helpers(texts[_SUBJECT], texts)
    assert orphans == [], (
        f"{_SUBJECT} declares {len(orphans)} module-level private helper(s) "
        f"that nothing in the tracked tree references: {orphans}. A "
        "documented policy nothing executes cannot be verified by any test "
        "(row 524) -- give it a call site and a behavioural test, or delete "
        "it.")


def test_negative_control_a_planted_orphan_is_named():
    """The gate is not vacuously green."""
    texts = dict(_tracked_reference_texts())
    planted = "_row524_planted_orphan_helper"
    texts[_SUBJECT] = texts[_SUBJECT] + (
        f"\n\ndef {planted}():\n    return None\n")
    orphans = _orphan_helpers(texts[_SUBJECT], texts)
    assert orphans == [planted], orphans


def test_negative_control_a_referenced_helper_is_never_named():
    texts = _tracked_reference_texts()
    helpers = _module_private_helpers(texts[_SUBJECT])
    assert "_learned_candidate_requires_signal" in helpers
    orphans = _orphan_helpers(texts[_SUBJECT], texts)
    assert "_learned_candidate_requires_signal" not in orphans


def test_negative_control_an_empty_denominator_reads_unknown():
    texts = _tracked_reference_texts()
    with pytest.raises(Unknown):
        _orphan_helpers("x = 1\n", texts)
    with pytest.raises(Unknown):
        _orphan_helpers(texts[_SUBJECT], {})


def test_negative_control_comments_and_docstrings_are_not_references():
    subject = (
        "def _only_mentioned():\n"
        "    return 1\n"
        "\n"
        "def _real():\n"
        "    return 2\n"
        "\n"
        "USED = _real()\n"
    )
    mention = '"""_only_mentioned is discussed here."""\n# _only_mentioned\n'
    orphans = _orphan_helpers(
        subject, {_SUBJECT: subject, "tests/prose.py": mention})
    assert orphans == ["_only_mentioned"], orphans
