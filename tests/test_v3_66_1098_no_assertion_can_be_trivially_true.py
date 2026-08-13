"""No tracked test may carry an assertion that is true for every input.

A DECIDABLE SLICE OF BACKLOG 26. That row asks for a vacuous-test detector and
is filed as hard, correctly: "can this assertion fail?" is undecidable in
general. But a useful subset is not hard at all, and it has live hits --
`assert <anything> or True` is true for every input, and an AST walk finds it in
a few lines.

FOUND BY WRITING ONE. At v3.66.1095 this session shipped
`assert "aifc" not in sys.modules or True` into the file whose entire subject is
tests that cannot fail, and it survived review, a mutation battery, a 517-file
band, twelve CI checks and four captures. Nothing could have caught it, because
nothing was looking. A census then found four more across the suite, one of them
also carrying a clause (`"sec" not in [s.lower() for s in ("SEC",)]`) that is
always False -- so that assertion could not fail in one direction and could not
pass in the other.

WHAT THIS GATE DELIBERATELY DOES NOT CLAIM. It is not the vacuous-test detector
row 26 asks for and does not close it. It cannot see:
  - an assertion vacuous through a VARIABLE that happens to be truthy;
  - a test whose assertions are real but unreachable;
  - a test that asserts something true of every possible implementation.
It catches exactly one shape: an assert whose truth is decidable from the syntax
tree alone. That is a floor, and the row stays open above it.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

# Its subject is every tracked test file, so a new test file changes its
# denominator. That makes it axis-6 and repo-wide.
BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent


def _tracked_test_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--", "tests/test*.py"],
        cwd=str(_REPO), capture_output=True, text=True, check=True,
    ).stdout.split("\n")
    return [_REPO / p for p in out if p.strip()]


def _always_true(node: ast.expr) -> bool:
    """Is this expression true for every input, decidably, from syntax alone?

    Deliberately narrow. A NAME is not judged even if it looks constant, and a
    call is never judged -- guessing there is how a gate starts crying wolf,
    which CLAUDE.md section 0 counts as a soundness bug rather than a safe
    default.
    """
    if isinstance(node, ast.Constant):
        return bool(node.value)
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        # `x or True` is true regardless of x.
        return any(_always_true(v) for v in node.values)
    return False


def _vacuous_asserts(path: Path) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert) and _always_true(node.test):
            hits.append((node.lineno, ast.unparse(node.test)))
    return hits


def test_the_walk_sees_a_substantial_number_of_files():
    """Non-empty denominator, asserted before the verdict.

    A gate that scans nothing reports clean -- truthfully and uselessly -- and
    that is the failure CLAUDE.md section 0 is entirely about. This gate's
    subject is `git ls-files`, so a broken path or a cwd surprise would produce
    exactly that silence.
    """
    files = _tracked_test_files()
    assert len(files) > 500, (
        f"only {len(files)} tracked test files found; the walk is not seeing "
        "the suite, so a clean result below would mean nothing")


def test_the_detector_recognises_the_shape_it_hunts():
    """Positive control. Without this, a zero from the real scan is
    indistinguishable from a detector that matches nothing."""
    tree = ast.parse("assert x or True\n")
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.Assert))
    assert _always_true(node.test), "the detector cannot see `x or True`"

    tree = ast.parse("assert True\n")
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.Assert))
    assert _always_true(node.test), "the detector cannot see a bare `assert True`"


def test_the_detector_does_not_fire_on_real_assertions():
    """The over-sensitivity control. Every one of these must stay silent, or
    the gate is unusable and gets switched off."""
    for src in (
        "assert x",
        "assert x or y",
        "assert x, 'message'",
        "assert x or y, 'message'",
        "assert f() or g()",
        "assert x is not None",
        "assert 'a' in b",
        # A FALSE constant in an `or` does not make the whole thing true.
        "assert x or False",
        # An `and` with True still depends on x.
        "assert x and True",
    ):
        tree = ast.parse(src + "\n")
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.Assert))
        assert not _always_true(node.test), (
            f"the detector fired on a REAL assertion: {src!r}")


def test_no_tracked_test_carries_an_assertion_that_cannot_fail():
    offenders = []
    for path in _tracked_test_files():
        for lineno, text in _vacuous_asserts(path):
            rel = path.relative_to(_REPO)
            offenders.append(f"{rel}:{lineno}  assert {text}")

    assert not offenders, (
        "%d assertion(s) are true for every input, so they constrain nothing "
        "and pass on any implementation:\n  %s\n\n"
        "Remove the assertion, or make it real. If the property is not "
        "reliably true -- which is usually why the `or True` was added -- "
        "deleting it is the honest fix: a comment can say what was intended, "
        "while an assertion that cannot fail claims coverage it does not have."
        % (len(offenders), "\n  ".join(offenders))
    )
