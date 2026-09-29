"""tpl95-justporn-1: a learned row inside a CLOSED dropdown is still a hit when the template reads its URL.

Live on test2 (justporn 73b3e3dd, template user_b6b_justporn_o1517, /video/29807/): learned._per_selector hits 0
misses 1. Read-only probe (harness-work/FIX/tpl95-justporn-1-bd-worker-B10-B/probe1.txt): both
``ul.fav-drop a.download-link`` rows (29807_720p.mp4, 29807.mp4) report is_visible() False. Their ``ul.fav-drop`` is
``opacity:0; visibility:hidden`` until a click on ``button.drop-btn`` adds ``.fav-open`` to ``.btn-holder``
(css.txt). The learned loop skipped every invisible row (v3.66.247: "cannot be clicked"), so the template never hit.
The template's ``url_attribute`` is ``href``: the transport fetches the attribute directly, so no click is needed.

  1. Hidden rows whose resolved url_attribute carries a value are a learned hit, best tier first.
  2. They are a FALLBACK only: any visible row from the learned selectors still wins, and hidden rows are not scored
     next to it (v3.66.247's hidden-decoy guarantee).
  3. Without a url_attribute, or with an empty one, a hidden row is still skipped (click-and-capture needs a
     clickable row).

The fixture rebuilds the probed ancestor chain (div.video-controls > div.col.second > div.btn-holder > ul.fav-drop >
li > a.download-link) and the page's own two CSS rules. Hosts are ``.example``; hrefs are placeholders.
"""
from contextlib import contextmanager

import pytest

from bulk_downloader.detect import find_best_download

BD_GATE_SCOPE = "module"

URL = "https://justporn.example/video/29807/hot-girl-teasing-her-body-alone/"
LEARNED = {
    "row_selectors": [
        "ul.fav-drop a.download-link[href*='/get_file/'][href*='.mp4']",
        "a.download-link[href*='/get_file/'][href*='.mp4']",
    ],
    "url_attribute": "href",
    "tier_labels_seen": ["720p", "original"],
}
CSS = """
.btn-holder { position: relative; }
.fav-drop { position: absolute; top: calc(100% + 10px); z-index: 2; padding: 10px 15px; width: 150px;
            opacity: 0; visibility: hidden; }
.btn-holder.fav-open .fav-drop { opacity: 1; visibility: visible; }
"""
ROWS = """
<li><a class="download-link" href="https://justporn.example/get_file/7/aaaa/29000/29807/29807_720p.mp4/">720p</a></li>
<li><a class="download-link" href="https://justporn.example/get_file/7/bbbb/29000/29807/29807.mp4/">Original</a></li>
"""


def _html(holder_class="btn-holder", extra=""):
    return (
        "<html><head><title>Hot girl teasing her body alone</title><style>%s</style></head><body>"
        "<div class='video-controls flex'><div class='col second flex sort-control'>"
        "<div class='%s'><button class='btn drop-btn second'>Download</button>"
        "<ul class='fav-drop'>%s</ul></div></div></div>%s</body></html>" % (CSS, holder_class, ROWS, extra))


@contextmanager
def _page(html):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch()
        try:
            pg = br.new_page(viewport={"width": 1400, "height": 900})
            pg.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
            pg.goto(URL, wait_until="load")
            assert pg.url == URL, "route interception did not serve the fixture at its own URL; got %r" % pg.url
            yield pg
        finally:
            br.close()


def test_fixture_reproduces_the_hidden_rows():
    with _page(_html()) as pg:
        loc = pg.locator(LEARNED["row_selectors"][0])
        assert loc.count() == 2
        assert [loc.nth(i).is_visible() for i in range(2)] == [False, False], "fixture lost the closed dropdown"


def test_hidden_dropdown_rows_are_a_learned_hit_when_the_template_reads_href():
    with _page(_html()) as pg:
        best = find_best_download(pg, learned=dict(LEARNED))
        assert best is not None and best.get("_via_learned") is True, (
            "TPL95_JUSTPORN_HIDDEN_ROW_MISSED: %r" % (best and {k: best.get(k) for k in ("text", "_via_learned")}))
        assert best["_learned_sel"] == LEARNED["row_selectors"][0]
        href = best["locator"].get_attribute("href")
        assert href.endswith("29807_720p.mp4/"), "TPL95_JUSTPORN_WRONG_TIER: %r" % href


def test_open_dropdown_is_unchanged():
    with _page(_html("btn-holder fav-open")) as pg:
        best = find_best_download(pg, learned=dict(LEARNED))
        assert best is not None and best.get("_via_learned") is True
        assert best["locator"].get_attribute("href").endswith("29807_720p.mp4/")


def test_a_visible_row_wins_and_hidden_rows_are_not_scored_beside_it():
    visible = ("<p><a class='download-link' href='https://justporn.example/get_file/7/cccc/29000/29807/"
               "29807_480p.mp4/'>480p</a></p>")
    with _page(_html(extra=visible)) as pg:
        best = find_best_download(pg, learned=dict(LEARNED))
        assert best is not None and best.get("_via_learned") is True
        assert best["locator"].get_attribute("href").endswith("29807_480p.mp4/"), (
            "TPL95_JUSTPORN_HIDDEN_BEAT_VISIBLE: %r" % best["locator"].get_attribute("href"))
        assert "720" not in " ".join(c["text"] for c in best.get("_all_candidates", []))


@pytest.mark.parametrize("url_attribute", ["", None])
def test_without_a_url_attribute_hidden_rows_stay_skipped(url_attribute):
    learned = dict(LEARNED)
    if url_attribute is None:
        del learned["url_attribute"]
    else:
        learned["url_attribute"] = url_attribute
    with _page(_html()) as pg:
        best = find_best_download(pg, learned=learned)
        assert not (best and best.get("_via_learned")), "click-and-capture admitted an unclickable row"


def test_a_hidden_row_with_an_empty_url_attribute_is_skipped():
    learned = dict(LEARNED, row_selectors=["ul.fav-drop a.download-link"], url_attribute="data-src")
    with _page(_html()) as pg:
        best = find_best_download(pg, learned=learned)
        assert not (best and best.get("_via_learned")), "a hidden row with no URL to read was admitted"
