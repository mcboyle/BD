"""P5-3 tests — DOM-visibility honeypot filter (v3.66.28).

Acceptance criteria from handoff/OPEN_THREADS.md §P5-3 C4:

  - >=4 cases per visibility signal (inline style, hidden attr,
    aria-hidden, CSS-class hidden)
  - >=3 cases for tabindex=-1 semantics (alone: NOT honeypot for
    a link; combined with another signal: IS honeypot)
  - >=4 cases for TRAP_URL_TERMS (positive matches + non-firing
    on legit URLs)
  - >=4 cases for find_duplicate_field_decoys (real duplicate,
    real non-duplicate, all-hidden duplicate, all-visible duplicate)
  - Integration tests for find_best_download with a mock Playwright
    page; verify off / cheap / strict modes.

The module is read at call time — tests use ``monkeypatch.setenv``
to flip ``BD_DOM_HONEYPOT_FILTER``.

Polluter-isolation note: this test file does NOT mutate
``sys.modules``. We just import dom_honeypot and detect; both are
pure modules with no module-load side effects. No conftest hook
or saved_modules snapshot is needed.
"""

import os
import sys

import pytest
from bs4 import BeautifulSoup

from bulk_downloader.dom_honeypot import (
    DECOY_LINK_HIDDEN_PATTERNS,
    TRAP_TEXT_TERMS,
    TRAP_URL_TERMS,
    find_duplicate_field_decoys,
    is_link_decoy,
    is_link_decoy_playwright,
)


# --------------------------------------------------------------------------
# BS-level helpers
# --------------------------------------------------------------------------

def _bs(html: str):
    """Parse an HTML fragment and return its first child element."""
    return BeautifulSoup(html, "html.parser").find()


def _bs_all(html: str, tag: str):
    """Parse a fragment and return all elements of a given tag."""
    return BeautifulSoup(html, "html.parser").find_all(tag)


# --------------------------------------------------------------------------
# Visibility signal tests — inline style
# --------------------------------------------------------------------------

