"""Row 759 -- a click that OPENS a quality modal is a trigger, not a failure.

THE OPERATOR-VISIBLE SHAPE (dfxtra / evilangel / xempire, v3.66.1498). The
scene's Download control is an hrefless ``<div>``. No href means no direct
route, so runner_transport._do_download takes the click path:
``with page.expect_download(timeout=_FIRST_DOWNLOAD_TIMEOUT_MS): click()``. The
click does not start a download -- it APPENDS a quality modal carrying the
live label the operator would click next. Sixty seconds later the run records
``needs_review: Clicked but no download started -- looks like a modal-trigger
button - set Trigger Selector``, on a page that is at that moment showing
exactly the control that WOULD have worked. Manual save succeeds; BD does not.

WHAT THIS GATE PINS. The timeout is not the end of the attempt. The runner
re-detects on the page the click produced, honours ``quality_preference``
over what detect admitted, and clicks that once. Row 759-4 (v3.66.1506,
tests/test_row759_hidden_quality_cell_is_not_a_candidate.py) already scores a
computed-hidden resolution cell 0, so what a preference can reach here is the
VISIBLE label and never the responsive-duplicate ``2160p`` cell -- the two
halves of the row's acceptance are asserted together below.

The re-entry is narrow: when the click opened nothing this can act on, the
method returns None and the caller reports needs_review exactly as before
(``TestTheReentryIsNarrow``). No browser, no network, no site contact.
"""

# The gate parses a module-level ASSIGNMENT, not a docstring line.
BD_GATE_SCOPE = "repo-wide"

import contextlib

from playwright.sync_api import TimeoutError as PWTimeout

from bulk_downloader.detect import res_score
from bulk_downloader.runner_integrity import IntegrityMixin
from bulk_downloader.runner_transport import TransportMixin

REENTRY = getattr(TransportMixin, "_download_from_revealed_modal", None)

_MISSING = (
    "row 759: TransportMixin has no _download_from_revealed_modal -- the "
    "60s expect_download timeout is still the end of the attempt, so the "
    "quality modal the click OPENED is never acted on and the run ends "
    "'Clicked but no download started'")

LABEL_TEXT = "1080p FHD"
HIDDEN_CELL_TEXT = "2160p"
LABEL_URL = "https://cdn.example.invalid/scene-1234-1080.mp4"
SCENE = "https://members.example.invalid/scene/1234"


class _Downloaded:
    def __init__(self, url, name):
        self.url = url
        self.suggested_filename = name


class _DLInfo:
    def __init__(self):
        self.value = None


class _Loc:
    """A Playwright-locator stub: only what the wide sweep and the click path
    actually call. No ``evaluate`` and no ``locator``, which is how the shipped
    fail-open helpers behave for a stub -- the candidate is kept."""

    def __init__(self, *, text, visible=True, on_click=None, attrs=None):
        self._text = text
        self._visible = visible
        self._on_click = on_click
        self._attrs = attrs or {}
        self.clicks = 0

    def is_visible(self, timeout=None):
        return self._visible

    def inner_text(self, timeout=None):
        return self._text

    def get_attribute(self, name):
        return self._attrs.get(name)

    def click(self, *a, **kw):
        self.clicks += 1
        if self._on_click is not None:
            self._on_click()


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
    """A page whose DOM CHANGES when the Download div is clicked."""

    def __init__(self, url=SCENE):
        self.url = url
        self._map = {}
        self.expect_calls = []
        self._pending = None

    def locator(self, sel):
        return _LocList(self._map.get(sel, []))

    def fire_download(self, url, name):
        if self._pending is not None:
            self._pending.value = _Downloaded(url, name)

    @contextlib.contextmanager
    def expect_download(self, timeout=None):
        self.expect_calls.append(timeout)
        info = _DLInfo()
        self._pending = info
        try:
            yield info
        finally:
            self._pending = None
        if info.value is None:
            raise PWTimeout("no download event fired")


