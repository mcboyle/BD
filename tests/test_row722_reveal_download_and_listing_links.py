"""Row 722 G20 (nookies.com/membersarea/video/3480, 2026-09-15 09:2xZ, logged
in): the runner refused with

    Clicked but no download started -- ... Saw: 4K(?):4k .../membersarea/tag/4k

because the scorer took ``<a href="/membersarea/tag/4k">4k</a>`` -- a TAG
listing link -- as the 4K download.  The real control is a bare

    <button>Download</button>

with no href and no dropdown attribute, whose click opens a modal revealing
HIDDEN anchors ``Full quality video / Save the video`` (href
``/membersarea/video/stream/3480``) and ``Photo gallery (ZIP)``.

Two fixes at the owners: (a) an anchor whose path is a listing shape
(/tag/, /tags/, /category/, /categories/, /search, /model(s)/) is never a
download candidate whatever its text; (b) G9's dropdown opener also treats a
bare "Download" button as a reveal trigger: click it, wait for newly visible
media anchors, pick by quality preference, video before zip.

Local headless chromium, inline fixtures served by ``page.route``.  NO LIVE
SITE IS TOUCHED; a missing browser SKIPS.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://fixture-722-reveal.test"
VIDEO_URL = ORIGIN + "/membersarea/video/3480"
EMPTY_REVEAL_URL = ORIGIN + "/membersarea/video/3481"
DIRECT_URL = ORIGIN + "/membersarea/video/3482"
ZIP_ONLY_URL = ORIGIN + "/membersarea/video/3483"
STREAM_PATH = "/membersarea/video/stream/3480"

# The modal is display:none until the bare button's click handler shows it.
_REVEAL_JS = """
<script>
document.addEventListener('click', function (ev) {
  var b = ev.target.closest('button.dl-trigger');
  if (b) {
    var m = document.getElementById('dl-modal');
    if (m) m.style.display = 'block';
    ev.preventDefault();
  }
});
</script>
<style>#dl-modal{display:none}</style>
"""

_TAGS = """
<div class="tags">
  <a href="/membersarea/tag/4k">4k</a>
  <a href="/membersarea/tag/1080p">1080p</a>
  <a href="/membersarea/category/hd">HD</a>
  <a href="/membersarea/models/jane-doe">Jane Doe</a>
  <a href="/membersarea/search?q=4k">Search 4k</a>
</div>
"""

VIDEO_HTML = """<!doctype html><html><head><title>Video 3480</title>
%s</head><body>
<nav><a href="/membersarea">Members</a></nav>
<h1>Scene 3480</h1>
<video src="%s" controls></video>
%s
<div class="actions">
  <button class="flex flex-col items-center gap-2 cursor-pointer dl-trigger" type="button">Download</button>
</div>
<div id="dl-modal">
  <a href="%s">Full quality video / Save the video</a>
  <a href="/membersarea/video/3480/images.zip">Photo gallery (ZIP)</a>
