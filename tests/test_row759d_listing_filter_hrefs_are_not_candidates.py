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


# A2-A REFUTE (gen 2, correctness): three fresh escapes reproduced directly
# against _is_listing_filter_href, unit-level (no browser needed).
def test_escape1_cdn_signed_download_with_stray_filter_param_is_not_listing():
    """xh1: a cross-origin signed CDN href is never a listing page; an
    incidental &filter=hd must not misclassify it."""
    from bulk_downloader.detect import _is_listing_filter_href

    class _El:
        def get_attribute(self, name):
            if name == "href":
                return "https://cdn.example/asset/9a7c?token=abc&filter=hd"
            return None
    assert _is_listing_filter_href(
        _El(), "Download 2160p",
        page_url="https://members.dfxtra.com/en/video/x") is False


def test_escape2_media_extension_in_query_value_does_not_spoof_signal():
    """med1a: MEDIA_EXT_RE must be checked against the URL PATH, not the
    whole href -- a filter query VALUE of trailer.mp4 is not a media path."""
    from bulk_downloader.detect import _is_listing_filter_href

    class _El:
        def get_attribute(self, name):
            if name == "href":
                return "/en/videos/?filter=trailer.mp4"
            return None
    assert _is_listing_filter_href(
        _El(), "4K (2160p)",
        page_url="https://members.dfxtra.com/en/video/x") is True


def test_escape3_percent_encoded_filter_key_is_still_a_listing_filter():
    """med1b: the query must be percent-decoded before the key regex match,
    or %66ilter= silently bypasses the listing-filter rule."""
    from bulk_downloader.detect import _is_listing_filter_href

    class _El:
        def get_attribute(self, name):
            if name == "href":
                return "/en/videos/?%66ilter=hd"
            return None
    assert _is_listing_filter_href(
        _El(), "HD",
        page_url="https://members.dfxtra.com/en/video/x") is True


def test_escape_xh1_manifest_value_in_filter_query_does_not_spoof_signal():
    """xh1: a filter query VALUE of trailer.m3u8 must not spoof manifest_url
    via candidate_filter.positive_signals -- the value is not a path."""
    from bulk_downloader.detect import _is_listing_filter_href

    class _El:
        def get_attribute(self, name):
            if name == "href":
                return "/en/videos/?filter=trailer.m3u8"
            return None
    assert _is_listing_filter_href(
        _El(), "4K (2160p)",
        page_url="https://members.dfxtra.com/en/video/x") is True


def test_escape_xh1_download_path_value_in_filter_query_does_not_spoof_signal():
    """xh1: a filter query VALUE of /download/trailer must not spoof
    download_path -- the value is not the href's own path."""
    from bulk_downloader.detect import _is_listing_filter_href

    class _El:
        def get_attribute(self, name):
            if name == "href":
                return "/en/videos/?filter=/download/trailer"
            return None
    assert _is_listing_filter_href(
        _El(), "4K (2160p)",
        page_url="https://members.dfxtra.com/en/video/x") is True


def test_bounce_r2_encoded_ampersand_in_filter_value_is_still_a_listing_filter():
    """r2 (PM BOUNCE landing/BOUNCE-759dc-r2-decode-order-A6A.md): an encoded
    &format=m3u8 fully INSIDE one filter value must not decode into a
    fabricated separate key. Decoding the whole query before stripping (the
    first xh1 fix) let ?filter=plain%26format%3Dm3u8 become
    'filter=plain&format=m3u8', stripping only 'filter=plain' and leaving a
    spoofed 'format=m3u8' behind."""
    from bulk_downloader.detect import _is_listing_filter_href

    class _El:
        def get_attribute(self, name):
            if name == "href":
                return "/en/videos/?filter=plain%26format%3Dm3u8"
            return None
    assert _is_listing_filter_href(
        _El(), "4K (2160p)",
        page_url="https://members.dfxtra.com/en/video/x") is True


