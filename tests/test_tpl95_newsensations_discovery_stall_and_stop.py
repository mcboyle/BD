"""tpl95-newsensations-1 (download-95/A8-A/tpl-newsensations, test2 2026-09-29): a scene
discovery on https://www.newsensations.com/members/ stayed RUNNING from 03:11:45Z,
pages_walked 0, until the service restarted at 03:30Z. Nothing bounded the run, the
status never said where it was, and there was no way to stop it: the site's slot was
held, so every new start was refused as "already running".

A run now proves it is alive at every navigation. One that stops moving for STALL_S
is failed with where it stopped and its site is freed; an operator can stop a run
(POST /api/discovery/scenes/stop). A thread that resumes after either verdict can
neither overwrite it nor queue anything.

Hermetic: the browser is a scripted page whose navigation blocks until released.
"""
from __future__ import annotations

import contextlib
import threading
import time

import pytest

from bulk_downloader import scene_crawler

BD_GATE_SCOPE = "module"
SITE = "910a72be"
LISTING = "https://www.newsensations.com/members/"
NAVIGATIONS: list[str] = []   # thread name per page.goto, reset per test


class _Page:
    """Just enough page for crawl_with_page; goto blocks while ``gate`` is closed."""

    def __init__(self, gate: threading.Event, entered: threading.Event):
        self.gate = gate
        self.entered = entered
        self.url = "about:blank"

    def goto(self, url, **_kw):
        NAVIGATIONS.append(threading.current_thread().name)
        self.entered.set()
        self.gate.wait(30)
        self.url = url
        return None

    def evaluate(self, js, *_a):
        return None if "scrollTo" in js else [720, 2]

    def wait_for_timeout(self, _ms):
        return None

    def content(self):
        return "<html><body></body></html>"

    def locator(self, selector):
        page = self

        class _Locator:
            def evaluate_all(self, _js):
                return [
                    {"url": f"https://www.newsensations.com/members/gallery/{i}", "text": f"Scene {i}",
                     "title": "", "aria": "", "img_alt": "", "nearest": "",
                     "class_name": "", "rel": "", "has_img": True}
                    for i in range(2)
                ] if page.url != "about:blank" else []

            def count(self):
                return 1 if "logout" in selector else 0

        return _Locator()


@pytest.fixture
def harness(tmp_path, monkeypatch):
    gate = threading.Event()
    entered = threading.Event()
    queued = []
    monkeypatch.setattr(scene_crawler, "STALL_S", 0.4, raising=False)
    monkeypatch.setattr(scene_crawler, "_clear_gates", lambda *a, **k: None)

    NAVIGATIONS.clear()
    openings = []   # per start, in order: an Event the browser "opening" waits on

    @contextlib.contextmanager
    def fake_runner_page(_runner, _site_id):
        if openings:
            opening = openings.pop(0)
            opening.wait(30)
        yield _Page(gate, entered)

    monkeypatch.setattr(scene_crawler, "_runner_page", fake_runner_page)
    db_path = str(tmp_path / "crawl.sqlite")

    def start():
        return scene_crawler.start_background_crawl(
            site_id=SITE, listing_url=LISTING, site_config={}, runner=object(),
            newest_n=3, max_pages=1, max_scrolls=1, delay_s=0.1, title_fetch_limit=0,
            enqueue_fn=lambda sid, url: queued.append(url) or {"added": 1},
            db_path=db_path,
        )

    def status(run_id=None):
        return scene_crawler.crawl_status(site_id=SITE, run_id=run_id, db_path=db_path)

    yield gate, start, status, queued, db_path, entered, openings
    gate.set()
    for thread in threading.enumerate():
        if thread.name == f"scene-crawl-{SITE}":
            thread.join(10)


def _join_crawl_threads():
    for thread in threading.enumerate():
        if thread.name == f"scene-crawl-{SITE}":
            thread.join(10)
            assert not thread.is_alive()


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_the_row_a_stuck_run_is_failed_with_where_it_stopped_and_frees_its_site(harness):
    gate, start, status, queued, _db, entered, *_ = harness
    run_id = start()["run_id"]
    assert entered.wait(5)
    time.sleep(0.6)
    stuck = status(run_id)
    assert stuck["state"] == "FAILED", f"stuck discovery still reported as {stuck['state']}: {stuck}"
    assert "no progress for" in stuck["error"] and f"listing page {LISTING}" in stuck["error"], stuck
    second = start()   # the site is free again; the base raised CrawlAlreadyRunning here
    assert second["state"] == "RUNNING" and second["run_id"] != run_id
    gate.set()
    _join_crawl_threads()
    assert status(run_id)["state"] == "FAILED", "the resumed thread overwrote the stall verdict"


