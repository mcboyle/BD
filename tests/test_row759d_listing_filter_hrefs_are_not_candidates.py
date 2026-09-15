"""Row 759d -- a listing FILTER href is not a download candidate.

Measured live 2026-09-15 on dfxtra and evilangel (2/2, O738; record:
bd-persist/live-proof/row759c-A6-A/{dfxtra-02-runB,evilangel-01-run}.out):
the scene page carries the site's Algolia refinement sidebar, whose links
read ``4K (2160p) (1234)`` and point at ``/en/videos/?refinementList[...]``.
``res_score`` reads the ``2160p`` in that label, so the filter link outranks
the score-0 ``Download`` div that opens the quality modal, and
``_download_from_revealed_modal`` (row 759c) never runs: the runner reports
``scored ok but no download fired`` and the operator gets nothing.

CONTRACT: a candidate whose href is a LISTING/FILTER URL -- a refinementList
query, or facet/sort/filter query parameters, with no strong media signal --
is refused at admission, BEFORE ranking, and the refusal is COUNTED in the
``candidate_admission_filtered`` summary (row 499 vocabulary). The score-0
``Download`` control then wins, as row 759c requires. A real download link
that merely lives beside the sidebar is untouched.
"""

BD_GATE_SCOPE = "module"

from contextlib import contextmanager

import pytest

from bulk_downloader.detect import find_best_download, res_score

# The two hrefs recorded live (the runner's "Saw:" list truncates the query
# at ``refinementList%5Bv``; the parameter name is Algolia's fixed schema).
_FILTER_4K = "/en/videos/?refinementList%5Bvideo_quality%5D%5B0%5D=2160p"
_FILTER_HD = "/en/videos/?refinementList%5Bvideo_quality%5D%5B0%5D=1080p"
_SIDEBAR = f"""
<div class="ais-RefinementList">
  <ul class="ais-RefinementList-list">
    <li><a class="ais-RefinementList-link" href="{_FILTER_4K}">4K (2160p) <span>(1234)</span></a></li>
    <li><a class="ais-RefinementList-link" href="{_FILTER_HD}">HD Porn (1080p) <span>(5678)</span></a></li>
    <li><a class="ais-RefinementList-link" href="/en/videos/?sortBy=most_viewed">Most viewed</a></li>
    <li><a class="ais-SortBy-link" href="/en/videos/?sortBy=quality_desc">Best quality (1080p) first</a></li>
  </ul>
</div>
"""
_SCENE_URL = "https://members.dfxtra.com/en/video/dfxtrapartners/Rebel-Lyn"
_DOWNLOAD_DIV = '<div class="download"><span class="text">Download</span></div>'

_SCENE = f"""<!doctype html><html><body>
<main class="scene">
  <h1>Rebel Lynn's First Gloryhole</h1>
  <div class="actions">{_DOWNLOAD_DIV}<div class="favorite">Favorite</div></div>
  {_SIDEBAR}
</main></body></html>"""

# Negative control: a real signed download link beside the same sidebar is
# a candidate and wins outright; admission must not touch it.
_REAL_DL = "https://cdn.example/dl/scene_2160p.mp4?token=abc"
_SCENE_WITH_REAL_LINK = _SCENE.replace(
    _DOWNLOAD_DIV,
    f'<a class="dl" href="{_REAL_DL}">Download 2160p</a>')
# Lens flip (correctness, 759d gen 1): a real media/download href whose QUERY
# happens to carry a filter token is still a download -- the strong-signal
# fail-open is the row-399 lesson and must be load-bearing, not decorative.
_REAL_DL_WITH_FILTER_QUERY = (
    "https://cdn.example/dl/scene_2160p.mp4?sortBy=most_viewed"
    "&refinementList%5Bx%5D=1&token=abc")
_REAL_DOWNLOAD_PATH_WITH_FILTER_QUERY = (
    "/download/dfxtrapartners/Rebel-Lyn?filter=hd")  # same work as the page
