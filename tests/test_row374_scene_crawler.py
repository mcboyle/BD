"""ROW 374 -- authenticated GUI scene crawler, local-fixture RED tests.

The fixture pages mirror the four shapes measured on 2026-08-29.  They are
served only on an ephemeral loopback port; no authenticated or live site is
contacted by this module.
"""
from __future__ import annotations

import importlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

BD_GATE_SCOPE = "module"

FIXTURES = Path(__file__).parent / "fixtures" / "row374_scene_crawler"

_STATIC = {
    "/evilangel/en/videos": "evilangel_page1.html",
    "/evilangel/en/videos/sort/latest/page/2": "evilangel_page2.html",
    "/ultrafilms/members/home": "ultrafilms.html",
    "/wowgirls/updates/": "wowgirls.html",
    "/nubile/video/gallery/": "nubile_0.html",
    "/nubile/video/gallery/12": "nubile_12.html",
    "/nubile/video/gallery/24": "nubile_24.html",
    "/logged-out": "logged_out.html",
}

_ULTRA_TITLES = {
    "4f338e1e-with-leo-in-bed": "With Leo In Bed",
    "2a-second-movie": "Second Movie",
    "3a-third-movie": "Third Movie",
    "4a-fourth-movie": "Fourth Movie",
}


class _FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - stdlib handler API
        path = urlsplit(self.path).path
        if path == "/nubile/video/recent/":
            self.send_response(302)
            self.send_header("Location", "/nubile/video/gallery/")
            self.end_headers()
            return
        prefix = "/ultrafilms/members/content/item/"
        if path.startswith(prefix):
            slug = path[len(prefix):]
            title = _ULTRA_TITLES.get(slug)
            if title:
                raw = (
                    "<!doctype html><html><head>"
                    f'<meta property="og:title" content="UltraFilms / Members / Movie / {title}">'
                    f"<title>UltraFilms / Members / Movie / {title}</title>"
                    "</head><body><a href='/account/logout'>Logout</a></body></html>"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
        name = _STATIC.get(path)
        if name:
            raw = (FIXTURES / name).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        self.send_error(404)

    def log_message(self, _fmt, *_args):
        return


@pytest.fixture(scope="module")
def fixture_origin():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
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

    # Still goes through BD's canonical Cloak wrapper; the deterministic local
    # fixture explicitly selects its Playwright fallback backend.
    with cloak.cloaked_page(
        headless=True,
        config={"browser_backend": "playwright"},
        viewport={"width": 1280, "height": 720},
    ) as browser_page:
        yield browser_page


def _crawler():
    return importlib.import_module("bulk_downloader.scene_crawler")


def _run(page, origin, tmp_path, *, site_id, path, expected, pages,
         title_fetch_limit=0, config=None, db_name=None):
    crawler = _crawler()
    queued = []
    result = crawler.crawl_with_page(
        page,
        site_id=site_id,
        listing_url=origin + path,
        site_config=config or {},
        newest_n=0,
        max_pages=pages,
        max_scrolls=4,
        delay_s=0,
        title_fetch_limit=title_fetch_limit,
        db_path=str(tmp_path / (db_name or f"{site_id}.sqlite")),
        enqueue_fn=lambda sid, url: queued.append((sid, url)) or {
            "added": 1, "dupes": 0, "skipped": 0,
        },
    )
    assert result["state"] == "COMPLETED", result
    assert result["discovered"] == expected, result
    assert result["queued"] == expected, result
    assert result["pages_walked"] == pages, result
    assert len(result["scenes"]) == expected, result
    assert len([s for s in result["scenes"] if s["title"].strip()]) == expected
    assert len(queued) == expected
    return crawler, result, queued


def test_fixture_precondition_noise_is_more_frequent_but_has_no_thumbnails(
    page, fixture_origin,
):
    """The wrong most-frequent-shape heuristic must lose on both fixtures."""
    page.goto(fixture_origin + "/wowgirls/updates/", wait_until="domcontentloaded")
    wow_noise = page.locator('a[href*="/updates/genre/"]')
    wow_scene = page.locator('a[href*="/film/"]')
    assert wow_noise.count() == 20
    assert wow_scene.count() == 4
    assert wow_noise.count() > wow_scene.count()
    assert wow_noise.locator("img").count() == 0
    assert wow_scene.locator("img").count() == 3

    page.goto(fixture_origin + "/nubile/video/gallery/", wait_until="domcontentloaded")
    nubile_noise = page.locator('a[href*="/video/gallery/"]')
    nubile_scene = page.locator('a[href*="/video/watch/"]')
    assert nubile_noise.count() == 20
    assert nubile_scene.count() == 3
    assert nubile_noise.count() > nubile_scene.count()
    assert nubile_noise.locator("img").count() == 0
    assert nubile_scene.locator("img").count() == 1


def test_evilangel_combines_gate_scroll_and_numbered_pager(
    page, fixture_origin, tmp_path,
):
    page.goto(fixture_origin + "/evilangel/en/videos", wait_until="domcontentloaded")
    assert page.locator('a[href*="/view/"]').count() == 0
    assert page.locator("#enter-members").count() == 1

    _crawler_mod, result, _queued = _run(
        page,
        fixture_origin,
        tmp_path,
        site_id="evilangel",
        path="/evilangel/en/videos",
        expected=8,
        pages=2,
        # interstitial.selector_lines reserves a leading # for comments, so
        # express this ID selector in its equivalent element#id form.
        config={"dismiss_selectors": "button#enter-members"},
    )
    assert result["scroll_growth_steps"] == 1
    assert any("/view/" in shape for shape in result["scene_shapes"])


def test_ultrafilms_scene_page_title_overrides_performer_card_and_splits_date(
    page, fixture_origin, tmp_path,
):
    _crawler_mod, result, _queued = _run(
        page,
        fixture_origin,
        tmp_path,
        site_id="ultrafilms",
        path="/ultrafilms/members/home",
        expected=4,
        pages=1,
        title_fetch_limit=4,
    )
    assert result["title_pages_fetched"] == 4
    first = result["scenes"][0]
    assert first["title"] == "With Leo In Bed"
    assert first["title_source"] == "og:title"
    assert first["card_context"] == "LEONA MIA"
    assert "AUG 25,2026" not in first["card_context"]


def test_wowgirls_empty_anchor_titles_use_ordered_fallbacks_and_not_noise(
    page, fixture_origin, tmp_path,
):
    _crawler_mod, result, _queued = _run(
        page,
        fixture_origin,
        tmp_path,
        site_id="wowgirls",
        path="/wowgirls/updates/",
        expected=4,
        pages=1,
    )
    assert [s["card_context"] for s in result["scenes"]] == [
        "Title Attribute",
        "ARIA Scene",
        "Image Alt Scene",
        "Nearest Heading Scene",
    ]
    assert all("/updates/genre/" not in s["url"] for s in result["scenes"])


def test_nubile_redirect_effective_url_and_numeric_offsets_walk_exact_pages(
    page, fixture_origin, tmp_path,
):
    _crawler_mod, result, _queued = _run(
        page,
        fixture_origin,
        tmp_path,
        site_id="nubilefilms",
        path="/nubile/video/recent/",
        expected=9,
        pages=3,
    )
    assert result["effective_url"].endswith("/nubile/video/gallery/")
    assert result["page_urls"] == [
        fixture_origin + "/nubile/video/gallery/",
        fixture_origin + "/nubile/video/gallery/12",
        fixture_origin + "/nubile/video/gallery/24",
    ]
    assert all("/video/gallery/" not in s["url"] for s in result["scenes"])


def test_repeated_title_template_is_stripped_but_a_real_dash_title_is_not():
    crawler = _crawler()
    assert crawler.strip_repeated_title_templates([
        "UltraFilms / Members / Movie / With Leo In Bed",
        "UltraFilms / Members / Movie / Second Movie",
    ]) == ["With Leo In Bed", "Second Movie"]
    assert crawler.strip_repeated_title_templates([
        "A Real Title - Part Two",
        "Completely Different",
    ]) == ["A Real Title - Part Two", "Completely Different"]


def test_crawler_site_settings_are_typed_bounded_and_listing_url_validated():
    from bulk_downloader import site_editor as se

    assert se._FIELD_TYPES["crawler_listing_url"][0] == "string"
    assert se._FIELD_TYPES["crawler_newest_n"][0] == "integer"
    assert se._FIELD_TYPES["crawler_max_pages"][0] == "integer"
    assert se._FIELD_TYPES["crawler_max_scrolls"][0] == "integer"
    assert se._FIELD_TYPES["crawler_delay_s"][0] == "number"
    assert se._FIELD_TYPES["crawler_title_fetch_limit"][0] == "integer"

    invalid_numbers = se.validate_numeric_updates({
        "crawler_newest_n": "many",
        "crawler_max_pages": 0,
        "crawler_max_scrolls": -1,
        "crawler_delay_s": 0,
        "crawler_title_fetch_limit": -1,
    })
    assert set(invalid_numbers) == {
        "crawler_newest_n",
        "crawler_max_pages",
        "crawler_max_scrolls",
        "crawler_delay_s",
        "crawler_title_fetch_limit",
    }
    invalid_url = se.validate_config({
        "name": "Demo",
        "crawler_listing_url": "members.example.test/videos",
    })
    assert invalid_url["ok"] is False
    assert any("crawler_listing_url" in error for error in invalid_url["errors"])


def test_start_route_uses_safe_defaults_for_malformed_legacy_crawler_config(
    monkeypatch,
):
    from flask import Flask
    from bulk_downloader import app_discovery as discovery
    from bulk_downloader import scene_crawler as crawler

    app = Flask(__name__)
    discovery.register_routes(app)
    runner = object()
    captured = {}
    malformed = {
        "crawler_newest_n": "many",
        "crawler_max_pages": "never",
        "crawler_max_scrolls": [],
        "crawler_delay_s": "later",
        "crawler_title_fetch_limit": {},
    }
    monkeypatch.setattr(discovery, "_check_csrf", lambda: None)
    monkeypatch.setattr(discovery, "_app_runners", lambda: {"demo": runner})
    monkeypatch.setattr(discovery, "_app_s_cfg", lambda: {"demo": malformed})

    def fake_start(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "run_id": "row374",
            "site_id": "demo",
            "state": "RUNNING",
        }

    monkeypatch.setattr(crawler, "start_background_crawl", fake_start)
    response = app.test_client().post(
        "/api/discovery/scenes/start",
        json={
            "site_id": "demo",
            "listing_url": "https://members.example.test/videos",
        },
    )
    assert response.status_code == 202, response.get_json()
    assert captured["newest_n"] == 50
    assert captured["max_pages"] == 5
    assert captured["max_scrolls"] == 8
    assert captured["delay_s"] == 1.0
    assert captured["title_fetch_limit"] == 50


def test_discoveries_are_durable_and_a_rerun_never_queues_the_same_url_twice(
    page, fixture_origin, tmp_path,
):
    crawler = _crawler()
    db_path = str(tmp_path / "resume.sqlite")
    queued = []
    kwargs = dict(
        page=page,
        site_id="wow-resume",
        listing_url=fixture_origin + "/wowgirls/updates/",
        site_config={},
        newest_n=0,
        max_pages=5,
        max_scrolls=2,
        delay_s=0,
        title_fetch_limit=0,
        db_path=db_path,
        enqueue_fn=lambda sid, url: queued.append((sid, url)) or {
            "added": 1, "dupes": 0, "skipped": 0,
        },
    )
    first = crawler.crawl_with_page(**kwargs)
    assert first["discovered"] == 4
    assert first["queued"] == 4
    assert len(queued) == 4
    rows = crawler.discovery_history("wow-resume", db_path=db_path, limit=20)
    assert len(rows) == 4
    assert all(r["title"] and r["title_source"] == "card" for r in rows)
    assert all(r["discovered_at"] > 0 and r["queued_at"] > 0 for r in rows)

    second = crawler.crawl_with_page(**kwargs)
    assert second["state"] == "COMPLETED"
    assert second["discovered"] == 0
    assert second["queued"] == 0
    assert second["pages_walked"] == 1
    assert len(queued) == 4
    assert len(crawler.discovery_history(
        "wow-resume", db_path=db_path, limit=20,
    )) == 4


def test_newest_n_checkpoint_resumes_an_unfinished_page_before_its_pager(
    page, fixture_origin, tmp_path,
):
    """A depth stop must not skip the rest of a mixed scroll+pager page."""
    crawler = _crawler()
    db_path = str(tmp_path / "depth-resume.sqlite")
    queued = []
    kwargs = dict(
        page=page,
        site_id="evil-depth-resume",
        listing_url=fixture_origin + "/evilangel/en/videos",
        site_config={"dismiss_selectors": "button#enter-members"},
        newest_n=2,
        max_pages=5,
        max_scrolls=4,
        delay_s=0,
        title_fetch_limit=0,
        db_path=db_path,
        enqueue_fn=lambda sid, url: queued.append((sid, url)) or {
            "added": 1, "dupes": 0, "skipped": 0,
        },
    )

    first = crawler.crawl_with_page(**kwargs)
    second = crawler.crawl_with_page(**kwargs)

    assert first["discovered"] == first["queued"] == 2
    assert second["discovered"] == second["queued"] == 2
    assert first["pages_walked"] == second["pages_walked"] == 1
    assert first["page_urls"] == [fixture_origin + "/evilangel/en/videos"]
    assert second["page_urls"] == first["page_urls"]
    assert len(queued) == 4
    assert len(crawler.discovery_history(
        "evil-depth-resume", db_path=db_path, limit=20,
    )) == 4


def test_depth_budget_that_fills_on_a_pages_last_visible_scene_still_stops(
    page, fixture_origin, tmp_path,
):
    """The depth stop must not need a further candidate scene to fire.

    ``max_scrolls=0`` takes wall-clock out of the causal path: the gate reveals
    exactly four scenes plus a pager link and no scroll growth is possible, so
    the second run's newest-N budget fills on the LAST scene the page shows.
    A loaded host reaches the same shape with ``max_scrolls=4`` because the
    scroll event may not have been dispatched before the crawler re-reads the
    DOM, which is what made the sibling checkpoint test schedule-sensitive.
    """
    crawler = _crawler()
    listing = fixture_origin + "/evilangel/en/videos"
    pager = fixture_origin + "/evilangel/en/videos/sort/latest/page/2"

    # Precondition and negative control.  Without a depth budget the very same
    # max_scrolls=0 walk really does reach the pager, so a pages_walked of 1
    # below cannot be a vacuous "there was nowhere left to go".
    control_queued = []
    control = crawler.crawl_with_page(
        page,
        site_id="evil-depth-control",
        listing_url=listing,
        site_config={"dismiss_selectors": "button#enter-members"},
        newest_n=0,
        max_pages=5,
        max_scrolls=0,
        delay_s=0,
        title_fetch_limit=0,
        db_path=str(tmp_path / "depth-control.sqlite"),
        enqueue_fn=lambda sid, url: control_queued.append((sid, url)) or {
            "added": 1, "dupes": 0, "skipped": 0,
        },
    )
    assert control["state"] == "COMPLETED", control
    assert control["scroll_growth_steps"] == 0, control
    assert control["discovered"] == 6, control
    assert control["page_urls"] == [listing, pager], control

    db_path = str(tmp_path / "depth-boundary.sqlite")
    queued = []
    kwargs = dict(
        page=page,
        site_id="evil-depth-boundary",
        listing_url=listing,
        site_config={"dismiss_selectors": "button#enter-members"},
        newest_n=2,
        max_pages=5,
        max_scrolls=0,
        delay_s=0,
        title_fetch_limit=0,
        db_path=db_path,
        enqueue_fn=lambda sid, url: queued.append((sid, url)) or {
            "added": 1, "dupes": 0, "skipped": 0,
        },
    )

    first = crawler.crawl_with_page(**kwargs)
    second = crawler.crawl_with_page(**kwargs)
    third = crawler.crawl_with_page(**kwargs)

    # Run 1 fills the budget mid-page; run 2 fills it on the page's last
    # visible scene.  Both are depth stops and neither may spend a request on
    # the pager, and run 3 proves the checkpoint was left on the listing page
    # rather than on the pager the first two runs never earned.
    assert first["scroll_growth_steps"] == second["scroll_growth_steps"] == 0
    assert first["discovered"] == first["queued"] == 2, first
    assert second["discovered"] == second["queued"] == 2, second
    assert first["pages_walked"] == 1, first
    assert second["pages_walked"] == 1, second
    assert first["page_urls"] == [listing], first
    assert second["page_urls"] == [listing], second
    assert third["page_urls"] == [listing], third
    assert third["discovered"] == 0, third
    assert len(queued) == 4
    assert len(crawler.discovery_history(
        "evil-depth-boundary", db_path=db_path, limit=20,
    )) == 4


def test_logged_out_tour_is_not_laundered_into_zero_scenes(
    page, fixture_origin, tmp_path,
):
    crawler = _crawler()
    result = crawler.crawl_with_page(
        page,
        site_id="logged-out",
        listing_url=fixture_origin + "/logged-out",
        site_config={},
        newest_n=50,
        max_pages=3,
        max_scrolls=2,
        delay_s=0,
        title_fetch_limit=10,
        db_path=str(tmp_path / "logged-out.sqlite"),
        enqueue_fn=lambda _sid, _url: pytest.fail(
            "a logged-out fixture must never enqueue",
        ),
    )
    assert result["state"] == "NOT_LOGGED_IN"
    assert result["zero_scenes_found"] is False
    assert result["discovered"] == 0
    assert result["queued"] == 0
    assert result["pages_walked"] == 1


def test_scene_crawl_start_and_status_routes_are_registered():
    from bulk_downloader import app as bd_app

    routes = {(r.rule, frozenset(r.methods or ())) for r in bd_app.app.url_map.iter_rules()}
    assert any(
        path == "/api/discovery/scenes/start" and "POST" in methods
        for path, methods in routes
    )
    assert any(
        path == "/api/discovery/scenes/status" and "GET" in methods
        for path, methods in routes
    )
