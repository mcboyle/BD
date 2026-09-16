"""Row 722s live (dorcelclub, 2026-09-15 14:34Z): the download pop-in offers
href-less quality options -- `<div class="filter" data-quality="1080"
data-slug="https://www.dorcelclub.com/dl/scene/.../full/1080.mp4?lang=en">`.
The ranker picked "MP4 - Full HD 1080p (921.4 MB)" from the label, clicked it
(the site's own Download button consumes the pick, so nothing fires) and the
job ended "Clicked but no download started -- scored ok but no download
fired" while the file's URL sat on the element in `data-slug`.

Contract: the winner's URL is read from `href` first, else from ANY attribute
whose value IS the file (or a manifest) -- the attribute's NAME is the
site's, the value's shape is ours to recognise. Wired into _do_download at the
row-384 direct-route seam so the option is fetched, not clicked.
"""
from __future__ import annotations

import inspect

import pytest

from bulk_downloader.runner_transport import TransportMixin as T

BD_GATE_SCOPE = "module"

PAGE = "https://www.dorcelclub.com/en/scene/892210/one-bed-for-four"
SLUG = "https://www.dorcelclub.com/dl/scene/892210/one-bed-for-four/full/1080.mp4?lang=en"
DORCEL_OPTION = [("class", "filter"), ("data-slug", SLUG), ("data-lang", "en"),
                 ("data-quality", "1080"), ("style", "display: block;")]


def test_the_measured_dorcelclub_option_yields_its_data_slug_file_url():
    got = T._winner_url_value(DORCEL_OPTION, PAGE)
    assert got == SLUG, (
        f"the href-less option's file URL was not read from its attributes "
        f"(got {got!r}): the job clicks it and reports 'no download fired'")
    durl, dname = T._direct_media_route(got, PAGE)
    assert durl == SLUG and dname.endswith(".mp4"), (durl, dname)


def test_href_wins_over_any_data_attribute():
    attrs = [("data-slug", "https://cdn.test/other/2160.mp4"), ("href", "https://cdn.test/v/1080.mp4")]
    assert T._winner_url_value(attrs, PAGE) == "https://cdn.test/v/1080.mp4"


def test_a_manifest_in_a_data_attribute_is_returned_for_the_stream_route():
    attrs = [("class", "q"), ("data-src", "https://cdn.test/v/master.m3u8")]
    assert T._winner_url_value(attrs, PAGE) == "https://cdn.test/v/master.m3u8"


@pytest.mark.parametrize("attrs", [
    [("class", "filter"), ("data-quality", "1080"), ("data-slug", "javascript:void(0)")],
    [("class", "filter"), ("data-quality", "1080")],
    [("data-title", "Full HD 1080p"), ("data-size", "921.4 MB")],
    [("href", "")],
    [],
])
def test_negative_control_no_url_shaped_value_means_no_url(attrs):
    assert T._winner_url_value(attrs, PAGE) == ""


def test_a_bare_href_fragment_is_still_the_href_for_the_click_grant_check():
    # `#download` is not a file, but it is still what the click-only-grant
    # check must see (row 384 behaviour unchanged for anchors).
    assert T._winner_url_value([("href", "#download")], PAGE) == "#download"


def test_do_download_reads_the_winner_through_the_attribute_map():
    src = inspect.getsource(T._do_download)
    assert "_winner_url_value(" in src, (
        "_do_download still reads only get_attribute('href'): an href-less "
        "option whose file URL lives in another attribute is clicked, never fetched")
    i_attr = src.index("_winner_url_value(")
    i_stream = src.index("_stream_route(_href")
    assert i_attr < i_stream
