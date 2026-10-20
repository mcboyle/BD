"""BH1-49 r3: after bind the credential path calls no Locator, and the
checked element is the bound one.

r2 SEC F3: a page-scheduled navigation (the page's own setTimeout, no hook)
detached the bound handle during its click; the except branch fell back to
``loc.fill(value)``, which re-resolved the Locator in the FOREIGN document
and typed the full credential there.  r2 SEC F4: the honeypot/search checks
ran on ``nth(idx)`` and bind re-resolved ``nth(idx)`` afterwards, so an input
prepended between the two received the credential.  r3 binds FIRST, checks
the handle, and a failed click raises BoundFieldLost.  Real-Chromium rows
use local stub servers only (*.test mapped to 127.0.0.1) and a public
fixture value.
"""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import sync_playwright

from bulk_downloader import synthetic_input_scheduler
from bulk_downloader.login_impl import _common, submit

BD_GATE_SCOPE = "module"
BOUND_FIELD_LOST = "credential entry aborted: bound field lost"  # the abort reason prefix
TYPED_FIXTURE = "fixture-0123"  # public, 12-character fixture
LOGIN_URL = "https://www.site.test/login"
PAGES = {}


# --- doubles: every Locator call is recorded; any after bind is the defect ---

class Page:
    def __init__(self, click_fails):
        self.url = LOGIN_URL
        self.main_frame = Frame(LOGIN_URL)
        self.keyboard = self
        self.click_fails = click_fails
        self.bound = False
        self.locator_calls, self.after_bind, self.keyboard_keys = [], [], []
        self.handle = None

    def type(self, char, **kwargs):
        self.keyboard_keys.append(char)

    def press(self, key, **kwargs):
        self.keyboard_keys.append(key)

    def locator(self, selector):
        return Matches(self)


class Frame:
    def __init__(self, url):
        self.url = url

    def is_detached(self):
        return False


class Matches:
    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    def wait_for(self, **kwargs):
        pass

    def count(self):
        return 1

    def nth(self, index):
        return Locator(self.page)


class Locator:
    """Records every call; after ``element_handle`` each one is a violation."""

    def __init__(self, page):
        self.page = page

    def _call(self, name, *args):
        self.page.locator_calls.append((name, args))
        if self.page.bound:
            self.page.after_bind.append((name, args))

    def wait_for(self, **kwargs):
        self._call("wait_for")

    def get_attribute(self, name):
        self._call("get_attribute", name)

    def bounding_box(self):
        self._call("bounding_box")
        return {"x": 10, "y": 10}

    def evaluate(self, script, arg=None):
        self._call("evaluate")
        return ""

    def fill(self, value, **kwargs):
        self._call("fill", value)

    def click(self, **kwargs):
        self._call("click")

    def type(self, value, **kwargs):
        self._call("type", value)

    def press(self, key, **kwargs):
        self._call("press", key)

    def press_sequentially(self, value, **kwargs):
        self._call("press_sequentially", value)

    def element_handle(self, **kwargs):
        self._call("element_handle")
        self.page.bound = True
        self.page.handle = Handle(self.page)
        return self.page.handle


class Handle:
    def __init__(self, page):
        self.page = page
        self.frame = Frame(LOGIN_URL)
        self.value = ""
        self.filled, self.typed = [], []

    def owner_frame(self):
        return self.frame

    def evaluate(self, script, arg=None):
        if "sameDoc" in script:
            return {"connected": True, "sameDoc": True, "origin": "https://www.site.test"}
        if isinstance(arg, dict):  # BoundField._WRITE (r4: one in-realm write per character)
            if arg["value"] is not None:
                self.value = arg["value"]
                if arg["data"]:
                    self.typed.append(arg["data"])
            return {"value": self.value}
        return ""  # _SEARCH_FIELD_JS: not a search box

    def get_attribute(self, name):
        return None

    def bounding_box(self):
        return {"x": 10, "y": 10}

    def wait_for_element_state(self, state, **kwargs):
        pass

    def fill(self, value, **kwargs):
        self.filled.append(value)

    def click(self, **kwargs):
        if self.page.click_fails:
            raise RuntimeError("Element is not attached to the DOM")

    def type(self, *args, **kwargs):
        raise AssertionError("BH1_49_R4_HANDLE_KEY: ElementHandle.type in the credential path")

    press = focus = type


def no_scheduler():
    raise RuntimeError("fixture: plan unavailable")


