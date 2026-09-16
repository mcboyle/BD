"""Row 761: an Algolia quality facet is navigation, never a download tier."""

BD_GATE_SCOPE = "repo-wide"

from contextlib import contextmanager

import pytest

from bulk_downloader.detect import (
    _candidate_admission,
    _has_non_video_url_shape,
    find_best_download,
    res_score,
)

PAGE = "https://members.gammaentertainment.example/en/scenes/a.html"
FACET_HREF = "/en/videos/?refinementList[video_formats.format][0]=2160p"
ENCODED_FACET_HREF = "/en/videos/?refinementList%5Bvideo_formats.format%5D%5B0%5D=2160p"
MEDIA_VALUE_HREF = (
    "https://cdn.gammaentertainment.example/movie.mp4?"
    "source=refinementList%5Bvideo_formats.format%5D%5B0%5D&quality=2160p"
)
MEDIA_SLASH_VALUE_HREF = (
    "https://cdn.gammaentertainment.example/scene_2160p.mp4?"
    "source=/refinementList%5Babc%5D"
)
FACET_LABEL = "4K (2160p) (2327)"
FIXTURE = (
    '<div id="download-control" class="ScenePlayerHeaderPlus-IconItem styles_download">'
    '<span class="Icon-Download"></span>Download</div>'
    f'<a class="facet" href="{FACET_HREF}">{FACET_LABEL}</a>')
CONTROL_FIXTURE = (
    '<div id="download-control" class="ScenePlayerHeaderPlus-IconItem styles_download">'
    '<span class="Icon-Download"></span>Download</div>')


@contextmanager
def page_for(html):
    """A real DOM fixture, served locally through Playwright routing."""
    sync = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: route.fulfill(
                status=200, content_type="text/html", body=html))
            page.goto(PAGE)
            yield page
        finally:
            browser.close()


class Facet:
    def __init__(self, href=FACET_HREF):
        self.href = href

    def get_attribute(self, name):
        return self.href if name == "href" else None


def test_fixture_builds_the_listing_facet_and_hrefless_download_control():
    """The fixture shape must exist before its refusal is asserted."""
    with page_for(FIXTURE) as page:
        assert page.locator("a.facet").count() == 1
        assert page.locator("#download-control").count() == 1


def test_listing_facet_is_refused_before_its_2160_label_can_win():
    """Deleting the listing URL rule makes this exact assertion fail."""
    assert res_score(FACET_LABEL) == 2160
    assert _has_non_video_url_shape(Facet())
    admission = _candidate_admission(
        Facet(), f"{FACET_LABEL} {FACET_HREF}", PAGE, label=FACET_LABEL)
    assert admission == "non_video", admission


def test_percent_encoded_listing_facet_is_refused_before_its_2160_label_can_win():
    """The encoded query-key spelling is the same navigation product."""
    assert res_score(FACET_LABEL) == 2160
    assert _has_non_video_url_shape(Facet(ENCODED_FACET_HREF))
    admission = _candidate_admission(
        Facet(ENCODED_FACET_HREF),
        f"{FACET_LABEL} {ENCODED_FACET_HREF}",
        PAGE,
        label=FACET_LABEL,
    )
    assert admission == "non_video", admission


@pytest.mark.parametrize("href", [MEDIA_VALUE_HREF, MEDIA_SLASH_VALUE_HREF])
def test_media_query_value_named_refinement_list_is_still_admitted(href):
    """Only a listing-filter key/path is navigation; a value is media metadata."""
    admission = _candidate_admission(
        Facet(href),
        f"2160p {href}",
        PAGE,
        label="2160p",
    )
    assert admission is None, admission


def test_listing_facet_never_reaches_the_best_download_candidate():
    with page_for(FIXTURE) as page:
        best = find_best_download(page)
        assert best is not None
        candidates = best.get("_all_candidates") or []
        assert all(FACET_LABEL not in item["text"] for item in candidates), candidates
        assert "Download" in best.get("text", ""), best


def test_negative_control_has_exactly_one_real_download_candidate():
    with page_for(CONTROL_FIXTURE) as page:
        best = find_best_download(page)
        assert best is not None
        candidates = best.get("_all_candidates") or []
        assert len(candidates) == 1, candidates
        assert "Download" in best.get("text", ""), best
