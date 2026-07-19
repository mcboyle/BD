"""v3.66.194 derivation-quality tests (SYNTHETIC fixtures only).

Covers two non-guard builder improvements landed in 194:

  * MOD-tokens — the modal-scope recognizer now also recognizes the common
    non-ARIA modal class families (lightbox / overlay / popup / fancybox /
    colorbox) in addition to modal / dialog / drawer / popover, while keeping
    the specific-scope precedence (role=dialog / ant-modal / MuiDialog) and the
    exact-class-token rule (a .ant-modal must not also emit a phantom .modal).

  * NET-year — a bare 4-digit year segment (1900-2099) is treated as a STATIC
    archive marker (kept literal), not a variable id, so a date archive path no
    longer over-parametrizes to a phantom {*_id}. A non-year numeric segment
    (e.g. movie/9) stays variable.
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "tools"))

import build_template_from_wacz as btw  # noqa: E402


# ── MOD-tokens ────────────────────────────────────────────────────────────────

def test_modal_tokens_new_class_families_recognized():
    for tok in ("lightbox", "overlay", "popup", "fancybox", "colorbox"):
        toks = btw._modal_scope_tokens({"class": tok})
        assert toks == ["." + tok], (tok, toks)
        # single-token form recognizes them too
        assert btw._modal_scope_token({"class": tok}) == "." + tok, tok


def test_modal_tokens_original_families_still_recognized():
    for tok in ("modal", "dialog", "drawer", "popover"):
        assert btw._modal_scope_tokens({"class": tok}) == ["." + tok], tok


def test_modal_specific_scope_precedence_no_phantom_token():
    # an ant-modal that also carries role=dialog emits the specific scopes only,
    # never a phantom generic ".modal"
    toks = btw._modal_scope_tokens({"class": "ant-modal", "role": "dialog"})
    assert '[role="dialog"]' in toks
    assert ".ant-modal" in toks
    assert ".modal" not in toks


def test_modal_non_modal_class_yields_no_scope():
    assert btw._modal_scope_tokens({"class": "scene-row video-card"}) == []
    assert btw._modal_scope_token({"class": "scene-row video-card"}) is None


# ── NET-year ────────────────────────────────────────────────────────────────

def test_year_segment_is_static_not_variable():
    for yr in ("1900", "1999", "2024", "2099"):
        assert btw._is_variable_seg(yr) is False, yr


def test_non_year_numeric_segment_stays_variable():
    assert btw._is_variable_seg("9") is True
    assert btw._is_variable_seg("42") is True
    # year-shaped but out of the 1900-2099 window is still a normal numeric id
    assert btw._is_variable_seg("3001") is True
    assert btw._is_variable_seg("1899") is True


def test_movie_id_segment_still_parametrized():
    # the canonical "movie/9 still {movie_id}" case is unaffected by the year guard
    assert btw._param_for_segment("9", "movie") == "{movie_id}"


def test_resolution_segment_still_recognized():
    # 1080 is not a 1900-2099 year, so resolution derivation is unchanged
    assert btw._is_variable_seg("1080") is True
    assert btw._param_for_segment("1080", "download-resolution") == "{resolution}"
