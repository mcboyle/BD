"""tpl95-xhamster-1: a hidden score-0 winner whose own href IS the file was
routed to the reveal probe, which clicked a related-video tile.

MEASURED on test2 2026-09-29 (history id 277, site aa825daa, scene
``/videos/army-girls-are-so-naughty-to-share-this-handsome-dudes-xh2wxk8``;
journal ``harness-work/FIX/tpl95-xhamster-1-bd-worker-B13-B/
journal-test2-004345-005215.txt``; screenshot ``.../test2-history-277.png``):

    download: ... admitted without identity proof ...
    download: opened reveal 'Download' -> 14 option(s), picked 4K 29:17
    needs_review: Clicked but no download started -- scored ok but no download
      fired. Saw: auto(?):https://video5.xhcdn.com/key=i | auto(?):Download

"4K 29:17" is the badge and duration of a RELATED-VIDEO tile in the scene's
sidebar (visible in the screenshot); the scene itself is 08:12.

The winner was xhamster's no-JS player fallback, captured from the live page:

    <a href="https://video5.xhcdn.com/key=...,end=...,limit=3/data=.../
       referer=force,.xhcdn.com,.xhamster.com/speed=0/031/140/395/480p.h264.mp4"
       target="_blank" class="player-container__no-player xplayer
       xplayer-fallback-image xh-helper-hidden" ...>

It is hidden (``xh-helper-hidden``), so row 759 zeroes its score and
``res_label`` prints ``auto``.  In ``_do_download`` the score-0 dropdown/reveal
probe (row 722 G9/G20) runs BEFORE the winner's own href is read for
``_stream_route``/``_direct_media_route`` (row 384) -- although the reveal
helper's own comment assumes "the winner's own href, if any, was already
refused above".  The probe clicked the visible "Download" button, collected the
tiles that became visible, and replaced the winner with one.

The contract: a winner whose own href routes (direct media file or manifest)
is fetched on that href; the dropdown/reveal probe is for a winner that has no
usable href.  Fixture hosts are ``.example``; nothing live is touched.
"""

# The gate parses a module-level ASSIGNMENT, not a docstring line.
BD_GATE_SCOPE = "module"

import os
from contextlib import contextmanager

import pytest

ORIGIN = "https://xhamster.example"
SCENE_URL = ORIGIN + "/videos/army-girls-are-so-naughty-xh2wxk8"
# The live href's shape with the signing segments zeroed (A4).
MEDIA_URL = ("https://video5.xhcdn.example/key=0000,end=0000000000,limit=3/"
             "data=0.0.0.0-dvp/referer=force,.xhcdn.example,.xhamster.example/"
             "speed=0/031/140/395/480p.h264.mp4")
TILE_URL = ORIGIN + "/videos/the-best-way-celebrate-xh0tile"

_SCENE_HTML = """<!doctype html><html><head><title>Army girls</title>
<style>.xh-helper-hidden{display:none}</style></head><body>
<h1>Army girls are so naughty to share this handsome dudes</h1>
<a href="%(media)s" target="_blank"
   class="player-container__no-player xplayer xplayer-fallback-image xh-helper-hidden"></a>
<div id="player">00:00 / 08:12</div>
%(own_button)s
<div id="related" style="display:none">
  <a href="%(tile)s">4K 29:17 The Best Way Celebrate</a>
  <a href="%(tile)s-2">SD 16:33 Charming Blonde</a>
</div>
<script>
var b = document.getElementById('dl');
if (b) b.addEventListener('click', function () {
  // sessionStorage: the base path then clicks a tile, which navigates.
  sessionStorage.setItem('revealed',
      String(Number(sessionStorage.getItem('revealed') || 0) + 1));
  document.getElementById('related').style.display = 'block';
});
</script>
</body></html>"""

_DL_BUTTON = '<button id="dl" type="button">Download</button>'

CHROME = os.environ.get("BD_PW_CHROME", "")


