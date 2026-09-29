"""dl95-youporn-1: a related-video tile must not win a numeric-id watch page.

MEASURED on test2 (10.0.70.95) 2026-09-28, v3.66.1707, youporn job https://www.youporn.com/watch/189547511/:
history row 131 needs_review "no dl event; scored ok but no download fired; saw: 4K(?):FUCK A FAN 4K ...".
Replaying the page saved the same hour offline through ``find_best_download`` gave WINNER score=2160
``<a href="/watch/17134715/">`` -- a related tile -- with every candidate at work=0 (UNKNOWN).

Cause: the page names its work only by the numeric id, so ``page_work_tokens`` reads just 'watch', and a
tile href ``/watch/17134715/`` derives no slug identity either; UNKNOWN tiles are admitted by the marked
fallback and the tile whose title says "4K" out-scores everything. Fix: on the page's own host and route, a
link to a DIFFERENT numeric id is FOREIGN -- for the candidate's own URL attributes and for the link a
thumbnail <img> sits inside. Same id, other hosts, other routes and short numbers stay UNKNOWN.

Hosts are ``.example`` and every request is served by route interception: no network, no real site.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from bulk_downloader import detect
from bulk_downloader.detect import find_best_download

BD_GATE_SCOPE = "module"

_PAGE = "https://tube.example/watch/189547511/"

_TILE = """
<article class="video-box" data-video-id="{vid}" aria-label="{title}">
  <a href="/watch/{vid}/" class="tm_video_link">
    <img class="thumb-image" alt="{title}"
         data-src="https://cdn.tube.example/videos/{vid}/original_{vid}.mp4/plain/rs:fit:320:180">
  </a>
  <a href="/watch/{vid}/" class="video-title-text"><span>{title}</span></a>
</article>
"""

_HTML = """<!doctype html><html><head><title>Requested video</title></head><body>
<main>
  <h1>Requested video</h1>
  <div id="player" data-video-id="189547511">
    <a class="own-download" href="https://cdn.tube.example/get/189547511_720p.mp4">Download 720p</a>
  </div>
</main>
<aside class="related">{tiles}</aside>
</body></html>""".format(
    tiles="".join(
        _TILE.format(vid=vid, title=title)
        for vid, title in (
            ("103311411", "Mountain Trip 4K - Day Two"),
            ("17134715", "City Lights 2160p Special"),
        )
    )
)


def _numeric_rule():
    rule = getattr(detect, "_candidate_names_another_numeric_work", None)
    assert rule is not None, (
        "DL95_YOUPORN_NO_NUMERIC_RULE: detect has no numeric work-id FOREIGN rule"
    )
    return rule


@contextmanager
def _page(url, html):
    """A page served AT `url` without touching the network (row 388's fixture shape)."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch()
        try:
            pg = br.new_page(viewport={"width": 1400, "height": 900})
            pg.route(
                "**/*",
                lambda route: (
                    route.fulfill(status=200, content_type="text/html", body=html)
                    if route.request.resource_type == "document"
                    else route.abort()
                ),
            )
            pg.goto(url, wait_until="domcontentloaded")
            assert pg.url == url, f"fixture not served at its own URL: {pg.url!r}"
            yield pg
        finally:
            br.close()


def _link_of(locator):
    return locator.evaluate(
        "e => e.getAttribute('href') || (e.closest('a[href]') || {getAttribute: () => ''}).getAttribute('href')"
    )


def test_related_numeric_id_tiles_never_win_the_watch_page():
    with _page(_PAGE, _HTML) as pg:
        best = find_best_download(pg)
        assert best, (
            "find_best_download returned nothing on a page with an own download link"
        )
        winner = _link_of(best["locator"])
        seen = [
            (c["score"], c.get("work"), c["text"][:40])
            for c in best.get("_all_candidates", [])
        ]
        assert "/watch/" not in (winner or ""), (
            f"DL95_YOUPORN_TILE_WON: related tile {winner!r} (score {best.get('score')}) won "
            f"{_PAGE}; candidates={seen}"
        )
        assert winner == "https://cdn.tube.example/get/189547511_720p.mp4", (
            winner,
            seen,
        )


def test_tile_link_and_its_thumbnail_are_both_foreign():
    """The <img> inside the tile link is refused through the link it sits in."""
    from bulk_downloader.detect import _WORK_FOREIGN, _candidate_work_affinity

    with _page(_PAGE, _HTML) as pg:
        link = pg.locator("a.video-title-text").first
        thumb = pg.locator("img.thumb-image").first
        own = pg.locator("a.own-download").first
        assert _candidate_work_affinity(link, _PAGE) == _WORK_FOREIGN
        assert _candidate_work_affinity(thumb, _PAGE) == _WORK_FOREIGN
        assert _candidate_work_affinity(own, _PAGE) != _WORK_FOREIGN


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/watch/17134715/", True),
        ("/watch/13101383/some-other-title/", True),
        ("https://tube.example/watch/103311411/", True),
        # controls: never a FOREIGN finding
        ("/watch/189547511/", False),  # the page's own id
        ("/watch/0189547511/", False),  # same id, zero-padded
        ("https://other.example/watch/17134715/", False),  # another host
        ("/channel/17134715/", False),  # another route
        ("/watch/2026/", False),  # a year, not a work id
        ("https://tube.example/watch/17134715/clip.mp4", False),  # media path (row 388)
        ("#", False),
        ("javascript:void(0)", False),
    ],
)
def test_numeric_work_rule(value, expected):
    assert _numeric_rule()(_PAGE, value) is expected


def test_control_page_without_numeric_id_condemns_nothing():
    """A slug-only page (row 388's shape) gets no numeric verdict at all."""
    page = "https://members.example/video/seeing-red-s50e30"
    rule = _numeric_rule()
    assert rule(page, "/video/watch/254257/") is False
    assert rule("about:blank", "/watch/17134715/") is False


def test_control_own_link_wins_when_tiles_carry_no_resolution():
    """Passes on BASE and cut: the page's own tier keeps winning where no tile competes on score."""
    html = _HTML.replace("Mountain Trip 4K - Day Two", "Mountain Trip Day Two").replace(
        "City Lights 2160p Special", "City Lights Special"
    )
    with _page(_PAGE, html) as pg:
        best = find_best_download(pg)
        assert (
            best
            and _link_of(best["locator"])
            == "https://cdn.tube.example/get/189547511_720p.mp4"
        )
