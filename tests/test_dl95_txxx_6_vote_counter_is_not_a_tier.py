"""dl95-txxx-6: a like/dislike vote counter is never a resolution tier.

LIVE on test2 (v3.66.1715, job 448, 2026-09-29 06:09Z; harness-work/DOT95-LANE/
live-dl95-txxx-5/LIVE-RESULT-B7-B.md): the txxx member scene /videos/2671542/
went needs_review "scored ok but no download fired. Saw: 6K(?):6K Dislike |
auto(?):Download /download/?video=2671...". txxx renders the vote bar as
<button title="Dislike" class="dislike"> 6K </button>: a 6-thousand vote COUNT.
Its harvested label "6K Dislike" matched the 6K tier (3160), outranked the
scene's Download link and was CLICKED -- the job screenshot shows the dislike
cast on the operator's account.

Fixture: the verbatim video-stats + video-tags block of the real member scene
page (tests/fixtures/dl95_txxx_6/). The capture's counts are 0/0/1; the live
job-448 counts (11K / 6K / 9M) are derived from it here. Real Chromium runs the
real find_best_download over it.
"""

from __future__ import annotations

import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CAPTURE = (
    Path(__file__).parent / "fixtures" / "dl95_txxx_6" / "scene_stats_block.html"
).read_text(encoding="utf-8")


def _with_counts(html, like, dislike, views):
    """The capture's vote/view counts (" 0 ", " 0 ", " 1 ", each after the icon svg)."""
    out = html
    for cls, n in (('<button title="Like" class="like">', like),
                   ('<button title="Dislike" class="dislike">', dislike),
                   ('<div class="stat-item">', views)):
        out, k = re.subn(re.escape(cls) + r"(<svg.*?</svg>) \d+ ", cls + rf"\1 {n} ", out, count=1)
        assert k == 1, cls
    return out


LIVE = _with_counts(CAPTURE, "11K", "6K", "9M")
SCENE = "/videos/21752927/flogging-him-as-i-want/"
PAGES = {
    SCENE: LIVE,
    "/capture-counts": CAPTURE,
    # A real 4K download beside the same vote bar must still win.
    "/with-4k-link": LIVE.replace(
        "</body>", '<a href="/get_file/21752927_2160p.mp4">Download 4K</a></body>'),
    # A bare count, no title: the markup (class="dislike") names the vote.
    "/count-only": '<button class="dislike">6K</button>'
                   '<a href="/download/?video=1">Download</a>',
    # Control: a bare "6K" whose markup names no vote stays a 6K candidate.
    "/count-only-quality": '<a class="quality" href="/get_file/1_6k.mp4">6K</a>',
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
    counted = [e for k, e in runner.events if k == "candidate_admission_filtered"]
    return best, texts, counted


def test_the_dislike_count_is_not_a_6k_tier_and_the_download_link_wins(page, origin):
    best, texts, counted = _best(page, origin, SCENE)
    assert not [t for t in texts if "Dislike" in t], (
        f"DL95_TXXX6_VOTE_COUNTER_SCORED saw={texts}")
    assert best is not None and "/download/?video=21752927" in best["text"], (
        f"DL95_TXXX6_VOTE_COUNTER_SCORED best={best and best['text']!r} saw={texts}")
    assert counted and counted[0].get("rating_control") == 1, counted


def test_capture_counts_control_nothing_to_refuse(page, origin):
    """With the capture's 0 votes the label has no tier; nothing is refused or counted."""
    best, texts, counted = _best(page, origin, "/capture-counts")
    assert best is not None and "/download/?video=21752927" in best["text"], texts
    assert not [e for e in counted if e.get("rating_control")], counted


def test_a_real_4k_link_beside_the_vote_bar_still_wins(page, origin):
    best, texts, _ = _best(page, origin, "/with-4k-link")
    assert best is not None and best["text"].startswith("Download 4K"), texts
    assert best["score"] == 2160, best["score"]


def test_a_bare_count_on_a_marked_vote_button_is_refused(page, origin):
    best, texts, counted = _best(page, origin, "/count-only")
    assert "6K" not in texts, f"DL95_TXXX6_VOTE_COUNTER_SCORED saw={texts}"
    assert best is not None and best["text"].startswith("Download"), texts
    assert counted and counted[0].get("rating_control") == 1, counted


def test_control_a_bare_count_without_a_vote_mark_stays_a_tier(page, origin):
    best, texts, _ = _best(page, origin, "/count-only-quality")
    assert best is not None and best["score"] == 3160, (best, texts)


@pytest.mark.parametrize("label,refused", [
    ("6K Dislike", True), ("Like 11K", True), ("4K Likes", True),
    ("Thumbs up 8K", True), ("2K votes", True), ("8K upvotes", True),
    ("Download 4K", False), ("6K UHD", False), ("Save 1080p", False),
    ("4K", False), ("1,2K", False),
])
def test_label_matrix(label, refused):
    """Whole-label decisions only; a count-only label ("4K") needs the markup (above)."""
    from bulk_downloader import detect

    class _NoMark:
        def evaluate(self, *_a):
            return False

    assert detect._is_rating_control(_NoMark(), label) is refused, label


@pytest.mark.parametrize("label,reason", [
    ("Like", "no_signal"), ("0 Dislike", "no_signal"), ("6K Dislike", "rating_control"),
])
def test_only_a_scoring_vote_label_is_refused_as_a_vote(label, reason):
    """A plain vote button carries no signal and stays an uncounted no_signal drop;
    rating_control (counted) is for the label that would otherwise SCORE."""
    from bulk_downloader import detect

    class _El:
        def evaluate(self, *_a):
            return False

        def get_attribute(self, _name):
            return None

    got = detect._candidate_admission(_El(), label, label=label)
    assert got == reason, (label, got)