def _launch(p):
    from playwright.sync_api import Error as PWError
    args = ["--no-sandbox", "--disable-dev-shm-usage"]
    kw = {"executable_path": CHROME} if CHROME and os.path.exists(CHROME) else {}
    try:
        return p.chromium.launch(headless=True, timeout=20000, args=args, **kw)
    except PWError as e:
        pytest.fail(f"chromium not launchable here -- a launch failure is a "
                    f"FAILURE, not a skip (T5): {e}")


@contextmanager
def _scene_page(html):
    from playwright.sync_api import sync_playwright

    def _serve(route, request):
        if request.url.split("?", 1)[0] == SCENE_URL:
            route.fulfill(status=200, content_type="text/html", body=html)
        elif request.url.startswith(ORIGIN):
            route.fulfill(status=200, content_type="text/html",
                          body="<html><body>other video</body></html>")
        else:
            route.abort()

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": 1000, "height": 760})
            page.route("**/*", _serve)
            page.goto(SCENE_URL, wait_until="load")
            yield page
        finally:
            browser.close()


class _Routed(Exception):
    pass


def _route(monkeypatch, tmp_path, page, best):
    """Drive _do_download to its routing decision.  Returns the direct URL it
    chose, or None with the runner's messages when it clicked instead."""
    from bulk_downloader import runner_transport as rt

    def _direct(url, name):
        raise _Routed(url)

    monkeypatch.setattr(rt, "_DirectURLDownload", _direct)
    monkeypatch.setattr(rt, "_arm_popup_grant_capture",
                        lambda page: (lambda: None, lambda: None))
    monkeypatch.setattr(rt, "gate_candidate_url", lambda *a, **k: ("", ""))
    monkeypatch.setattr(rt, "db_log", lambda *a, **k: None)
    r = rt.TransportMixin.__new__(rt.TransportMixin)
    r.config = {"name": "fixture", "quality_preference": "best"}
    r.site_id = 1
    r.jobs = {}
    r.messages = []
    r._FIRST_DOWNLOAD_TIMEOUT_MS = 3000
    r._update_job = lambda url, status, msg, **kw: r.messages.append((status, msg))
    r._screenshot = lambda page, url: ""
    try:
        r._do_download(page, None, SCENE_URL, best, tmp_path, "?")
    except _Routed as routed:
        return str(routed), r.messages
    return None, r.messages


def _winner(page):
    from bulk_downloader.detect import find_best_download
    best = find_best_download(page)
    assert best, "the wide sweep found nothing on the fixture"
    return best


def test_the_hidden_media_href_winner_is_fetched_not_revealed(monkeypatch, tmp_path):
    html = _SCENE_HTML % {"media": MEDIA_URL, "tile": TILE_URL,
                          "own_button": _DL_BUTTON}
    with _scene_page(html) as page:
        best = _winner(page)
        # Precondition: the measured shape -- the hidden fallback wins at score 0.
        assert best["locator"].get_attribute("href") == MEDIA_URL, best["text"]
        assert best["score"] == 0, best["score"]
        url, messages = _route(monkeypatch, tmp_path, page, best)
        revealed = page.evaluate(
            "() => Number(sessionStorage.getItem('revealed') || 0)")
    assert url == MEDIA_URL and not revealed, (
        "TPL95-XH1: a score-0 winner whose own href is a media file was sent "
        f"to the reveal probe (Download clicked {revealed}x) instead of a "
        f"direct fetch; routed to {url!r}; runner said {messages[-2:]!r}")


def test_control_a_winner_without_a_usable_href_still_opens_the_reveal(
        monkeypatch, tmp_path):
    """Row 722 G20 is kept: with no media href on the winner, the bare
    Download button is still revealed.  Holds on base."""
    html = (_SCENE_HTML % {"media": "#", "tile": TILE_URL,
                           "own_button": _DL_BUTTON})
    with _scene_page(html) as page:
        best = _winner(page)
        assert best["score"] == 0, best["score"]
        _url, _messages = _route(monkeypatch, tmp_path, page, best)
        revealed = page.evaluate(
            "() => Number(sessionStorage.getItem('revealed') || 0)")
    assert revealed >= 1, (
        "TPL95-XH1 control: the reveal probe no longer runs for a score-0 "
        "winner that has no usable href")
