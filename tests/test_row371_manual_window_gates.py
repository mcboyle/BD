"""Row 371: safe, reported dismissal of stacked page gates (offline)."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from bulk_downloader import interstitial


BD_GATE_SCOPE = "module"


@dataclass
class _Control:
    selector: str
    label: str
    next_stage: int
    destination: str = ""


class _ControlLocator:
    def __init__(self, page, control):
        self._page = page
        self._control = control

    @property
    def first(self):
        return self

    def wait_for(self, **_kwargs):
        if self._control not in self._page.visible_controls:
            raise TimeoutError("control is not visible")

    def click(self, **_kwargs):
        self.wait_for()
        self._page.clicked.append(self._control.label)
        self._page.stage = self._control.next_stage
        if self._control.destination:
            self._page.url = self._control.destination

    def inner_text(self, **_kwargs):
        return self._control.label

    def get_attribute(self, name, **_kwargs):
        if name == "aria-label":
            return self._control.label
        return None


class _ControlListLocator:
    def __init__(self, page):
        self._page = page

    def count(self):
        return len(self._page.visible_controls)

    def nth(self, index):
        return _ControlLocator(self._page, self._page.visible_controls[index])


class _StackedPage:
    CONTROL_SELECTOR = (
        "button, a, [role='button'], input[type='button'], input[type='submit']"
    )

    def __init__(self, target_url):
        self.target_url = target_url
        self.url = target_url
        self.stage = 0
        self.clicked = []
        self.gotos = []
        self.backs = []
        self.layers = (
            (_Control("button.cookie", "Accept All", 1),),
            (_Control("button.age", "I Agree, Enter Here", 2),),
            (_Control(
                "a.offer",
                "No Thanks. Continue to Members",
                3,
                "https://unknown.example/members",
            ),),
            (),
        )

    @property
    def visible_controls(self):
        return self.layers[self.stage]

    def locator(self, selector):
        if selector == self.CONTROL_SELECTOR:
            return _ControlListLocator(self)
        for control in self.visible_controls:
            if selector == control.selector:
                return _ControlLocator(self, control)
        return _ControlLocator(self, _Control(selector, "", self.stage))

    def wait_for_load_state(self, **_kwargs):
        return None

    def goto(self, url, **_kwargs):
        self.gotos.append(url)
        self.url = url

    def go_back(self, **_kwargs):
        self.backs.append(self.url)
        self.url = self.target_url


def _page_with_controls(target_url, controls):
    page = _StackedPage(target_url)
    page.layers = (tuple(controls), ())
    return page


def test_unknown_site_clears_one_control_per_tier_then_re_requests_destination():
    target = "https://unknown.example/scenes/371"
    page = _StackedPage(target)

    # PRECONDITION: the fixture really is three stacked layers. Only the first
    # is initially visible; every click reveals exactly the next one, and the
    # last control diverts to the same-origin members home.
    assert len(page.layers) == 4
    assert [layer[0].label for layer in page.layers[:3]] == [
        "Accept All",
        "I Agree, Enter Here",
        "No Thanks. Continue to Members",
    ]
    assert len(page.visible_controls) == 1
    assert page.visible_controls[0].label == "Accept All"
    assert page.layers[2][0].destination == "https://unknown.example/members"

    assert hasattr(interstitial, "dismiss_gates"), (
        "row 371: interstitial.dismiss_gates is missing; unknown sites have "
        "no generic consent/age/interstitial pass"
    )
    actions = interstitial.dismiss_gates(
        page,
        "",
        destination_url=target,
        timeout_ms=1,
        settle_s=0,
        sleep=lambda _seconds: None,
    )

    assert page.stage == 3
    assert page.clicked == [
        "Accept All",
        "I Agree, Enter Here",
        "No Thanks. Continue to Members",
    ]
    assert [action["tier"] for action in actions
            if action["outcome"] == "cleared"] == [
        "consent", "age", "interstitial",
    ]
    assert sum(action["outcome"] == "cleared" for action in actions) == 3
    assert page.gotos == [target]
    assert page.backs == []
    assert actions[-1]["destination_re_requested"] is True


def test_measured_four_cause_stack_clears_in_order_with_exact_counts():
    target = "https://known.example/scenes/371"
    page = _StackedPage(target)
    page.layers = (
        (_Control("button.cookie", "Accept All Cookies", 1),),
        (_Control("button.age", "I Agree, Enter Here", 2),),
        (_Control(
            "a.upsell", "No Thanks. Continue to Members", 3,
            "https://known.example/members"),),
        (_Control("button.login", "LOG IN", 4),),
        (),
    )
    measured = (
        "button.cookie\nbutton.age\na.upsell\nbutton.login"
    )

    # PRECONDITION: exactly four stacked causes exist, only the first is
    # visible, and each measured control reveals precisely the next cause.
    assert len(page.layers) == 5
    assert sum(len(layer) for layer in page.layers) == 4
    assert [layer[0].label for layer in page.layers[:4]] == [
        "Accept All Cookies",
        "I Agree, Enter Here",
        "No Thanks. Continue to Members",
        "LOG IN",
    ]
    assert len(page.visible_controls) == 1
    assert interstitial.selector_lines(measured) == [
        "button.cookie", "button.age", "a.upsell", "button.login"]

    actions = interstitial.dismiss_gates(
        page,
        measured,
        destination_url=target,
        timeout_ms=1,
        settle_s=0,
        sleep=lambda _seconds: None,
    )

    assert page.stage == 4
    assert page.clicked == [
        "Accept All Cookies",
        "I Agree, Enter Here",
        "No Thanks. Continue to Members",
        "LOG IN",
    ]
    assert len(actions) == 4
    assert [action["source"] for action in actions] == ["site"] * 4
    assert [action["outcome"] for action in actions] == ["cleared"] * 4
    assert sum(action["destination_re_requested"] for action in actions) == 1
    assert page.gotos == [target]


def test_denylist_refuses_every_measured_exit_label_and_safe_control_still_works():
    denied = [
        "Exit",
        "Leave",
        "I Disagree, Exit Here",
        "Decline",
        "Reject",
        "Deny",
        "Opt-Out",
        "Cancel",
        "Under-18",
    ]
    target = "https://www.kink.com/login"
    dangerous = [
        _Control(f"button.bad-{index}", label, 0,
                 "https://accounts.google.com/signin")
        for index, label in enumerate(denied)
    ]
    page = _page_with_controls(target, dangerous)
    page.destination_form = {
        "id": "identifierId",
        "autocomplete": "username webauthn",
    }

    # PRECONDITION: all nine measured refusal strings are live controls and a
    # click would leave Kink for the exact Google SSO-form shape that caused the
    # false "password field present" verdict.
    assert len(page.visible_controls) == 9
    assert [control.label for control in page.visible_controls] == denied
    assert page.destination_form == {
        "id": "identifierId",
        "autocomplete": "username webauthn",
    }
    assert all(control.destination.startswith("https://accounts.google.com/")
               for control in page.visible_controls)
    assert interstitial.DENIED_CONTROL_TERMS == (
        "exit", "leave", "disagree", "decline", "reject", "deny",
        "opt-out", "cancel", "under-18",
    )

    measured = "\n".join(control.selector for control in dangerous)
    assert len(interstitial.selector_lines(measured)) == 9
    actions = interstitial.dismiss_gates(
        page, measured, timeout_ms=1, settle_s=0,
        sleep=lambda _seconds: None)

    refusals = [action for action in actions
                if action["outcome"] == "refused"]
    assert len(refusals) == 9, (
        "denylist diagnostic count differs: "
        f"expected 9 explicit refusals, got {len(refusals)} from {actions!r}"
    )
    assert [action["label"] for action in refusals] == denied
    assert all("denylisted label" in action["reason"] for action in refusals)
    assert page.clicked == []
    assert page.backs == []
    assert page.gotos == []

    matched_terms = [
        "exit", "leave", "exit", "decline", "reject", "deny",
        "opt-out", "cancel", "under-18",
    ]
    assert [action["reason"] for action in refusals] == [
        f"denylisted label matched {term!r}; control not used"
        for term in matched_terms
    ]

    # NEGATIVE CONTROL for matcher coverage: the real combined wording above
    # matches "exit" first, so an isolated measured label must prove the
    # independent "disagree" term is load-bearing too.
    disagree = _page_with_controls(
        target, [_Control("button.disagree", "Disagree", 0,
                          "https://accounts.google.com/signin")])
    disagree_actions = interstitial.dismiss_gates(
        disagree,
        "button.disagree",
        timeout_ms=1,
        settle_s=0,
        sleep=lambda _seconds: None,
    )
    assert disagree.clicked == []
    assert len(disagree_actions) == 1
    assert disagree_actions[0]["reason"] == (
        "denylisted label matched 'disagree'; control not used")

    # POSITIVE CONTROL: the neighbouring enter control is not denied. If
    # this fails, the policy simply disabled age-gate dismissal rather than
    # distinguishing enter from exit.
    allowed = _page_with_controls(
        target, [_Control("button.good", "I Agree, Enter Here", 1)])
    assert len(allowed.visible_controls) == 1
    allowed_actions = interstitial.dismiss_gates(
        allowed, "", timeout_ms=1, settle_s=0,
        sleep=lambda _seconds: None)
    assert allowed.clicked == ["I Agree, Enter Here"]
    assert len(allowed_actions) == 1
    assert allowed_actions[0]["outcome"] == "cleared"
    assert allowed_actions[0]["tier"] == "age"


def test_denylist_checks_every_label_surface_before_clicking():
    class _ConflictingLabelLocator(_ControlLocator):
        def get_attribute(self, name, **_kwargs):
            if name == "aria-label":
                return "Exit"
            return super().get_attribute(name, **_kwargs)

    class _ConflictingLabelPage(_StackedPage):
        def locator(self, selector):
            for control in self.visible_controls:
                if selector == control.selector:
                    return _ConflictingLabelLocator(self, control)
            return super().locator(selector)

    target = "https://www.kink.com/login"
    page = _ConflictingLabelPage(target)
    page.layers = ((
        _Control("button.measured", "Accept All", 1),
    ), ())
    locator = page.locator("button.measured")

    # PRECONDITION: visible text looks safe, but a second real label surface
    # carries the exact exit instruction. Stopping at inner_text is the defect.
    assert len(page.visible_controls) == 1
    assert locator.inner_text() == "Accept All"
    assert locator.get_attribute("aria-label") == "Exit"

    actions = interstitial.dismiss_gates(
        page,
        "button.measured",
        timeout_ms=1,
        settle_s=0,
        sleep=lambda _seconds: None,
    )

    assert page.clicked == []
    assert len(actions) == 1
    assert actions[0]["outcome"] == "refused"
    assert actions[0]["label"] == "Accept All"
    assert actions[0]["reason"] == (
        "denylisted label matched 'exit'; control not used")


def test_unavailable_label_surface_is_unknown_not_partial_safe_label():
    class _PartialLabelLocator(_ControlLocator):
        def get_attribute(self, name, **_kwargs):
            if name == "aria-label":
                raise RuntimeError("aria accessibility tree unavailable")
            return None

    class _PartialLabelPage(_StackedPage):
        def locator(self, selector):
            for control in self.visible_controls:
                if selector == control.selector:
                    return _PartialLabelLocator(self, control)
            return super().locator(selector)

    target = "https://www.kink.com/login"
    page = _PartialLabelPage(target)
    page.layers = ((
        _Control("button.measured", "Accept All", 1),
    ), ())
    locator = page.locator("button.measured")

    # PRECONDITION: visible text is safe-looking, but the independent ARIA
    # surface cannot be measured and could contain the earned Exit warning.
    assert len(page.visible_controls) == 1
    assert locator.inner_text() == "Accept All"
    with pytest.raises(RuntimeError, match="accessibility tree unavailable"):
        locator.get_attribute("aria-label")

    actions = interstitial.dismiss_gates(
        page,
        "button.measured",
        timeout_ms=1,
        settle_s=0,
        sleep=lambda _seconds: None,
    )

    assert page.clicked == []
    assert len(actions) == 1
    assert actions[0]["outcome"] == "measurement_unknown"
    assert actions[0]["reason"] == (
        "site control label measurement UNKNOWN: aria-label: RuntimeError")


def test_cross_origin_click_is_reported_and_goes_back_exactly_once():
    target = "https://www.kink.com/login"
    page = _page_with_controls(target, [
        _Control(
            "a.continue",
            "Continue to Members",
            1,
            "https://accounts.google.com/signin",
        ),
    ])
    page.destination_form = {
        "id": "identifierId",
        "autocomplete": "username webauthn",
    }

    # PRECONDITION: this is an allowed-looking interstitial control whose
    # destination is the wrong origin and whose resulting form would otherwise
    # look plausibly login-shaped.
    assert len(page.visible_controls) == 1
    assert page.visible_controls[0].label == "Continue to Members"
    assert page.visible_controls[0].destination == (
        "https://accounts.google.com/signin")
    assert page.destination_form["autocomplete"] == "username webauthn"

    actions = interstitial.dismiss_gates(
        page, "", timeout_ms=1, settle_s=0, sleep=lambda _seconds: None)

    assert page.clicked == ["Continue to Members"]
    assert len(actions) == 1
    assert actions[0]["outcome"] == "origin_changed"
    assert actions[0]["reason"] == (
        "control left https://www.kink.com for "
        "https://accounts.google.com; went back")
    assert page.backs == ["https://accounts.google.com/signin"]
    assert page.gotos == []
    assert page.url == target


def test_url_read_failure_before_click_is_unknown_and_control_is_not_used():
    class _UnreadableOriginPage(_StackedPage):
        @property
        def url(self):
            raise RuntimeError("page URL unavailable")

        @url.setter
        def url(self, value):
            self._stored_url = value

    target = "https://www.kink.com/login"
    page = _UnreadableOriginPage(target)
    page.layers = ((
        _Control("button.age", "I Agree, Enter Here", 1),
    ), ())

    # PRECONDITION: the safe-looking control is visible, but the origin
    # property itself raises. This is unavailable evidence, not same-origin.
    assert len(page.visible_controls) == 1
    assert page.visible_controls[0].label == "I Agree, Enter Here"
    try:
        page.url
    except RuntimeError as exc:
        assert str(exc) == "page URL unavailable"
    else:
        raise AssertionError("fixture unexpectedly exposed a readable origin")

    actions = interstitial.dismiss_gates(
        page, "", timeout_ms=1, settle_s=0, sleep=lambda _seconds: None)

    assert page.clicked == []
    assert page.backs == []
    assert len(actions) == 1
    assert actions[0]["outcome"] == "origin_unknown"
    assert actions[0]["reason"] == (
        "origin unavailable before click; control not used")


def test_foreign_origin_before_click_is_unknown_and_control_is_not_used():
    requested = "https://www.kink.com/login"
    page = _page_with_controls(
        "https://accounts.google.com/signin",
        [_Control("button.accept", "Accept All", 1)],
    )

    # PRECONDITION: the safe-looking control is on Google's measurable origin,
    # not the requested Kink site. Treating the current origin as its own
    # baseline would authorize a click on the wrong page.
    assert page.url == "https://accounts.google.com/signin"
    assert interstitial._origin(page.url) == "https://accounts.google.com"
    assert interstitial._origin(requested) == "https://www.kink.com"
    assert len(page.visible_controls) == 1

    actions = interstitial.dismiss_gates(
        page,
        "",
        destination_url=requested,
        timeout_ms=1,
        settle_s=0,
        sleep=lambda _seconds: None,
    )

    assert page.clicked == []
    assert page.backs == []
    assert page.gotos == []
    assert len(actions) == 1
    assert actions[0]["outcome"] == "origin_unknown"
    assert actions[0]["reason"] == (
        "origin mismatch before click: requested site kink.com, current site "
        "google.com; control not used")


def test_click_error_after_navigation_still_verifies_and_recovers_origin():
    class _RaisesAfterClickLocator(_ControlLocator):
        def click(self, **kwargs):
            super().click(**kwargs)
            raise RuntimeError("navigation interrupted click completion")

    class _RaisesAfterClickPage(_StackedPage):
        def locator(self, selector):
            for control in self.visible_controls:
                if selector == control.selector:
                    return _RaisesAfterClickLocator(self, control)
            return super().locator(selector)

    target = "https://www.kink.com/login"
    page = _RaisesAfterClickPage(target)
    page.layers = ((
        _Control(
            "a.measured",
            "Continue to Members",
            1,
            "https://accounts.google.com/signin",
        ),
    ), ())

    # PRECONDITION: the declared control mutates URL/stage before raising from
    # click, reproducing a Playwright timeout that occurs after navigation.
    locator = page.locator("a.measured")
    assert isinstance(locator, _RaisesAfterClickLocator)
    assert len(page.visible_controls) == 1
    assert page.visible_controls[0].destination.startswith(
        "https://accounts.google.com/")

    actions = interstitial.dismiss_gates(
        page,
        "a.measured",
        timeout_ms=1,
        settle_s=0,
        sleep=lambda _seconds: None,
    )

    assert page.clicked == ["Continue to Members"]
    assert page.backs == ["https://accounts.google.com/signin"]
    assert page.url == target
    assert len(actions) == 1
    assert actions[0]["outcome"] == "origin_changed"
    assert actions[0]["reason"] == (
        "control click raised RuntimeError and left https://www.kink.com for "
        "https://accounts.google.com; went back")


def test_same_origin_click_error_still_re_requests_requested_destination():
    class _RaisesAfterClickLocator(_ControlLocator):
        def click(self, **kwargs):
            super().click(**kwargs)
            raise RuntimeError("navigation interrupted click completion")

    class _RaisesAfterClickPage(_StackedPage):
        def locator(self, selector):
            for control in self.visible_controls:
                if selector == control.selector:
                    return _RaisesAfterClickLocator(self, control)
            return super().locator(selector)

    target = "https://members.adulttime.com/en/video/scene-371"
    page = _RaisesAfterClickPage(target)
    page.layers = ((
        _Control(
            "a.measured",
            "No Thanks",
            1,
            "https://members.adulttime.com/en/members",
        ),
    ), ())

    # PRECONDITION: the click lands on same-origin members home before raising;
    # the requested scene is a distinct path and must still be restored.
    assert len(page.visible_controls) == 1
    assert interstitial._origin(page.visible_controls[0].destination) == (
        interstitial._origin(target))
    assert page.visible_controls[0].destination.endswith("/en/members")
    assert target.endswith("/en/video/scene-371")

    actions = interstitial.dismiss_gates(
        page,
        "a.measured",
        destination_url=target,
        timeout_ms=1,
        settle_s=0,
        sleep=lambda _seconds: None,
    )

    assert page.clicked == ["No Thanks"]
    assert page.backs == []
    assert page.gotos == [target]
    assert page.url == target
    assert len(actions) == 1
    assert actions[0]["outcome"] == "click_unknown"
    assert actions[0]["destination_re_requested"] is True
    assert actions[0]["reason"] == (
        "control click outcome UNKNOWN: RuntimeError; origin unchanged")


def test_shared_unknown_classifier_covers_every_safety_outcome_exactly():
    expected = {
        "label_unknown",
        "origin_unknown",
        "origin_recovery_unknown",
        "destination_re_request_unknown",
        "click_unknown",
        "measurement_unknown",
    }
    actions = [
        {"outcome": outcome, "label": outcome, "reason": "fixture"}
        for outcome in sorted(expected)
    ]

    # PRECONDITION: the fixture and production denominator each contain the
    # exact six UNKNOWN outcomes—neither a subset nor a loose nonempty check.
    assert len(actions) == 6
    assert {action["outcome"] for action in actions} == expected
    assert interstitial.SAFETY_UNKNOWN_OUTCOMES == frozenset(expected)

    for action in actions:
        assert interstitial.first_safety_unknown([action]) is action
    assert interstitial.first_safety_unknown([
        {"outcome": "cleared"}, {"outcome": "refused"}]) is None


def test_cross_origin_recovery_restores_exact_preclick_url_without_hint():
    class _BackToHomePage(_StackedPage):
        def go_back(self, **_kwargs):
            self.backs.append(self.url)
            self.url = "https://www.kink.com/"

    target = "https://www.kink.com/login?next=%2Fmembers#form"
    page = _BackToHomePage(target)
    page.layers = ((
        _Control(
            "a.continue",
            "Continue to Members",
            0,
            "https://accounts.google.com/signin",
        ),
    ),)

    # PRECONDITION: the click leaves Kink, while browser history recovers only
    # the origin's home page—not the exact login URL (query and fragment too).
    assert len(page.visible_controls) == 1
    assert page.url == target
    page.url = "https://accounts.google.com/signin"
    page.go_back()
    assert page.url == "https://www.kink.com/"
    page.url = target
    page.backs.clear()

    actions = interstitial.dismiss_gates(
        page, "", timeout_ms=1, settle_s=0,
        sleep=lambda _seconds: None)

    assert page.clicked == ["Continue to Members"]
    assert page.backs == ["https://accounts.google.com/signin"]
    assert page.gotos == [target]
    assert page.url == target
    assert len(actions) == 1
    assert actions[0]["outcome"] == "origin_changed"
    assert actions[0]["destination_re_requested"] is True


def test_requested_destination_remains_authoritative_after_initial_redirect():
    requested = "https://www.kink.com/login"
    redirected = "https://members.kink.com/login"

    class _RedirectedPage(_StackedPage):
        def go_back(self, **_kwargs):
            self.backs.append(self.url)
            self.url = "https://members.kink.com/"

        def goto(self, url, **_kwargs):
            self.gotos.append(url)
            assert url == requested
            self.url = redirected

    page = _RedirectedPage(redirected)
    page.layers = ((
        _Control(
            "a.continue",
            "Continue to Members",
            0,
            "https://accounts.google.com/signin",
        ),
    ),)

    # PRECONDITION: the requested and current URLs are both valid but have
    # distinct origins, and history recovery loses the current path.
    assert interstitial._origin(requested) == "https://www.kink.com"
    assert interstitial._origin(redirected) == "https://members.kink.com"
    assert interstitial._origin(requested) != interstitial._origin(redirected)
    assert len(page.visible_controls) == 1
    page.goto(requested)
    assert page.url == redirected
    assert page.gotos == [requested]
    page.gotos.clear()

    actions = interstitial.dismiss_gates(
        page,
        "",
        destination_url=requested,
        timeout_ms=1,
        settle_s=0,
        sleep=lambda _seconds: None,
    )

    assert page.clicked == ["Continue to Members"]
    assert page.backs == ["https://accounts.google.com/signin"]
    assert page.gotos == [requested]
    assert page.url == redirected
    assert len(actions) == 1
    assert actions[0]["outcome"] == "origin_changed"
    assert actions[0]["destination_re_requested"] is True


def test_delayed_navigation_during_settle_is_verified_before_success():
    target = "https://www.kink.com/login"
    page = _page_with_controls(target, [
        _Control("a.continue", "Continue to Members", 1),
    ])
    settle_calls = []

    def _navigate_during_settle(seconds):
        settle_calls.append(seconds)
        page.url = "https://accounts.google.com/signin"

    # PRECONDITION: click itself leaves the origin unchanged; the supplied
    # settle hook performs exactly the deferred cross-origin navigation that a
    # JavaScript click handler can schedule after Playwright returns.
    assert len(page.visible_controls) == 1
    assert page.visible_controls[0].destination == ""
    assert page.url == target

    actions = interstitial.dismiss_gates(
        page,
        "",
        timeout_ms=1,
        settle_s=0.25,
        sleep=_navigate_during_settle,
    )

    assert settle_calls == [0.25]
    assert page.clicked == ["Continue to Members"]
    assert page.backs == ["https://accounts.google.com/signin"]
    assert page.url == target
    assert len(actions) == 1
    assert actions[0]["outcome"] == "origin_changed"
    assert actions[0]["reason"] == (
        "control left https://www.kink.com for "
        "https://accounts.google.com; went back")


def test_per_site_selector_is_tried_before_generic_controls():
    target = "https://known.example/login"
    page = _page_with_controls(target, [
        _Control("button.measured", "Open measured login", 1),
        _Control("button.generic", "Accept All", 1),
    ])

    # PRECONDITION: both paths could make progress, and document order puts the
    # measured selector first. The verdict therefore proves source priority,
    # not mere clickability.
    assert len(page.visible_controls) == 2
    assert [control.selector for control in page.visible_controls] == [
        "button.measured", "button.generic",
    ]
    page.locator("button.measured").wait_for(state="visible", timeout=1)

    actions = interstitial.dismiss_gates(
        page,
        "button.measured",
        timeout_ms=1,
        settle_s=0,
        sleep=lambda _seconds: None,
    )

    assert page.clicked == ["Open measured login"]
    assert len(actions) == 1
    assert actions[0]["source"] == "site"
    assert actions[0]["outcome"] == "cleared"


def test_duplicate_measured_selectors_do_not_click_same_control_twice():
    class _AliasPage(_StackedPage):
        def locator(self, selector):
            if selector in ("button.alias-one", "button.alias-two"):
                return _ControlLocator(self, self.visible_controls[0])
            return super().locator(selector)

    target = "https://known.example/login"
    page = _AliasPage(target)
    page.layers = ((
        _Control("button.real", "Open measured login", 0),
    ),)

    # PRECONDITION: two independent declared lines resolve the exact same live
    # control, and that control remains visible after its first click.
    assert len(page.visible_controls) == 1
    assert page.locator("button.alias-one")._control is (
        page.locator("button.alias-two")._control)
    assert page.visible_controls[0].next_stage == 0

    actions = interstitial.dismiss_gates(
        page,
        "button.alias-one\nbutton.alias-two",
        timeout_ms=1,
        settle_s=0,
        sleep=lambda _seconds: None,
    )

    assert page.clicked == ["Open measured login"]
    assert len(actions) == 1
    assert actions[0]["source"] == "site"
    assert actions[0]["outcome"] == "cleared"


def test_generic_pass_ignores_short_ordinary_action_labels():
    ordinary = ["Accept", "Allow", "Agree", "Got It", "OK", "18+", "21+"]
    target = "https://unknown.example/members"
    page = _page_with_controls(
        target,
        [_Control(f"a.ordinary-{index}", label, 0)
         for index, label in enumerate(ordinary)],
    )

    # PRECONDITION: all seven ordinary links are visible and clickable, but
    # none states a complete consent, age-entry, or interstitial decision.
    assert len(page.visible_controls) == 7
    assert [control.label for control in page.visible_controls] == ordinary
    assert all(control.next_stage == 0 for control in page.visible_controls)

    actions = interstitial.dismiss_gates(
        page, "", timeout_ms=1, settle_s=0,
        sleep=lambda _seconds: None)

    assert actions == []
    assert page.clicked == []
    assert page.backs == []
    assert page.gotos == []


def test_generic_interstitial_accepts_full_measured_members_area_phrase():
    target = "https://unknown.example/scenes/371"
    label = "No Thanks. Continue to Members Area"
    page = _page_with_controls(
        target, [_Control("a.offer", label, 1)])

    # PRECONDITION: the unknown-site fixture contains exactly the full Gamma
    # wording; it is neither a shortened test alias nor a declared selector.
    assert len(page.visible_controls) == 1
    assert page.visible_controls[0].label == label
    assert page.visible_controls[0].selector == "a.offer"

    actions = interstitial.dismiss_gates(
        page, "", timeout_ms=1, settle_s=0,
        sleep=lambda _seconds: None)

    assert page.clicked == [label]
    assert len(actions) == 1
    assert actions[0]["tier"] == "interstitial"
    assert actions[0]["outcome"] == "cleared"


def test_generic_pass_continues_after_site_control_reveals_the_next_tier():
    target = "https://known.example/login"
    page = _StackedPage(target)
    page.layers = (
        (_Control("button.measured", "Open measured layer", 1),),
        (_Control("button.age", "I Agree, Enter Here", 2),),
        (),
    )

    # PRECONDITION: only the site-specific control exists at stage zero; its
    # click reveals one generic age control at stage one.
    assert len(page.layers) == 3
    assert [control.label for control in page.visible_controls] == [
        "Open measured layer"]
    assert page.layers[1][0].label == "I Agree, Enter Here"

    actions = interstitial.dismiss_gates(
        page,
        "button.measured",
        timeout_ms=1,
        settle_s=0,
        sleep=lambda _seconds: None,
    )

    assert page.clicked == ["Open measured layer", "I Agree, Enter Here"]
    assert len(actions) == 2
    assert [(action["source"], action["tier"], action["outcome"])
            for action in actions] == [
        ("site", "site", "cleared"),
        ("generic", "age", "cleared"),
    ]


def test_generic_pass_clicks_at_most_one_matching_control_per_tier():
    target = "https://unknown.example/login"
    controls = [
        _Control("button.consent-1", "Accept All", 0),
        _Control("button.consent-2", "Allow All", 0),
        _Control("button.age-1", "I Agree, Enter Here", 0),
        _Control("button.age-2", "I am 18 or older", 0),
        _Control(
            "button.offer-1", "No Thanks. Continue to Members", 0),
        _Control("button.offer-2", "Skip this page", 0),
    ]
    page = _page_with_controls(target, controls)

    # PRECONDITION: every tier has exactly two simultaneously visible safe
    # matches, and clicking one deliberately leaves all six controls visible.
    assert len(page.visible_controls) == 6
    assert [control.next_stage for control in page.visible_controls] == [
        0, 0, 0, 0, 0, 0]

    actions = interstitial.dismiss_gates(
        page, "", timeout_ms=1, settle_s=0, sleep=lambda _seconds: None)

    assert page.clicked == [
        "Accept All",
        "I Agree, Enter Here",
        "No Thanks. Continue to Members",
    ]
    assert len(actions) == 3
    assert [action["tier"] for action in actions] == [
        "consent", "age", "interstitial"]
    assert sum(action["outcome"] == "cleared" for action in actions) == 3


def test_declared_control_with_unreadable_label_is_unknown_not_clicked():
    target = "https://known.example/login"
    page = _page_with_controls(
        target, [_Control("button.measured", "", 1)])

    # PRECONDITION: the site declaration resolves to exactly one visible
    # control, but none of the denylist's observable label sources has data.
    assert len(page.visible_controls) == 1
    assert page.visible_controls[0].selector == "button.measured"
    assert page.visible_controls[0].label == ""
    locator = page.locator("button.measured")
    assert locator.inner_text() == ""
    assert locator.get_attribute("aria-label") == ""

    actions = interstitial.dismiss_gates(
        page,
        "button.measured",
        timeout_ms=1,
        settle_s=0,
        sleep=lambda _seconds: None,
    )

    assert page.clicked == []
    assert len(actions) == 1
    assert actions[0]["outcome"] == "label_unknown"
    assert actions[0]["reason"] == (
        "control label UNKNOWN; denylist could not be evaluated; "
        "control not used")


def test_failed_origin_recovery_is_unknown_not_reported_as_back():
    class _BackFailsPage(_StackedPage):
        def go_back(self, **_kwargs):
            self.backs.append(self.url)
            # Simulate a browser/history failure: the URL remains off-origin.

    target = "https://www.kink.com/login"
    page = _BackFailsPage(target)
    page.layers = ((
        _Control(
            "a.continue",
            "Continue to Members",
            1,
            "https://accounts.google.com/signin",
        ),
    ), ())

    # PRECONDITION: the click leaves Kink and the fake's go_back deliberately
    # cannot recover it. This distinguishes failed recovery from no history.
    assert len(page.visible_controls) == 1
    page.url = "https://accounts.google.com/signin"
    page.go_back()
    assert page.url == "https://accounts.google.com/signin"
    assert len(page.backs) == 1
    page.url = target
    page.backs.clear()

    actions = interstitial.dismiss_gates(
        page, "", timeout_ms=1, settle_s=0, sleep=lambda _seconds: None)

    assert page.clicked == ["Continue to Members"]
    assert page.backs == ["https://accounts.google.com/signin"]
    assert len(actions) == 1
    assert actions[0]["outcome"] == "origin_recovery_unknown"
    assert actions[0]["reason"] == (
        "origin recovery UNKNOWN: expected https://www.kink.com after "
        "going back, got https://accounts.google.com")


def test_unmeasurable_post_click_origin_is_unknown_even_after_recovery():
    target = "https://www.kink.com/login"
    page = _page_with_controls(target, [
        _Control("a.continue", "Continue to Members", 1, "about:blank"),
    ])

    # PRECONDITION: the click makes the post-click origin unavailable, while
    # history recovery itself remains observable. This separates an UNKNOWN
    # measurement from the known cross-origin case above.
    assert len(page.visible_controls) == 1
    assert page.visible_controls[0].destination == "about:blank"
    assert interstitial._origin(page.visible_controls[0].destination) is None
    assert page.url == target

    actions = interstitial.dismiss_gates(
        page, "", timeout_ms=1, settle_s=0, sleep=lambda _seconds: None)

    assert page.clicked == ["Continue to Members"]
    assert page.backs == ["about:blank"]
    assert page.url == target
    assert len(actions) == 1
    assert actions[0]["outcome"] == "origin_unknown"
    assert actions[0]["reason"] == (
        "origin UNKNOWN after click; went back to https://www.kink.com")


def test_failed_origin_recovery_stops_before_controls_on_the_wrong_site():
    class _BackFailsWithSsoPage(_StackedPage):
        def go_back(self, **_kwargs):
            self.backs.append(self.url)
            # The failed history operation leaves the page and its controls on
            # the foreign origin.

    target = "https://www.kink.com/login"
    page = _BackFailsWithSsoPage(target)
    page.layers = (
        (_Control(
            "a.measured",
            "Continue to Members",
            1,
            "https://accounts.google.com/signin",
        ),),
        (_Control(
            "button.google",
            "Accept All",
            2,
            "https://accounts.google.com/consent",
        ),),
        (),
    )

    # PRECONDITION: the first declared control leaves Kink and exposes a
    # second, superficially safe control on Google. Both selectors are live in
    # their respective stages, and go_back cannot restore stage zero.
    assert len(page.layers) == 3
    assert page.layers[0][0].selector == "a.measured"
    assert page.layers[1][0].selector == "button.google"
    assert page.layers[1][0].label == "Accept All"
    assert interstitial._origin(page.layers[1][0].destination) == (
        "https://accounts.google.com")
    page.stage = 1
    page.url = "https://accounts.google.com/signin"
    page.go_back()
    assert page.stage == 1
    assert page.url == "https://accounts.google.com/signin"
    assert len(page.backs) == 1
    page.stage = 0
    page.url = target
    page.backs.clear()

    actions = interstitial.dismiss_gates(
        page,
        "a.measured\nbutton.google",
        timeout_ms=1,
        settle_s=0,
        sleep=lambda _seconds: None,
    )

    assert page.clicked == ["Continue to Members"]
    assert page.backs == ["https://accounts.google.com/signin"]
    assert len(actions) == 1
    assert actions[0]["outcome"] == "origin_recovery_unknown"
    assert actions[0]["reason"] == (
        "origin recovery UNKNOWN: expected https://www.kink.com after "
        "going back, got https://accounts.google.com")


def test_recovered_cross_origin_control_is_not_clicked_again_by_generic_pass():
    class _RestoringPage(_StackedPage):
        def go_back(self, **_kwargs):
            self.backs.append(self.url)
            self.url = self.target_url
            self.stage = 0

    target = "https://www.kink.com/login"
    page = _RestoringPage(target)
    page.layers = ((
        _Control(
            "a.measured",
            "Continue to Members",
            1,
            "https://accounts.google.com/signin",
        ),
    ), ())

    # PRECONDITION: successful history recovery restores the same DOM control,
    # whose label also matches the generic interstitial tier. Without attempt
    # deduplication, the fallback would click the proven-bad control again.
    assert len(page.visible_controls) == 1
    assert page.visible_controls[0].selector == "a.measured"
    assert page.visible_controls[0].label == "Continue to Members"
    page.stage = 1
    page.go_back()
    assert len(page.visible_controls) == 1
    assert len(page.backs) == 1
    page.backs.clear()

    actions = interstitial.dismiss_gates(
        page,
        "a.measured",
        timeout_ms=1,
        settle_s=0,
        sleep=lambda _seconds: None,
    )

    assert page.clicked == ["Continue to Members"]
    assert page.backs == ["https://accounts.google.com/signin"]
    assert len(actions) == 1
    assert actions[0]["outcome"] == "origin_changed"


def test_cross_origin_recovery_re_requests_destination_if_back_lands_elsewhere():
    class _BackToHomePage(_StackedPage):
        def go_back(self, **_kwargs):
            self.backs.append(self.url)
            self.url = "https://www.kink.com/home"

    target = "https://www.kink.com/login"
    page = _BackToHomePage(target)
    page.layers = ((
        _Control(
            "a.measured",
            "Continue to Members",
            1,
            "https://accounts.google.com/signin",
        ),
    ), ())

    # PRECONDITION: history recovery returns to Kink's origin but the wrong
    # path. Origin-only recovery would therefore strand the caller at /home.
    assert page.url == target
    page.url = "https://accounts.google.com/signin"
    page.go_back()
    assert page.url == "https://www.kink.com/home"
    page.url = target
    page.backs.clear()

    actions = interstitial.dismiss_gates(
        page,
        "a.measured",
        destination_url=target,
        timeout_ms=1,
        settle_s=0,
        sleep=lambda _seconds: None,
    )

    assert page.clicked == ["Continue to Members"]
    assert page.backs == ["https://accounts.google.com/signin"]
    assert page.gotos == [target]
    assert page.url == target
    assert len(actions) == 1
    assert actions[0]["outcome"] == "origin_changed"
    assert actions[0]["destination_re_requested"] is True


def test_recovery_navigation_uses_navigation_budget_not_selector_budget():
    class _BudgetPage(_StackedPage):
        def __init__(self, target_url):
            super().__init__(target_url)
            self.back_kwargs = []
            self.goto_kwargs = []

        def go_back(self, **kwargs):
            self.back_kwargs.append(kwargs)
            self.backs.append(self.url)
            self.url = "https://www.kink.com/home"

        def goto(self, url, **kwargs):
            self.goto_kwargs.append(kwargs)
            super().goto(url, **kwargs)

    target = "https://www.kink.com/login"
    page = _BudgetPage(target)
    page.layers = ((
        _Control(
            "a.measured",
            "Continue to Members",
            1,
            "https://accounts.google.com/signin",
        ),
    ), ())

    # PRECONDITION: click actionability is deliberately budgeted at 7 ms,
    # while both recovery navigations require the independent 30-second budget.
    assert len(page.visible_controls) == 1
    assert page.back_kwargs == []
    assert page.goto_kwargs == []

    actions = interstitial.dismiss_gates(
        page,
        "a.measured",
        destination_url=target,
        timeout_ms=7,
        navigation_timeout_ms=30000,
        settle_s=0,
        sleep=lambda _seconds: None,
    )

    assert len(actions) == 1
    assert actions[0]["outcome"] == "origin_changed"
    assert page.back_kwargs == [{
        "wait_until": "domcontentloaded", "timeout": 30000}]
    assert page.goto_kwargs == [{
        "wait_until": "domcontentloaded", "timeout": 30000}]
    assert page.gotos == [target]


def test_failed_destination_re_request_is_not_reported_as_cleared():
    class _GotoFailsPage(_StackedPage):
        def goto(self, url, **_kwargs):
            self.gotos.append(url)
            raise RuntimeError("navigation refused")

    target = "https://unknown.example/scenes/371"
    page = _GotoFailsPage(target)
    page.url = "https://unknown.example/en/interstitial"
    page.layers = ((
        _Control(
            "a.offer",
            "No Thanks. Continue to Members",
            1,
            "https://unknown.example/members",
        ),
    ), ())

    # PRECONDITION: this is the measured same-origin upsell shape. Dismissal
    # lands on members home and the only way to restore the requested scene is
    # the re-request that this fake makes fail.
    assert page.url.endswith("/en/interstitial")
    assert page.visible_controls[0].destination.endswith("/members")
    assert target.endswith("/scenes/371")

    actions = interstitial.dismiss_gates(
        page,
        "",
        destination_url=target,
        timeout_ms=1,
        settle_s=0,
        sleep=lambda _seconds: None,
    )

    assert page.clicked == ["No Thanks. Continue to Members"]
    assert page.gotos == [target]
    assert page.backs == []
    assert len(actions) == 1
    assert actions[0]["outcome"] == "destination_re_request_unknown"
    assert actions[0]["destination_re_requested"] is False
    assert actions[0]["reason"] == (
        "destination re-request UNKNOWN: RuntimeError")


def test_destination_query_mismatch_forces_exact_re_request():
    target = "https://unknown.example/watch?id=371"
    page = _page_with_controls(target, [
        _Control(
            "a.offer",
            "No Thanks. Continue to Members",
            1,
            "https://unknown.example/watch?id=999",
        ),
    ])

    # PRECONDITION: origin and path are identical, while the query identifies
    # a different scene. A path-only comparator cannot see the defect.
    assert interstitial._origin(target) == (
        interstitial._origin(page.visible_controls[0].destination))
    assert target.split("?", 1)[0] == (
        page.visible_controls[0].destination.split("?", 1)[0])
    assert target.split("?", 1)[1] == "id=371"
    assert page.visible_controls[0].destination.split("?", 1)[1] == "id=999"

    actions = interstitial.dismiss_gates(
        page,
        "",
        destination_url=target,
        timeout_ms=1,
        settle_s=0,
        sleep=lambda _seconds: None,
    )

    assert page.clicked == ["No Thanks. Continue to Members"]
    assert page.gotos == [target]
    assert len(actions) == 1
    assert actions[0]["outcome"] == "cleared"
    assert actions[0]["destination_re_requested"] is True


def test_hash_routed_destination_mismatch_forces_exact_re_request():
    target = "https://unknown.example/app#/scene/371"
    page = _page_with_controls(target, [
        _Control(
            "a.offer",
            "No Thanks. Continue to Members",
            1,
            "https://unknown.example/app#/members",
        ),
    ])

    # PRECONDITION: origin, path, and query are identical; only the SPA route
    # in the fragment distinguishes the requested scene from members home.
    wanted = target.split("#", 1)
    landed = page.visible_controls[0].destination.split("#", 1)
    assert wanted[0] == landed[0]
    assert wanted[1] == "/scene/371"
    assert landed[1] == "/members"

    actions = interstitial.dismiss_gates(
        page,
        "",
        destination_url=target,
        timeout_ms=1,
        settle_s=0,
        sleep=lambda _seconds: None,
    )

    assert page.clicked == ["No Thanks. Continue to Members"]
    assert page.gotos == [target]
    assert len(actions) == 1
    assert actions[0]["outcome"] == "cleared"
    assert actions[0]["destination_re_requested"] is True


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


class _OriginRecoveryFailsPage(_StackedPage):
    def go_back(self, **_kwargs):
        self.backs.append(self.url)
        # The URL/stage deliberately remain on the foreign origin.


def _patch_login_runtime(monkeypatch, page, *, fill_ok):
    from bulk_downloader import cloak, learn, stealth
    from bulk_downloader.login_impl import submit

    browser = _LoginBrowser(page)
    fill_states = []
    submit_calls = []

    monkeypatch.setattr(
        cloak,
        "launch_browser",
        lambda **_kwargs: (browser, None, "row371-fake"),
    )
    monkeypatch.setattr(cloak, "log_choice", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(learn, "install_recorder", lambda _page: None)
    monkeypatch.setattr(stealth, "apply_to_page", lambda _page, _cfg: None)
    monkeypatch.setattr(submit.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        submit,
        "replay_saved_login_flow",
        lambda _page, _cfg: {"ran": False},
    )

    def _fill(_page, _selectors, _value, what):
        fill_states.append((what, list(page.clicked)))
        return fill_ok, ("fixture field" if fill_ok else "fixture has no field")

    def _submit(_page, _submit_selectors, _password_selectors):
        submit_calls.append(list(page.clicked))
        return True, "fixture submit"

    monkeypatch.setattr(submit, "_try_fill", _fill)
    monkeypatch.setattr(submit, "_submit_login", _submit)
    monkeypatch.setattr(submit, "_wait_captcha_tokens", lambda *_a, **_k: (None, 0))
    monkeypatch.setattr(submit, "_try_check_remember_me", lambda _page: None)
    return submit, browser, fill_states, submit_calls


def _login_config(url, **extra):
    return {
        "name": "row371 fixture",
        "login_url": url,
        "username": "operator",
        "password": "zero-entropy-password",
        "success_url": "",
        "wait": 0,
        "use_real_chrome": False,
        "use_stealth": False,
        "use_stealth_library": False,
        **extra,
    }


def test_do_login_clears_and_reports_gate_before_first_form_probe(
        monkeypatch, capsys):
    target = "https://known.example/login"
    page = _page_with_controls(
        target, [_Control("button.age", "I Agree, Enter Here", 1)])
    submit, browser, fill_states, submit_calls = _patch_login_runtime(
        monkeypatch, page, fill_ok=True)

    # PRECONDITION: the login page starts with exactly one age control and no
    # click has happened before do_login owns the page.
    assert len(page.visible_controls) == 1
    assert page.visible_controls[0].label == "I Agree, Enter Here"
    assert page.clicked == []

    result = submit.do_login(_login_config(target), allow_manual_takeover=False)
    stderr = capsys.readouterr().err

    assert result[0] is True
    assert page.clicked == ["I Agree, Enter Here"]
    assert fill_states == [
        ("username", ["I Agree, Enter Here"]),
        ("password", ["I Agree, Enter Here"]),
    ]
    assert submit_calls == [["I Agree, Enter Here"]]
    assert stderr.count("login: cleared age gate via 'I Agree, Enter Here'") == 1
    assert browser.close_count == 1


def test_do_login_manual_handoff_names_the_denylist_refusal(
        monkeypatch, capsys):
    target = "https://www.kink.com/login"
    page = _page_with_controls(target, [
        _Control(
            "button.exit",
            "I Disagree, Exit Here",
            1,
            "https://accounts.google.com/signin",
        ),
    ])
    page.destination_form = {
        "id": "identifierId",
        "autocomplete": "username webauthn",
    }
    submit, browser, fill_states, submit_calls = _patch_login_runtime(
        monkeypatch, page, fill_ok=False)

    # PRECONDITION: this is the measured dangerous control and SSO destination;
    # there is exactly one form probe available to trigger the manual fallback.
    assert len(page.visible_controls) == 1
    assert page.visible_controls[0].label == "I Disagree, Exit Here"
    assert page.destination_form["id"] == "identifierId"
    assert page.destination_form["autocomplete"] == "username webauthn"

    result = submit.do_login(
        _login_config(target), allow_manual_takeover=True)
    stderr = capsys.readouterr().err

    assert result[0] == "MANUAL_PENDING"
    assert result[1] == (
        "Page gate refused 'I Disagree, Exit Here': denylisted label matched "
        "'exit'; control not used. Couldn't find username field: fixture has "
        "no field")
    assert page.clicked == []
    assert page.backs == []
    assert fill_states == [("username", [])]
    assert submit_calls == []
    assert stderr.count(
        "login: refused safety gate via 'I Disagree, Exit Here'") == 1
    assert browser.close_count == 0


def test_do_login_stops_before_credentials_when_origin_recovery_is_unknown(
        monkeypatch, capsys):
    target = "https://www.kink.com/login"
    page = _OriginRecoveryFailsPage(target)
    page.layers = (
        (_Control(
            "a.continue",
            "Continue to Members",
            1,
            "https://accounts.google.com/signin",
        ),),
        (),
    )
    submit, browser, fill_states, submit_calls = _patch_login_runtime(
        monkeypatch, page, fill_ok=True)

    # PRECONDITION: the allowed-looking control leaves Kink, back cannot
    # recover, and the fill seam would accept fields if execution reached it.
    assert len(page.visible_controls) == 1
    assert page.visible_controls[0].destination.startswith(
        "https://accounts.google.com/")
    page.url = "https://accounts.google.com/signin"
    page.go_back()
    assert page.url == "https://accounts.google.com/signin"
    page.url = target
    page.backs.clear()

    result = submit.do_login(
        _login_config(target), allow_manual_takeover=True)
    stderr = capsys.readouterr().err

    expected = (
        "Page gate safety UNKNOWN for 'Continue to Members': origin recovery "
        "UNKNOWN: expected https://www.kink.com after going back, got "
        "https://accounts.google.com"
    )
    assert result == (False, expected, [])
    assert page.clicked == ["Continue to Members"]
    assert page.backs == ["https://accounts.google.com/signin"]
    assert fill_states == []
    assert submit_calls == []
    assert browser.close_count == 1
    assert stderr.count("login: origin_recovery_unknown") == 1


def test_do_login_auto_submit_gate_unknown_stops_before_submit_retry(
        monkeypatch, capsys):
    target = "https://unknown.example/login"
    page = _OriginRecoveryFailsPage(target)
    page.layers = ((),)
    submit, browser, fill_states, submit_calls = _patch_login_runtime(
        monkeypatch, page, fill_ok=True)

    def _fill_and_auto_submit(_page, _selectors, _value, what):
        fill_states.append((what, list(page.clicked)))
        if what == "password":
            page.layers = (
                (_Control(
                    "a.offer",
                    "Continue to Members",
                    1,
                    "https://accounts.google.com/signin",
                ),),
                (),
            )
            page.stage = 0
            page.url = "https://unknown.example/en/interstitial"
        return True, "fixture field"

    monkeypatch.setattr(submit, "_try_fill", _fill_and_auto_submit)

    # PRECONDITION: no gate precedes the form; password fill alone creates the
    # foreign-navigation wall, and the normal submit seam would be observable.
    assert len(page.visible_controls) == 0
    assert fill_states == []
    assert submit_calls == []

    result = submit.do_login(
        _login_config(target, success_url="/members"),
        allow_manual_takeover=False,
    )
    stderr = capsys.readouterr().err

    expected = (
        "Page gate safety UNKNOWN for 'Continue to Members': origin recovery "
        "UNKNOWN: expected https://unknown.example after going back, got "
        "https://accounts.google.com"
    )
    assert result == (False, expected, [])
    assert fill_states == [("username", []), ("password", [])]
    assert page.clicked == ["Continue to Members"]
    assert page.backs == ["https://accounts.google.com/signin"]
    assert submit_calls == []
    assert browser.close_count == 1
    assert stderr.count("login: origin_recovery_unknown") == 1


def test_do_login_clears_gate_even_when_fill_already_reached_success_url(
        monkeypatch, capsys):
    target = "https://unknown.example/login"
    page = _page_with_controls(target, [])
    submit, browser, fill_states, submit_calls = _patch_login_runtime(
        monkeypatch, page, fill_ok=True)

    def _fill_and_reach_success(_page, _selectors, _value, what):
        fill_states.append((what, list(page.clicked)))
        if what == "password":
            page.layers = ((
                _Control("button.consent", "Accept All", 1),
            ), ())
            page.stage = 0
            page.url = "https://unknown.example/members"
        return True, "fixture field"

    monkeypatch.setattr(submit, "_try_fill", _fill_and_reach_success)

    # PRECONDITION: password fill reaches the configured success URL but leaves
    # one visible consent gate; the ordinary submit seam is still observable.
    assert len(page.visible_controls) == 0
    assert fill_states == []
    assert submit_calls == []

    result = submit.do_login(
        _login_config(target, success_url="/members"),
        allow_manual_takeover=False,
    )
    stderr = capsys.readouterr().err

    assert result[0] is True, result[1]
    assert fill_states == [("username", []), ("password", [])]
    assert submit_calls == []
    assert page.clicked == ["Accept All"]
    assert stderr.count("login: cleared consent gate via 'Accept All'") == 1
    assert browser.close_count == 1


def test_do_login_generic_pass_clears_unknown_post_submit_interstitial(
        monkeypatch, capsys):
    target = "https://unknown.example/login"
    page = _page_with_controls(target, [])
    submit, browser, fill_states, submit_calls = _patch_login_runtime(
        monkeypatch, page, fill_ok=True)

    def _land_on_interstitial(_page, _submit_selectors, _password_selectors):
        submit_calls.append(list(page.clicked))
        page.layers = ((
            _Control(
                "a.offer",
                "No Thanks. Continue to Members",
                1,
                "https://unknown.example/members",
            ),
        ), ())
        page.stage = 0
        page.url = "https://unknown.example/en/interstitial"
        return True, "fixture submit"

    monkeypatch.setattr(submit, "_submit_login", _land_on_interstitial)

    # PRECONDITION: there is no gate before the form probes; exactly one submit
    # creates the interstitial, so a pre-form-only implementation cannot pass.
    assert len(page.visible_controls) == 0
    assert page.clicked == []
    assert submit_calls == []

    result = submit.do_login(
        _login_config(target, success_url="/members"),
        allow_manual_takeover=False,
    )
    stderr = capsys.readouterr().err

    assert result[0] is True, result[1]
    assert fill_states == [("username", []), ("password", [])]
    assert submit_calls == [[]]
    assert page.clicked == ["No Thanks. Continue to Members"]
    assert stderr.count(
        "login: cleared interstitial gate via "
        "'No Thanks. Continue to Members'") == 1
    assert browser.close_count == 1


def test_do_login_never_reports_success_after_post_submit_safety_unknown(
        monkeypatch, capsys):
    target = "https://unknown.example/login"
    page = _OriginRecoveryFailsPage(target)
    page.layers = ((),)
    submit, browser, fill_states, submit_calls = _patch_login_runtime(
        monkeypatch, page, fill_ok=True)

    def _land_on_foreign_interstitial(
            _page, _submit_selectors, _password_selectors):
        submit_calls.append(list(page.clicked))
        page.layers = (
            (_Control(
                "a.offer",
                "Continue to Members",
                1,
                "https://accounts.google.com/signin",
            ),),
            (),
        )
        page.stage = 0
        page.url = "https://unknown.example/en/interstitial"
        return True, "fixture submit"

    monkeypatch.setattr(submit, "_submit_login", _land_on_foreign_interstitial)

    # PRECONDITION: there is no pre-form gate; exactly one successful submit
    # reveals a control whose failed recovery leaves the browser on Google.
    assert len(page.visible_controls) == 0
    assert fill_states == []
    assert submit_calls == []

    result = submit.do_login(
        _login_config(target), allow_manual_takeover=False)
    stderr = capsys.readouterr().err

    expected = (
        "Page gate safety UNKNOWN for 'Continue to Members': origin recovery "
        "UNKNOWN: expected https://unknown.example after going back, got "
        "https://accounts.google.com"
    )
    assert result == (False, expected, [])
    assert fill_states == [("username", []), ("password", [])]
    assert submit_calls == [[]]
    assert page.clicked == ["Continue to Members"]
    assert page.backs == ["https://accounts.google.com/signin"]
    assert browser.close_count == 1
    assert stderr.count("login: origin_recovery_unknown") == 1


class _RunnerGateHarness:
    def __init__(self, dismiss_selectors=""):
        self.config = {"dismiss_selectors": dismiss_selectors}
        self.events = []
        self.job_updates = []

    def log_event(self, kind, message, url=None, extra=None):
        self.events.append({
            "kind": kind,
            "message": message,
            "url": url,
            "extra": extra,
        })

    def _update_job(self, url, state, message):
        self.job_updates.append((url, state, message))

    def _dismiss_page_gates(self, page, url):
        from bulk_downloader.runner import SiteRunner
        return SiteRunner._dismiss_page_gates(self, page, url)


class _ExtractionReached(BaseException):
    """Stop the real worker immediately after its page-gate boundary."""


class _NoControlLocator:
    @property
    def first(self):
        return self

    def count(self):
        return 0


class _ProcessPage:
    def __init__(self, url):
        self.url = url
        self.close_count = 0

    def goto(self, url, **_kwargs):
        self.url = url

    def evaluate(self, _script):
        return None

    def locator(self, _selector):
        return _NoControlLocator()

    def close(self):
        self.close_count += 1


class _ProcessContext:
    def __init__(self, page):
        self.page = page
        self.new_page_count = 0

    def new_page(self):
        self.new_page_count += 1
        return self.page


class _ProcessOneGateHarness:
    """Only the dependencies before ``_process_one``'s extraction seam."""

    def __init__(self, target, *, gate_safe):
        from contextlib import nullcontext

        self.site_id = "row383-fixture"
        self.config = {
            "wait": 0,
            "use_cluster_rate": False,
            "use_library_extractor": False,
            "use_jsonapi": False,
            "use_scrapling_turnstile": False,
            "use_flaresolverr": False,
        }
        self.jobs = {target: {}}
        self._lock = nullcontext()
        self._pause = SimpleNamespace(wait=lambda: None)
        self._stop = SimpleNamespace(is_set=lambda: False)
        self.gate_safe = gate_safe
        self.trace = []
        self.job_updates = []

    def _update_job(self, url, state, message, **_kwargs):
        self.job_updates.append((url, state, message))

    def _dedup_preflight(self, _url, _job):
        return None

    def _handle_auto_teach_check(self, _url, _job):
        return False

    def _check_cookies_or_relogin(self, _url):
        return True

    def _stash_dedup_check(self, _url):
        return False

    def _try_plugin_extractor(self, _url):
        return False

    def _apply_stealth_library_to_page(self, _page):
        return None

    def _install_event_listeners(self, _page, _url):
        return None

    def _warm_session(self, _page):
        return None

    def _handle_captcha_check(self, _page, _url):
        return True

    def _check_redirect(self, _page, _url):
        return None

    def _page_gates_are_safe(self, page, url):
        self.trace.append(("gate", page.url, url))
        return self.gate_safe

    def _draft_override_template(self):
        return None


