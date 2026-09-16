"""Row 722 G9b (site-ma.bangbros.com/scene/11522485, 2026-09-15, logged in):
the scene page's ``button "Download"`` opens a menu of href-less BUTTONs

    h264 - 2160p / h264 - 1080p / h264 - 720p / h264 - 480p

whose click hands the grant to ``window.open('/movieaction/download/...')``.
The G20 reveal opener judged the reveal on ANCHORS only and refused with

    reveal 'Download' revealed no download option (0 new anchor(s) became visible)

Fix at the owner (runner_transport G9/G20): a VISIBLE ``button`` /
``[role=menuitem]`` / ``[role=option]`` / ``li`` that BECAME visible after the
trigger and whose text names a tier or codec is an option too; it is ranked by
the same ``_pick_download_option`` and, having no href, is CLICKED by the
existing click-only path (row 760 popup-grant capture / expect_download). An
upsell control (join|upgrade|buy|purchase|trial|$) is never an option.

Local headless chromium, inline fixtures served by ``page.route``. NO LIVE
SITE IS TOUCHED; a missing browser SKIPS.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://fixture-722-hrefless.test"
SCENE_URL = ORIGIN + "/scene/11522485/a-scene"
UPSELL_URL = ORIGIN + "/scene/11522486/upsell"
MIXED_URL = ORIGIN + "/scene/11522487/mixed"
GRANT_PATH = "/movieaction/download/1/%s/mp4"

# The menu is display:none until the bare trigger's click handler shows it;
# a quality BUTTON's click hands the grant to window.open (Aylo shape).
_MENU_JS = """
<script>
document.addEventListener('click', function (ev) {
  var t = ev.target.closest('button.dl-trigger');
  if (t) {
    var m = document.getElementById('dl-menu');
    if (m) m.style.display = 'block';
    ev.preventDefault();
    return;
  }
  var q = ev.target.closest('button[data-tier]');
  if (q) {
    window.__bd_test_clicked = (window.__bd_test_clicked || []).concat([q.innerText.trim()]);
    window.open('%s'.replace('%%s', q.getAttribute('data-tier')));
    ev.preventDefault();
    return;
  }
  var u = ev.target.closest('button.upsell');
  if (u) {
    window.__bd_test_clicked = (window.__bd_test_clicked || []).concat([u.innerText.trim()]);
    window.open('/join?plan=premium');
    ev.preventDefault();
  }
});
</script>
<style>#dl-menu{display:none}</style>
""" % GRANT_PATH

SCENE_HTML = """<!doctype html><html><head><title>Scene 11522485</title>
%s</head><body>
<h1>A Scene</h1>
<video src="/stream/11522485.m3u8"></video>
<div class="actions">
  <button class="dl-trigger" type="button">Download</button>
</div>
<div id="dl-menu" role="menu">
  <button type="button" data-tier="2160p">h264 - 2160p</button>
  <button type="button" data-tier="1080p">h264 - 1080p</button>
  <button type="button" data-tier="720p">h264 - 720p</button>
  <button type="button" data-tier="480p">h264 - 480p</button>
</div>
</body></html>""" % _MENU_JS

# Negative control (a): the reveal offers only an upsell control.
UPSELL_HTML = """<!doctype html><html><head><title>Scene 11522486</title>
%s</head><body>
<h1>Upsell</h1>
<button class="dl-trigger" type="button">Download</button>
<div id="dl-menu" role="menu">
  <button type="button" class="upsell">UPGRADE $9.99 for 4K download</button>
</div>
</body></html>""" % _MENU_JS

# Negative control (b): anchors AND buttons appear; the anchor is preferred.
MIXED_HTML = """<!doctype html><html><head><title>Scene 11522487</title>
%s</head><body>
<h1>Mixed</h1>
<button class="dl-trigger" type="button">Download</button>
<div id="dl-menu">
  <button type="button" data-tier="2160p">h264 - 2160p</button>
  <a href="/files/scene_1080p.mp4">Download 1080p</a>
