"""Row 912: standard web dialog / notice acknowledgment handling.

bulk_downloader/interstitial.py already dismisses declared and generic
consent/age/interstitial gates (rows 371, 721, 722, 1016) and verifies page
ORIGIN after every click. What it never checked is whether the control that
was clicked actually LEFT the viewport -- a click that raises nothing and
leaves the origin alone is not proof the overlay is gone (an animation delay,
or a backdrop the control itself does not own, can leave the page blocked
while every existing check reports success). This is acceptance criterion
#2, "viewport clearance verification" -- the gap this row closes.

The check is attached as an ADDITIVE ``viewport_cleared`` field on every
``cleared`` action (True/False/None -- None means unmeasurable, per this
module's own UNKNOWN-is-never-permission convention for callers that choose
to act on it) rather than folded into ``outcome``. Every existing caller of
``_click_gate``/``dismiss_gates``/``clear_gates`` already keys off
``outcome``, and several of their fixtures model "the control's element left
the DOM" only via navigation (``next_text``) -- not via an ordinary in-place
removal -- so promoting a fixture-modeling gap into a new refusal ``outcome``
would report those pages as un-cleared when the product behaviour (and the
real page) has not changed. The new signal is additive precisely so it is
provably new coverage, not a change to what already passes.

Acceptance (register row text, verbatim):
  1. Safe dismissal of standard overlay dialogs
  2. Viewport clearance verification
  3. Non-blocking execution on pages without dialogs
"""
from __future__ import annotations

import pytest

from bulk_downloader import interstitial


BD_GATE_SCOPE = "module"


class _Control:
    def __init__(self, label, *, sticky=False):
        self.label = label
        # sticky=True models an overlay whose click succeeds (no exception,
        # no navigation) but whose own DOM node stays put -- the case
        # acceptance #2 exists to catch.
        self.sticky = sticky
        self.gone = False


class _ControlLocator:
    def __init__(self, page, control):
        self._page = page
        self._control = control

    @property
    def first(self):
        return self

    def is_visible(self):
        return not self._control.gone

    def count(self):
        # a dismissed control is REMOVED from the DOM (0 matches); the
        # probe must treat that as clearance without waiting on evaluate()
        return 0 if self._control.gone else 1

    def evaluate(self, _script, timeout=None):
        # the fixture's answer to _VIEWPORT_BLOCKED_JS for a control that is
        # still attached: a sticky overlay still covers the viewport
        assert not self._control.gone, "evaluate() on a removed control would wait 30s in Playwright"
        return True

    def wait_for(self, **_kwargs):
        if self._control.gone:
            raise TimeoutError("not attached")

    def inner_text(self, **_kwargs):
        return self._control.label

    def get_attribute(self, name, **_kwargs):
        return None

    def click(self, **_kwargs):
        self._page.clicked.append(self._control.label)
        if not self._control.sticky:
            self._control.gone = True


class _ControlList:
    def __init__(self, page):
        self._page = page

    def count(self):
        return len(self._page.controls)

    def nth(self, index):
        return _ControlLocator(self._page, self._page.controls[index])


class _Page:
    """A page with zero or more generic-tier dialog controls, plus zero or
    more site-DECLARED selectors (per-site config, not phrase-matched)."""

    def __init__(self, controls, *, site_controls=None):
        self.url = "https://example.test/scenes/1"
        self.controls = list(controls)
        self.site_controls = dict(site_controls or {})
        self.clicked: list[str] = []
        self.gotos: list[str] = []

    def locator(self, selector):
        if selector == interstitial.GENERIC_CONTROL_SELECTOR:
            return _ControlList(self)
        if selector in self.site_controls:
            return _ControlLocator(self, self.site_controls[selector])
        return _ControlLocator(self, _Control(""))

    def inner_text(self, selector, **_kwargs):
        assert selector == "body"
        return ""

    def wait_for_load_state(self, **_kwargs):
        return None

    def goto(self, url, **_kwargs):
        self.gotos.append(url)
        self.url = url

    def go_back(self, **_kwargs):
        return None


def _run(page, raw=""):
    return interstitial.dismiss_gates(
        page, raw, destination_url=page.url, settle_s=0.0, sleep=lambda _s: None
    )


# ── 1. safe dismissal of standard overlay dialogs ───────────────────────


def test_accept_all_cookie_banner_is_dismissed():
    page = _Page([_Control("Accept All")])
    actions = _run(page)

    assert page.clicked == ["Accept All"]
    assert [a["outcome"] for a in actions] == ["cleared"]


def test_continue_to_the_site_interstitial_is_dismissed():
    page = _Page([_Control("Continue to the site")])
    actions = _run(page)

    assert page.clicked == ["Continue to the site"]
    assert [a["outcome"] for a in actions] == ["cleared"]


def test_declared_site_selector_dialog_is_dismissed():
    """Standard dialogs are also dismissed through the per-site DECLARED
    selector path (an operator-configured CSS line), not just the always-on
    generic phrase match -- a second, independent route to the same
    ``_click_gate`` verification this row adds to.
    """
    selector = "button.cookie-accept"
    control = _Control("Accept")
    page = _Page([], site_controls={selector: control})
    actions = _run(page, raw=selector)

    assert page.clicked == ["Accept"]
    assert [a["outcome"] for a in actions] == ["cleared"]
    assert actions[0]["viewport_cleared"] is True


def test_denylisted_exit_label_is_never_clicked():
    page = _Page([_Control("Decline")])
    actions = _run(page)

    assert page.clicked == []
    assert [a["outcome"] for a in actions] == ["refused"]