def test_process_one_holds_before_extraction_when_page_gate_safety_is_unknown(
        monkeypatch):
    from bulk_downloader import runner as runner_module
    from bulk_downloader.runner import SiteRunner

    target = "https://members.example/scenes/383-unknown"
    page = _ProcessPage(target)
    context = _ProcessContext(page)
    runner = _ProcessOneGateHarness(target, gate_safe=False)

    def _extraction_started(*_args, **_kwargs):
        runner.trace.append(("extract", page.url, target))
        raise _ExtractionReached

    monkeypatch.setattr(
        runner_module, "merge_template_download_hints", _extraction_started)

    # PRECONDITION: this is a browser-path job whose structured gate verdict
    # is non-success; the extraction sentinel has not fired yet.
    assert runner.gate_safe is False
    assert runner.trace == []
    assert context.new_page_count == 0

    SiteRunner._process_one(
        runner, browser=None, url=target, persistent_ctx=context)

    assert runner.trace == [("gate", target, target)]
    assert sum(kind == "gate" for kind, *_ in runner.trace) == 1
    assert sum(kind == "extract" for kind, *_ in runner.trace) == 0
    assert context.new_page_count == 1
    assert page.close_count == 1


def test_process_one_proceeds_to_extraction_after_safe_page_gate_verdict(
        monkeypatch):
    from bulk_downloader import runner as runner_module
    from bulk_downloader.runner import SiteRunner

    target = "https://members.example/scenes/383-safe"
    page = _ProcessPage(target)
    context = _ProcessContext(page)
    runner = _ProcessOneGateHarness(target, gate_safe=True)

    def _extraction_started(*_args, **_kwargs):
        runner.trace.append(("extract", page.url, target))
        raise _ExtractionReached

    monkeypatch.setattr(
        runner_module, "merge_template_download_hints", _extraction_started)

    # NEGATIVE CONTROL: the same real worker path is allowed through when the
    # structured orchestrator returns an explicit safe verdict.
    assert runner.gate_safe is True
    assert runner.trace == []
    with pytest.raises(_ExtractionReached):
        SiteRunner._process_one(
            runner, browser=None, url=target, persistent_ctx=context)

    assert runner.trace == [
        ("gate", target, target),
        ("extract", target, target),
    ]
    assert sum(kind == "gate" for kind, *_ in runner.trace) == 1
    assert sum(kind == "extract" for kind, *_ in runner.trace) == 1
    assert context.new_page_count == 1
    assert page.close_count == 1


