"""dl95-discover-login-gate-1 (PM RULING-pm-A4-A-inbox-batch-2218Z item 3; queue APP-DL-FIX dl95-discover-login-gate-1).

Live on .95 (1e334559, 3.66.1857), discover https://porndoe.com/videos newest_n=1 ended NOT_LOGGED_IN with 0
discovered (harness-work/O1789/landing/dl95/RESULT-dl95-porndoe-1-2210Z.md step 2), although the listing it walked
(tester-S140-A/porndoe/listing-videos.html, 169129 B, sha256 49f05499d892cba7..., 31 /watch/<id> cards) is a
public page with no login wall. The login gate was not the defect's origin: porndoe's scene link is an EMPTY
overlay anchor (a.video-item-link) and the thumbnail is its sibling <svg data-src=...> inside .video-item-thumb.
The anchor collector looked for a card thumbnail only around href-less router links, so every /watch/ anchor read
has_img=False, no thumbnail cohort existed, _public_listing_evidence had no scenes, and the run was called a
login wall.

_CARDS below is the first six cards of that saved listing, verbatim except blank lines dropped. Served on an
ephemeral loopback port and driven through the real crawl_with_page with BD's cloak page; no live site is contacted.
Controls: the same cards with their thumbnails removed find no cohort and stay NOT_LOGGED_IN (the sibling
thumbnail is what admits them); a site that declares a login stays fail-closed on these cards; the same cards on a
login URL stay NOT_LOGGED_IN.
"""
from __future__ import annotations

import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

BD_GATE_SCOPE = "module"

