"""F11 (v3.66.50) — duplicate-field detection extracted to a standalone
module (bulk_downloader.duplicate_fields).

Verifies the new import surface, the lazy back-compat re-export through
dom_honeypot, and that the detection + input-visibility rules are
unchanged by the move.
"""

import pytest

from bs4 import BeautifulSoup

from bulk_downloader.duplicate_fields import (
    find_duplicate_field_decoys,
    _input_is_hidden,
)


def _inputs(html):
    return BeautifulSoup(html, "html.parser").find_all("input")


def _one(html):
    return BeautifulSoup(html, "html.parser").find("input")


class TestF11ImportSurface:

    def test_standalone_import(self):
        from bulk_downloader.duplicate_fields import (
            find_duplicate_field_decoys as f)
        assert callable(f)

    def test_backcompat_reexport_is_same_object(self):
        # The old location must still work and resolve to the exact
        # same function (lazy __getattr__ re-export, no copy).
        from bulk_downloader.dom_honeypot import (
            find_duplicate_field_decoys as old)
        from bulk_downloader.duplicate_fields import (
            find_duplicate_field_decoys as new)
        assert old is new

    def test_backcompat_private_helper_reexport(self):
        from bulk_downloader.dom_honeypot import _input_is_hidden as old
        assert old is _input_is_hidden

    def test_dom_honeypot_getattr_rejects_unknown(self):
        import bulk_downloader.dom_honeypot as dh
        with pytest.raises(AttributeError):
            dh.no_such_symbol


class TestF11Detection:

    def test_mixed_visible_hidden_reported(self):
        html = ('<form>'
                '<input name="email" type="text">'
                '<input name="email" type="text" style="display:none">'
                '</form>')
        decoys = find_duplicate_field_decoys(_inputs(html))
        assert decoys == [{"name": "email",
                           "reason": "duplicate_field_hidden_decoy"}]

    def test_all_visible_not_reported(self):
        # Genuine multi-step chunking: two visible same-name inputs.
        html = ('<form>'
                '<input name="q" type="text">'
                '<input name="q" type="text">'
                '</form>')
        assert find_duplicate_field_decoys(_inputs(html)) == []

    def test_all_hidden_not_reported(self):
        # Dual CSRF mirrors etc. — both hidden, no visible companion.
        html = ('<form>'
                '<input name="csrf" type="hidden">'
                '<input name="csrf" type="hidden">'
                '</form>')
        assert find_duplicate_field_decoys(_inputs(html)) == []

    def test_single_input_not_reported(self):
        html = '<form><input name="email" type="text"></form>'
        assert find_duplicate_field_decoys(_inputs(html)) == []

    def test_unnamed_inputs_ignored(self):
        html = ('<form>'
                '<input type="text">'
                '<input type="text" style="display:none">'
                '</form>')
        assert find_duplicate_field_decoys(_inputs(html)) == []

    def test_css_class_hidden_companion_reported(self):
        html = ('<form>'
                '<input name="user" type="text">'
                '<input name="user" class="trap" type="text">'
                '</form>')
        css = ".trap{visibility:hidden}"
        decoys = find_duplicate_field_decoys(_inputs(html), css)
        assert [d["name"] for d in decoys] == ["user"]

    def test_media_hidden_companion_reported(self):
        # Exercises the F3 @media flatten through the form path.
        html = ('<form>'
                '<input name="user" type="text">'
                '<input name="user" class="trap" type="text">'
                '</form>')
        css = "@media (max-width:600px){.trap{display:none}}"
        decoys = find_duplicate_field_decoys(_inputs(html), css)
        assert [d["name"] for d in decoys] == ["user"]


class TestF11InputIsHidden:

    def test_inline_style(self):
        assert _input_is_hidden(
            _one('<input style="display:none">')) is True

    def test_hidden_attribute(self):
        assert _input_is_hidden(_one('<input hidden>')) is True

    def test_type_hidden(self):
        assert _input_is_hidden(_one('<input type="hidden">')) is True

    def test_aria_hidden(self):
        assert _input_is_hidden(
            _one('<input aria-hidden="true">')) is True

    def test_tabindex_minus1_standalone(self):
        # Form-field rule: tabindex=-1 alone hides the input (unlike
        # the link rule where it's contributing-only).
        assert _input_is_hidden(_one('<input tabindex="-1">')) is True

    def test_css_class_hidden(self):
        el = _one('<input class="hide">')
        assert _input_is_hidden(el, ".hide{opacity:0}") is True

    def test_visible_input_not_hidden(self):
        assert _input_is_hidden(_one('<input type="text">')) is False
