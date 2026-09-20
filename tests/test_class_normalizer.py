"""Cut 948: Dynamic CSS Class Hash Normalizer and Unminifier.

Row 948 Acceptance criteria:
(1) normalization of compiled class names
(2) resilient wildcard selector compilation
(3) stability across simulated builds
"""

from __future__ import annotations

import re
import pytest

try:
    from bulk_downloader.class_normalizer import (
        NormalizedClass,
        normalize_class_name,
        normalize_class_list,
        compile_wildcard_selector,
        compile_element_selector,
        extract_stable_prefix,
        is_hashed_class,
    )
except ImportError:
    # Stubs for clean collection and true behavioral RED assertions on base
    NormalizedClass = None  # type: ignore

    def normalize_class_name(class_name: str, tag: str | None = None) -> object:  # type: ignore
        return None

    def normalize_class_list(class_list: str | list[str], tag: str | None = None) -> list:  # type: ignore
        return []

    def compile_wildcard_selector(class_name: str, tag: str | None = None, match_type: str = "contains") -> str:  # type: ignore
        return ""

    def compile_element_selector(class_names: str | list[str], tag: str | None = None) -> str:  # type: ignore
        return ""

    def extract_stable_prefix(class_name: str) -> str:  # type: ignore
        return ""

    def is_hashed_class(class_name: str) -> bool:  # type: ignore
        return False

BD_GATE_SCOPE = "module"


def test_normalization_of_compiled_css_modules():
    """AC 1: Normalize CSS Modules class names (e.g. component_element__hash)."""
    norm = normalize_class_name("button_primaryBtn__3x7fA")
    assert norm is not None
    assert norm.prefix == "button_primaryBtn"
    assert norm.hash_part == "3x7fA"
    assert norm.framework == "css-modules"
    assert extract_stable_prefix("button_primaryBtn__3x7fA") == "button_primaryBtn"
    assert is_hashed_class("button_primaryBtn__3x7fA") is True


def test_normalization_of_styled_components_and_emotion():
    """AC 1: Normalize Styled-Components and Emotion dynamic hashes."""
    # Styled Components: sc-componentId hash
    sc_norm = normalize_class_name("sc-bdVaJa")
    assert sc_norm is not None
    assert sc_norm.framework == "styled-components"
    assert sc_norm.prefix == "sc-bdVaJa"

    # Emotion: css-<hash> carries NO stable part (REFUTE E2); css-<hash>-<Label> keeps the label
    emotion_norm = normalize_class_name("css-1a2b3c4")
    assert emotion_norm is not None
    assert emotion_norm.framework == "emotion"
    assert emotion_norm.prefix == ""
    assert emotion_norm.hash_part == "1a2b3c4"
    assert emotion_norm.wildcard_selector == ""  # UNSTABLE: never '[class*="css-"]'
    labelled = normalize_class_name("css-1a2b3c4-MyButton")
    assert (labelled.framework, labelled.prefix, labelled.hash_part) == ("emotion", "MyButton", "1a2b3c4")

    # Plain un-hashed class
    plain_norm = normalize_class_name("nav-item")
    assert plain_norm.prefix == "nav-item"
    assert plain_norm.hash_part is None
    assert is_hashed_class("nav-item") is False


def test_resilient_wildcard_selector_compilation():
    """AC 2: Compile resilient wildcard selector for CSS-in-JS classes."""
    # CSS Modules with tag
    sel1 = compile_wildcard_selector("button_primaryBtn__3x7fA", tag="button")
    assert sel1 == 'button[class^="button_primaryBtn__"], button[class*=" button_primaryBtn__"]'

    # Plain selector without tag
    sel2 = compile_wildcard_selector("card_header___2K9x")
    assert sel2 == '[class^="card_header___"], [class*=" card_header___"]'

    # Starts-with match type (a token prefix is what every form is anchored to)
    sel3 = compile_wildcard_selector("video-player-container__8a9b", match_type="starts_with")
    assert sel3 == '[class^="video-player-container__"], [class*=" video-player-container__"]'

    # Unhashed class falls back to standard dot selector
    sel_plain = compile_wildcard_selector("container", tag="div")
    assert sel_plain == "div.container"


def test_multiple_class_list_normalization():
    """Normalize space-separated class attribute or list of classes."""
    classes = "button_btn__8f2a1 theme-dark sc-htpNat gXqZpB"
    normalized_list = normalize_class_list(classes)
    assert len(normalized_list) == 4

    prefixes = [n.prefix for n in normalized_list]
    assert "button_btn" in prefixes
    assert "theme-dark" in prefixes
    assert "sc-htpNat" in prefixes

    compound_sel = compile_element_selector(classes, tag="button")
    assert compound_sel == 'button[class^="button_btn__"], button[class*=" button_btn__"]'


