"""diag_d2's body_sha256 field must be treated as STRUCTURAL, behaviourally.

v3.66.824 promoted `body_sha256` from the (now-empty) cosmetic list to the
structural list in `tools/diag_d2_fresh_bd_home.py::_diff_probes` -- the Jinja
shell that used to inject a random per-request CSRF token into the body (the
reason a body-hash difference was once EXPECTED and filed as cosmetic) was
deleted at v3.66.334, so `/` is now the installer 503 or a static file and two
probes with a genuinely different body hash are a real D2 asymmetry, not noise.

A mutation (M4) that moves `body_sha256` back out of `structural` is not
caught by anything that scans the source for the literal string
`"body_sha256"` in a list -- that string would still be present in a comment,
a docstring, or the dict-building code above `_diff_probes`, so a presence
check is satisfied either way. This is exactly the presence-not-behaviour
class `tests/test_capture_csrf_diag_redacts_cookies.py` documents (see its
module docstring and the note above `test_diag_d2_collector_never_records_the_
cookie_value`): a source scan cannot tell "the literal occurs" from "the
literal does the job".

So this test is BEHAVIOURAL: it loads the real `_diff_probes` function (plain
module-level code -- verified by reading the file; no embedded-string slicing
needed, unlike the cookie test's subprocess payload) and drives it with two
well-formed probe dicts that are IDENTICAL except for `body_sha256`. On the
fixed form, `_diff_probes` must print "STRUCTURAL DIFF" -- because a body hash
difference with everything else equal is exactly the D2 asymmetry the tool
exists to surface. If `body_sha256` is ever demoted back to cosmetic (M4),
`_diff_probes` reports "No structural difference" instead, and this test
fails.
"""
from __future__ import annotations

import importlib.util
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DIAG_D2 = REPO_ROOT / "tools" / "diag_d2_fresh_bd_home.py"


def _load_diag_d2():
    """Load tools/diag_d2_fresh_bd_home.py as a real module.

    `tools/` is not a package (no `__init__.py`), so this uses the same
    `importlib.util.spec_from_file_location` pattern already used elsewhere
    for `tools/` modules (e.g. tests/test_build_session_pack.py), not a plain
    `import`. `_diff_probes` is plain module-level code -- confirmed by
    reading the file: it is a top-level `def`, not embedded inside a string
    literal the way the subprocess payload in the same file is -- so no AST
    slicing of a string constant is needed to reach it, unlike the sibling
    cookie test which must slice a string because ITS subject lives inside
    `_SUBPROCESS_SCRIPT`.
    """
    spec = importlib.util.spec_from_file_location(
        "diag_d2_fresh_bd_home", DIAG_D2)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _probe(body_sha256: str) -> dict:
    """A well-formed probe dict covering every field `_diff_probes` reads."""
    return {
        "ok": True,
        "status": 200,
        "body_len": 512,
        "body_sha256": body_sha256,
        "set_cookies_count": 0,
        "set_cookies_have_bd_session": False,
        "pkg_file": "/repo/bulk_downloader/__init__.py",
        "app_file": "/repo/bulk_downloader/app.py",
        "head_snippet": "<html></html>",
    }


def test_body_sha256_only_difference_is_reported_as_structural():
    """Two probes identical except body_sha256 must yield a STRUCTURAL DIFF.

    This is the whole point of the promotion: on pristine source, a body hash
    mismatch with every other field equal must not be filed as "no structural
    difference" -- that is precisely the D2 asymmetry this diagnostic exists
    to catch.
    """
    mod = _load_diag_d2()
    a = _probe("a" * 64)
    b = _probe("b" * 64)

    buf = StringIO()
    with redirect_stdout(buf):
        mod._diff_probes(a, b)
    out = buf.getvalue()

    assert "STRUCTURAL DIFF" in out, (
        "a body_sha256-only difference between two otherwise-identical probes "
        "was not reported as a structural diff -- body_sha256 is not being "
        "treated as structural:\n" + out)
    assert "body_sha256" in out, (
        "the structural diff output did not name body_sha256 as the "
        "differing field:\n" + out)


def test_identical_probes_report_no_structural_difference():
    """Sanity check on the other side: truly identical probes must not
    falsely report a structural diff (a gate that fires on identity is a
    soundness bug per CLAUDE.md section 0)."""
    mod = _load_diag_d2()
    a = _probe("c" * 64)
    b = _probe("c" * 64)

    buf = StringIO()
    with redirect_stdout(buf):
        mod._diff_probes(a, b)
    out = buf.getvalue()

    assert "No structural difference" in out, (
        "two byte-identical probes were reported as structurally different:\n"
        + out)
