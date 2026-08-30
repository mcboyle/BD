"""ROW 374 -- authenticated GUI scene crawler, local-fixture RED tests.

The fixture pages mirror the four shapes measured on 2026-08-29.  They are
served only on an ephemeral loopback port; no authenticated or live site is
contacted by this module.
"""
from __future__ import annotations

import importlib
import threading
import time
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
    # Every walk must have OBSERVED its listing pages stop growing; a run whose
    # settle budget expired reports UNKNOWN and must not read as a clean crawl.
    assert result["scroll_settle_state"] == crawler.SETTLE_SETTLED, result
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
    assert result["scroll_settle_state"] == "SETTLED", result
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


class _ScriptedLazyLoadPage:
    """A scripted page whose scroll-triggered append becomes visible only after
    ``reads_before_visible`` further DOM reads.

    That is exactly what a renderer whose next rendering opportunity has been
    descheduled presents to a CDP client: ``window.scrollTo`` returns, but the
    ``scroll`` listener that appends the remaining cards has not run yet, so the
    reads that follow still observe the pre-scroll page.  No sleeping and no
    real browser is involved, so the schedule under test is fixed rather than
    sampled.
    """

    def __init__(self, *, initial=4, appended=2, reads_before_visible=3,
                 grows_forever=False):
        self.initial = initial
        self.appended = appended
        self.reads_before_visible = reads_before_visible
        self.grows_forever = grows_forever
        self.expanded = False
        self.scrolls = 0
        self.reads = 0
        self.waits = []
        self._pending = None
        self._extra = 0

    # -- scripted DOM -------------------------------------------------
    def _card_count(self):
        count = self.initial + (self.appended if self.expanded else 0)
        return count + self._extra

    def _cards(self):
        return [
            {
                "url": f"https://members.example.test/view/{index}",
                "text": f"Scene {index}",
                "title": "",
                "aria": "",
                "img_alt": "",
                "nearest": "",
                "class_name": "scene-card",
                "rel": "",
                "has_img": True,
            }
            for index in range(self._card_count())
        ]

    def _read(self):
        self.reads += 1
        if self.grows_forever:
            self._extra += 1
            return
        if self._pending is not None:
            self._pending -= 1
            if self._pending <= 0:
                self.expanded = True
                self._pending = None

    # -- Playwright surface used by the walk --------------------------
    def evaluate(self, js):
        if "scrollTo" in js:
            self.scrolls += 1
            if not self.expanded and self._pending is None:
                self._pending = self.reads_before_visible
            return None
        self._read()
        count = self._card_count()
        return [720 + 200 * count, count]

    def locator(self, selector):
        assert selector == "a[href]", selector
        page = self

        class _Locator:
            def evaluate_all(self, _js):
                page._read()
                return page._cards()

        return _Locator()

    def wait_for_timeout(self, ms):
        self.waits.append(ms)
        time.sleep(max(0.0, float(ms)) / 1000.0)


def test_row397_walk_waits_for_a_late_lazy_load_instead_of_a_scroll_event_count(
):
    """RED on the defective base: the walk breaks on ONE stale post-scroll read.

    ``max_scrolls`` counts scroll EVENTS; it is not a settle condition.  On the
    defective base this returns 4 cards and growth 0 -- the two lazy-loaded
    cards are dropped and the run still reports a clean result, which is the
    measured ``assert 6 == 8`` shape.
    """
    crawler = _crawler()
    page = _ScriptedLazyLoadPage(reads_before_visible=3)

    # Preconditions: the scripted page really does lazy-load, and the append is
    # genuinely invisible to the first read after the scroll.
    assert page._card_count() == 4
    outcome = crawler._scroll_and_collect(page, max_scrolls=4, settle_s=0.0)
    rows, growth = outcome[0], outcome[1]

    # Preconditions: the walk really scrolled and really re-read the DOM.
    assert page.scrolls >= 1, page.scrolls
    assert page.reads > 2, page.reads
    # Verdict: on the defective base this is `assert 4 == 6` -- the two
    # lazy-loaded cards are dropped and the run still reports a clean result.
    urls = sorted({row["url"] for row in rows})
    assert len(urls) == 6, urls
    assert growth == 1, growth
    # The append became visible only because the walk kept polling for it.
    assert page.expanded is True
    assert outcome[2] == crawler.SETTLE_SETTLED
    # The first read after the scroll was stale and a later poll saw the growth:
    # this walk observed and absorbed the exact race the old single read lost.
    assert outcome[3] == 1, outcome


