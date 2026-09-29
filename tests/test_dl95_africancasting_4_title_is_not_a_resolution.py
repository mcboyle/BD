"""dl95-africancasting-4 (DOT95-LANE/live-dl95-africancasting-3/LIVE-RESULT-A1-A.md, history 266).

On test2 an africancasting scene went to needs_review "below 1080p; got 240p; saw: 240p(?):Spicy Doll seeks makeup sex
wi...". That candidate was a related-scene card TITLE. detect.res_score falls back to named tier words when no height
is written ("tiny"/"mobile" = 240, "low" = 360, "hd" = 720). The wide sweep then admitted the title as a quality
option, and the min-res gate refused the scene over it.

Drives the real detect.find_best_download on a real chromium page (BD's cloak wrapper, set_content; no network).
Controls keep every real quality signal: a short badge, a download option in long text, and an explicit height in
long text.
"""
from __future__ import annotations

import pytest

BD_GATE_SCOPE = "module"

TITLES = [
    "Spicy Doll seeks makeup sex with a tiny twist",
    "Mobile girl meets her match in the city",
    "Low key evening turns into something more",
]


def _cards(titles):
    return "".join(
        f'<div class="card"><a href="/video/other-{i}"><img src="/t/{i}.jpg"></a>'
        f'<a class="title" href="/video/other-{i}">{t}</a></div>'
        for i, t in enumerate(titles))


@pytest.fixture(scope="module")
def page():
    from bulk_downloader import cloak

    with cloak.cloaked_page(headless=True, config={"browser_backend": "playwright"},
                            viewport={"width": 1280, "height": 720}) as p:
        yield p


def _best(page, body):
    from bulk_downloader import detect

    page.set_content(f"<!doctype html><html><body><main><h1>Scene</h1>{body}</main></body></html>")
    return detect.find_best_download(page)


def test_precondition_titles_carry_tier_words():
    """Positive control: res_score DOES read these titles as resolutions; a zero below is a measurement."""
    from bulk_downloader import detect

    assert [detect.res_score(t) for t in TITLES] == [240, 240, 360]


def test_related_card_titles_are_not_quality_candidates(page):
    """Not admitted at all: admitted at score 0, a title link could still win and be clicked as the download."""
    best = _best(page, _cards(TITLES))
    admitted = [c for c in (best.get("_all_candidates") or [best] if best else [])
                if any(t in c.get("text", "") for t in TITLES)]
    assert not admitted, f"dl95-africancasting-4: a link TITLE was admitted as a quality candidate: {admitted}"


def test_title_does_not_outrank_or_undercut_the_scene_option(page):
    best = _best(page, _cards(TITLES) + '<a href="/dl/scene_1080p.mp4">Download 1080p</a>')
    assert best and best["score"] == 1080, best
    assert all(c["score"] in (0, 1080) for c in best.get("_all_candidates", [])), best.get("_all_candidates")


@pytest.mark.parametrize("body,score", [
    ('<a href="/scene/quality-hd">HD</a>', 720),
    ('<a href="/get/scene">Download the Full HD version of this scene</a>', 1080),
    ('<a href="/scene/full">Watch the full scene in 1080p right now</a>', 1080),
    # bd-cx-worker-2 REFUTE F1: a named-tier rendition option with format/size metadata is not a title.
    ('<div id="player"><div class="quality-options"><a href="/scene/quality-hd">Full HD MP4 (2.1 GB)</a></div></div>', 1080),
    ('<a href="/scene/quality-fhd">Full HD version of this scene 2.1 GB</a>', 1080),
    ('<a href="/scene/quality-4k">Ultra HD version of this scene in HEVC</a>', 2160),
], ids=["badge", "download-option", "explicit-height", "cx2-format-and-size", "size-only", "codec-only"])
def test_real_quality_signals_still_score(page, body, score):
    best = _best(page, body)
    assert best and best["score"] == score, best
