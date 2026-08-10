"""@1020. Three residues in already-merged work, and one of them the BOX found.

None of these is a new feature. Each is something @1016/@1018 left behind that
a later reader would have inherited as true.

RESIDUE A -- A TEST THAT SKIPPED INSTEAD OF ASSERTING, and the receipt is in
the operator's capture. `test_the_rate_limit_key_is_the_registrable_domain`
reached for its subject with `getattr(R, "_extract_domain", None)` and skipped
when that returned None. It is a @staticmethod on DomainRateLimiter, never a
module attribute, so the getattr ALWAYS returned None and both behavioural
assertions never ran. The capture at e7d3b5e shows skips going 4 -> 5 the
moment @1018 landed:

    test_the_rate_limit_key_is_the_registrable_domain
        SKIPPED: "_extract_domain is nested; covered by the AST test above"

-- and even the reason is wrong; it is not nested. A skip reads as fine in
every summary line, which is exactly CLAUDE.md section 0: a check reporting a
benign status over a subject it cannot reach. Fixed in
test_v3_66_1018_registrable_domain_drain.py itself; asserted here so the fix
cannot silently regress into a skip again.

RESIDUE B -- A DOCSTRING DESCRIBING A MECHANISM THAT WAS REMOVED. @1018
replaced rate_limit._extract_domain's three-layer repair with one call to the
canonical rule and deleted the rate_limit -> extension_vault import edge
(declared with --shrink). The docstring still said the function "uses
extension_vault's helper which IS eTLD+1-aware", is "intentionally simpler
than full eTLD+1", and repairs a bare-suffix answer afterwards. Three claims,
none of them true any more, in the place a reader looks first.

RESIDUE C -- AN UNCOVERED PATH IN @1016, AND IT IS A STALL RATHER THAN A MISS.
`do_login` handles a form that auto-submits when the password field is filled:
if the URL is already the success URL it returns early. A post-login wall is
NOT the success URL, so that check does not fire -- and the wall carries no
login form, so `_submit_login` then walks its whole selector list against a
page that can never satisfy any of them. @1016's dismissal sat AFTER that walk,
so it could not help. The fix dismisses a DECLARED wall in the auto-submit
branch, before the walk.

GATED, so the blast radius is one config key. A site that declares no wall
takes a byte-identical path -- `test_a_site_with_no_declared_wall_is_untouched`
is the over-sensitivity guard, and it is green on pristine source by design.
"""
from __future__ import annotations

import ast
import contextlib
import copy
import http.server
import os
import pathlib
import socketserver
import sys
import threading

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")


# ── residue A: the test asserts instead of skipping ───────────────

_DRAIN = REPO / "tests/test_v3_66_1018_registrable_domain_drain.py"


def _drain_fn(name: str) -> ast.FunctionDef:
    tree = ast.parse(_DRAIN.read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError("no %s in %s" % (name, _DRAIN.name))


def test_the_rate_limit_test_can_no_longer_SKIP_its_own_subject():
    """A skip is invisible in a pass count. This asserts the branch is gone."""
    fn = _drain_fn("test_the_rate_limit_key_is_the_registrable_domain")
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call)
             and getattr(n.func, "attr", None) == "skip"]
    assert not calls, (
        "the rate-limit test can still skip; it did exactly that on the box "
        "for every run since @1018, reporting a benign status over two "
        "assertions that never executed")


def _code_only(fn: ast.FunctionDef) -> str:
    """`fn`'s CODE, with docstrings removed.

    NOT TIDINESS, AND I LEARNED IT THE HARD WAY IN THIS VERY FILE. The first
    version of the test below asserted that the fixed function does not contain
    `getattr(R, "_extract_domain"` -- and FAILED, because the fixed function's
    own docstring quotes that call in order to explain what used to be wrong.
    `ast.unparse` renders docstrings as ordinary string expressions, so prose
    and code were indistinguishable to the predicate.

    That is CLAUDE.md section 0's "explaining a removal by naming the removed
    thing recreates it", committed inside a cut whose whole subject is
    residues, minutes after writing that sentence down. It is recorded here
    rather than quietly fixed because the file already argues that reading
    section 0 does not inoculate anyone against it.
    """
    stripped = copy.deepcopy(fn)
    for node in ast.walk(stripped):
        body = getattr(node, "body", None)
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.ClassDef, ast.Module))
                and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(stripped)


