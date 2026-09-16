"""Row 722 live (nookies.com, 2026-09-15 08:25-09:08Z): after a successful
login the site lands on https://nookies.com/membersarea/gateway, an "OUR
EXCLUSIVE PARTNERS & PREMIUM DEALS" upsell page whose only forward control is
a same-origin ``<a href="https://nookies.com/membersarea">ACCESS NOOKIES</a>``
beside foreign partner links ("JOIN NOW", "SAVE BIG NOW") that must never be
pressed.

The walker's interstitial tier admitted "CONTINUE TO MEMBERS AREA"-shaped
labels (``_GENERIC_TIERS`` "interstitial") but not "ACCESS <brand>", so the
run stayed on the gateway and /video/ URLs rendered as tour pages.

The fix admits "ACCESS <one brand token>" / "ENTER MEMBERS AREA" / "GO TO
MEMBERS AREA" / "ACCESS MEMBERS AREA" at the interstitial tier, corroborated
by the control's OWN destination -- same-origin href, or no href at all --
never page content.  Instruction words after ACCESS ("your", "code",
"denied", "restricted") are refused, and two or more tokens (row 792 style)
never are.

The fixtures here are the row 721 ``_GatePage`` / ``_Control`` shapes,
extended locally with an ``href`` the row 721 ``_ControlLocator`` cannot
carry -- kept in this module only, per SUBAGENT-LAW: shared test modules are
not touched.
"""

from __future__ import annotations

from bulk_downloader import interstitial


BD_GATE_SCOPE = "module"


GATEWAY_TEXT = (
    "OUR EXCLUSIVE PARTNERS & PREMIUM DEALS -- upgrade your membership "
    "and unlock even more content from our network."
)

SAVE_BIG = "SAVE BIG NOW"
JOIN_NOW = "JOIN NOW"
ACCESS_NOOKIES = "ACCESS NOOKIES"


class _Control:
    """Row 721's fixture control, extended with an ``href`` this module owns."""

    def __init__(self, label, *, href=None, next_text=None, destination=""):
        self.label = label
        self.href = href
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
        if name == "href":
            return self._control.href
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
    """A duck-typed page whose body text, url and controls are the fixture."""

    def __init__(self, url, text, controls):
        self.url = url
        self.text = text
        self.controls = list(controls)
        self.clicked = []
        self.gotos = []

    def locator(self, selector):
        if selector == interstitial.GENERIC_CONTROL_SELECTOR:
            return _ControlList(self)
        return _ControlLocator(self, _Control(""))

    def inner_text(self, selector, **_kwargs):
        assert selector == "body"
        return self.text

    def wait_for_load_state(self, **_kwargs):
        return None

    def goto(self, url, **_kwargs):
        self.gotos.append(url)
        self.url = url

    def go_back(self, **_kwargs):
        return None


def _gateway_page(access_href="https://nookies.test/membersarea"):
    return _GatePage(
        "https://nookies.test/membersarea/gateway",
        GATEWAY_TEXT,
        [
            _Control(SAVE_BIG, href="https://mylf.test/join"),
            _Control(JOIN_NOW, href="https://teamskeet.test/join"),
            _Control(ACCESS_NOOKIES, href=access_href,
                     next_text="Members area"),
        ],
    )


def _run(page):
    return interstitial.dismiss_gates(
        page, "", destination_url=page.url, settle_s=0.0, sleep=lambda _s: None
    )


def _outcomes(actions, outcome):
    return [a for a in actions if a.get("outcome") == outcome]


def test_precondition_the_gateway_has_one_same_origin_control():
    page = _gateway_page()
    assert [c.label for c in page.controls] == [SAVE_BIG, JOIN_NOW, ACCESS_NOOKIES]
    assert not any(pattern.fullmatch(ACCESS_NOOKIES)
                   for _tier, pattern in interstitial._GENERIC_TIERS), (
        "no shipped generic tier admits ACCESS NOOKIES; that is the gap"
    )
    assert interstitial.ACCESS_AFFORDANCE.fullmatch(ACCESS_NOOKIES) is not None


def test_the_row_access_nookies_is_pressed_and_the_partner_links_are_not():
    """THE ROW: ACCESS NOOKIES is pressed, exactly once."""
    page = _gateway_page()
    actions = _run(page)
    assert page.clicked == [ACCESS_NOOKIES], (
        "the gateway's forward control was not pressed; clicked=%r"
        % (page.clicked,)
    )
    cleared = _outcomes(actions, "cleared")
    assert len(cleared) == 1
    assert cleared[0]["label"] == ACCESS_NOOKIES
    assert cleared[0]["tier"] == "interstitial"
    assert SAVE_BIG not in page.clicked
    assert JOIN_NOW not in page.clicked


def test_negative_control_a_foreign_host_access_is_not_pressed():
    """Same label, foreign subdomain href: the control is never pressed."""
    page = _gateway_page(access_href="https://join.nookies.test/signup")
    actions = _run(page)
    assert page.clicked == [], (
        "a foreign-host ACCESS control was pressed: %r" % (page.clicked,)
    )
    assert _outcomes(actions, "cleared") == []


def test_negative_control_b_instruction_and_denial_labels_stay_refused():
    for label in ("ACCESS YOUR ACCOUNT", "ACCESS CODE", "ACCESS DENIED"):
        assert interstitial.ACCESS_AFFORDANCE.fullmatch(label) is None, label
        page = _GatePage(
            "https://nookies.test/membersarea/gateway",
            GATEWAY_TEXT,
            [_Control(label, href="https://nookies.test/membersarea",
                      next_text="Members area")],
        )
        actions = _run(page)
        assert page.clicked == [], (
            "an instruction-word ACCESS label was pressed: %r (%s)"
            % (page.clicked, label)
        )
        assert _outcomes(actions, "cleared") == []


def test_negative_control_c_partner_links_are_never_pressed_in_any_case():
    for access_href in (
        "https://nookies.test/membersarea",
        "https://join.nookies.test/signup",
    ):
        page = _gateway_page(access_href=access_href)
        _run(page)
        assert SAVE_BIG not in page.clicked
        assert JOIN_NOW not in page.clicked


def test_enter_members_area_and_go_to_members_area_are_admitted_too():
    for label in ("ENTER MEMBERS AREA", "GO TO MEMBERS AREA",
                  "ACCESS MEMBERS AREA"):
        assert interstitial.ACCESS_AFFORDANCE.fullmatch(label) is not None, label
        page = _GatePage(
            "https://nookies.test/membersarea/gateway",
            GATEWAY_TEXT,
            [_Control(label, href="https://nookies.test/membersarea",
                      next_text="Members area")],
        )
        actions = _run(page)
        assert page.clicked == [label]
        assert len(_outcomes(actions, "cleared")) == 1


def test_a_control_with_no_href_at_all_is_same_origin_by_construction():
    """A <button> ACCESS control carries no href; it is admitted, not refused."""
    page = _GatePage(
        "https://nookies.test/membersarea/gateway",
        GATEWAY_TEXT,
        [_Control(ACCESS_NOOKIES, href=None, next_text="Members area")],
    )
    actions = _run(page)
    assert page.clicked == [ACCESS_NOOKIES]
    assert len(_outcomes(actions, "cleared")) == 1