def test_row397_a_page_that_never_stops_growing_reports_unknown_and_stays_bounded(
):
    """A7: an unavailable measurement is UNKNOWN, never a settled page."""
    crawler = _crawler()
    page = _ScriptedLazyLoadPage(grows_forever=True)
    started = time.monotonic()
    outcome = crawler._scroll_and_collect(
        page,
        max_scrolls=2,
        settle_s=0.0,
        poll_s=0.01,
        quiet_polls=4,
        settle_budget_s=0.30,
    )
    elapsed = time.monotonic() - started
    assert page.reads > 4, page.reads
    assert outcome[2] == crawler.SETTLE_UNKNOWN, outcome
    assert elapsed < 5.0, elapsed


def test_row397_a_page_without_lazy_load_settles_promptly_and_reads_a_bounded_number(
):
    """Negative control: nothing to wait for must not cost the settle budget."""
    crawler = _crawler()
    page = _ScriptedLazyLoadPage(initial=4, appended=0, reads_before_visible=0)
    page.expanded = True
    started = time.monotonic()
    outcome = crawler._scroll_and_collect(
        page, max_scrolls=4, settle_s=0.0, poll_s=0.01, quiet_polls=4,
        settle_budget_s=5.0,
    )
    elapsed = time.monotonic() - started
    assert page.scrolls == 1, page.scrolls
    assert outcome[1] == 0
    assert outcome[2] == crawler.SETTLE_SETTLED
    assert outcome[3] == 0
    assert len({row["url"] for row in outcome[0]}) == 4
    assert elapsed < 1.0, elapsed


def test_row397_fixture_precondition_evilangel_lazy_loads_two_of_its_eight_scenes(
    page, fixture_origin,
):
    """The 8 expected scenes are really 4 + 2 lazy-loaded + 2 on page two."""
    page.goto(fixture_origin + "/evilangel/en/videos", wait_until="domcontentloaded")
    assert page.locator('a[href*="/view/"]').count() == 0
    page.click("button#enter-members")
    page.wait_for_selector('a[href*="/view/"]')
    assert page.locator('a[href*="/view/"]').count() == 4
    assert page.locator('a[href*="/videos/sort/latest/page/2"]').count() == 1
    page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_function(
        "() => document.querySelectorAll('a[href*=\"/view/\"]').length === 6",
        timeout=15000,
    )
    assert page.locator('a[href*="/view/"]').count() == 6

    page.goto(
        fixture_origin + "/evilangel/en/videos/sort/latest/page/2",
        wait_until="domcontentloaded",
    )
    page.click("button#enter-members")
    page.wait_for_selector('a[href*="/view/"]')
    assert page.locator('a[href*="/view/"]').count() == 2


def test_row397_a_listing_with_fewer_scenes_completes_without_spending_the_budget(
    page, fixture_origin, tmp_path,
):
    """Negative control on the real browser: a listing that genuinely exposes
    fewer scenes must reach COMPLETED promptly, not spin to the settle budget."""
    crawler = _crawler()
    started = time.monotonic()
    _crawler_mod, result, _queued = _run(
        page,
        fixture_origin,
        tmp_path,
        site_id="wowgirls",
        path="/wowgirls/updates/",
        expected=4,
        pages=1,
    )
    elapsed = time.monotonic() - started
    assert result["scroll_settle_state"] == crawler.SETTLE_SETTLED, result
    assert result["scroll_growth_steps"] == 0, result
    assert elapsed < crawler.SCROLL_SETTLE_BUDGET_S, elapsed


