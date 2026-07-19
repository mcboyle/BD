"""F3 (v3.66.50) — DOM-visibility honeypot filter, full P5-3.

Covers the three deltas built on top of the v3.66.28 P5-3 base:

  C1. CSS-hidden vocabulary expansion — off-screen ``transform`` and
      ``clip-path`` shapes added to ``HONEYPOT_CSS_HIDDEN``.
  C2. ``@media`` / ``@supports`` / ``@container`` awareness — rules
      hidden only inside a conditional at-rule are now consulted by
      the css-class matcher (the flat rule-block regex couldn't see
      into nested braces on its own).
  C3. ``strict`` mode — a computed-style probe (locator.evaluate)
      catches post-hydration hidden states that Playwright's
      ``is_visible()`` reports as visible (opacity:0, off-screen
      transform, clip-path, pointer-events:none).

The C3 probe's JavaScript itself is validated against a real browser
in the live-tests framework, not here; these unit tests drive the
probe through a mock ``evaluate`` and assert the wiring/fail-open
behaviour around it.
"""

import pytest

from bs4 import BeautifulSoup

from bulk_downloader.dom_honeypot import (
    DECOY_LINK_HIDDEN_PATTERNS,
    _flatten_at_rules,
    _STRICT_PROBE_JS,
    find_duplicate_field_decoys,
    is_link_decoy,
    is_link_decoy_playwright,
)
from bulk_downloader.deep_detect import HONEYPOT_CSS_HIDDEN


def _bs(html):
    return BeautifulSoup(html, "html.parser").find()


def _bs_all(html, tag):
    return BeautifulSoup(html, "html.parser").find_all(tag)


# --------------------------------------------------------------------------
# Mock locator with evaluate() support (the v3.66.28 mock lacks it)
# --------------------------------------------------------------------------

class _Loc:
    def __init__(self, *, visible=True, attrs=None, text="",
                 evaluate_result="", evaluate_raises=False,
                 visible_raises=False):
        self._visible = visible
        self._attrs = attrs or {}
        self._text = text
        self._evaluate_result = evaluate_result
        self._evaluate_raises = evaluate_raises
        self._visible_raises = visible_raises
        self.evaluate_calls = 0

    def is_visible(self, timeout=None):
        if self._visible_raises:
            raise RuntimeError("playwright crash")
        return self._visible

    def get_attribute(self, name):
        return self._attrs.get(name)

    def inner_text(self, timeout=None):
        return self._text

    def evaluate(self, js):
        self.evaluate_calls += 1
        if self._evaluate_raises:
            raise RuntimeError("evaluate failed")
        return self._evaluate_result


# ==========================================================================
# C1 — CSS-hidden vocabulary expansion
# ==========================================================================

