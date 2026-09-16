"""Row 722s live (hustlerunlimited.com/login/, 2026-09-15 14:33Z): the login
page opens under a modal -- "Are you over the age of 18?" with "Yes, I'm
Over 18" beside "No". The walker admitted nothing (the unconditional "age"
tier knew "i am 18", "yes, i am 18", never OVER / "or over"), the fields were
filled behind the modal, the Turnstile widget stayed covered and the submit
click had to be forced: "no submit method produced navigation". bang.com's
wall reads "Yes I am 18 or over" (same miss; the campaign harness's own
fallback had been clearing it).

Contract: a bare age affirmation with OVER / AT LEAST and an "or over" /
"years old" tail is admitted by the unconditional age tier; a sentence that
merely contains one is not.
"""
from __future__ import annotations

import pytest

from bulk_downloader import interstitial
from tests.test_row721_age_gate_enter_affordance import _Control, _GatePage

BD_GATE_SCOPE = "module"

HUSTLER_TEXT = ("Are you over the age of 18? This site contains mature content that "
                "may be unsuitable for children under 18. All models appearing on "
                "this website are 18 years or older.")
HUSTLER_YES = "Yes, I'm Over 18"
BANG_YES = "Yes I am 18 or over"


def _age_tier():
    return [p for t, p in interstitial._GENERIC_TIERS if t == "age"][0]


def _wall(yes_label, no_label="No"):
    return _GatePage("https://hustlerunlimited.com/login/", HUSTLER_TEXT,
                     [_Control(yes_label, next_text="Login"), _Control(no_label)])


def _run(page):
    return interstitial.dismiss_gates(
        page, "", destination_url=page.url, settle_s=0.0, sleep=lambda _s: None)


def test_the_hustler_modal_is_cleared_by_its_affirmation():
    assert _age_tier().fullmatch(HUSTLER_YES) is not None, (
        "the unconditional age tier refuses %r: the login form stays under "
        "the modal and the submit is forced blind" % HUSTLER_YES)
    page = _wall(HUSTLER_YES)
    actions = _run(page)
    assert page.clicked == [HUSTLER_YES]
    assert len([a for a in actions if a.get("outcome") == "cleared"]) == 1


@pytest.mark.parametrize("label", [
    BANG_YES, "I'm over 18", "I am at least 18", "yes, i am 21 or older",
    "I am 18 years old", "Yes I'm 18 or over",
])
def test_other_bare_affirmations_also_clear(label):
    page = _wall(label)
    _run(page)
    assert page.clicked == [label], label


@pytest.mark.parametrize("label", [
    "I am 17", "I am 18 and I agree to the terms", "over 18 videos", "Yes",
    "No, I am under 18", "I am over 18 and want to subscribe",
])
def test_negative_control_a_sentence_or_the_wrong_number_is_never_admitted(label):
    assert _age_tier().fullmatch(label) is None, label
    page = _wall(label)
    _run(page)
    assert page.clicked == [], label


def test_negative_control_the_no_control_is_never_pressed():
    page = _wall(HUSTLER_YES, no_label="No")
    _run(page)
    assert "No" not in page.clicked
