"""Row 721: an age-gate interstitial is recognised by CONTENT and its ENTER
control is clicked, while the EXIT denylist refusal stays exactly as it was.

The measured page is the Gamma Billing age wall served at ``/login-abused``:
"ACCESS BEYOND THIS PAGE IS RESTRICTED TO ADULTS" with EXIT HERE and ENTER
buttons.  The product already refuses EXIT (the safety half).  What it had no
affordance for is ENTER, so the wall was neither dismissed nor reported as
dismissible.

Recognition is deliberately CONTENT-based, never URL shape: 18-plus language
together with BOTH an ENTER and an EXIT affordance.  A bare "ENTER" on a page
with no adult-content language is not authority to click anything, which is
what keeps a genuine abuse response from being clicked through.
"""

from __future__ import annotations

import pytest

from bulk_downloader import interstitial


BD_GATE_SCOPE = "module"


GATE_TEXT = (
    "ACCESS BEYOND THIS PAGE IS RESTRICTED TO ADULTS\n"
    "You must be 18 years or older to enter this site."
)
ABUSE_TEXT = (
    "Your IP was blocked! Please try later or call support.\n"
    "Too many failed login attempts were recorded."
)


# A real age wall carries more than its two gate controls. This one is the
# hazard the ENTER admission must never widen onto: pressing it is a purchase,
# not a gate. Zero-entropy fixture prose, not a real product or price.
PURCHASE_LABEL = "JOIN NOW - $29.95/month"


class _Control:
    def __init__(self, label, *, next_text=None, destination=""):
        self.label = label
        self.next_text = next_text
        self.destination = destination


class _ControlLocator:
    def __init__(self, page, control):
        self._page = page
        self._control = control

    @property
    def first(self):
        return self

    def is_visible(self):
        return self._control in self._page.controls

    def inner_text(self, **_kwargs):
        return self._control.label

    def get_attribute(self, name, **_kwargs):
        return None

    def click(self, **_kwargs):
        self._page.clicked.append(self._control.label)
        if self._control.next_text is not None:
            self._page.text = self._control.next_text
            self._page.controls = []
        if self._control.destination:
            self._page.url = self._control.destination


class _ControlList:
    def __init__(self, page):
        self._page = page

    def count(self):
        return len(self._page.controls)

    def nth(self, index):
        return _ControlLocator(self._page, self._page.controls[index])


class _GatePage:
    """A duck-typed page whose body text and controls are the whole fixture."""

    def __init__(self, url, text, controls, *, text_error=None,
                 text_error_after_click=None):
        self.url = url
        self.text = text
        self.controls = list(controls)
        self.clicked = []
        self.gotos = []
        self.text_error = text_error
        self.text_error_after_click = text_error_after_click
        self.text_reads = 0

    def locator(self, selector):
        if selector == interstitial.GENERIC_CONTROL_SELECTOR:
            return _ControlList(self)
        return _ControlLocator(self, _Control(""))

    def inner_text(self, selector, **_kwargs):
        assert selector == "body"
        self.text_reads += 1
        if self.text_error is not None:
            raise self.text_error
        if self.clicked and self.text_error_after_click is not None:
            raise self.text_error_after_click
        return self.text

    def wait_for_load_state(self, **_kwargs):
        return None

    def goto(self, url, **_kwargs):
        self.gotos.append(url)
        self.url = url

    def go_back(self, **_kwargs):
        return None


def _age_gate_page(**kwargs):
    return _GatePage(
        "https://gamma.example/login-abused",
        GATE_TEXT,
        [_Control("EXIT HERE"), _Control("ENTER", next_text="Members area"),
         _Control(PURCHASE_LABEL)],
        **kwargs,
    )


def _run(page):
    return interstitial.dismiss_gates(
        page, "", destination_url=page.url, settle_s=0.0, sleep=lambda _s: None
    )


def _outcomes(actions, outcome):
    return [a for a in actions if a.get("outcome") == outcome]


