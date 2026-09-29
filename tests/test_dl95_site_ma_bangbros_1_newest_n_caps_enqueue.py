"""dl95-site-ma-bangbros-1 (download-95/B7-B/p1/site-ma-bangbros/RESULT.md, events.jsonl#scan_done).

On test2, discovery ran with newest_n=3 and reported discovered=3, queued=6: six distinct scenes were enqueued. Run 1 had
been interrupted after it recorded its 3 discoveries and before it enqueued them. Run 2 then carried those 3 unqueued
records into its enqueue loop beside its own 3 new ones, and it submitted all of them. Carrying them over is right,
because an interrupted run must not lose scenes. Submitting past newest_n, and counting them in `queued` so that queued
exceeds discovered, is the defect.

Drives the real crawl_with_page against a loopback member listing (logout link = members evidence) with a real cloak
page; enqueue_fn is a recorder. Run 1 is interrupted at the enqueue step (enqueue_fn raises), which leaves exactly the
state the restart left on test2.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

BD_GATE_SCOPE = "module"

N = 6
_LISTING = (
    "<!doctype html><html><head><title>Members / Scenes</title></head><body>"
    '<a href="/account/logout">Logout</a><main>'
    + "".join(
        f'<article><a href="/scenes/{11524260 - i}/scene-{i}"><img src="/t/{i}.jpg" alt="Scene {i}"></a>'
        f"<h3>Scene {i}</h3></article>"
        for i in range(N))
    + "</main></body></html>"
).encode()


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] != "/scenes":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_LISTING)))
        self.end_headers()
        self.wfile.write(_LISTING)

    def log_message(self, _fmt, *_args):
        return


@pytest.fixture(scope="module")
def origin():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def page():
    from bulk_downloader import cloak

    with cloak.cloaked_page(headless=True, config={"browser_backend": "playwright"},
                            viewport={"width": 1280, "height": 720}) as p:
        yield p


def _crawl(page, origin, db, enqueue_fn, newest_n=3):
    from bulk_downloader import scene_crawler

    return scene_crawler.crawl_with_page(
        page, site_id="bangbros", listing_url=origin + "/scenes", site_config={},
        newest_n=newest_n, max_pages=1, max_scrolls=1, delay_s=0, title_fetch_limit=0,
        db_path=db, enqueue_fn=enqueue_fn)


def _interrupted(_sid, _url):
    raise RuntimeError("app restarted before discovery finished")


@pytest.fixture
def after_interrupted_run(page, origin, tmp_path):
    db = str(tmp_path / "crawl.sqlite")
    first = _crawl(page, origin, db, _interrupted)
    # Positive control: run 1 really recorded 3 scenes and enqueued none, the state the restart left.
    assert first["discovered"] == 3 and first["queued"] == 0 and len(first["enqueue_errors"]) == 3, first
    return db


def _recorder():
    sent = []

    def enqueue(_sid, url):
        sent.append(url)
        return {"added": 1, "dupes": 0, "skipped": 0}

    return sent, enqueue


def test_newest_n_caps_what_one_run_enqueues(page, origin, after_interrupted_run):
    sent, enqueue = _recorder()
    _crawl(page, origin, after_interrupted_run, enqueue)
    assert len(sent) <= 3, f"dl95-site-ma-bangbros-1: newest_n=3 but {len(sent)} scenes were enqueued: {sent}"
    assert len(set(sent)) == len(sent), sent


def test_status_keeps_discovered_at_least_queued(page, origin, after_interrupted_run):
    sent, enqueue = _recorder()
    result = _crawl(page, origin, after_interrupted_run, enqueue)
    assert result["discovered"] >= result["queued"], (
        f"dl95-site-ma-bangbros-1: status discovered={result['discovered']} < queued={result['queued']}")
    assert result["queued"] + result["requeued"] == len(sent), (result, sent)


def test_carried_over_scenes_are_not_lost(page, origin, after_interrupted_run):
    """Every scene either run discovered reaches the queue exactly once over the following runs."""
    sent, enqueue = _recorder()
    for _ in range(3):
        _crawl(page, origin, after_interrupted_run, enqueue)
    assert len(sent) == len(set(sent)) == N, sent


def test_unbounded_newest_n_still_submits_everything(page, origin, after_interrupted_run):
    sent, enqueue = _recorder()
    result = _crawl(page, origin, after_interrupted_run, enqueue, newest_n=0)
    assert len(sent) == N and result["requeued"] == 3 and result["queued"] == 3, (result["discovered"], sent)
