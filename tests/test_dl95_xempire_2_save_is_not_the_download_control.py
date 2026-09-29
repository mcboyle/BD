"""dl95-xempire-2: the scene's Download control outranks a playlist "Save".

LIVE on test2 (download-95/B7-B/p1/xempire/RESULT.md, joblog-361.json): a fresh
members.xempire.com scene went needs_review in 70 s -- "Clicked but no download
started ... Saw: auto(?):Save". The scene header is Favorites / Save / Download;
"save" is in detect's download vocabulary, all three score 0, and the stable
sort left Save first in DOM order. Save files the scene into a playlist
(span.Icon-Playlist); Download is the reveal trigger row 722 G20 already opens.

Fixture: verbatim DOM excerpts of the real members scene page (test2 campaign
capture 2026-09-15, /home/mboyle/campaign/xempire/1/page.scrubbed.html on .95):
sidebar nav, the player header controls, one related-scene card. The account
menu and the page's config JSON are not carried. Real Chromium runs the real
find_best_download over it.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

SCENE = (
    Path(__file__).parent / "fixtures" / "dl95_xempire_scene_player_header.html"
).read_text(encoding="utf-8")
SCENE_PATH = "/en/video/eroticax/Supportive-In-Every-Way/288932"
PAGES = {
    SCENE_PATH: SCENE,
    # Derived at test time: the same real header with the playlist icon's
    # class token removed, so "Save" carries no collection mark.
    "/unmarked": SCENE.replace("Icon-Playlist", "Icon-Unmarked"),
    "/save-video": '<a href="/dl/scene-288932.mp4">Save video</a>',
    "/save-to-playlist": "<button>Save to playlist</button>",
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGES.get(self.path, "<html></html>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


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
def page(origin):
    from bulk_downloader import cloak

    with cloak.cloaked_page(
        headless=True,
        config={"browser_backend": "playwright"},
        viewport={"width": 1400, "height": 900},
    ) as browser_page:
        # The capture references the site's CDN assets; stay hermetic.
        browser_page.route(
            "**/*",
            lambda route: (
                route.continue_()
                if route.request.url.startswith(origin)
                else route.abort()
            ),
        )
        yield browser_page


class _Runner:
    def __init__(self):
        self.events = []

    def log_event(self, kind, message, extra=None, **_):
        self.events.append((kind, extra or {}))


def _best(page, origin, path):
    from bulk_downloader import detect

    page.goto(origin + path, wait_until="domcontentloaded")
    runner = _Runner()
    best = detect.find_best_download(page, runner=runner)
    texts = [c["text"] for c in (best or {}).get("_all_candidates", [])]
    return best, texts, runner.events


def test_scene_header_download_is_chosen_over_playlist_save(page, origin):
    best, texts, events = _best(page, origin, SCENE_PATH)
    assert best is not None
    assert best["text"] == "Download", (
        f"DL95_XEMPIRE_2_CLICKS_SAVE best={best['text']!r} saw={texts}"
    )
    assert "Save" not in texts, texts
    # The header control, not a related scene's thumb trigger.
    assert texts.index("Download") < texts.index("Download This Video"), texts
    counted = [e for k, e in events if k == "candidate_admission_filtered"]
    assert counted and counted[0].get("collection_action") == 1, events


def test_winner_is_the_reveal_trigger_the_runtime_opens(page, origin):
    from bulk_downloader import runner_transport

    best, _, _ = _best(page, origin, SCENE_PATH)
    assert runner_transport._reveal_trigger_for(best["locator"]) is not None


def test_unmarked_save_is_admitted_but_download_wins_the_tie(page, origin):
    best, texts, _ = _best(page, origin, "/unmarked")
    assert "Save" in texts, (
        f"a bare Save with no collection mark stays a candidate: {texts}"
    )
    assert best["text"] == "Download", f"DL95_XEMPIRE_2_TIE_TO_SAVE saw={texts}"


def test_save_video_link_is_still_a_download(page, origin):
    best, _, events = _best(page, origin, "/save-video")
    assert best is not None and best["text"].startswith("Save video"), best
    assert not [e for k, e in events if e.get("collection_action")], events


def test_save_to_playlist_is_refused_and_counted(page, origin):
    best, _, events = _best(page, origin, "/save-to-playlist")
    assert best is None, best
    counted = [e for k, e in events if k == "candidate_admission_filtered"]
    assert counted and counted[0].get("collection_action") == 1, events
