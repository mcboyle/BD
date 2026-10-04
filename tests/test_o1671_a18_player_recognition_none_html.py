"""Absent capture markup must not reach regex matching as a None subject."""

import re

import pytest
from tools import player_recognition as recognition

BD_GATE_SCOPE = "module"


@pytest.mark.parametrize("pattern", ["video", "", ".*"])
def test_absent_html_never_matches(pattern):
    assert recognition._has(pattern, None) is False, "ABSENT_HTML_MATCHED"


@pytest.mark.parametrize(
    ("pattern", "html"),
    [
        ("video", "VIDEO player"),
        ("video.*player", "VIDEO\nPLAYER"),
        (r"data-player=\"jwplayer\"", 'data-player="jwplayer"'),
    ],
)
def test_present_html_retains_regex_flags(pattern, html):
    assert recognition._has(pattern, html) is True, "PRESENT_HTML_MATCH_LOST"


@pytest.mark.parametrize("html", ["", "plain document"])
def test_nonmatching_html_remains_false(html):
    assert recognition._has("video", html) is False


@pytest.mark.parametrize("html", ["", "video"])
def test_empty_regex_on_present_html_retains_match(html):
    assert recognition._has("", html) is True, "EMPTY_STRING_IS_PRESENT"


def test_invalid_regex_on_present_html_remains_an_error():
    with pytest.raises(re.error):
        recognition._has("[", "video")


@pytest.mark.parametrize("html", [42, object()])
def test_other_invalid_subjects_are_not_coerced(html):
    with pytest.raises(TypeError):
        recognition._has("video", html)