# ── 2. viewport clearance verification (the new signal) ─────────────────


def test_cleared_dialog_reports_viewport_cleared_true():
    """Positive control: a real (non-sticky) dismissal both reports
    ``cleared`` AND ``viewport_cleared: True`` -- proving the new check
    actually measures the control, not a rubber stamp.
    """
    page = _Page([_Control("Accept All")])
    actions = _run(page)

    assert actions[0]["outcome"] == "cleared"
    assert actions[0]["viewport_cleared"] is True


def test_stuck_overlay_is_not_reported_cleared():
    """The click succeeds (no exception, no origin change) but the
    control's own element never leaves the DOM -- e.g. a fade-out
    animation the click handler starts but does not finish. That is NOT a
    clearance: ``outcome`` is ``viewport_blocked`` (callers keying off
    ``cleared`` treat the page as still gated) and ``viewport_cleared`` is
    the measured False.
    """
    page = _Page([_Control("Accept All", sticky=True)])
    actions = _run(page)

    assert page.clicked == ["Accept All"]
    assert actions[0]["outcome"] == "viewport_blocked"
    assert actions[0]["viewport_cleared"] is False
    assert "still visible" in actions[0]["reason"]


def test_viewport_cleared_is_none_when_unmeasurable():
    """A locator with no ``evaluate`` capability at all (some duck-typed
    callers) yields ``viewport_cleared: None`` -- UNKNOWN, not a silent
    True -- rather than raising or being assumed clear; the click's own
    evidence stands, so ``outcome`` stays ``cleared``.
    """
    class _NoVisibilityLocator:
        """No ``is_visible`` attribute at all -- the capability is absent,
        not merely falsy. Deliberately does NOT subclass ``_ControlLocator``,
        which defines the method.
        """

        def __init__(self, page, control):
            self._page = page
            self._control = control

        @property
        def first(self):
            return self

        def inner_text(self, **_kwargs):
            return self._control.label

        def get_attribute(self, name, **_kwargs):
            return None

        def wait_for(self, **_kwargs):
            return None

        def click(self, **_kwargs):
            self._page.clicked.append(self._control.label)
            self._control.gone = True

    class _NoVisibilityList(_ControlList):
        def nth(self, index):
            return _NoVisibilityLocator(self._page, self._page.controls[index])

    class _NoVisibilityPage(_Page):
        def locator(self, selector):
            if selector == interstitial.GENERIC_CONTROL_SELECTOR:
                return _NoVisibilityList(self)
            return _ControlLocator(self, _Control(""))

    page = _NoVisibilityPage([_Control("Accept All")])
    actions = _run(page)

    assert page.clicked == ["Accept All"]
    assert actions[0]["outcome"] == "cleared"
    assert actions[0]["viewport_cleared"] is None


# ── 3. non-blocking execution on pages without dialogs ───────────────────


def test_no_dialogs_on_page_returns_immediately_with_no_actions():
    page = _Page([])
    actions = _run(page)

    assert actions == []
    assert page.clicked == []
    assert page.gotos == []


# ── FIXER round 2 (E1 removed-control stall, E2 bottom-bar, E3 consumer) ──


def test_viewport_blocked_is_a_blocked_outcome_for_the_consumer():
    """E3: runner._page_gates_are_safe keys off BLOCKED_AFTER_GATE_OUTCOMES;
    a measured stuck overlay must be in it (fail closed at the consumer)."""
    assert "viewport_blocked" in interstitial.BLOCKED_AFTER_GATE_OUTCOMES


def _chromium():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright unavailable -- real-browser clearance probe UNKNOWN")
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True)
    except Exception as exc:
        pw.stop()
        pytest.skip(f"chromium unavailable -- real-browser clearance probe UNKNOWN: {exc}")
    return pw, browser


@pytest.mark.parametrize("html,expected,label", [
    # (a) the common path: the overlay is REMOVED on click -> cleared, and fast
    ("""<div id="ov" style="position:fixed;inset:0;background:#000a">
          <button onclick="document.getElementById('ov').remove()">Accept All</button></div>""",
     ("cleared", True), "removed"),
    # (b) E2: a fixed BOTTOM BAR that stays after the click -> blocked
    ("""<div id="bar" style="position:fixed;left:0;right:0;bottom:0;height:120px;background:#eee">
          <button onclick="void 0">Accept All</button></div>""",
     ("viewport_blocked", False), "bottom-bar"),
    # (c) a centre modal that stays -> blocked
    ("""<div style="position:fixed;inset:0;background:#000a"><div style="position:absolute;top:40%;left:40%">
          <button onclick="void 0">Accept All</button></div></div>""",
     ("viewport_blocked", False), "modal"),
    # (d) an inline control that merely stays in the page is not an overlay -> cleared
    ("""<p>hello</p><a href="#" onclick="event.preventDefault()">Accept All</a>""",
     ("cleared", True), "inline"),
])
def test_real_chromium_clearance_probe(html, expected, label):
    import time as _time
    pw, browser = _chromium()
    try:
        page = browser.new_page()
        page.set_content(html)
        started = _time.perf_counter()
        actions = interstitial.dismiss_gates(page, None, timeout_ms=3000, settle_s=0)
        elapsed = _time.perf_counter() - started
        assert len(actions) == 1, (label, actions)
        assert (actions[0]["outcome"], actions[0]["viewport_cleared"]) == expected, (label, actions[0], elapsed)
        assert elapsed < 8.0, (label, elapsed)  # E1: never Playwright's 30s wait on a removed control
    finally:
        browser.close()
        pw.stop()
