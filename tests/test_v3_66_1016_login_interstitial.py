"""@1016. The post-login interstitial: one dismissal loop, two declared scopes.

ITEM E of 15.74's A-H program. The design is the operator's, recorded in
SESSION_CARRY 15.83: login-wall selectors fire ONCE in ``do_login``, per-page
selectors (cookie / age / consent) keep firing per URL, and templates declare
which is which.

WHAT WAS ACTUALLY BROKEN, and it is not only cost. ``do_login`` dismisses
NOTHING between ``_submit_login`` and its ``success_url`` comparison -- read
from source, and 15.79 records the same. A site whose "No Thanks. Continue to
Members Area" wall sits between the login POST and the members area therefore
leaves ``page.url`` on the wall, ``success not in cur`` fires, and a perfectly
good login is thrown into manual takeover. ``test_the_wall_blocks_the_success_
url_check_until_it_is_dismissed`` is that failure, end to end, in a browser.

THE COST FIGURE IN 15.83 IS HALF RIGHT, AND THE HALF THAT MISSES MATTERS.
The register says five login-wall selectors cost "up to 15s PER URL", flagged
as READ FROM SOURCE rather than measured. Measured here at 987e960 against a
real chromium and a page where none of them match:

    shipped Gamma value (ONE comma-joined line -> 1 locator)    3.00s per URL
    the same five as five lines (what a captured template emits) 15.01s per URL

Both are real; they are different shapes. ``runner.py`` splits on NEWLINES, so
the hand-written Gamma one-liner has always cost 3.00s, not 15s. So this cut is
honest about what it buys: for Gamma the split is cost-NEUTRAL and the win is
purely the correctness one above; the 3s-per-line saving lands for a captured
template, which emits one selector per line. A saving is claimed for the shape
that has it, and not for the shape that does not.

WHY A SECOND CONFIG KEY RATHER THAN A PREFIX INSIDE THE EXISTING ONE.
``dismiss_selectors`` is a documented "one CSS selector per line" surface with a
settings-center control, a ledger entry, and hand-written values in
``site_templates/_data_players.py``. Encoding a scope in-band (``login: a.foo``)
would make a valid CSS selector and a scope marker the same syntax, and the
parser could not tell a custom element ``login`` from the marker. A separate key
costs one CFG_FIELDS entry and one ledger row, and cannot be misread.

THE HELPER IS SHARED ON PURPOSE. Two copies of a click-with-timeout loop drift:
one grows a settle, the other does not, and no test compares them. The census
below asserts no hand-rolled copy survives outside the helper -- proved
sensitive on a known positive AND on a known negative first, because a census
that cannot see its subject reports "none" truthfully and uselessly, and a
census that over-matches reports an offender that never existed. Both halves
were needed here: the first predicate flagged the download TRIGGER loop.

A ZERO-OFFENDER CENSUS IS ALSO SATISFIED BY DELETING THE FEATURE, so
``test_the_per_url_path_still_DELEGATES_rather_than_having_dropped_it`` asserts
the other direction. Every gate in this file is paired that way.
"""
from __future__ import annotations

import ast
import contextlib
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


# ── a page that records what was asked of it ──────────────────────

class _FakeLocator:
    def __init__(self, page, selector, visible):
        self._page = page
        self._selector = selector
        self._visible = visible

    @property
    def first(self):
        return self

    def wait_for(self, **kw):
        self._page.waited.append((self._selector, kw.get("timeout")))
        if not self._visible:
            raise RuntimeError("Timeout %r exceeded" % kw.get("timeout"))

    def click(self):
        self._page.clicked.append(self._selector)


class _FakePage:
    """Visible for the selectors in ``visible``; a timeout for everything else."""

    def __init__(self, visible=()):
        self._visible = set(visible)
        self.waited = []
        self.clicked = []
        self.slept = []

    def locator(self, selector):
        return _FakeLocator(self, selector, selector in self._visible)


def _mod():
    from bulk_downloader import interstitial
    return interstitial


# ── the helper ────────────────────────────────────────────────────