_SCENE_WITH_FILTER_QUERIED_MEDIA = _SCENE.replace(
    _DOWNLOAD_DIV,
    f'<a class="dl" href="{_REAL_DL_WITH_FILTER_QUERY}">Download 2160p</a>')
_SCENE_WITH_FILTER_QUERIED_DOWNLOAD_PATH = _SCENE.replace(
    _DOWNLOAD_DIV,
    f'<a class="dl" href="{_REAL_DOWNLOAD_PATH_WITH_FILTER_QUERY}">'
    'Download 1080p</a>')


class _RecordingRunner:
    def __init__(self):
        self.events = []

    def log_event(self, kind, message, **kwargs):
        self.events.append({"kind": kind, "message": message,
                            "extra": kwargs.get("extra")})


@contextmanager
def _page(html):
    sync_playwright = pytest.importorskip(
        "playwright.sync_api").sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            # Served at the live scene URL (never fetched: the route answers)
            # so the relative filter hrefs resolve exactly as they did live.
            page.route("**/*", lambda route: route.fulfill(
                status=200, content_type="text/html", body=html))
            page.goto(_SCENE_URL, wait_until="load")
            yield page
        finally:
            browser.close()


def _summaries(runner):
    return [e for e in runner.events
            if e["kind"] == "candidate_admission_filtered"]


def _href(best):
    return (best["locator"].get_attribute("href") or "") if best else None


def test_precondition_the_filter_label_outscores_the_download_div():
    """The defect needs the ranker to read the label; prove it does."""
    assert res_score("4K (2160p) (1234)") > 0
    assert res_score("Download") <= 0


def test_listing_filter_hrefs_are_refused_and_the_download_div_wins():
    runner = _RecordingRunner()
    with _page(_SCENE) as page:
        best = find_best_download(page, runner=runner)
        assert best is not None, "no candidate at all: the Download div was lost"
        assert "refinementList" not in _href(best), (
            f"listing filter href won the ranking: {_href(best)}")
        assert "sortBy" not in _href(best)
        assert best["score"] == 0 and "Download" in best["text"], best["text"]
    summaries = _summaries(runner)
    assert len(summaries) == 1, runner.events
    extra = summaries[0]["extra"]
    # Three of four: the bare ``Most viewed`` sort link carries no resolution
    # or download word, so the (uncounted, row 499) no_signal refusal takes it
    # first. The ``(1080p)`` sort link IS ranker food and is counted -- that is
    # the sort/facet half of the query rule doing work.
    assert extra.get("listing_filter") == 3, extra
    assert extra.get("count") == 3, extra


def test_negative_control_a_real_download_link_beside_the_sidebar_wins():
    runner = _RecordingRunner()
    with _page(_SCENE_WITH_REAL_LINK) as page:
        best = find_best_download(page, runner=runner)
        assert best is not None
        assert _href(best) == _REAL_DL, _href(best)
        assert best["score"] > 0


@pytest.mark.parametrize("html, winner", [
    pytest.param(_SCENE_WITH_FILTER_QUERIED_MEDIA, _REAL_DL_WITH_FILTER_QUERY,
                 id="signed_mp4"),
    pytest.param(_SCENE_WITH_FILTER_QUERIED_DOWNLOAD_PATH,
                 _REAL_DOWNLOAD_PATH_WITH_FILTER_QUERY, id="download_path"),
])
def test_negative_control_a_filter_token_in_a_media_query_is_not_a_filter(
        html, winner):
    """M3 (lens): delete the strong-signal fail-open and a signed .mp4 (or a
    /download/ path) whose query carries sortBy/refinementList/filter is
    refused as a listing filter and the sidebar link wins instead."""
    runner = _RecordingRunner()
    with _page(html) as page:
        best = find_best_download(page, runner=runner)
        assert best is not None
        assert _href(best) == winner, _href(best)
        assert best["score"] > 0
    extra = _summaries(runner)[0]["extra"]
    # The three sidebar refusals are unchanged; the real link is not among them.
    assert extra.get("listing_filter") == 3, extra
