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

import ast
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


# ── the denominator ─────────────────────────────────────────────────────────
#
# The gate above examined capture.sh and nothing else, so it certified "the
# CSRF diagnostic never prints a cookie value" while a SECOND CSRF diagnostic,
# tools/diag_csrf_bootstrap.py, printed `c[:120]` of every Set-Cookie header,
# and tools/adversarial_probe.py emitted `victim_sess[:30]` of a real minted
# session. Both are the leak four tests removed from capture.sh, alive in
# siblings the gate could not see.
#
# The population is DERIVED. Each member is then REVIEWED explicitly: a general
# taint analysis over these files was tried first and rejected -- it fired on
# fuzz_probe.py's attack-payload strings and on diag_d2's count-and-boolean
# summary, and over-sensitivity is a soundness bug in its own right. A registry
# fails closed on anything NEW without inventing verdicts about what it cannot
# analyse.


def _cookie_reading_sources() -> list[Path]:
    """Every tools/ source that mentions a Set-Cookie header.

    The predicate is NOT "and contains the literal print": adversarial_probe.py
    emits through a reporter object, and requiring `print` excluded the very
    file whose leak this test exists to catch.
    """
    return [p for p in sorted((REPO_ROOT / "tools").rglob("*.py"))
            if "__pycache__" not in p.parts
            and "set-cookie" in p.read_text(encoding="utf-8", errors="replace").lower()]


# Why each member is safe. A new file reading Set-Cookie fails this gate until
# someone looks at it and records a reason here.
REVIEWED_COOKIE_READERS = {
    "diag_csrf_bootstrap.py":
        "redacts: prints cookie NAME, value_len and flag attrs (see the "
        "behavioural test below); never the value",
    "adversarial_probe.py":
        "redacts: reports whether the server minted a fresh id as a BOOLEAN "
        "comparison, never the session value",
    "diag_d2_fresh_bd_home.py":
        "records only len(set_cookies) and a bd_session-present boolean; no "
        "value reaches output",
    "capture_scrub.py":
        "the redactor itself -- 'set-cookie' appears in its header regex",
    "scrub_recon.py":
        "docstring only: names cookie carriers as a class",
    "fuzz_probe.py":
        "attack INPUT payloads ('test\\nSet-Cookie: evil=1'); it sends these, "
        "it does not receive or print a cookie",
}


def _unreviewed(found: set[str], reviewed) -> list[str]:
    """The registry's actual predicate, factored out so it can be TESTED.

    Inline, this logic could be neutered to `unreviewed = []` and every test
    here would still pass -- an unfailable gate. test_the_review_gate_can_fail
    below drives it with a synthetic population so the gate itself is exercised.
    """
    return sorted(found - set(reviewed))
    # KNOWN LIMIT, measured by mutation: neutering this predicate to `return []`
    # IS caught (test_the_review_gate_can_fail drives it with a synthetic
    # population). Substituting the CALLER's argument -- _unreviewed(set(), ...)
    # -- is not, because that mutates the test's own wiring rather than the
    # subject, and no test can be its own meta-test. Stated rather than papered
    # over.


def test_the_review_gate_can_fail():
    """The registry check must be able to fail, or it certifies nothing."""
    assert _unreviewed({"a.py", "b.py"}, {"a.py": "reason"}) == ["b.py"], (
        "the review gate did not flag an unreviewed file; a gate that cannot "
        "fail is worse than no gate")
    assert _unreviewed({"a.py"}, {"a.py": "reason"}) == []


def test_every_cookie_reading_tool_has_been_reviewed():
    """The denominator must contain the subject, and must stay that way.

    This is what makes a THIRD leaking diagnostic impossible to add silently:
    the population is derived from source, so a new file appears here on the day
    it lands and fails until reviewed.
    """
    found = {p.name for p in _cookie_reading_sources()}
    assert found, (
        "no Set-Cookie-reading source found under tools/ -- the derivation is "
        "broken, and a check that cannot see its subject reports OK")
    unreviewed = _unreviewed(found, REVIEWED_COOKIE_READERS)
    assert not unreviewed, (
        "these tools/ files read a Set-Cookie header and have never been "
        "reviewed for whether they emit its VALUE. Read each one, then record "
        "why it is safe in REVIEWED_COOKIE_READERS: " + ", ".join(unreviewed))
    stale = sorted(set(REVIEWED_COOKIE_READERS) - found)
    assert not stale, (
        "REVIEWED_COOKIE_READERS names files that no longer read Set-Cookie; a "
        "registry describing files that are gone is stale authority: "
        + ", ".join(stale))


