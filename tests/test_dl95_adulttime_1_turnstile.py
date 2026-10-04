"""Saved journal state: a clicked Turnstile stays on Just a moment.

Source: UIUX-20260928/download-95/B5-B/p1/adulttime/journal-login.txt.
The clock and checkbox boundary are fixtures; page detection and wait are real.
No browser, service, or site traffic is used.
"""
import ast
import inspect
from types import SimpleNamespace

import pytest
from bulk_downloader.login_impl import submit

BD_GATE_SCOPE = "module"


class Clock:
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        assert 0 < seconds <= 1
        self.now += seconds


class Page:
    def __init__(self, clock, clears_at=None, challenge=True, foreign=False):
        self.clock = clock
        self.clears_at = clears_at
        self.challenge = challenge
        self.foreign = foreign
        self.clicks = 0
        self.probes = 0
        self.url = "https://adulttime.fixture.test/en/login"
        self.frames = [SimpleNamespace(url="https://challenges.cloudflare.com/fixture")]

    @property
    def stuck(self):
        return self.challenge and (
            self.clears_at is None or self.clock.now < self.clears_at)

    def title(self):
        self.probes += 1
        return "Just a moment..." if self.stuck else "Login"

    def locator(self, selector):
        if "hcaptcha" in selector or "recaptcha" in selector:
            count = int(self.foreign)
        elif "input[type=password]" in selector:
            count = int(not self.stuck)
        elif "challenges.cloudflare.com" in selector or selector == ".cf-turnstile":
            count = int(self.stuck and not self.foreign)
        else:
            count = 0
        return SimpleNamespace(count=lambda: count)

    def evaluate(self, _script):
        return None