def test_content_worker_rewaits_render_budget_only_after_destination_rerequest(
        monkeypatch):
    from bulk_downloader import runner as runner_module
    from bulk_downloader.runner import SiteRunner

    target = "https://members.adulttime.com/en/video/scene-371"
    trace = []
    runner = _RunnerGateHarness()
    runner.config["wait"] = 1
    runner._pause = SimpleNamespace(wait=lambda: trace.append("pause"))
    runner._stop = SimpleNamespace(is_set=lambda: False)

    action = {
        "outcome": "cleared",
        "label": "No Thanks. Continue to Members",
        "destination_re_requested": True,
    }

    # PRECONDITION: the measured action says the scene was requested again,
    # the configured render budget is exactly two half-second ticks, and no
    # prior wait or terminal update has happened.
    assert action["destination_re_requested"] is True
    assert int(float(runner.config["wait"]) * 2) == 2
    assert trace == []
    assert runner.job_updates == []

    runner._dismiss_page_gates = lambda _page, _url: (
        trace.append("gate") or [action])
    monkeypatch.setattr(
        runner_module.time,
        "sleep",
        lambda seconds: trace.append(("sleep", seconds)),
    )

    assert SiteRunner._page_gates_are_safe(
        runner, object(), target) is True
    trace.append("extract")
    assert trace == [
        "gate",
        "pause", ("sleep", 0.5),
        "pause", ("sleep", 0.5),
        "extract",
    ]
    assert trace.count("pause") == 2
    assert trace.count(("sleep", 0.5)) == 2
    assert runner.job_updates == []

    # NEGATIVE CONTROL: a cleared gate that did not navigate must not impose a
    # second page-render delay on every ordinary URL.
    trace.clear()
    action["destination_re_requested"] = False
    runner._dismiss_page_gates = lambda _page, _url: (
        trace.append("gate") or [action])
    assert SiteRunner._page_gates_are_safe(
        runner, object(), target) is True
    trace.append("extract")
    assert trace == ["gate", "extract"]
    assert trace.count("pause") == 0
    assert trace.count(("sleep", 0.5)) == 0
    assert runner.job_updates == []


