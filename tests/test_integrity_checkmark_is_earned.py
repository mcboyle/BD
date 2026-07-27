"""A verification checkmark must mean verification happened.

`bulk_downloader/integrity.py` fails OPEN when ffprobe is absent: it returns
`(True, "ffprobe not installed")`. That is a deliberate, documented product
decision -- BD would rather download than refuse to run -- and it is pinned by
`test_returns_ok_when_ffprobe_missing`. This file does not touch it.

The defect is downstream. `runner_integrity` discarded the reason on the ok
path (`return True, False, ""`), and `runner_transport` then rendered an
unconditional " OK" marker. So on a host without ffprobe every completed
download was reported to the operator as integrity-verified when nothing had
been verified.

Fail-open is a choice about whether to PROCEED. It is not a licence to report
the check as having passed. Those are different claims, and only the first one
was ever decided.

Measured on this container (ffprobe absent):
    integrity.verify_media_integrity("...") -> (True, 'ffprobe not installed')
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_INTEGRITY = REPO_ROOT / "bulk_downloader" / "runner_integrity.py"
RUNNER_TRANSPORT = REPO_ROOT / "bulk_downloader" / "runner_transport.py"

CHECKMARK = "✓"


def test_integrity_helper_propagates_the_reason_on_the_ok_path():
    """`return True, False, ""` throws away the only evidence of a no-op.

    Derived from the AST rather than by grepping the text: a literal empty
    string in a return tuple is the subject, and a grep for `""` would match
    hundreds of unrelated sites.
    """
    tree = ast.parse(RUNNER_INTEGRITY.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Tuple):
            continue
        elts = node.value.elts
        if len(elts) != 3:
            continue
        first, third = elts[0], elts[2]
        is_ok_true = isinstance(first, ast.Constant) and first.value is True
        is_empty_reason = isinstance(third, ast.Constant) and third.value == ""
        if is_ok_true and is_empty_reason:
            offenders.append(node.lineno)
    assert not offenders, (
        "runner_integrity returns (True, ..., \"\") at line(s) %s, discarding "
        "the reason. When ffprobe is absent the reason is "
        "'ffprobe not installed' -- the one fact the caller needs in order to "
        "avoid claiming the file was verified." % offenders
    )


def test_the_checkmark_is_conditional_on_an_empty_reason():
    """The marker must be earned, not assigned unconditionally.

    Asserts on the assignment's position in the AST: a checkmark assigned in
    the body of an `if` that tests the reason is earned; one assigned straight
    after the ok-check is not. A substring search for the character cannot tell
    those apart -- it matches either way.
    """
    source = RUNNER_TRANSPORT.read_text(encoding="utf-8")
    assert CHECKMARK in source, (
        "no checkmark found in runner_transport -- anchor stale, this gate can "
        "no longer see its subject"
    )
    tree = ast.parse(source)

    unconditional = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        val = node.value
        if not (isinstance(val, ast.Constant) and isinstance(val.value, str)):
            continue
        if CHECKMARK not in val.value:
            continue
        # Find the nearest enclosing If and check whether it tests `reason`.
        guarded = False
        for parent in ast.walk(tree):
            if not isinstance(parent, ast.If):
                continue
            if node not in ast.walk(parent):
                continue
            test_src = ast.dump(parent.test)
            if "reason" in test_src:
                guarded = True
                break
        if not guarded:
            unconditional.append(node.lineno)

    assert not unconditional, (
        "the verification checkmark is assigned at line(s) %s without testing "
        "the reason. On a host with no ffprobe, integrity.verify_media_integrity "
        "returns (True, 'ffprobe not installed') -- so every download is "
        "reported to the operator as verified when nothing was checked."
        % unconditional
    )


def test_integrity_contract_itself_is_untouched():
    """Guard the boundary of this cut.

    The fail-open return is a product decision pinned by an existing test. This
    change is call-sites-only, and this asserts it stayed that way -- a fix that
    quietly widened its own scope is how an authorised change becomes an
    unauthorised one.
    """
    from bulk_downloader import integrity

    ok, reason = integrity.verify_media_integrity("/tmp/nonexistent_for_test_xyz.mp4")
    if integrity._ffprobe() is None:
        assert ok is True, (
            "integrity.verify_media_integrity no longer fails open when ffprobe "
            "is absent. That contract is deliberate and pinned elsewhere; this "
            "cut was authorised for call sites only."
        )
        assert reason, "fail-open must still explain itself"