def test_the_code_only_view_can_tell_prose_from_code():
    """Non-empty denominator for the two assertions below: prove the stripper
    removes a docstring AND keeps the code around it."""
    mod = ast.parse('def f():\n    """getattr(R, X) is what we removed."""\n    return DomainRateLimiter._extract_domain\n')
    fn = next(n for n in ast.walk(mod) if isinstance(n, ast.FunctionDef))
    code = _code_only(fn)
    assert "what we removed" not in code, code
    assert "DomainRateLimiter._extract_domain" in code, code


def test_it_reaches_the_staticmethod_through_its_OWNER():
    """The access is the subject: a module-level getattr can never find a
    @staticmethod on a class, and returns None instead of raising."""
    fn = _drain_fn("test_the_rate_limit_key_is_the_registrable_domain")
    src = _code_only(fn)
    assert "DomainRateLimiter._extract_domain" in src, src[:400]
    assert "getattr(R, '_extract_domain'" not in src, src[:400]


def test_the_assertions_actually_execute_and_hold():
    """The two behaviours that never ran, run here too -- so this file fails if
    the drain file is ever weakened."""
    from bulk_downloader.rate_limit import DomainRateLimiter
    fn = DomainRateLimiter._extract_domain
    assert fn("https://www.bbc.co.uk/x") == "bbc.co.uk"
    assert fn("https://a.github.io/x") == "a.github.io"
    assert fn("magnet:?xt=urn:btih:abc") == ""


# ── residue B: the docstring names the mechanism the code uses ────

