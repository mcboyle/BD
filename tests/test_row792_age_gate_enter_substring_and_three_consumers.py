"""Row 792: the ENTER vocabulary is pinned against SUBSTRING widening, and the
three gate-blocked consumers row 721 added are each executed by a named test.

Row 721 shipped correct refusals and its verdict said so
(bd-persist/verdicts/row721-age-gate-enter-VERDICT-shape.md, VERDICT: BOARD).
What it left behind are two DIAGNOSTIC residuals, which are this row:

  R1  ``ENTER_AFFORDANCE`` is pinned against TOTAL widening but not against
      SUBSTRING widening.  Rewriting it as ``^.*enter.*$`` left the row 721
      file green, because no label in that fixture corpus contains "enter"
      except ENTER itself.  A wall carrying "ENTER YOUR CARD DETAILS" would
      then be both RECOGNISED as an age gate and CLICKED -- the product would
      press a payment control on the operator's behalf.

  R2  Of the five consumers of ``first_blocked_after_gate``, three carry no
      test and no mutant: ``login_impl/manual.py`` (autofill withheld),
      ``login_impl/submit.py`` at the FILL gate and at the POST-SUBMIT gate.
      Deleting any of the three left row 721 green.

Every test below drives the REAL consumer route rather than the helper in
isolation, so a refusal nothing consumes fails here.
"""

from __future__ import annotations

import pytest

from bulk_downloader import interstitial

from tests.test_row721_age_gate_enter_affordance import (
    ABUSE_TEXT,
    GATE_TEXT,
    _Control,
    _GatePage,
    _login_config,
    _patch_login_runtime,
)


BD_GATE_SCOPE = "module"


# The hazard label.  It contains "enter" as a SUBSTRING and is a purchase
# control, not a gate control.  Zero-entropy fixture prose, not a real
# product, price or merchant.
CARD_LABEL = "ENTER YOUR CARD DETAILS"


def _card_wall():
    """An age wall whose only enter-ish control is a payment control.

    Everything else about it is a genuine age gate: 18-plus body text and an
    EXIT affordance beside it.  The ONLY thing standing between the product
    and a click on the card control is that ``ENTER_AFFORDANCE`` requires a
    whole-label match.
    """
    return _GatePage(
        "https://gamma.example/login-abused",
        GATE_TEXT,
        [_Control("EXIT HERE"), _Control(CARD_LABEL, next_text="Checkout")],
    )


def _run(page):
    return interstitial.dismiss_gates(
        page, "", destination_url=page.url, settle_s=0.0, sleep=lambda _s: None
    )


# --------------------------------------------------------------------------
# R1 -- the substring-widening mutant
# --------------------------------------------------------------------------
def test_precondition_the_card_wall_is_an_age_wall_but_for_the_enter_label():
    """Prove the fixture built the shape BEFORE any verdict is read off it.

    Each clause is the reason a later assertion means what it says: if the
    body text carried no 18-plus language, or no EXIT sat beside the card
    control, the wall would be refused for a reason that has nothing to do
    with the ENTER vocabulary and the mutant would be caught by accident.
    """
    page = _card_wall()
    labels = [control.label for control in page.controls]
    assert labels == ["EXIT HERE", CARD_LABEL]
    assert len(labels) == 2
    # 18-plus language IS present -- the content half of the recognition.
    assert interstitial.AGE_GATE_LANGUAGE.search(page.text) is not None
    # An EXIT affordance IS present -- the second half.
    assert interstitial._deny_term("EXIT HERE") is not None
    # The card control is NOT itself denylisted, so nothing refuses it early.
    assert interstitial._deny_term(CARD_LABEL) is None
    # And "enter" really is in it, as a substring -- this is what a widened
    # vocabulary would admit.
    assert "enter" in CARD_LABEL.casefold()


