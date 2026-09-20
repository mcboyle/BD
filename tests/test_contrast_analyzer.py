"""Tests for bulk_downloader.contrast_analyzer (Row 944).

Verifies:
(1) Identification of zero-contrast text anchors (transparent fonts, matching background colors, <1.05:1).
(2) Detection of text-indent disguise links (extreme negative text-indent e.g. -9999px).
(3) Admission of standard links (legitimate visible links with perceptible contrast and normal indent).
"""

import pytest
from bs4 import BeautifulSoup

BD_GATE_SCOPE = "module"

from bulk_downloader.contrast_analyzer import (
    MIN_CONTRAST_RATIO,
    ContrastAnalyzer,
    analyze_anchor_visibility,
    compute_contrast_ratio,
    compute_relative_luminance,
    filter_visible_anchors,
    is_anchor_visible,
    is_text_indent_disguise,
    parse_color,
    parse_text_indent,
)

# ============================================================================
# Core Color & Contrast Tests
# ============================================================================


def test_parse_color_hex():
    assert parse_color("#fff") == (255, 255, 255, 1.0)
    assert parse_color("#000") == (0, 0, 0, 1.0)
    assert parse_color("#ffffff") == (255, 255, 255, 1.0)
    assert parse_color("#000000") == (0, 0, 0, 1.0)
    assert parse_color("#ff0000") == (255, 0, 0, 1.0)
    assert parse_color("#00ff00") == (0, 255, 0, 1.0)
    assert parse_color("#0000ff") == (0, 0, 255, 1.0)


def test_parse_color_rgb_and_rgba():
    assert parse_color("rgb(255, 255, 255)") == (255, 255, 255, 1.0)
    assert parse_color("rgb(0, 0, 0)") == (0, 0, 0, 1.0)
    assert parse_color("rgba(0, 0, 0, 0)") == (0, 0, 0, 0.0)
    assert parse_color("rgba(255, 0, 0, 0.5)") == (255, 0, 0, 0.5)


def test_parse_color_named_and_special():
    assert parse_color("transparent") == (0, 0, 0, 0.0)
    assert parse_color("white") == (255, 255, 255, 1.0)
    assert parse_color("black") == (0, 0, 0, 1.0)
    assert parse_color("red") == (255, 0, 0, 1.0)
    assert parse_color("blue") == (0, 0, 255, 1.0)


def test_compute_relative_luminance():
    assert compute_relative_luminance((0, 0, 0)) == pytest.approx(0.0, abs=1e-4)
    assert compute_relative_luminance((255, 255, 255)) == pytest.approx(1.0, abs=1e-4)
    # Relative luminance of pure green (0, 255, 0) is approx 0.7152
    assert compute_relative_luminance((0, 255, 0)) == pytest.approx(0.7152, abs=1e-3)


def test_compute_contrast_ratio_standard():
    # Black on white: 21:1
    ratio = compute_contrast_ratio("#000000", "#ffffff")
    assert ratio == pytest.approx(21.0, abs=0.1)

    # Identical colors: 1:1
    assert compute_contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1.0, abs=1e-3)
    assert compute_contrast_ratio("#000000", "#000000") == pytest.approx(1.0, abs=1e-3)
    assert compute_contrast_ratio("#123456", "#123456") == pytest.approx(1.0, abs=1e-3)


# ============================================================================
# Acceptance (1): Identification of zero-contrast text anchors
# ============================================================================


def test_zero_contrast_transparent_font():
    # Fully transparent font rgba(0,0,0,0) on white background
    res = analyze_anchor_visibility(
        text="Invisible Download",
        style="color: rgba(0, 0, 0, 0); background-color: #ffffff;",
    )
    assert not res.is_visible
    assert res.contrast_ratio < MIN_CONTRAST_RATIO
    assert "contrast" in res.reason.lower() or "transparent" in res.reason.lower()


def test_zero_contrast_named_transparent():
    # Color: transparent
    res = analyze_anchor_visibility(
        text="Trap Link",
        style="color: transparent; background: #ffffff;",
    )
    assert not res.is_visible
    assert res.contrast_ratio < MIN_CONTRAST_RATIO


def test_zero_contrast_matching_background_white_on_white():
    # White on white
    res = analyze_anchor_visibility(
        text="Hidden Link",
        style="color: #ffffff; background-color: #ffffff;",
    )
    assert not res.is_visible
    assert res.contrast_ratio == pytest.approx(1.0, abs=0.01)
    assert res.contrast_ratio < MIN_CONTRAST_RATIO


def test_zero_contrast_matching_background_black_on_black():
    # Black on black
    res = analyze_anchor_visibility(
        text="Hidden Link",
        style="color: #000000; background-color: #000000;",
    )
    assert not res.is_visible
    assert res.contrast_ratio == pytest.approx(1.0, abs=0.01)
    assert res.contrast_ratio < MIN_CONTRAST_RATIO


