"""Row 373 -- a hidden login form is still a known login form.

The fixture deliberately mirrors the measured kink.com shape without making a
live request: the complete form is in the DOM but has no box until the visible
one of two identical trigger selectors is clicked.  The click counter is
carried through the local form submission URL so the assertions survive
``do_login`` closing its browser.
"""

from __future__ import annotations

import contextlib
import http.server
import json
import threading
from urllib.parse import parse_qs, urlsplit

import pytest


BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.capture_serial


_TRIGGER_SELECTOR = "[data-row373-login-trigger]"


def _login_html(
    *, hidden: bool, trigger_reveals: bool = True, username_present: bool = True
) -> bytes:
    form_style = "display:none" if hidden else ""
    reveal_parts = []
    if trigger_reveals:
        reveal_parts.append(
            "document.getElementById('loginPopup').style.display = 'block';"
        )
        if not username_present:
            reveal_parts.append(
                "document.getElementById('loginPopup').insertAdjacentHTML("
                "'afterbegin', '<input id=\"username\" name=\"username\" "
                "type=\"text\">');"
            )
    reveal = "\n  ".join(reveal_parts)
    username = (
        '<input id="username" name="username" type="text">'
        if username_present
        else ""
    )
    return f"""<!doctype html>
<html><body>
<script>
window.loginTriggerFired = 0;
function openLogin(event) {{
  event.preventDefault();
  window.loginTriggerFired += 1;
  {reveal}
}}
function submitLogin(event) {{
  event.preventDefault();
  window.location.href = '/members?trigger_fired=' + window.loginTriggerFired;
}}
</script>
<nav>
  <a class="nav-login" data-row373-login-trigger style="display:none" href="#" onclick="openLogin(event)">LOG&nbsp;IN</a>
  <a class="nav-login" data-row373-login-trigger href="#" onclick="openLogin(event)">LOG&nbsp;IN</a>
</nav>
<input id="site-search" type="text" aria-label="Site search">
<form id="loginPopup" style="{form_style}" onsubmit="submitLogin(event)">
  {username}
  <input id="password" name="password" type="password">
  <button id="submit" type="submit">Submit</button>
</form>
</body></html>""".encode("utf-8")


def _handler(login_html: bytes):
    class Handler(http.server.BaseHTTPRequestHandler):
        requests: list[str] = []

        def do_GET(self):
            type(self).requests.append(self.path)
            body = (
                b"<html><body><h1>members area</h1></body></html>"
                if self.path.startswith("/members")
                else login_html
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    return Handler


@contextlib.contextmanager
def _serving(handler_cls):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _submitted_trigger_counts(handler_cls) -> list[int]:
    counts = []
    for path in handler_cls.requests:
        parsed = urlsplit(path)
        if parsed.path != "/members":
            continue
        raw = parse_qs(parsed.query).get("trigger_fired", [])
        assert len(raw) == 1, f"local submit did not carry one trigger count: {path!r}"
        counts.append(int(raw[0]))
    return counts


def _is_zero_sized(box) -> bool:
    return box is None or box["width"] == 0 or box["height"] == 0


class _ProbedPage:
    """Delegating Page wrapper that records the fixture precondition at goto."""

    def __init__(self, page, observations):
        self._page = page
        self._observations = observations

    def goto(self, *args, **kwargs):
        result = self._page.goto(*args, **kwargs)
        username = self._page.locator("#username")
        username_count = username.count()
        triggers = self._page.locator(_TRIGGER_SELECTOR)
        self._observations.append({
            "count": username_count,
            "box": username.first.bounding_box() if username_count else None,
            "trigger_count": triggers.count(),
            "trigger_boxes": [
                triggers.nth(index).bounding_box()
                for index in range(triggers.count())
            ],
            "trigger_texts": [
                triggers.nth(index).text_content()
                for index in range(triggers.count())
            ],
        })
        return result

    def __getattr__(self, name):
        return getattr(self._page, name)


class _ProbedContext:
    def __init__(self, context, observations):
        self._context = context
        self._observations = observations

    def new_page(self):
        return _ProbedPage(self._context.new_page(), self._observations)

    def __getattr__(self, name):
        return getattr(self._context, name)


class _ProbedBrowser:
    def __init__(self, browser, observations):
        self._browser = browser
        self._observations = observations

    def new_context(self, **kwargs):
        return _ProbedContext(
            self._browser.new_context(**kwargs), self._observations
        )

    def __getattr__(self, name):
        return getattr(self._browser, name)


def _headless_launch(monkeypatch, observations):
    from playwright.sync_api import sync_playwright
    from bulk_downloader import cloak

    def launch_for_test(*, headless=True, args=None, config=None, **kwargs):
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=True)
        return _ProbedBrowser(browser, observations), playwright, "playwright-test"

    monkeypatch.setattr(cloak, "launch_browser", launch_for_test)