</div>
</body></html>""" % _MENU_JS

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
    def __init__(self):
        self.paths = []


def _serve(hits):
    def handler(route, request):
        url = request.url
        path = url.split("?", 1)[0][len(ORIGIN):]
        hits.paths.append(path)
        if path.endswith((".mp4", ".zip")) or "/movieaction/" in path:
            route.fulfill(status=200, content_type="application/octet-stream",
                          headers={"Content-Disposition":
                                   'attachment; filename="scene.mp4"'},
                          body=b"\x00" * 64)
        elif url.startswith(UPSELL_URL):
            route.fulfill(status=200, content_type="text/html", body=UPSELL_HTML)
        elif url.startswith(MIXED_URL):
            route.fulfill(status=200, content_type="text/html", body=MIXED_HTML)
        elif url.startswith(SCENE_URL):
            route.fulfill(status=200, content_type="text/html", body=SCENE_HTML)
        else:
            route.fulfill(status=200, content_type="text/html",
                          body="<html><body>other</body></html>")
    return handler


@contextmanager
def _scene_page(url=SCENE_URL):
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


def _clicked(page):
    return page.evaluate("() => window.__bd_test_clicked || []")


def _pick(page, quality_preference="best", min_resolution=0):
    from bulk_downloader.runner_transport import _open_dropdown_download_options
    best = _best(page)
    assert best["score"] == 0, (
        "the fixture must be a score-0 reveal shape; got %r" % (best["text"],))
    return _open_dropdown_download_options(page, best, quality_preference,
                                           min_resolution)


def _click_and_capture(page, option):
    """What _do_download does for a pick with no href: arm the row 760
    popup-grant capture, click, read the grant."""
    from bulk_downloader.runner_transport import (
        _arm_popup_grant_capture, _is_click_only_download_grant)
    href = option["locator"].get_attribute("href") or ""
    assert _is_click_only_download_grant(href), href
    read, disarm = _arm_popup_grant_capture(page)
    try:
        option["locator"].click(timeout=5000)
        return read()
    finally:
        disarm()


def test_the_row_href_less_tier_buttons_are_options_and_the_2160p_one_is_clicked(capsys):
    """THE ROW: the trigger reveals 4 href-less tier BUTTONs; 2160p is picked
    and its click hands a window.open grant that the capture takes."""
    with _scene_page() as (page, hits):
        picked = _pick(page, "best", 0)
        assert picked and picked.get("option"), (
            "the href-less tier buttons were not taken as options; the runner "
            "refused with: %r" % ((picked or {}).get("reason"),))
        opt = picked["option"]
        assert opt["score"] == 2160, opt
        assert "2160p" in opt["text"], opt
        assert picked["count"] == 4, picked
        grant = _click_and_capture(page, opt)
        assert grant == ORIGIN + GRANT_PATH % "2160p", grant
        assert _clicked(page) == ["h264 - 2160p"], _clicked(page)
        err = capsys.readouterr().err
        assert ("download: opened reveal 'Download' -> 4 option(s) (4 href-less), "
                "picked h264 - 2160p (2160p) via click") in err, err


def test_quality_preference_picks_the_1080p_button():
    with _scene_page() as (page, hits):
        picked = _pick(page, "1080p", 0)
        assert picked and picked.get("option"), picked
        assert picked["option"]["score"] == 1080, picked["option"]
        grant = _click_and_capture(page, picked["option"])
        assert grant == ORIGIN + GRANT_PATH % "1080p", grant
        assert _clicked(page) == ["h264 - 1080p"], _clicked(page)


def test_negative_a_an_upsell_only_menu_is_refused_and_nothing_is_clicked(capsys):
    with _scene_page(UPSELL_URL) as (page, hits):
        picked = _pick(page, "best", 0)
        assert not (picked and picked.get("option")), (
            "an UPGRADE $ control was taken as a download option: %r" % (picked,))
        assert picked and "revealed no download option" in picked["reason"], picked
        assert _clicked(page) == [], _clicked(page)
        assert not any("/join" in p for p in hits.paths), hits.paths
        err = capsys.readouterr().err
        assert "revealed no download option" in err, err


def test_negative_b_an_anchor_is_preferred_over_a_button_when_both_appear(capsys):
    """The 2160p BUTTON outranks the 1080p anchor by tier, yet the href-bearing
    anchor is preferred: a static URL beats a JS-only grant."""
    with _scene_page(MIXED_URL) as (page, hits):
        picked = _pick(page, "best", 0)
        assert picked and picked.get("option"), picked
        opt = picked["option"]
        href = opt["locator"].get_attribute("href") or ""
        assert href.endswith("/files/scene_1080p.mp4"), (href, opt)
        assert picked["count"] == 2, picked
        assert _clicked(page) == [], _clicked(page)
        err = capsys.readouterr().err
        assert ("download: opened reveal 'Download' -> 2 option(s) (1 href-less), "
                "picked Download 1080p") in err, err
        assert "via click" not in err, err


def test_negative_c_min_resolution_unmet_is_refused_and_nothing_is_clicked():
    with _scene_page() as (page, hits):
        picked = _pick(page, "best", 4320)
        assert not (picked and picked.get("option")), picked
        assert picked and picked["reason"], picked
        assert _clicked(page) == [], _clicked(page)
