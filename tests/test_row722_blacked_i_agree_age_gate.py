"""Row 722 live (blacked.com, test2, 2026-09-15 05:58Z & 06:01Z): the age
wall carries bare "I AGREE" beside "I DISAGREE". The walker refused the
DISAGREE control (correct) and then admitted nothing -- ``_GENERIC_TIERS``'s
unconditional "age" tier knows only "i agree, enter|continue", "i am 18",
"yes, i am 18", so bare "I AGREE" matched no tier -- and the login fell to
manual takeover with the gate still up.

The fix admits bare "I AGREE" / "AGREE" / "YES, I AGREE" ONLY on a page whose
content corroborates an age gate (18-plus language AND a denylisted sibling
control beside it), the same corroboration ENTER_AFFORDANCE uses (row 721/
722 kink.com). It is never promoted into the unconditional tier: a bare
"I agree" on a ToS/checkout page must not be pressed.
"""
from __future__ import annotations

import pytest

from bulk_downloader import interstitial
from tests.test_row721_age_gate_enter_affordance import (
    GATE_TEXT,
    _Control,
    _GatePage,
)

BD_GATE_SCOPE = "module"

BLACKED_DISAGREE = "I DISAGREE"
BLACKED_AGREE = "I AGREE"


def _blacked_wall():
    return _GatePage(
        "https://www.blacked.com/",
        GATE_TEXT,
        [_Control(BLACKED_DISAGREE),
         _Control(BLACKED_AGREE, next_text="Members area")],
    )


def _run(page):
    return interstitial.dismiss_gates(
        page, "", destination_url=page.url, settle_s=0.0, sleep=lambda _s: None
    )


def test_precondition_the_blacked_wall_is_an_age_wall_but_bare_agree():
    page = _blacked_wall()
    assert [c.label for c in page.controls] == [BLACKED_DISAGREE, BLACKED_AGREE]
    assert interstitial.AGE_GATE_LANGUAGE.search(page.text) is not None
    assert interstitial._deny_term(BLACKED_DISAGREE) is not None
    assert interstitial._deny_term(BLACKED_AGREE) is None
    for tier, pattern in interstitial._GENERIC_TIERS:
        assert not pattern.fullmatch(BLACKED_AGREE), (
            "bare %r already matches the unconditional %r tier; the row 722 "
            "admission would be redundant with (and could be masked by) it"
            % (BLACKED_AGREE, tier)
        )


def test_bare_i_agree_clears_the_blacked_age_wall():
    """THE ROW: I AGREE is pressed, exactly once, and the wall is cleared."""
    assert interstitial.AGREE_AFFORDANCE.fullmatch(BLACKED_AGREE) is not None, (
        "the AGREE vocabulary refuses %r; the blacked.com age wall cannot "
        "be cleared and login falls to manual takeover" % BLACKED_AGREE
    )
    page = _blacked_wall()
    actions = _run(page)
    assert page.clicked == [BLACKED_AGREE]
    cleared = [a for a in actions if a.get("outcome") == "cleared"]
    assert len(cleared) == 1
    assert cleared[0]["label"] == BLACKED_AGREE
    refused = [a for a in actions if a.get("outcome") == "refused"]
    assert [a["label"] for a in refused] == [BLACKED_DISAGREE]


@pytest.mark.parametrize("label", ["AGREE", "Yes, I Agree", "yes i agree"])
def test_other_bare_agree_spellings_also_clear(label):
    page = _GatePage(
        "https://www.blacked.com/",
        GATE_TEXT,
        [_Control(BLACKED_DISAGREE), _Control(label, next_text="Members area")],
    )
    actions = _run(page)
    assert page.clicked == [label]
    assert len([a for a in actions if a.get("outcome") == "cleared"]) == 1


def test_negative_control_a_agree_with_no_age_language_is_not_pressed():
    """No 18-plus content: a ToS/checkout "I Agree" is not an age gate."""
    page = _GatePage(
        "https://www.blacked.com/checkout",
        "By continuing you accept the Terms of Service.",
        [_Control(BLACKED_DISAGREE), _Control(BLACKED_AGREE, next_text="Paid")],
    )
    actions = _run(page)
    assert page.clicked == []
    assert [a for a in actions if a.get("outcome") == "cleared"] == []


def test_negative_control_b_agree_with_age_language_but_no_deny_sibling():
    """Age language alone, no EXIT/DISAGREE beside it: follows the ENTER rule
    exactly -- no corroborating denylisted sibling, no admission."""
    page = _GatePage(
        "https://www.blacked.com/",
        GATE_TEXT,
        [_Control(BLACKED_AGREE, next_text="Members area")],
    )
    actions = _run(page)
    assert page.clicked == []
    assert [a for a in actions if a.get("outcome") == "cleared"] == []


def test_negative_control_c_i_agree_to_the_terms_is_never_pressed():
    """The widening is bare AGREE forms only, never a ToS-shaped sentence."""
    assert interstitial.AGREE_AFFORDANCE.fullmatch("I AGREE TO THE TERMS") is None
    page = _GatePage(
        "https://www.blacked.com/",
        GATE_TEXT,
        [_Control(BLACKED_DISAGREE),
         _Control("I AGREE TO THE TERMS", next_text="Members area")],
    )
    actions = _run(page)
    assert page.clicked == []
    assert [a for a in actions if a.get("outcome") == "cleared"] == []
