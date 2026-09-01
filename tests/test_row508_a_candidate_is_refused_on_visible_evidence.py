"""Row 508 -- a candidate is refused as non-video only on visible evidence.

``NON_VIDEO_RE`` is two different predicates joined into one alternation: 14
VISIBLE-WORD alternatives, and 6 URL-SHAPE alternatives added in v3.43.65 for
specific preview file-naming conventions.  A regex word boundary is satisfied
by ``/``, ``?``, ``=``, ``-`` and ``.``, so the visible-word half fires on URL
PUNCTUATION.  Since the learned loop harvests ``href`` into the same string it
hands the predicate, a taught anchor whose rendered label is a clean ``1080p``
is refused because its URL happens to contain ``poster=``, ``/preview/`` or a
``gallery-`` host -- bytes the operator never saw.

There is no fall-through escape: the wide sweep scans ``href`` too and applies
the same predicate, so both populations refuse the same element.  ``runner``
then credits ``download_misses`` plus per-selector misses against the CORRECT
taught selector and ``_maybe_demote_selectors`` DROPS it at 6 misses with 0
hits, so teaching is deleted over bytes the operator never saw.

CONTRACT: the visible-word half is decided from rendered text and explicit
labels; the URL-shape half keeps reading URLs.  The shared regex in
``constants.py`` is NOT changed -- it is partitioned at its application site.
"""

BD_GATE_SCOPE = "module"

from contextlib import contextmanager

import pytest

from bulk_downloader.constants import NON_VIDEO_RE
from bulk_downloader.detect import find_best_download, res_score

_TAUGHT = "a.tier"

# One clean visible label; the only difference between arms is the href.
_ARMS = {
    "query-poster": "https://cdn.example/dl/1080p.mp4?poster=1",
    "path-preview": "https://cdn.example/preview/1080p.mp4",
    "host-gallery": "https://gallery-assets.example.com/dl/1080p.mp4",
}
_CLEAN = "https://cdn.example/dl/1080p.mp4"

# URL-shape tokens: refused on the URL even after the word half stops reading
# it, because that half of the predicate is about file naming, not vocabulary.
_URL_SHAPE = {
    "gamma-trailer": "https://cdn.example/tr_127673_sm.mp4",
    "vixen-preview-path": "https://cdn.example/previewvideos/106488/1080p.mp4",
}


def _one_anchor(href):
    return (f'<!doctype html><html><body><main>'
            f'<a class="tier" href="{href}">1080p</a>'
            f'</main></body></html>')


def _all_four():
    anchors = "".join(
        f'<a class="tier" href="{href}">1080p</a>'
        for href in list(_ARMS.values()) + [_CLEAN])
    return f'<!doctype html><html><body><main>{anchors}</main></body></html>'


_VISIBLE_WORD_LABELS = {
    "photo-gallery": '<a class="tier" href="https://cdn.example/dl/a.mp4">'
                     "Photo gallery 1080p</a>",
    "trailer": '<a class="tier" href="https://cdn.example/dl/b.mp4">'
               "Trailer 1080p</a>",
}


@contextmanager
def _page(html):
    sync_playwright = pytest.importorskip(
        "playwright.sync_api").sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_content(html, wait_until="load")
            yield page
        finally:
            browser.close()


def test_precondition_the_partition_accounts_for_the_whole_shared_pattern():
    """The split is derived from the shipped object, with a real denominator."""
    from bulk_downloader.detect import _non_video_partition
    word_half, shape_half = _non_video_partition(NON_VIDEO_RE.pattern)
    assert word_half is not None and shape_half, (word_half, shape_half)
    assert len(word_half) == 1, word_half
    assert len(shape_half) == 6, shape_half
    assert word_half[0].startswith(r"\b") and word_half[0].endswith(r"\b")
    assert all(not alt.startswith(r"\b") for alt in shape_half), shape_half


def test_precondition_the_label_is_clean_and_the_url_is_not():
    from bulk_downloader.detect import _NON_VIDEO_URL_SHAPE_RE
    for name, href in _ARMS.items():
        assert not NON_VIDEO_RE.search("1080p"), name
        assert len(NON_VIDEO_RE.findall(href)) == 1, (name, href)
        assert not _NON_VIDEO_URL_SHAPE_RE.search(href), (name, href)
    assert not NON_VIDEO_RE.search(_CLEAN)
    for name, href in _ARMS.items():
        with _page(_one_anchor(href)) as page:
            anchor = page.locator(_TAUGHT)
            assert anchor.count() == 1, name
            assert anchor.first.is_visible(), name
            assert anchor.first.inner_text().strip() == "1080p", name


@pytest.mark.parametrize("name", sorted(_ARMS))
def test_a_word_found_only_in_the_url_does_not_refuse_the_candidate(name):
    """RED on the defective parent: no learned hit and no candidate at all."""
    with _page(_one_anchor(_ARMS[name])) as page:
        best = find_best_download(page, learned={"row_selectors": [_TAUGHT]})
        assert best is not None, (
            f"{name}: the taught anchor was refused on bytes the operator "
            "never saw")
        assert best.get("_via_learned") is True, name
        assert best.get("_learned_sel") == _TAUGHT, name
        assert best["locator"].get_attribute("href") == _ARMS[name], name


