"""The CSRF diagnostic (capture.sh step [3]) must never print a cookie VALUE.

THE LEAK, measured 2026-07-30 from a real capture bundle
(bd_capture/03_csrf_diag.log):

    Set-Cookie headers: 1
      bd_session=<43-char value redacted here on purpose>; Expires=...;
      Max-Age=28800; HttpOnly;

capture.sh:1005 tars the whole of $OUT and the operator ships that bundle to
third parties, and bd_session is "the CSRF-bound auth session" (app.py:1074):
possessing the value lets the holder derive the expected CSRF token
(app.py:751, _csrf_token_for(sess)) and replays as the operator in the audit log
(app_captures.py:105). So the value is a live credential, and the diagnostic
printed 120 characters of it.

The diagnostic FACTS are worth keeping: a session was minted, how many headers,
which flags, what TTL. The value is not one of them. This mirrors the capture
vault rule already in the codebase -- status recorded, response body never.

WHY THIS TEST RUNS THE REAL BLOCK. A source scan for "h[:120] is absent" is the
presence-not-behaviour class that has survived mutation five times in this
programme. Instead this extracts capture.sh's actual step-[3] program and runs
it against a fake app whose GET / sets a cookie with a KNOWN secret value, then
asserts the secret never reaches stdout while the count, length and flags do.
On pristine source the loop is `print(' ', h[:120])`, which emits the secret --
so every value assertion below fails RED before the fix.
"""
from __future__ import annotations

import io
import re
import sys
import types
from contextlib import redirect_stdout
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CAPTURE_SH = REPO_ROOT / "capture.sh"

# A deliberately SYNTHETIC, low-entropy stand-in -- never a real token. Built
# from readable fragments for two reasons: a test about not shipping a credential
# must not commit one into permanent git history (the real leaked value would
# trip gitleaks, rightly); and the writer redacts by STRUCTURE (partition on
# '='), not by entropy, so a fake value exercises it identically. It is long
# enough to survive the pristine code's h[:120] truncation and distinctive
# enough that its appearance in output is unambiguous.
_SECRET = "EXAMPLE-fake-bd-session-do-not-use-" + "x" * 12
_COOKIE = (f"bd_session={_SECRET}; Expires=Thu, 30 Jul 2026 11:25:51 GMT; "
           f"Max-Age=28800; HttpOnly")


def _extract_diagnostic_program() -> str:
    """The exact Python that capture.sh step [3] feeds to `python -c`.

    Undoes the two bash-double-quote escapes (\\" -> " and \\$ -> $) so the
    result is the program the interpreter actually sees.
    """
    src = CAPTURE_SH.read_text(encoding="utf-8")
    m = re.search(
        r'venv/bin/python -c "\n(.*?)\n" > "\$OUT/03_csrf_diag\.log"',
        src, re.S)
    assert m, "could not locate the step [3] `python -c` block in capture.sh"
    code = m.group(1).replace('\\"', '"').replace('\\$', '$')
    assert "getlist('Set-Cookie')" in code, (
        "the extracted block does not read Set-Cookie headers; the anchor moved")
    return code


class _FakeResponse:
    status_code = 200
    data = b"<html><body>spa shell, no meta tag</body></html>"

    class _Headers:
        @staticmethod
        def getlist(name):
            return [_COOKIE] if name == "Set-Cookie" else []

    headers = _Headers()


class _FakeClient:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, path):
        return _FakeResponse()


def _run_diagnostic() -> str:
    """Exec the extracted program with a fake bulk_downloader.app injected,
    capturing everything it prints."""
    program = _extract_diagnostic_program()

    fake_app_mod = types.ModuleType("bulk_downloader.app")
    fake_app_mod.app = types.SimpleNamespace(test_client=lambda: _FakeClient())
    fake_pkg = sys.modules.get("bulk_downloader")
    saved_app = sys.modules.get("bulk_downloader.app")
    created_pkg = False
    if fake_pkg is None:
        fake_pkg = types.ModuleType("bulk_downloader")
        fake_pkg.__path__ = []  # mark as a package
        sys.modules["bulk_downloader"] = fake_pkg
        created_pkg = True
    sys.modules["bulk_downloader.app"] = fake_app_mod
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            exec(compile(program, "<capture.sh step3>", "exec"), {})
    finally:
        if saved_app is not None:
            sys.modules["bulk_downloader.app"] = saved_app
        else:
            sys.modules.pop("bulk_downloader.app", None)
        if created_pkg:
            sys.modules.pop("bulk_downloader", None)
    return buf.getvalue()


