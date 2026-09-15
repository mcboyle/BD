"""Row 775: an operator-enabled local Turnstile tick never uses a solver."""

BD_GATE_SCOPE = "repo-wide"

from bulk_downloader.login_impl.submit import _try_turnstile_one_click, _TURNSTILE_CHECKBOX


class _Checkbox:
    def __init__(self, clicks, name):
        self._clicks = clicks
        self._name = name

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def is_visible(self, timeout=0):
        return True

    def click(self, timeout=0):
        self._clicks.append(self._name)


class _MissingCheckbox:
    @property
    def first(self):
        return self

    def count(self):
        return 0


class _Frame:
    def __init__(self, url, locator):
        self.url = url
        self._locator = locator

    def locator(self, selector):
        return self._locator


class _TurnstilePage:
    url = "https://operator.example/login"

    def __init__(self):
        self.clicks = []
        self._page_checkbox = _Checkbox(self.clicks, "in-page")
        self._frame_checkbox = _Checkbox(self.clicks, "same-origin-frame")
        self.frames = [_Frame(self.url, self._frame_checkbox), _Frame("https://foreign.example/widget", _Checkbox(self.clicks, "foreign-frame"))]

    @property
    def candidate_count(self):
        return 2

    def locator(self, selector):
        return self._page_checkbox if "cf-turnstile" in selector else _MissingCheckbox()


def test_enabled_blank_key_ticks_exactly_one_local_turnstile_checkbox():
    page = _TurnstilePage()
    assert page.candidate_count == 2, "fixture must expose page and same-origin-frame shapes"
    clicked = _try_turnstile_one_click(page, {"turnstile_one_click": True, "captcha_api_key": ""})
    assert clicked is True
    assert page.clicks == ["in-page"]
    assert len(page.clicks) == 1


def test_disabled_or_keyed_config_never_ticks_the_checkbox():
    disabled = _TurnstilePage()
    keyed = _TurnstilePage()
    assert _try_turnstile_one_click(disabled, {"turnstile_one_click": False, "captcha_api_key": ""}) is False
    assert _try_turnstile_one_click(keyed, {"turnstile_one_click": True, "captcha_api_key": "synthetic-key"}) is False
    assert disabled.clicks == []
    assert keyed.clicks == []


# ── lens bounce (VERDICT-correctness REFUTE): the four items ─────────────────

class _FramesOnlyPage:
    """No in-page checkbox; the FOREIGN frame comes first and carries one,
    the same-origin frame second.  The only lawful click is the second."""

    url = "https://operator.example/login"

    def __init__(self):
        self.clicks = []
        self.frames = [
            _Frame("https://foreign.example/widget", _Checkbox(self.clicks, "foreign-frame")),
            _Frame(self.url, _Checkbox(self.clicks, "same-origin-frame")),
        ]

    def locator(self, selector):
        return _MissingCheckbox()


def test_frames_only_page_clicks_the_same_origin_frame_and_never_the_foreign_one_first():
    page = _FramesOnlyPage()
    assert page.locator(_TURNSTILE_CHECKBOX).first.count() == 0, "fixture: no in-page checkbox"
    assert page.frames[0].url.startswith("https://foreign.example"), "fixture: foreign frame FIRST"
    clicked = _try_turnstile_one_click(page, {"turnstile_one_click": True, "captcha_api_key": ""})
    assert clicked is True
    assert page.clicks == ["same-origin-frame"], page.clicks


def test_a_page_whose_only_checkbox_is_in_a_foreign_frame_is_left_alone():
    page = _FramesOnlyPage()
    page.frames = page.frames[:1]  # foreign frame only
    assert _try_turnstile_one_click(page, {"turnstile_one_click": True, "captcha_api_key": ""}) is False
    assert page.clicks == []


def test_the_affordance_is_opt_in_everywhere_the_key_is_declared():
    from bulk_downloader.app_kernel import DEFAULTS
    from bulk_downloader import site_editor
    assert DEFAULTS["turnstile_one_click"] is False
    kind, _label = site_editor._FIELD_TYPES["turnstile_one_click"]
    assert kind == "boolean"
    # a site record that never mentions the key gets the opt-in default
    assert _try_turnstile_one_click(_TurnstilePage(), dict(DEFAULTS)) is False


