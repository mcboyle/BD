"""Tests for Row 943: ARIA-HIDDEN-AND-TABINDEX-SEMANTIC-ELEMENT-CLASSIFIER.

Acceptance requirements:
(1) exclusion of aria-hidden elements
(2) filtering of un-focusable links (tabindex=-1)
(3) preservation of navigable controls
"""
from __future__ import annotations

import pytest

from bulk_downloader import semantic_filter as _sf   # a missing module is an ImportError, not a masked None

BD_GATE_SCOPE = "module"


def _get_semantic_filter():
    return _sf


def test_exclusion_of_aria_hidden_elements():
    """Verify elements with aria-hidden=true are identified and excluded from crawling."""
    sf = _get_semantic_filter()
    assert sf is not None, "bulk_downloader.semantic_filter must be implemented"

    # Direct attribute dictionaries
    hidden_link = {"href": "https://example.com/trap", "aria-hidden": "true"}
    visible_link = {"href": "https://example.com/real", "aria-hidden": "false"}
    unmarked_link = {"href": "https://example.com/normal"}

    assert sf.is_aria_hidden(hidden_link) is True
    assert sf.is_aria_hidden(visible_link) is False
    assert sf.is_aria_hidden(unmarked_link) is False

    # HTML string snippets
    html_hidden = '<a href="/trap1" aria-hidden="true">Hidden</a>'
    html_visible = '<a href="/page1" aria-hidden="false">Visible</a>'
    html_unmarked = '<a href="/page2">Normal</a>'

    assert sf.is_navigable(html_hidden) is False
    assert sf.is_navigable(html_visible) is True
    assert sf.is_navigable(html_unmarked) is True

    # Classification details
    cls_hidden = sf.classify_element(hidden_link)
    assert cls_hidden["navigable"] is False
    assert cls_hidden["aria_hidden"] is True
    assert "aria-hidden" in cls_hidden["reason"]


def test_filtering_of_unfocusable_links():
    """Verify links with negative tabindex (e.g. tabindex=-1) or hidden markers are filtered."""
    sf = _get_semantic_filter()
    assert sf is not None, "bulk_downloader.semantic_filter must be implemented"

    # Tabindex negative cases
    unfocusable_dict = {"href": "/trap2", "tabindex": "-1"}
    focusable_default = {"href": "/item1"}
    focusable_explicit = {"href": "/item2", "tabindex": "0"}
    positive_tabindex = {"href": "/item3", "tabindex": "1"}

    assert sf.is_focusable(unfocusable_dict) is False
    assert sf.is_focusable(focusable_default) is True
    assert sf.is_focusable(focusable_explicit) is True
    assert sf.is_focusable(positive_tabindex) is True

    # Navigability checks
    assert sf.is_navigable(unfocusable_dict) is False
    assert sf.is_navigable(focusable_default) is True

    # HTML string with tabindex=-1
    html_unfocusable = '<a href="/unfocusable" tabindex="-1">Decorative Link</a>'
    assert sf.is_navigable(html_unfocusable) is False

    cls_unfocus = sf.classify_element(unfocusable_dict)
    assert cls_unfocus["navigable"] is False
    assert cls_unfocus["focusable"] is False
    assert "tabindex" in cls_unfocus["reason"]


def test_preservation_of_navigable_controls():
    """Verify standard navigable controls and links are preserved without disruption."""
    sf = _get_semantic_filter()
    assert sf is not None, "bulk_downloader.semantic_filter must be implemented"

    elements = [
        {"href": "https://example.com/target1", "text": "Valid Download"},
        {"href": "https://example.com/trap-aria", "aria-hidden": "true", "text": "Trap"},
        {"href": "https://example.com/target2", "tabindex": "0", "text": "Keyboard Navigable"},
        {"href": "https://example.com/trap-tabindex", "tabindex": "-1", "text": "Trap 2"},
        {"href": "https://example.com/target3", "role": "link", "text": "Accessible Role"},
    ]

    filtered = sf.filter_navigable_links(elements)
    assert len(filtered) == 3
    assert [e["href"] for e in filtered] == [
        "https://example.com/target1",
        "https://example.com/target2",
        "https://example.com/target3",
    ]

    # HTML batch filtering
    html_page = """
    <nav>
      <a href="/real-item-1">Item 1</a>
      <a href="/decorative-icon" aria-hidden="true">Icon</a>
      <a href="/real-item-2" tabindex="0">Item 2</a>
      <a href="/skip-link-trap" tabindex="-1">Hidden Trap</a>
    </nav>
    """
    links = sf.extract_navigable_urls(html_page)
    assert links == ["/real-item-1", "/real-item-2"]


# ---- fixer (O928): correctness REFUTE E1/E3 (bare / empty aria-hidden) -------

@pytest.mark.parametrize("markup", [
    '<a href="/trap" aria-hidden>Trap</a>',              # bare boolean form (HTMLParser -> None)
    '<a href="/trap" aria-hidden="">Trap</a>',           # empty-quoted
    "<a href='/trap' aria-hidden=''>Trap</a>",
    '<a href="/trap" aria-hidden=true>Trap</a>',         # unquoted
    '<a href="/trap" aria-hidden="TRUE">Trap</a>',
    '<a href="/trap" aria-hidden="1">Trap</a>',
    '<a aria-hidden href="/trap">Trap</a>',              # attribute before href
])
def test_e1_bare_and_empty_aria_hidden_fail_closed(markup):
    """E1: presence is the intent -- every spelling of a present aria-hidden
    that does not say "false" conceals the element, in the string path, the
    attribute-dict path (HTMLParser yields None for a bare attribute) and the
    full extractor."""
    assert _sf.is_aria_hidden(markup) is True
    assert _sf.is_navigable(markup) is False
    assert _sf.extract_navigable_urls(markup) == []


def test_e1_attribute_dicts_from_the_parser_fail_closed_and_false_stays_open():
    for hidden in ({"href": "/t", "aria-hidden": None}, {"href": "/t", "aria-hidden": ""},
                   {"href": "/t", "aria-hidden": True}, {"href": "/t", "aria_hidden": "1"}):
        assert _sf.is_aria_hidden(hidden) is True, hidden
        assert _sf.classify_element(hidden)["navigable"] is False
    # positive controls: an explicit false, an absent attribute and a look-alike name are navigable
    for shown in ({"href": "/r", "aria-hidden": "false"}, {"href": "/r", "aria-hidden": False}, {"href": "/r"},
                  {"href": "/r", "aria-hiddenx": "true"}):
        assert _sf.is_aria_hidden(shown) is False, shown
    assert _sf.is_aria_hidden('<a href="/r" aria-hiddenx="true">x</a>') is False
    assert _sf.is_aria_hidden('<a href="/r" data-aria-hidden="true">x</a>') is False
    assert _sf.extract_navigable_urls(
        '<a href="/trap" aria-hidden>T</a><a href="/ok">ok</a><a href="/t2" aria-hidden="">x</a><a href="/shown" aria-hidden="false">s</a>'
    ) == ["/ok", "/shown"]
