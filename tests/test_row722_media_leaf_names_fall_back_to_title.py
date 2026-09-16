"""Row 722 G29 (test2, 2026-09-15): the saved name was the URL LEAF.

    filthykings  /movieaction/download/292639/2160p/mp4 -> "mp4.mp4"
    dfxtra / evilangel                                   -> "mp4.mp4"
    vip4k                                                -> "360p.mp4"
    nookies      /membersarea/video/stream/3480          -> "high.mp4"

Every one of those pages carried a title G19 already harvests into history.
A leaf that is only a format / tier token is treated as NO name and falls
back, in order: Content-Disposition filename -> website title + tier + ext ->
the scene URL's last meaningful segment.  A leaf with a real stem is never
renamed (wowgirls/newsensations/nubiles/kink got real names from their hrefs),
and no title is ever invented from a filename.
"""
from __future__ import annotations

import pytest

BD_GATE_SCOPE = "module"

from bulk_downloader.runner_transport import (
    _is_bare_media_leaf, resolve_media_leaf_name,
)

TITLE = "Enchanting Pixie | Members Area | Tiny 4K!"


@pytest.mark.parametrize("leaf", [
    "mp4.mp4", "mp4", "high.mp4", "360p.mp4", "1080p", "4k.mp4", "8K.webm",
    "stream.mp4", "download.mp4", "video.mp4", "file.mp4", "index.mp4",
    "hd.mov", "full.m4v", "HIGH.MP4",
])
def test_bare_leaf_is_detected(leaf):
    assert _is_bare_media_leaf(leaf), (
        f"{leaf!r} is a bare format/tier leaf but was accepted as a name")


@pytest.mark.parametrize("leaf", [
    "ChelseaBaby_Enchanting_Pixie_7680x4320_60fps.mp4", "108453_shoot_4k.mp4",
    "Real_Name_2160.mp4", "scene-292639.mp4", "mp4_collection.mp4",
    "high_tide.mp4", "", None,
])
def test_real_stem_is_not_bare(leaf):
    assert not _is_bare_media_leaf(leaf), leaf


def test_mp4_leaf_takes_the_website_title_and_tier():
    """THE ROW (filthykings): "mp4.mp4" -> title-derived name."""
    got = resolve_media_leaf_name(
        "mp4.mp4", website_title="Bad Girl Chelsea", tier="2160p",
        scene_url="https://site.example/movies/bad-girl-chelsea")
    assert got != "mp4.mp4", (
        "the URL leaf 'mp4.mp4' was kept as the file name although the page "
        "title was known")
    assert got == "Bad Girl Chelsea [2160p].mp4", got


def test_high_leaf_nookies():
    got = resolve_media_leaf_name(
        "high.mp4", website_title="Cool Summer Nights", tier="",
        scene_url="https://site.example/membersarea/video/stream/3480")
    assert got == "Cool Summer Nights.mp4", got


def test_tier_leaf_carries_its_own_tier_into_the_suffix():
    got = resolve_media_leaf_name(
        "360p.mp4", website_title="Scene Title",
        scene_url="https://site.example/video/1234")
    assert got == "Scene Title [360p].mp4", got


def test_tier_already_in_title_is_not_doubled():
    got = resolve_media_leaf_name("mp4.mp4", website_title="Pixie 4K", tier="4K")
    assert got == "Pixie 4K.mp4", got


def test_extensionless_format_token_supplies_the_extension():
    got = resolve_media_leaf_name("mp4", website_title="Pixie")
    assert got == "Pixie.mp4", got
    got = resolve_media_leaf_name("high", website_title="Pixie")
    assert got == "Pixie.mp4", got


def test_title_is_sanitised_for_the_filesystem():
    got = resolve_media_leaf_name(
        "mp4.mp4", website_title='A: "Quoted" / Title <x>', tier="1080p")
    assert "/" not in got and ":" not in got and '"' not in got, got
    assert got.endswith(" [1080p].mp4"), got


def test_content_disposition_wins_over_the_title():
    got = resolve_media_leaf_name(
        "mp4.mp4", disposition_name="site_scene_2160.mp4",
        website_title="Pixie", tier="2160p")
    assert got == "site_scene_2160.mp4", got


def test_bare_content_disposition_is_no_name_either():
    got = resolve_media_leaf_name(
        "mp4.mp4", disposition_name="video.mp4", website_title="Pixie")
    assert got == "Pixie.mp4", got


def test_no_title_no_disposition_uses_the_scene_slug():
    got = resolve_media_leaf_name(
        "mp4.mp4", scene_url="https://site.example/movies/bad-girl-chelsea/")
    assert got == "bad-girl-chelsea.mp4", got
    got = resolve_media_leaf_name(
        "high.mp4",
        scene_url="https://site.example/membersarea/video/stream/3480?x=1")
    assert got == "3480.mp4", got


def test_nothing_known_keeps_the_leaf_never_invents():
    assert resolve_media_leaf_name("mp4.mp4") == "mp4.mp4"
    assert resolve_media_leaf_name("mp4.mp4", scene_url="https://x.test/") == "mp4.mp4"
    assert resolve_media_leaf_name("mp4.mp4", scene_url="https://x.test/download/video") == "mp4.mp4"


@pytest.mark.parametrize("name", [
    "ChelseaBaby_Enchanting_Pixie_7680x4320_60fps.mp4", "108453_shoot_4k.mp4",
    "Real_Name_2160.mp4",
])
def test_negative_control_real_stem_is_never_renamed(name):
    got = resolve_media_leaf_name(
        name, disposition_name="other.mp4", website_title=TITLE, tier="8K",
        scene_url="https://site.example/scene/1")
    assert got == name, f"a real stem was renamed: {name!r} -> {got!r}"


def test_the_transport_placeholder_is_left_alone():
    assert resolve_media_leaf_name("download.bin", website_title=TITLE) == "download.bin"


def test_hex_hash_and_uuid_leaves_are_bare_too():
    """brazzers/bangbros live: CDN object names are content hashes."""
    from bulk_downloader import runner_transport as rt
    assert rt._is_bare_media_leaf("936997063d2ca6f8c6d81fae3ffcb77e3578feda.mp4")
    assert rt._is_bare_media_leaf("2da1abca15649adf10e5fed168dab32a56feec8e.mp4")
    assert rt._is_bare_media_leaf("3b1f2c4d-1a2b-4c3d-9e8f-0a1b2c3d4e5f.mp4")
    # positive controls: real stems with digits stay names
    assert not rt._is_bare_media_leaf("108453_shoot_4k.mp4")
    assert not rt._is_bare_media_leaf("ChelseaBaby_SumikoSmile_StunnedByEachOther_7680x4320_60fps.mp4")
    assert not rt._is_bare_media_leaf("deadbeef.mp4")  # 8 hex chars could be a word

