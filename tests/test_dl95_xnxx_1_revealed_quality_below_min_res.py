"""dl95-xnxx-1 (O1513 on test2, RESULT-xnxx.md D1, xnxx/XN-dedupe/XN__activity-done.png): xnxx saved a 360p
file as "done" while the site asks min_resolution 1080. Other paths hold "Best is 360p (below 1080p) -- Approve to
force"; this one did not.

The chain, measured live on test2 (real Chromium, fixture tests/fixtures/xnxx_revealed_panel_20260928.json):
the page's Download control is unlabelled (score 0), so the runner's pre-click min_resolution gate (``score > 0``)
passes it. Its click opens xnxx's download panel (MEDIUM 360p / LOW 240p). Row 759's ``_download_from_revealed_modal``
clicks the revealed 360p link, and nothing judges it against min_resolution: history row done,
XNXX_erotic_masturbation_in_hd_SD.mp4, 19,767,487 B == video_360p.mp4.

The fix judges the resolved file once the click has produced it (URL leaf + suggested name, else the revealed
label). Below the bar -> needs_review "Approve to force", and the started download is cancelled.

Hermetic: the row 759 fake-page shape, with the real captured labels, URL leaf and file name. No browser, no network.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest
from playwright.sync_api import TimeoutError as PWTimeout

from bulk_downloader.runner_integrity import IntegrityMixin
from bulk_downloader.runner_transport import TransportMixin

BD_GATE_SCOPE = "module"

FX = json.loads(
    (
        Path(__file__).parent / "fixtures" / "xnxx_revealed_panel_20260928.json"
    ).read_text()
)
SCENE = "https://www.xnxx.com/video-13c6ura2/erotic_masturbation_in_hd"


class _Reached(Exception):
    """The transfer path was entered: nothing held the download."""


class _Download:
    def __init__(self, url, name):
        self.url = url
        self.suggested_filename = name
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class _Loc:
    def __init__(self, text, on_click=None):
        self._text = text
        self._on_click = on_click

    def is_visible(self, timeout=None):
        return True

    def inner_text(self, timeout=None):
        return self._text

    def get_attribute(self, name):
        return None

    def click(self, *a, **kw):
        if self._on_click:
            self._on_click()


class _LocList:
    def __init__(self, els):
        self._els = list(els)

    def all(self):
        return list(self._els)

    def count(self):
        return len(self._els)

    @property
    def first(self):
        return _LocList(self._els[:1])

    def nth(self, i):
        return self._els[i]


class _Page:
    """xnxx reduced to what is load-bearing: an unlabelled Download control whose click reveals the panel."""

    def __init__(self, dl_url, dl_name, revealed):
        self.url = SCENE
        self._map = {}
        self._pending = None
        self.fired = []

        def fire():
            if self._pending is not None:
                d = _Download(dl_url, dl_name)
                self.fired.append(d)
                self._pending["value"] = d

        links = [_Loc(r["text"], on_click=fire) for r in revealed]
        trigger = _Loc(
            FX["trigger"]["text"],
            on_click=lambda: self._map.__setitem__("[role='menuitem']", links),
        )
        self._map["[role='menuitem']"] = [trigger]
        self.best = {
            "locator": trigger,
            "text": "Download",
            "score": 0,
            "size": 0,
            "_all_candidates": [
                {
                    "locator": trigger,
                    "text": "Download",
                    "score": 0,
                    "size": 0,
                    "work": 0,
                }
            ],
        }

    def locator(self, sel):
        return _LocList(self._map.get(sel, []))

    def title(self):
        return "Erotic Masturbation in HD"

    @contextlib.contextmanager
    def expect_download(self, timeout=None):
        box = {"value": None}
        self._pending = box

        class _Info:
            @property
            def value(self):
                return box["value"]

        try:
            yield _Info()
        finally:
            self._pending = None
        if box["value"] is None:
            raise PWTimeout("no download event fired")


class _Runner(TransportMixin, IntegrityMixin):
    def __init__(self, config, forced=False):
        import threading

        self.config = {"name": "xnxx", **config}
        self.site_id = "54919fd0"
        self._lock = threading.Lock()
        self.jobs = {SCENE: {"force_download": forced}}
        self.updates = []
        self.history = []

    def _update_job(self, url, status, msg="", **kw):
        self.updates.append((status, msg))

    def _screenshot(self, page, url):
        return "/dev/null/shot.png"


@pytest.fixture
def run(monkeypatch, tmp_path):
    import bulk_downloader.runner_transport as rt

    def _reached(*a, **k):
        raise _Reached()

    monkeypatch.setattr(rt, "resolve_filename_template", _reached)

    def go(config=None, *, forced=False, dl_url=None, dl_name=None, revealed=None):
        page = _Page(
            dl_url or FX["download"]["url"],
            dl_name or FX["download"]["suggested_filename"],
            revealed or FX["revealed"],
        )
        runner = _Runner({**FX["site"], **(config or {})}, forced=forced)
        monkeypatch.setattr(rt, "db_log", lambda *a, **k: runner.history.append(a))
        try:
            runner._do_download(page, None, SCENE, page.best, tmp_path, "?")
            reached = False
        except _Reached:
            reached = True
        return runner, page, reached

    return go


def test_xnxx_360p_under_min_resolution_1080_is_held_not_saved(run):
    runner, page, reached = run()
    assert not reached, (
        "the 360p file went on to the transfer path: saved as done under min_resolution 1080"
    )
    assert [s for s, _ in runner.updates] == ["needs_review"], runner.updates
    msg = runner.updates[0][1]
    assert (
        msg.startswith("Best is 360p (below 1080p)")
        and "Approve to force" in msg
        and "video_360p.mp4" in msg
    )
    assert [h[3] for h in runner.history] == ["needs_review"]
    assert len(page.fired) == 1 and page.fired[0].cancelled, (
        "the started download was not cancelled"
    )
    assert "secure=" not in msg and all(
        "secure=" not in str(h) for h in runner.history
    ), "the signed token leaked"


def test_a_file_that_meets_the_bar_proceeds(run):
    # Control: the same page, the site asks 360 -> the transfer path is entered, nothing held.
    runner, page, reached = run({"min_resolution": 360})
    assert reached and runner.updates == [] and not page.fired[0].cancelled


def test_approve_to_force_proceeds(run):
    runner, _page, reached = run(forced=True)
    assert reached and runner.updates == []


def test_min_resolution_off_proceeds(run):
    runner, _page, reached = run({"min_resolution": 0})
    assert reached and runner.updates == []


def test_a_nameless_file_is_judged_by_the_revealed_label(run):
    # The file name says nothing; the panel's label said 360p.
    runner, _page, reached = run(
        dl_url="https://cdn.example.invalid/f/abc.mp4", dl_name="clip.mp4"
    )
    assert not reached and runner.updates[0][1].startswith(
        "Best is 360p (below 1080p)"
    ), runner.updates


def test_nothing_says_a_resolution_proceeds_unchanged(run):
    unlabelled = [{"text": "Get file", "score": 0}]
    runner, _page, reached = run(
        dl_url="https://cdn.example.invalid/f/abc.mp4",
        dl_name="clip.mp4",
        revealed=unlabelled,
    )
    # No revealed winner with a score -> row 759 re-entry declines -> the pre-existing needs_review.
    assert not reached and "Clicked but no download started" in runner.updates[0][1]


def test_a_scored_trigger_is_left_to_the_pre_click_gate():
    from bulk_downloader.runner_transport import TransportMixin as T

    runner = _Runner(FX["site"])
    d = _Download(FX["download"]["url"], FX["download"]["suggested_filename"])
    assert (
        T._below_min_resolution_by_file(
            runner, None, SCENE, d, {"score": 2160}, d.suggested_filename
        )
        is False
    )
    assert runner.updates == [] and not d.cancelled