def test_every_line_is_tried_and_a_visible_one_is_clicked():
    m = _mod()
    page = _FakePage(visible={"button.ok"})
    clicked = m.dismiss(page, "a.gone\nbutton.ok\nspan.absent",
                        sleep=page.slept.append)
    assert [s for s, _ in page.waited] == ["a.gone", "button.ok", "span.absent"]
    assert page.clicked == ["button.ok"]
    assert clicked == ["button.ok"]


def test_a_selector_that_never_appears_does_not_stop_the_ones_after_it():
    """The shipped loop swallows every exception; that behaviour is the point --
    a popup that did not show up must never fail a URL."""
    m = _mod()
    page = _FakePage(visible={"span.last"})
    assert m.dismiss(page, "a.gone\nb.gone\nspan.last",
                     sleep=page.slept.append) == ["span.last"]


def test_blank_lines_and_hash_comments_are_skipped():
    m = _mod()
    page = _FakePage()
    m.dismiss(page, "\n  \n# a comment\n#another\n  a.real  \n",
              sleep=page.slept.append)
    assert [s for s, _ in page.waited] == ["a.real"]


def test_the_settle_sleep_happens_only_after_a_click():
    """A MISS must cost nothing beyond its own timeout. If the settle ran on
    every line, five absent selectors would add 2.5s of pure sleep on top of
    the 15s of waiting -- and no existing test would have noticed."""
    m = _mod()
    page = _FakePage(visible={"button.ok"})
    m.dismiss(page, "a.gone\nbutton.ok\nb.gone", sleep=page.slept.append)
    assert page.slept == [m.DEFAULT_SETTLE_S]


def test_dismiss_reports_what_it_CLICKED_not_what_it_TRIED():
    """do_login needs this: it re-waits for a load only when something was
    actually clicked. A helper returning the tried list would make it re-wait
    on every login, including the sites with no wall at all."""
    m = _mod()
    page = _FakePage()
    assert m.dismiss(page, "a.gone\nb.gone", sleep=page.slept.append) == []


def test_the_timeout_is_the_one_the_runner_has_always_used():
    m = _mod()
    page = _FakePage()
    m.dismiss(page, "a.gone", sleep=page.slept.append)
    assert page.waited == [("a.gone", 3000)]
    assert m.DEFAULT_TIMEOUT_MS == 3000


def test_a_blank_or_missing_block_touches_the_page_at_all():
    m = _mod()
    for raw in ("", None, "   \n\n"):
        page = _FakePage()
        assert m.dismiss(page, raw, sleep=page.slept.append) == []
        assert page.waited == []


# ── exactly one dismissal loop in the product ─────────────────────

def _is_dismiss_loop(fn: ast.FunctionDef) -> bool:
    """Whether ``fn`` loops over a NEWLINE-SPLIT selector block and clicks it.

    Matched on SHAPE, not on a name: a second copy under a different name would
    be invisible to a name check, which is the whole point of consolidating.

    THE FIRST VERSION OF THIS PREDICATE WAS WRONG, and the way it was wrong is
    worth keeping. It asked only for a loop containing ``wait_for`` + ``click``
    + ``locator``, and reported TWO offenders after the consolidation had
    landed. The second was ``_process_one``'s DOWNLOAD TRIGGER loop at
    runner.py:3377 -- a legitimately different thing that happens to click a
    selector it waited for. CLAUDE.md section 1: the instrument fixes the
    denominator, the predicate fixes the subject, and the AST walk was never
    the problem. What distinguishes a dismissal is its INPUT: an operator-edited
    block of selectors, one per line, hence ``splitlines`` on the iterable.
    """
    for node in ast.walk(fn):
        if not isinstance(node, ast.For):
            continue
        if "splitlines" not in ast.unparse(node.iter):
            continue
        if ".click()" in ast.unparse(node):
            return True
    return False