def test_bounce_r2_control_genuine_separate_format_key_still_honored():
    """r2 control: a genuine, separate format=m3u8 KEY (not encoded inside
    the filter value) is still a real manifest signal -- the strong-signal
    fail-open must stay load-bearing (row-399 lesson), not merely decorative."""
    from bulk_downloader.detect import _is_listing_filter_href

    class _El:
        def get_attribute(self, name):
            if name == "href":
                return "/en/videos/?filter=hd&format=m3u8"
            return None
    assert _is_listing_filter_href(
        _El(), "4K (2160p)",
        page_url="https://members.dfxtra.com/en/video/x") is False


def test_right_plain_filter_query_with_no_media_signal_still_rejected():
    """RIGHT (A2-A baseline): unaffected by the fix."""
    from bulk_downloader.detect import _is_listing_filter_href

    class _El:
        def get_attribute(self, name):
            if name == "href":
                return "/en/videos/?filter=hd"
            return None
    assert _is_listing_filter_href(_El(), "HD Porn (1080p)") is True


def test_refute_r3_encoded_separator_in_a_non_filter_value_is_not_a_key():
    """REFUTE r3 (bd-review-correctness-A3-A, VERDICT-correctness.md 19:24Z):
    a genuine filter=hd href beside an UNRELATED q=... param whose VALUE
    happens to contain an encoded &/= must still be refused as a listing
    filter. Unquoting the retained (non-filter) remainder as a WHOLE let
    q=plain%26format%3Dm3u8 decode into a fabricated 'format=m3u8' pair,
    spoofing the literal 'format=m3u8' manifest-signal pattern even though
    no such key was ever in the raw query."""
    from bulk_downloader.detect import _is_listing_filter_href

    class _El:
        def get_attribute(self, name):
            if name == "href":
                return "/en/videos/?filter=hd&q=plain%26format%3Dm3u8"
            return None
    assert _is_listing_filter_href(
        _El(), "4K (2160p)",
        page_url="https://members.dfxtra.com/en/video/x") is True


def test_refute_r3_control_plain_q_value_beside_filter_still_rejected():
    """r3 control: with no encoded separator at all in the other key's
    value, the href is still correctly refused as a listing filter."""
    from bulk_downloader.detect import _is_listing_filter_href

    class _El:
        def get_attribute(self, name):
            if name == "href":
                return "/en/videos/?filter=hd&q=plain"
            return None
    assert _is_listing_filter_href(
        _El(), "4K (2160p)",
        page_url="https://members.dfxtra.com/en/video/x") is True


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


def test_listing_filter_hrefs_are_refused_via_the_learned_admission_path():
    """The taught-row admission call site (find_best_download's learned
    row_selectors branch) must refuse a listing-filter href exactly like the
    wide-sweep path does -- both call _candidate_admission with the same
    page_url-aware _is_listing_filter_href."""
    runner = _RecordingRunner()
    with _page(_SCENE_WITH_REAL_LINK) as page:
        best = find_best_download(
            page, learned={"row_selectors": ["a"]}, runner=runner)
        assert best is not None
        assert _href(best) == _REAL_DL, _href(best)
    extra = _summaries(runner)[0]["extra"]
    assert extra.get("listing_filter", 0) >= 1, extra


@pytest.mark.parametrize("filter_href", [
    pytest.param("/en/videos/?filter=hd", id="control_hd"),
    pytest.param("/en/videos/?filter=trailer.mp4", id="control_media_extension"),
    pytest.param("/en/videos/?filter=trailer.m3u8", id="xh1_manifest_value_spoof"),
    pytest.param("/en/videos/?filter=/download/trailer",
                 id="xh1_download_path_value_spoof"),
])
def test_xh1_filter_query_value_spoofing_the_download_div_still_wins(
        filter_href):
    """PM BOUNCE (landing/BOUNCE-759dc-query-spoof-A6A.md, lens rs-xh1): the
    routed Chromium _page fixture, _FILTER_4K swapped for each repro/control
    href (label unchanged -- only the href varies). In every case the
    score-0 Download div must win -- the filter link (whatever signal its
    query VALUE spoofs) must never outrank it."""
    scene = _SCENE.replace(_FILTER_4K, filter_href)
    runner = _RecordingRunner()
    with _page(scene) as page:
        best = find_best_download(page, runner=runner)
        assert best is not None, "no candidate at all: the Download div was lost"
        assert best["score"] == 0 and "Download" in best["text"], (
            f"the filter link won at score={best['score']}: {_href(best)}")


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