def _extract_domain_node() -> ast.FunctionDef:
    tree = ast.parse((REPO / "bulk_downloader/rate_limit.py").read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == "_extract_domain":
            return n
    raise AssertionError("_extract_domain not found -- re-derive this test")


def test_the_docstring_names_the_rule_the_CODE_actually_asks():
    fn = _extract_domain_node()
    doc = ast.get_docstring(fn) or ""
    # instrument-sees-subject: the body really does import the canonical rule,
    # so a docstring naming it is describing this code and not an aspiration
    imported = {a.name for n in ast.walk(fn) if isinstance(n, ast.ImportFrom)
                for a in n.names}
    assert "registrable_domain" in imported, (
        "the body no longer imports registrable_domain -- this test is "
        "asserting about the wrong function")
    assert "registrable_domain" in doc, (
        "the docstring does not name the rule the code asks: %r" % doc[:300])


def test_the_docstring_no_longer_claims_a_removed_dependency():
    """@1018 deleted the call AND the import edge. A docstring that still
    names extension_vault sends the next reader to a module this function has
    not touched since."""
    fn = _extract_domain_node()
    doc = ast.get_docstring(fn) or ""
    # The docstring's own v3.66.1020 paragraph NAMES extension_vault in order
    # to say the dependency was removed -- so only the half above it is the
    # live description, and that is what must be clean.
    assert "extension_vault" not in doc.split("v3.66.1020")[0], (
        "the live half of the docstring still claims extension_vault: %r" % doc[:400])
    # and the CODE must not reach it either. Docstring stripped, not
    # string-replaced: ast.get_docstring CLEANS indentation, so the text it
    # returns does not occur verbatim in ast.unparse output and a .replace()
    # removes nothing.
    assert "extension_vault" not in _code_only(fn), (
        "the CODE still reaches extension_vault, so @1018's --shrink was wrong")


# ── residue C: the auto-submit-on-fill path ───────────────────────

def _require_playwright():
    try:
        import playwright  # noqa: F401
    except ImportError:
        pytest.skip("playwright not installed -- this check cannot run here, "
                    "which is not the same as passing")


@contextlib.contextmanager
def _serving(handler_cls):
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield "http://127.0.0.1:%d" % httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


# The form submits itself the moment the password field receives input -- the
# shape do_login's early-return exists for. It lands on the WALL, not on
# success_url, so that early return does not fire.
_AUTOSUBMIT_LOGIN = b"""<html><body><h1>Sign in</h1>
<form method="GET" action="/wall">
  <input name="username" type="text">
  <input name="password" type="password" oninput="this.form.submit()">
</form></body></html>"""

# No button of any kind: every submit-selector fallback misses, which is what
# makes the pristine path a walk through the whole list for nothing.
_WALL = b"""<html><body><h1>Special offer</h1>
<a class="SkipPageButton-ButtonLink" href="/members">No Thanks. Continue</a>
</body></html>"""

_MEMBERS = b"""<html><body><h1>members area</h1></body></html>"""


def _server(login_html=_AUTOSUBMIT_LOGIN):
    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            body = (_MEMBERS if path.startswith("/members")
                    else _WALL if path.startswith("/wall")
                    else login_html)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    return _H


def _headless(monkeypatch):
    from playwright.sync_api import sync_playwright
    from bulk_downloader import cloak

    def _fake(*, headless=True, args=None, config=None, **kw):
        pw = sync_playwright().start()
        return pw.chromium.launch(headless=True), pw, "playwright-test"

    monkeypatch.setattr(cloak, "launch_browser", _fake)


def _login(base, wall):
    from bulk_downloader.login import do_login
    cfg = {"login_url": base + "/login", "username": "u", "password": "p",
           "success_url": "/members", "wait": 1,
           "use_stealth": False, "use_stealth_library": False}
    if wall is not None:
        cfg["dismiss_selectors_login"] = wall
    return do_login(cfg, allow_manual_takeover=False)


@pytest.mark.capture_serial
def test_the_wall_is_dismissed_WITHOUT_walking_the_submit_list(monkeypatch):
    """THE DEFECT, and note carefully what it is NOT.

    THE FIRST VERSION OF THIS TEST ASSERTED `ok is True` AND PASSED ON PRISTINE
    SOURCE -- proving nothing, which CLAUDE.md section 6 names exactly: "a test
    that passes on both is not a test." Measured: pristine took 578 SECONDS and
    still returned True, because _submit_login eventually clicks something, the
    flow reaches @1016's post-submit dismissal, and the wall clears there.

    So the defect is not a failure, it is a STALL: the whole submit-selector
    fallback list walked against a wall page that has no form. The exact,
    non-timing assertion for that is whether _submit_login is entered at all.
    A duration bound would work too (578s vs a few seconds is not marginal) but
    would be a clock in a test, and this is a fact about control flow.
    """
    _require_playwright()
    _headless(monkeypatch)
    from bulk_downloader.login_impl import submit as S

    calls = []
    real = S._submit_login

    def _counting(page, sb, pf):
        calls.append(1)
        return real(page, sb, pf)

    monkeypatch.setattr(S, "_submit_login", _counting)
    with _serving(_server()) as base:
        ok, why, _ = _login(base, "a.SkipPageButton-ButtonLink")
    assert ok is True, (
        "an auto-submitting login that lands on a DECLARED wall did not "
        "recover: %r" % (why,))
    assert calls == [], (
        "_submit_login was entered %d time(s) against the WALL page, which "
        "carries no login form -- that is the whole fallback list walked for "
        "nothing (measured at 578s on pristine source)" % len(calls))


@pytest.mark.capture_serial
def test_a_site_with_no_declared_wall_is_untouched(monkeypatch):
    """The over-sensitivity direction, green on pristine source and required
    to stay green: the recovery is gated on the site declaring a wall, so a
    site without one must reach exactly the same outcome as before."""
    _require_playwright()
    _headless(monkeypatch)
    # this form auto-submits straight to /members -- the pre-existing early
    # return handles it, and nothing this cut adds may interfere
    direct = _AUTOSUBMIT_LOGIN.replace(b'action="/wall"', b'action="/members"')
    with _serving(_server(direct)) as base:
        ok_unset, why_unset, _ = _login(base, None)
        ok_blank, why_blank, _ = _login(base, "")
    assert ok_unset is True, why_unset
    assert ok_blank is True, why_blank
    assert "auto-submitted on fill" in why_unset, why_unset


def test_the_wall_is_read_ONCE_and_gates_both_dismissal_sites():
    """Two reads of the same key could drift apart. One read, two uses."""
    tree = ast.parse((REPO / "bulk_downloader/login_impl/submit.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "do_login")
    reads = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "get"
             and n.args and isinstance(n.args[0], ast.Constant)
             and n.args[0].value == "dismiss_selectors_login"]
    assert len(reads) == 1, (
        "%d reads of dismiss_selectors_login in do_login; one read feeding "
        "both dismissal sites is what keeps them from drifting" % len(reads))
    dismissals = [n for n in ast.walk(fn)
                  if isinstance(n, ast.Call)
                  and getattr(n.func, "id", None) == "_dismiss_interstitials"]
    assert len(dismissals) == 2, (
        "expected the auto-submit dismissal AND the post-submit one; found %d"
        % len(dismissals))
    assert reads[0].lineno < min(d.lineno for d in dismissals), (
        "the key is read after a site that uses it")