def test_imperceptible_contrast_below_threshold():
    # Extremely subtle difference: #ffffff on #fefefe -> contrast ~1.01:1 (<1.05:1)
    res = analyze_anchor_visibility(
        text="Subtle Link",
        style="color: #ffffff; background-color: #fefefe;",
    )
    assert not res.is_visible
    assert res.contrast_ratio < 1.05
    assert "contrast" in res.reason.lower()


# ============================================================================
# Acceptance (2): Detection of text-indent disguise links
# ============================================================================


def test_parse_text_indent():
    assert parse_text_indent("-9999px") == -9999.0
    assert parse_text_indent("-1000px") == -1000.0
    assert parse_text_indent("0px") == 0.0
    assert parse_text_indent("10px") == 10.0
    assert parse_text_indent("-50em") == -800.0  # -50 * 16px
    assert parse_text_indent(None) == 0.0
    assert parse_text_indent("") == 0.0


def test_is_text_indent_disguise():
    assert is_text_indent_disguise("-9999px") is True
    assert is_text_indent_disguise("-1000px") is True
    assert is_text_indent_disguise("-500px") is True
    assert is_text_indent_disguise("-100px") is True
    assert is_text_indent_disguise("-99px") is False
    assert is_text_indent_disguise("0px") is False
    assert is_text_indent_disguise("20px") is False


def test_detection_text_indent_disguise_anchor():
    # Anchor with extreme negative text indent
    res = analyze_anchor_visibility(
        text="Download Tracker Trap",
        style="text-indent: -9999px; color: #000000; background-color: #ffffff;",
    )
    assert not res.is_visible
    assert res.is_text_indent_disguise is True
    assert "text-indent" in res.reason.lower()


def test_detection_text_indent_disguise_em_unit():
    # Disguise with em units: text-indent: -999em
    res = analyze_anchor_visibility(
        text="Disguised Link",
        style="text-indent: -999em;",
    )
    assert not res.is_visible
    assert res.is_text_indent_disguise is True


# ============================================================================
# Acceptance (3): Admission of standard links
# ============================================================================


def test_admission_standard_black_on_white():
    res = analyze_anchor_visibility(
        text="Legitimate Download Link",
        style="color: #000000; background-color: #ffffff; text-indent: 0;",
    )
    assert res.is_visible is True
    assert res.contrast_ratio >= 1.05
    assert res.is_text_indent_disguise is False


def test_admission_standard_link_blue_on_white():
    # Standard hyperlink blue (#0000ee) on white (#ffffff)
    res = analyze_anchor_visibility(
        text="Get File Now",
        style="color: #0000ee; background-color: #ffffff;",
    )
    assert res.is_visible is True
    assert res.contrast_ratio > 4.5
    assert res.is_text_indent_disguise is False


def test_admission_standard_link_default_styles():
    # No explicit styles provided (defaults to black text on white background)
    res = analyze_anchor_visibility(
        text="Default Styled Anchor",
        style="",
    )
    assert res.is_visible is True
    assert res.contrast_ratio >= 1.05
    assert res.is_text_indent_disguise is False


def test_is_anchor_visible_convenience_function():
    # is_anchor_visible returns (bool, reason)
    vis1, reason1 = is_anchor_visible("color: #000; background: #fff;")
    assert vis1 is True
    assert reason1 == "visible"

    vis2, reason2 = is_anchor_visible("color: rgba(0,0,0,0); background: #fff;")
    assert vis2 is False
    assert "contrast" in reason2.lower()

    vis3, reason3 = is_anchor_visible("text-indent: -9999px;")
    assert vis3 is False
    assert "text-indent" in reason3.lower()


# ============================================================================
# BeautifulSoup & HTML Integration Tests
# ============================================================================


def test_contrast_analyzer_with_beautifulsoup_element():
    html = (
        '<a href="/dl" style="color: rgba(0,0,0,0); background: #fff;">Hidden Trap</a>'
    )
    soup = BeautifulSoup(html, "html.parser")
    a_tag = soup.find("a")

    analyzer = ContrastAnalyzer()
    res = analyzer.analyze_element(a_tag)
    assert not res.is_visible
    assert res.contrast_ratio < 1.05


def test_contrast_analyzer_with_parent_background():
    # Anchor inherits background from parent container
    html = """
    <div style="background-color: #000000;">
        <a id="bad" href="/trap" style="color: #000000;">Black on Black</a>
        <a id="good" href="/real" style="color: #ffffff;">White on Black</a>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    bad_a = soup.find("a", id="bad")
    good_a = soup.find("a", id="good")

    analyzer = ContrastAnalyzer()
    res_bad = analyzer.analyze_element(bad_a)
    assert not res_bad.is_visible
    assert res_bad.contrast_ratio < 1.05

    res_good = analyzer.analyze_element(good_a)
    assert res_good.is_visible
    assert res_good.contrast_ratio > 15.0


def test_filter_visible_anchors():
    html = """
    <div>
        <a href="/normal" style="color: #000; background: #fff;">Normal</a>
        <a href="/zero-contrast" style="color: #fff; background: #fff;">Invisible</a>
        <a href="/disguise" style="text-indent: -9999px; color: #000;">Disguise</a>
        <a href="/another-normal" style="color: #0066cc; background: #fff;">Another Normal</a>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.find_all("a")

    visible_anchors = filter_visible_anchors(anchors)
    hrefs = [a["href"] for a in visible_anchors]
    assert hrefs == ["/normal", "/another-normal"]