def _run_bootstrap_cookie_block(cookie: str) -> str:
    """Exec tools/diag_csrf_bootstrap.py's cookie-reporting block in isolation.

    Behavioural, like the capture.sh harness above, and for the same stated
    reason: a source scan for "c[:120] is absent" is the presence-not-behaviour
    class this file's docstring already rejects. The block is extracted rather
    than the whole script because the script imports bulk_downloader and calls
    db_init() -- running that would test the app, not the redaction.
    """
    src = (REPO_ROOT / "tools" / "diag_csrf_bootstrap.py").read_text(encoding="utf-8")
    m = re.search(r"^set_cookies = .*?(?=\n# 4\.)", src, re.S | re.M)
    assert m, ("could not locate the Set-Cookie block in "
               "tools/diag_csrf_bootstrap.py; the anchor moved")
    block = m.group(0)
    assert "set-cookie" in block.lower(), "extracted block does not read Set-Cookie"

    class _Headers:
        @staticmethod
        def items():
            return [("Content-Type", "text/html"), ("Set-Cookie", cookie)]

    resp = types.SimpleNamespace(headers=_Headers())
    buf = io.StringIO()
    with redirect_stdout(buf):
        exec(compile(block, "<diag_csrf_bootstrap cookie block>", "exec"),
             {"resp": resp})
    return buf.getvalue()


def test_the_bootstrap_diagnostic_never_emits_the_cookie_value():
    """THE SECOND LEAK. tools/diag_csrf_bootstrap.py:128 was print(f"  {c[:120]}").

    RED on pristine: the whole 43-char bd_session value fits in 120 characters,
    so the diagnostic emitted a live credential. Possessing it lets the holder
    derive the expected CSRF token (app.py:751) and replay as the operator
    (app_captures.py:105).
    """
    out = _run_bootstrap_cookie_block(_COOKIE)
    assert _SECRET not in out, (
        "tools/diag_csrf_bootstrap.py emitted the bd_session cookie VALUE:\n" + out)
    assert _SECRET[:12] not in out, (
        "a prefix of the cookie value leaked -- truncation is not redaction:\n" + out)


def test_the_bootstrap_diagnostic_keeps_its_diagnostic_facts():
    """Redaction that deletes the signal is a different bug.

    Same shape capture.sh step [3] settled on: count, name, value length, flags.
    """
    out = _run_bootstrap_cookie_block(_COOKIE)
    assert "1" in out, f"the header count is gone: {out!r}"
    assert "bd_session" in out, f"the cookie NAME is a fact and it is gone: {out!r}"
    assert str(len(_SECRET)) in out, (
        f"the value length -- the approved stand-in for the value -- is gone or "
        f"wrong; expected {len(_SECRET)}:\n{out}")
    assert "HttpOnly" in out, f"the HttpOnly flag is gone: {out!r}"


def test_the_bootstrap_diagnostic_omits_unknown_attribute_values():
    """Never emit what the diagnostic does not control.

    Mirrors test_an_unknown_attribute_value_is_omitted_not_echoed, which pinned
    this for capture.sh only -- the sibling had no such floor, so dropping the
    omission branch here was invisible.
    """
    marker = "FORBIDDEN-attr-value-must-not-leak"
    out = _run_bootstrap_cookie_block(
        f"bd_session=abcdefgh; X-Custom={marker}; HttpOnly")
    assert marker not in out, (
        f"an unknown cookie attribute's value was echoed; only flag-attribute "
        f"values may be printed:\n{out}")
    assert "X-Custom" in out, (
        f"the unknown attribute's NAME should still be reported: {out!r}")


def test_the_adversarial_probe_never_emits_the_minted_session():
    """tools/adversarial_probe.py:89 emitted victim_sess[:30] -- a REAL minted
    bd_session, not a synthetic one.

    Narrow and file-specific ON PURPOSE. A general taint analysis over tools/
    was tried and rejected: it fired on fuzz_probe.py's attack-payload strings
    and on diag_d2's count-and-boolean summary. This asserts one property about
    one known-risky name: victim_sess may reach an emitting call only inside
    len(), never whole and never sliced.
    """
    tree = ast.parse((REPO_ROOT / "tools" / "adversarial_probe.py")
                     .read_text(encoding="utf-8"))
    assert any(isinstance(n, ast.Name) and n.id == "victim_sess"
               for n in ast.walk(tree)), (
        "victim_sess is gone from adversarial_probe.py -- this test's subject "
        "moved and the check is now vacuous")

    safe = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "len":
            for sub in ast.walk(node):
                safe.add(id(sub))
        if isinstance(node, ast.Compare):
            for sub in ast.walk(node):
                safe.add(id(sub))

    leaks = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        for arg in node.args:
            if id(arg) in safe:
                continue
            for sub in ast.walk(arg):
                if id(sub) in safe:
                    continue
                if isinstance(sub, ast.Name) and sub.id == "victim_sess":
                    leaks.append((node.lineno, ast.unparse(node)[:150]))
                    break
    assert not leaks, (
        "adversarial_probe.py emits the server-minted bd_session value. The "
        "finding is that the ids DIFFER, which is a boolean; the value adds "
        "nothing and this report is shipped:\n"
        + "\n".join(f"  line {ln}: {src}" for ln, src in leaks))


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