# ROW 394 -- per-host adaptive pacing and conditional listing requests.  The
# HTTP evidence remains loopback-only, and pacing uses recorded sleeps rather
# than wall-clock thresholds so the matched windows are deterministic.


class _Row394Response:
    def __init__(self, status: int):
        self.status = status
        self.headers = {}


class _Row394StatusPage:
    def __init__(self, statuses: list[int]):
        self._statuses = iter(statuses)
        self.requests: list[str] = []

    def goto(self, url: str, **_kwargs):
        self.requests.append(url)
        return _Row394Response(next(self._statuses))


def _row394_paced_window(monkeypatch, statuses: list[int]):
    crawler = _crawler()
    sleeps: list[float] = []
    monkeypatch.setattr(crawler.time, "sleep", sleeps.append)
    page = _Row394StatusPage(statuses)
    pacer = crawler._Pacer(1.0)
    for _status in statuses:
        crawler._goto(page, "https://members.example.test/listing", pacer)
    return page.requests, sleeps


def test_row394_throttle_adapts_one_hosts_matched_window_without_wall_clock(
    monkeypatch,
):
    healthy_requests, healthy_sleeps = _row394_paced_window(
        monkeypatch, [200, 200, 200, 200]
    )
    throttled_requests, throttled_sleeps = _row394_paced_window(
        monkeypatch, [429, 200, 200, 200]
    )

    # Preconditions: both windows dispatched the same nonzero request count and
    # differ only in the first response status.  No elapsed-time threshold can
    # turn a loaded host into a different verdict.
    assert len(healthy_requests) == len(throttled_requests) == 4
    assert set(healthy_requests) == set(throttled_requests) == {
        "https://members.example.test/listing"
    }
    assert healthy_sleeps == [1.0, 1.0, 1.0]
    assert throttled_sleeps == [2.0, 1.0, 1.0]
    assert sum(throttled_sleeps) > sum(healthy_sleeps)


def test_row394_throttle_delay_is_isolated_to_responding_host(monkeypatch):
    crawler = _crawler()
    sleeps: list[float] = []
    monkeypatch.setattr(crawler.time, "sleep", sleeps.append)
    pacer = crawler._Pacer(1.0)

    sequence = [
        ("https://slow.example.test/listing", 429),
        ("https://healthy.example.test/listing", 200),
        ("https://healthy.example.test/page/2", 200),
        ("https://slow.example.test/page/2", 200),
    ]
    page = _Row394StatusPage([status for _url, status in sequence])
    for url, _status in sequence:
        crawler._goto(page, url, pacer)

    # The healthy host's second request keeps the configured floor (1s); only
    # the throttled host's second request receives the 2s backoff.
    assert page.requests == [url for url, _status in sequence]
    assert sleeps == [1.0, 2.0]