def _do_login(
    monkeypatch,
    base_url,
    observations,
    *,
    allow_manual=False,
    login_trigger=_TRIGGER_SELECTOR,
    user_fallbacks=(),
):
    from bulk_downloader.login_impl import submit as submit_impl

    _headless_launch(monkeypatch, observations)
    # Keep this regression on the configured selectors.  Walking unrelated
    # generic fallbacks would add minutes to the unfixed RED without exercising
    # a different behavior.
    monkeypatch.setattr(submit_impl, "USER_FIELD_FALLBACKS", list(user_fallbacks))
    monkeypatch.setattr(submit_impl, "PASS_FIELD_FALLBACKS", [])
    monkeypatch.setattr(submit_impl, "SUBMIT_FALLBACKS", [])
    monkeypatch.setattr(submit_impl, "_try_check_remember_me", lambda page: False)
    monkeypatch.setattr(submit_impl.time, "sleep", lambda seconds: None)
    config = {
        "login_url": base_url + "/login",
        "username": "u",
        "password": "p",
        "user_field": "#username",
        "pass_field": "#password",
        "submit_btn": "#submit",
        "success_url": "/members",
        "wait": 0,
        "use_real_chrome": False,
        "use_stealth": False,
        "use_stealth_library": False,
    }
    if login_trigger is not None:
        config["login_trigger"] = login_trigger
    return submit_impl.do_login(
        config, allow_manual_takeover=allow_manual
    )


def _assert_fixture_probe(observation, *, username_count: int, username_visible: bool):
    assert observation["count"] == username_count
    if username_visible:
        box = observation["box"]
        assert box is not None and box["width"] > 0 and box["height"] > 0
    else:
        assert _is_zero_sized(observation["box"])

    assert observation["trigger_count"] == 2, (
        "fixture drifted: both desktop and mobile trigger matches are required"
    )
    first_box, second_box = observation["trigger_boxes"]
    assert _is_zero_sized(first_box), (
        "fixture drifted: the first/mobile trigger must be non-actionable"
    )
    assert (
        second_box is not None
        and second_box["width"] > 0
        and second_box["height"] > 0
    ), "fixture drifted: the second/desktop trigger must be actionable"
    assert observation["trigger_texts"] == ["LOG\xa0IN", "LOG\xa0IN"], (
        "fixture drifted: the measured trigger label contains U+00A0"
    )


def _manual_page_state_and_close(handles):
    playwright, browser, context = handles
    try:
        page = context.pages[0]
        username = page.locator("#username")
        count = username.count()
        return {
            "fired": page.evaluate("window.loginTriggerFired"),
            "username_count": count,
            "username_box": username.first.bounding_box() if count else None,
        }
    finally:
        browser.close()
        playwright.stop()


def test_hidden_login_form_fires_the_visible_trigger_exactly_once(monkeypatch):
    handler = _handler(_login_html(hidden=True))
    observations = []
    with _serving(handler) as base_url:
        ok, reason, _cookies = _do_login(
            monkeypatch,
            base_url,
            observations,
            user_fallbacks=["input[type='text']"],
        )

    assert len(observations) == 1
    _assert_fixture_probe(
        observations[0], username_count=1, username_visible=False
    )
    assert ok is True, reason
    assert _submitted_trigger_counts(handler) == [1]


def test_absent_username_field_fires_the_visible_trigger_exactly_once(monkeypatch):
    handler = _handler(_login_html(hidden=False, username_present=False))
    observations = []
    with _serving(handler) as base_url:
        ok, reason, _cookies = _do_login(
            monkeypatch,
            base_url,
            observations,
            user_fallbacks=["input[type='text']"],
        )

    assert len(observations) == 1
    _assert_fixture_probe(
        observations[0], username_count=0, username_visible=False
    )
    assert ok is True, reason
    assert _submitted_trigger_counts(handler) == [1]


@pytest.mark.parametrize("login_trigger", [None, "", _TRIGGER_SELECTOR])
def test_visible_login_form_fires_the_trigger_exactly_zero_times(
    monkeypatch, login_trigger
):
    handler = _handler(_login_html(hidden=False))
    observations = []
    with _serving(handler) as base_url:
        ok, reason, _cookies = _do_login(
            monkeypatch,
            base_url,
            observations,
            login_trigger=login_trigger,
        )

    assert len(observations) == 1
    _assert_fixture_probe(
        observations[0], username_count=1, username_visible=True
    )
    assert ok is True, reason
    assert _submitted_trigger_counts(handler) == [0]


@pytest.mark.parametrize("login_trigger", [None, ""])
def test_hidden_form_without_a_trigger_preserves_legacy_failure_and_zero_clicks(
    monkeypatch, login_trigger
):
    handler = _handler(_login_html(hidden=True))
    observations = []
    handles = None
    with _serving(handler) as base_url:
        status, reason, handles = _do_login(
            monkeypatch,
            base_url,
            observations,
            allow_manual=True,
            login_trigger=login_trigger,
        )
        state = _manual_page_state_and_close(handles)

    assert len(observations) == 1
    _assert_fixture_probe(
        observations[0], username_count=1, username_visible=False
    )
    assert status == "MANUAL_PENDING"
    assert reason == (
        "Couldn't find username field: could not fill username; tried 1 selectors"
    )
    assert state["fired"] == 0
    assert state["username_count"] == 1
    assert _is_zero_sized(state["username_box"])
    assert _submitted_trigger_counts(handler) == []