# Saved listing tester-S140-A/porndoe/listing-videos.html, bytes 99730-109535 (cards 1-6), blank lines dropped.
_CARDS = """\
<div class="video-item" data-video-item data-fake-key="0" data-exclusive="false" data-duration="11:49" data-vr="false" data-hd="true" data-views="109">
    <div class="video-item-thumb">
        <div class="video-item-block">
                        <svg class="video-item-svg" viewBox="0 0 390 219" data-src="https://p.cdnc.porndoe.com/image/movie/crop/390x219/2/3/8/6/9/4/3/6a9d0005a234b-565_1788674742_14.jpg" data-first="0" data-webp="true">
                <rect width="100%" height="100%" fill="none"></rect>
            </svg>
        </div>
        <a class="video-item-link" target="_self" href="/watch/pd9v8b2a8c9s" aria-label="Hot Brittany Loves Fucking On Her Own Naughty Rules!" v-preview data-source="https://p.cdnc.porndoe.com/movie/2/3/8/6/9/4/3/6a9d0005a234b-565-preview.mp4" v-under="10503"></a>
            </div>
    <span class="video-item-title" v-follow="'L3dhdGNoL3BkOXY4YjJhOGM5cw=='">
        Hot Brittany Loves Fucking On Her Own Naughty Rules!    </span>
    <div class="video-item-bottom">
                                                            <a class="video-item-site" href="/channel-profile/brittany-andrews">
                    Brittany Andrews                </a>
                    <span class="video-item-gap"></span>
                                                <a class="video-item-actor" data-vi="actor-0" href="/pornstars-profile/brittany-andrews" title="Brittany Andrews">
                        Brittany Andrews                    </a>
                                        </div>
</div>
<div class="video-item" data-video-item data-fake-key="1" data-exclusive="false" data-duration="10:09" data-vr="false" data-hd="true" data-views="109">
    <div class="video-item-thumb">
        <div class="video-item-block">
                        <svg class="video-item-svg" viewBox="0 0 390 219" data-src="https://p.cdnc.porndoe.com/image/movie/crop/390x219/2/3/8/6/6/9/3/6a9a917d3aa9b-283_1788515127_15.jpg" data-first="1" data-webp="true">
                <rect width="100%" height="100%" fill="none"></rect>
            </svg>
        </div>
        <a class="video-item-link" target="_self" href="/watch/pd8i1u5k2g2y" aria-label="Brunette Passionate Hardcore Sex On Bed In Multiple Positions" v-preview data-source="https://p.cdnc.porndoe.com/movie/2/3/8/6/6/9/3/6a9a917d3aa9b-283-preview.mp4" v-under="7096"></a>
            </div>
    <span class="video-item-title" v-follow="'L3dhdGNoL3BkOGkxdTVrMmcyeQ=='">
        Brunette Passionate Hardcore Sex On Bed In Multiple Positions    </span>
    <div class="video-item-bottom">
                                                            <a class="video-item-site" href="/channel-profile/love-home-porn">
                    Love Home Porn                </a>
            </div>
</div>
<div class="video-item" data-video-item data-fake-key="2" data-exclusive="false" data-duration="03:04" data-vr="false" data-hd="true" data-views="109">
    <div class="video-item-thumb">
        <div class="video-item-block">
                        <svg class="video-item-svg" viewBox="0 0 390 219" data-src="https://p.cdnc.porndoe.com/image/movie/crop/390x219/2/3/8/6/6/9/1/main-thumb-6a9a91ec7bedc.jpg" data-first="2" data-webp="true">
                <rect width="100%" height="100%" fill="none"></rect>
            </svg>
        </div>
        <a class="video-item-link" target="_self" href="/watch/pd1b0t0d6x5y" aria-label="Summer’s Not Over Until I Say So!" v-preview data-source="https://p.cdnc.porndoe.com/movie/2/3/8/6/6/9/1/6a9a91c14adc0-550-preview.mp4" v-under="465"></a>
            </div>
    <span class="video-item-title" v-follow="'L3dhdGNoL3BkMWIwdDBkNng1eQ=='">
        Summer’s Not Over Until I Say So!    </span>
    <div class="video-item-bottom">
                                                            <a class="video-item-site" href="/channel-profile/lara-s-playground">
                    Lara's Playground                </a>
                    <span class="video-item-gap"></span>
                                                <a class="video-item-actor" data-vi="actor-0" href="/pornstars-profile/lara-latex" title="Lara Latex">
                        Lara Latex                    </a>
                                        </div>
</div>
                    <section class="advertisement-mobile" v-if="!device.desktop && service.banners.available">
    <div class="advertisement-mobile-holder">
        <div v-if="service.banners.company.mobile.middle" :data-provider="service.banners.company.mobile.middle" v-include="/views/banners/owners.html"></div>
        <div v-if="service.banners.provider.mobile.middle" :data-provider="service.banners.provider.mobile.middle" v-include="/views/banners/choices.html"></div>
        <div v-if="!device.tablet" v-html="service.banners.available.tablet.middle"></div>
        <div v-if="!device.mobile" v-html="service.banners.available.mobile.middle"></div>
    </div>
</section>
<div class="video-item" data-video-item data-fake-key="3" data-exclusive="false" data-duration="05:24" data-vr="false" data-hd="true" data-views="109">
    <div class="video-item-thumb">
        <div class="video-item-block">
                        <svg class="video-item-svg" viewBox="0 0 390 219" data-src="https://p.cdnc.porndoe.com/image/movie/crop/390x219/2/3/8/6/6/9/0/main-thumb-6a9a91a83f341.jpg" data-first="3" data-webp="true">
                <rect width="100%" height="100%" fill="none"></rect>
            </svg>
        </div>
        <a class="video-item-link" target="_self" href="/watch/pd9t0e6k0s0f" aria-label="I Taste Just As Good As Mommy! Jessica" v-preview data-source="https://p.cdnc.porndoe.com/movie/2/3/8/6/6/9/0/6a9a854939335-895-preview.mp4" v-under="707"></a>
            </div>
    <span class="video-item-title" v-follow="'L3dhdGNoL3BkOXQwZTZrMHMwZg=='">
        I Taste Just As Good As Mommy! Jessica    </span>
    <div class="video-item-bottom">
                                                            <a class="video-item-site" href="/channel-profile/young-busty">
                    Young Busty                </a>
                    <span class="video-item-gap"></span>
                                                <a class="video-item-actor" data-vi="actor-0" href="/pornstars-profile/jessica-starling" title="Jessica Starling">
                        Jessica Starling                    </a>
                                        </div>
</div>
<div class="video-item" data-video-item data-fake-key="4" data-exclusive="false" data-duration="06:18" data-vr="false" data-hd="true" data-views="109">
    <div class="video-item-thumb">
        <div class="video-item-block">
                        <svg class="video-item-svg" viewBox="0 0 390 219" data-src="https://p.cdnc.porndoe.com/image/movie/crop/390x219/2/3/8/6/6/8/6/main-thumb-6a9a65b4200da.jpg" data-first="4" data-webp="true">
                <rect width="100%" height="100%" fill="none"></rect>
            </svg>
        </div>
        <a class="video-item-link" target="_self" href="/watch/pd2a2a1g0s8c" aria-label="Big Ass Asian Wife Gives White Husband An Amazing Thai Massage" v-preview data-source="https://p.cdnc.porndoe.com/movie/2/3/8/6/6/8/6/6a9a600daa6df-817-preview.mp4" v-under="9739"></a>
            </div>
    <span class="video-item-title" v-follow="'L3dhdGNoL3BkMmEyYTFnMHM4Yw=='">
        Big Ass Asian Wife Gives White Husband An Amazing Thai Massage    </span>
    <div class="video-item-bottom">
                                                            <a class="video-item-site" href="/channel-profile/thai-swinger">
                    Thai Swinger                </a>
            </div>
</div>
<div class="video-item" data-video-item data-fake-key="5" data-exclusive="false" data-duration="10:22" data-vr="false" data-hd="true" data-views="109">
    <div class="video-item-thumb">
        <div class="video-item-block">
                        <svg class="video-item-svg" viewBox="0 0 390 219" data-src="https://p.cdnc.porndoe.com/image/movie/crop/390x219/2/3/8/6/6/8/3/6a9a29fedfbff-708_1788488452_15.jpg" data-first="5" data-webp="true">
                <rect width="100%" height="100%" fill="none"></rect>
            </svg>
        </div>
        <a class="video-item-link" target="_self" href="/watch/pd0f0n1u6w5j" aria-label="My Latina Gf Said This Was The Last Time" v-preview data-source="https://p.cdnc.porndoe.com/movie/2/3/8/6/6/8/3/6a9a29fedfbff-708-preview.mp4" v-under="6278"></a>
            </div>
    <span class="video-item-title" v-follow="'L3dhdGNoL3BkMGYwbjF1Nnc1ag=='">
        My Latina Gf Said This Was The Last Time    </span>
    <div class="video-item-bottom">
                                                            <a class="video-item-site" href="/channel-profile/latina-fuck-tour">
                    Latina Fuck Tour                </a>
                    <span class="video-item-gap"></span>
                                                <a class="video-item-actor" data-vi="actor-0" href="/pornstars-profile/steven-hard" title="Steven Hard">
                        Steven Hard                    </a>
                                        </div>
</div>
"""