def test_content_worker_re_requests_scene_and_reports_exact_gate_action():
    from bulk_downloader.runner import SiteRunner

    target = "https://members.adulttime.com/en/video/scene-371"
    page = _page_with_controls(target, [
        _Control(
            "a.offer",
            "No Thanks. Continue to Members",
            1,
            "https://members.adulttime.com/en/members",
        ),
    ])
    page.url = "https://members.adulttime.com/en/interstitial"
    runner = _RunnerGateHarness()

    # PRECONDITION: the requested scene has been replaced by the measured
    # same-origin interstitial, and its sole control lands on members home.
    assert page.url.endswith("/en/interstitial")
    assert len(page.visible_controls) == 1
    assert page.visible_controls[0].destination.endswith("/en/members")
    assert target.endswith("/en/video/scene-371")
    assert runner.events == []

    assert hasattr(SiteRunner, "_dismiss_page_gates"), (
        "row 371: SiteRunner has no reported page-gate seam; _process_one "
        "still discards dismissal outcomes"
    )
    actions = SiteRunner._dismiss_page_gates(runner, page, target)

    assert page.clicked == ["No Thanks. Continue to Members"]
    assert page.gotos == [target]
    assert page.backs == []
    assert len(actions) == 1
    assert actions[0]["outcome"] == "cleared"
    assert actions[0]["destination_re_requested"] is True
    assert len(runner.events) == 1
    assert runner.events[0] == {
        "kind": "page_gate",
        "message": (
            "cleared interstitial gate via "
            "'No Thanks. Continue to Members'; destination re-requested"
        ),
        "url": target,
        "extra": actions[0],
    }

    # NEGATIVE CONTROL: a normal scene produces no action and no report. A
    # reporter that emits on every page would hide the signal in noise.
    clear_page = _page_with_controls(target, [])
    clear_runner = _RunnerGateHarness()
    assert len(clear_page.visible_controls) == 0
    clear_actions = SiteRunner._dismiss_page_gates(
        clear_runner, clear_page, target)
    assert clear_actions == []
    assert clear_page.clicked == []
    assert clear_page.gotos == []
    assert clear_page.backs == []
    assert clear_runner.events == []