@pytest.mark.parametrize("fallback", [False, True], ids=["scheduler", "plan-fallback"])
@pytest.mark.parametrize("click_fails", [True, False], ids=["click-fails", "click-ok"])
def test_no_locator_call_after_bind(monkeypatch, click_fails, fallback):
    monkeypatch.setattr(synthetic_input_scheduler.BoundField, "_sleep", staticmethod(lambda s: None))
    if fallback:
        monkeypatch.setattr(_common, "get_input_scheduler", no_scheduler)
    page = Page(click_fails)
    ok, why = _common._try_fill(page, ["#secret", "#alternate"], TYPED_FIXTURE, "password",
                                refusal=lambda: None)
    assert page.after_bind == [], (
        f"BH1_49_R3_LOCATOR_AFTER_BIND: {page.after_bind} after element_handle; {ok=} {why=}")
    leaked = [c for c in page.locator_calls if TYPED_FIXTURE in map(str, c[1])]
    assert leaked == [], f"BH1_49_R3_LOCATOR_GOT_SECRET: {leaked}"
    assert page.keyboard_keys == [], f"BH1_49_R3_KEYBOARD: {page.keyboard_keys}"
    assert TYPED_FIXTURE not in page.handle.filled, f"BH1_49_R3_HANDLE_FILL: {page.handle.filled}"
    if click_fails:
        assert ok is False and why.startswith(f"{BOUND_FIELD_LOST} (click"), (
            f"BH1_49_R3_CLICK_ABORT: {ok=} {why=}")
        assert page.handle.typed == [], f"BH1_49_R3_KEY_AFTER_ABORT: {page.handle.typed}"
    else:
        assert (ok, why) == (True, "#secret"), f"BH1_49_R3_CONTROL: {ok=} {why=}"
        assert "".join(page.handle.typed) == TYPED_FIXTURE, f"BH1_49_R3_CONTROL: {page.handle.typed}"


# --- real Chromium ---

class Stub(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.partition("?")[0]
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
        "<button type=submit>go</button></form></body></html>")
    # The page animates its field, so the click's actionability wait (stable
    # box) spans the page's own navigation instead of a ~40 ms window.
    PAGES[(a, "/moving")] = PAGES[(a, "/login")].replace(
        "<body>", "<body><style>@keyframes slide { to { margin-left: 40px; } }"
        " #secret { animation: slide .2s infinite alternate; }</style>")
    PAGES[(b, "/phish")] = "<html><body><form><input id=secret type=password></form></body></html>"
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


def run(site, pre=None, sel="#secret", navigates=False, path="/login"):
    ctx = site.browser.new_context()
    try:
        page = ctx.new_page()
        page.goto(site.login.replace("/login", path))
        page.locator("#secret").wait_for()
        if pre:
            page.evaluate(pre)
        ok, why = _common._try_fill(
            page, [sel], TYPED_FIXTURE, "password",
            refusal=lambda: submit._prefill_origin_refusal(page, site.login, None))
        seen = {}
        if navigates:
            page.wait_for_url(f"{site.foreign}/phish*", timeout=10000)
            page.locator("#secret").wait_for(state="attached")
            seen["foreign"] = page.locator("#secret").input_value()
        else:
            seen["main"] = page.locator("#secret").evaluate_all("els => els.map(e => e.value)")
            seen["decoy"] = page.locator("input.decoy").evaluate_all("els => els.map(e => e.value)")
        return ok, why, seen
    finally:
        ctx.close()


@pytest.mark.parametrize("ms", list(range(100, 501, 20)))
@pytest.mark.parametrize("path", ["/login", "/moving"])
def test_p6_page_scheduled_navigation_types_nothing_into_the_foreign_document(site, path, ms):
    pre = f"() => setTimeout(() => {{ location.href = '{site.foreign}/phish'; }}, {ms})"
    ok, why, seen = run(site, pre=pre, navigates=True, path=path)
    assert seen["foreign"] == "", (
        f"BH1_49_R3_P6_FOREIGN_LEAK: {len(seen['foreign'])} credential char(s) reached the "
        f"foreign document ({path}, page setTimeout nav at {ms} ms, no hook); {ok=} {why=}")


def test_f4_input_prepended_between_check_and_bind_gets_no_key(site, monkeypatch):
    real = _common._is_honeypot_field
    done = []

    def check_then_prepend(el):
        result = real(el)
        if not done:  # once, right after the first match's check
            done.append(el)
            el.evaluate("e => { const d = document.createElement('input'); d.type = 'password';"
                        " d.className = 'decoy'; e.form.prepend(d); }")
        return result

    monkeypatch.setattr(_common, "_is_honeypot_field", check_then_prepend)
    ok, why, seen = run(site, sel="input[type=password]")
    assert seen["decoy"] == [""], (
        f"BH1_49_R3_F4_DECOY: the unchecked decoy got {seen['decoy']}; {ok=} {why=}")
    assert seen["main"] == [TYPED_FIXTURE], f"BH1_49_R3_F4_MAIN: {seen['main']}; {ok=} {why=}"


def test_control_normal_typing_completes(site):
    ok, why, seen = run(site)
    assert (ok, why) == (True, "#secret"), f"BH1_49_R3_CONTROL: {ok=} {why=}"
    assert seen["main"] == [TYPED_FIXTURE] and seen["decoy"] == [], f"BH1_49_R3_CONTROL: {seen}"