_WATCH = re.findall(r'href="(/watch/[^"]+)"', _CARDS)
_BARE = re.sub(r"<svg\b.*?</svg>", "", _CARDS, flags=re.S)


def _listing(cards: str) -> str:
    return ('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
            "<title>Watch All the Best New Free HD Porn Videos on PornDoe.com</title></head>"
            f'<body><div class="videos-listing">{cards}</div></body></html>')


_PAGES = {
    "/videos": _listing(_CARDS),
    "/bare/videos": _listing(_BARE),
    "/login/videos": _listing(_CARDS),
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = _PAGES.get(urlsplit(self.path).path)
        if body is None:
            self.send_error(404)
            return
        raw = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

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
                            viewport={"width": 1280, "height": 720}) as browser_page:
        # The saved cards point their thumbnails and previews at porndoe's CDN; nothing leaves loopback.
        browser_page.route(re.compile(r"^https?://(?!127\.0\.0\.1[:/])"), lambda route: route.abort())
        yield browser_page


def _crawl(page, origin, tmp_path, path, config, site_id="dl95-porndoe"):
    from bulk_downloader import scene_crawler

    queued = []
    result = scene_crawler.crawl_with_page(
        page, site_id=site_id, listing_url=origin + path, site_config=config,
        newest_n=0, max_pages=1, max_scrolls=1, delay_s=0, title_fetch_limit=0,
        db_path=str(tmp_path / f"{site_id}.sqlite"),
        enqueue_fn=lambda sid, url: queued.append(url) or {"added": 1, "dupes": 0, "skipped": 0},
    )
    return result, queued


def test_fixture_is_the_saved_porndoe_listing():
    assert _WATCH == ["/watch/pd9v8b2a8c9s", "/watch/pd8i1u5k2g2y", "/watch/pd1b0t0d6x5y",
                      "/watch/pd9t0e6k0s0f", "/watch/pd2a2a1g0s8c", "/watch/pd0f0n1u6w5j"]
    assert _CARDS.count('<a class="video-item-link"') == 6 and _CARDS.count("<svg") == 6
    assert "<img" not in _CARDS and 'type="password"' not in _CARDS and "<svg" not in _BARE


def test_public_porndoe_listing_with_sibling_thumbnails_is_discovered(page, origin, tmp_path):
    result, queued = _crawl(page, origin, tmp_path, "/videos", {"name": "porndoe"})
    assert result["state"] != "NOT_LOGGED_IN", (
        "dl95-discover-login-gate-1: a public porndoe listing (6 /watch/ cards, thumbnail = sibling <svg data-src> "
        f"of an empty overlay anchor) was refused as NOT_LOGGED_IN: shapes={result.get('scene_shapes')} "
        f"discovered={result.get('discovered')}")
    assert result["state"] == "COMPLETED", result
    assert result["discovered"] == len(_WATCH), result
    assert sorted(queued) == sorted(origin + path for path in _WATCH)
    assert any(shape.startswith("/watch/") for shape in result["scene_shapes"]), result["scene_shapes"]


def test_the_same_cards_without_their_thumbnails_stay_not_logged_in(page, origin, tmp_path):
    result, queued = _crawl(page, origin, tmp_path, "/bare/videos", {"name": "porndoe"}, site_id="dl95-bare")
    assert result["state"] == "NOT_LOGGED_IN", result
    assert result["discovered"] == 0 and queued == []