def _modal_page(*, opens=True, cell_visible=False):
    """The Gamma shape reduced to what is load-bearing: an hrefless Download
    div whose click appends the operator-visible quality LABEL beside the
    responsive-duplicate resolution CELL that is in the DOM at no breakpoint."""
    page = _Page()
    label = _Loc(text=LABEL_TEXT, visible=True,
                 on_click=lambda: page.fire_download(LABEL_URL, "scene-1080.mp4"))
    cell = _Loc(text=HIDDEN_CELL_TEXT, visible=cell_visible)

    def _open():
        if opens:
            page._map["[role='menuitem']"] = [label, cell]

    div = _Loc(text="Download", visible=True, on_click=_open)
    page._map["[role='menuitem']"] = [div]
    best = {"locator": div, "text": "Download", "score": 0, "size": 0,
            "_all_candidates": [{"locator": div, "text": "Download",
                                 "score": 0, "size": 0, "work": 0}]}
    return page, best, div, label, cell


class _Runner(TransportMixin, IntegrityMixin):
    """The two shipped mixins that own this seam, and nothing else -- the
    method under test is exercised on the real class, not on a copy of it."""

    def __init__(self, qpref="best"):
        self.config = {"quality_preference": qpref}


def _reentry(runner, page, best):
    assert REENTRY is not None, _MISSING
    return runner._download_from_revealed_modal(page, best)


def _click_the_trigger(page, best):
    """What _do_download does before the handler under test runs."""
    try:
        with page.expect_download(
                timeout=TransportMixin._FIRST_DOWNLOAD_TIMEOUT_MS):
            best["locator"].click()
    except PWTimeout:
        return
    raise AssertionError(
        "the fixture's Download div fired a download event -- there would be "
        "no row 759 defect to re-enter on")


class TestTheFixtureItself:
    def test_the_click_really_builds_the_modal(self):
        # Prove the shape exists before any verdict is read from it: the
        # click appends exactly the two rows, and the hazard row 759-4 closed
        # is present -- the cell the operator cannot see outscores the label
        # they can. Lose either and every assertion below is vacuous.
        page, best, div, _label, _cell = _modal_page()
        assert page.locator("[role='menuitem']").count() == 1
        _click_the_trigger(page, best)
        assert div.clicks == 1
        assert page.locator("[role='menuitem']").count() == 2, (
            "the fixture did not append the quality modal")
        assert res_score(HIDDEN_CELL_TEXT) > res_score(LABEL_TEXT) > 0

    def test_a_page_that_opens_nothing_really_opens_nothing(self):
        page, best, _div, _label, _cell = _modal_page(opens=False)
        _click_the_trigger(page, best)
        assert page.locator("[role='menuitem']").count() == 1


class TestTheModalIsActedOn:
    def test_the_visible_label_is_clicked_and_the_download_fires(self):
        page, best, div, label, cell = _modal_page()
        _click_the_trigger(page, best)
        dl = _reentry(_Runner(), page, best)
        assert dl is not None, (
            "the re-entry gave up on a page that is showing the quality "
            "modal the click opened")
        assert dl.url == LABEL_URL
        assert (label.clicks, cell.clicks) == (1, 0), (
            "the hidden 2160p cell was clicked, or the visible label was "
            f"clicked more than once: label={label.clicks} cell={cell.clicks}")
        # EXACT COUNT: one trigger click, one re-entry. A retry loop that
        # spends timeout after timeout on the same page is a different and
        # much more expensive defect.
        assert len(page.expect_calls) == 2, (
            f"expected exactly one re-entry, saw {page.expect_calls}")
        assert div.clicks == 1

    def test_a_quality_preference_cannot_reach_the_hidden_cell(self):
        # THE ROW'S ACCEPTANCE. '2160' is exactly what the hidden cell
        # advertises; it must still resolve to the highest VISIBLE label,
        # because row 759-4 scores a computed-hidden cell 0 and a preference
        # chooses on score.
        page, best, _div, label, cell = _modal_page()
        _click_the_trigger(page, best)
        dl = _reentry(_Runner(qpref="2160,best"), page, best)
        assert dl is not None and dl.url == LABEL_URL
        assert (label.clicks, cell.clicks) == (1, 0), (
            "quality_preference='2160,best' reached the hidden 2160p cell")