def test_the_token_free_control_was_always_admitted():
    """The seam is the URL token, not the fixture shape."""
    with _page(_one_anchor(_CLEAN)) as page:
        best = find_best_download(page, learned={"row_selectors": [_TAUGHT]})
        assert best is not None
        assert best.get("_via_learned") is True
        assert best["locator"].get_attribute("href") == _CLEAN


def test_all_four_taught_anchors_are_scored_together():
    with _page(_all_four()) as page:
        assert page.locator(_TAUGHT).count() == 4
        best = find_best_download(page, learned={"row_selectors": [_TAUGHT]})
        assert best is not None
        assert best.get("_via_learned") is True
        assert best.get("_learned_sel") == _TAUGHT
        assert len(best.get("_all_candidates") or []) == 4, (
            best.get("_all_candidates"))


@pytest.mark.parametrize("name", sorted(_VISIBLE_WORD_LABELS))
@pytest.mark.parametrize("learned", [None, {"row_selectors": [_TAUGHT]}],
                         ids=["wide", "learned"])
def test_negative_control_a_visible_word_still_refuses_on_both_paths(
        name, learned):
    """The word half is narrowed to visible evidence, not removed."""
    html = ('<!doctype html><html><body><main>'
            f'{_VISIBLE_WORD_LABELS[name]}</main></body></html>')
    with _page(html) as page:
        anchor = page.locator(_TAUGHT)
        assert anchor.count() == 1
        assert NON_VIDEO_RE.search(anchor.first.inner_text())
        assert find_best_download(page, learned=learned) is None, name


@pytest.mark.parametrize("name", sorted(_URL_SHAPE))
@pytest.mark.parametrize("learned", [None, {"row_selectors": [_TAUGHT]}],
                         ids=["wide", "learned"])
def test_negative_control_a_url_shape_token_still_refuses(name, learned):
    """The shape half still reads hrefs after the word half stops."""
    from bulk_downloader.detect import _NON_VIDEO_URL_SHAPE_RE
    href = _URL_SHAPE[name]
    assert _NON_VIDEO_URL_SHAPE_RE.search(href), (name, href)
    with _page(_one_anchor(href)) as page:
        anchor = page.locator(_TAUGHT)
        assert anchor.count() == 1
        assert not NON_VIDEO_RE.search(anchor.first.inner_text())
        assert find_best_download(page, learned=learned) is None, name


def test_negative_control_url_derived_tier_scoring_is_untouched():
    """The fix narrowed the refusal, not the ranking."""
    assert res_score("https://cdn.example/mp4_2160/clip.mp4") == 2160


def test_negative_control_the_shared_pattern_object_is_unchanged():
    """constants.NON_VIDEO_RE keeps every alternative it shipped with."""
    for probe in ("trailers-fame.gammacdn.com/.../tr_127673_sm.mp4",
                  "mini-bite_S.mp4", "/brt/tour/pics/.../bio_small.mp4",
                  "VIXEN_106491_353P.mp4", "ofmsagewill_desktop10sec.mp4",
                  "cdn.blacked.com/previewvideos/106488/..."):
        assert NON_VIDEO_RE.search(probe), probe
    for word in ("zip", "photos", "gallery", "trailer", "teaser", "preview",
                 "sample", "screenshot", "thumbnail", "cover", "poster"):
        assert NON_VIDEO_RE.search(f"a {word} b"), word


def test_the_label_url_split_is_mechanically_complete():
    """The split must not go stale when the harvest grows.

    An attribute added to the harvest and forgotten here would silently make
    its URL "visible evidence" again -- the exact defect. The check is
    mechanical rather than a retyped list: every harvested name that NAMES a
    URL must be on the URL side, and the URL side must be a real subset.
    """
    from bulk_downloader.detect import (
        _WIDE_SCAN_ATTRS, _WIDE_SCAN_URL_ATTRS)
    assert len(_WIDE_SCAN_ATTRS) == len(set(_WIDE_SCAN_ATTRS)) > 0
    assert _WIDE_SCAN_URL_ATTRS, "the URL side is empty"
    assert _WIDE_SCAN_URL_ATTRS.issubset(set(_WIDE_SCAN_ATTRS)), (
        _WIDE_SCAN_URL_ATTRS - set(_WIDE_SCAN_ATTRS))
    url_shaped = {
        name for name in _WIDE_SCAN_ATTRS
        if any(token in name for token in ("href", "url", "src", "download"))
    }
    assert url_shaped, "the URL-name heuristic matched nothing"
    assert url_shaped == set(_WIDE_SCAN_URL_ATTRS), (
        "harvested URL attributes missing from the URL side: "
        f"{sorted(url_shaped - set(_WIDE_SCAN_URL_ATTRS))}; "
        "non-URL names claimed as URLs: "
        f"{sorted(set(_WIDE_SCAN_URL_ATTRS) - url_shaped)}")
    # The learned path harvests its own URL set; it must agree.
    from bulk_downloader.detect import _CANDIDATE_URL_ATTRS
    assert set(_CANDIDATE_URL_ATTRS).issubset(_WIDE_SCAN_URL_ATTRS), (
        sorted(set(_CANDIDATE_URL_ATTRS) - _WIDE_SCAN_URL_ATTRS))