# ── the defect, at its narrowest ─────────────────────────────────────────────

def test_the_cookie_value_never_reaches_stdout():
    """THE LEAK. The secret is the value of bd_session; it must not appear in
    the diagnostic output in any form."""
    out = _run_diagnostic()
    assert _SECRET not in out, (
        "the CSRF diagnostic printed the bd_session cookie VALUE -- that log is "
        "tarred and shipped, and the value is a live auth credential:\n"
        + out)
    # Even a prefix of it is a leak: the pristine code truncated to 120 chars,
    # which is the whole 43-char value. Guard against a future `h[:8]`.
    assert _SECRET[:12] not in out, (
        "a prefix of the cookie value leaked -- truncation is not redaction:\n"
        + out)


def test_the_diagnostic_facts_survive():
    """Redaction that also deletes the signal is a different bug. The count, the
    value length, and the flag names must all still be reported."""
    out = _run_diagnostic()
    assert "Set-Cookie headers: 1" in out, (
        f"the header count is gone: {out!r}")
    assert f"value_len: {len(_SECRET)}" in out, (
        f"the value length (the redacted stand-in for the value) is gone or "
        f"wrong; expected {len(_SECRET)}:\n{out}")
    assert "HttpOnly" in out, f"the HttpOnly flag is gone: {out!r}"
    assert "Max-Age=28800" in out, (
        f"the Max-Age TTL -- a real diagnostic fact -- is gone: {out!r}")
    # the cookie NAME is a fact; the value is not
    assert "bd_session" in out, f"the cookie name is gone: {out!r}"


def test_an_unknown_attribute_value_is_omitted_not_echoed():
    """Never emit what the diagnostic does not control. A non-flag attribute
    (some future cookie option carrying data) must have its value omitted, not
    printed."""
    program = _extract_diagnostic_program()
    # Craft a cookie with a bespoke attribute carrying a secret-shaped value.
    marker = "FORBIDDEN-attr-value-must-not-leak"
    cookie = f"sid=abc; X-Custom={marker}; HttpOnly"

    class _R(_FakeResponse):
        class _Headers:
            @staticmethod
            def getlist(name):
                return [cookie] if name == "Set-Cookie" else []
        headers = _Headers()

    fake_app_mod = types.ModuleType("bulk_downloader.app")
    fake_app_mod.app = types.SimpleNamespace(
        test_client=lambda: type(
            "C", (), {"__enter__": lambda s: s, "__exit__": lambda s, *a: False,
                      "get": lambda s, p: _R()})())
    saved = sys.modules.get("bulk_downloader.app")
    saved_pkg = sys.modules.get("bulk_downloader")
    created = False
    if saved_pkg is None:
        pkg = types.ModuleType("bulk_downloader"); pkg.__path__ = []
        sys.modules["bulk_downloader"] = pkg
        created = True
    sys.modules["bulk_downloader.app"] = fake_app_mod
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            exec(compile(program, "<capture.sh step3>", "exec"), {})
    finally:
        if saved is not None:
            sys.modules["bulk_downloader.app"] = saved
        else:
            sys.modules.pop("bulk_downloader.app", None)
        if created:
            sys.modules.pop("bulk_downloader", None)
    out = buf.getvalue()
    assert marker not in out, (
        f"an unknown cookie attribute's value was echoed; only flag-attribute "
        f"values may be printed:\n{out}")
    assert "X-Custom" in out, (
        f"the unknown attribute's NAME should still be reported: {out!r}")


def test_the_program_still_parses_and_reads_the_root():
    """Guard the extraction itself: the block must be valid Python and must
    still probe GET / (a regression here would make step [3] inert)."""
    import ast
    program = _extract_diagnostic_program()
    ast.parse(program)   # raises on malformed
    assert "c.get('/')" in program, "step [3] no longer requests the SPA root"