def test_content_worker_stops_before_extraction_on_gate_safety_unknown():
    from bulk_downloader.runner import SiteRunner

    target = "https://www.kink.com/scenes/371"
    page = _OriginRecoveryFailsPage(target)
    page.layers = (
        (_Control(
            "a.continue",
            "Continue to Members",
            1,
            "https://accounts.google.com/signin",
        ),),
        (),
    )
    runner = _RunnerGateHarness()

    # PRECONDITION: failed history recovery leaves the worker on a foreign
    # origin, and no job terminal/update has happened before the gate seam.
    assert len(page.visible_controls) == 1
    page.url = "https://accounts.google.com/signin"
    page.go_back()
    assert page.url == "https://accounts.google.com/signin"
    page.url = target
    page.backs.clear()
    assert runner.job_updates == []

    assert hasattr(SiteRunner, "_page_gates_are_safe"), (
        "row 371: content worker has no fail-closed gate guard")
    safe = SiteRunner._page_gates_are_safe(runner, page, target)

    expected = (
        "Page gate safety UNKNOWN for 'Continue to Members': origin recovery "
        "UNKNOWN: expected https://www.kink.com after going back, got "
        "https://accounts.google.com"
    )
    assert safe is False
    assert page.clicked == ["Continue to Members"]
    assert page.backs == ["https://accounts.google.com/signin"]
    assert len(runner.events) == 1
    assert runner.events[0]["extra"]["outcome"] == (
        "origin_recovery_unknown")
    assert runner.job_updates == [(target, "needs_review", expected)]


