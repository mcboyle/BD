"""fx-xhamster-member-quality (O1568d, spare12 10.0.70.183; results/spare12/xhamster.md, PM ruling ESC-OP-QS2-2350Z #2
Option A): logged in, an xhamster scene page has NO download menu (DOM read of the site's runtime profile: Favorite /
Comments / Share only; window.initials has no downloadDropdownComponent) and a FapHouse pre-roll. The source-list
reader returned [] and the run fell to page-media 240p or the pre-roll's 540p ad (svacdn77.modusygunaciro.com).

The player's own master (read in the same Option A pass, key redacted):
  https://video-nss-b.xhcdn.com/<key>/media=hls4/multi=256x144:144p:,426x240:240p:,854x480:480p:,1280x720:720p.7700b:,
  1920x1080:1080p.7700b:/030/627/130/_TPL_.av1.mp4.m3u8      (videoModel.id 30627130)
With no menu, the reader now offers that master (xhcdn host, the zero-padded id in its path); an ad master or another
scene's master never. Local headless chromium, fixture served by page.route; no live site, no credentials.
"""
import json
from contextlib import contextmanager

import pytest
from playwright.sync_api import sync_playwright

BD_GATE_SCOPE = "module"
SCENE = "https://xhamster.com/videos/fixture-cutie-xh407Av"
MULTI = "multi=256x144:144p:,426x240:240p:,854x480:480p:,1280x720:720p.7700b:,1920x1080:1080p.7700b:"
OWN = f"https://video-nss-b.xhcdn.com/fixturekey==,1/media=hls4/{MULTI}/030/627/130/_TPL_.av1.mp4.m3u8"
OWN_VARIANT = f"https://video-nss-b.xhcdn.com/fixturekey==,1/media=hls4/{MULTI}/030/627/130/144p.av1.mp4.m3u8"
AD = ("https://svacdn77.modusygunaciro.com/fixturekey==,1/media=hlsA/multi=636x360:630x360,442x250:440x250"
      "/videos/d/d/27ba/630x360.mp4.m3u8")
OTHER = f"https://video-nss-b.xhcdn.com/fixturekey==,1/media=hls4/{MULTI}/030/627/131/_TPL_.av1.mp4.m3u8"


def state(menu=False):
    s = {"videoModel": {"id": 30627130, "pageURL": SCENE},
         "xplayerSettings": {"videoId": 30627130, "sources": {"hls": {"av1": {"url": "encrypted"}}}}}
    if menu:
        s["downloadDropdownComponent"] = {"videoId": 30627130, "sources": {
            "mp4": {"480p": "https://video7.xhcdn.com/fixture/480p.h264.mp4?x=1"}}}
    return s


@contextmanager
def page(data, fetched):
    html = ("<html><body><div class='xplayer'></div><script>window.initials=" + json.dumps(data) + ";"
            + "".join(f"fetch({json.dumps(u)}).catch(()=>{{}});" for u in fetched)
            + "window.__done=true;</script></body></html>")

    def handler(route):
        u = route.request.url
        if u == SCENE:
            route.fulfill(content_type="text/html", body=html)
        elif ".m3u8" in u:
            route.fulfill(content_type="application/vnd.apple.mpegurl", body="#EXTM3U\n")
        else:
            route.abort()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            pg = browser.new_page()
            pg.route("**/*", handler)
            pg.goto(SCENE, wait_until="load")
            pg.wait_for_function("() => window.__done === true")
            pg.wait_for_timeout(400)
            yield pg
        finally:
            browser.close()


def test_member_page_offers_the_players_own_master():
    """THE ROW: no menu, the scene's xhcdn master (id path) is offered at its top height; the ad is not."""
    from bulk_downloader import spa_media_extract as spa

    with page(state(), [AD, OWN, OWN_VARIANT]) as pg:
        got = spa.xhamster_source_candidates(SCENE, pg)
    assert [(c["url"], c["height"], c["source"]) for c in got] == [(OWN, 1080, "xhamster-player-hls")], (
        f"FX_XHAMSTER_MEMBER_NO_SOURCE: a member page (no download menu) offered {got!r}; the run falls to "
        "page-media 240p / the pre-roll ad")


def test_control_published_menu_still_wins_and_hls_is_not_read():
    from bulk_downloader import spa_media_extract as spa

    with page(state(menu=True), [OWN]) as pg:
        got = spa.xhamster_source_candidates(SCENE, pg)
    assert [c["source"] for c in got] == ["xhamster-menu"] and got[0]["height"] == 480, got


@pytest.mark.parametrize("urls", [[AD], [OTHER], [OWN_VARIANT], []])
def test_control_ads_other_scenes_and_variants_are_never_offered(urls):
    from bulk_downloader import spa_media_extract as spa

    assert spa._xhamster_player_hls("30627130", urls) == []


def test_control_another_page_is_not_read():
    from bulk_downloader import spa_media_extract as spa

    with page(state(), [OWN]) as pg:
        assert spa.xhamster_source_candidates("https://xhamster.com/videos/another-scene-xhZZZ", pg) == []
