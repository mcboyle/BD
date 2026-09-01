"""Row 486 -- an authoritative taught row is not deleted for holding a control.

The learned scoring loop ran the wrapper guard UNCONDITIONALLY, one line before
the signal gate, so it also deleted a taught row that
``_learned_candidate_requires_signal`` had just measured as fully
authoritative.  ``_is_wrapper_not_control`` calls an element a wrapper whenever
it has no affordance of its own AND contains at least one control descendant --
which is the ordinary shape of an operator-taught quality ROW whose click
target is a nested button.

Consequence on the fall-through arm: ``runner`` credits ``download_misses`` and
a per-selector miss against the operator's own reviewed selector and calls
``_maybe_demote_selectors``, which DROPS that selector at 6 misses with 0 hits.
On the None arm the job has no candidate at all.  Either way the deletion was a
bare ``continue`` with no counter and no reason.

CONTRACT: a taught row that is not itself a control is RESOLVED to the single
control it contains, not deleted; where its click target cannot be identified
unambiguously the drop is COUNTED and reported rather than silent; and where
wrapper status cannot be measured at all the taught candidate is KEPT.
"""

BD_GATE_SCOPE = "module"

from contextlib import contextmanager

import pytest

from bulk_downloader.detect import (
    _CONTROL_DESCENDANT_SEL,
    _candidate_has_own_affordance,
    _learned_candidate_requires_signal,
    find_best_download,
    res_score,
)


def _tier_rows(control_markup):
    # The whitespace between the label and the control is load-bearing: without
    # it inner_text reads "2160pGet" and res_score's ``\d{3,4}\s*p\b`` never
    # matches, so every row would tie at 0 and the assertions below would be
    # about sort stability rather than about tiers.
    rows = "".join(
        f'<li class="quality-row"><span class="tier">{tier}</span> '
        f'{control_markup.format(tier=tier)}</li>'
        for tier in ("720p", "1080p", "2160p"))
    return f'<!doctype html><html><body><ul class="quality-list">{rows}' \
           "</ul></body></html>"


# Each taught row carries the TIER LABEL and holds exactly one nested control.
_NESTED_BUTTON = (
    '<button class="pick" data-href="https://cdn.example/dl/{tier}.mp4">'
    "Get</button>")
_NESTED_CHEVRON = '<a class="chev" href="/pick/{tier}">&rsaquo;</a>'
_NO_NESTED_CONTROL = '<span class="chev">&rsaquo;</span>'

# A taught row whose click target is genuinely ambiguous: two controls.
_AMBIGUOUS = """<!doctype html><html><body><ul class="quality-list">
  <li class="quality-row"><span class="tier">2160p</span>
    <button class="pick" data-href="https://cdn.example/dl/a.mp4">A</button>
    <button class="pick" data-href="https://cdn.example/dl/b.mp4">B</button>
  </li>
</ul></body></html>"""

# A taught row whose single control is not clickable because it is hidden.
_HIDDEN_CONTROL = """<!doctype html><html><body><ul class="quality-list">
  <li class="quality-row"><span class="tier">2160p</span>
    <button class="pick" style="display:none"
            data-href="https://cdn.example/dl/a.mp4">A</button>
  </li>
</ul></body></html>"""

# Row-380's own negative control: a wrapper that IS the control.
_REAL_WRAPPER_CONTROL = """<!doctype html><html><body>
<div class="downloads">
  <div class="download-button" data-href="https://cdn.example/dl/4320.mp4">
    <span>7680 x 4320</span><span>8K &bull; 3.01GB</span>
  </div>
</div></body></html>"""


class _RecordingRunner:
    def __init__(self):
        self.events = []

    def log_event(self, kind, message, **kwargs):
        self.events.append({"kind": kind, "message": message,
                            "extra": kwargs.get("extra")})


@contextmanager
def _page(html):
    sync_playwright = pytest.importorskip(
        "playwright.sync_api").sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_content(html, wait_until="load")
            yield page
        finally:
            browser.close()


@pytest.mark.parametrize(
    "control", [_NESTED_BUTTON, _NESTED_CHEVRON],
    ids=["nested-button", "nested-anchor"])
def test_precondition_the_taught_rows_are_authoritative_opaque_wrappers(
        control):
    """Preconditions, asserted before any verdict."""
    with _page(_tier_rows(control)) as page:
        rows = page.locator("li.quality-row")
        assert rows.count() == 3, rows.count()
        for index in range(3):
            element = rows.nth(index)
            assert element.is_visible()
            assert _candidate_has_own_affordance(element) is False, (
                "row %d already carries its own affordance" % index)
            assert element.locator(_CONTROL_DESCENDANT_SEL).count() == 1, (
                "row %d does not hold exactly one control" % index)
            assert _learned_candidate_requires_signal(
                element, "li.quality-row") is False, (
                "row %d is not measured as authoritative" % index)
        # The rows carry DISTINCT tiers, so the winner is decided by score and
        # not by sort stability.
        scores = [res_score(rows.nth(i).inner_text()) for i in range(3)]
        assert scores == [720, 1080, 2160], scores


