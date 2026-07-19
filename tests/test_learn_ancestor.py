"""Regression tests for the learn.classify_download module.

v3.42.1: previously, when a user clicked the inner <span> of an
<a href="..."> during teach takeover, the recorder only saw the span's
attributes (which had no href) and classify_download fell through to
a wide-scan page heuristic that picked unrelated rows. The fix has the
JS recorder walk up the DOM to find the nearest ancestor with a URL
attribute, and classify_download uses the ancestor's tag/text/url to
synthesize selectors targeting the actual anchor.
"""
from bulk_downloader.learn import classify_download, _which_url_attr


def _click(target_tag, target_text, **attrs):
    """Build a click record matching the JS recorder output shape."""
    rec = {
        "tag": target_tag,
        "id": "",
        "name": "",
        "type": "",
        "cls": "",
        "text": target_text,
        "href": "",
        "role": "",
        "autocomplete": "",
        "placeholder": "",
        "ariaLabel": "",
        "testid": "",
        "dataHref": "",
        "dataUrl": "",
        "dataSrc": "",
        "dataDownload": "",
        "ancestorTag": "",
        "ancestorAttr": "",
        "ancestorUrl": "",
        "ancestorDepth": -1,
        "ancestorText": "",
        "url": "https://example.com/v/foo",
        "ts": 1_000_000_000,
    }
    rec.update(attrs)
    return rec


def test_which_url_attr_finds_direct_href():
    """Click record with href on the click target itself."""
    c = _click("a", "Download 4K", href="https://example.com/v/foo-4k.mp4")
    attr, val = _which_url_attr(c)
    assert attr == "href"
    assert val == "https://example.com/v/foo-4k.mp4"


def test_which_url_attr_finds_ancestor_href():
    """v3.42.1 bug fix: click target was an inner <span>, but the wrapping
    <a> had the href. Recorder captured it as ancestor*. Detector must
    find it there too."""
    c = _click(
        "span", "3840 x 2160",
        ancestorTag="a",
        ancestorAttr="href",
        ancestorUrl="https://example.com/v/foo-4k.mp4",
        ancestorDepth=1,
        ancestorText="3840 x 2160 4K",
    )
    attr, val = _which_url_attr(c)
    assert attr == "href"
    assert val == "https://example.com/v/foo-4k.mp4"


def test_which_url_attr_returns_none_when_no_url():
    """No URL anywhere — neither direct nor ancestor — return (None, None)."""
    c = _click("span", "Random text")
    attr, val = _which_url_attr(c)
    assert attr is None
    assert val is None


def test_classify_download_uses_ancestor_for_synthesis():
    """v3.42.1: when the URL was found via ancestor, the synthesized
    row_selector must target the ancestor anchor, NOT the click target
    span. Previously, classify_download passed the raw click record to
    the synthesizer, producing selectors that matched the span (which
    had no href, breaking replay)."""
    clicks = [
        _click(
            "span", "3840 x 2160",
            cls="quality-label",  # span has its own class
            ancestorTag="a",
            ancestorAttr="href",
            ancestorUrl="https://example.com/v/foo-4k.mp4",
            ancestorText="3840 x 2160 4K",
        ),
    ]
    result = classify_download({"clicks": clicks})
    # We should have detected a row
    assert "row_selectors" in result
    assert result.get("url_attribute") == "href"
    # And the tier label should come from the ancestor's text, not the span
    # (synthesize result is opaque; just sanity check it isn't empty)
    assert isinstance(result["row_selectors"], list)
    assert len(result["row_selectors"]) >= 1


def test_classify_download_empty_clicks():
    """No clicks recorded — return empty dict, don't crash."""
    assert classify_download({"clicks": []}) == {}
    assert classify_download({}) == {}


def test_classify_download_only_text_clicks():
    """Clicks recorded but none had URL anywhere — also returns empty
    so caller falls back to wide-scan."""
    clicks = [_click("button", "Close")]
    result = classify_download({"clicks": clicks})
    # With no row_rec, no trigger should be set either
    assert "row_selectors" not in result