def test_measured_site_templates_store_their_gate_selectors_in_order():
    from bulk_downloader.site_templates import _data_studios_a

    by_id = {item["id"]: item for item in _data_studios_a.ITEMS}

    # PRECONDITION: all three independently measured site templates are in the
    # population. A missing template is not an empty/green selector verdict.
    assert len(by_id) > 10
    assert sum(site_id in by_id for site_id in (
        "kink_network", "adulttime_network", "evilangel")) == 3

    kink_raw = by_id["kink_network"]["config_defaults"].get(
        "dismiss_selectors", "")
    kink_lines = interstitial.selector_lines(kink_raw)
    assert kink_lines == [
        "button[data-cky-tag='accept-button'], button.cky-btn-accept",
        "button:text-is('I Agree, Enter Here'), "
        "a:text-is('I Agree, Enter Here')",
        "button:text-is('LOG IN'), a:text-is('LOG IN')",
    ], (
        "Kink must store exactly the three measured controls in encounter "
        f"order; got {kink_lines!r}"
    )

    for site_id in ("adulttime_network", "evilangel"):
        raw = by_id[site_id]["config_defaults"].get(
            "dismiss_selectors", "")
        lines = interstitial.selector_lines(raw)
        assert lines == [
            "button:text-is('No Thanks'), a:text-is('No Thanks')"
        ], (f"{site_id} must store exactly the measured upsell control; "
            f"got {lines!r}")

    # NEGATIVE CONTROL: measured selectors may name the safe "I Agree" side,
    # but none may encode an exit/decline action that bypasses the runtime
    # denylist by being site-specific.
    measured_blob = "\n".join(
        by_id[site_id]["config_defaults"].get("dismiss_selectors", "")
        for site_id in ("kink_network", "adulttime_network", "evilangel")
    ).casefold()
    for denied in interstitial.DENIED_CONTROL_TERMS:
        assert denied not in measured_blob, (
            f"site template contains denylisted control {denied!r}")

    # PRECONDITION: production auto-pick chooses the older platform template
    # first for both domains. Inspecting only the lower-priority studio records
    # would therefore prove data that ordinary site creation never receives.
    from bulk_downloader import templates
    cases = {
        "https://members.adulttime.com/en/video/371": (
            "adulttime_network"),
        "https://members.evilangel.com/en/video/371": "evilangel",
    }
    for url, specific_id in cases.items():
        suggestions = templates.suggest_for_url(url)
        assert suggestions[:2] == ["gamma_kosmos", specific_id], (
            f"template precedence fixture changed for {url}: {suggestions!r}")
        effective = templates.get(suggestions[0])
        effective_lines = interstitial.selector_lines(
            effective["config_defaults"].get("dismiss_selectors", ""))
        assert effective_lines == [
            "button:has-text('I Agree'), "
            "button[aria-label*='close' i], "
            "button:text-is('No Thanks'), a:text-is('No Thanks')"
        ], (
            f"auto-picked {suggestions[0]!r} omits the measured scene upsell "
            f"for {url}: {effective_lines!r}")


@pytest.mark.capture_serial
def test_measured_text_selectors_match_safe_controls_not_longer_neighbors():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright unavailable — selector measurement UNKNOWN")

    from bulk_downloader.site_templates import _data_studios_a
    by_id = {item["id"]: item for item in _data_studios_a.ITEMS}
    kink_lines = interstitial.selector_lines(
        by_id["kink_network"]["config_defaults"]["dismiss_selectors"])
    kink_cookie, kink_age, kink_login = kink_lines
    adult_upsell = interstitial.selector_lines(
        by_id["adulttime_network"]["config_defaults"][
            "dismiss_selectors"])[0]
    from bulk_downloader import templates
    gamma_upsell = interstitial.selector_lines(
        templates.get("gamma_kosmos")["config_defaults"][
            "dismiss_selectors"])[0]

    pw = sync_playwright().start()
    browser = None
    try:
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception as exc:
            pytest.skip(f"chromium unavailable — selector measurement UNKNOWN: {exc}")
        page = browser.new_page()
        page.set_content("""
          <button data-cky-tag="accept-button">Accept All Cookies</button>
          <button>I Agree, Enter Here later</button>
          <button>I Agree, Enter Here</button>
          <button>BLOG INDEX</button>
          <button>LOG IN</button>
          <button>No Thanks for the newsletter</button>
          <button>No Thanks</button>
        """)

        # PRECONDITION: the real selector engine sees all four measured safe
        # controls and their three longer/unrelated neighbours as distinct.
        assert page.locator("button").count() == 7
        assert page.locator("button").all_inner_texts() == [
            "Accept All Cookies",
            "I Agree, Enter Here later", "I Agree, Enter Here",
            "BLOG INDEX", "LOG IN",
            "No Thanks for the newsletter", "No Thanks"]

        cookie_matches = page.locator(kink_cookie)
        age_matches = page.locator(kink_age)
        kink_matches = page.locator(kink_login)
        adult_matches = page.locator(adult_upsell)
        gamma_matches = page.locator(gamma_upsell)
        assert cookie_matches.count() == 1
        assert cookie_matches.first.inner_text() == "Accept All Cookies"
        assert age_matches.count() == 1
        assert age_matches.first.inner_text() == "I Agree, Enter Here"
        assert kink_matches.count() == 1
        assert kink_matches.first.inner_text() == "LOG IN"
        assert adult_matches.count() == 1
        assert adult_matches.first.inner_text() == "No Thanks"
        assert gamma_matches.count() == 3
        assert gamma_matches.all_inner_texts() == [
            "I Agree, Enter Here later", "I Agree, Enter Here"] + [
            "No Thanks"]
    finally:
        if browser is not None:
            browser.close()
        pw.stop()