def _hidden_only_modal():
    """A modal whose every quality row is rendered at NO breakpoint -- row
    759-4 scores them all 0, so there is nothing the operator could click."""
    page = _Page()
    rows = [_Loc(text=HIDDEN_CELL_TEXT, visible=False),
            _Loc(text="1080p", visible=False)]
    div = _Loc(text="Download", visible=True,
               on_click=lambda: page._map.__setitem__("[role='menuitem']", rows))
    page._map["[role='menuitem']"] = [div]
    best = {"locator": div, "text": "Download", "score": 0, "size": 0,
            "_all_candidates": []}
    return page, best, rows


class _RecordingRunner(_Runner):
    """The real IntegrityMixin choice, with a note of what it was asked."""

    def __init__(self, qpref="best"):
        super().__init__(qpref=qpref)
        self.preference_calls = []

    def _apply_quality_preference(self, best, qpref):
        self.preference_calls.append(qpref)
        return super()._apply_quality_preference(best, qpref)


class TestTheReentryIsNarrow:
    def test_the_operators_preference_is_actually_consulted(self):
        # The acceptance test above would pass even if the preference were
        # never applied, because the highest VISIBLE label also wins on raw
        # score. Pin that the choice is put to IntegrityMixin at all -- once,
        # with what the operator configured.
        page, best, _div, _label, _cell = _modal_page()
        _click_the_trigger(page, best)
        runner = _RecordingRunner(qpref="2160,best")
        assert _reentry(runner, page, best) is not None
        assert runner.preference_calls == ["2160,best"], (
            "the revealed population never reached quality_preference: "
            f"{runner.preference_calls}")

    def test_a_candidate_that_already_scored_is_never_reclicked(self):
        # NEGATIVE CONTROL. A scored link that fired no event is a page that
        # did not respond (a manifest, a dead anchor) -- clicking it again
        # spends a second timeout and can burn a single-use browser grant.
        # Only the score-0 modal-trigger shape earns a second look.
        page, _best, _div, label, _cell = _modal_page()
        page._map["[role='menuitem']"] = [label]
        scored = {"locator": _Loc(text="Download 1080p (HLS)", visible=True),
                  "text": "Download 1080p (HLS)", "score": 1080, "size": 0,
                  "_all_candidates": []}
        assert _reentry(_Runner(), page, scored) is None
        assert scored["locator"].clicks == 0
        assert page.expect_calls == [], (
            f"a scored candidate was clicked a second time: {page.expect_calls}")

    def test_a_modal_of_hidden_cells_only_is_not_clicked(self):
        # NEGATIVE CONTROL, and the half row 759-4 owns: every revealed row is
        # invisible, so every one of them scores 0. Clicking one is a
        # guaranteed 30s timeout on a control the operator cannot see either.
        page, best, rows = _hidden_only_modal()
        _click_the_trigger(page, best)
        assert page.locator("[role='menuitem']").count() == 2, (
            "the fixture did not append its hidden rows")
        assert _reentry(_Runner(), page, best) is None
        assert [r.clicks for r in rows] == [0, 0], (
            "an invisible resolution cell was clicked")
        assert len(page.expect_calls) == 1

    def test_a_revealed_label_that_still_fires_nothing_returns_none(self):
        # NEGATIVE CONTROL. Best-effort: when the second click also produces
        # no event, the caller must still reach its needs_review rather than
        # have PWTimeout escape _do_download.
        page = _Page()
        label = _Loc(text=LABEL_TEXT, visible=True)   # no on_click -> no event
        div = _Loc(text="Download", visible=True,
                   on_click=lambda: page._map.__setitem__(
                       "[role='menuitem']", [label]))
        page._map["[role='menuitem']"] = [div]
        best = {"locator": div, "text": "Download", "score": 0, "size": 0,
                "_all_candidates": []}
        _click_the_trigger(page, best)
        assert _reentry(_Runner(), page, best) is None
        assert label.clicks == 1
        assert len(page.expect_calls) == 2

    def test_the_second_wait_is_bounded_and_shorter_than_the_first(self):
        # The first expect_download has already spent 60s on this item. An
        # equal second wait doubles the worst case for every genuinely dead
        # control on every site, not just the ones this row is about.
        page, best, _div, _label, _cell = _modal_page()
        _click_the_trigger(page, best)
        _reentry(_Runner(), page, best)
        assert page.expect_calls == [
            TransportMixin._FIRST_DOWNLOAD_TIMEOUT_MS,
            TransportMixin._REVEALED_MODAL_TIMEOUT_MS,
        ]
        assert (0 < TransportMixin._REVEALED_MODAL_TIMEOUT_MS
                < TransportMixin._FIRST_DOWNLOAD_TIMEOUT_MS)

    def test_a_click_that_opens_nothing_is_still_needs_review(self):
        # NEGATIVE CONTROL. When the click changed nothing this can act on,
        # the method refuses and the caller's existing needs_review report
        # stands. Re-clicking the same control would only spend another
        # timeout on the same refusal -- and would hide a real dead button.
        page, best, div, _label, _cell = _modal_page(opens=False)
        _click_the_trigger(page, best)
        assert _reentry(_Runner(), page, best) is None, (
            "the re-entry claimed progress on a page the click did not change")
        assert div.clicks == 1, (
            f"the dead trigger was clicked again: {div.clicks} clicks")
        assert len(page.expect_calls) == 1, (
            f"a second download wait was spent for nothing: {page.expect_calls}")

    def test_the_timeout_handler_consults_the_reentry_before_reporting(self):
        # Seam, not component: the method is worthless if _do_download's
        # PWTimeout arm never reaches it, and it must be consulted BEFORE the
        # needs_review report, not after.
        import inspect
        src = inspect.getsource(TransportMixin._do_download)
        assert "_download_from_revealed_modal" in src, _MISSING
        assert (src.index("_download_from_revealed_modal")
                < src.index("Clicked but no download started")), (
            "the re-entry is consulted after the run has already been "
            "recorded needs_review")


