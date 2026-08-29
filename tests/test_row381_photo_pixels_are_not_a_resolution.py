"""A photo-set pixel dimension is not a video resolution.

BD_GATE_SCOPE is a module-level ASSIGNMENT below, not a docstring line -- the
classifier in tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py parses the
assignment, and a docstring marker leaves the file undeclared.

MEASURED on test6 2026-08-29 (v3.66.1339, history rows 111 and 112). Two
nubilefilms scenes failed with "no dl event; scored ok but no download fired",
and the winning candidate text was a photo-set control:

    'Large 6000x4000px'  -> res_score 4000 -> res_label '6K'
    'Large 8192x5464px'  -> res_score 5464 -> res_label '8K'

while the real video anchor reads '2160p' -> 2160 -> '4K'. res_score's
`(\\d{3,4})[x×](\\d{3,4})` rule takes group 2 as a pixel HEIGHT, so a 3:2 photo
dimension outranks every real video tier, and NON_VIDEO_RE does not fire because
the text contains none of its words.

The fix anchors the second group on a word boundary before rejecting a `px`
suffix. A bare negative lookahead is NOT sufficient and was measured failing:
`(\\d{3,4})[x×](\\d{3,4})(?!\\s*px)` BACKTRACKS on '6000x4000px' and matches
('6000', '400') -- still a 400-high "resolution", from a number that does not
appear in the text. That regression is pinned below.
"""

BD_GATE_SCOPE = "module"

import pytest

from bulk_downloader.detect import res_score, res_label
from bulk_downloader.constants import NON_VIDEO_RE

# (text, why it is here) -- photo-set captions observed in the wild.
PHOTO_CAPTIONS = [
    ("Large 6000x4000px", "history row 111, scored 6K"),
    ("Large 8192x5464px", "history row 112, scored 8K"),
]

# Real video labels. Every one of these MUST keep its exact height.
VIDEO_LABELS = [
    ("1920x1080", 1080),
    ("3840x2160", 2160),
    ("7680x4320", 4320),
    ("1280 x 720", 720),
    ("2160p", 2160),
    # REGRESSION, caught by the band: "_" is a WORD character, so an earlier
    # \b-anchored draft killed this and broke the 60fps tiebreaker in
    # tests/test_v3_43_65_cascade.py::Test60FpsTiebreaker.
    ("1280x720_60FPS.mp4", 725),   # 720 + the documented +5 60fps boost
    ("1280x720.mp4", 720),
]


def test_precondition_non_video_re_does_not_catch_a_photo_caption():
    """The existing filter genuinely cannot see these -- otherwise this row is
    solving a problem that another mechanism already solves."""
    for text, _why in PHOTO_CAPTIONS:
        assert not NON_VIDEO_RE.search(text), (
            f"NON_VIDEO_RE already rejects {text!r}; res_score is not the seam")


def test_precondition_the_real_anchor_scores_lower_than_the_photo_caption():
    """The defect only bites because the photo number is BIGGER. Prove the
    ordering that makes it win, so a future change to either side is visible."""
    photo_raw = 4000          # group 2 of '6000x4000px' under the old rule
    real = res_score("2160p")
    assert real == 2160
    assert photo_raw > real, (
        "the photo dimension no longer exceeds the real tier; this test's "
        "premise has changed and the assertions below prove nothing")


@pytest.mark.parametrize("text,why", PHOTO_CAPTIONS)
def test_a_photo_pixel_dimension_is_not_a_video_resolution(text, why):
    got = res_score(text)
    assert got == -1, (
        f"{text!r} ({why}) scored {got} -> res_label {res_label(got)!r}; a "
        f"photo-set caption must contribute no video resolution at all")


@pytest.mark.parametrize("text,height", VIDEO_LABELS)
def test_negative_control_real_video_labels_keep_their_exact_height(text, height):
    """The guard must not cost a single real resolution."""
    assert res_score(text) == height


def test_a_tier_WORD_still_scores_even_beside_a_photo_dimension():
    """MEASURED, and it is why 'Medium 3000x2000px' is NOT in PHOTO_CAPTIONS.

    res_score has a third rule: named tier words. 'Medium' legitimately means
    360p, so that caption scores 360 -- from the WORD, not from the 2000-high
    dimension, which now contributes nothing. Asserting -1 here would have been
    asserting that a correct rule is broken, and 360 cannot outrank a real 4K
    anchor anyway. Pin both halves so a future change to either is visible."""
    assert res_score("Medium 3000x2000px") == 360
    assert res_score("3000x2000px") == -1


def test_the_truncating_backtrack_regression_stays_fixed():
    """A bare lookahead lets the regex backtrack to a SHORTER first alternative
    and match a number that is not in the text. Measured: ('6000', '400').

    Asserting `!= 400` alone would pass for the wrong reason if res_score simply
    returned -1 for everything, so the negative control above carries the other
    half of this claim."""
    for text in ("Large 6000x4000px", "Large 8192x5464px"):
        got = res_score(text)
        assert got not in (400, 546), (
            f"{text!r} scored {got}: the second group backtracked to a "
            f"truncated number rather than being rejected outright")
