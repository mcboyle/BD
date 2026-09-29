"""dl95-teenmegaworld-1 (O1513 A1-A finding A6): a top tier that fires no download must not end the job.

Measured on test2 v3.66.1706: teenmegaworld history 140 -> needs_review 0 B "no dl event; scored ok but no download
fired; saw: 6K(4.6 GB) | ... | 4K(3.5 GB) | 1440p(2.3 GB)", while the sibling scene 145 downloaded its 4K link fine
(2.65 GB). GREEN: when the winning tier's click fires no download event, _do_download clicks the next in-scope
candidates in the scorer's order (at most TransportMixin._TIER_FALLBACK_MAX, never below min_resolution unless the
job is forced) before filing needs_review, and the review message names the tiers it also tried.

Hermetic: page/locator doubles in the row-760 test's shape; no browser, network or site.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout

from bulk_downloader import runner_transport as transport

BD_GATE_SCOPE = "module"

PAGE_URL = "https://members.teenmegaworld.example/scene/will-you-help-me-darling"
PAYLOAD = b"x" * 4096


class _Download:
    def __init__(self, url):
        self.url = url
        self.suggested_filename = url.rsplit("/", 1)[-1]


class _Page:
    url = PAGE_URL

    def __init__(self):
        self.clicked = None
        self.timeouts = []
        self.gotos = []

    def evaluate(self, _script):   # popup-grant arm/read/disarm: nothing is ever captured
        return None

    def goto(self, url, **_kw):
        self.gotos.append(url)

    @contextmanager
    def expect_download(self, *, timeout):
        self.timeouts.append(timeout)
        self.clicked = None

        class _Info:
            pass

        info = _Info()
        yield info
        if self.clicked is not None and self.clicked.fires:
            info.value = _Download(self.clicked.file_url)
            return
        raise PWTimeout("no download event")

    def title(self):
        return "Will You Help Me Darling"


class _Tier:
    """One quality link. `fires` = the site answers its click with a download event."""

    def __init__(self, page, label, fires):
        self.page, self.label, self.fires = page, label, fires
        self.file_url = f"https://cdn.teenmegaworld.example/files/scene140_{label}.mp4"
        self.clicks = 0

    def evaluate(self, _js):
        return [["href", f"/members/download?tier={self.label}"]]

    def get_attribute(self, name):
        return f"/members/download?tier={self.label}" if name == "href" else None

    def click(self):
        self.clicks += 1
        self.page.clicked = self


class _Runner(transport.TransportMixin):
    def __init__(self, min_resolution=1080, forced=False):
        self.site_id = "teenmegaworld"
        self.config = {"name": "teenmegaworld", "use_http_dl": True, "verify_hash": False,
                       "verify_integrity": False, "min_resolution": min_resolution}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self.jobs = {PAGE_URL: {"status": "running", "force_download": forced}}
        self.log = logging.getLogger("teenmegaworld")
        self.fetched, self.failures, self.status = [], [], []

    def _http_download(self, _page_url, _page, _ctx, file_url, final_path):
        Path(final_path).write_bytes(PAYLOAD)
        self.fetched.append(file_url)
        return (len(PAYLOAD), len(PAYLOAD))

    def _probe_for_higher_tier(self, url, **_kwargs):
        return url

    def _build_mirror_urls(self, _url):
        return []

    def _screenshot(self, *_args, **_kwargs):
        return ""

    def _update_job(self, _url, state, message="", **_kwargs):
        self.status.append((state, message))

    def _handle_failure(self, url, message):
        self.failures.append((url, message))

    def _size_on_disk_after_tagging(self, _path, downloaded_size):
        return downloaded_size


def _drive(tmp_path, monkeypatch, runner, page, tiers):
    """tiers: [(locator, score)] in the scorer's order; the first is the winner."""
    monkeypatch.setattr(transport, "_HTTPX_AVAILABLE", True)
    monkeypatch.setattr(transport, "db_skip_identity", lambda *_a: ("different", ""))
    monkeypatch.setattr(transport, "db_log", lambda *_a, **_kw: None)
    monkeypatch.setattr(transport.staging_claim, "reserve", lambda path, _identity: (path, path.with_suffix(".part")))
    monkeypatch.setattr(transport.staging_claim, "release", lambda *_a: None)
    cands = [{"locator": loc, "score": score, "size": 0, "text": loc.label, "work": 0} for loc, score in tiers]
    best = dict(cands[0], _all_candidates=cands)
    runner._do_download(page, object(), PAGE_URL, best, Path(tmp_path), transport.res_label(tiers[0][1]))