class _CallerRunner(TransportMixin, IntegrityMixin):
    """_do_download's collaborators reduced to recorders. Everything the
    standard click path touches on the way to the probe hand-off is real;
    only the four seams that would reach a browser, a DB or a disk are stubs."""

    def __init__(self, qpref="best"):
        self.config = {"quality_preference": qpref, "name": "gamma-test"}
        self.site_id = 759
        self.jobs = []
        self.probed = []

    def _update_job(self, url, status, msg="", **kw):
        self.jobs.append((status, msg))

    def _screenshot(self, page, url):
        return "/dev/null/shot.png"

    def _do_probe_fetch(self, page_url, page, ctx, dl, best, res_lbl, suggested):
        self.probed.append((dl.url, suggested))


class TestTheCallerArmActuallyUsesIt:
    """THE FLIP. The tests above exercise _download_from_revealed_modal on its
    own; they stay green even if _do_download throws the result away, which is
    the pre-patch outcome under a different name. These two drive the real
    caller and read the OUTCOME: a download reaches the transfer path and no
    needs_review row is written."""

    def _run(self, monkeypatch, *, opens=True):
        import bulk_downloader.runner_transport as rt
        monkeypatch.setattr(rt, "db_log", lambda *a, **kw: None)
        page, best, _div, _label, _cell = _modal_page(opens=opens)
        runner = _CallerRunner()
        runner._do_download(page, None, page.url, best, None, "?", probe=True)
        return page, runner

    def test_the_revealed_download_reaches_the_transfer_path(self, monkeypatch):
        page, runner = self._run(monkeypatch)
        assert runner.probed == [(LABEL_URL, "scene-1080.mp4")], (
            "row 759: the quality modal opened and its label fired a download "
            "event, but _do_download did not carry that download forward -- "
            "the run ends exactly as it did before the fix")
        assert [s for s, _ in runner.jobs if s == "needs_review"] == []
        assert page.expect_calls == [
            TransportMixin._FIRST_DOWNLOAD_TIMEOUT_MS,
            TransportMixin._REVEALED_MODAL_TIMEOUT_MS,
        ]

    def test_a_caller_whose_click_opens_nothing_still_files_needs_review(
            self, monkeypatch):
        # NEGATIVE CONTROL on the same caller: the refusal is not removed,
        # only deferred until the page has been looked at.
        _page, runner = self._run(monkeypatch, opens=False)
        assert runner.probed == []
        assert [s for s, _ in runner.jobs] == ["needs_review"]
        assert "Clicked but no download started" in runner.jobs[0][1]