@pytest.fixture
def journal_page(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(submit, "time", clock)

    def click(page):
        assert page.stuck and not page.foreign
        page.clicks += 1
        return True

    monkeypatch.setattr(submit, "_click_turnstile_checkbox", click)
    return clock


def test_clicked_interstitial_can_clear_after_old_window(journal_page):
    page = Page(journal_page, clears_at=40)
    assert submit._is_challenge_interstitial(page)
    assert submit.clear_cloudflare_challenge(page), "TURNSTILE_SETTLE_WINDOW_TOO_SHORT"
    assert 40 <= journal_page.now <= 90
    assert page.clicks in (1, 2)
    assert page.probes >= 40
    assert not submit._is_challenge_interstitial(page)


def test_quick_clear_positive_control(journal_page):
    page = Page(journal_page, clears_at=2)
    assert submit.clear_cloudflare_challenge(page)
    assert journal_page.now == 2
    assert page.clicks == 1


def test_stuck_page_respects_explicit_bound(journal_page):
    page = Page(journal_page)
    assert not submit.clear_cloudflare_challenge(page, wait=2, max_rounds=2)
    assert journal_page.now == 4
    assert page.clicks == 2
    assert submit._is_challenge_interstitial(page)


def test_non_challenge_never_waits_or_clicks(journal_page):
    page = Page(journal_page, challenge=False)
    assert not submit.clear_cloudflare_challenge(page)
    assert journal_page.now == 0
    assert page.clicks == 0


def test_cancellation_interrupts_settle_window(journal_page):
    page = Page(journal_page)
    with submit.login_abort_check(
        lambda: "cancelled by operator" if journal_page.now >= 1 else ""
    ):
        assert not submit.clear_cloudflare_challenge(page, wait=2, max_rounds=2)
    assert journal_page.now == 1, "TURNSTILE_WAIT_IGNORES_CANCEL"
    assert page.clicks == 1


def test_cancellation_between_rounds_never_clicks_again(journal_page):
    page = Page(journal_page)
    with submit.login_abort_check(
        lambda: "cancelled by operator" if journal_page.now >= 2 else ""
    ):
        assert not submit.clear_cloudflare_challenge(page, wait=2, max_rounds=2)
    assert journal_page.now == 2
    assert page.clicks == 1, "TURNSTILE_CLICKED_AFTER_CANCEL"


class LateWidgetPage(Page):
    """No widget at round start; the Turnstile iframe appears at t>=3."""

    def __init__(self, clock):
        super().__init__(clock)
        self.frames = []

    def locator(self, selector):
        if "challenges.cloudflare.com" in selector and self.clock.now >= 3 and self.stuck:
            return SimpleNamespace(count=lambda: 1)
        if ("hcaptcha" in selector or "recaptcha" in selector
                or "challenges.cloudflare.com" in selector or selector == ".cf-turnstile"):
            return SimpleNamespace(count=lambda: 0)
        return super().locator(selector)


def test_cancellation_interrupts_auto_verify_wait(journal_page, monkeypatch):
    monkeypatch.setattr(submit, "_click_human_button", lambda page: False)
    page = LateWidgetPage(journal_page)
    with submit.login_abort_check(
        lambda: "cancelled by operator" if journal_page.now >= 1 else ""
    ):
        assert not submit.clear_cloudflare_challenge(page, wait=30, max_rounds=2)
    assert page.clicks == 0, "TURNSTILE_CLICKED_AFTER_CANCEL"
    assert journal_page.now == 1, "TURNSTILE_WAIT_IGNORES_CANCEL"


def test_late_widget_click_positive_control(journal_page, monkeypatch):
    monkeypatch.setattr(submit, "_click_human_button", lambda page: False)
    page = LateWidgetPage(journal_page)
    assert not submit.clear_cloudflare_challenge(page, wait=2, max_rounds=1)
    assert page.clicks == 1


def test_human_check_window_status_names_the_action(monkeypatch):
    from bulk_downloader import human_challenge, login, session_keeper
    from bulk_downloader.runner_auth import AuthMixin

    class Session:
        def __init__(self, url, user_data_dir, user_agent=None):
            self.url, self.domain = url, "adulttime.fixture.test"

        def prepare(self):
            return []

        def start(self):
            return None

    monkeypatch.setattr(human_challenge, "enabled", lambda config: True)
    monkeypatch.setattr(human_challenge, "PlainChallengeSession", Session)
    monkeypatch.setattr(login, "cancel_manual_login", lambda handle: None)
    monkeypatch.setattr(session_keeper, "pause_site_keepers", lambda site_id: None)
    statuses = []
    runner = SimpleNamespace(
        site_id="adulttime-fixture", config={}, _manual_profile_dir=lambda: "manual",
        _set_login_status=statuses.append, log_event=lambda *args, **kwargs: None,
        _human_challenge_wait=lambda session: None)
    handle = (None, None, SimpleNamespace(pages=[Page(Clock())]))
    assert AuthMixin._maybe_start_human_challenge(runner, handle) is True
    assert statuses[-1].startswith("⏳ ACTION REQUIRED: turnstile-not-cleared; "), (
        "HUMAN_CHECK_ACTION_NOT_NAMED", statuses)
    assert "tick the Cloudflare box" in statuses[-1]


@pytest.fixture
def runner_case(clean_workdir, monkeypatch):
    from bulk_downloader import captcha_relay, runner_auth, session_keeper
    from bulk_downloader.db import db_init
    from bulk_downloader.runner import SiteRunner

    db_init()
    captcha_relay._reset_for_tests()
    monkeypatch.setattr(captcha_relay, "_maybe_push", lambda *args: None)
    runner = SiteRunner("adulttime-fixture", {
        "login_url": "https://adulttime.fixture.test/en/login",
        "auto_teach_first_run": False,
        "learned": {"login": {"user_field": ["#username"]}},
        "manual_use_persistent_profile": False,
        session_keeper.LOGIN_CAP_KEY: 20,
    })
    opened = []
    runner.start_manual_login = lambda: (opened.append(1), (True, "window"))[1]
    monkeypatch.setattr(runner, "_maybe_start_human_challenge", lambda handle: False)
    callbacks = []

    def run(verdict, relay=False, allow_manual=False):
        runner.config["use_captcha_relay"] = relay
        monkeypatch.setattr(runner_auth, "do_login", lambda *args, **kwargs: verdict)
        runner.login_async(on_done=callbacks.append, allow_manual=allow_manual)
        runner._login_thread.join(timeout=3)
        assert not runner._login_thread.is_alive(), "FIXTURE_LOGIN_DID_NOT_SETTLE"
        assert len(callbacks) == 1
        return runner._login_status, captcha_relay.get_pending(runner.config["login_url"])

    yield runner, run, opened, callbacks, captcha_relay
    captcha_relay._reset_for_tests()


CF_REASON = submit.LOGIN_TURNSTILE_UNCLEARED_PREFIX + " -- log in with Take Over to pass it"


def test_do_login_names_the_uncleared_challenge_by_the_shared_prefix():
    tree = ast.parse(inspect.getsource(submit.do_login).lstrip())
    values = [node.value for node in ast.walk(tree) if isinstance(node, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == "_cf" for t in node.targets)]
    assert len(values) == 1, "TURNSTILE_PRODUCER_NOT_FOUND"
    assert isinstance(values[0], ast.Name), "TURNSTILE_REASON_NOT_SHARED_PREFIX"
    assert values[0].id == "LOGIN_TURNSTILE_UNCLEARED_PREFIX", "TURNSTILE_REASON_NOT_SHARED_PREFIX"


@pytest.mark.parametrize("relay", [False, True, "false"])
def test_uncleared_challenge_names_action_and_configured_relay(runner_case, relay):
    runner, run, opened, callbacks, _relay = runner_case
    status, pending = run((False, CF_REASON, []), relay=relay)
    assert "ACTION REQUIRED: turnstile-not-cleared" in status, "TURNSTILE_GENERIC_LOGIN_FAILURE"
    assert callbacks == [False]
    assert opened == []
    if relay is True:
        assert pending is not None, "TURNSTILE_RELAY_NOT_REGISTERED"
        assert pending["site_id"] == runner.site_id
        assert pending["captcha_type"] == "turnstile"
        assert pending["status"] == "pending"
        assert "captcha relay pending" in status
    else:
        assert pending is None


def test_manual_handoff_keeps_handle_and_precise_reason(runner_case):
    runner, run, opened, callbacks, _relay = runner_case
    handle = object()
    status, pending = run(("MANUAL_PENDING", CF_REASON, handle), allow_manual=True)
    assert "ACTION REQUIRED: turnstile-not-cleared" in status, "TURNSTILE_HANDOFF_LOST_REASON"
    assert runner._manual_login_handle is handle
    assert callbacks == [False]
    assert opened == []
    assert pending is None


@pytest.mark.parametrize("verdict", [(False, "Incorrect password", []), (True, "Logged in", [])])
def test_unrelated_login_result_never_registers_relay(runner_case, verdict):
    _runner, run, opened, callbacks, _relay = runner_case
    status, pending = run(verdict, relay=True)
    assert "turnstile-not-cleared" not in status
    assert pending is None
    assert callbacks == [verdict[0]]
    assert opened == []


def test_relay_refusal_never_promises_pending(runner_case, monkeypatch):
    _runner, run, opened, callbacks, relay = runner_case
    monkeypatch.setattr(relay, "mark_captcha_needed", lambda *args, **kwargs: None)
    status, pending = run((False, CF_REASON, []), relay=True)
    assert "ACTION REQUIRED: turnstile-not-cleared" in status
    assert "captcha relay unavailable" in status
    assert "captcha relay pending" not in status
    assert pending is None
    assert opened == []
    assert callbacks == [False]


@pytest.mark.parametrize("manual", [False, True])
def test_withdrawn_challenge_never_registers_relay(runner_case, monkeypatch, manual):
    from bulk_downloader import login

    runner, run, opened, callbacks, _relay = runner_case
    cancelled = []
    monkeypatch.setattr(login, "cancel_manual_login", cancelled.append)
    monkeypatch.setattr(runner, "_relogin_abort_reason", lambda: "operator cancelled")
    handle = object()
    verdict = ("MANUAL_PENDING", CF_REASON, handle) if manual else (False, CF_REASON, [])
    status, pending = run(verdict, relay=True, allow_manual=manual)
    assert "Login cancelled before submit: operator cancelled" in status
    assert "turnstile-not-cleared" not in status
    assert pending is None
    assert cancelled == ([handle] if manual else [])
    assert opened == []
    assert callbacks == [False]


def test_relay_transport_error_keeps_precise_action(runner_case, monkeypatch):
    _runner, run, opened, callbacks, relay = runner_case

    def unavailable(*args, **kwargs):
        raise OSError("fixture relay unavailable")

    monkeypatch.setattr(relay, "mark_captcha_needed", unavailable)
    status, pending = run((False, CF_REASON, []), relay=True)
    assert "ACTION REQUIRED: turnstile-not-cleared" in status
    assert "captcha relay unavailable (OSError)" in status
    assert pending is None
    assert opened == []
    assert callbacks == [False]
