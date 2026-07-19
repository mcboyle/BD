"""v3.66.195 derivation-correctness tests (SYNTHETIC fixtures only).

Four functionality fixes (deep-audit findings), each affecting how real content
sites derive templates:

  * SEL-class  — prefer the most-distinctive class (skip generic content words +
    js-/utility/state classes) instead of classes[0].
  * SEL-row    — class-anchored modal rows (a repeated <a class="..."> with an
    href, no role/no download attr) now derive a modal-scoped row candidate.
  * MOD-scope  — modal-scope recognition no longer treats common compound classes
    (loading-overlay / cookie-overlay / image-popup / *-trigger) as modal
    containers; modal/dialog/drawer/popover still match compounds (modal-dialog).
  * S1 lint    — ambiguous nav words (browse/category/home/search/account/
    settings) downgrade to a non-blocking WARN so legit content-listing selectors
    promote; hard chrome (nav/navbar/login/header/footer) stays a blocking ERROR.
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "tools"))

import build_template_from_wacz as b  # noqa: E402
from bulk_downloader import selector_lint as sl  # noqa: E402


def _node(tag, attrs=None, kids=None, text=None):
    n = {"type": 2, "tagName": tag, "attributes": attrs or {}, "childNodes": kids or []}
    if text is not None:
        n["childNodes"] = [{"type": 3, "textContent": text}]
    return n


# ── SEL-class: distinctive class preferred ──────────────────────────────────

def test_selclass_prefers_distinctive_over_generic():
    assert b._selector_for_element(_node("a", {"class": "card video-thumbnail"})) == "a.video-thumbnail"
    assert b._selector_for_element(_node("a", {"class": "box clip-tile"})) == "a.clip-tile"


def test_selclass_skips_js_and_utility_classes():
    assert b._selector_for_element(_node("a", {"class": "js-foo scene-card"})) == "a.scene-card"
    assert b._selector_for_element(_node("a", {"class": "is-active product-link"})) == "a.product-link"


def test_selclass_all_generic_falls_through_not_overbroad():
    # an element whose only class is generic must NOT derive a too-broad a.item;
    # it falls through to role/tag instead
    assert b._selector_for_element(_node("a", {"class": "item"})) == "a"
    assert b._selector_for_element(_node("div", {"class": "card"})) == "div"


def test_selclass_id_and_role_preference_unchanged():
    assert b._selector_for_element(_node("a", {"id": "dl-btn", "class": "card"})) == "#dl-btn"
    assert b._selector_for_element(_node("a", {"role": "button", "class": "item"})) == 'a[role="button"]'


# ── SEL-row: class-anchored modal rows ──────────────────────────────────────

def test_selrow_class_anchored_rows_derive_scoped_candidate():
    rows = [_node("a", {"class": "scene-link", "href": f"/scene/{i}"}, text=f"Scene {i}") for i in range(4)]
    modal = _node("div", {"class": "modal", "role": "dialog"}, kids=rows)
    dom = [{"type": "full_snapshot", "data": {"node": _node("body", kids=[modal])}}]
    out = b._modal_row_selectors_from_dom(dom)
    assert any(s == '[role="dialog"] a.scene-link' or s == ".modal a.scene-link" for s in out), out


def test_selrow_requires_repeat_and_distinctive_class():
    # a single (non-repeated) class anchor is NOT a row; a generic-class anchor is NOT emitted
    one = _node("a", {"class": "scene-link", "href": "/scene/1"})
    generic = [_node("a", {"class": "item", "href": f"/x/{i}"}) for i in range(3)]
    modal = _node("div", {"class": "modal"}, kids=[one] + generic)
    dom = [{"type": "full_snapshot", "data": {"node": _node("body", kids=[modal])}}]
    out = b._modal_row_selectors_from_dom(dom)
    assert not any("scene-link" in s for s in out)   # only one occurrence
    assert not any(s.endswith("a.item") for s in out)  # generic class not emitted


# ── MOD-scope: no false modal containers ────────────────────────────────────

def test_modscope_compound_non_modal_classes_rejected():
    for cls in ("loading-overlay", "cookie-overlay", "image-popup", "video-lightbox-trigger"):
        assert b._modal_scope_tokens({"class": cls}) == [], cls
        assert b._modal_scope_token({"class": cls}) is None, cls


def test_modscope_modal_compounds_still_match():
    # modal/dialog/drawer/popover stems still match compounds (bootstrap etc.)
    assert b._modal_scope_token({"class": "modal-dialog"}) == ".modal"
    assert b._modal_scope_token({"class": "modal-content"}) == ".modal"


def test_modscope_bare_library_and_standalone_tokens_match():
    for cls in ("lightbox", "fancybox", "colorbox", "popup", "overlay"):
        assert b._modal_scope_tokens({"class": cls}) == ["." + cls], cls


def test_modscope_specific_precedence_no_phantom():
    toks = b._modal_scope_tokens({"class": "ant-modal", "role": "dialog"})
    assert '[role="dialog"]' in toks and ".ant-modal" in toks and ".modal" not in toks


# ── S1 lint: soft nav words don't block ─────────────────────────────────────

def test_s1_content_listing_selectors_not_blocking():
    for s in (".category .item a", ".browse .clip a", "#home .episode a"):
        issues = sl.lint_selector(s, role="row")
        assert not sl.has_blocking_issues(issues), (s, issues)


def test_s1_hard_chrome_still_blocks():
    for s in (".navbar .login a", ".site-footer a", ".masthead a"):
        issues = sl.lint_selector(s, role="row")
        assert sl.has_blocking_issues(issues), (s, issues)


def test_s1_soft_nav_still_carries_code():
    # account/search/settings still surface a (now non-blocking) nav_selector code
    codes = [i.code for i in sl.lint_selector('[aria-label="Account settings"]', role="trigger")]
    assert "nav_selector" in codes
