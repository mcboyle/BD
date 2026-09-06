"""Row 759 -- a COMPUTED-HIDDEN quality cell must never be the quality winner.

THE OPERATOR-VISIBLE SHAPE. A Gamma scene's Download control is an hrefless
``<div>``; clicking it appends a quality modal. The modal is built for every
breakpoint at once, so a resolution CELL that is rendered at NO breakpoint is
still in the DOM -- ``2160p`` present, ``is_visible()`` False -- beside the
live quality LABEL the operator can actually click. ``find_best_download``'s
wide sweep harvested both and ranked on ``res_score`` alone, so the cell that
cannot be clicked outscored the label that can, and the run ended
"Clicked but no download started".

THE ASYMMETRY THIS CLOSES. detect.py already refuses an invisible element on
its OTHER two admission paths -- the learned row-selector loop since v3.66.247
(``if not el.is_visible(): continue``, pinned by
tests/test_v3_66_247_learned_visibility.py) and ``_resolve_taught_control``
since row 486. The WIDE SWEEP had no such decision. This gate is the wide-sweep
twin of test_v3_66_247_learned_visibility.py and is deliberately built on the
same mock-page pattern (no browser, no network, no site contact).

PRECONDITIONS ARE ASSERTED, NOT ASSUMED. ``test_the_fixture_contains_the_hazard``
proves the hidden cell really does outscore the visible label on the shipped
scorer, so a green verdict here can never come from a fixture that lost the
divergence it exists to establish, and ``test_visibility_is_consulted_exactly_
once_per_admitted_candidate`` proves the admission FIRED rather than being
skipped past.

The refusal is narrow in both directions: it deletes a candidate only when
Playwright REPORTS it not visible, never when visibility cannot be measured
(``test_a_locator_whose_visibility_raises_is_still_admitted``), and it changes
nothing about how visible candidates are ranked
(``test_a_visible_resolution_cell_still_wins_normally``).
"""

# The gate parses a module-level ASSIGNMENT, not a docstring line.
BD_GATE_SCOPE = "module"

from bulk_downloader.detect import find_best_download, res_score


class _Loc:
    """A Playwright-locator stub: only what the wide sweep actually calls.

    It deliberately exposes NO ``evaluate`` and NO ``locator``, which is how
    the shipped fail-open paths (``_candidate_has_own_affordance``,
    ``_is_wrapper_not_control``) behave for a stub -- the candidate is kept.
    ``visible_calls`` records that the admission under test ran at all.
    """

    def __init__(self, *, text, visible=True, visible_raises=False, attrs=None):
        self._text = text
        self._visible = visible
        self._visible_raises = visible_raises
        self._attrs = attrs or {}
        self.visible_calls = 0
        self.text_calls = 0

    def is_visible(self, timeout=None):
        self.visible_calls += 1
        if self._visible_raises:
            raise RuntimeError("playwright: element handle is detached")
        return self._visible

    def inner_text(self, timeout=None):
        # Harvest evidence that does NOT depend on this cut: gather_text reads
        # inner_text on every element the wide sweep reaches, on the base tree
        # and on the fixed one alike. The preconditions below are asserted
        # through this counter for exactly that reason.
        self.text_calls += 1
        return self._text

    def get_attribute(self, name):
        return self._attrs.get(name)


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
    """The modal AFTER the Download div was clicked: quality rows on screen."""

    def __init__(self, selector_map, url="https://scenes.example/scene/1234"):
        self._map = selector_map
        self.url = url

    def locator(self, sel):
        return _LocList(self._map.get(sel, []))


# The Gamma quality menu reduced to its load-bearing shape: the operator-visible
# label span and the responsive-duplicate resolution cell, both reachable by the
# wide sweep's `[role='menuitem']` selector, in DOM order (label first, so a
# battery that kills the SCORING call cannot land on the right answer by
# accident of ordering).
_LABEL_TEXT = "1080p FHD"
_HIDDEN_CELL_TEXT = "2160p"