def _product_functions():
    for path in sorted((REPO / "bulk_downloader").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield path.relative_to(REPO).as_posix(), n


def test_the_loop_census_can_SEE_a_known_positive():
    """Non-empty denominator AND a proven-sensitive predicate, both asserted
    before the census below is believed. The positive is the loop that shipped
    in runner._process_one until this cut, verbatim in shape."""
    positive = ("def f(page, raw):\n"
                "    for sel_line in raw.splitlines():\n"
                "        sel = sel_line.strip()\n"
                "        loc = page.locator(sel).first\n"
                "        loc.wait_for(timeout=3000, state='visible')\n"
                "        loc.click()\n")
    fn = next(n for n in ast.walk(ast.parse(positive))
              if isinstance(n, ast.FunctionDef))
    assert _is_dismiss_loop(fn), "the census cannot see a known positive"

    # and the NEGATIVE the first predicate got wrong: a trigger loop over a
    # LIST of selectors is not a dismissal, and counting it made the census
    # report an offender that had never existed.
    negative = ("def g(page, triggers):\n"
                "    for sel in triggers:\n"
                "        loc = page.locator(sel).first\n"
                "        loc.wait_for(timeout=3000)\n"
                "        loc.click()\n")
    fn2 = next(n for n in ast.walk(ast.parse(negative))
               if isinstance(n, ast.FunctionDef))
    assert not _is_dismiss_loop(fn2), "a list-driven trigger loop is not a dismissal"

    assert sum(1 for _ in _product_functions()) > 500, "the file walk went blind"


def test_no_hand_rolled_dismissal_loop_survives_outside_the_helper():
    found = ["%s:%d %s" % (rel, n.lineno, n.name)
             for rel, n in _product_functions() if _is_dismiss_loop(n)]
    assert found == [], (
        "a hand-rolled dismissal loop survives; both consumers must share "
        "bulk_downloader.interstitial.dismiss: %r" % found)


def test_the_per_url_path_still_DELEGATES_rather_than_having_dropped_it():
    """The half a census cannot see. Deleting the dismissal outright satisfies
    'no hand-rolled loop survives' perfectly, and silently stops dismissing
    cookie banners on every site -- the fix reproducing the shape of the
    defect, which CLAUDE.md calls the highest-yield rule on the page."""
    fn = _fn_named("bulk_downloader/runner.py", "_process_one")
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "dismiss"]
    assert calls, "_process_one no longer dismisses anything per URL"


# ── which consumer reads which key, and in what order ─────────────

def _fn_named(rel: str, name: str) -> ast.FunctionDef:
    tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    raise AssertionError("no function %r in %s" % (name, rel))


def _get_string_args(fn: ast.AST) -> list:
    """Every ``X.get("literal"...)`` string argument inside ``fn``, with its
    line -- read from the CALL, so a comment or docstring naming a key cannot
    enter the answer. CLAUDE.md section 0 records four cuts where an assertion
    could not tell prose from code."""
    out = []
    for n in ast.walk(fn):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "get" and n.args
                and isinstance(n.args[0], ast.Constant)
                and isinstance(n.args[0].value, str)):
            out.append((n.args[0].value, n.lineno))
    return out


LOGIN_KEY = "dismiss_selectors_login"
PAGE_KEY = "dismiss_selectors"


def test_the_per_url_path_reads_the_per_page_key_and_never_the_login_one():
    """If ``_process_one`` fired the login-wall block it would pay for it on
    every URL -- which is the cost this cut removes."""
    keys = [k for k, _ in _get_string_args(
        _fn_named("bulk_downloader/runner.py", "_process_one"))]
    assert PAGE_KEY in keys, "the per-URL dismissal stopped reading %r" % PAGE_KEY
    assert LOGIN_KEY not in keys, (
        "_process_one reads %r -- the login wall would be retried on every "
        "URL, at 3s per selector line" % LOGIN_KEY)


def test_do_login_reads_the_login_key_BEFORE_it_checks_the_success_url():
    """The ordering IS the fix. Dismissing after the comparison would leave the
    comparison reading the wall's URL, which is exactly today's failure."""
    fn = _fn_named("bulk_downloader/login_impl/submit.py", "do_login")
    reads = [ln for k, ln in _get_string_args(fn) if k == LOGIN_KEY]
    assert reads, "do_login never reads %r" % LOGIN_KEY

    # the success_url comparison: `if success and success not in cur`
    checks = [n.lineno for n in ast.walk(fn)
              if isinstance(n, ast.Compare)
              and any(isinstance(o, ast.NotIn) for o in n.ops)
              and isinstance(n.left, ast.Name) and n.left.id == "success"]
    assert checks, "the success_url comparison moved -- re-derive this test"
    assert min(reads) < min(checks), (
        "the interstitial is dismissed at line %d, AFTER the success_url check "
        "at line %d: the check still reads the wall's URL" % (min(reads), min(checks)))