@pytest.mark.capture_serial
def test_aggregate_site_wait_is_not_masked_by_a_hidden_first_match():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright unavailable — appearance measurement UNKNOWN")

    pw = sync_playwright().start()
    browser = None
    try:
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception as exc:
            pytest.skip(f"chromium unavailable — appearance measurement UNKNOWN: {exc}")
        page = browser.new_page()
        page.set_content("""
          <button id="hidden" style="display:none">Hidden measured gate</button>
          <button id="later" style="display:none">Open measured layer</button>
          <script>
            setTimeout(() => {
              document.querySelector('#later').style.display = 'block';
            }, 250);
          </script>
        """)

        # PRECONDITION: both measured selectors resolve, the first remains
        # hidden, and the second starts hidden but is scheduled to appear.
        assert page.locator("#hidden").count() == 1
        assert page.locator("#later").count() == 1
        assert page.locator("#hidden").is_visible() is False
        assert page.locator("#later").is_visible() is False

        # The click budget is deliberately generous: this test measures the
        # AGGREGATE APPEARANCE WAIT (site_appear_ms), and a 50 ms click budget
        # made the real chromium click race the machine under a parallel band
        # -- observed once as a correct-but-unwanted 'click_unknown'. Raising
        # the budget removes the race; every assertion below is unchanged.
        actions = interstitial.dismiss_gates(
            page,
            "#hidden\n#later",
            timeout_ms=5000,
            site_appear_ms=3000,
            settle_s=0,
        )

        assert page.locator("#hidden").is_visible() is False
        assert len(actions) == 1
        assert actions[0]["source"] == "site"
        assert actions[0]["label"] == "Open measured layer"
        assert actions[0]["outcome"] == "cleared"
    finally:
        if browser is not None:
            browser.close()
        pw.stop()


@pytest.mark.capture_serial
def test_comma_group_site_selector_binds_the_visible_alternative():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright unavailable — selector measurement UNKNOWN")

    pw = sync_playwright().start()
    browser = None
    try:
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception as exc:
            pytest.skip(
                f"chromium unavailable — selector measurement UNKNOWN: {exc}")
        page = browser.new_page()
        page.set_content("""
          <button style="display:none">LOG IN</button>
          <a href="#login">LOG IN</a>
          <script>
            window.clicked = [];
            document.querySelector('a').addEventListener('click', event => {
              event.preventDefault();
              window.clicked.push('visible anchor');
            });
          </script>
        """)
        selector = "button:text-is('LOG IN'), a:text-is('LOG IN')"
        matches = page.locator(selector)

        # PRECONDITION: one declared comma-group resolves exactly two controls;
        # its first alternative is hidden and its second is the visible target.
        assert matches.count() == 2
        assert matches.nth(0).is_visible() is False
        assert matches.nth(1).is_visible() is True
        assert page.evaluate("window.clicked") == []

        # Generous click budget for the same reason as the appearance test
        # above: the subject here is WHICH alternative of the comma group gets
        # bound, not how long a click may take on a loaded box.
        actions = interstitial.dismiss_gates(
            page, selector, timeout_ms=5000, settle_s=0)

        assert page.evaluate("window.clicked") == ["visible anchor"]
        assert len(actions) == 1
        assert actions[0]["source"] == "site"
        assert actions[0]["label"] == "LOG IN"
        assert actions[0]["outcome"] == "cleared"
    finally:
        if browser is not None:
            browser.close()
        pw.stop()


def test_generic_layer_can_reveal_a_measured_login_modal_control():
    target = "https://www.kink.com/login"
    page = _StackedPage(target)
    page.layers = (
        (_Control("button.cookie", "Accept All", 1),),
        (_Control("button.login", "LOG IN", 2),),
        (),
    )

    # PRECONDITION: the only declared selector is hidden behind an undeclared
    # consent layer, so the initial measured pass cannot see it.
    assert len(page.layers) == 3
    assert page.visible_controls[0].label == "Accept All"
    assert page.locator("button.login")._control.label == ""

    actions = interstitial.dismiss_gates(
        page,
        "button.login",
        site_appear_ms=0,
        timeout_ms=1,
        settle_s=0,
        sleep=lambda _seconds: None,
    )

    assert page.clicked == ["Accept All", "LOG IN"]
    assert len(actions) == 2
    assert [action["source"] for action in actions] == ["generic", "site"]
    assert [action["tier"] for action in actions] == ["consent", "site"]
    assert [action["outcome"] for action in actions] == ["cleared", "cleared"]