class TestF3CssVocab:

    @pytest.mark.parametrize("pat", [
        "transform:translatex(-9999px)",
        "transform:translatex(-10000px)",
        "transform:translatey(-9999px)",
        "transform:translatey(-10000px)",
        "transform:scale(0)",
        "clip-path:circle(0)",
    ])
    def test_pattern_present(self, pat):
        assert pat in HONEYPOT_CSS_HIDDEN
        # DECOY_LINK_HIDDEN_PATTERNS aliases the same tuple.
        assert pat in DECOY_LINK_HIDDEN_PATTERNS

    def test_clip_path_inset_still_present(self):
        # Regression: the pre-F3 pattern must survive the expansion.
        assert "clip-path:inset(100%)" in HONEYPOT_CSS_HIDDEN

    def test_inline_transform_translatex_fires(self):
        el = _bs('<a href="/x" style="transform:translateX(-9999px)">dl</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is True
        assert reason == "inline_style_hidden"

    def test_inline_transform_scale_zero_fires(self):
        el = _bs('<a href="/x" style="transform:scale(0)">dl</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is True
        assert reason == "inline_style_hidden"

    def test_inline_clip_path_circle_fires(self):
        el = _bs('<a href="/x" style="clip-path:circle(0)">dl</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is True
        assert reason == "inline_style_hidden"

    def test_inline_with_whitespace_normalizes(self):
        # Authors write "transform: scale(0)" with spaces; the matcher
        # strips whitespace before substring-testing.
        el = _bs('<a href="/x" style="transform: scale(0)">dl</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is True
        assert reason == "inline_style_hidden"

    def test_benign_transform_not_flagged(self):
        # A small positive translate is a normal layout nudge, not a
        # honeypot — must not match any hidden pattern.
        el = _bs('<a href="/x" style="transform:translateX(2px)">dl</a>')
        is_decoy, reason = is_link_decoy(el)
        assert is_decoy is False
        assert reason == ""


# ==========================================================================
# C2 — @media / @supports / @container awareness
# ==========================================================================

class TestF3AtMediaFlatten:

    def test_flatten_noop_without_at_rule(self):
        css = ".trap{display:none}"
        assert _flatten_at_rules(css) == css

    def test_flatten_strips_media_opener(self):
        css = "@media (max-width:600px){.trap{display:none}}"
        out = _flatten_at_rules(css)
        assert "@media" not in out
        assert ".trap{display:none}" in out

    def test_flatten_strips_supports_opener(self):
        css = "@supports (display:grid){.trap{visibility:hidden}}"
        out = _flatten_at_rules(css)
        assert "@supports" not in out
        assert ".trap{visibility:hidden}" in out

    def test_media_hidden_class_fires(self):
        el = _bs('<a class="trap" href="/x">dl</a>')
        css = "@media (max-width:600px){.trap{display:none}}"
        is_decoy, reason = is_link_decoy(el, css)
        assert is_decoy is True
        assert reason == "css_class_hidden"

    def test_supports_hidden_class_fires(self):
        el = _bs('<a class="trap" href="/x">dl</a>')
        css = "@supports (clip-path:inset(0)){.trap{clip-path:inset(100%)}}"
        is_decoy, reason = is_link_decoy(el, css)
        assert is_decoy is True
        assert reason == "css_class_hidden"

    def test_second_media_block_fires(self):
        el = _bs('<a class="trap" href="/x">dl</a>')
        css = ("@media screen{.other{color:red}}"
               "@media print{.trap{display:none}}")
        is_decoy, reason = is_link_decoy(el, css)
        assert is_decoy is True
        assert reason == "css_class_hidden"

    def test_media_non_hiding_rule_does_not_fire(self):
        # A responsive rule that restyles but does NOT hide must not
        # be flagged — the false-positive guard.
        el = _bs('<a class="btn" href="/x">dl</a>')
        css = "@media (max-width:600px){.btn{color:blue;font-size:2em}}"
        is_decoy, reason = is_link_decoy(el, css)
        assert is_decoy is False
        assert reason == ""

    def test_top_level_rule_still_fires(self):
        # Regression: non-nested rules unaffected by the flatten.
        el = _bs('<a class="trap" href="/x">dl</a>')
        css = ".trap{display:none}"
        is_decoy, reason = is_link_decoy(el, css)
        assert is_decoy is True
        assert reason == "css_class_hidden"

    def test_media_hidden_duplicate_field_detected(self):
        # A same-name pair where the decoy is hidden only inside an
        # @media block, with a visible companion → reported.
        html = (
            '<form>'
            '<input name="email" type="text">'
            '<input name="email" class="trap" type="text">'
            '</form>'
        )
        inputs = _bs_all(html, "input")
        css = "@media (max-width:600px){.trap{display:none}}"
        decoys = find_duplicate_field_decoys(inputs, css)
        names = [d["name"] for d in decoys]
        assert "email" in names


# ==========================================================================
# C3 — strict-mode computed-style probe
# ==========================================================================

class TestF3StrictProbe:

    def test_probe_js_constant_shape(self):
        # Cheap guard that the probe reports the reason tokens the
        # docstring/tests rely on.
        for token in ("computed_opacity_zero",
                      "computed_pointer_events_none",
                      "computed_transform_scale_zero",
                      "computed_transform_offscreen",
                      "computed_clip_path"):
            assert token in _STRICT_PROBE_JS

    @pytest.mark.parametrize("reason", [
        "computed_opacity_zero",
        "computed_pointer_events_none",
        "computed_transform_offscreen",
        "computed_transform_scale_zero",
        "computed_clip_path",
    ])
    def test_strict_probe_fires(self, reason):
        loc = _Loc(visible=True, evaluate_result=reason)
        is_decoy, got = is_link_decoy_playwright(loc, strict=True)
        assert is_decoy is True
        assert got == reason
        assert loc.evaluate_calls == 1

    def test_strict_probe_empty_means_visible(self):
        loc = _Loc(visible=True, evaluate_result="")
        is_decoy, reason = is_link_decoy_playwright(loc, strict=True)
        assert is_decoy is False
        assert reason == ""
        assert loc.evaluate_calls == 1

    def test_strict_probe_fail_open_on_exception(self):
        loc = _Loc(visible=True, evaluate_raises=True)
        is_decoy, reason = is_link_decoy_playwright(loc, strict=True)
        assert is_decoy is False
        assert reason == ""

    def test_strict_false_skips_probe(self):
        # Without strict, evaluate must never be called even if it
        # would have fired.
        loc = _Loc(visible=True, evaluate_result="computed_opacity_zero")
        is_decoy, reason = is_link_decoy_playwright(loc, strict=False)
        assert is_decoy is False
        assert reason == ""
        assert loc.evaluate_calls == 0

    def test_strict_short_circuits_on_not_visible(self):
        # Cheap is_visible() already fired → probe must not run.
        loc = _Loc(visible=False, evaluate_result="computed_opacity_zero")
        is_decoy, reason = is_link_decoy_playwright(loc, strict=True)
        assert is_decoy is True
        assert reason == "not_visible"
        assert loc.evaluate_calls == 0

    def test_strict_short_circuits_on_trap_url(self):
        # A cheap URL-trap match fires before the probe.
        loc = _Loc(visible=True, attrs={"href": "/clk?id=1"},
                   evaluate_result="computed_opacity_zero")
        is_decoy, reason = is_link_decoy_playwright(loc, strict=True)
        assert is_decoy is True
        assert reason == "trap_url_term"
        assert loc.evaluate_calls == 0


# ==========================================================================
# C3 — dispatch wiring through detect.find_best_download
# ==========================================================================

class _MockLocatorList:
    def __init__(self, elements):
        self._elements = elements

    def all(self):
        return list(self._elements)

    def count(self):
        return len(self._elements)

    @property
    def first(self):
        return _MockLocatorList(self._elements[:1] if self._elements else [])

    def nth(self, i):
        return self._elements[i]


class _MockPage:
    def __init__(self, selector_map):
        self._map = selector_map

    def locator(self, sel):
        return _MockLocatorList(self._map.get(sel, []))


# find_best_download iterates locators via .all(); make a single _Loc
# behave as a one-element list too.
_Loc.all = lambda self: [self]
_Loc.count = lambda self: 1
_Loc.first = property(lambda self: self)


class TestF3StrictDispatch:
    """A candidate that is_visible() reports visible but whose
    computed style is hidden: dropped in strict, kept in cheap. This
    is the capability strict mode adds over cheap."""

    def _page(self):
        clean = _Loc(visible=True,
                     attrs={"href": "https://cdn.example.com/video.mp4"},
                     text="Download 1080p")
        # computed-hidden decoy: visible to is_visible, probe flags it.
        decoy = _Loc(visible=True,
                     attrs={"href": "https://cdn.example.com/decoy.mp4"},
                     text="Download HD",
                     evaluate_result="computed_opacity_zero")
        return _MockPage({"a[href*='.mp4']": [clean, decoy]}), clean, decoy

    def test_strict_drops_computed_hidden(self, monkeypatch):
        from bulk_downloader.detect import find_best_download
        monkeypatch.setenv("BD_DOM_HONEYPOT_FILTER", "strict")
        page, clean, decoy = self._page()
        result = find_best_download(page)
        assert result is not None
        texts = [c["text"] for c in result["_all_candidates"]]
        assert any("1080p" in t for t in texts)
        # candidate text concatenates inner_text + scanned attrs, so
        # the decoy's distinctive marker is its href ("decoy").
        assert not any("decoy" in t for t in texts)
        assert decoy.evaluate_calls >= 1

    def test_cheap_keeps_computed_hidden(self, monkeypatch):
        from bulk_downloader.detect import find_best_download
        monkeypatch.setenv("BD_DOM_HONEYPOT_FILTER", "cheap")
        page, clean, decoy = self._page()
        result = find_best_download(page)
        assert result is not None
        texts = [c["text"] for c in result["_all_candidates"]]
        # cheap path never calls the probe; the decoy survives.
        assert any("decoy" in t for t in texts)
        assert decoy.evaluate_calls == 0
