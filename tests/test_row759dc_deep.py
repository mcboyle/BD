"""Deep-lane adversary probes for row759dc listing-filter admission."""

BD_GATE_SCOPE = "module"

import pytest

from bulk_downloader.detect import _candidate_admission, _is_listing_filter_href


_PAGE_URL = "https://members.dfxtra.com/en/video/example"


class _HrefElement:
    """Minimal element boundary: the helper only reads ``href``."""

    def __init__(self, href):
        self._href = href

    def get_attribute(self, name):
        return self._href if name == "href" else None


def _listing(href):
    return _is_listing_filter_href(
        _HrefElement(href), "4K (2160p)", page_url=_PAGE_URL)


@pytest.mark.parametrize("href", [
    "/en/videos/?filter=trailer.m3u8",
    "/en/videos/?filter=/download/trailer",
])
def test_lens_query_value_spoofs_are_refused_as_listing_filters(href):
    """REFUTE xh1: media-shaped FILTER VALUES still reject the listing."""
    assert _listing(href) is True


def test_lens_encoded_separator_in_filter_value_is_refused_as_listing_filter():
    """REFUTE r2: an encoded separator inside a FILTER VALUE is not a key."""
    assert _listing("/en/videos/?filter=plain%26format%3Dm3u8") is True


def test_lens_admission_returns_listing_filter_for_a_real_filter_query():
    """REFUTE correctness: admission returns the counted listing refusal."""
    assert _candidate_admission(
        _HrefElement("/en/videos/?filter=hd"), "4K (2160p)", _PAGE_URL
    ) == "listing_filter"


@pytest.mark.parametrize("href", [
    "/en/videos/?filter%5Bquality%5D=2160p",
    "/en/videos/?filters%5Bquality%5D=2160p",
    "/en/videos/?facets%5Bquality%5D=2160p",
])
def test_bracketed_filter_query_keys_are_refused_as_listing_filters(href):
    """Nested filter/facet keys are listing controls, not quality downloads."""
    assert _listing(href) is True


@pytest.mark.parametrize("href", [
    "/en/videos/?filter",
    "/en/videos/?sortBy",
])
def test_bare_filter_query_keys_are_refused_as_listing_filters(href):
    """A present-but-empty listing control is still a listing navigation URL."""
    assert _listing(href) is True


@pytest.mark.parametrize("href", [
    "/en/videos/?q=plain%26filter%3Dhd",
    "/en/videos/?q=plain%26sortBy%3Dquality_desc",
])
def test_encoded_listing_text_inside_a_search_value_is_not_a_listing_key(href):
    """Only query KEYS, not decoded search VALUES, trigger a listing refusal."""
    assert _listing(href) is False


def test_double_encoded_refinement_key_is_not_decoded_twice():
    """A double-encoded key remains literal query text, not a filter control."""
    assert _listing(
        "/en/videos/?refinementList%255Bvideo_quality%255D%255B0%255D=2160p"
    ) is False


@pytest.mark.parametrize("href, expected", [
    ("/en/videos/filter=hd", False),
    ("\n/en/videos/?filter=hd\n", True),
    ("/en/videos/?filter=%E9%AB%98%E6%B8%85", True),
    ("/en/videos/?f%C3%ADlter=hd", False),
    ("/en/videos/?filter=hd&format=m3u8", False),
])
def test_path_layout_unicode_and_separate_media_key_keep_their_expected_behavior(
        href, expected):
    """Path text, layout, Unicode, and real media keys retain their meaning."""
    assert _listing(href) is expected