def test_manual_window_runs_same_gate_pass_before_operator_sees_page(
        monkeypatch, capsys):
    from bulk_downloader import cloak, stealth
    from bulk_downloader.login_impl.manual import ManualLoginSession

    target = "https://www.kink.com/login"
    page = _page_with_controls(
        target, [_Control("button.age", "I Agree, Enter Here", 1)])
    browser = _LoginBrowser(page)
    session = object.__new__(ManualLoginSession)
    session._config = {
        "name": "Kink fixture",
        "login_url": target,
        "username": "",
        "password": "",
        "use_real_chrome": False,
        "use_stealth": False,
        "use_stealth_library": False,
        "dismiss_selectors": "",
    }
    session._banner_js = "/* row371 banner */"
    session._manual_profile_dir = None
    session._headless = False

    monkeypatch.setattr(
        cloak,
        "launch_browser",
        lambda **_kwargs: (browser, None, "row371-fake"),
    )
    monkeypatch.setattr(cloak, "log_choice", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(stealth, "apply_to_page", lambda _page, _cfg: None)

    # PRECONDITION: this is the direct manual-launch lifecycle, with exactly
    # one gate visible and no prior click/report from do_login.
    assert len(page.visible_controls) == 1
    assert page.visible_controls[0].label == "I Agree, Enter Here"
    assert page.clicked == []

    launched_browser, context, launched_page, used_pw = session._launch()
    stderr = capsys.readouterr().err

    assert launched_browser is browser
    assert context is browser.context
    assert launched_page is page
    assert used_pw is None
    assert page.clicked == ["I Agree, Enter Here"]
    assert stderr.count(
        "manual_login: cleared age gate via 'I Agree, Enter Here'") == 1


def test_manual_window_withholds_saved_credentials_after_safety_unknown(
        monkeypatch, capsys):
    from bulk_downloader import cloak, secrets_store, stealth
    from bulk_downloader.login_impl.manual import ManualLoginSession

    target = "https://www.kink.com/login"
    page = _OriginRecoveryFailsPage(target)
    page.layers = (
        (_Control(
            "a.continue",
            "Continue to Members",
            1,
            "https://accounts.google.com/signin",
        ),),
        (),
    )
    browser = _LoginBrowser(page)
    session = object.__new__(ManualLoginSession)
    session._config = {
        "name": "Kink unsafe fixture",
        "login_url": target,
        "username": "operator",
        "password": "saved-secret",
        "use_real_chrome": False,
        "use_stealth": False,
        "use_stealth_library": False,
        "dismiss_selectors": "",
    }
    session._banner_js = "/* row371 banner */"
    session._manual_profile_dir = None
    session._headless = False
    resolve_calls = []

    monkeypatch.setattr(
        cloak,
        "launch_browser",
        lambda **_kwargs: (browser, None, "row371-fake"),
    )
    monkeypatch.setattr(cloak, "log_choice", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(stealth, "apply_to_page", lambda _page, _cfg: None)
    monkeypatch.setattr(
        secrets_store,
        "resolve_password",
        lambda value: resolve_calls.append(value) or value,
    )

    # PRECONDITION: the session carries non-empty saved credentials, the click
    # leaves Kink, and the fake history operation demonstrably cannot return.
    assert session._config["username"] == "operator"
    assert session._config["password"] == "saved-secret"
    assert len(page.visible_controls) == 1
    page.url = "https://accounts.google.com/signin"
    page.go_back()
    assert page.url == "https://accounts.google.com/signin"
    page.url = target
    page.backs.clear()

    launched_browser, context, launched_page, used_pw = session._launch()
    stderr = capsys.readouterr().err

    assert launched_browser is browser
    assert context is browser.context
    assert launched_page is page
    assert used_pw is None
    assert page.clicked == ["Continue to Members"]
    assert page.backs == ["https://accounts.google.com/signin"]
    assert resolve_calls == []
    assert stderr.count(
        "manual_login: credential autofill withheld — Page gate safety "
        "UNKNOWN for 'Continue to Members': origin recovery UNKNOWN: "
        "expected https://www.kink.com after going back, got "
        "https://accounts.google.com") == 1


def test_generic_scan_never_waits_a_timeout_for_each_ordinary_control():
    class _VisibilityLocator:
        def __init__(self, page, control):
            self.page = page
            self.control = control

        def is_visible(self):
            self.page.visibility_checks += 1
            return self.control[1]

        def wait_for(self, **_kwargs):
            self.page.timeout_waits += 1
            if not self.control[1]:
                raise RuntimeError("hidden")

        def inner_text(self, **_kwargs):
            self.page.label_reads += 1
            return self.control[0]

        def get_attribute(self, _name, **_kwargs):
            self.page.label_reads += 1
            return None

        def click(self, **_kwargs):
            self.page.clicked.append(self.control[0])

    class _VisibilityList:
        def __init__(self, page):
            self.page = page

        def count(self):
            return len(self.page.controls)

        def evaluate_all(self, _script):
            self.page.snapshot_calls += 1
            return [
                {"index": index, "visible": visible, "labels": [label]}
                for index, (label, visible) in enumerate(self.page.controls)
            ]

        def nth(self, index):
            self.page.nth_calls += 1
            return _VisibilityLocator(self.page, self.page.controls[index])

    class _VisibilityPage:
        url = "https://ordinary.example/login"

        def __init__(self):
            self.controls = [
                (f"Ordinary link {index}", True) for index in range(499)
            ] + [("Accept All", True)]
            self.visibility_checks = 0
            self.timeout_waits = 0
            self.label_reads = 0
            self.snapshot_calls = 0
            self.nth_calls = 0
            self.clicked = []

        def locator(self, selector):
            assert selector == interstitial.GENERIC_CONTROL_SELECTOR
            return _VisibilityList(self)

        def wait_for_load_state(self, **_kwargs):
            return None

    page = _VisibilityPage()

    # PRECONDITION: this production-sized population has 500 visible controls
    # and exactly one safe consent action, deliberately at the final index.
    assert len(page.controls) == 500
    assert sum(visible for _label, visible in page.controls) == 500
    assert sum(label == "Accept All" for label, _visible in page.controls) == 1
    assert page.controls[-1] == ("Accept All", True)

    actions = interstitial.dismiss_gates(
        page, "", timeout_ms=3000, settle_s=0,
        sleep=lambda _seconds: None)

    assert page.clicked == ["Accept All"]
    assert len(actions) == 1
    assert actions[0]["tier"] == "consent"
    assert page.snapshot_calls == 3
    assert page.nth_calls == 1
    assert page.visibility_checks == 1
    assert page.label_reads == 4
    assert page.timeout_waits == 0, (
        "generic scanning used per-control remote calls; the always-on pass "
        f"would stall ordinary pages ({page.timeout_waits} waits observed)"
    )


def test_generic_snapshot_candidate_is_revalidated_before_click():
    class _ChangedLocator:
        def __init__(self, page):
            self.page = page

        def is_visible(self):
            self.page.visibility_checks += 1
            return True

        def inner_text(self, **_kwargs):
            self.page.label_reads += 1
            return "Exit"

        def get_attribute(self, _name, **_kwargs):
            self.page.label_reads += 1
            return None

        def click(self, **_kwargs):
            self.page.clicked.append("Exit")

    class _ChangingList:
        def __init__(self, page):
            self.page = page

        def evaluate_all(self, _script):
            self.page.snapshot_calls += 1
            return [{"index": 0, "visible": True, "labels": ["Accept All"]}]

        def nth(self, index):
            assert index == 0
            self.page.nth_calls += 1
            return _ChangedLocator(self.page)

    class _ChangingPage:
        url = "https://unknown.example/login"

        def __init__(self):
            self.snapshot_calls = 0
            self.nth_calls = 0
            self.visibility_checks = 0
            self.label_reads = 0
            self.clicked = []

        def locator(self, selector):
            assert selector == interstitial.GENERIC_CONTROL_SELECTOR
            return _ChangingList(self)

    page = _ChangingPage()
    control_list = page.locator(interstitial.GENERIC_CONTROL_SELECTOR)

    # PRECONDITION: the bulk snapshot says index zero is the safe consent
    # action, but resolving that live index yields the denylisted Exit control.
    assert control_list.evaluate_all("fixture") == [
        {"index": 0, "visible": True, "labels": ["Accept All"]}]
    assert control_list.nth(0).inner_text() == "Exit"
    assert page.snapshot_calls == 1
    assert page.nth_calls == 1
    page.snapshot_calls = 0
    page.nth_calls = 0
    page.label_reads = 0

    actions = interstitial.dismiss_gates(
        page, "", timeout_ms=1, settle_s=0, sleep=lambda _seconds: None)

    assert page.clicked == []
    assert len(actions) == 1
    assert actions[0]["outcome"] == "refused"
    assert actions[0]["label"] == "Exit"
    assert actions[0]["reason"] == (
        "denylisted label matched 'exit'; control not used")
    assert page.snapshot_calls == 3
    assert page.nth_calls == 1
    assert page.visibility_checks == 1
    assert page.label_reads == 4


def test_generic_snapshot_safe_to_safe_index_drift_is_unknown():
    class _ChangedLocator:
        def __init__(self, page):
            self.page = page

        def is_visible(self):
            self.page.visibility_checks += 1
            return True

        def inner_text(self, **_kwargs):
            self.page.label_reads += 1
            return "Allow All"

        def get_attribute(self, _name, **_kwargs):
            self.page.label_reads += 1
            return None

        def click(self, **_kwargs):
            self.page.clicked.append("Allow All")

    class _ChangingList:
        def __init__(self, page):
            self.page = page

        def evaluate_all(self, _script):
            self.page.snapshot_calls += 1
            return [{"index": 0, "visible": True,
                     "labels": ["Accept All"]}]

        def nth(self, index):
            assert index == 0
            self.page.nth_calls += 1
            return _ChangedLocator(self.page)

    class _ChangingPage:
        url = "https://unknown.example/login"

        def __init__(self):
            self.snapshot_calls = 0
            self.nth_calls = 0
            self.visibility_checks = 0
            self.label_reads = 0
            self.clicked = []

        def locator(self, selector):
            assert selector == interstitial.GENERIC_CONTROL_SELECTOR
            return _ChangingList(self)

    page = _ChangingPage()
    controls = page.locator(interstitial.GENERIC_CONTROL_SELECTOR)

    # PRECONDITION: the snapshot selects safe "Accept All", while the live
    # index has drifted to a different but also-safe "Allow All" control. The
    # denylist cannot catch this race; identity binding must.
    assert controls.evaluate_all("fixture") == [
        {"index": 0, "visible": True, "labels": ["Accept All"]}]
    assert controls.nth(0).inner_text() == "Allow All"
    page.snapshot_calls = 0
    page.nth_calls = 0
    page.label_reads = 0

    actions = interstitial.dismiss_gates(
        page, "", timeout_ms=1, settle_s=0,
        sleep=lambda _seconds: None)

    assert page.clicked == []
    assert len(actions) == 1
    assert actions[0]["outcome"] == "measurement_unknown"
    assert actions[0]["reason"] == (
        "generic consent matched control changed after snapshot; "
        "control not used")
    assert page.snapshot_calls == 3
    assert page.nth_calls == 1
    assert page.visibility_checks == 1
    assert page.label_reads == 4


def test_duck_typed_site_visibility_failure_is_unknown_but_timeout_is_absent():
    class _SiteLocator:
        @property
        def first(self):
            return self

        def __init__(self, error):
            self.error = error

        def wait_for(self, **_kwargs):
            raise self.error

    class _EmptyGeneric:
        def evaluate_all(self, _script):
            return []

    class _Page:
        url = "https://known.example/login"

        def __init__(self, error):
            self.error = error

        def locator(self, selector):
            if selector == interstitial.GENERIC_CONTROL_SELECTOR:
                return _EmptyGeneric()
            return _SiteLocator(self.error)

    broken = _Page(RuntimeError("page closed"))
    absent = _Page(TimeoutError("not visible"))

    # PRECONDITION: neither duck-typed locator supplies is_visible; one wait
    # loses measurement, while the negative control is an explicit timeout.
    assert not hasattr(broken.locator("button.measured"), "is_visible")
    with pytest.raises(RuntimeError, match="page closed"):
        broken.locator("button.measured").wait_for()
    with pytest.raises(TimeoutError, match="not visible"):
        absent.locator("button.measured").wait_for()

    broken_actions = interstitial.dismiss_gates(
        broken, "button.measured", site_appear_ms=0, timeout_ms=1,
        settle_s=0, sleep=lambda _seconds: None)
    absent_actions = interstitial.dismiss_gates(
        absent, "button.measured", site_appear_ms=0, timeout_ms=1,
        settle_s=0, sleep=lambda _seconds: None)

    assert len(broken_actions) == 1
    assert broken_actions[0]["outcome"] == "measurement_unknown"
    assert broken_actions[0]["reason"] == (
        "site control visibility UNKNOWN: RuntimeError")
    assert absent_actions == []


def test_duck_typed_generic_visibility_failure_is_unknown_but_timeout_is_absent():
    class _Control:
        def __init__(self, error):
            self.error = error

        def wait_for(self, **_kwargs):
            raise self.error

    class _Controls:
        def __init__(self, error):
            self.error = error

        def count(self):
            return 1

        def nth(self, index):
            assert index == 0
            return _Control(self.error)

    class _Page:
        url = "https://unknown.example/login"

        def __init__(self, error):
            self.error = error

        def locator(self, selector):
            assert selector == interstitial.GENERIC_CONTROL_SELECTOR
            return _Controls(self.error)

    broken = _Page(RuntimeError("protocol gone"))
    absent = _Page(TimeoutError("not visible"))

    # PRECONDITION: the generic population has exactly one enumerated control,
    # but its visibility wait respectively fails or times out.
    assert broken.locator(interstitial.GENERIC_CONTROL_SELECTOR).count() == 1
    assert absent.locator(interstitial.GENERIC_CONTROL_SELECTOR).count() == 1

    broken_actions = interstitial.dismiss_gates(
        broken, "", timeout_ms=1, settle_s=0,
        sleep=lambda _seconds: None)
    absent_actions = interstitial.dismiss_gates(
        absent, "", timeout_ms=1, settle_s=0,
        sleep=lambda _seconds: None)

    assert len(broken_actions) == 1
    assert broken_actions[0]["outcome"] == "measurement_unknown"
    assert broken_actions[0]["reason"] == (
        "generic consent control visibility UNKNOWN at index 0: RuntimeError")
    assert absent_actions == []


def test_missing_measured_kink_controls_use_immediate_visibility_checks():
    class _MissingSiteLocator:
        @property
        def first(self):
            return self

        def __init__(self, page):
            self.page = page

        def is_visible(self):
            self.page.visibility_checks += 1
            return False

        def filter(self, *, visible=None):
            assert visible is True
            return self

        def wait_for(self, **_kwargs):
            self.page.timeout_waits += 1
            raise TimeoutError("not visible")

    class _EmptyGenericList:
        def evaluate_all(self, _script):
            return []

    class _NoGatePage:
        url = "https://www.kink.com/scenes/371"

        def __init__(self):
            self.visibility_checks = 0
            self.timeout_waits = 0
            self.site_lookups = []

        def locator(self, selector):
            if selector == interstitial.GENERIC_CONTROL_SELECTOR:
                return _EmptyGenericList()
            self.site_lookups.append(selector)
            return _MissingSiteLocator(self)

    page = _NoGatePage()
    measured = "button.cookie\nbutton.age\nbutton.login"

    # PRECONDITION: exactly three measured lines are absent on an otherwise
    # measurable page, reproducing the normal authenticated Kink content URL.
    assert interstitial.selector_lines(measured) == [
        "button.cookie", "button.age", "button.login"]
    assert page.timeout_waits == 0

    actions = interstitial.dismiss_gates(
        page, measured, timeout_ms=3000, settle_s=0,
        sleep=lambda _seconds: None)

    assert actions == []
    assert page.site_lookups == [
        "button.cookie", "button.age", "button.login",
        ":is(button.cookie, button.age, button.login):visible",
    ]
    assert page.visibility_checks == 3
    assert page.timeout_waits == 1


def test_measured_appearance_window_preserves_the_shipped_three_seconds():
    # PRECONDITION: the legacy visible-selector wait was exactly 3000 ms; the
    # union optimization may remove N-times cost but may not shorten tolerance.
    assert interstitial.DEFAULT_TIMEOUT_MS == 3000
    assert interstitial.DEFAULT_SITE_APPEAR_MS == 3000


def test_measured_controls_get_one_bounded_aggregate_appearance_wait():
    class _DelayedLocator:
        @property
        def first(self):
            return self

        def __init__(self, page, selector):
            self.page = page
            self.selector = selector

        def is_visible(self):
            self.page.visibility_checks += 1
            return self.selector == "button.age" and self.page.age_visible

        def filter(self, *, visible=None):
            assert visible is True
            return self

        def wait_for(self, **kwargs):
            self.page.wait_calls.append((self.selector, kwargs))
            self.page.age_visible = True

        def inner_text(self, **_kwargs):
            return "I Agree, Enter Here"

        def get_attribute(self, _name, **_kwargs):
            return None

        def click(self, **_kwargs):
            self.page.clicked.append("I Agree, Enter Here")

    class _EmptyGenericList:
        def evaluate_all(self, _script):
            return []

    class _DelayedPage:
        url = "https://www.kink.com/login"

        def __init__(self):
            self.age_visible = False
            self.visibility_checks = 0
            self.wait_calls = []
            self.clicked = []

        def locator(self, selector):
            if selector == interstitial.GENERIC_CONTROL_SELECTOR:
                return _EmptyGenericList()
            return _DelayedLocator(self, selector)

        def wait_for_load_state(self, **_kwargs):
            return None

    page = _DelayedPage()
    measured = "button.cookie\nbutton.age\nbutton.login"

    # PRECONDITION: all three declared controls are initially absent; the one
    # aggregate wait makes only the age control appear.
    assert interstitial.selector_lines(measured) == [
        "button.cookie", "button.age", "button.login"]
    assert page.age_visible is False
    assert page.clicked == []

    actions = interstitial.dismiss_gates(
        page,
        measured,
        timeout_ms=3000,
        site_appear_ms=3000,
        settle_s=0,
        sleep=lambda _seconds: None,
    )

    assert page.wait_calls == [(
        ":is(button.cookie, button.age, button.login):visible",
        {"state": "attached", "timeout": 3000},
    )]
    assert page.clicked == ["I Agree, Enter Here"]
    assert len(actions) == 1
    assert actions[0]["source"] == "site"
    assert actions[0]["outcome"] == "cleared"


def test_unavailable_generic_control_enumeration_is_unknown_not_empty():
    class _CountFails:
        def count(self):
            raise RuntimeError("page closed during count")

    class _CountFailsPage:
        url = "https://unknown.example/login"

        def __init__(self):
            self.locator_calls = []

        def locator(self, selector):
            self.locator_calls.append(selector)
            return _CountFails()

    page = _CountFailsPage()

    # PRECONDITION: the generic population itself cannot be enumerated. This
    # is not evidence that the population is empty.
    controls = page.locator(interstitial.GENERIC_CONTROL_SELECTOR)
    try:
        controls.count()
    except RuntimeError as exc:
        assert str(exc) == "page closed during count"
    else:
        raise AssertionError("fixture unexpectedly measured an empty population")
    page.locator_calls.clear()

    actions = interstitial.dismiss_gates(
        page, "", timeout_ms=1, settle_s=0, sleep=lambda _seconds: None)

    assert page.locator_calls == [interstitial.GENERIC_CONTROL_SELECTOR]
    assert len(actions) == 1
    assert actions[0]["outcome"] == "measurement_unknown"
    assert actions[0]["reason"] == (
        "generic consent control enumeration UNKNOWN: RuntimeError")