@pytest.mark.parametrize(
    "control", [_NESTED_BUTTON, _NESTED_CHEVRON],
    ids=["nested-button", "nested-anchor"])
def test_taught_row_resolves_to_its_control_instead_of_being_deleted(control):
    """RED on the defective parent: the learned path scores 0 candidates."""
    with _page(_tier_rows(control)) as page:
        best = find_best_download(
            page, learned={"row_selectors": ["li.quality-row"]})
        assert best is not None, "the taught rows produced no candidate at all"
        assert best.get("_via_learned") is True, (
            "the taught selector produced no learned hit; "
            f"text={best.get('text')!r}")
        assert best.get("_learned_sel") == "li.quality-row"
        locator = best["locator"]
        tag = locator.evaluate("e => e.tagName")
        assert tag in ("BUTTON", "A"), (
            f"winner is a {tag}, which fires no download when clicked")
        assert "2160p" in best["text"], best["text"]
        target = (locator.get_attribute("data-href")
                  or locator.get_attribute("href") or "")
        assert "2160p" in target, target


def test_negative_control_an_ambiguous_taught_row_is_dropped_and_counted():
    """A row holding two controls has no identifiable click target.

    Proving the fix RESOLVED taught rows rather than admitting every wrapper:
    this one must still be dropped, and the drop must be reported.
    """
    with _page(_AMBIGUOUS) as page:
        row = page.locator("li.quality-row")
        assert row.count() == 1
        assert row.first.locator(_CONTROL_DESCENDANT_SEL).count() == 2
        runner = _RecordingRunner()
        best = find_best_download(
            page, learned={"row_selectors": ["li.quality-row"]},
            runner=runner)
        if best is not None:
            assert not best.get("_via_learned"), (
                "an ambiguous wrapper was returned as a learned hit: "
                f"{best.get('text')!r}")
        summaries = [e for e in runner.events
                     if e["kind"] == "candidate_admission_filtered"]
        assert len(summaries) == 1, runner.events
        extra = summaries[0]["extra"] or {}
        assert extra.get("wrapper_unresolved", 0) >= 1, extra


def test_negative_control_a_hidden_single_control_is_dropped_and_counted():
    """An unclickable control is not a click target either."""
    with _page(_HIDDEN_CONTROL) as page:
        row = page.locator("li.quality-row")
        assert row.count() == 1
        control = row.first.locator(_CONTROL_DESCENDANT_SEL)
        assert control.count() == 1
        assert control.first.is_visible() is False
        runner = _RecordingRunner()
        best = find_best_download(
            page, learned={"row_selectors": ["li.quality-row"]},
            runner=runner)
        if best is not None:
            assert not best.get("_via_learned"), best.get("text")
        summaries = [e for e in runner.events
                     if e["kind"] == "candidate_admission_filtered"]
        assert len(summaries) == 1, runner.events
        assert (summaries[0]["extra"] or {}).get("wrapper_unresolved", 0) >= 1


def test_negative_control_a_row_with_no_nested_control_is_unchanged():
    """The pre-existing behaviour for an opaque row with 0 controls."""
    with _page(_tier_rows(_NO_NESTED_CONTROL)) as page:
        rows = page.locator("li.quality-row")
        assert rows.count() == 3
        assert rows.first.locator(_CONTROL_DESCENDANT_SEL).count() == 0
        best = find_best_download(
            page, learned={"row_selectors": ["li.quality-row"]})
        assert best is not None
        assert best.get("_via_learned") is True
        assert "2160p" in best["text"], best["text"]


@pytest.mark.parametrize(
    "learned",
    [None, {"row_selectors": ["div.download-button[data-href]"]}],
    ids=["wide", "learned"])
def test_negative_control_a_real_wrapper_control_is_still_kept(learned):
    """A wrapper that carries the affordance itself is never resolved away."""
    with _page(_REAL_WRAPPER_CONTROL) as page:
        best = find_best_download(page, "", learned=learned, runner=None)
        assert best is not None, "the real control was dropped entirely"
        assert best["locator"].get_attribute("data-href") == \
            "https://cdn.example/dl/4320.mp4", best["text"]


def test_negative_control_unmeasurable_wrapper_status_keeps_the_candidate():
    """A locator that cannot answer ``count()`` must not delete the row."""
    class _RaisingLocator:
        def __init__(self, element):
            self._element = element

        def locator(self, selector):
            raise RuntimeError("locator stub cannot resolve descendants")

        def __getattr__(self, name):
            return getattr(self._element, name)

    from bulk_downloader.detect import _is_wrapper_not_control

    with _page(_tier_rows(_NESTED_BUTTON)) as page:
        row = page.locator("li.quality-row").first
        assert _candidate_has_own_affordance(row) is False
        assert _is_wrapper_not_control(_RaisingLocator(row)) is False, (
            "an unmeasurable element was declared a wrapper")