def test_a_card_control_whose_label_merely_contains_enter_is_never_clicked():
    """THE MUTANT CATCHER for R1.

    Widen ``ENTER_AFFORDANCE`` to ``^.*enter.*$`` and this fails twice over:
    the wall becomes a recognised age gate and the card control is pressed.
    """
    assert interstitial.ENTER_AFFORDANCE.fullmatch(CARD_LABEL) is None, (
        "the ENTER vocabulary admits a label that merely CONTAINS 'enter'; "
        "a substring-widened vocabulary would press %r" % CARD_LABEL
    )
    page = _card_wall()
    actions = _run(page)
    assert page.clicked == [], (
        "a control labelled %r was pressed behind an age wall; the product "
        "clicked what is a payment control, not a gate control" % CARD_LABEL
    )
    # EXACT COUNT: the wall is refused at the EXIT term and nothing else is
    # admitted -- not "at least one" refusal, exactly one cleared-gate zero.
    cleared = [a for a in actions if a.get("outcome") == "cleared"]
    assert len(cleared) == 0, (
        "the card wall was reported as a CLEARED age gate: %r" % (cleared,)
    )


def test_the_same_wall_with_a_whole_word_enter_IS_cleared():
    """POSITIVE CONTROL for the test above: the probe can say YES.

    Identical fixture, identical route, one label changed from
    "ENTER YOUR CARD DETAILS" to "ENTER".  If this did not clear, the zero
    above would be the harness failing to reach the click, not the vocabulary
    refusing the label.
    """
    page = _GatePage(
        "https://gamma.example/login-abused",
        GATE_TEXT,
        [_Control("EXIT HERE"), _Control("ENTER", next_text="Members area")],
    )
    actions = _run(page)
    assert page.clicked == ["ENTER"]
    cleared = [a for a in actions if a.get("outcome") == "cleared"]
    assert len(cleared) == 1


# --------------------------------------------------------------------------
# R2 consumer 1 -- login_impl/manual.py: autofill withheld
# --------------------------------------------------------------------------
class _ManualContext:
    def __init__(self, page):
        self._page = page
        self.pages = []

    def add_init_script(self, *_a, **_k):
        return None

    def new_page(self):
        return self._page


class _ManualBrowser:
    def __init__(self, page):
        self._page = page
        self.close_count = 0

    def new_context(self, **_kwargs):
        return _ManualContext(self._page)

    def close(self):
        self.close_count += 1


def _blocked_wall():
    """An age wall whose landing, once ENTER is pressed, is a block page."""
    return _GatePage(
        "https://gamma.example/login-abused",
        GATE_TEXT,
        [_Control("EXIT HERE"), _Control("ENTER", next_text=ABUSE_TEXT)],
    )


def test_manual_login_withholds_the_credentials_when_the_landing_is_a_block(
        monkeypatch):
    """NAMED CATCHER for login_impl/manual.py's ``_gate_blocked`` arm.

    Delete that arm and ``_launch`` runs on into the autofill block, which
    resolves the stored password for a page that is a block page -- so the
    assertion that the vault was never consulted is what fails, and it fails
    for the intended reason.
    """
    from bulk_downloader import cloak as _cloak
    from bulk_downloader import secrets_store
    from bulk_downloader.login_impl import manual

    page = _blocked_wall()
    browser = _ManualBrowser(page)
    resolved = []
    monkeypatch.setattr(
        _cloak, "launch_browser",
        lambda **_kwargs: (browser, "row792-fake-pw", "row792-fake"))
    monkeypatch.setattr(_cloak, "log_choice", lambda *_a, **_k: None)
    monkeypatch.setattr(
        secrets_store, "resolve_password_state",
        lambda value: (resolved.append(value), (value, "ok"))[1])

    # PRECONDITION: nothing pressed, nothing resolved.
    assert page.clicked == [] and resolved == []

    # ManualLoginSession.__init__ starts a worker thread and blocks on it; we
    # want the product's own ``_launch`` with no thread, so the instance is
    # built directly and given exactly the four attributes _launch reads.
    session = manual.ManualLoginSession.__new__(manual.ManualLoginSession)
    session._config = _login_config("https://gamma.example/login-abused")
    session._banner_js = "/*banner*/"
    session._headless = False
    session._manual_profile_dir = None

    browser_out, ctx, page_out, used_pw = session._launch()

    assert browser_out is browser
    assert page_out is page
    assert page.clicked == ["ENTER"], "the gate must have been pressed"
    assert resolved == [], (
        "manual_login resolved the stored password for a page behind the age "
        "wall whose landing is a block page; the credentials were offered to "
        "a page that is not the members page they are for"
    )