# ── the call site itself, through do_login on a real page ─────────────────────
# The lens battery escaped two seams the unit tests cannot see: inverting the
# branch at the call site (the log line is its only body) and an early return
# at do_login's definition.  Both are caught only by driving do_login.

import contextlib
import http.server
import threading
from urllib.parse import parse_qs, urlsplit

_LOGIN_HTML = b"""<!doctype html><html><body>
<form id="f" method="get" action="/members">
  <input id="username" name="username" type="text">
  <input id="password" name="password" type="password">
  <div class="cf-turnstile">
    <input id="ts" type="checkbox" onchange="document.getElementById('tok').value='ticked'">
    <input id="tok" type="hidden" name="cf-turnstile-response" value="">
  </div>
  <button id="submit" type="submit">Submit</button>
</form>
</body></html>"""


def _turnstile_handler():
    class Handler(http.server.BaseHTTPRequestHandler):
        requests: list = []

        def do_GET(self):
            type(self).requests.append(self.path)
            body = (b"<html><body><h1>members</h1></body></html>"
                    if self.path.startswith("/members") else _LOGIN_HTML)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_a):
            pass
    return Handler


@contextlib.contextmanager
def _serving(handler_cls):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown(); server.server_close(); t.join(timeout=5)


def _do_login_on(monkeypatch, base_url, *, one_click):
    import pytest
    from bulk_downloader import cloak
    from bulk_downloader.login_impl import submit as submit_impl
    from playwright.sync_api import sync_playwright

    def launch_for_test(*, headless=True, args=None, config=None, **kw):
        pw = sync_playwright().start()
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception as e:  # a launch failure is a FAILURE, not a skip (T5)
            pw.stop()
            pytest.fail(f"chromium not launchable here: {e}")
        return browser, pw, "playwright-test"

    monkeypatch.setattr(cloak, "launch_browser", launch_for_test)
    monkeypatch.setattr(submit_impl, "USER_FIELD_FALLBACKS", [])
    monkeypatch.setattr(submit_impl, "PASS_FIELD_FALLBACKS", [])
    monkeypatch.setattr(submit_impl, "SUBMIT_FALLBACKS", [])
    monkeypatch.setattr(submit_impl, "_try_check_remember_me", lambda page: False)
    monkeypatch.setattr(submit_impl.time, "sleep", lambda s: None)
    config = {
        "login_url": base_url + "/login", "username": "u", "password": "p",
        "user_field": "#username", "pass_field": "#password", "submit_btn": "#submit",
        "success_url": "/members", "wait": 0, "use_real_chrome": False,
        "use_stealth": False, "use_stealth_library": False,
        "turnstile_one_click": one_click, "captcha_api_key": "",
    }
    return submit_impl.do_login(config, allow_manual_takeover=False)


def _token_seen_by_server(handler_cls):
    toks = []
    for path in handler_cls.requests:
        u = urlsplit(path)
        if u.path == "/members":
            toks.append(parse_qs(u.query).get("cf-turnstile-response", [""])[0])
    return toks


def test_do_login_ticks_the_local_checkbox_once_and_says_so(monkeypatch, capfd):
    handler = _turnstile_handler()
    with _serving(handler) as base:
        ok, reason, _cookies = _do_login_on(monkeypatch, base, one_click=True)
    err = capfd.readouterr().err
    assert ok is True, reason
    assert "login: clicked one local Turnstile checkbox" in err, err[-600:]
    assert _token_seen_by_server(handler) == ["ticked"], handler.requests


def test_negative_control_opt_out_leaves_the_checkbox_and_the_log_alone(monkeypatch, capfd):
    handler = _turnstile_handler()
    with _serving(handler) as base:
        ok, reason, _cookies = _do_login_on(monkeypatch, base, one_click=False)
    err = capfd.readouterr().err
    assert ok is True, reason  # the form still submits; only the tick is withheld
    assert "clicked one local Turnstile checkbox" not in err, err[-600:]
    assert _token_seen_by_server(handler) == [""], handler.requests