def test_precondition_fixture_is_an_age_gate_with_both_affordances():
    """The RED corpus must actually contain the hazard it is about."""
    page = _age_gate_page()
    labels = [c.label for c in page.controls]
    assert labels == ["EXIT HERE", "ENTER", PURCHASE_LABEL]
    assert "RESTRICTED TO ADULTS" in page.text
    # No shipped generic tier pattern accepts a bare "ENTER"; that is the gap.
    assert not any(pattern.fullmatch("ENTER")
                   for _tier, pattern in interstitial._GENERIC_TIERS)


def test_age_gate_enter_is_clicked_exactly_once():
    page = _age_gate_page()
    actions = _run(page)
    assert page.clicked == ["ENTER"], (
        "the age-gate ENTER control was not clicked; clicked=%r" % (page.clicked,)
    )
    cleared = _outcomes(actions, "cleared")
    assert len(cleared) == 1, "expected exactly one cleared action, got %r" % (
        [a.get("outcome") for a in actions],
    )
    assert cleared[0]["tier"] == "age"
    assert cleared[0]["label"] == "ENTER"
    # The wall's other control is a purchase. Admission must be exactly one
    # label wide, so this is asserted in the SAME round as the ENTER click.
    assert PURCHASE_LABEL not in page.clicked
    assert len(page.clicked) == 1


def test_exit_denylist_refusal_is_unchanged_in_the_same_round():
    """The safety half must be proven in the SAME round as the ENTER click."""
    page = _age_gate_page()
    actions = _run(page)
    refused = _outcomes(actions, "refused")
    assert len(refused) == 1, "expected exactly one refusal, got %r" % (refused,)
    assert refused[0]["label"] == "EXIT HERE"
    assert refused[0]["reason"] == (
        "denylisted label matched 'exit'; control not used"
    )
    assert "EXIT HERE" not in page.clicked


def test_recognition_is_by_content_not_url_shape():
    """Same /login-abused URL, no adult-content language: nothing is clicked."""
    page = _GatePage(
        "https://gamma.example/login-abused",
        ABUSE_TEXT,
        [_Control("EXIT HERE"), _Control("ENTER")],
    )
    actions = _run(page)
    assert page.clicked == [], (
        "an abuse response was clicked through: %r" % (page.clicked,)
    )
    assert _outcomes(actions, "cleared") == []


def test_enter_needs_an_exit_affordance_too():
    """18-plus language plus a lone ENTER is not an age gate."""
    page = _GatePage(
        "https://gamma.example/login-abused", GATE_TEXT, [_Control("ENTER")]
    )
    assert _run(page) == [] or page.clicked == []
    assert page.clicked == []


def test_unreadable_body_text_is_unknown_and_refuses_the_control():
    """An unavailable measurement is never permission (A2)."""
    page = _age_gate_page(text_error=RuntimeError("detached frame"))
    actions = _run(page)
    assert page.clicked == []
    unknown = _outcomes(actions, "measurement_unknown")
    assert len(unknown) == 1, "expected one UNKNOWN, got %r" % (
        [a.get("outcome") for a in actions],
    )
    assert "age gate content UNKNOWN" in unknown[0]["reason"]
    assert interstitial.first_safety_unknown(actions) is unknown[0]


def test_abuse_response_behind_the_gate_is_reported_as_a_block():
    """The block page sits BEHIND the gate, so it is a post-ENTER content read."""
    page = _GatePage(
        "https://gamma.example/login-abused",
        GATE_TEXT,
        [_Control("EXIT HERE"), _Control("ENTER", next_text=ABUSE_TEXT)],
    )
    actions = _run(page)
    assert page.clicked == ["ENTER"]
    assert _outcomes(actions, "cleared") == [], (
        "a block page behind the gate was reported as members content"
    )
    blocked = _outcomes(actions, "blocked_after_gate")
    assert len(blocked) == 1
    assert "blocked" in blocked[0]["reason"].lower()
    assert interstitial.first_blocked_after_gate(actions) is blocked[0]