def _modal(*, cell_visible=False, cell_raises=False):
    label = _Loc(text=_LABEL_TEXT, visible=True)
    cell = _Loc(text=_HIDDEN_CELL_TEXT, visible=cell_visible,
                visible_raises=cell_raises)
    page = _Page({"[role='menuitem']": [label, cell]})
    return page, label, cell


def _candidate_texts(result):
    return [c["text"] for c in (result or {}).get("_all_candidates", [])]


class TestTheFixtureItself:
    def test_the_fixture_contains_the_hazard(self):
        # The divergence this gate exists to catch must actually be present:
        # the cell the operator cannot see scores HIGHER than the label they
        # can. Delete this divergence and every assertion below is vacuous.
        assert res_score(_HIDDEN_CELL_TEXT) > res_score(_LABEL_TEXT) > 0

    def test_visibility_is_consulted_exactly_once_per_admitted_candidate(self):
        # The admission FIRED, and fired once per harvested candidate -- not
        # zero times (no decision at all) and not once per selector.
        page, label, cell = _modal()
        find_best_download(page)
        assert (label.text_calls, cell.text_calls) == (1, 1), (
            "the fixture did not harvest exactly the two rows it builds")
        assert (label.visible_calls, cell.visible_calls) == (1, 1)
        # The decision runs on the unmeasurable case too -- it is a refusal
        # that was CONSIDERED and declined, not a branch that was skipped.
        page, _label, boom = _modal(cell_raises=True)
        find_best_download(page)
        assert boom.visible_calls == 1


class TestHiddenCellIsNotACandidate:
    def test_a_hidden_resolution_cell_is_not_the_quality_winner(self):
        page, _label, cell = _modal()
        result = find_best_download(page)
        assert result is not None, "the visible quality label was not admitted"
        assert cell.text_calls > 0, "the hidden cell was never harvested"
        assert _HIDDEN_CELL_TEXT not in result["text"], (
            "a hidden 2160p resolution cell became the quality winner")
        assert "1080" in result["text"]

    def test_a_hidden_resolution_cell_scores_zero_but_is_still_a_candidate(self):
        # _all_candidates is what _apply_quality_preference chooses from, and
        # it chooses on SCORE. The refusal is exactly that score: the hidden
        # cell must carry 0, so it can never be preferred over the visible
        # label -- and it must still BE in the list, because deleting an
        # invisible control is v3.66.28 P5-3's separate, default-OFF decision
        # and an invisible-but-real trigger is still clickable.
        page, _label, _cell = _modal()
        result = find_best_download(page)
        cands = (result or {}).get("_all_candidates") or []
        hidden = [c for c in cands if _HIDDEN_CELL_TEXT in c["text"]]
        assert len(hidden) == 1, (
            "the hidden cell was deleted rather than de-scored -- that is "
            f"P5-3's decision, not row 759's: {[c['text'] for c in cands]}")
        assert hidden[0]["score"] == 0, (
            "a hidden 2160p resolution cell kept a resolution score: "
            f"{hidden[0]['score']}")
        visible = [c for c in cands if _LABEL_TEXT in c["text"]]
        assert len(visible) == 1 and visible[0]["score"] > 0, (
            "the visible label lost its score too -- the refusal is not "
            f"narrow: {[(c['text'], c['score']) for c in cands]}")


