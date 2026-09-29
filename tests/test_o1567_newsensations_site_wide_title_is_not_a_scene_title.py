"""fx-newsensations-generic-title (O1567, test7 live 2026-09-29 21:45Z).

newsensations' members area gives every page the same <title>, "New
Sensations Premium Access", and no og:title.  All three completed scenes
(gallery ids 11399, 11479, 11467) were recorded in history AND tagged in the
MP4 with that site-wide constant.  A whole title that another scene of the
site repeats verbatim names the site, not the scene: the harvest falls to the
next source (h1, then the listing card), and the teach-path MP4 tag follows
the history title.
"""
from __future__ import annotations

import pytest

from tests.test_history_records_the_website_title import (
    _FixturePage,
    _runner_and_page,
    _stop_runner,
)


BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

_SITE_WIDE = "New Sensations Premium Access"
_SCENE_A = "https://www.newsensations.com/members/gallery.php?id=11479&type=vids"
_SCENE_B = "https://www.newsensations.com/members/gallery.php?id=11467&type=vids"
_H1_A = "Lola Pearl Tiny Labia"
_H1_B = "Krystal Davis Oils Her Sofa"


def _members_page(h1: str = "") -> str:
    heading = f"<h1>{h1}</h1>" if h1 else ""
    return (f"<html><head><title>{_SITE_WIDE}</title></head>"
            f"<body>{heading}</body></html>")


def test_site_wide_title_falls_to_h1_and_the_first_scene_is_repaired(clean_workdir):
    runner, page, best, download_dir, target = _runner_and_page(
        clean_workdir,
        html=_members_page(_H1_A),
        filename="lolapearlstinylabia.mp4",
        url=_SCENE_A,
    )
    try:
        # One scene is no evidence: the first completion records the raw
        # document.title, exactly as before.
        runner._do_download(page, None, _SCENE_A, best, download_dir, "1080p")
        from bulk_downloader.db import db_conn

        with db_conn() as cx:
            before = [dict(r) for r in cx.execute(
                "SELECT title, title_source FROM library").fetchall()]
        assert before == [{"title": _SITE_WIDE, "title_source": "document.title"}]

        # The second scene repeats the whole title: it names the site.
        other = _FixturePage(_SCENE_B, _members_page(_H1_B))
        assert runner._capture_website_title(other, _SCENE_B) == (_H1_B, "h1")
        assert other.evaluate_calls == 1

        # The first scene's cached candidates repair its history row too.
        with db_conn() as cx:
            after = [dict(r) for r in cx.execute(
                "SELECT title, title_source FROM library").fetchall()]
        assert after == [{"title": _H1_A, "title_source": "h1"}]
        assert runner._history_title_fields(_SCENE_A) == {
            "title": _H1_A, "title_source": "h1"}
        assert runner._history_title_fields(_SCENE_B) == {
            "title": _H1_B, "title_source": "h1"}
        assert target.exists()
    finally:
        _stop_runner(runner)


def test_teach_path_mp4_tag_uses_the_scene_title_not_the_site_title(clean_workdir):
    runner, page, best, download_dir, _target = _runner_and_page(
        clean_workdir,
        html=_members_page(_H1_B),
        filename="krystaldavissoilshersofa.mp4",
        url=_SCENE_B,
    )
    tagged: list[dict] = []
    runner._embed_metadata_if_mp4 = lambda path, **kw: tagged.append(kw) or True
    try:
        # An earlier scene of this site already showed the same <title>.
        earlier = _FixturePage(_SCENE_A, _members_page(_H1_A))
        runner._capture_website_title(earlier, _SCENE_A)

        runner._do_download(page, None, _SCENE_B, best, download_dir, "1080p")
        assert [kw["title"] for kw in tagged] == [_H1_B], tagged
    finally:
        _stop_runner(runner)


def test_site_wide_title_with_no_other_source_records_no_title(clean_workdir):
    runner, _page, _best, _dir, _target = _runner_and_page(
        clean_workdir,
        html=_members_page(),
        filename="unused.mp4",
        url=_SCENE_A,
    )
    try:
        runner._capture_website_title(_FixturePage(_SCENE_A, _members_page()), _SCENE_A)
        # Never invent a title: no h1 and no listing card -> empty, not the site.
        assert runner._capture_website_title(
            _FixturePage(_SCENE_B, _members_page()), _SCENE_B) == ("", "")
        assert runner._history_title_fields(_SCENE_B) == {
            "title": "", "title_source": ""}
    finally:
        _stop_runner(runner)


def test_negative_control_distinct_document_titles_are_kept():
    from bulk_downloader.website_title import choose_scene_title

    # Distinct per-scene titles are scene titles; h1 is never consulted.
    assert choose_scene_title(
        [("Her Deepest Needs :: Wow Girls", "document.title"),
         ("Wow Girls", "h1")],
        {"https://venus.wowgirls.com/film/a/x": "Stunning Princess :: Wow Girls"},
    ) == ("Her Deepest Needs :: Wow Girls", "document.title")
    # The existing template rule still strips a repeated leading template.
    assert choose_scene_title(
        [("UltraFilms / Members / Movie / With Leo In Bed", "og:title")],
        {"u": "UltraFilms / Members / Movie / Another Scene"},
    ) == ("With Leo In Bed", "og:title")
    # A whole-title repeat falls through every candidate it matches.
    assert choose_scene_title(
        [(_SITE_WIDE, "document.title"), (_H1_A, "h1")],
        {_SCENE_B: _SITE_WIDE},
    ) == (_H1_A, "h1")
