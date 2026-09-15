"""Row 762: a gate click must never accept a prechecked recurring-charge consent.

The measured page is the Aylo ``/store`` post-login upsell: a prechecked
``<input type="checkbox" checked>`` beside "you agree to add <upsell> ...
charged $29.97 every 30 days until you cancel", under a CONTINUE TO MEMBERS
AREA control.  ``dismiss_gates`` already clears "Continue to Members Area"
through BOTH its paths -- the generic ``interstitial`` tier pattern and the
measured per-site selector line -- so on that page the dismisser's click IS
the purchase.  T3 real-money safety: the deliverable is the gate that refuses.

Recognition is CONTENT-based, like row 721: recurring-charge language in the
body text together with a checked checkbox.  A page with no charge language
is the ordinary upsell wall and may proceed; a page whose charge language is
present but whose checkbox state cannot be measured is UNKNOWN and fails
closed (A2: unavailable evidence is never permission to spend money).
"""

from __future__ import annotations

from bulk_downloader import interstitial


# Real-money safety boundary: it must run on every PR regardless of what the
# diff touched, so it is declared repo-wide and pinned into a CI shard beside
# the row-771 interstitial gate rather than left to the diff-derived band.
BD_GATE_SCOPE = "repo-wide"


CONTINUE_LABEL = "CONTINUE TO MEMBERS AREA"
CONTINUE_SELECTOR = "a:has-text('Continue to Members Area')"
# A declared line whose label no generic tier recognises: only the measured
# per-site selector, re-offered after the generic pass, can reach it.
PROCEED_LABEL = "PROCEED"
PROCEED_SELECTOR = "a.store-continue"
CHARGE_TEXT = (
    "By checking this box you agree to add Premium Plus to your membership.\n"
    "You will be charged $29.97 every 30 days until you cancel."
)
COOKIE_TEXT = "We use cookies to improve your experience."
NO_CHARGE_TEXT = (
    "Welcome back. Explore the new releases this week.\n"
    "Your membership is active."
)
CHECKED_SELECTOR = interstitial.CHECKED_CONSENT_SELECTOR if hasattr(
    interstitial, "CHECKED_CONSENT_SELECTOR") else "input[type='checkbox']:checked"


class _Control:
    def __init__(self, label, *, next_text="Members area", next_controls=()):
        self.label = label
        self.next_text = next_text
        self.next_controls = list(next_controls)


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

    def wait_for(self, **_kwargs):
        # The bounded SPA wait for a declared control that is not yet there.
        if not self.is_visible():
            raise TimeoutError("not attached")

    def click(self, **_kwargs):
        self._page.clicked.append(self._control.label)
        self._page.text = self._control.next_text
        self._page.controls = self._control.next_controls


class _ControlList:
    def __init__(self, page):
        self._page = page

    def count(self):
        return len(self._page.controls)

    def nth(self, index):
        return _ControlLocator(self._page, self._page.controls[index])


class _CheckedBoxes:
    """The ``input[type='checkbox']:checked`` locator: only ``count`` matters."""

    def __init__(self, page):
        self._page = page

    def count(self):
        self._page.checkbox_reads += 1
        if self._page.checkbox_error is not None:
            raise self._page.checkbox_error
        return self._page.checked_boxes


class _UpsellPage:
    """A duck-typed page: body text, controls, and its checked checkboxes."""

    def __init__(self, text, *, checked_boxes, checkbox_error=None,
                 checkbox_measurable=True):
        self.url = "https://members.example/store"
        self.text = text
        self.controls = [_Control(CONTINUE_LABEL)]
        self.checked_boxes = checked_boxes
        self.checkbox_error = checkbox_error
        self.checkbox_measurable = checkbox_measurable
        self.checkbox_reads = 0
        self.clicked = []

    def locator(self, selector):
        if selector == interstitial.GENERIC_CONTROL_SELECTOR:
            return _ControlList(self)
        if selector == CHECKED_SELECTOR:
            if not self.checkbox_measurable:
                # A locator with no ``count`` API: the state is unmeasurable.
                return object()
            return _CheckedBoxes(self)
        for declared, label in ((CONTINUE_SELECTOR, CONTINUE_LABEL),
                                (PROCEED_SELECTOR, PROCEED_LABEL)):
            if selector in (declared, ":is(%s):visible" % declared):
                return _ControlLocator(self, next(
                    (c for c in self.controls if c.label == label),
                    _Control("")))
        return _ControlLocator(self, _Control(""))

    def inner_text(self, selector, **_kwargs):
        assert selector == "body"
        return self.text

    def wait_for_load_state(self, **_kwargs):
        return None

    def goto(self, url, **_kwargs):
        self.url = url

    def go_back(self, **_kwargs):
        return None


