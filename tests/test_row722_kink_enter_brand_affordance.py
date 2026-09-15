"""Row 722 live (kink.com, 2026-09-15 04:13Z): the age wall carries
"I Disagree, Exit Here" and "ENTER KINK".  The walker refused the EXIT
control (correct) and then admitted nothing, because ``ENTER_AFFORDANCE``
knew only the bare word and the site/here forms.  The gate stayed up, the
username field was never reachable and the login fell to manual takeover.

The fix admits ENTER followed by ONE brand-shaped token, still only on a page
whose content corroborates an age gate (18-plus language AND an EXIT beside
it).  The row 792 hazard is preserved by construction: "ENTER YOUR CARD
DETAILS" is three tokens, and instruction words ("your", "password", ...)
are refused even as a single token.
"""
from __future__ import annotations

import pytest

from bulk_downloader import interstitial
from tests.test_row721_age_gate_enter_affordance import (
    GATE_TEXT,
    _Control,
    _GatePage,
)
from tests.test_row792_age_gate_enter_substring_and_three_consumers import (
    CARD_LABEL,
)

BD_GATE_SCOPE = "module"

KINK_EXIT = "I Disagree, Exit Here"
KINK_ENTER = "ENTER KINK"


def _kink_wall():
    return _GatePage(
        "https://www.kink.com/login",
        GATE_TEXT,
        [_Control(KINK_EXIT), _Control(KINK_ENTER, next_text="Members area")],
    )


def _run(page):
    return interstitial.dismiss_gates(
        page, "", destination_url=page.url, settle_s=0.0, sleep=lambda _s: None
    )


def test_precondition_the_kink_wall_is_an_age_wall_but_for_the_brand_label():
    page = _kink_wall()
    assert [c.label for c in page.controls] == [KINK_EXIT, KINK_ENTER]
    assert interstitial.AGE_GATE_LANGUAGE.search(page.text) is not None
    assert interstitial._deny_term(KINK_EXIT) is not None
    assert interstitial._deny_term(KINK_ENTER) is None


def test_enter_brand_clears_the_kink_age_wall():
    """THE ROW: ENTER KINK is pressed, exactly once, and the wall is cleared."""
    assert interstitial.ENTER_AFFORDANCE.fullmatch(KINK_ENTER) is not None, (
        "the ENTER vocabulary refuses %r; the kink.com age wall cannot be "
        "cleared and the login form behind it is never reached" % KINK_ENTER
    )
    page = _kink_wall()
    actions = _run(page)
    assert page.clicked == [KINK_ENTER]
    cleared = [a for a in actions if a.get("outcome") == "cleared"]
    assert len(cleared) == 1
    assert cleared[0]["label"] == KINK_ENTER
    refused = [a for a in actions if a.get("outcome") == "refused"]
    assert [a["label"] for a in refused] == [KINK_EXIT]


@pytest.mark.parametrize("label", [
    CARD_LABEL,             # three tokens: the row 792 payment control
    "ENTER YOUR PASSWORD",
    "ENTER PASSWORD",       # one token, but an instruction word
    "ENTER PIN",
    "ENTER CODE",
    "ENTER EMAIL",
    "ENTER THE",
    "ENTER 4-DIGIT CODE",
])
def test_negative_control_instruction_labels_stay_refused(label):
    """The widening is ONE brand token, never an input instruction."""
    assert interstitial.ENTER_AFFORDANCE.fullmatch(label) is None, label
    page = _GatePage(
        "https://gamma.example/login-abused",
        GATE_TEXT,
        [_Control("EXIT HERE"), _Control(label, next_text="Card form")],
    )
    actions = _run(page)
    assert page.clicked == []
    assert [a for a in actions if a.get("outcome") == "cleared"] == []


def test_enter_brand_without_age_language_is_not_pressed():
    """Content still gates the admission: no 18-plus text, no click."""
    page = _GatePage(
        "https://www.kink.com/login",
        "Welcome back. Sign in to continue.",
        [_Control(KINK_EXIT), _Control(KINK_ENTER, next_text="Members area")],
    )
    actions = _run(page)
    assert page.clicked == []
    assert [a for a in actions if a.get("outcome") == "cleared"] == []