</div>
</body></html>""" % (_REVEAL_JS, STREAM_PATH, _TAGS, STREAM_PATH)

# Negative control 1: the button reveals nothing (empty modal).
EMPTY_REVEAL_HTML = """<!doctype html><html><head><title>Video 3481</title>
%s</head><body>
<h1>Scene 3481</h1>
%s
<button class="dl-trigger" type="button">Download</button>
<div id="dl-modal"><p>Coming soon</p></div>
</body></html>""" % (_REVEAL_JS, _TAGS)

# Negative control 2: a real direct anchor beside the tags; no reveal needed.
DIRECT_HTML = """<!doctype html><html><head><title>Video 3482</title>
%s</head><body>
<h1>Scene 3482</h1>
%s
<a class="btn" href="/membersarea/video/3482/scene_1080p.mp4" download>Download 1080p</a>
<button class="dl-trigger" type="button">Download</button>
<div id="dl-modal"><a href="/membersarea/video/3482/images.zip">Photo gallery (ZIP)</a></div>
</body></html>""" % (_REVEAL_JS, _TAGS)

# Negative control 3: the reveal offers only a zip (no video alternative).
ZIP_ONLY_HTML = """<!doctype html><html><head><title>Video 3483</title>
%s</head><body>
<h1>Scene 3483</h1>
%s
<button class="dl-trigger" type="button">Download</button>
<div id="dl-modal"><a href="/membersarea/video/3483/images.zip">Download photo set (ZIP)</a></div>
</body></html>""" % (_REVEAL_JS, _TAGS)

# Row 722: an operator-named chrome only (BD_PW_CHROME). The retired sandbox
# home default that tests/test_element_pick_selector.py still carries is a
# ratcheted population (tests/test_sandbox_home_stays_retired.py) and exists
# on no host this runs on; the fallback below is Playwright's own chromium.
CHROME = os.environ.get("BD_PW_CHROME", "")


def _launch(p):
    args = ["--no-sandbox", "--disable-dev-shm-usage"]
    if CHROME and os.path.exists(CHROME):
        try:
            return p.chromium.launch(headless=True, timeout=20000, args=args,
                                     executable_path=CHROME)
        except Exception:
            pass
    try:
        return p.chromium.launch(headless=True, timeout=20000, args=args)
    except Exception as e:
        pytest.fail(f"chromium not launchable here -- a launch failure is a FAILURE, not a skip (T5): {e}")


class _Hits:
    """Every request the fixture origin saw, so a test can prove a tag link
    was never followed."""
    def __init__(self):
        self.paths = []


def _serve(hits):
    def handler(route, request):
        url = request.url
        path = url.split("?", 1)[0][len(ORIGIN):]
        hits.paths.append(path)
        if "/stream/" in path or path.endswith((".mp4", ".zip")):
            name = path.rsplit("/", 1)[-1]
            if "/stream/" in path:
                name = f"video_{name}.mp4"
            route.fulfill(status=200, content_type="application/octet-stream",
                          headers={"Content-Disposition":
                                   f'attachment; filename="{name}"'},
                          body=b"\x00" * 64)
        elif "/tag/" in path or "/category/" in path or "/models/" in path \
                or "/search" in path:
            route.fulfill(status=200, content_type="text/html",
                          body="<html><body><h1>Listing</h1></body></html>")
        elif url.startswith(EMPTY_REVEAL_URL):
            route.fulfill(status=200, content_type="text/html", body=EMPTY_REVEAL_HTML)
        elif url.startswith(DIRECT_URL):
            route.fulfill(status=200, content_type="text/html", body=DIRECT_HTML)
        elif url.startswith(ZIP_ONLY_URL):
            route.fulfill(status=200, content_type="text/html", body=ZIP_ONLY_HTML)
        else:
            route.fulfill(status=200, content_type="text/html", body=VIDEO_HTML)
    return handler


@contextmanager
def _scene_page(url=VIDEO_URL):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": 1000, "height": 760})
            hits = _Hits()
            page.route(ORIGIN + "/**", _serve(hits))
            page.goto(url, wait_until="load")
            yield page, hits
        finally:
            browser.close()


def _best(page):
    from bulk_downloader.detect import find_best_download
    best = find_best_download(page)
    assert best, "the wide sweep found nothing on the fixture"
    return best


def _listing_hits(hits):
    return [p for p in hits.paths
            if any(s in p for s in ("/tag/", "/category/", "/models/", "/search"))]


def _texts(best):
    return [c["text"] for c in best.get("_all_candidates", []) or []] + [best["text"]]


def test_the_row_listing_links_are_not_candidates_and_reveal_picks_the_stream(capsys):
    """THE ROW: the /tag/4k link is not the winner, the bare Download button
    reveals the modal, /video/stream/3480 is chosen, no tag link is followed."""
    from bulk_downloader.runner_transport import _open_dropdown_download_options
    with _scene_page() as (page, hits):
        best = _best(page)
        assert "/tag/" not in best["text"] and best["score"] == 0, (
            "the scorer picked the TAG listing link as the download: %r"
            % (best["text"],))
        assert not any("/tag/" in t or "/category/" in t or "/models/" in t
                       or "/search" in t for t in _texts(best)), _texts(best)
        picked = _open_dropdown_download_options(page, best, "best", 0)
        assert picked and picked.get("option"), (
            "the bare 'Download' button was never treated as a reveal "
            "trigger; the runner would refuse with the modal-trigger hint: %r"
            % (picked,))
        opt = picked["option"]
        href = opt["locator"].get_attribute("href") or ""
        assert href.endswith(STREAM_PATH), href
        assert opt["locator"].is_visible()
        assert picked["toggle_label"] == "Download", picked
        with page.expect_download(timeout=10000) as dli:
            opt["locator"].click()
        assert dli.value.suggested_filename == "video_3480.mp4"
        assert _listing_hits(hits) == [], hits.paths
        err = capsys.readouterr().err
        assert "download: skipped listing link '4k' (/membersarea/tag/4k)" in err, err
        # 2 options were revealed (video + photo ZIP); the video wins.
        assert ("download: opened reveal 'Download' -> 2 option(s), picked "
                "Full quality video / Save the video") in err, err
        assert "skipped listing link 'Search 4k' (/membersarea/search)" in err, err


def test_positive_control_a_stream_or_mp4_anchor_keeps_its_score():
    """An anchor to /video/stream/<id> or a .mp4 is untouched by the listing
    rule; the tag beside it still loses."""
    with _scene_page(DIRECT_URL) as (page, hits):
        best = _best(page)
        assert best["score"] == 1080 and "scene_1080p.mp4" in best["text"], best["text"]
        assert not any("/tag/" in t for t in _texts(best)), _texts(best)


def test_negative_a_reveal_that_shows_nothing_is_refused_and_no_tag_is_clicked(capsys):
    from bulk_downloader.runner_transport import _open_dropdown_download_options
    with _scene_page(EMPTY_REVEAL_URL) as (page, hits):
        best = _best(page)
        assert best["score"] == 0 and "/tag/" not in best["text"], best["text"]
        picked = _open_dropdown_download_options(page, best, "best", 0)
        assert not (picked and picked.get("option")), picked
        assert picked and "revealed no download option" in picked["reason"], picked
        assert _listing_hits(hits) == [], hits.paths
        err = capsys.readouterr().err
        assert "download: reveal 'Download' revealed no download option" in err, err


def test_negative_a_real_download_anchor_is_taken_without_any_reveal():
    from bulk_downloader.runner_transport import _open_dropdown_download_options
    with _scene_page(DIRECT_URL) as (page, hits):
        best = _best(page)
        assert best["score"] == 1080, best["text"]
        picked = _open_dropdown_download_options(page, best, "best", 0)
        assert picked is None, picked
        assert page.evaluate(
            "() => getComputedStyle(document.getElementById('dl-modal')).display"
        ) == "none"


def test_negative_a_zip_only_reveal_picks_the_zip():
    from bulk_downloader.runner_transport import _open_dropdown_download_options
    with _scene_page(ZIP_ONLY_URL) as (page, hits):
        best = _best(page)
        picked = _open_dropdown_download_options(page, best, "best", 0)
        assert picked and picked.get("option"), picked
        href = picked["option"]["locator"].get_attribute("href") or ""
        assert href.endswith("/images.zip"), href
        assert _listing_hits(hits) == [], hits.paths


def test_a_listing_filter_query_link_is_a_listing_link_too():
    """dfxtra/evilangel live (10:3xZ): the '4K' candidate was
    /en/videos/?refinementList[quality][0]=2160p -- a filter view, not a file."""
    from bulk_downloader import detect
    assert detect._listing_link_path(
        "4K https://members.dfxtra.test/en/videos/?refinementList%5Bquality%5D%5B0%5D=2160p") != ""
    assert detect._listing_link_path(
        "4K https://members.dfxtra.test/en/videos/?refinementList[quality][0]=2160p") != ""
    # Positive controls: a scene page with an innocuous query, a stream URL
    # with a signature query, and a plain .mp4 stay candidates.
    assert detect._listing_link_path("https://members.dfxtra.test/en/video/dfxtra/A-Spicy/288921?ref=home") == ""
    assert detect._listing_link_path("https://cdn.test/movieaction/download/1/2160p/mp4?codec=h264&filters=1") == ""
    assert detect._listing_link_path("https://cdn.test/x/scene.mp4?sort=1") == ""