def test_do_login_does_not_pay_for_sites_that_declare_no_wall():
    """Guarded on the value being non-empty. Without the guard every login on
    every site imports the helper and calls into it for nothing."""
    fn = _fn_named("bulk_downloader/login_impl/submit.py", "do_login")
    src = ast.unparse(fn)
    assert LOGIN_KEY in src
    # the read is bound to a name, and that name gates the dismissal
    assert any(isinstance(n, ast.If) and LOGIN_KEY not in ast.unparse(n.test)
               and "dismiss" in ast.unparse(n).lower()
               for n in ast.walk(fn)), (
        "the dismissal is not gated on the site declaring a wall")


# ── the config surface ────────────────────────────────────────────

def test_the_new_key_is_in_CFG_FIELDS_so_it_survives_a_reload():
    """A per-site key absent from CFG_FIELDS is dropped by the
    ``_load_sites_config`` rebuild -- app_kernel.py says so in the comment on
    ``predictive_relogin_enabled``, which was added for exactly that reason."""
    from bulk_downloader import app_kernel
    fields = app_kernel.CFG_FIELDS
    assert PAGE_KEY in fields, "denominator check: the existing key is present"
    assert LOGIN_KEY in fields, (
        "%r is not in CFG_FIELDS -- a template could set it and the next "
        "config reload would silently drop it" % LOGIN_KEY)


def test_the_new_key_is_ledgered_in_the_config_surface_inventory():
    sys.path.insert(0, str(REPO / "tools"))
    import config_surface_inventory as csi
    d = csi.build(str(REPO))
    site_keys = {i["key"]: i for i in d["items"] if i["kind"] == "site_key"}
    assert PAGE_KEY in site_keys, "denominator check"
    assert LOGIN_KEY in site_keys, (
        "%r is a per-site setting and is not in the config-surface ledger"
        % LOGIN_KEY)
    # Categorised WITH the other login keys rather than pinned to a literal:
    # the inventory prefixes per-site keys ("per-site/auth/session"), and a
    # test asserting the bare string would be pinning a naming convention it
    # does not own. Derived from a sibling, so it survives that convention
    # changing and still fails if this key drifts away from the login group.
    it = site_keys[LOGIN_KEY]
    assert it["category"] == site_keys["login_url"]["category"], (
        "%r is categorised %r, not with the login keys (%r)"
        % (LOGIN_KEY, it["category"], site_keys["login_url"]["category"]))
    assert it["gui_exposure"] in ("full", "partial", "none", "display-only")


# ── the template declares which is which ──────────────────────────

def _gamma_defaults() -> dict:
    from bulk_downloader.site_templates import _data_players as dp
    for t in dp.ITEMS:
        if (t.get("config_defaults") or {}).get(LOGIN_KEY):
            return t["config_defaults"]
    raise AssertionError(
        "no template in _data_players declares %r -- the Gamma 'Skip this "
        "page' wall is the precedent this cut exists to model" % LOGIN_KEY)


_WALL = ("SkipPageButton", "No Thanks. Continue", "Continue to Members Area")
_PER_PAGE = ("I Agree", "close")


def test_the_gamma_wall_selectors_moved_to_the_login_key():
    cd = _gamma_defaults()
    wall = cd[LOGIN_KEY]
    for token in _WALL:
        assert token in wall, "%r left the login-wall block" % token


def test_the_gamma_wall_selectors_are_NOT_still_fired_per_url():
    """Leaving them in both places would keep the per-URL cost AND make the
    split cosmetic -- the shape a fix takes when it adds without removing."""
    cd = _gamma_defaults()
    per_page = cd.get(PAGE_KEY, "")
    for token in _WALL:
        assert token not in per_page, (
            "%r is still in %r; it is dismissed at login now" % (token, PAGE_KEY))