def test_a_running_discovery_says_where_it_is(harness, monkeypatch):
    gate, start, status, queued, _db, entered, *_ = harness
    monkeypatch.setattr(scene_crawler, "STALL_S", 60.0, raising=False)
    run_id = start()["run_id"]
    assert entered.wait(5)
    progress = status(run_id).get("progress")
    assert progress and progress["phase"] == "listing page" and progress["current_url"] == LISTING, (
        f"a running discovery does not say where it is: {status(run_id)}")


def test_an_operator_stop_cancels_the_run_and_the_resumed_thread_queues_nothing(harness):
    gate, start, status, queued, _db, entered, *_ = harness
    run_id = start()["run_id"]
    assert entered.wait(5)
    stopped = scene_crawler.stop_crawl(SITE, db_path=harness[4])
    assert stopped == {"ok": True, "site_id": SITE, "run_id": run_id, "state": "STOPPED"}
    now = status(run_id)
    assert now["state"] == "STOPPED" and now["error"] == "stopped by operator", now
    gate.set()
    _join_crawl_threads()
    assert status(run_id)["state"] == "STOPPED"
    assert queued == [], f"a stopped run still queued {queued}"


def test_a_stop_while_the_browser_is_opening_still_stops_the_run(harness):
    """Lens bd-cx-worker-1 R1: the stop frees the site before the crawl begins; the
    crawl must still check its own stopped control, not the now-empty site slot."""
    gate, start, status, queued, db_path, _entered, openings = harness
    opening = threading.Event()
    openings.append(opening)
    gate.set()
    run_id = start()["run_id"]
    assert scene_crawler.stop_crawl(SITE, db_path=db_path)["state"] == "STOPPED"
    opening.set()
    _join_crawl_threads()
    assert status(run_id)["state"] == "STOPPED"
    assert queued == [], f"CX1_STOPPED_OPENING_QUEUED: the stopped run queued {queued}"


def test_a_stopped_run_never_borrows_the_newer_runs_control(harness):
    gate, start, status, queued, db_path, _entered, openings = harness
    old_opening = threading.Event()
    openings.append(old_opening)
    gate.set()
    old = start()["run_id"]
    scene_crawler.stop_crawl(SITE, db_path=db_path)
    gate.clear()                        # the new run holds the site, parked in its first navigation
    new = start()["run_id"]
    assert _wait_for(lambda: len(NAVIGATIONS) == 1)
    old_opening.set()                   # the stopped run resumes while the new one owns the site
    time.sleep(0.3)
    assert len(NAVIGATIONS) == 1, (
        "the stopped run navigated on after resuming (it borrowed the newer run's control)")
    gate.set()
    _join_crawl_threads()
    assert status(old)["state"] == "STOPPED"
    assert status(new)["state"] == "COMPLETED"


def test_stop_with_nothing_running_says_so(harness):
    result = scene_crawler.stop_crawl(SITE, db_path=harness[4])
    assert result["ok"] is False and "no scene discovery is running" in result["error"]


def test_a_run_that_keeps_moving_completes_and_is_not_reaped(harness):
    """Positive control: the stall verdict is for silence, not for runs that finish."""
    gate, start, status, queued, _db, _entered, *_ = harness
    gate.set()
    run_id = start()["run_id"]
    assert _wait_for(lambda: status(run_id)["state"] != "RUNNING"), status(run_id)
    time.sleep(0.5)
    done = status(run_id)
    assert done["state"] == "COMPLETED", done
    assert done["discovered"] == 2 and len(queued) == 2, (done, queued)


def test_the_stop_route_is_registered():
    from bulk_downloader import app as bd_app

    rules = {(r.rule, frozenset(r.methods or ())) for r in bd_app.app.url_map.iter_rules()}
    assert any(path == "/api/discovery/scenes/stop" and "POST" in methods
               for path, methods in rules)