@pytest.fixture
def row394_conditional_listing_origin():
    state = {"version": 1, "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib handler API
            if self.path != "/members/listing":
                self.send_error(404)
                return
            version = int(state["version"])
            etag = f'"row394-v{version}"'
            last_modified = (
                "Sun, 30 Aug 2026 12:00:00 GMT"
                if version == 1
                else "Sun, 30 Aug 2026 12:01:00 GMT"
            )
            observed = {
                "if-none-match": self.headers.get("If-None-Match"),
                "if-modified-since": self.headers.get("If-Modified-Since"),
            }
            state["requests"].append(observed)
            if (
                observed["if-none-match"] == etag
                and observed["if-modified-since"] == last_modified
            ):
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Last-Modified", last_modified)
                self.end_headers()
                return

            cards = [(1, "First Scene"), (2, "Second Scene")]
            if version >= 2:
                cards.append((3, "Third Scene"))
            anchors = "".join(
                "<article><a href='/video/watch/{number}/scene-{number}'>"
                "<img alt='{title}'></a></article>".format(
                    number=number, title=title
                )
                for number, title in cards
            )
            raw = (
                "<!doctype html><html><body data-members-area='true'>"
                f"{anchors}</body></html>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("ETag", etag)
            self.send_header("Last-Modified", last_modified)
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, _fmt, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _row394_crawl_in_fresh_context(
    root_page, origin, db_path, queued, *, enqueue_fn=None,
):
    crawler = _crawler()
    browser = root_page.context.browser
    assert browser is not None
    context = browser.new_context(viewport={"width": 800, "height": 600})
    page = context.new_page()
    try:
        submit = enqueue_fn or (
            lambda sid, url: queued.append((sid, url)) or {
                "added": 1,
                "dupes": 0,
                "skipped": 0,
            }
        )
        return crawler.crawl_with_page(
            page,
            site_id="row394",
            listing_url=origin + "/members/listing",
            site_config={},
            newest_n=0,
            max_pages=1,
            max_scrolls=0,
            delay_s=0,
            title_fetch_limit=0,
            enqueue_fn=submit,
            db_path=str(db_path),
        )
    finally:
        context.close()


def test_row394_completed_listing_revalidates_and_changed_body_is_processed(
    page, row394_conditional_listing_origin, tmp_path,
):
    crawler = _crawler()
    origin, state = row394_conditional_listing_origin
    db_path = tmp_path / "conditional.sqlite"
    queued: list[tuple[str, str]] = []

    first = _row394_crawl_in_fresh_context(page, origin, db_path, queued)
    assert first["state"] == crawler.STATE_COMPLETED
    assert first["discovered"] == first["queued"] == 2
    assert len(queued) == 2
    assert state["requests"] == [{
        "if-none-match": None,
        "if-modified-since": None,
    }]

    # A fresh browser context has no HTTP cache.  The validators therefore can
    # only come from the crawler's durable completed-walk evidence.
    unchanged = _row394_crawl_in_fresh_context(page, origin, db_path, queued)
    assert unchanged["state"] == crawler.STATE_COMPLETED
    assert unchanged["listing_not_modified"] is True
    assert unchanged["pages_walked"] == 1
    assert unchanged["discovered"] == unchanged["queued"] == 0
    assert len(queued) == 2
    assert state["requests"][-1] == {
        "if-none-match": '"row394-v1"',
        "if-modified-since": "Sun, 30 Aug 2026 12:00:00 GMT",
    }

    # Negative control: the same conditional headers no longer match after the
    # server changes.  A 200 body must be processed, not mistaken for a 304.
    state["version"] = 2
    changed = _row394_crawl_in_fresh_context(page, origin, db_path, queued)
    assert changed["listing_not_modified"] is False
    assert changed["discovered"] == changed["queued"] == 1
    assert len(queued) == 3
    assert state["requests"][-1] == state["requests"][-2]


def test_row394_pending_enqueue_disables_304_until_retry_succeeds(
    page, row394_conditional_listing_origin, tmp_path,
):
    crawler = _crawler()
    origin, state = row394_conditional_listing_origin
    db_path = tmp_path / "pending-enqueue.sqlite"
    queued: list[tuple[str, str]] = []

    first = _row394_crawl_in_fresh_context(
        page,
        origin,
        db_path,
        queued,
        enqueue_fn=lambda _sid, _url: {
            "ok": False,
            "error": "deliberate row394 refusal",
        },
    )
    assert first["discovered"] == 2
    assert first["queued"] == 0
    assert len(first["enqueue_errors"]) == 2
    assert not queued

    retried = _row394_crawl_in_fresh_context(page, origin, db_path, queued)
    assert retried["listing_not_modified"] is False
    assert retried["discovered"] == 0
    assert retried["queued"] == 2
    assert len(queued) == 2
    # A validator was available, but pending durable rows made the second
    # request unconditional so their enqueue attempts could not be skipped.
    assert state["requests"] == [
        {"if-none-match": None, "if-modified-since": None},
        {"if-none-match": None, "if-modified-since": None},
    ]