def test_the_consent_and_age_selectors_STAYED_per_url():
    """The other half of the split, and the one a careless move breaks: an
    'I Agree' consent gate can appear on ANY content page, so it is not a
    login-wall selector and must keep firing per URL."""
    cd = _gamma_defaults()
    per_page = cd.get(PAGE_KEY, "")
    for token in _PER_PAGE:
        assert token in per_page, (
            "%r left the per-URL block; a consent gate is not a login wall"
            % token)


# ── end to end, in a browser: the wall blocks the success check ───

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


_LOGIN_HTML = b"""<html><body><h1>Sign in</h1>
<form method="GET" action="/interstitial">
  <input name="username" type="text">
  <input name="password" type="password">
  <button type="submit">Log in</button>
</form></body></html>"""

# The wall. Note it carries NO password field and NO members-area URL: the only
# way past it is the link.
_WALL_HTML = b"""<html><body><h1>Special offer</h1>
<a class="SkipPageButton-ButtonLink" href="/members">No Thanks. Continue</a>
</body></html>"""

_MEMBERS_HTML = b"""<html><body><h1>members area</h1></body></html>"""


def _wall_server():
    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            body = (_MEMBERS_HTML if path.startswith("/members")
                    else _WALL_HTML if path.startswith("/interstitial")
                    else _LOGIN_HTML)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    return _H


def _headless_launch(monkeypatch):
    """do_login launches HEADED (a human may take over). There is no display
    here, so the browser is launched headless instead -- the only thing this
    changes is the window, and every assertion below is about navigation."""
    from playwright.sync_api import sync_playwright
    from bulk_downloader import cloak

    def _fake(*, headless=True, args=None, config=None, **kw):
        pw = sync_playwright().start()
        return pw.chromium.launch(headless=True), pw, "playwright-test"

    monkeypatch.setattr(cloak, "launch_browser", _fake)


def _login_against(base_url, wall_value):
    from bulk_downloader.login import do_login
    cfg = {"login_url": base_url + "/login",
           "username": "u", "password": "p",
           "success_url": "/members",
           "wait": 1,
           "use_stealth": False, "use_stealth_library": False}
    if wall_value is not None:
        cfg[LOGIN_KEY] = wall_value
    return do_login(cfg, allow_manual_takeover=False)


@pytest.mark.capture_serial
def test_the_wall_blocks_the_success_url_check_until_it_is_dismissed(monkeypatch):
    """THE DEFECT, end to end. Same server, same login, same success_url --
    the only variable is whether the site declares its login wall."""
    _require_playwright()
    _headless_launch(monkeypatch)
    with _serving(_wall_server()) as base:
        undeclared_ok, undeclared_why, _ = _login_against(base, None)
        declared_ok, declared_why, _ = _login_against(
            base, "a.SkipPageButton-ButtonLink")

    # the control: with no wall declared the login is thrown away on the wall's
    # own URL. If this ever passes, the fixture stopped reproducing the defect.
    assert undeclared_ok is False, (
        "the fixture no longer reproduces the wall: %r" % (undeclared_why,))
    assert "/interstitial" in undeclared_why, undeclared_why

    assert declared_ok is True, (
        "the declared login wall was not dismissed before the success_url "
        "check: %r" % (declared_why,))


@pytest.mark.capture_serial
def test_a_site_with_no_wall_is_unaffected(monkeypatch):
    """The over-sensitive direction. A fix that navigates or waits on every
    login would pass the test above and break every site without a wall."""
    _require_playwright()
    _headless_launch(monkeypatch)

    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            body = (_MEMBERS_HTML if path.startswith("/members")
                    else _LOGIN_HTML.replace(b"/interstitial", b"/members"))
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    with _serving(_H) as base:
        ok_unset, why_unset, _ = _login_against(base, None)
        ok_blank, why_blank, _ = _login_against(base, "")
    assert ok_unset is True, why_unset
    assert ok_blank is True, why_blank
