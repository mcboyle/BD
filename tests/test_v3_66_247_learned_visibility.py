"""v3.66.247 — find_best_download learned fast-path must skip non-visible matches.

A learned ``row_selector`` can match elements present in the DOM but not visible
— the canonical case is a modal-scoped discriminating row while the modal is
CLOSED. Before this fix the learned fast-path scored those hidden elements and
returned one with ``_via_learned=True`` — an unclickable winner that:
  * produces a false drift "hit" (Phase 5.8 hit/miss counters),
  * wastes the full ``expect_download`` timeout on an element that can't be
    clicked, and
  * is misdiagnosable (looks matched; never downloads).

Fix: score only ``is_visible()`` matches; if a selector matches but nothing is
visible, treat it as a miss and fall through (clean no-learned-hit) instead of
returning a hidden row.

Unit tests over a mock page (same pattern as
``test_v3_66_50_f3_dom_visibility.py``). Live-browser behaviour is exercised by
the in-sandbox cloakbrowser sweep, not here.
"""

from bulk_downloader.detect import find_best_download


class _Loc:
    def __init__(self, *, visible=True, attrs=None, text="", visible_raises=False):
        self._visible = visible
        self._attrs = attrs or {}
        self._text = text
        self._visible_raises = visible_raises

    def is_visible(self, timeout=None):
        if self._visible_raises:
            raise RuntimeError("playwright crash")
        return self._visible

    def get_attribute(self, name):
        return self._attrs.get(name)

    def inner_text(self, timeout=None):
        return self._text


class _LocList:
    def __init__(self, elements):
        self._elements = list(elements)

    def all(self):
        return list(self._elements)

    def count(self):
        return len(self._elements)

    @property
    def first(self):
        return _LocList(self._elements[:1])

    def nth(self, i):
        return self._elements[i]


class _Page:
    def __init__(self, selector_map):
        self._map = selector_map

    def locator(self, sel):
        return _LocList(self._map.get(sel, []))


SEL = '[role="dialog"] a.ct_dl_button[data-framerate]'


class TestLearnedVisibility:
    def test_hidden_only_yields_no_learned_hit(self):
        # Modal closed: the discriminating row exists but is not visible, and
        # there is no other download-shaped candidate on the page. The learned
        # path must not return the hidden row; the empty wide sweep -> None.
        hidden = _Loc(visible=False,
                      attrs={"data-framerate": "30"},
                      text="1080p")
        page = _Page({SEL: [hidden]})
        result = find_best_download(page, learned={"row_selectors": [SEL]})
        assert result is None

    def test_visible_match_still_returns_via_learned(self):
        # Modal open: row visible -> learned fast-path must still hit.
        visible = _Loc(visible=True,
                       attrs={"data-framerate": "30"},
                       text="1080p")
        page = _Page({SEL: [visible]})
        result = find_best_download(page, learned={"row_selectors": [SEL]})
        assert result is not None
        assert result.get("_via_learned") is True
        assert "1080" in result["text"]

    def test_visible_chosen_over_hidden_higher_res_decoy(self):
        # A hidden higher-res decoy must not win over (nor even be scored
        # alongside) a visible legit row.
        decoy = _Loc(visible=False, attrs={"data-framerate": "60"}, text="2160p")
        legit = _Loc(visible=True, attrs={"data-framerate": "30"}, text="1080p")
        page = _Page({SEL: [decoy, legit]})
        result = find_best_download(page, learned={"row_selectors": [SEL]})
        assert result is not None
        assert result.get("_via_learned") is True
        assert "2160" not in result["text"]
        cand_text = " ".join(c["text"] for c in result.get("_all_candidates", []))
        assert "2160" not in cand_text

    def test_is_visible_raising_is_treated_as_skip(self):
        # If is_visible() raises, the element is skipped (fail-closed,
        # consistent with the existing per-element try/except), not returned.
        boom = _Loc(visible_raises=True, attrs={"data-framerate": "30"}, text="1080p")
        page = _Page({SEL: [boom]})
        result = find_best_download(page, learned={"row_selectors": [SEL]})
        assert result is None