def _reviews(runner):
    return [m for s, m in runner.status if s == "needs_review"]


def test_silent_top_tier_falls_back_to_the_next_tier_that_downloads(tmp_path, monkeypatch):
    page = _Page()
    k6, k4, p1440 = _Tier(page, "6k", False), _Tier(page, "4k", True), _Tier(page, "1440p", True)
    runner = _Runner()
    _drive(tmp_path, monkeypatch, runner, page, [(k6, 3456), (k4, 2160), (p1440, 1440)])
    assert (k6.clicks, k4.clicks, p1440.clicks) == (1, 1, 0)
    assert runner.fetched == [k4.file_url], runner.status
    assert _reviews(runner) == [], runner.status
    assert page.timeouts == [transport.TransportMixin._FIRST_DOWNLOAD_TIMEOUT_MS] * 2


def test_every_tier_silent_tries_at_most_two_more_then_reviews_naming_them(tmp_path, monkeypatch):
    page = _Page()
    tiers = [_Tier(page, n, False) for n in ("6k", "4k", "1440p", "1080p")]
    runner = _Runner()
    _drive(tmp_path, monkeypatch, runner, page, list(zip(tiers, (3456, 2160, 1440, 1080))))
    assert [t.clicks for t in tiers] == [1, 1, 1, 0]
    assert runner.fetched == []
    review = _reviews(runner)
    assert len(review) == 1 and "scored ok but no download fired" in review[0], runner.status
    assert f"also tried {transport.res_label(2160)}, {transport.res_label(1440)}" in review[0], review[0]


def test_fallback_never_goes_below_min_resolution(tmp_path, monkeypatch):
    page = _Page()
    k6, k4, p1440 = _Tier(page, "6k", False), _Tier(page, "4k", False), _Tier(page, "1440p", True)
    runner = _Runner(min_resolution=2160)
    _drive(tmp_path, monkeypatch, runner, page, [(k6, 3456), (k4, 2160), (p1440, 1440)])
    assert (k6.clicks, k4.clicks, p1440.clicks) == (1, 1, 0)
    assert runner.fetched == [] and len(_reviews(runner)) == 1


def test_a_forced_job_may_fall_back_below_min_resolution(tmp_path, monkeypatch):
    page = _Page()
    k6, p1440 = _Tier(page, "6k", False), _Tier(page, "1440p", True)
    runner = _Runner(min_resolution=2160, forced=True)
    _drive(tmp_path, monkeypatch, runner, page, [(k6, 3456), (p1440, 1440)])
    assert runner.fetched == [p1440.file_url], runner.status


def test_a_top_tier_that_fires_is_the_only_click(tmp_path, monkeypatch):
    page = _Page()
    k6, k4 = _Tier(page, "6k", True), _Tier(page, "4k", True)
    runner = _Runner()
    _drive(tmp_path, monkeypatch, runner, page, [(k6, 3456), (k4, 2160)])
    assert (k6.clicks, k4.clicks) == (1, 0)
    assert runner.fetched == [k6.file_url]


class _NavTier(_Tier):
    """A lower 'tier' whose href is a site navigation link (/logout): the runtime nav gate refuses it for a winner."""

    NAV_HREF = "/logout"

    def evaluate(self, _js):
        return [["href", self.NAV_HREF]]

    def get_attribute(self, name):
        return self.NAV_HREF if name == "href" else None


def test_fallback_never_clicks_a_tier_the_runtime_nav_gate_rejects(tmp_path, monkeypatch):
    """Lens (correctness-B3-B, O1481): the winner passes gate_candidate_url before its click; a fallback tier must too."""
    page = _Page()
    k6, nav, k4 = _Tier(page, "6k", False), _NavTier(page, "4k", True), _Tier(page, "1440p", True)
    probe = transport.gate_candidate_url(nav, PAGE_URL, text=nav.label)
    assert probe[1], f"positive control: the gate must reject the nav href, got {probe!r}"
    runner = _Runner()
    _drive(tmp_path, monkeypatch, runner, page, [(k6, 3456), (nav, 2160), (k4, 1440)])
    assert nav.clicks == 0, runner.status
    assert runner.fetched == [k4.file_url], runner.status
