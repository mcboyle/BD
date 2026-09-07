"""Row 704: open-class listing routes must not be treated as scene links."""

BD_GATE_SCOPE = "repo-wide"

import pytest
from urllib.parse import urlparse

from bulk_downloader.playlist_extractor import _looks_like_scene_url


_LISTINGS = (
    "https://www.evilangel.com/en/videos/sort/latest",
    "https://www.evilangel.com/en/videos/sort/latest/page/2",
    "https://www.evilangel.com/en/videos/page/3",
    "https://www.evilangel.com/en/videos/page/337",
    "https://www.dogfartnetwork.com/en/videos/sites/blackmeatwhitefeet",
    "https://www.nubilefilms.com/video/gallery",
    "https://www.nubilefilms.com/video/gallery/website/71",
    "https://www.nubilefilms.com/video/shorts",
    "https://x.example/clips/sort/latest",
    "https://x.example/scenes/sort/latest",
    "https://x.example/movies/page/3",
    "https://x.example/videos/page/2/foo",
)

_SCENES = (
    "https://www.dogfartnetwork.com/en/video/blacksonblondes/In-Awe-Of-Its-Size/291195",
    "https://www.nubilefilms.com/video/watch/255524/some-slug",
    "https://www.evilangel.com/en/video/some-studio-name/slug/4",
    "https://x.example/videos/some-title/12345",
)


def test_row704_listing_words_in_the_second_route_segment_refuse_at_any_depth():
    assert len(_LISTINGS) == 12
    assert all(len([part for part in urlparse(url).path.split("/") if part]) >= 2
               for url in _LISTINGS), "fixture must contain real routes"
    assert not any(_looks_like_scene_url(url) for url in _LISTINGS)


def test_row704_existing_scene_shapes_and_template_short_circuit_stay_accepted():
    assert len(_SCENES) == 4
    assert all(_looks_like_scene_url(url) for url in _SCENES)
    template = {"url_patterns": [r"/only-this-scene/"]}
    assert _looks_like_scene_url("https://x.example/only-this-scene/42", template=template)
    assert not _looks_like_scene_url(_SCENES[0], template=template)


def test_row704_the_listing_rule_is_data_a_template_can_extend():
    """Row 704's acceptance verbatim: the rule is expressed as DATA the site
    templates can extend rather than a hard-coded pair, since /sort/ and
    /page/ are two members of an open class.  This node reads the data and
    never drives the classifier, so it is also the transform control's band."""
    from bulk_downloader.playlist_extractor import _LISTING_ROUTE_WORDS
    assert isinstance(_LISTING_ROUTE_WORDS, tuple)
    assert {"sort", "page"} <= set(_LISTING_ROUTE_WORDS)
    assert len(set(_LISTING_ROUTE_WORDS)) == len(_LISTING_ROUTE_WORDS)


def test_row704_a_template_can_extend_the_listing_words_with_its_own_action():
    """B1: the listing rule is DATA the site templates extend, not a closed
    list.  A site-specific route action the defaults have never heard of must
    refuse once the template declares it -- and the SAME url must still be
    accepted without the template, or this proves nothing about the
    extension."""
    from bulk_downloader.playlist_extractor import (_LISTING_KEYWORDS,
                                                    _LISTING_ROUTE_WORDS,
                                                    _NON_SCENE_HINTS)
    url = "https://x.example/videos/tour/3"
    # Precondition: "tour" is in NO default vocabulary, so only the template
    # can be what refuses it.
    assert "tour" not in _LISTING_ROUTE_WORDS
    assert not any("tour" in kw for kw in _LISTING_KEYWORDS + _NON_SCENE_HINTS)
    assert _looks_like_scene_url(url), (
        "precondition lost: the defaults already refuse this route, so a "
        "template extension cannot be told apart from them")
    assert not _looks_like_scene_url(url, template={
        "listing_route_words": ["tour"]}), (
        "a template-declared listing action did not reach the decision: the "
        "rule is still a closed list")
    # The declared words are normalised, not matched raw.
    assert not _looks_like_scene_url(url, template={
        "listing_route_words": ["/Tour/"]})
    # A template that declares none of them changes nothing.
    assert _looks_like_scene_url(url, template={"listing_route_words": []})
    assert _looks_like_scene_url(url, template={})
    # And the defaults keep refusing whatever the template says: extension is
    # a UNION, never a replacement.
    assert not _looks_like_scene_url("https://x.example/videos/page/3",
                                     template={"listing_route_words": ["tour"]})
