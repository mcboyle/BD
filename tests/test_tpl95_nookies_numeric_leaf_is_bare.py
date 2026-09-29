"""tpl95-nookies-1: a numeric-only media leaf is a bare leaf -- the file takes the website title.

Live on test2 (harness-work/UIUX-20260928/download-95/B6-B/tpl/nookies-proof-v2/tplproof-nookies-v2.json): with the
learned template, nookies 3514645b hit "#downloadModal a[download]" -> /membersarea/video/stream/3504 and landed
"3504.mp4". The same site's heuristic path names files by website title, because its leaf ("high.mp4") is a known
bare token. A numeric leaf is a route id, not a name.

  1. "3504.mp4" / "3504" are bare leaves; names with any word ("scene-3504", "3504_shoot") are not.
  2. resolve_media_leaf_name turns "3504.mp4" into the website title.
  3. With no title, the scene URL's numeric id is still an acceptable fallback stem (never "video").
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bulk_downloader.runner_transport import _is_bare_media_leaf, resolve_media_leaf_name  # noqa: E402

BD_GATE_SCOPE = "module"

SCENE = "https://nookies.example/membersarea/video/3504"


@pytest.mark.parametrize("leaf", ["3504.mp4", "3504", "12345678.webm"])
def test_numeric_leaf_is_bare(leaf):
    assert _is_bare_media_leaf(leaf), f"TPL95_NOOKIES_NUMERIC_NOT_BARE: {leaf!r}"


@pytest.mark.parametrize("leaf", ["scene-3504.mp4", "3504_shoot.mp4", "Real_Name_2160.mp4", "108453_shoot_4k.mp4"])
def test_a_leaf_with_words_is_still_a_name(leaf):
    assert not _is_bare_media_leaf(leaf), leaf


def test_numeric_leaf_takes_the_website_title():
    got = resolve_media_leaf_name("3504.mp4", website_title="Cool Summer Nights - Nookies", scene_url=SCENE)
    assert got == "Cool Summer Nights - Nookies.mp4", f"TPL95_NOOKIES_NOT_TITLED: {got!r}"


def test_without_a_title_the_numeric_scene_id_is_the_fallback_not_a_route_word():
    got = resolve_media_leaf_name("high.mp4", website_title="", scene_url=SCENE)
    assert got == "3504.mp4", f"TPL95_NOOKIES_FALLBACK: {got!r}"