@pytest.mark.parametrize("config", [
    {"login_url": "https://porndoe.example/login"},
    {"accounts": [{"username": "a", "password": "b"}]},
    {"auth_required": True},
], ids=["login_url", "account", "auth_required"])
def test_a_logged_out_members_site_on_these_cards_stays_not_logged_in(page, origin, tmp_path, config):
    result, queued = _crawl(page, origin, tmp_path, "/videos", config, site_id="dl95-members")
    assert result["state"] == "NOT_LOGGED_IN", result
    assert queued == []


def test_the_cards_on_a_login_url_stay_not_logged_in(page, origin, tmp_path):
    result, queued = _crawl(page, origin, tmp_path, "/login/videos", {"name": "porndoe"}, site_id="dl95-login")
    assert result["state"] == "NOT_LOGGED_IN", result
    assert queued == []


# GUARD (dispatch-A ANSWER-dispatch-A-2356Z.md item 3): discover's stored-credential re-login spends one counted
# login_attempt row BEFORE it contacts the site. GREEN on base c80a6883 -- the add-on premise "unlogged on base" was
# withdrawn; this pins the existing accounting so a later change to _relogin_for_discovery cannot bypass it.
class _GuardRunner:
    """AuthMixin host reaching the real runner_auth.login_async seam (fixture style of test_row667)."""

    def __init__(self, auth_mixin, config):
        self.__class__ = type("Dl95GuardRunner", (auth_mixin, _GuardRunner), {})
        self.site_id = "dl95-guard-site"
        self.config = config
        self._login_thread = None
        self._manual_login_handle = None
        self._login_status = ""
        self.cookies = []

    def set_cookies(self, cookies):
        self.cookies = list(cookies)


def _guard_seam(monkeypatch, tmp_path, config):
    from bulk_downloader import db, runner_auth, session_keeper

    monkeypatch.setenv("BD_DISABLE_KEEPALIVE", "1")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "dl95-guard.db"))
    db.db_init()
    rows_at_contact = []
    monkeypatch.setattr(
        runner_auth, "do_login",
        lambda *_a, **_k: (
            rows_at_contact.append(session_keeper.login_attempts_for_day("dl95-guard-site")["count"])
            or (False, "fixture rejection", [])),
    )
    return _GuardRunner(runner_auth.AuthMixin, config), session_keeper, rows_at_contact


_GUARD_CONFIG = {"login_url": "https://invalid.test/login", "auto_teach_first_run": False,
                 "username": "dl95-user", "password": "dl95-pass"}


def _guard_relogin(monkeypatch, tmp_path, config):
    """Run discover's re-login once; return (outcome, attempt rows counted when the site was contacted, attempts)."""
    from bulk_downloader import scene_crawler

    runner, session_keeper, rows_at_contact = _guard_seam(monkeypatch, tmp_path, config)
    assert session_keeper.login_attempts_for_day("dl95-guard-site")["count"] == 0
    outcome = scene_crawler._relogin_for_discovery(runner, timeout_s=5)
    if runner._login_thread is not None:
        runner._login_thread.join(5)
    return outcome, rows_at_contact, session_keeper.login_attempts_for_day("dl95-guard-site")


def _unaccounted(outcome, rows_at_contact, attempts):
    return ("dl95-discover-login-gate-1 GUARD: discover re-login contacted the site without its login_attempt row "
            f"first; outcome={outcome!r} rows_at_contact={rows_at_contact} attempts={attempts}")


def test_guard_discover_relogin_records_one_login_attempt_before_contact(monkeypatch, tmp_path):
    outcome, rows_at_contact, attempts = _guard_relogin(monkeypatch, tmp_path, dict(_GUARD_CONFIG))
    assert outcome == "failed" and rows_at_contact == [1], _unaccounted(outcome, rows_at_contact, attempts)
    assert attempts["status"] == "OK" and attempts["count"] == 1, attempts
    assert [row["source"] for row in attempts["attempts"]] == ["runner_auth.login_async"], attempts


def test_guard_discover_relogin_without_credentials_records_nothing(monkeypatch, tmp_path):
    from bulk_downloader import scene_crawler

    config = {key: value for key, value in _GUARD_CONFIG.items() if key not in ("username", "password")}
    runner, session_keeper, rows_at_contact = _guard_seam(monkeypatch, tmp_path, config)

    assert scene_crawler._relogin_for_discovery(runner, timeout_s=5) == "no_credentials"
    assert rows_at_contact == [] and runner._login_thread is None
    assert session_keeper.login_attempts_for_day("dl95-guard-site")["count"] == 0
