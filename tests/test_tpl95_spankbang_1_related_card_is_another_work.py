"""tpl95-spankbang-1: a related-scene card on the SAME route with ANOTHER id
and ANOTHER slug belongs to another work, even when its slug shares a token
run with the page's.

Measured on test2 (2026-09-29, harness-work/UIUX-20260928/download-95/B9-B/
tpl-spankbang/RESULT.md): scene a594c went needs_review "below 1080p; got
240p" with `Saw:` naming the related cards /a57yw/video/dark+haired+18... and
/a57ye/video/brunette+18+te...  The page slug and each card slug share the
contiguous run ('18', 'teen') -- 2 tokens, 6 chars, exactly the
`work_affinity` bar -- so the cards were stamped IN_SCOPE and decided the
quality gate for a scene they are not.

Hermetic: pure URL functions plus a stub element; no network, no browser.
"""

import pytest

from bulk_downloader.detect import (
    _WORK_FOREIGN,
    _WORK_IN_SCOPE,
    _candidate_names_another_work,
    _candidate_work_affinity,
    work_affinity,
)

BD_GATE_SCOPE = "repo-wide"

# Route shape copied from the measured page; host is `.example`.
_PAGE = ("https://sb.example/a594c/video/"
         "innocent+petite+swedish+18+teen+leaked+snapchat+nudes")
_CARD_REL = "/a57yw/video/dark+haired+18+teen+playing+with"
_CARD_ABS = "https://sb.example/a57ye/video/brunette+18+teen+with+tiny"


class _El:
    def __init__(self, **attrs):
        self._attrs = attrs

    def get_attribute(self, name):
        return self._attrs.get(name)


def test_the_fixture_carries_the_measured_coincidental_run():
    # Positive control for the RED: without the route shape the run alone
    # is what the pre-fix stamp saw, and it clears the bar.
    assert work_affinity(_PAGE, "18+teen+playing") == 1


@pytest.mark.parametrize("card", [_CARD_REL, _CARD_ABS])
def test_a_sibling_route_card_is_not_this_work(card):
    assert work_affinity(_PAGE, card) == 0
    assert _candidate_names_another_work(_PAGE, card) is True


@pytest.mark.parametrize("card", [_CARD_REL, _CARD_ABS])
def test_the_card_element_is_stamped_foreign_not_in_scope(card):
    assert _candidate_work_affinity(_El(href=card), _PAGE) == _WORK_FOREIGN


# ── Negative controls: the guard must not condemn this scene's own links. ──

@pytest.mark.parametrize("own", [
    _PAGE,
    _PAGE + "?quality=1080p",
    "/a594c/video/innocent+petite+swedish+18+teen+leaked+snapchat+nudes",
])
def test_the_scene_own_route_stays_in_scope(own):
    assert work_affinity(_PAGE, own) == 1
    assert _candidate_work_affinity(_El(href=own), _PAGE) == _WORK_IN_SCOPE


def test_another_host_with_the_same_shape_keeps_the_run_verdict():
    # Cross-host is not a sibling route; the pre-existing verdict stands.
    other = "https://cdn.other.example/a57yw/video/dark+haired+18+teen"
    assert work_affinity(_PAGE, other) == 1


def test_a_different_route_word_keeps_the_run_verdict():
    # /a57yw/embed/... is not the same route as /a594c/video/...
    assert work_affinity(
        _PAGE, "/a57yw/embed/dark+haired+18+teen+playing") == 1


def test_a_differing_non_id_segment_is_not_a_sibling():
    # 'video' vs 'clips' carries no digit: not an id, so no sibling verdict.
    page = "https://sb.example/v/video/a594c/innocent+petite+swedish+18+teen"
    assert work_affinity(page, "/v/clips/a57yw/dark+haired+18+teen") == 1


def test_same_route_same_slug_other_id_keeps_the_run_verdict():
    # Only a DIFFERENT slug plus a different id names another work.
    assert work_affinity(
        _PAGE,
        "/a594d/video/innocent+petite+swedish+18+teen+leaked+snapchat+nudes",
    ) == 1