class TestTheRefusalIsNarrow:
    def test_a_visible_resolution_cell_still_wins_normally(self):
        # NEGATIVE CONTROL. Nothing about ranking changes for a cell the
        # operator CAN see: the 2160p row still beats the 1080p label, and it
        # still does so on the scorer rather than on DOM order (it is second).
        page, _label, _cell = _modal(cell_visible=True)
        result = find_best_download(page)
        assert result is not None
        assert _HIDDEN_CELL_TEXT in result["text"], (
            "a VISIBLE 2160p cell stopped winning: "
            f"{result['text']!r} won instead")
        assert sorted(_candidate_texts(result)) == sorted(
            [_LABEL_TEXT, _HIDDEN_CELL_TEXT])

    def test_a_locator_whose_visibility_raises_is_still_admitted(self):
        # NEGATIVE CONTROL. Unmeasurable visibility is not evidence of hiding.
        # The wide sweep is the last-resort path, and deleting a control here
        # because a handle went detached would cost a real download -- the same
        # fail-open _is_wrapper_not_control and the honeypot filter already take.
        page, _label, cell = _modal(cell_raises=True)
        result = find_best_download(page)
        assert cell.text_calls == 1, "the raising cell was never harvested"
        texts = _candidate_texts(result)
        assert any(_HIDDEN_CELL_TEXT in t for t in texts), (
            "a candidate whose visibility could not be measured was deleted: "
            f"{texts}")
        # PRESENCE IS NOT THE PROPERTY UNDER TEST. The refusal is a DE-SCORE,
        # never a deletion, so an assertion about membership alone cannot fail
        # however the except arm behaves -- it would pass just as happily on a
        # fail-CLOSED arm that costs the operator the correct quality. Pin the
        # SCORE, which is what the docstring actually claims and what
        # _apply_quality_preference actually chooses on.
        raising = [c for c in (result or {}).get("_all_candidates") or []
                   if _HIDDEN_CELL_TEXT in c["text"]]
        assert len(raising) == 1
        assert raising[0]["score"] == res_score(_HIDDEN_CELL_TEXT), (
            "unmeasurable visibility de-scored a candidate: "
            f"{raising[0]['score']}")
        assert _HIDDEN_CELL_TEXT in result["text"], (
            "the candidate whose visibility could not be measured stopped "
            "winning")

    def test_a_page_of_visible_labels_only_is_untouched(self):
        # NEGATIVE CONTROL. No hidden element anywhere: the winner, the
        # candidate list and their order are what they were before this cut.
        page = _Page({"[role='menuitem']": [
            _Loc(text="720p", visible=True),
            _Loc(text=_LABEL_TEXT, visible=True),
        ]})
        result = find_best_download(page)
        assert result is not None
        assert result["text"] == _LABEL_TEXT
        assert sorted(_candidate_texts(result)) == sorted(["720p", _LABEL_TEXT])


class TestEveryWideSweepEntryPoint:
    """``add()`` is reached from four places; the refusal must cover them all.

    Sections 1 (explicit media links), 2 (general clickables) and 3 (data-*
    resolution markers) each call ``add`` with their own harvest, and section 4
    (the ancestor walk) calls the SAME ``add`` with a promoted ancestor. A fix
    wired into one section's loop instead of into ``add`` would pass the tests
    above and still ship the defect through the other three, so each reachable
    entry point is exercised here with the same hidden cell.
    """

    def test_section_1_direct_media_link_hidden_is_refused(self):
        hidden = _Loc(text="2160p", visible=False,
                      attrs={"href": "https://cdn.example/dl/2160.mp4"})
        visible = _Loc(text="1080p", visible=True,
                       attrs={"href": "https://cdn.example/dl/1080.mp4"})
        page = _Page({"a[href*='.mp4']": [visible, hidden]})
        result = find_best_download(page)
        assert result is not None
        assert hidden.visible_calls == 1
        assert "2160" not in result["text"], (
            "a hidden 2160p resolution cell became the quality winner "
            "through the direct-media-link sweep")

    def test_section_3_data_resolution_marker_hidden_is_refused(self):
        hidden = _Loc(text="2160p", visible=False, attrs={"data-res": "2160"})
        visible = _Loc(text="1080p", visible=True, attrs={"data-res": "1080"})
        page = _Page({"[data-res]": [visible, hidden]})
        result = find_best_download(page)
        assert result is not None
        assert hidden.visible_calls == 1
        assert "2160" not in result["text"], (
            "a hidden 2160p resolution cell became the quality winner "
            "through the data-attribute sweep")