def _run(page, raw=""):
    return interstitial.dismiss_gates(
        page, raw, destination_url=page.url, settle_s=0.0,
        sleep=lambda _s: None)


def _outcomes(actions, outcome):
    return [a for a in actions if a.get("outcome") == outcome]


def test_precondition_fixture_is_the_measured_upsell_shape():
    """The RED corpus must contain the hazard: checked box, charge, CONTINUE."""
    page = _UpsellPage(CHARGE_TEXT, checked_boxes=1)
    assert page.locator(CHECKED_SELECTOR).count() == 1
    assert "$29.97 every 30 days" in page.text
    assert [c.label for c in page.controls] == [CONTINUE_LABEL]
    # The shipped interstitial tier accepts the label on its own: that is the
    # gap, and it is why a content guard and not a pattern edit is the fix.
    assert any(tier == "interstitial" and pattern.fullmatch(CONTINUE_LABEL)
               for tier, pattern in interstitial._GENERIC_TIERS)


def test_generic_tier_refuses_prechecked_billing_consent():
    page = _UpsellPage(CHARGE_TEXT, checked_boxes=1)
    actions = _run(page)
    assert page.clicked == [], (
        "dismiss_gates clicked %r on a page with a prechecked recurring "
        "charge consent" % (page.clicked,))
    refused = _outcomes(actions, "refused")
    assert len(refused) == 1, "expected exactly one refusal, got %r" % (
        [a.get("outcome") for a in actions],)
    assert refused[0]["tier"] == "safety"
    assert "$29.97 every 30 days" in refused[0]["reason"]
    assert "prechecked" in refused[0]["reason"]
    assert _outcomes(actions, "cleared") == []


def test_declared_site_selector_refuses_the_same_page():
    """The measured per-site line resolves the same control; same refusal."""
    page = _UpsellPage(CHARGE_TEXT, checked_boxes=1)
    actions = _run(page, CONTINUE_SELECTOR)
    assert page.clicked == [], (
        "the declared selector clicked %r past a prechecked recurring charge"
        % (page.clicked,))
    refused = _outcomes(actions, "refused")
    assert len(refused) == 1, [a.get("outcome") for a in actions]
    assert refused[0]["source"] == "site"
    assert refused[0]["tier"] == "safety"
    assert "$29.97 every 30 days" in refused[0]["reason"]
    assert _outcomes(actions, "cleared") == []


def test_upsell_revealed_behind_a_cookie_wall_is_refused_on_re_offer():
    """The kink shape: a consent banner hides the measured control, so the
    declared selector is re-offered once the generic pass has changed the
    page.  The verdict taken before the banner click (no charge language)
    must not carry over to the upsell it revealed."""
    page = _UpsellPage(COOKIE_TEXT, checked_boxes=1)
    page.controls = [_Control("ACCEPT ALL COOKIES", next_text=CHARGE_TEXT,
                              next_controls=[_Control(CONTINUE_LABEL)])]
    actions = _run(page, CONTINUE_SELECTOR)
    assert page.clicked == ["ACCEPT ALL COOKIES"], page.clicked
    assert len(_outcomes(actions, "cleared")) == 1
    refused = _outcomes(actions, "refused")
    assert len(refused) == 1, [a.get("outcome") for a in actions]
    # The generic pass re-snapshots after a clearance and meets the revealed
    # control first, under the interstitial tier.
    assert refused[0]["source"] == "generic"
    assert refused[0]["label"] == CONTINUE_LABEL
    assert "$29.97 every 30 days" in refused[0]["reason"]
    # The box was read once for the revealed control, never for the banner.
    assert page.checkbox_reads == 1