# ============================================================================
# fixer (O928): correctness REFUTE E1/E2 + PREP-REFUSED shards-939
# ============================================================================

def test_e1_a_background_shorthand_without_a_colour_is_the_page_background():
    """E1 (acceptance 3): a sprite/none/url() background is not a colour and
    must never be read as opaque black -- ordinary links are admitted."""
    from bulk_downloader.contrast_analyzer import background_color_of
    assert background_color_of("url(sprite.png) no-repeat") is None
    assert background_color_of("none") is None
    assert background_color_of("#fff url(a.png) no-repeat 0 0") == (255, 255, 255, 1.0)
    assert background_color_of("url(a.png), rgb(0 0 0 / 0.5) center / cover") == (0, 0, 0, 0.5)
    for style in ("background: url(sprite.png) no-repeat", "background: none",
                  "background: #fff url(a.png)", "background: inherit"):
        r = analyze_anchor_visibility("Next page", style)
        assert r.is_visible and r.contrast_ratio == pytest.approx(21.0), (style, r)
    # a translucent background composites over the page: white text on 50% black over white
    r = analyze_anchor_visibility("x", "background: rgba(0,0,0,0.5) url(a.png); color: white")
    assert r.is_visible and 3.5 < r.contrast_ratio < 4.5
    # negative control: a real black background still hides black text
    assert not analyze_anchor_visibility("x", "background: #000 url(a.png); color: #000").is_visible


def test_e2_every_transparent_syntax_is_rejected_and_near_white_names_measure_true():
    """E2 (acceptance 1): CSS Color 4 space syntax, percentage alpha, hsla and
    the full named table parse; a transparent font in ANY syntax is
    zero-contrast, and whitesmoke/percentage-white measure their true ratio."""
    for transparent in ("rgb(0 0 0 / 0)", "rgba(0,0,0,0%)", "hsla(0,0%,0%,0)", "hsl(0 0% 0% / 0%)", "#0000", "#00000000"):
        assert parse_color(transparent)[3] == 0.0, transparent
        r = analyze_anchor_visibility("hidden", f"color: {transparent}")
        assert not r.is_visible and r.contrast_ratio == pytest.approx(1.0), (transparent, r)
    assert parse_color("whitesmoke") == (245, 245, 245, 1.0)
    assert parse_color("rgb(100%,100%,100%)") == (255, 255, 255, 1.0)
    assert parse_color("hsl(120, 100%, 25%)") == (0, 128, 0, 1.0)
    assert parse_color("hsl(0.5turn 100% 50%)") == (0, 255, 255, 1.0)
    assert parse_color("rebeccapurple") == (102, 51, 153, 1.0)
    assert parse_color("url(x.png)") is None and parse_color("none") is None and parse_color("") is None
    r = analyze_anchor_visibility("x", "color: whitesmoke")
    assert r.contrast_ratio == pytest.approx(1.09, abs=0.01) and r.is_visible          # true ratio, not 21
    r = analyze_anchor_visibility("x", "color: rgb(100%,100%,100%)")
    assert not r.is_visible and r.contrast_ratio == pytest.approx(1.0)
    # an unknown colour keyword is the default text colour: admitted, never black-on-accident
    r = analyze_anchor_visibility("x", "color: var(--link); background: #fff")
    assert r.is_visible and r.color == (0, 0, 0, 1.0)


# ── correctness REFUTE E1 (2026-09-20): the !important suffix is not part of the value ──

def test_important_suffixed_disguises_are_still_rejected():
    """'color: transparent !important' is transparent; the routine spelling of cloaked-link CSS
    must not read as an unknown keyword (admitted). Controls: the same values without the suffix."""
    for style in (
        "color: transparent !important; background: #ffffff;",
        "color: #ffffff !important; background-color: #fff !important;",
        "color: rgba(0,0,0,0) ! IMPORTANT; background: white",
    ):
        res = analyze_anchor_visibility(text="Trap Link", style=style)
        assert not res.is_visible, (style, res)
        assert res.contrast_ratio < MIN_CONTRAST_RATIO, (style, res)
        control = analyze_anchor_visibility(text="Trap Link", style=style.replace("!important", "").replace("! IMPORTANT", ""))
        assert not control.is_visible
    indent = analyze_anchor_visibility(text="Trap", style="color:#000; background:#fff; text-indent: -9999px !important")
    assert indent.is_text_indent_disguise and not indent.is_visible, indent
    assert analyze_anchor_visibility(text="ok", style="color:#000 !important; background:#fff").is_visible
