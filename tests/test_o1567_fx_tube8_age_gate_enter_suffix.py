"""O1567 fx-tube8 (fresh149, 2026-09-29 20:2xZ): Tube8's wall is the "This is an
adult website" modal with "I am 18 or older - Enter" beside "I am under 18 -
Exit". The label carries an " - Enter" tail that neither the generic age tier
nor AGE_CONTROL_LABEL admitted, so the wall was never pressed: the runner
clicked "[auto]" behind it, the extractor scraped a 300x250 ad banner
(3299031_video.mp4, 6 s) and the job reported done.

Contract: a bare age affirmation followed by an " - Enter" / ": Enter" tail is
admitted; the EXIT sibling and sentences containing an affirmation are not.
"""
from __future__ import annotations

import pytest

from bulk_downloader import interstitial
from tests.test_row721_age_gate_enter_affordance import _Control, _GatePage

BD_GATE_SCOPE = "module"

TUBE8_TEXT = ("This is an adult website. This website contains age-restricted "
              "materials including nudity and explicit depictions of sexual "
              "activity. By entering, you affirm that you are at least 18 years "
              "of age or the age of majority in the jurisdiction you are "
              "accessing the website from and you consent to viewing sexually "
              "explicit content.")
TUBE8_ENTER = "I am 18 or older - Enter"
TUBE8_EXIT = "I am under 18 - Exit"


def _age_tier():
    return [p for t, p in interstitial._GENERIC_TIERS if t == "age"][0]


def _wall(yes_label=TUBE8_ENTER, no_label=TUBE8_EXIT):
    return _GatePage("https://www.tube8.com/porn-video/195232531/", TUBE8_TEXT,
                     [_Control(yes_label, next_text="Video"), _Control(no_label)])


def _run(page):
    return interstitial.dismiss_gates(
        page, "", destination_url=page.url, settle_s=0.0, sleep=lambda _s: None)


def test_the_tube8_wall_is_cleared_by_its_enter_control():
    assert _age_tier().fullmatch(TUBE8_ENTER) is not None, (
        "the generic age tier refuses %r: the wall stays up and the extractor "
        "scrapes an ad banner" % TUBE8_ENTER)
    page = _wall()
    actions = _run(page)
    assert page.clicked == [TUBE8_ENTER]
    assert len([a for a in actions if a.get("outcome") == "cleared"]) == 1


@pytest.mark.parametrize("label", [
    "I am 18 or older - Enter", "I am 18 or over: Enter",
    "Yes, I am 21 or older – Enter",
])
def test_enter_tail_variants_clear(label):
    page = _wall(label)
    _run(page)
    assert page.clicked == [label], label


def test_age_control_label_admits_the_tail_for_a_configured_selector():
    assert interstitial.AGE_CONTROL_LABEL.fullmatch(TUBE8_ENTER) is not None


@pytest.mark.parametrize("label", [
    TUBE8_EXIT, "I am under 18 - Enter", "I am 17 - Enter",
    "I am 18 or older - Enter and pay", "over 18 videos - Enter",
    "I am 18 or older - Exit",
])
def test_negative_control_exit_and_wrong_labels_never_admitted(label):
    assert _age_tier().fullmatch(label) is None, label
    page = _wall(label, no_label="No")
    _run(page)
    assert page.clicked == [], label


def test_negative_control_the_exit_control_is_never_pressed():
    page = _wall()
    _run(page)
    assert TUBE8_EXIT not in page.clicked
