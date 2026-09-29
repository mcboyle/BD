"""dl95-tube8-1: "Finding download button..." had no bound.

MEASURED on test2 v3.66.1709 (evidence
``harness-work/UIUX-20260928/download-95/A5-A/RESULT-tube8.md`` and
``_queue/qh/QH__stuck-finding-button.png``): tube8 and pornhub jobs sat at
"Finding download button..." 0% for ~9 min (23:49-23:58Z) and ~19 min
(00:01-00:20Z) with no bytes, then filed needs_review on a category nav link.
The wide sweep walks every a/button/[onclick]/... and every "HD" text holder
(a tube page carries one per thumbnail) with several Playwright round trips
each; on a browser shared with 16-20 running jobs each round trip is slow, and
nothing in the phase ever looks at the clock.

Contract pinned here: the sweep checks a phase deadline between elements; once
it is spent find_best_download stops and returns a falsy, keyed result that
names the budget and where it stopped, and the runner fails the job with that
reason instead of scoring a half-read page.  A page that fits the budget is
unchanged.  Hermetic: the fixture is served by route interception on
``.example`` and every other request is aborted; browser slowness is a fake
monotonic clock advanced once per Playwright locator round trip.
"""

# An ordinary module test: its subject is the module under test, not the tree.
BD_GATE_SCOPE = "module"

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from bulk_downloader import detect

_PAGE = "https://www.tube8.example/porn-video/94851231/"

# Seconds one locator round trip costs on the loaded hub (fake clock).
_STEP_S = 1.0
_ROUND_TRIPS = ("inner_text", "get_attribute", "evaluate_all", "evaluate",
                "count", "is_visible", "all", "text_content")


def _tube_page(n_tiles):
    """tube8's shape: a category nav "HD" link and one "HD" badge per
    related-video thumbnail; no download control at all."""
    tiles = "".join(
        f'<li><a href="/porn-video/{80000000 + i}/" title="Scene {i}">'
        f'<span class="hd-badge">HD</span> Related scene {i}</a></li>'
        for i in range(n_tiles))
    return ("<!doctype html><html><body>"
            '<nav><a href="/cat/hd/">HD</a></nav><h1>Scene</h1>'
            '<div id="player"></div><ul>' + tiles + "</ul></body></html>")


_SMALL_PAGE = (
    "<!doctype html><html><body><h1>Scene</h1>"
    '<a href="/dl/94851231/480p.mp4">Download 480p</a>'
    '<a href="/dl/94851231/1080p.mp4">Download 1080p</a>'
    "</body></html>")


@contextmanager
def _page(html):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch()
        try:
            pg = br.new_page()
            pg.route("**/*", lambda route: route.fulfill(
                status=200, content_type="text/html", body=html)
                if route.request.url == _PAGE else route.abort())
            pg.goto(_PAGE, wait_until="load")
            assert pg.url == _PAGE, (
                "route interception did not serve the fixture at its own URL")
            yield pg
        finally:
            br.close()


def _slow_browser(monkeypatch):
    """Advance a fake clock by _STEP_S per locator round trip; point the
    phase clock at it.  Only the phase clock is faked -- Playwright's own
    event loop keeps the real one."""
    from playwright.sync_api import Locator
    clock = {"now": 1000.0}
    for name in _ROUND_TRIPS:
        orig = getattr(Locator, name, None)
        if orig is None:
            continue

        def slow(self, *a, _orig=orig, **k):
            clock["now"] += _STEP_S
            return _orig(self, *a, **k)
        monkeypatch.setattr(Locator, name, slow)
    monkeypatch.setattr(detect, "_phase_clock", lambda: clock["now"],
                        raising=False)
    return clock


def test_the_sweep_stops_at_the_phase_budget_on_a_slow_tube_page(monkeypatch):
    budget = float(getattr(detect, "FIND_BUTTON_BUDGET_S", 300.0))
    clock = _slow_browser(monkeypatch)
    with _page(_tube_page(150)) as pg:
        start = clock["now"]
        best = detect.find_best_download(pg)
        spent = clock["now"] - start
    # A bound checked between elements may overrun by one element's reads
    # (gather_text + admission + visibility: well under 60 round trips).
    assert spent <= budget + 60 * _STEP_S, (
        f"DL95-TUBE8-1: 'Finding download button' ran {spent:.0f} s of "
        f"browser time on a 150-tile tube page against a {budget:.0f} s "
        "phase budget -- the sweep never looks at the clock")
    assert not best and detect.no_selection(best), (
        f"DL95-TUBE8-1: a spent budget must not return a candidate: {best!r}")
    assert best.get("_find_button_budget_spent"), (
        f"DL95-TUBE8-1: a spent budget must be a NAMED outcome, got {best!r}")
    reason = best.get("reason") or ""
    assert f"{budget:.0f} s" in reason and "stopped in" in reason, reason


def test_a_page_inside_the_budget_is_unchanged(monkeypatch):
    """Control: the bound must not fire, nor move the winner, on a page the
    sweep finishes in time.  Holds on base."""
    _slow_browser(monkeypatch)
    with _page(_SMALL_PAGE) as pg:
        best = detect.find_best_download(pg)
        assert best, "DL95-TUBE8-1 control: the in-budget page lost its find"
        assert not best.get("_find_button_budget_spent"), best
        href = best["locator"].get_attribute("href")
    assert href == "/dl/94851231/1080p.mp4", href


def test_an_already_spent_deadline_scores_nothing(monkeypatch):
    """The runner anchors the deadline when it publishes the status, so work
    done before the sweep (pre-scrape action, extractors) counts against it.
    A deadline already in the past must not read a single element."""
    clock = _slow_browser(monkeypatch)
    with _page(_SMALL_PAGE) as pg:
        start = clock["now"]
        best = detect.find_best_download(pg, deadline=start - 1.0)
        spent = clock["now"] - start
    assert best.get("_find_button_budget_spent"), best
    assert spent <= 2 * _STEP_S, spent


def test_the_runner_fails_the_job_with_the_budget_reason():
    from bulk_downloader.runner import SiteRunner
    calls = []
    stub = SimpleNamespace(
        _screenshot=lambda page, url: "ss.png",
        _handle_failure=lambda url, msg, screenshot="": calls.append(
            (url, msg, screenshot)),
        _classify_error=lambda msg: SiteRunner._classify_error(None, msg))
    spent = detect._budget_spent_result(
        300.0, 301.0, "general sweep [a]", 3)
    handled = SiteRunner._handle_find_button_budget_spent(
        stub, None, _PAGE, spent)
    assert handled is True and len(calls) == 1, calls
    url, msg, ss = calls[0]
    assert url == _PAGE and ss == "ss.png"
    assert "Finding download button" in msg and "300 s" in msg, msg
    # A load-dependent overrun retries on the moderate ladder: it is not a
    # page without a control (page_shape) nor a socket failure (network).
    assert SiteRunner._classify_error(None, msg) == "transient", msg


@pytest.mark.parametrize("best", [None, {}, {"score": 720, "text": "HD"}])
def test_the_runner_hook_ignores_every_other_result(best):
    """Negative control: only the budget outcome is consumed."""
    from bulk_downloader.runner import SiteRunner
    stub = SimpleNamespace(
        _screenshot=lambda *a: pytest.fail("screenshot taken"),
        _handle_failure=lambda *a, **k: pytest.fail("job failed"))
    assert SiteRunner._handle_find_button_budget_spent(
        stub, None, _PAGE, best) is False