def test_re_offered_site_selector_is_refused_on_the_revealed_upsell():
    """Same wall, but the revealed control is one only the declared per-site
    line resolves: the re-offer of the declared selectors after the generic
    pass is the click that must be refused."""
    page = _UpsellPage(COOKIE_TEXT, checked_boxes=1)
    page.controls = [_Control("ACCEPT ALL COOKIES", next_text=CHARGE_TEXT,
                              next_controls=[_Control(PROCEED_LABEL)])]
    actions = _run(page, PROCEED_SELECTOR)
    assert page.clicked == ["ACCEPT ALL COOKIES"], page.clicked
    assert len(_outcomes(actions, "cleared")) == 1
    refused = _outcomes(actions, "refused")
    assert len(refused) == 1, [a.get("outcome") for a in actions]
    assert refused[0]["source"] == "site"
    assert refused[0]["selector"] == PROCEED_SELECTOR
    assert refused[0]["label"] == PROCEED_LABEL
    assert "$29.97 every 30 days" in refused[0]["reason"]
    assert page.checkbox_reads == 1


def test_negative_control_no_charge_text_still_proceeds():
    """The ordinary members-area wall: same control, same checked box, no
    charge language.  It must clear exactly as before -- the guard is about
    money, not about checkboxes."""
    page = _UpsellPage(NO_CHARGE_TEXT, checked_boxes=1)
    assert interstitial.RECURRING_CHARGE_LANGUAGE.search(page.text) is None
    actions = _run(page)
    assert page.clicked == [CONTINUE_LABEL], (
        "a page with no charge language was not cleared; clicked=%r actions=%r"
        % (page.clicked, [a.get("outcome") for a in actions]))
    assert len(_outcomes(actions, "cleared")) == 1
    assert _outcomes(actions, "refused") == []
    # The box is only read when charge language makes it matter.
    assert page.checkbox_reads == 0


def test_charge_text_with_no_checked_box_proceeds():
    """Consent not prechecked: clicking through does not add the upsell."""
    page = _UpsellPage(CHARGE_TEXT, checked_boxes=0)
    actions = _run(page)
    assert page.clicked == [CONTINUE_LABEL]
    assert len(_outcomes(actions, "cleared")) == 1
    assert page.checkbox_reads == 1


def test_charge_text_with_unmeasurable_checkbox_fails_closed():
    """Charge language present and the box state UNKNOWN: refuse, report."""
    unreadable = _UpsellPage(CHARGE_TEXT, checked_boxes=1,
                             checkbox_error=RuntimeError("detached"))
    unmeasurable = _UpsellPage(CHARGE_TEXT, checked_boxes=1,
                               checkbox_measurable=False)
    for page in (unreadable, unmeasurable):
        actions = _run(page)
        assert page.clicked == [], page.clicked
        unknown = interstitial.first_safety_unknown(actions)
        assert unknown is not None, [a.get("outcome") for a in actions]
        assert unknown["outcome"] == "measurement_unknown"
        assert "consent" in unknown["reason"]
        assert "UNKNOWN" in interstitial.safety_unknown_diagnostic(unknown)


def test_recurring_charge_language_is_anchored_to_money_and_period():
    """A price alone, or a period alone, is not a recurring charge."""
    language = interstitial.RECURRING_CHARGE_LANGUAGE
    assert language.search("charged $29.97 every 30 days until you cancel")
    assert language.search("Billed at €9.99/month, rebills automatically")
    assert language.search("will be charged 24.95 USD per month")
    assert language.search("until you cancel")
    assert language.search("Save $29.97 today with this bundle") is None
    assert language.search("Videos are added every 30 days") is None
    assert language.search("29.97 fps at 1080p") is None