class TestInlineStyleHidden:
    """At least 4 cases for the inline-style hidden signal."""

    def test_display_none(self):
        el = _bs('<a href="/dl" style="display:none">Download</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is True
        assert reason == "inline_style_hidden"

    def test_visibility_hidden(self):
        el = _bs('<a href="/dl" style="visibility: hidden">Download</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is True
        assert reason == "inline_style_hidden"

    def test_opacity_zero(self):
        el = _bs('<a href="/dl" style="opacity: 0">Download</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is True
        assert reason == "inline_style_hidden"

    def test_off_screen_left(self):
        el = _bs(
            '<a href="/dl" '
            'style="position:absolute; left:-9999px">Download</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is True
        assert reason == "inline_style_hidden"

    def test_no_style_is_visible(self):
        el = _bs('<a href="/file.mp4">Download</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is False
        assert reason == ""


# --------------------------------------------------------------------------
# Visibility signal tests — hidden attribute
# --------------------------------------------------------------------------

class TestHiddenAttribute:
    """At least 4 cases for the bare HTML ``hidden`` attribute."""

    def test_hidden_bare(self):
        el = _bs('<a href="/dl" hidden>Download</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is True
        assert reason == "hidden_attribute"

    def test_hidden_empty_string(self):
        el = _bs('<a href="/dl" hidden="">Download</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is True
        assert reason == "hidden_attribute"

    def test_hidden_value_true(self):
        # Browsers treat any value as hidden (not just empty/bare).
        el = _bs('<a href="/dl" hidden="true">Download</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is True
        assert reason == "hidden_attribute"

    def test_button_hidden(self):
        el = _bs('<button hidden>Download MP4</button>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is True
        assert reason == "hidden_attribute"


# --------------------------------------------------------------------------
# Visibility signal tests — aria-hidden
# --------------------------------------------------------------------------

class TestAriaHidden:
    """At least 4 cases for aria-hidden semantics."""

    def test_aria_hidden_true(self):
        el = _bs('<a href="/dl" aria-hidden="true">Download</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is True
        assert reason == "aria_hidden"

    def test_aria_hidden_false_is_visible(self):
        el = _bs(
            '<a href="/file.mp4" aria-hidden="false">Download</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is False

    def test_aria_hidden_absent_is_visible(self):
        el = _bs('<a href="/file.mp4">Download</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is False

    def test_aria_hidden_on_button(self):
        el = _bs('<button aria-hidden="true">Download HD</button>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is True
        assert reason == "aria_hidden"


# --------------------------------------------------------------------------
# Visibility signal tests — CSS class hidden via document <style>
# --------------------------------------------------------------------------

class TestCssClassHidden:
    """At least 4 cases for CSS-text class consultation."""

    def test_class_display_none(self):
        css = ".honey { display:none; }"
        el = _bs('<a href="/dl" class="honey">Download</a>')
        is_decoy, reason = is_link_decoy(el, css_text=css)
        assert is_decoy is True
        assert reason == "css_class_hidden"

    def test_class_visibility_hidden(self):
        css = ".trap-btn { visibility:hidden; }"
        el = _bs('<button class="trap-btn">Click</button>')
        is_decoy, reason = is_link_decoy(el, css_text=css)
        assert is_decoy is True
        assert reason == "css_class_hidden"

    def test_id_selector_hidden(self):
        css = "#decoy { opacity:0; }"
        el = _bs('<a id="decoy" href="/dl">Get</a>')
        is_decoy, reason = is_link_decoy(el, css_text=css)
        assert is_decoy is True
        assert reason == "css_class_hidden"

    def test_unrelated_css_doesnt_fire(self):
        css = ".other-class { display:none; } a { color:blue; }"
        el = _bs('<a href="/file.mp4" class="real">Download</a>')
        is_decoy, reason = is_link_decoy(el, css_text=css)
        assert is_decoy is False

    def test_no_css_text_no_fire(self):
        el = _bs('<a href="/file.mp4" class="real">Download</a>')
        is_decoy, reason = is_link_decoy(el, css_text="")
        assert is_decoy is False


# --------------------------------------------------------------------------
# tabindex=-1 semantics — link vs combo
# --------------------------------------------------------------------------

class TestTabindexMinus1:
    """At least 3 cases for tabindex=-1 semantics on links.

    Spec: tabindex=-1 alone is NOT a honeypot trigger for a link
    (modal close buttons, JS focus managers legitimately use it).
    It IS a trigger for inputs (handled in deep_detect, not here).
    """

    def test_tabindex_minus1_alone_on_link_is_not_decoy(self):
        # The point of the tabindex semantic — JS-managed widgets.
        el = _bs(
            '<a href="/file.mp4" tabindex="-1">Download</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is False
        assert reason == ""

    def test_tabindex_minus1_plus_hidden_attr_is_decoy(self):
        # The combination IS a decoy — hidden attribute fires
        # (tabindex contributes nothing because the hidden attr
        # is already standalone-sufficient).
        el = _bs(
            '<a href="/dl" tabindex="-1" hidden>Click</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is True
        assert reason == "hidden_attribute"

    def test_tabindex_minus1_plus_aria_hidden_is_decoy(self):
        el = _bs(
            '<a href="/dl" tabindex="-1" '
            'aria-hidden="true">Click</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is True
        assert reason == "aria_hidden"

    def test_tabindex_minus1_on_button_alone_is_not_decoy(self):
        # Modal-close buttons commonly have tabindex=-1.
        el = _bs('<button tabindex="-1">Close</button>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is False


# --------------------------------------------------------------------------
# TRAP_URL_TERMS — at least 4 positive + non-firing cases
# --------------------------------------------------------------------------

class TestTrapUrlTerms:

    def test_click_query_path_fires(self):
        el = _bs('<a href="https://t.example/click?id=abc">Download</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is True
        assert reason == "trap_url_term"

    def test_go_query_path_fires(self):
        el = _bs('<a href="https://t.example/go?to=foo">Free Video</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is True
        assert reason == "trap_url_term"

    def test_affiliate_path_fires(self):
        el = _bs(
            '<a href="https://shop.example/affiliate/1234">Buy</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is True
        assert reason == "trap_url_term"

    def test_redirect_query_fires(self):
        el = _bs(
            '<a href="https://x.com/redirect?url=https://bad.com">Go</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is True
        assert reason == "trap_url_term"

    def test_data_href_attribute_also_checked(self):
        el = _bs(
            '<div data-href="https://t.example/clk?x=1">Click</div>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is True
        assert reason == "trap_url_term"

    def test_legit_sports_url_does_not_fire(self):
        # Verifies we don't false-positive on common substrings.
        # ``/sports`` contains ``/spo`` but not ``/sponsor``.
        el = _bs(
            '<a href="https://example.com/category/sports">Sports</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is False

    def test_legit_clarification_url_does_not_fire(self):
        # ``/clarification`` contains ``/cl`` but not ``/clk``.
        el = _bs(
            '<a href="https://example.com/clarification">Info</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is False

    def test_legit_video_url_does_not_fire(self):
        el = _bs(
            '<a href="https://cdn.example.com/video/file.mp4">Download</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is False


# --------------------------------------------------------------------------
# TRAP_TEXT_TERMS — visible text vocabulary
# --------------------------------------------------------------------------

class TestTrapTextTerms:

    def test_sponsored_text_fires(self):
        el = _bs(
            '<a href="https://x.com/page">Sponsored content</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is True
        assert reason == "trap_text_term"

    def test_advertisement_text_fires(self):
        el = _bs(
            '<a href="https://x.com/page">Advertisement</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is True
        assert reason == "trap_text_term"

    def test_aria_label_text_fires(self):
        el = _bs(
            '<button aria-label="Sponsored link">Click</button>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is True
        assert reason == "trap_text_term"

    def test_legit_download_text_does_not_fire(self):
        el = _bs('<a href="/file.mp4">Download HD</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is False


# --------------------------------------------------------------------------
# Rule precedence — first-fire reason
# --------------------------------------------------------------------------

class TestReasonPrecedence:
    """Spec says reason returns the FIRST match — so visibility
    signals beat URL/text vocab. This is intentional: visibility
    is the higher-confidence signal."""

    def test_inline_style_beats_url_trap(self):
        el = _bs(
            '<a href="/clk?x=1" style="display:none">'
            'Sponsored</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is True
        assert reason == "inline_style_hidden"

    def test_aria_hidden_beats_text_trap(self):
        el = _bs(
            '<a href="/dl" aria-hidden="true">Sponsored</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is True
        assert reason == "aria_hidden"


# --------------------------------------------------------------------------
# Playwright-locator variant — mock locator
# --------------------------------------------------------------------------

class _MockLocator:
    """Minimal Playwright locator stand-in: implements
    ``is_visible(timeout=)``, ``get_attribute(name)``,
    ``inner_text(timeout=)``."""

    def __init__(self, *, visible=True, attrs=None, text="",
                 visible_raises=False, attr_raises=False):
        self._visible = visible
        self._attrs = attrs or {}
        self._text = text
        self._visible_raises = visible_raises
        self._attr_raises = attr_raises

    def is_visible(self, timeout=None):
        if self._visible_raises:
            raise RuntimeError("playwright crash")
        return self._visible

    def get_attribute(self, name):
        if self._attr_raises:
            raise RuntimeError("attribute access failed")
        return self._attrs.get(name)

    def inner_text(self, timeout=None):
        return self._text


class TestIsLinkDecoyPlaywright:

    def test_not_visible_fires(self):
        loc = _MockLocator(visible=False)
        is_decoy, reason = is_link_decoy_playwright(loc)
        assert is_decoy is True
        assert reason == "not_visible"

    def test_aria_hidden_fires(self):
        loc = _MockLocator(visible=True,
                           attrs={"aria-hidden": "true"})
        is_decoy, reason = is_link_decoy_playwright(loc)
        assert is_decoy is True
        assert reason == "aria_hidden"

    def test_trap_url_fires(self):
        loc = _MockLocator(visible=True,
                           attrs={"href": "https://t.com/clk?x=1"})
        is_decoy, reason = is_link_decoy_playwright(loc)
        assert is_decoy is True
        assert reason == "trap_url_term"

    def test_trap_text_fires(self):
        loc = _MockLocator(visible=True,
                           attrs={"href": "/page"},
                           text="Sponsored content")
        is_decoy, reason = is_link_decoy_playwright(loc)
        assert is_decoy is True
        assert reason == "trap_text_term"

    def test_clean_link_passes(self):
        loc = _MockLocator(
            visible=True,
            attrs={"href": "https://cdn.example.com/file.mp4"},
            text="Download HD")
        is_decoy, reason = is_link_decoy_playwright(loc)
        assert is_decoy is False
        assert reason == ""

    def test_is_visible_exception_fails_open(self):
        # Playwright exception during is_visible — we should NOT
        # drop the candidate, but we should still check the other
        # signals (so a trap_url still fires).
        loc = _MockLocator(
            visible_raises=True,
            attrs={"href": "https://cdn.example.com/file.mp4"})
        is_decoy, reason = is_link_decoy_playwright(loc)
        assert is_decoy is False

    def test_get_attribute_exception_fails_open(self):
        loc = _MockLocator(visible=True, attr_raises=True)
        is_decoy, reason = is_link_decoy_playwright(loc)
        # All attribute access failed → no signal could fire.
        assert is_decoy is False


# --------------------------------------------------------------------------
# find_duplicate_field_decoys — 4 cases
# --------------------------------------------------------------------------

class TestFindDuplicateFieldDecoys:

    def test_real_duplicate_visible_plus_hidden_fires(self):
        html = '''
          <form>
            <input type="email" name="email" placeholder="Your email">
            <input type="email" name="email"
                   style="display:none">
          </form>
        '''
        inputs = _bs_all(html, "input")
        decoys = find_duplicate_field_decoys(inputs)
        assert len(decoys) == 1
        assert decoys[0]["name"] == "email"
        assert decoys[0]["reason"] == "duplicate_field_hidden_decoy"

    def test_unique_name_not_a_duplicate(self):
        html = '''
          <form>
            <input type="text" name="username">
            <input type="email" name="email">
            <input type="password" name="password">
          </form>
        '''
        inputs = _bs_all(html, "input")
        decoys = find_duplicate_field_decoys(inputs)
        assert decoys == []

    def test_all_hidden_duplicates_not_reported(self):
        # Framework artifact (dual CSRF mirrors etc) — not the
        # honeypot pattern we're looking for.
        html = '''
          <form>
            <input type="hidden" name="csrf" value="aaa">
            <input type="hidden" name="csrf" value="bbb">
          </form>
        '''
        inputs = _bs_all(html, "input")
        decoys = find_duplicate_field_decoys(inputs)
        assert decoys == []

    def test_all_visible_duplicates_not_reported(self):
        # Multi-step form chunking — both visible, both legitimate.
        # User is filling in the same field twice (confirmation).
        html = '''
          <form>
            <input type="email" name="email">
            <input type="email" name="email">
          </form>
        '''
        inputs = _bs_all(html, "input")
        decoys = find_duplicate_field_decoys(inputs)
        assert decoys == []

    def test_hidden_via_tabindex_minus1_counts_as_hidden_for_inputs(self):
        # Form-input tabindex semantics differ from link semantics —
        # for inputs, tabindex=-1 IS standalone-hidden.
        html = '''
          <form>
            <input type="email" name="email">
            <input type="email" name="email" tabindex="-1">
          </form>
        '''
        inputs = _bs_all(html, "input")
        decoys = find_duplicate_field_decoys(inputs)
        assert len(decoys) == 1
        assert decoys[0]["name"] == "email"

    def test_multiple_duplicates_each_reported(self):
        html = '''
          <form>
            <input type="email" name="email">
            <input type="email" name="email" hidden>
            <input type="text" name="username">
            <input type="text" name="username"
                   style="visibility:hidden">
          </form>
        '''
        inputs = _bs_all(html, "input")
        decoys = find_duplicate_field_decoys(inputs)
        names = sorted(d["name"] for d in decoys)
        assert names == ["email", "username"]

    def test_input_without_name_skipped(self):
        # Defensive — should not blow up on nameless inputs.
        html = '<form><input><input></form>'
        inputs = _bs_all(html, "input")
        decoys = find_duplicate_field_decoys(inputs)
        assert decoys == []


# --------------------------------------------------------------------------
# Integration: find_best_download with mock Playwright page
# --------------------------------------------------------------------------

class _MockPage:
    """Mock page exposing a ``locator(sel)`` interface compatible
    with what ``find_best_download`` calls. Each locator returns
    a list-shaped object with ``.all()``, ``.first``, ``.count()``,
    and ``.nth(i)``.

    The mock is selector-mapped: pass in a dict mapping selectors
    to lists of ``_MockLocator``s. Unknown selectors return empty.
    """

    def __init__(self, selector_map):
        self._map = selector_map
        # find_best_download also sets page.locator(":text-matches(...)")
        # for the ancestor walk. Catch it via the all() empty default.

    def locator(self, sel):
        # If the selector is registered, return a list-shaped
        # locator. Otherwise empty.
        elements = self._map.get(sel, [])
        return _MockLocatorList(elements)


class _MockLocatorList:
    def __init__(self, elements):
        self._elements = elements

    def all(self):
        return list(self._elements)

    def count(self):
        return len(self._elements)

    @property
    def first(self):
        # find_best_download's custom path uses .first followed
        # by .count() — return a list-shape that mimics that.
        return _MockLocatorList(
            self._elements[:1] if self._elements else [])

    def nth(self, i):
        return self._elements[i]


def _make_loc(*, href=None, text="Download", attrs=None,
              visible=True, aria_hidden=None):
    """Build a fully-featured _MockLocator for find_best_download.

    The locator needs to support every attribute the wide-scan
    reads via ``get_attribute()`` — the ``_attrs_to_scan`` list
    inside detect.find_best_download is the source of truth.
    """
    base_attrs = {}
    if href is not None:
        base_attrs["href"] = href
    if aria_hidden is not None:
        base_attrs["aria-hidden"] = aria_hidden
    if attrs:
        base_attrs.update(attrs)
    loc = _MockLocator(visible=visible, attrs=base_attrs, text=text)
    return loc


# Patch _MockLocator with the extra methods find_best_download uses.
def _ml_all_for_loc(self):
    # find_best_download calls .all() to iterate; for a single loc
    # treat it as a single-element list.
    return [self]


_MockLocator.all = _ml_all_for_loc
_MockLocator.count = lambda self: 1
_MockLocator.first = property(lambda self: self)


class TestFindBestDownloadIntegration:
    """End-to-end: build a mock page with one clean link and one
    invisible decoy. With filter off, both candidates exist.
    With filter on, only the clean one survives."""

    def _make_page_with_clean_and_decoy(self):
        clean = _make_loc(
            href="https://cdn.example.com/video.mp4",
            text="Download 1080p",
            visible=True)
        decoy = _make_loc(
            href="https://cdn.example.com/decoy.mp4",
            text="Download HD",
            visible=False)  # Playwright says invisible
        # Map them to the very first selector find_best_download
        # scans — `a[download]` — so they both get to add().
        # We also need to populate `a[href*='.mp4']` because the
        # wide-scan iterates ALL of step-1's selectors. Using a
        # single shared list across the .mp4 selectors keeps the
        # mock simple but means each locator gets add()'d multiple
        # times (deduped via the ``seen`` set inside add()).
        sel_map = {
            "a[href*='.mp4']": [clean, decoy],
        }
        return _MockPage(sel_map), clean, decoy

    def test_filter_off_keeps_decoy(self, monkeypatch):
        from bulk_downloader.detect import find_best_download
        monkeypatch.delenv("BD_DOM_HONEYPOT_FILTER", raising=False)
        page, clean, decoy = self._make_page_with_clean_and_decoy()
        result = find_best_download(page)
        assert result is not None
        # _all_candidates should contain both texts.
        texts = [c["text"] for c in result["_all_candidates"]]
        assert any("1080p" in t for t in texts)
        assert any("HD" in t for t in texts)

    def test_filter_cheap_drops_decoy(self, monkeypatch):
        from bulk_downloader.detect import find_best_download
        monkeypatch.setenv("BD_DOM_HONEYPOT_FILTER", "cheap")
        page, clean, decoy = self._make_page_with_clean_and_decoy()
        result = find_best_download(page)
        assert result is not None
        texts = [c["text"] for c in result["_all_candidates"]]
        # The decoy ("HD") should be gone — only the visible one.
        assert any("1080p" in t for t in texts)
        assert not any(t == "Download HD" for t in texts)

    def test_filter_strict_drops_decoy(self, monkeypatch):
        from bulk_downloader.detect import find_best_download
        monkeypatch.setenv("BD_DOM_HONEYPOT_FILTER", "strict")
        page, clean, decoy = self._make_page_with_clean_and_decoy()
        result = find_best_download(page)
        assert result is not None
        texts = [c["text"] for c in result["_all_candidates"]]
        assert any("1080p" in t for t in texts)
        assert not any(t == "Download HD" for t in texts)

    def test_filter_unrecognized_value_treated_as_off(
            self, monkeypatch):
        from bulk_downloader.detect import find_best_download
        monkeypatch.setenv("BD_DOM_HONEYPOT_FILTER", "yes")
        page, clean, decoy = self._make_page_with_clean_and_decoy()
        result = find_best_download(page)
        assert result is not None
        texts = [c["text"] for c in result["_all_candidates"]]
        # "yes" is unrecognized → off → both candidates kept.
        assert any("HD" in t for t in texts)

    def test_all_candidates_filtered_returns_none(self, monkeypatch):
        from bulk_downloader.detect import find_best_download
        monkeypatch.setenv("BD_DOM_HONEYPOT_FILTER", "cheap")
        # Build a page where EVERY locator is invisible.
        decoy1 = _make_loc(
            href="https://x.com/clk?id=1",
            text="Download 1080p",
            visible=False)
        decoy2 = _make_loc(
            href="https://x.com/clk?id=2",
            text="Download HD",
            visible=False)
        sel_map = {"a[href*='.mp4']": [decoy1, decoy2]}
        page = _MockPage(sel_map)
        result = find_best_download(page)
        assert result is None

    def test_dom_filter_exception_fails_open(self, monkeypatch):
        """If is_link_decoy_playwright itself raises (not the
        Playwright API but our code), the candidate should be
        KEPT — bug in scoring must not cost a real download."""
        from bulk_downloader import detect

        def _exploding(_locator):
            raise RuntimeError("boom")

        monkeypatch.setenv("BD_DOM_HONEYPOT_FILTER", "cheap")
        # Force import the module so the patch sticks.
        import bulk_downloader.dom_honeypot as dh
        monkeypatch.setattr(
            dh, "is_link_decoy_playwright", _exploding)
        # Also patch the binding inside detect.add()'s closure
        # — it does `from .dom_honeypot import ...` lazily, which
        # picks up the patched attr.
        clean = _make_loc(
            href="https://cdn.example.com/video.mp4",
            text="Download 1080p",
            visible=True)
        # We can't pre-import detect's binding because add()
        # imports lazily. The lazy import will hit our monkeypatch
        # on the module attribute.
        sel_map = {"a[href*='.mp4']": [clean]}
        result = detect.find_best_download(_MockPage(sel_map))
        assert result is not None
        texts = [c["text"] for c in result["_all_candidates"]]
        assert any("1080p" in t for t in texts)


# --------------------------------------------------------------------------
# Module-level constants — sanity
# --------------------------------------------------------------------------

class TestConstants:

    def test_trap_url_terms_is_tuple(self):
        assert isinstance(TRAP_URL_TERMS, tuple)
        assert len(TRAP_URL_TERMS) >= 5

    def test_trap_text_terms_is_tuple(self):
        assert isinstance(TRAP_TEXT_TERMS, tuple)
        assert len(TRAP_TEXT_TERMS) >= 3

    def test_decoy_link_hidden_patterns_reexports_deep_detect(self):
        from bulk_downloader.deep_detect import HONEYPOT_CSS_HIDDEN
        # Same identity — one source of truth.
        assert DECOY_LINK_HIDDEN_PATTERNS is HONEYPOT_CSS_HIDDEN

    def test_no_overly_short_url_terms(self):
        # Defensive: terms shorter than 3 chars would false-positive
        # in random URL paths. Make sure we don't accidentally have
        # any tiny terms.
        for t in TRAP_URL_TERMS:
            assert len(t) >= 3, f"URL term too short: {t!r}"