# --------------------------------------------------------------------------
# E1: the ENTER admission is pinned in the WIDENING direction.
# A narrowing mutant is caught by the click test above; the hazard is the other
# direction -- admitting a label that is not an ENTER affordance at all. This
# page carries NO enter control, so any widening of ENTER_AFFORDANCE lands on
# the purchase button and the product buys a membership to clear a gate.
# --------------------------------------------------------------------------
def test_a_purchase_control_on_an_age_wall_is_never_clicked():
    page = _GatePage(
        "https://gamma.example/login-abused",
        GATE_TEXT,
        [_Control("EXIT HERE"), _Control(PURCHASE_LABEL, next_text="Checkout")],
    )
    # PRECONDITION: the hazard is present -- age-gate language, an EXIT
    # affordance, and a non-ENTER control that a widened pattern would admit.
    assert "RESTRICTED TO ADULTS" in page.text
    assert [c.label for c in page.controls] == ["EXIT HERE", PURCHASE_LABEL]
    assert not interstitial.ENTER_AFFORDANCE.fullmatch(PURCHASE_LABEL)

    actions = _run(page)
    assert page.clicked == [], (
        "a non-ENTER control was clicked on an age wall: %r" % (page.clicked,)
    )
    assert _outcomes(actions, "cleared") == []
    assert len(_outcomes(actions, "refused")) == 1


# --------------------------------------------------------------------------
# E2: the gate_landing_unknown arm. An unavailable measurement is never
# permission -- the landing behind a pressed gate that cannot be read is not
# reported as a clearance, and it reaches the consumers as a refusal.
# --------------------------------------------------------------------------
def test_an_unreadable_landing_behind_the_gate_is_unknown_not_cleared():
    page = _GatePage(
        "https://gamma.example/login-abused",
        GATE_TEXT,
        [_Control("EXIT HERE"), _Control("ENTER", next_text="Members area")],
        text_error_after_click=RuntimeError("detached frame"),
    )
    actions = _run(page)
    # PRECONDITION: the gate WAS pressed -- this is a post-click verdict, not a
    # refusal that happened before the control was ever used.
    assert page.clicked == ["ENTER"]
    assert page.text_reads == 2
    assert _outcomes(actions, "cleared") == [], (
        "an unreadable landing was reported as a cleared gate"
    )
    unknown = _outcomes(actions, "gate_landing_unknown")
    assert len(unknown) == 1
    assert "UNKNOWN" in unknown[0]["reason"]
    assert interstitial.first_blocked_after_gate(actions) is unknown[0]


# --------------------------------------------------------------------------
# E3: the two new outcomes GOVERN. Each test below executes the real consumer
# route -- not the helper in isolation -- so a report nothing consumes fails.
# --------------------------------------------------------------------------
class _RunnerGateHarness:
    """Minimal SiteRunner stand-in that runs the REAL gate methods."""

    def __init__(self):
        self.config = {"dismiss_selectors": "", "wait": 0}
        self.events = []
        self.job_updates = []

    def log_event(self, kind, message, url=None, extra=None):
        self.events.append((kind, message))

    def _update_job(self, url, state, message):
        self.job_updates.append((url, state, message))

    def _dismiss_page_gates(self, page, url):
        from bulk_downloader.runner import SiteRunner
        return SiteRunner._dismiss_page_gates(self, page, url)


def _blocked_gate_page():
    return _GatePage(
        "https://gamma.example/login-abused",
        GATE_TEXT,
        [_Control("EXIT HERE"), _Control("ENTER", next_text=ABUSE_TEXT)],
    )


