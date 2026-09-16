"""Independent row 761 acceptance and adversarial URL-component probes."""

BD_GATE_SCOPE = "repo-wide"

from contextlib import contextmanager
from html import escape

import pytest

from bulk_downloader.detect import _candidate_admission, find_best_download, res_score

PAGE = "https://members.gamma.example/en/scenes/example.html"
LABEL = "4K (2160p) (2327)"
FACET = "/en/videos/?refinementList[video_formats.format][0]=2160p"


class Element:
    def __init__(self, attrs):
        self.attrs = attrs

    def get_attribute(self, name):
        return self.attrs.get(name)


def admission(attrs, label="2160p", page=PAGE):
    text = " ".join([label, *attrs.values()])
    return _candidate_admission(Element(attrs), text, page, label=label)


LISTINGS = (
    FACET,
    "/fr/videos/?refinementList%5Bvideo_formats.format%5D%5B0%5D=2160p",
    "/en/videos/?refinementList%5bformat%5d=2160p",
    "/en/videos/?%72efinementList%5Bformat%5D=2160p",
    "/en/videos/?REFINEMENTLIST[format]=2160p",
    "/en/videos/?refinementList[format]=",
    "/en/videos/?q=full&refinementList[format]=2160p&refinementList[format]=1080p",
    "https://members.other.example/videos/?refinementList[format]=2160p",
)


@pytest.mark.parametrize("href", LISTINGS)
@pytest.mark.parametrize("attr", ("href", "data-url"))
def test_listing_key_refuses_quality_candidate(href, attr):
    assert res_score(LABEL) == 2160
    assert len(LISTINGS) == 8
    actual = admission({attr: href}, LABEL)
    assert actual == "non_video", (attr, href, actual)


MEDIA = (
    "https://cdn.example/scene_2160p.mp4",
    "https://cdn.example/scene_2160p.mp4?refinementList[format]=2160p",
    "https://cdn.example/scene_2160p.mp4?refinementList%5Bformat%5D=2160p",
    "https://cdn.example/scene_2160p.mp4?source=/refinementList%5Babc%5D",
    "https://cdn.example/scene_2160p.mp4?source=tr_123_sm.mp4",
    "https://cdn.example/scene_2160p.mp4?source=previewvideos/a",
    "https://cdn.example/scene_2160p.mp4#/refinementList[x]=1",
    "https://cdn.example/dl?file=scene.mp4&refinementList[format]=2160p",
    "/en/videos/?source=refinementList%5Bformat%5D&quality=2160p",
    "/en/videos/?notrefinementList[format]=2160p",
    "/en/videos/?refinementList%255Bformat%255D=2160p",
)


@pytest.mark.parametrize("href", MEDIA)
def test_media_and_nonfacet_components_are_admitted(href):
    assert len(MEDIA) == 11
    actual = admission({"href": href})
    assert actual is None, (href, actual)


@pytest.mark.parametrize("href", (
    "https://cdn.example/tr_123_sm.mp4",
    "https://cdn.example/previewvideos/scene_2160p.mp4",
    "/en/videos/refinementList%5Bx%5D/a.mp4",
))
def test_path_refusal_has_a_positive_control(href):
    actual = admission({"href": href})
    assert actual == "non_video", (href, actual)


@pytest.mark.parametrize("href", (
    "/en/videos?refinementList[format]=2160p",
    "?refinementList[format]=2160p",
))
def test_equivalent_listing_links_cannot_become_downloads(href):
    actual = admission({"href": href}, LABEL,
                       page="https://members.gamma.example/en/videos/")
    assert actual == "non_video", (href, actual)


@pytest.mark.parametrize("attr", ("data-signed-url-key", "data-download"))
def test_opaque_auxiliary_value_does_not_poison_real_media(attr):
    # These fields can hold opaque keys, while href supplies the real URL.
    attrs = {"href": "https://cdn.example/scene_2160p.mp4", attr: "//[opaque"}
    actual = admission(attrs)
    assert actual is None, (attrs, actual)


def test_bad_auxiliary_value_cannot_mask_a_real_facet():
    actual = admission({"href": FACET, "data-download": "//[opaque"}, LABEL)
    assert actual == "non_video", actual


@pytest.mark.parametrize("href, expected", (
    ("?refinementList[format]=2160p", "listing_filter"),
    ("https://cdn.example?refinementList[format]=2160p", None),
))
def test_query_without_a_listing_path_respects_origin_policy(href, expected):
    # Row759d rejects a same-origin listing-only query on the scene page.
    actual = admission({"href": href}, page=PAGE)
    assert actual == expected, (href, actual)


@contextmanager
def page_for(html):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: route.fulfill(
                status=200, content_type="text/html", body=html))
            page.goto(PAGE)
            yield page
        finally:
            browser.close()


def candidate_ids(best):
    assert best is not None, "the real download control disappeared"
    candidates = best.get("_all_candidates")
    assert candidates, best
    return [candidate["locator"].get_attribute("id") for candidate in candidates]


@pytest.mark.parametrize("learned", (None, {"row_selectors": [".choice"]}))
@pytest.mark.parametrize("template", ("gamma", "second"))
def test_listing_never_wins_over_exactly_one_hrefless_download(learned, template):
    control = (
        '<div id="real" class="choice ScenePlayerHeaderPlus-IconItem styles_download">'
        '<span class="Icon-Download"></span>Download</div>' if template == "gamma"
        else '<button id="real" class="choice" type="button">Download</button>'
    )
    html = control + f'<a id="facet" class="choice" href="{escape(FACET, quote=True)}">{LABEL}</a>'
    with page_for(html) as page:
        assert page.locator("#real").count() == 1
        assert page.locator("#real").get_attribute("href") is None
        assert page.locator("#facet").count() == 1
        assert page.locator(".choice").count() == 2
        best = find_best_download(page, learned=learned)
        assert candidate_ids(best) == ["real"], best
        assert best["locator"].get_attribute("id") == "real"


def test_opaque_key_cannot_remove_the_only_real_dom_download():
    html = (
        '<a id="real" href="https://cdn.example/scene_2160p.mp4" '
        'data-signed-url-key="//[opaque">2160p Download</a>'
    )
    with page_for(html) as page:
        assert page.locator("a[href]").count() == 1
        assert candidate_ids(find_best_download(page)) == ["real"]


def test_fixture_negative_control_contains_exactly_one_candidate():
    with page_for('<button id="real" type="button">Download</button>') as page:
        assert page.locator("button").count() == 1
        assert candidate_ids(find_best_download(page)) == ["real"]