def test_unrevealed_login_form_has_a_trigger_specific_manual_reason(monkeypatch):
    handler = _handler(_login_html(hidden=True, trigger_reveals=False))
    observations = []
    handles = None
    with _serving(handler) as base_url:
        status, reason, handles = _do_login(
            monkeypatch, base_url, observations, allow_manual=True
        )
        state = _manual_page_state_and_close(handles)

    assert len(observations) == 1
    _assert_fixture_probe(
        observations[0], username_count=1, username_visible=False
    )
    assert status == "MANUAL_PENDING"
    assert "login form is hidden behind a trigger" in reason
    assert state["fired"] == 1
    assert state["username_count"] == 1
    assert _is_zero_sized(state["username_box"])
    assert _submitted_trigger_counts(handler) == []


def test_wrong_configured_trigger_is_not_replaced_by_a_hard_coded_selector(
    monkeypatch,
):
    handler = _handler(_login_html(hidden=True))
    observations = []
    handles = None
    with _serving(handler) as base_url:
        status, reason, handles = _do_login(
            monkeypatch,
            base_url,
            observations,
            allow_manual=True,
            login_trigger="[data-row373-wrong-trigger]",
        )
        state = _manual_page_state_and_close(handles)

    assert len(observations) == 1
    _assert_fixture_probe(
        observations[0], username_count=1, username_visible=False
    )
    assert status == "MANUAL_PENDING"
    assert "login form is hidden behind a trigger" in reason
    assert state["fired"] == 0
    assert _is_zero_sized(state["username_box"])
    assert _submitted_trigger_counts(handler) == []


def test_replay_fires_the_visible_trigger_before_filling(monkeypatch):
    from playwright.sync_api import sync_playwright
    from bulk_downloader import selector_chains
    from bulk_downloader.login_impl.replay import _attempt_headless_fill_submit

    # Keep the real selector-chain behavior while shortening only its failure
    # timeout, so the unfixed hidden-field path reports promptly.
    parse_chain = selector_chains.parse_chain

    def quick_chain(raw):
        steps = parse_chain(raw)
        for step in steps:
            step.timeout_ms = 100
        return steps

    monkeypatch.setattr(selector_chains, "parse_chain", quick_chain)
    handler = _handler(_login_html(hidden=True))
    with _serving(handler) as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(base_url + "/login", wait_until="domcontentloaded")
            username = page.locator("#username")
            count_before = username.count()
            box_before = username.first.bounding_box()
            triggers = page.locator(_TRIGGER_SELECTOR)
            probe_before = {
                "count": count_before,
                "box": box_before,
                "trigger_count": triggers.count(),
                "trigger_boxes": [
                    triggers.nth(index).bounding_box()
                    for index in range(triggers.count())
                ],
                "trigger_texts": [
                    triggers.nth(index).text_content()
                    for index in range(triggers.count())
                ],
            }
            ok, reason = _attempt_headless_fill_submit(
                page,
                {
                    "username": "u",
                    "password": "p",
                    "user_field": "#username",
                    "pass_field": "#password",
                    "submit_btn": "#submit",
                    "login_trigger": _TRIGGER_SELECTOR,
                },
                timeout=1,
            )
        finally:
            browser.close()

    _assert_fixture_probe(
        probe_before, username_count=1, username_visible=False
    )
    assert ok is True, reason
    assert _submitted_trigger_counts(handler) == [1]


def test_login_trigger_is_plain_string_config_on_create_and_put(
    fresh_app, clean_workdir
):
    from bulk_downloader import app as app_module

    created = fresh_app.post(
        "/api/sites",
        json={"name": "trigger fixture", "login_trigger": "a.nav-login"},
    )
    assert created.status_code == 200, created.get_json()
    site_id = created.get_json()["id"]
    assert "login_trigger" in app_module.CFG_FIELDS
    assert app_module.s_cfg[site_id]["login_trigger"] == "a.nav-login"

    updated = fresh_app.put(
        f"/api/sites/{site_id}",
        json={"login_trigger": "header a.nav-login"},
    )
    assert updated.status_code == 200, updated.get_json()
    assert app_module.s_cfg[site_id]["login_trigger"] == "header a.nav-login"

    persisted = json.loads((clean_workdir / "sites_config.json").read_text())
    assert persisted[site_id]["login_trigger"] == "header a.nav-login"

    for runner in app_module.runners.values():
        runner.stop()
        runner._stop_auto_retry()
    app_module.runners.clear()
    app_module.s_cfg.clear()
    app_module.s_meta.clear()
    app_module._load_sites_config()
    assert app_module.s_cfg[site_id]["login_trigger"] == "header a.nav-login"
