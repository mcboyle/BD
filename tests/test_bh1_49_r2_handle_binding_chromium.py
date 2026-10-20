"""BH1-49 r2: credential keys stay on the checked element, in real Chromium.

Adapted from the r1 security lens probe (bh1-49 .review/sec-probe-BH49S-B.py):
local stub servers only, *.test mapped to 127.0.0.1, public fixture value.
r1 bound a Locator, which re-resolves on every action: a navigation started
mid-typing (P7: the page's own input handler; P5: the refusal hook) carried
the next key into the foreign document (SEC F1), and a same-origin input
prepended mid-typing shifted nth() (SEC F2, P3).  r2 binds the ElementHandle,
verifies it before each key and aborts the whole entry when it is lost.
"""
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import sync_playwright

from bulk_downloader.login_impl import _common, submit

BD_GATE_SCOPE = "module"
BOUND_FIELD_LOST = "credential entry aborted: bound field lost"  # the abort reason prefix
TYPED_FIXTURE = "fixture-0123"  # public, 12-character fixture
PAGES = {}


class Stub(BaseHTTPRequestHandler):
    def do_GET(self):
        path, _, query = self.path.partition("?")
        if query.startswith("slow="):
            time.sleep(float(query[len("slow="):]))
        body = PAGES.get((self.server.server_port, path), "<html><body>404</body></html>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


class Site:
    def __init__(self, browser, a, b):
        self.browser = browser
        self.login = f"http://www.site.test:{a}/login"
        self.foreign = f"http://evil.test:{b}"


@pytest.fixture(scope="module")
def site():
    servers = [ThreadingHTTPServer(("127.0.0.1", 0), Stub) for _ in range(2)]
    for s in servers:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    a, b = (s.server_port for s in servers)
    PAGES[(a, "/login")] = (
        "<html><body><form><input id=user name=user>"
        "<input id=secret type=password name=pw style='width:200px'>"
        "<button type=submit>go</button></form>"
        f"<iframe id=fr src='http://evil.test:{b}/frame' width=300 height=80></iframe></body></html>")
    PAGES[(b, "/frame")] = "<html><body><input id=evil type=password></body></html>"
    PAGES[(b, "/phish")] = "<html><body><form><input id=secret type=password></form></body></html>"
    PAGES[(b, "/phishauto")] = (
        "<html><body><form><input id=secret type=password autofocus></form></body></html>")
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(
            headless=True, args=["--host-resolver-rules=MAP *.test 127.0.0.1"])
        try:
            yield Site(browser, a, b)
        finally:
            browser.close()
    finally:
        pw.stop()
        for s in servers:
            s.shutdown()
            s.server_close()


def run(site, hook=None, pre=None, sel="#secret", navigates_to=None):
    """_try_fill on the stub login page; ``hook(page, n)`` runs inside the
    n-th admitted refusal check.  Returns (ok, why, values read after)."""
    ctx = site.browser.new_context()
    try:
        page = ctx.new_page()
        page.goto(site.login)
        page.frame_locator("#fr").locator("#evil").wait_for()
        if pre:
            page.evaluate(pre)
        calls = []

        def refusal():
            why = submit._prefill_origin_refusal(page, site.login, None)
            calls.append(why)
            if not why and hook:
                hook(page, len(calls))
            return why

        ok, why = _common._try_fill(page, [sel], TYPED_FIXTURE, "password", refusal=refusal)
        seen = {}
        if navigates_to:
            page.wait_for_url(f"{site.foreign}{navigates_to}*", timeout=10000)
            page.locator("#secret").wait_for(state="attached")
            seen["foreign"] = page.locator("#secret").input_value()
        else:
            seen["main"] = page.locator("#secret").evaluate_all("els => els.map(e => e.value)")
            seen["frame"] = page.frame_locator("#fr").locator("#evil").input_value()
            seen["decoy"] = page.locator("input.decoy").evaluate_all("els => els.map(e => e.value)")
        return ok, why, seen
    finally:
        ctx.close()


@pytest.mark.parametrize("delay", [0, 0.15], ids=["fast", "slow150"])
@pytest.mark.parametrize("how", ["setTimeout0", "sync"])
@pytest.mark.parametrize("after", [1, 3, 6])
def test_f1_page_input_handler_navigation_types_nothing_into_the_foreign_document(
        site, after, how, delay):
    nav = f"location.href='{site.foreign}/phish?slow={delay}'"
    act = f"setTimeout(() => {{{nav}}}, 0)" if how == "setTimeout0" else nav
    pre = (f"() => document.querySelector('#secret').addEventListener('input', e => "
           f"{{ if (e.target.value.length === {after}) {{ {act} }} }})")
    ok, why, seen = run(site, pre=pre, navigates_to="/phish")
    assert seen["foreign"] == "", (
        f"BH1_49_R2_F1_FOREIGN_LEAK: {len(seen['foreign'])} credential char(s) reached the "
        f"foreign document after a nav started at char {after} ({how}, {delay}s); {ok=} {why=}")


@pytest.mark.parametrize("round_", [1, 2])
@pytest.mark.parametrize("delay", [0, 0.05, 0.15, 0.4])
@pytest.mark.parametrize("target", ["/phish", "/phishauto"], ids=["noautofocus", "autofocus"])
def test_p5_hook_navigation_types_nothing_into_the_foreign_document(site, target, delay, round_):
    def hook(page, n):
        if n == 6:
            page.evaluate(f"() => setTimeout(() => "
                          f"location.href='{site.foreign}{target}?slow={delay}', 0)")

    ok, why, seen = run(site, hook=hook, navigates_to=target)
    assert seen["foreign"] == "", (
        f"BH1_49_R2_P5_FOREIGN_LEAK: {seen['foreign']!r} reached {target} (delay {delay}s, "
        f"round {round_}); {ok=} {why=}")


def test_p3_prepended_same_origin_input_gets_no_key(site):
    def hook(page, n):
        if n == 6:
            page.evaluate("() => { const d = document.createElement('input'); d.type = 'password';"
                          " d.className = 'decoy'; document.querySelector('form').prepend(d); }")

    ok, why, seen = run(site, hook=hook, sel="input[type=password]")
    assert seen["decoy"] == [""], f"BH1_49_R2_P3_DECOY: decoy got {seen['decoy']}; {ok=} {why=}"
    assert seen["main"] == [TYPED_FIXTURE], f"BH1_49_R2_P3_MAIN: {seen['main']}; {ok=} {why=}"


@pytest.mark.parametrize("caret_home", [False, True], ids=["", "caret-moved-home"])
@pytest.mark.parametrize("from_check", [1, 7], ids=["every-key", "mid-sequence"])
def test_focus_steal_refocuses_with_the_caret_at_the_end(site, from_check, caret_home):
    def hook(page, n):
        if n >= from_check:
            if caret_home:  # focus() alone would restore this caret
                page.evaluate("() => document.querySelector('#secret').setSelectionRange(0, 0)")
            page.frame_locator("#fr").locator("#evil").focus()

    ok, why, seen = run(site, hook=hook)
    assert seen["frame"] == "", f"BH1_49_R2_FOCUS_LEAK: frame got {seen['frame']!r}"
    assert seen["main"] == [TYPED_FIXTURE], (
        f"BH1_49_R2_CARET_ORDER: field holds {seen['main']} after refocus; {ok=} {why=}")
    assert (ok, why) == (True, "#secret"), f"BH1_49_R2_FILL: {ok=} {why=}"


def test_detached_field_aborts_the_entry_without_retyping(site):
    at_detach = {}

    def hook(page, n):
        if n == 6:
            at_detach["value"] = page.evaluate(
                "() => { const o = document.querySelector('#secret'); const v = o.value; o.remove();"
                " const n = document.createElement('input'); n.id = 'secret'; n.type = 'password';"
                " n.value = v; document.querySelector('form').appendChild(n); return v; }")

    ok, why, seen = run(site, hook=hook)
    assert ok is False and why.startswith(BOUND_FIELD_LOST) and "isConnected" in why, (
        f"BH1_49_R2_DETACH_ABORT: {ok=} {why=}")
    assert seen["main"] == [at_detach["value"]], (
        f"BH1_49_R2_DETACH_RETYPED: replacement holds {seen['main']}, "
        f"had {at_detach['value']!r} at detach")
    assert seen["frame"] == ""


def test_control_normal_typing_completes(site):
    ok, why, seen = run(site)
    assert (ok, why) == (True, "#secret"), f"BH1_49_R2_CONTROL: {ok=} {why=}"
    assert seen["main"] == [TYPED_FIXTURE] and seen["frame"] == "", f"BH1_49_R2_CONTROL: {seen}"
