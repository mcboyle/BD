"""Row 775: one real Turnstile tick is explicit, local, and fail-closed."""

from bulk_downloader import runner_challenge
from bulk_downloader.runner_challenge import ChallengeMixin


BD_GATE_SCOPE = "repo-wide"


class _Checkbox:
    def __init__(self):
        self.clicks = 0

    def count(self):
        return 1

    def click(self, timeout=0):
        self.clicks += 1


class _Missing:
    def count(self):
        return 0


class _Page:
    def __init__(self, checkbox, frames=(), url="https://operator.example/login"):
        self.checkbox = checkbox
        self.frames = list(frames)
        self.url = url

    def locator(self, selector):
        if selector == ".cf-turnstile input[type='checkbox']":
            return self.checkbox
        return _Missing()


class _Frame:
    def __init__(self, url, checkbox):
        self.url = url
        self.checkbox = checkbox

    def locator(self, selector):
        if selector == "input[type='checkbox']":
            return self.checkbox
        return _Missing()


class _Challenge(ChallengeMixin):
    def __init__(self, config):
        self.config = config
        self.events = []

    def log_event(self, *args, **kwargs):
        self.events.append((args, kwargs))


class _HandlingChallenge(_Challenge):
    def __init__(self, config):
        super().__init__(config)
        self.captcha_checks = 0

    def _has_captcha(self, page):
        self.captcha_checks += 1
        return self.captcha_checks == 1


def test_enabled_without_solver_clicks_one_in_page_turnstile_checkbox():
    """Removing the enabled branch must leave the operator's own checkbox unticked."""
    checkbox = _Checkbox()
    runner = _Challenge({"turnstile_one_click_enabled": True, "captcha_api_key": ""})

    assert runner._try_captcha_solve(_Page(checkbox)) is True
    assert checkbox.clicks == 1
    assert len(runner.events) == 1


def test_enabled_without_solver_clicks_one_same_origin_frame_checkbox():
    """A same-origin frame is a real checkbox surface, unlike a foreign frame."""
    in_page = _Missing()
    same_origin = _Checkbox()
    foreign = _Checkbox()
    page = _Page(in_page, (
        _Frame("https://operator.example/cdn-cgi/challenge-platform/h/g", same_origin),
        _Frame("https://foreign.example/challenge", foreign),
    ))
    runner = _Challenge({"turnstile_one_click_enabled": True, "captcha_api_key": ""})

    assert runner._try_captcha_solve(page) is True
    assert same_origin.clicks == 1
    assert foreign.clicks == 0
    assert len(runner.events) == 1


def test_enabled_never_clicks_a_same_origin_non_challenge_frame():
    """Origin alone is insufficient: the frame must be Cloudflare's challenge path."""
    checkbox = _Checkbox()
    page = _Page(_Missing(), (
        _Frame("https://operator.example/anything", checkbox),
    ))
    runner = _Challenge({"turnstile_one_click_enabled": True, "captcha_api_key": ""})

    assert runner._try_captcha_solve(page) is False
    assert checkbox.clicks == 0


def test_enabled_never_clicks_a_foreign_challenge_frame():
    """Challenge path alone is insufficient: the frame must share the page origin."""
    checkbox = _Checkbox()
    page = _Page(_Missing(), (
        _Frame("https://foreign.example/cdn-cgi/challenge-platform/h/g", checkbox),
    ))
    runner = _Challenge({"turnstile_one_click_enabled": True, "captcha_api_key": ""})

    assert runner._try_captcha_solve(page) is False
    assert checkbox.clicks == 0


def test_absent_one_click_flag_defaults_closed_without_a_solver_key():
    """An operator who never sets the flag receives no browser click."""
    checkbox = _Checkbox()

    assert _Challenge({})._try_captcha_solve(_Page(checkbox)) is False
    assert checkbox.clicks == 0


def test_takeover_flag_never_enables_a_one_click():
    """The unrelated takeover flag cannot stand in for the explicit one-click flag."""
    checkbox = _Checkbox()

    assert _Challenge({"captcha_takeover_enabled": True})._try_captcha_solve(
        _Page(checkbox)) is False
    assert checkbox.clicks == 0


def test_legacy_turnstile_alias_reaches_the_one_click_path():
    """The retained compatibility entrypoint must not bypass the new affordance."""
    checkbox = _Checkbox()
    runner = _Challenge({"turnstile_one_click_enabled": True, "captcha_api_key": ""})

    assert runner._try_turnstile_solve(_Page(checkbox)) is True
    assert checkbox.clicks == 1


def test_captcha_handler_reaches_the_one_click_path(monkeypatch):
    """The real handler call must retain the affordance, not only its helper."""
    monkeypatch.setattr(runner_challenge.time, "sleep", lambda _seconds: None)
    checkbox = _Checkbox()
    runner = _HandlingChallenge({"turnstile_one_click_enabled": True, "captcha_api_key": ""})

    assert runner._handle_captcha_check(_Page(checkbox), "https://operator.example/login") is True
    assert checkbox.clicks == 1


def test_disabled_or_solver_configured_issues_no_checkbox_click():
    """The safety switch and solver path must not manufacture a browser tick."""
    disabled = _Checkbox()
    configured = _Checkbox()

    assert _Challenge({"turnstile_one_click_enabled": False})._try_captcha_solve(
        _Page(disabled)) is False
    assert disabled.clicks == 0
    assert _Challenge({"turnstile_one_click_enabled": True, "captcha_api_key": "key"})._try_captcha_solve(
        _Page(configured)) is False
    assert configured.clicks == 0


def test_both_flag_readers_parse_the_operator_value_and_default_closed():
    """Self-mutation seam (_captcha_takeover_enabled -> _truthy): each flag
    reader returns the PARSED operator value -- never a constant -- and
    defaults closed when the key is absent or the config is None."""
    from bulk_downloader.runner_challenge import (_captcha_takeover_enabled,
                                                  _truthy, _turnstile_one_click_enabled)
    for reader, key in ((_captcha_takeover_enabled, "captcha_takeover_enabled"),
                        (_turnstile_one_click_enabled, "turnstile_one_click_enabled")):
        assert reader(None) is False and reader({}) is False
        for on in (True, 1, "1", "true", " Yes ", "on"):
            assert reader({key: on}) is True, (reader, on)
        for off in (False, 0, "0", "false", "no", "off", "", None):
            assert reader({key: off}) is False, (reader, off)
    assert _truthy(2.5) is True and _truthy([]) is False and _truthy(["x"]) is True