def test_stability_across_simulated_builds():
    """AC 3: Verify wildcard selectors maintain stability across simulated builds.

    Simulates 3 successive site builds where hash suffixes change randomly.
    Verifies that the compiled resilient selector matches all builds while
    static class matching fails.
    """
    build1_class = "article_body__a1b2c3"
    build2_class = "article_body__d4e5f6"
    build3_class = "article_body__x9y8z7"

    # Compile selector from Build 1
    resilient_selector = compile_wildcard_selector(build1_class, tag="div")
    assert resilient_selector == 'div[class^="article_body__"], div[class*=" article_body__"]'

    # Mock DOM element simulator: the token-prefix form [class^="value"] anchors every build's class
    m = re.search(r'\[class\^="([^"]+)"\]', resilient_selector)
    assert m is not None, f"Selector {resilient_selector} missing attribute wildcard pattern"
    pattern = re.compile("^" + re.escape(m.group(1)))

    for build_class in (build1_class, build2_class, build3_class):
        assert pattern.search(build_class) is not None, (
            f"Resilient selector failed to match {build_class} across builds"
        )


def test_resilience_on_empty_and_special_chars():
    """Handle edge cases: empty strings, numbers, BEM modifiers."""
    assert normalize_class_name("") is not None
    assert compile_wildcard_selector("") == ""

    # BEM with hash suffix: block__elem--mod-hash
    bem_norm = normalize_class_name("menu__item--active-a9b8c")
    assert bem_norm.prefix == "menu__item--active"
    assert bem_norm.hash_part == "a9b8c"


# ── correctness REFUTE E1-E4 (2026-09-20): selectors evaluated in REAL Chromium, exact element sets ──

_FIXTURE_HTML = """<html><body>
<div id="tw" class="flex md:flex hover:bg-red-500 w-1/2"></div>
<div id="tw2" class="flex"></div>
<div id="em" class="css-1a2b3c4-MyButton"></div>
<div id="em2" class="css-9z8y7x6-Other"></div>
<span id="emx" class="css-a1b2c3d"></span>
<div id="bem" class="menu__item"></div>
<div id="beml" class="menu__link"></div>
<div id="bemt" class="menu__title"></div>
<div id="card" class="card-7f8a9b2c"></div>
<div id="discard" class="discard-btn"></div>
<div id="cm" class="button_btn__3x7fA other"></div>
<div id="cm2" class="x button_btn__9Q2zz"></div>
<div id="cmx" class="notbutton_btn__3x7fA"></div>
<div id="bg" class="bg-ff0000"></div>
</body></html>"""


def _ids_matching(page, selector: str) -> list[str]:
    return page.evaluate("s => Array.from(document.querySelectorAll(s)).map(e => e.id)", selector)


def test_real_chromium_compiled_selectors_match_exactly_their_elements():
    """Each compiled selector, run through querySelectorAll on a real page, matches exactly the
    elements carrying that class family -- never throws (E1), never matches everything (E2),
    never swallows a BEM element (E3), never a same-substring class (E4)."""
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(_FIXTURE_HTML)
        # E1: Tailwind variant/fraction classes are valid, exact-token selectors
        assert _ids_matching(page, compile_wildcard_selector("md:flex")) == ["tw"]
        assert _ids_matching(page, compile_wildcard_selector("w-1/2")) == ["tw"]
        assert _ids_matching(page, compile_element_selector("md:flex hover:bg-red-500 w-1/2")) == ["tw"]
        # E2: the Emotion label is the stable part; a labelless hash yields no selector at all
        assert _ids_matching(page, compile_wildcard_selector("css-1a2b3c4-MyButton")) == ["em"]
        assert _ids_matching(page, compile_wildcard_selector("css-0000000-MyButton")) == ["em"]  # rebuilt hash
        assert compile_wildcard_selector("css-a1b2c3d") == ""
        assert compile_element_selector("css-a1b2c3d") == ""
        # E3: a plain BEM element is a stable class, matched exactly (not '[class*="menu__"]')
        assert _ids_matching(page, compile_wildcard_selector("menu__item")) == ["bem"]
        # E4: token-anchored generic hash -- 'discard-btn' is not 'card-'
        assert _ids_matching(page, compile_wildcard_selector("card-7f8a9b2c")) == ["card"]
        assert _ids_matching(page, compile_wildcard_selector("bg-ff0000")) == ["bg"]  # hex colour utility: plain
        # CSS Modules across builds, token-anchored ('notbutton_btn__' excluded)
        assert _ids_matching(page, compile_wildcard_selector("button_btn__3x7fA")) == ["cm", "cm2"]
        browser.close()
    finally:
        pw.stop()