@pytest.mark.parametrize("make_page,expected", [
    (_blocked_gate_page, "blocked"),
    (lambda: _GatePage(
        "https://gamma.example/login-abused",
        GATE_TEXT,
        [_Control("EXIT HERE"), _Control("ENTER", next_text="Members area")],
        text_error_after_click=RuntimeError("detached frame")),
     "UNKNOWN"),
], ids=["block_behind_gate", "unreadable_landing"])
def test_the_content_worker_holds_the_job_instead_of_extracting(
        make_page, expected):
    from bulk_downloader.runner import SiteRunner

    page = make_page()
    runner = _RunnerGateHarness()
    # PRECONDITION: nothing held before the route runs.
    assert runner.job_updates == []

    safe = SiteRunner._page_gates_are_safe(runner, page, page.url)

    assert page.clicked == ["ENTER"], (
        "precondition: the gate must actually have been pressed"
    )
    assert safe is False, (
        "the content worker treated a %s landing as members content" % expected
    )
    assert len(runner.job_updates) == 1
    url, state, message = runner.job_updates[0]
    assert state == "needs_review"
    assert expected in message
    assert "not members content" in message


class _LoginContext:
    def __init__(self, page):
        self.page = page
        self.init_scripts = []

    def new_page(self):
        return self.page

    def add_init_script(self, script):
        self.init_scripts.append(script)

    def cookies(self):
        return []


class _LoginBrowser:
    def __init__(self, page):
        self.context = _LoginContext(page)
        self.close_count = 0

    def new_context(self, **_kwargs):
        return self.context

    def close(self):
        self.close_count += 1


def _login_config(url):
    return {
        "name": "row721 fixture",
        "login_url": url,
        # Documented zero-entropy fixture values; not a credential.
        "username": "operator",
        "password": "zero-entropy-password",
        "success_url": "",
        "wait": 0,
        "use_real_chrome": False,
        "use_stealth": False,
        "use_stealth_library": False,
    }


def _patch_login_runtime(monkeypatch, page):
    from bulk_downloader import cloak, learn, stealth
    from bulk_downloader.login_impl import submit

    browser = _LoginBrowser(page)
    reached = []
    monkeypatch.setattr(cloak, "launch_browser",
                        lambda **_kwargs: (browser, None, "row721-fake"))
    monkeypatch.setattr(cloak, "log_choice", lambda *_a, **_k: None)
    monkeypatch.setattr(learn, "install_recorder", lambda _page: None)
    monkeypatch.setattr(stealth, "apply_to_page", lambda _page, _cfg: None)
    monkeypatch.setattr(submit.time, "sleep", lambda _s: None)
    monkeypatch.setattr(submit, "replay_saved_login_flow",
                        lambda _p, _c: {"ran": False})
    monkeypatch.setattr(submit, "_try_fill",
                        lambda *_a, **_k: (reached.append("fill"), (True, "f"))[1])
    monkeypatch.setattr(submit, "_submit_login",
                        lambda *_a, **_k: (reached.append("submit"), (True, "s"))[1])
    monkeypatch.setattr(submit, "_wait_captcha_tokens", lambda *_a, **_k: (None, 0))
    monkeypatch.setattr(submit, "_try_check_remember_me", lambda _page: None)
    return submit, browser, reached


@pytest.mark.parametrize("next_text,text_error,expected", [
    (ABUSE_TEXT, None, "blocked"),
    ("Members area", RuntimeError("detached frame"), "UNKNOWN"),
], ids=["block_behind_gate", "unreadable_landing"])
def test_do_login_refuses_a_gate_landing_it_cannot_call_members_content(
        monkeypatch, next_text, text_error, expected):
    target = "https://gamma.example/login-abused"
    page = _GatePage(
        target, GATE_TEXT,
        [_Control("EXIT HERE"), _Control("ENTER", next_text=next_text)],
        text_error_after_click=text_error,
    )
    submit, browser, reached = _patch_login_runtime(monkeypatch, page)

    # PRECONDITION: nothing has been typed and no control pressed yet.
    assert reached == [] and page.clicked == []

    ok, reason, cookies = submit.do_login(
        _login_config(target), allow_manual_takeover=False)

    assert page.clicked == ["ENTER"], "the gate must have been pressed"
    assert ok is False, (
        "do_login accepted a %s landing behind the age gate" % expected
    )
    assert expected in reason
    assert "not members content" in reason
    # The credentials were never offered to the page behind the wall.
    assert reached == []
    assert cookies == []
    assert browser.close_count == 1
