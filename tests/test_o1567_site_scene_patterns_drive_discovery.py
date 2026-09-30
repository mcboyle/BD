"""fx-newsensations-discovery-scene-rule (O1568e, test7 10.0.70.84, 2026-09-29).

Measured live (results/test7/newsensations-discovery-diag-2229Z.txt): after the
offers.php gate, newsensations' members home lists its scenes as
/members/gallery.php?id=<n>&type=vids cards, and only 1 of 23 carries an <img>
(the rest paint a CSS background). Discovery reads thumbnails, so the scene
cards never form a cohort and the thumbnailed ad banners won instead.

A site can now declare ``crawler_scene_patterns`` (regexes, one per line): when
set, discovery queues exactly the same-site links that match, in page order,
whatever their thumbnails. The fixture mirrors that page shape on loopback.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

BD_GATE_SCOPE = "module"

_FIXTURE = Path(__file__).parent / "fixtures" / "o1567_newsensations" / "members_home.html"
_PATTERN = r"/members/gallery\.php\?id=\d+&type=vids"
_SCENE_IDS = [11399, 11398, 11397, 11396, 11395]


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - stdlib handler API
        path = urlsplit(self.path).path
        if path == "/members/gallery.php":
            # Every members page carries the same site-wide <title>.
            raw = (b"<!doctype html><html><head><title>New Sensations Premium Access"
                   b"</title></head><body><a href='/members/logout.php'>Logout</a>"
                   b"</body></html>")
        elif path == "/members/":
            raw = _FIXTURE.read_bytes()
        else:
            self.send_error(404)
            return
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
    with cloak.cloaked_page(
        headless=True,
        config={"browser_backend": "playwright"},
        viewport={"width": 1280, "height": 720},
    ) as browser_page:
        yield browser_page


def _crawl(page, origin, tmp_path, config, db_name, title_fetch_limit=0):
    from bulk_downloader import scene_crawler
    queued: list[str] = []
    result = scene_crawler.crawl_with_page(
        page,
        site_id="910a72be",
        listing_url=origin + "/members/",
        site_config=config,
        newest_n=0,
        max_pages=1,
        max_scrolls=2,
        delay_s=0,
        title_fetch_limit=title_fetch_limit,
        db_path=str(tmp_path / db_name),
        enqueue_fn=lambda _sid, url: queued.append(url) or {
            "added": 1, "dupes": 0, "skipped": 0},
    )
    return result, queued


def test_declared_scene_pattern_queues_the_gallery_cards(page, origin, tmp_path):
    result, queued = _crawl(
        page, origin, tmp_path,
        {"crawler_scene_patterns": _PATTERN}, "with_rule.sqlite")
    expected = [f"{origin}/members/gallery.php?id={n}&type=vids" for n in _SCENE_IDS]
    assert result["state"] == "COMPLETED", result
    assert queued == expected, (
        f"fx-newsensations-discovery-scene-rule: queued {queued}, "
        f"shapes={result.get('scene_shapes')}")
    assert result["scene_shapes"] == [f"pattern:{_PATTERN}"]
    assert not any("bannerload" in url for url in queued)
    titles = {s["url"]: s["title"] for s in result["scenes"]}
    assert titles[expected[1]] == "Stepsister Kenzie"


def test_negative_control_without_the_rule_the_cards_are_not_a_cohort(
        page, origin, tmp_path):
    # The unchanged thumbnail heuristic cannot see CSS-background cards.
    _result, queued = _crawl(page, origin, tmp_path, {}, "no_rule.sqlite")
    assert not any("gallery.php" in url for url in queued), queued


def test_patterns_parse_one_per_line_and_skip_bad_or_oversized():
    from bulk_downloader.scene_crawler import _scene_patterns
    parsed = _scene_patterns({"crawler_scene_patterns":
                              f" {_PATTERN} \n\n[unclosed\n" + "a" * 513 + "\n/x/\\d{1,3}/"})
    assert [p.pattern for p in parsed] == [_PATTERN, r"/x/\d{1,3}/"]
    assert _scene_patterns({}) == []
    assert _scene_patterns({"crawler_scene_patterns": None}) == []


def test_the_key_is_a_persisted_typed_validated_site_field():
    from bulk_downloader import app_kernel, site_editor
    assert "crawler_scene_patterns" in app_kernel.CFG_FIELDS
    assert app_kernel.DEFAULTS["crawler_scene_patterns"] == ""
    assert site_editor._FIELD_TYPES["crawler_scene_patterns"][0] == "string"
    bad = site_editor.validate_config({
        "name": "x", "login_url": "https://x.test/", "download_dir": "/tmp/x",
        "crawler_scene_patterns": "ok/\\d+\n[unclosed"})
    assert any("crawler_scene_patterns" in e for e in bad["errors"]), bad
    ok = site_editor.validate_config({
        "name": "x", "login_url": "https://x.test/", "download_dir": "/tmp/x",
        "crawler_scene_patterns": _PATTERN})
    assert not any("crawler_scene_patterns" in e for e in ok["errors"]), ok
    types = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "lib"
             / "api-types.ts").read_text(encoding="utf-8")
    assert "crawler_scene_patterns?: string;" in types


def test_newsensations_template_declares_its_listing_and_scene_rule():
    import re
    from bulk_downloader.site_templates import _data_studios_b as data
    tpl = next(t for t in data.ITEMS if t["id"] == "new_sensations")
    defaults = tpl["config_defaults"]
    assert defaults["crawler_listing_url"] == "https://www.newsensations.com/members/"
    rule = re.compile(defaults["crawler_scene_patterns"])
    assert rule.search("https://www.newsensations.com/members/gallery.php?id=11399&type=vids")
    for not_scene in ("https://www.newsensations.com/members/bannerload.php?track=2311",
                      "https://www.newsensations.com/members/gallery.php?id=11399&type=photos",
                      "https://www.newsensations.com/members/sets.php?id=4"):
        assert not rule.search(not_scene), not_scene


def test_a_site_wide_scene_page_title_does_not_replace_the_card_title(
        page, origin, tmp_path):
    # Live 23:04Z: all three discovered scenes were titled "New Sensations
    # Premium Access" -- the scene-page fetch overwrote each card title with
    # the <title> every members page shares.
    import time
    started = time.monotonic()
    result, _queued = _crawl(
        page, origin, tmp_path, {"crawler_scene_patterns": _PATTERN},
        "titles.sqlite", title_fetch_limit=5)
    elapsed = time.monotonic() - started
    # Scene pages without og:title each waited out a 30 s locator default
    # (150 s for five); a local fixture page reads in well under a second.
    assert elapsed < 60, f"title fetch of 5 og:title-less pages took {elapsed:.0f}s"
    titles = [s["title"] for s in result["scenes"]]
    assert "New Sensations Premium Access" not in titles, titles
    assert titles[1:] == ["Stepsister Kenzie", "Wife Cheating Blake",
                          "Milf Audrey", "Harley Haze Latina"], titles


def test_a_card_label_every_card_repeats_is_not_a_title(page, origin, tmp_path):
    # Live 23:1xZ: with the page-title constant skipped, both discovered scenes
    # were titled "Open scene" -- the link label every card repeats; the card's
    # own heading is the title.
    result, _queued = _crawl(
        page, origin, tmp_path, {"crawler_scene_patterns": _PATTERN}, "labels.sqlite")
    titles = [s["title"] for s in result["scenes"]]
    assert "Open scene" not in titles, titles
    assert titles == ["Hotwife Carmella", "Stepsister Kenzie", "Wife Cheating Blake",
                      "Milf Audrey", "Harley Haze Latina"], titles


def test_a_category_context_link_is_the_same_scene(page, origin, tmp_path):
    # Live 23:2xZ: the members home also links scenes as
    # gallery.php?id=<n>&type=vids&catid=5; discovery queued 11375 again under
    # that spelling although it was already downloaded. Query parameters after
    # the declared shape's match are context, not identity.
    _result, queued = _crawl(
        page, origin, tmp_path, {"crawler_scene_patterns": _PATTERN}, "catid.sqlite")
    assert not any("catid" in url for url in queued), queued
    assert len(queued) == len(_SCENE_IDS), queued


def test_canonical_url_only_trims_query_after_a_query_match():
    import re
    from bulk_downloader.scene_crawler import _pattern_canonical_url
    rule = re.compile(_PATTERN, re.IGNORECASE)
    base = "https://www.newsensations.com/members/gallery.php?id=11375&type=vids"
    assert _pattern_canonical_url(base + "&catid=5", rule) == base
    assert _pattern_canonical_url(base, rule) == base
    # A path-only rule never truncates the URL it admits.
    path_rule = re.compile(r"/video/")
    url = "https://x.test/video/123/slug?ref=home"
    assert _pattern_canonical_url(url, path_rule) == url
    # A match that stops inside a parameter value never truncates it.
    assert _pattern_canonical_url(base + "s2&catid=5", rule) == base + "s2&catid=5"


def test_negative_control_distinct_scene_page_titles_still_win():
    from bulk_downloader.scene_crawler import _site_wide_titles
    assert _site_wide_titles(["A :: Wow", "B :: Wow"]) == set()
    assert _site_wide_titles(["Site", "site", "Other"]) == {"site"}
    assert _site_wide_titles(["Only One"]) == set()