# --------------------------------------------------------------------------
# R2 consumers 2 and 3 -- login_impl/submit.py FILL gate and POST gate
# --------------------------------------------------------------------------
class _LateWallPage(_GatePage):
    """A login page that becomes an age wall only when ``arm()`` is called.

    This is the shape the product's own comment describes: a form that
    auto-submits on fill can land on the wall, and the post-submit wall is
    reached only after the submit.  Starting armed would block at the
    PRE-FORM gate -- the one consumer row 721 already covers -- and the two
    uncovered arms would never be reached.
    """

    def __init__(self, url):
        super().__init__(url, "Sign in to continue", [])
        self.armed = 0

    def arm(self):
        self.armed += 1
        self.text = GATE_TEXT
        self.controls = [_Control("EXIT HERE"),
                         _Control("ENTER", next_text=ABUSE_TEXT)]


def _drive_do_login(monkeypatch, arm_on):
    """Run the real ``do_login`` against a wall armed at ``arm_on``."""
    from bulk_downloader.login_impl import submit

    target = "https://gamma.example/login"
    page = _LateWallPage(target)
    submit, browser, reached = _patch_login_runtime(monkeypatch, page)

    def _fill(_page, _candidates, _value, what, *_a, **_k):
        reached.append("fill")
        if arm_on == "fill" and what.startswith("password"):
            page.arm()
        return True, "f"

    def _submit(*_a, **_k):
        reached.append("submit")
        if arm_on == "submit":
            page.arm()
        return True, "s"

    monkeypatch.setattr(submit, "_try_fill", _fill)
    monkeypatch.setattr(submit, "_submit_login", _submit)

    config = _login_config(target)
    config["dismiss_selectors_login"] = ""
    ok, reason, cookies = submit.do_login(config, allow_manual_takeover=False)
    return page, browser, reached, ok, reason, cookies


@pytest.mark.parametrize("arm_on,stops_before", [
    ("fill", "submit"),
    ("submit", None),
], ids=["fill_gate", "post_submit_gate"])
def test_do_login_refuses_a_block_revealed_by_a_late_wall(
        monkeypatch, arm_on, stops_before):
    """NAMED CATCHER for submit.py's ``_fill_blocked`` and ``_post_blocked``.

    Delete either arm and ``do_login`` runs past a block page: it returns
    True with the wall's cookies, or falls through to the success-URL
    comparison, and the ``ok is False`` assertion fails for the intended
    reason -- a block page was handed on as members content.
    """
    page, browser, reached, ok, reason, cookies = _drive_do_login(
        monkeypatch, arm_on)

    # PRECONDITION: the wall really was armed, and really was pressed.
    assert page.armed == 1, "the fixture never built the wall"
    assert page.clicked == ["ENTER"], "the gate must have been pressed"

    assert ok is False, (
        "do_login accepted a block landing revealed by the %s wall" % arm_on
    )
    assert "blocked" in reason
    assert "not members content" in reason
    assert cookies == []
    assert browser.close_count == 1
    # EXACT COUNT of how far execution got: the fill gate must stop the run
    # BEFORE the submit, which is what distinguishes the two consumers.
    if stops_before is not None:
        assert stops_before not in reached, (
            "the %s gate did not stop the run: reached %r" % (arm_on, reached)
        )
    else:
        assert "submit" in reached
