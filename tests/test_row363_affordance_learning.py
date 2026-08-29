"""Row 363 -- live-page download-affordance learning, entirely offline."""
from __future__ import annotations

import copy
import importlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest


BD_GATE_SCOPE = "module"

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "row363"


def _subject():
    """Import at call time so every RED node reports its own missing feature."""
    try:
        return importlib.import_module("bulk_downloader.affordance_learning")
    except ModuleNotFoundError as exc:  # pristine row-363 RED
        pytest.fail(f"row 363 learner is missing: {exc}")


def _fixture(name: str) -> Path:
    path = FIXTURES / name
    assert path.is_file(), f"fixture missing: {path}"
    assert path.stat().st_size > 100, f"fixture is empty/trivial: {path}"
    return path


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        instance = pw.chromium.launch(headless=True)
        try:
            yield instance
        finally:
            instance.close()


def _page(browser, fixture: str):
    page = browser.new_page(viewport={"width": 1200, "height": 760})
    path = _fixture(fixture)
    page.goto(path.as_uri(), wait_until="domcontentloaded")
    return page


def test_bar_fixture_learns_every_option_without_clicking(browser):
    page = _page(browser, "bar_rendered.html")
    try:
        assert page.locator("a[class*='DownloadOption']").count() == 8
        assert page.locator("[class*='ScenePlayerHeaderPlus-IconItem']").count() == 1
        assert page.evaluate("window.__row363ClickCount") == 0
        result = _subject().learn_from_page(
            page, quality_preference="1080,720,540", min_resolution=540
        )
        assert result["status"] == "FOUND"
        assert result["shape"] == "BAR"
        assert result["trigger_selector"] is None
        assert result["row_selector"] == "a[class*='DownloadOption']"
        assert len(result["options"]) == 8
        assert page.evaluate("window.__row363ClickCount") == 0, (
            "BAR learning must not click anything"
        )
    finally:
        page.close()


def test_dropdown_fixture_is_empty_before_one_click_and_full_after(browser):
    page = _page(browser, "dropdown_rendered.html")
    try:
        assert page.locator("a[class*='DownloadOption']").count() == 0
        result = _subject().learn_from_page(
            page, quality_preference="2160,1080,720", min_resolution=720
        )
        assert result["status"] == "FOUND"
        assert result["shape"] == "DROPDOWN"
        assert result["trigger_selector"] == (
            "[class*='ScenePlayerHeaderPlus-IconItem']"
            ":has([class*='Icon-Download'])"
        )
        assert result["row_selector"] == "a[class*='DownloadOption']"
        assert result["interaction_count"] == 1
        assert page.evaluate("window.__row363PlayClicks") == 0
        assert page.evaluate("window.__row363DownloadClicks") == 1
        assert page.locator("a[class*='DownloadOption']").count() == 8
        assert len(result["options"]) == 8
    finally:
        page.close()


def test_dropdown_polls_for_rows_rendered_after_the_click(browser):
    page = browser.new_page()
    try:
        page.set_content(
            "<button class='ScenePlayerHeaderPlus-IconItem-Button' id='download'>"
            "<span class='Icon-DownloadRevamp'></span>Download</button><div id='rows'></div>"
            "<script>document.getElementById('download').onclick=()=>setTimeout(()=>{"
            "document.getElementById('rows').innerHTML=\"<a class='DownloadOption' "
            "href='/movieaction/download/fixture/1080p/mp4'>Full HD 1080p</a>\";},450);</script>"
        )
        result = _subject().learn_from_page(
            page, quality_preference="1080", min_resolution=720)
        assert result["status"] == "FOUND"
        assert result["shape"] == "DROPDOWN"
        assert [option["height"] for option in result["options"]] == [1080]
        assert result["interaction_count"] == 1
    finally:
        page.close()


def test_unique_gamma_trigger_is_refined_before_it_is_saved_and_replays(browser):
    subject = _subject()
    page = browser.new_page()
    try:
        page.set_content(
            "<div class='ScenePlayerHeaderPlus-IconItem only-now' id='download'>"
            "<span class='Icon-Download'></span>Download</div><div id='rows'></div>"
            "<script>document.getElementById('download').onclick=()=>{"
            "document.getElementById('rows').innerHTML=\"<a class='DownloadOption' "
            "href='/movieaction/download/fixture/1080p/mp4'>Full HD 1080p</a>\";};</script>"
        )
        learned = subject.learn_from_page(
            page, quality_preference="1080", min_resolution=720)
        assert learned["status"] == "FOUND"
        assert learned["trigger_selector"] == (
            "[class*='ScenePlayerHeaderPlus-IconItem']"
            ":has([class*='Icon-Download'])"
        )
    finally:
        page.close()

    sibling = _page(browser, "adulttime_hidden_dropdown_rendered.html")
    try:
        selector = learned["trigger_selector"]
        assert sibling.locator("[class*='ScenePlayerHeaderPlus-IconItem']").count() == 2
        assert sibling.locator(selector).count() == 1
        replay = subject.learn_from_page(
            sibling,
            quality_preference="1080",
            min_resolution=720,
            trigger_selectors=[selector],
        )
        assert replay["status"] == "FOUND"
        assert sibling.evaluate("window.__row363AdulttimeClicks") == 1
    finally:
        sibling.close()


def test_learning_uses_the_proven_selector_with_the_complete_option_set(browser):
    page = browser.new_page()
    try:
        page.set_content(
            "<a class='DownloadOption' data-quality='540' href='/download/x/540p/mp4'>Web HD 540p</a>"
            "<a class='DownloadOption' href='/download/x/720p/mp4'>HD 720p</a>"
            "<a class='DownloadOption' href='/download/x/1080p/mp4'>Full HD 1080p</a>"
            + "".join(
                f"<a href='/user/history/download/site/history-{index}'>History {index}</a>"
                for index in range(5)
            )
        )
        result = _subject().learn_from_page(
            page,
            row_selectors=["a[data-quality][href]"],
            quality_preference="1080",
            min_resolution=540,
        )
        assert result["row_selector"] == "a[class*='DownloadOption']"
        assert [option["height"] for option in result["options"]] == [540, 720, 1080]
    finally:
        page.close()


def test_learning_skips_an_unsafe_trigger_and_clicks_one_later_safe_candidate(browser):
    page = browser.new_page()
    try:
        page.set_content(
            "<a id='decoy' href='https://outside.example.invalid/leave'>Decoy</a>"
            "<button id='real' type='button'>Download</button><div id='rows'></div>"
            "<script>window.__realClicks=0;document.getElementById('real').onclick=()=>{"
            "window.__realClicks++;document.getElementById('rows').innerHTML=\""
            "<a class='DownloadOption' href='/download/x/720p/mp4'>HD 720p</a>\";};</script>"
        )
        result = _subject().learn_from_page(
            page,
            trigger_selectors=["a#decoy", "button#real"],
            quality_preference="720",
            min_resolution=720,
        )
        assert result["status"] == "FOUND"
        assert result["trigger_selector"] == "button#real"
        assert result["interaction_count"] == 1
        assert page.evaluate("window.__realClicks") == 1
        assert any(
            attempt.get("selector") == "a#decoy" and attempt.get("status") == "FAILED"
            for attempt in result["selector_attempts"]
        )
    finally:
        page.close()


def test_learning_refuses_a_trigger_download_without_starting_one(browser):
    context = browser.new_context(
        accept_downloads=True,
        viewport={"width": 1200, "height": 760},
    )
    page = context.new_page()
    observed_downloads = []
    page.on("download", lambda download: observed_downloads.append(download))
    page.goto(_fixture("scene_download_side_effect.html").as_uri(), wait_until="domcontentloaded")
    try:
        result = _subject().learn_from_page(
            page, quality_preference="1080", min_resolution=720)
        assert observed_downloads == [], "the learner must prevent, not merely cancel, downloads"
        assert result["status"] == "FAILURE"
        assert result["state"] == "failed"
        assert result["blocked_download_attempts"] == 1
        assert result["download_events_started"] == 0
        assert result["selection"]["status"] == "REFUSED_DOWNLOAD_SIDE_EFFECT"
    finally:
        context.close()


def test_learning_refuses_without_click_when_dom_guard_cannot_arm(
    browser, monkeypatch
):
    subject = _subject()
    page = _page(browser, "dropdown_rendered.html")
    monkeypatch.setattr(subject, "_arm_dom_download_guard", lambda _page: False)
    try:
        result = subject.learn_from_page(
            page, quality_preference="1080", min_resolution=720)
        assert page.evaluate("window.__row363DownloadClicks") == 0
        assert result["interaction_count"] == 0
        assert result["download_events_started"] == 0
        assert result["status"] == "FAILURE"
        assert result["state"] == "failed"
        assert result["selection"]["status"] == "REFUSED_GUARD_UNAVAILABLE"
        assert "guard" in result["error"].lower()
    finally:
        page.close()


def test_learning_and_crawl_refuse_when_dom_guard_release_is_unproven(
    browser, monkeypatch
):
    subject = _subject()
    real_release = subject._release_dom_download_guard

    def restore_but_report_unproven(page):
        count, _released = real_release(page)
        return count, False

    monkeypatch.setattr(
        subject, "_release_dom_download_guard", restore_but_report_unproven)
    dropdown = _page(browser, "dropdown_rendered.html")
    try:
        learned = subject.learn_from_page(
            dropdown, quality_preference="1080", min_resolution=720)
        assert dropdown.evaluate("window.__row363DownloadClicks") == 1
        assert learned["status"] == "FAILURE"
        assert learned["selection"]["status"] == "REFUSED_GUARD_CLEANUP"
        assert "restored" in learned["error"].lower()
    finally:
        dropdown.close()

    listing = _page(browser, "listing_infinite.html")
    try:
        crawled = subject.crawl_listing_page(
            listing,
            options=_planning_options(),
            quality_preference="1080",
            min_resolution=720,
        )
        assert crawled["status"] == "FAILURE"
        assert "restored" in crawled["error"].lower()
    finally:
        listing.close()


@pytest.mark.parametrize("mode", ["learn", "crawl"])
@pytest.mark.parametrize("operation", ["on", "route"])
def test_live_planning_refuses_when_guard_setup_fails(
    browser, monkeypatch, mode, operation
):
    subject = _subject()
    page = _page(
        browser,
        "dropdown_rendered.html" if mode == "learn" else "listing_infinite.html",
    )

    def fail_setup(*_args, **_kwargs):
        raise RuntimeError(f"fixture {operation} setup failure")

    monkeypatch.setattr(page, operation, fail_setup)
    try:
        if mode == "learn":
            result = subject.learn_from_page(
                page, quality_preference="1080", min_resolution=720)
            assert page.evaluate("window.__row363DownloadClicks") == 0
            assert result["interaction_count"] == 0
            assert result["selection"]["status"] == "REFUSED_GUARD_SETUP"
        else:
            result = subject.crawl_listing_page(
                page,
                options=_planning_options(),
                quality_preference="1080",
                min_resolution=720,
            )
        assert result["status"] == "FAILURE"
        assert "guard" in result["error"].lower()
    finally:
        page.close()


@pytest.mark.parametrize("mode", ["learn", "crawl"])
@pytest.mark.parametrize("operation", ["unroute", "remove_listener"])
def test_live_planning_refuses_when_guard_cleanup_fails(
    browser, monkeypatch, mode, operation
):
    subject = _subject()
    page = _page(
        browser,
        "dropdown_rendered.html" if mode == "learn" else "listing_infinite.html",
    )

    def fail_cleanup(*_args, **_kwargs):
        raise RuntimeError(f"fixture {operation} cleanup failure")

    monkeypatch.setattr(page, operation, fail_cleanup)
    try:
        if mode == "learn":
            result = subject.learn_from_page(
                page, quality_preference="1080", min_resolution=720)
            assert page.evaluate("window.__row363DownloadClicks") == 1
            assert result["selection"]["status"] == "REFUSED_GUARD_CLEANUP"
        else:
            result = subject.crawl_listing_page(
                page,
                options=_planning_options(),
                quality_preference="1080",
                min_resolution=720,
            )
        assert result["status"] == "FAILURE"
        assert "guard" in result["error"].lower()
    finally:
        page.close()


def test_explicit_url_height_outranks_named_hd_tier():
    href = "/movieaction/download/291403/540p/mp4?codec=h264"
    assert "/540p/" in href
    subject = _subject()
    assert subject.parse_height("Web HD 540p", href) == 540
    assert subject.parse_height("Full HD 1080p", href.replace("540p", "1080p")) == 1080


def test_policy_picks_highest_at_or_below_preference_and_refuses_below_minimum():
    subject = _subject()
    options = [
        {"height": 540, "href": "/540p/mp4", "label": "Web HD 540p"},
        {"height": 720, "href": "/720p/mp4", "label": "HD 720p"},
        {"height": 1080, "href": "/1080p/mp4", "label": "Full HD 1080p"},
        {"height": 2160, "href": "/2160p/mp4", "label": "4K 2160p"},
    ]
    assert len({o["height"] for o in options}) == 4
    picked = subject.pick_resolution(options, "1080,720,540", 720)
    assert picked["status"] == "SELECTED"
    assert picked["option"]["height"] == 1080

    refused = subject.pick_resolution(options[:2], "2160,1080,720", 1080)
    assert refused["status"] == "BELOW_MIN_RESOLUTION"
    assert refused["option"] is None
    assert "1080" in refused["reason"] and "720" in refused["reason"]


def test_policy_honors_leading_best_in_the_existing_ordered_cascade():
    options = [
        {"height": 1080, "href": "/1080p/mp4", "label": "Full HD 1080p"},
        {"height": 2160, "href": "/2160p/mp4", "label": "4K 2160p"},
    ]
    picked = _subject().pick_resolution(options, "best,1080", 720)
    assert picked["status"] == "SELECTED"
    assert picked["option"]["height"] == 2160


def test_malformed_selector_is_distinct_from_a_valid_miss(browser):
    page = _page(browser, "bar_rendered.html")
    malformed = "a.download-link[href*='.mp4'"
    miss = "a[data-quality][href]"
    try:
        assert page.locator(miss).count() == 0
        probes = _subject().probe_page_selectors(page, [malformed, miss])
        assert probes[0]["selector"] == malformed
        assert probes[0]["status"] == "MALFORMED"
        assert probes[0]["error"]
        assert probes[1] == {"selector": miss, "status": "MISS", "count": 0}
    finally:
        page.close()


def _planning_options():
    return [
        {"height": 720, "container": "mp4", "size": "1.08 GB", "href": "/720p/mp4"},
        {"height": 1080, "container": "mp4", "size": "1.96 GB", "href": "/1080p/mp4"},
        {"height": 2160, "container": "mp4", "size": "3.77 GB", "href": "/2160p/mp4"},
    ]


def test_listing_pagination_enumerates_and_deduplicates_every_scene(browser):
    page = _page(browser, "listing_pagination_1.html")
    try:
        assert page.locator(".SceneCard").count() == 2
        result = _subject().crawl_listing_page(
            page,
            options=_planning_options(),
            quality_preference="1080,720",
            min_resolution=720,
        )
        assert result["status"] == "FOUND"
        assert result["scene_count"] == 4
        assert result["pagination_pages"] == 2
        assert len({p["url"].split("#", 1)[0] for p in result["plans"]}) == 4
        assert {p["chosen_height"] for p in result["plans"]} == {1080}
        assert result["downloads_started"] == 0
    finally:
        page.close()


def test_query_pagination_and_card_taxonomy_links_do_not_lose_or_invent_scenes(browser):
    page = _page(browser, "listing_query_pagination.html")
    try:
        result = _subject().crawl_listing_page(
            page,
            options=_planning_options(),
            quality_preference="1080",
            min_resolution=720,
        )
        assert result["status"] == "FOUND"
        assert result["pagination_pages"] == 2
        assert result["scene_count"] == 2
        urls = {plan["url"] for plan in result["plans"]}
        assert any("query-one" in url for url in urls)
        assert any("query-two" in url for url in urls)
        assert all("performer" not in url for url in urls)
    finally:
        page.close()


def test_listing_infinite_scroll_enumerates_every_scene(browser):
    page = _page(browser, "listing_infinite.html")
    try:
        assert page.locator(".SceneCard").count() == 2
        result = _subject().crawl_listing_page(
            page,
            options=_planning_options(),
            quality_preference="2160,1080,720",
            min_resolution=1080,
        )
        assert result["status"] == "FOUND"
        assert result["scene_count"] == 5
        assert result["scroll_rounds"] >= 2
        assert len(result["plans"]) == 5
        assert {p["chosen_height"] for p in result["plans"]} == {2160}
        assert result["downloads_started"] == 0
    finally:
        page.close()


def test_listing_traversal_blocks_requests_and_downloads_without_breaking_crawl(browser):
    context = browser.new_context(accept_downloads=True)
    escaped_requests = []
    listing_requests = []
    observed_downloads = []

    def fulfill_side_effect(route):
        if route.request.url.endswith("/listing-page.json"):
            listing_requests.append((route.request.url, route.request.resource_type))
            route.fulfill(
                status=200,
                content_type="application/json",
                headers={"access-control-allow-origin": "*"},
                body=json.dumps({
                    "href": "https://members.example.invalid/video/from-listing-fetch",
                }),
            )
            return
        escaped_requests.append((route.request.url, route.request.resource_type))
        route.fulfill(
            status=200,
            content_type="video/mp4",
            headers={"access-control-allow-origin": "*"},
            body="offline bytes that planning must never fetch",
        )

    context.route("https://cdn.example.invalid/**", fulfill_side_effect)
    page = context.new_page()
    page.on("download", lambda download: observed_downloads.append(download))
    page.goto(_fixture("listing_infinite.html").as_uri(), wait_until="domcontentloaded")
    page.evaluate(r"""
() => {
  window.__row363ListingSideEffects = {
    scroll: 0, listingFetch: 0, mediaFetch: 0, xhr: 0, media: 0, download: 0
  };
  window.addEventListener("scroll", () => {
    const state = window.__row363ListingSideEffects;
    if (state.scroll++) return;
    state.listingFetch += 1;
    fetch("https://cdn.example.invalid/listing-page.json")
      .then(response => response.json())
      .then(({href}) => {
        const card = document.createElement("article");
        card.className = "SceneCard";
        const link = document.createElement("a");
        link.href = href;
        link.textContent = "Fetched listing scene";
        card.appendChild(link);
        document.getElementById("scene-grid").appendChild(card);
      });
    state.mediaFetch += 1;
    fetch("https://cdn.example.invalid/listing-preview-fetch.mp4").catch(() => {});
    state.xhr += 1;
    const xhr = new XMLHttpRequest();
    xhr.open("GET", "https://cdn.example.invalid/movieaction/download/fixture/1080p/mp4");
    xhr.send();
    state.media += 1;
    const video = document.createElement("video");
    video.preload = "auto";
    video.src = "https://cdn.example.invalid/listing-preview.mp4";
    document.body.appendChild(video);
    video.load();
    state.download += 1;
    const anchor = document.createElement("a");
    anchor.href = URL.createObjectURL(new Blob(["offline download bytes"], {type: "video/mp4"}));
    anchor.download = "listing-side-effect.mp4";
    document.body.appendChild(anchor);
    anchor.click();
  }, { once: true });
}
""")
    try:
        result = _subject().crawl_listing_page(
            page,
            options=_planning_options(),
            quality_preference="1080",
            min_resolution=720,
        )
        assert page.evaluate("window.__row363ListingSideEffects") == {
            "scroll": 1,
            "listingFetch": 1,
            "mediaFetch": 1,
            "xhr": 1,
            "media": 1,
            "download": 1,
        }, "the fixture must attempt every forbidden side effect exactly once"
        assert listing_requests == [
            ("https://cdn.example.invalid/listing-page.json", "fetch"),
        ], "ordinary listing APIs must remain available to infinite scroll"
        assert escaped_requests == [], "listing traversal must abort before fulfillment"
        assert observed_downloads == [], "listing traversal must prevent browser downloads"
        assert result["status"] == "FOUND"
        assert result["scene_count"] == 6
        assert result["downloads_started"] == 0
        assert result["downloads_blocked"] >= 1
        assert result["media_requests_blocked"] >= 3
    finally:
        context.close()


def test_infinite_scroll_waits_for_delayed_rendered_scenes(browser):
    page = _page(browser, "listing_infinite_delayed.html")
    try:
        result = _subject().crawl_listing_page(
            page,
            options=_planning_options(),
            quality_preference="1080",
            min_resolution=720,
        )
        assert result["status"] == "FOUND"
        assert result["scene_count"] == 3
        assert {plan["chosen_height"] for plan in result["plans"]} == {1080}
    finally:
        page.close()


def test_infinite_scroll_safety_bound_is_failure_not_partial_success(browser):
    page = _page(browser, "listing_infinite_never_stable.html")
    try:
        result = _subject().crawl_listing_page(
            page,
            options=[],
            quality_preference="1080",
            min_resolution=720,
        )
        assert result["scroll_rounds"] == 15
        assert result["scene_count"] > 1, "fixture must keep exposing scenes"
        assert result["status"] == "FAILURE"
        assert result["state"] == "failed"
        assert "infinite-scroll safety limit" in result["error"]
    finally:
        page.close()


def test_scrollable_listing_without_end_evidence_fails_closed(browser):
    page = _page(browser, "listing_scroll_completion_unknown.html")
    try:
        result = _subject().crawl_listing_page(
            page,
            options=_planning_options(),
            quality_preference="1080",
            min_resolution=720,
        )
        assert result["scene_count"] == 1
        assert result["status"] == "FAILURE"
        assert result["state"] == "failed"
        assert "no proven total/end completion" in result["error"]
    finally:
        page.close()


def test_scroll_completion_is_proven_for_every_pagination_page(browser):
    page = _page(browser, "listing_scroll_then_pagination_1.html")
    first_page = _fixture("listing_scroll_then_pagination_1.html").as_uri()
    try:
        assert page.evaluate(
            "document.documentElement.scrollHeight > window.innerHeight + 1"
        ), "page 1 must genuinely be scrollable"
        assert page.locator(".SceneCard").count() == 1
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(4400)
        assert page.evaluate("window.__row363LateSceneInserted") is True
        assert page.locator(".SceneCard").count() == 2, (
            "the fixture must really expose a scene after the old stability window"
        )

        page.goto(first_page, wait_until="domcontentloaded")
        assert page.locator(".SceneCard").count() == 1
        result = _subject().crawl_listing_page(
            page,
            options=_planning_options(),
            quality_preference="1080",
            min_resolution=720,
        )
        assert result["pagination_pages"] == 2
        assert result["scene_count"] == 2
        assert all("late-page-one" not in plan["url"] for plan in result["plans"])
        assert result["status"] == "FAILURE"
        assert result["state"] == "failed"
        assert "completion" in result["error"].lower()
        assert "scrollable listing page" in result["error"].lower()
    finally:
        page.close()


def test_empty_rendered_listing_is_failure_not_empty_success(browser):
    page = _page(browser, "listing_empty.html")
    try:
        assert page.locator(".SceneGrid").count() == 1
        assert page.locator(".SceneCard").count() == 1
        result = _subject().crawl_listing_page(
            page,
            options=_planning_options(),
            quality_preference="1080",
            min_resolution=720,
        )
        assert result["status"] == "FAILURE"
        assert result["scene_count"] == 0
        assert "zero scenes" in result["error"].lower()
    finally:
        page.close()


def test_authoritatively_empty_listing_reports_found_nothing(browser):
    page = _page(browser, "listing_explicitly_empty.html")
    try:
        result = _subject().crawl_listing_page(
            page,
            options=_planning_options(),
            quality_preference="1080",
            min_resolution=720,
        )
        assert result == {
            "status": "EMPTY",
            "state": "found_nothing",
            "scene_count": 0,
            "plans": [],
            "pagination_pages": 1,
            "scroll_rounds": 3,
            "downloads_started": 0,
            "downloads_blocked": 0,
            "media_requests_blocked": 0,
            "reason": "Rendered listing explicitly declares zero scenes.",
        }

        # A cosmetic empty-state marker cannot overrule an authoritative
        # positive total; that is a broken listing and must fail closed.
        page.set_content(
            "<body data-total-scenes='3'><section class='SceneGrid'></section>"
            "<div data-empty-list='true'>No scenes loaded</div></body>"
        )
        conflict = _subject().crawl_listing_page(
            page,
            options=_planning_options(),
            quality_preference="1080",
            min_resolution=720,
        )
        assert conflict["status"] == "FAILURE"
        assert conflict["scene_count"] == 0
        assert "refusing empty success" in conflict["error"]
    finally:
        page.close()


def test_cosmetic_empty_marker_is_not_authoritative(browser):
    page = browser.new_page(viewport={"width": 1200, "height": 760})
    page.set_content(
        "<body style='min-height:2400px'><section class='SceneGrid'></section>"
        "<div data-empty-list='true'>No scenes loaded</div></body>"
    )
    try:
        assert page.evaluate(
            "document.documentElement.scrollHeight > window.innerHeight + 1"
        ), "the marker is on an incomplete, scrollable listing"
        assert _subject()._listing_explicitly_empty(page) is False
        result = _subject().crawl_listing_page(
            page,
            options=_planning_options(),
            quality_preference="1080",
            min_resolution=720,
        )
        assert result["status"] == "FAILURE"
        assert result["state"] == "failed"
        assert result["scene_count"] == 0
        assert "completion" in result["error"].lower()
    finally:
        page.close()


def test_authoritative_empty_total_cannot_mask_pagination_failure(browser):
    page = _page(browser, "listing_explicitly_empty.html")
    page.set_content(
        "<body data-total-scenes='0'><section class='SceneGrid'></section>"
        "<div data-empty-list='true'>No scenes loaded</div>"
        "<a rel='next' href='row363-definitely-missing-page.html'>Next</a></body>"
    )
    try:
        result = _subject().crawl_listing_page(
            page,
            options=_planning_options(),
            quality_preference="1080",
            min_resolution=720,
        )
        assert result["status"] == "FAILURE"
        assert result["state"] == "failed"
        assert result["scene_count"] == 0
        assert "pagination failed" in result["error"].lower()
    finally:
        page.close()


def test_production_scene_planning_cancels_download_side_effects(browser):
    context = browser.new_context(accept_downloads=True)
    observed_downloads = []

    def observe_page(created_page):
        created_page.on(
            "download", lambda download: observed_downloads.append(download)
        )

    context.on("page", observe_page)
    page = context.new_page()
    page.goto(_fixture("listing_scene_probe.html").as_uri(), wait_until="domcontentloaded")
    try:
        assert page.locator(".SceneCard").count() == 4
        result = _subject().crawl_listing_page(
            page,
            options=[],
            quality_preference="1080,720",
            min_resolution=720,
            probe_scene_pages=True,
        )
        assert result["status"] == "FOUND"
        assert result["scene_count"] == 3
        assert all(
            "outside.example.invalid" not in plan["url"]
            for plan in result["plans"]
        )
        assert result["downloads_started"] == 0
        assert observed_downloads == [], (
            "planning must prevent browser downloads instead of trusting its result field"
        )
        assert result["downloads_blocked"] == 1
        by_url = {plan["url"]: plan for plan in result["plans"]}
        unsafe = next(plan for url, plan in by_url.items() if "side_effect" in url)
        assert unsafe["status"] == "REFUSED"
        assert unsafe["selection_status"] == "REFUSED_DOWNLOAD_SIDE_EFFECT"
        assert "blocked" in unsafe["reason"].lower()
        safe = [plan for url, plan in by_url.items() if "side_effect" not in url]
        assert {plan["chosen_height"] for plan in safe} == {1080}
    finally:
        context.close()


def test_scene_planning_blocks_opaque_navigation_fetch_without_refusing_plan(browser):
    context = browser.new_context()
    fulfilled = []
    attempted = []
    failed = []

    context.on(
        "request",
        lambda request: attempted.append(request.url)
        if request.url == "https://cdn.example.invalid/opaque-resource" else None,
    )
    context.on(
        "requestfailed",
        lambda request: failed.append(request.url)
        if request.url == "https://cdn.example.invalid/opaque-resource" else None,
    )

    def fulfill_opaque(route):
        fulfilled.append(route.request.url)
        route.fulfill(
            status=200,
            content_type="video/mp4",
            headers={"access-control-allow-origin": "*", "cache-control": "no-store"},
            body="offline regression bytes",
        )

    context.route("https://cdn.example.invalid/**", fulfill_opaque)
    control = context.new_page()
    control.goto(
        _fixture("scene_opaque_navigation_fetch.html").as_uri(),
        wait_until="domcontentloaded",
    )
    control.wait_for_timeout(100)
    assert control.evaluate("window.__row363OpaqueFetchStarted") == 1
    assert fulfilled == ["https://cdn.example.invalid/opaque-resource"], (
        "an unguarded page must reach the offline fulfiller exactly once"
    )
    control.close()
    fulfilled.clear()
    attempted.clear()
    failed.clear()

    page = context.new_page()
    page.goto(
        _fixture("listing_opaque_scene_navigation.html").as_uri(),
        wait_until="domcontentloaded",
    )
    try:
        assert page.locator(".SceneCard").count() == 1
        result = _subject().crawl_listing_page(
            page,
            options=[],
            quality_preference="1080",
            min_resolution=720,
            probe_scene_pages=True,
        )
        assert attempted == ["https://cdn.example.invalid/opaque-resource"]
        assert failed == ["https://cdn.example.invalid/opaque-resource"]
        assert fulfilled == [], "scene planning must abort opaque bytes before fulfillment"
        assert result["status"] == "FOUND"
        assert result["scene_count"] == 1
        assert result["downloads_started"] == 0
        assert result["plans"] == [{
            "url": _fixture("scene_opaque_navigation_fetch.html").as_uri(),
            "status": "PLANNED",
            "chosen_height": 1080,
            "selection_status": "SELECTED",
            "reason": "",
            "source": "scene_page",
        }]
    finally:
        context.close()


def test_network_corroboration_is_redacted_and_disagreement_is_explicit(browser):
    page = _page(browser, "bar_rendered.html")
    network_log = [
        {
            "url": "https://service.gammaapis.com/user/history/download/example/zero-entropy-fixture-token",
            "response_status": 200,
            "response_headers": [{"name": "content-type", "value": "application/json"}],
        },
        {
            "url": "https://media.example.invalid/master/1080p.m3u8?token=zero-entropy-fixture-token",
            "response_status": 200,
            "response_headers": [{"name": "content-type", "value": "application/vnd.apple.mpegurl"}],
        },
    ]
    try:
        result = _subject().learn_from_page(
            page,
            network_log=network_log,
            quality_preference="1080",
            min_resolution=720,
        )
        evidence = result["network_evidence"]
        assert len(evidence) == 2
        assert all("zero-entropy-fixture-token" not in row["url"] for row in evidence)
        assert result["corroboration"]["status"] == "DISAGREE"
        assert result["corroboration"]["detail"]
    finally:
        page.close()


def test_learning_blocks_an_opaque_fetch_instead_of_discovering_media_by_downloading_it(browser):
    page = browser.new_page()
    fulfilled = []

    def fulfill(route):
        fulfilled.append(route.request.url)
        route.fulfill(status=200, content_type="video/mp4", body="offline bytes")

    page.route("https://cdn.example.invalid/**", fulfill)
    page.set_content(
        "<button class='ScenePlayerHeaderPlus-IconItem-Button' id='download'>"
        "<span class='Icon-DownloadRevamp'></span>Download</button><div id='rows'></div>"
        "<script>document.getElementById('download').onclick=()=>{"
        "fetch('https://cdn.example.invalid/opaque-resource');"
        "document.getElementById('rows').innerHTML=\"<a class='DownloadOption' "
        "href='/movieaction/download/fixture/1080p/mp4'>Full HD 1080p</a>\";};</script>"
    )
    try:
        result = _subject().learn_from_page(
            page, quality_preference="1080", min_resolution=720)
        assert fulfilled == [], "opaque bytes must be aborted before the fixture fulfiller runs"
        assert result["status"] == "FAILURE"
        assert result["selection"]["status"] == "REFUSED_DOWNLOAD_SIDE_EFFECT"
        assert result["blocked_media_requests"] == [
            "https://cdn.example.invalid/opaque-resource"
        ]
    finally:
        page.close()


def test_network_evidence_keeps_the_newest_hundred_and_redacts_path_credentials():
    network_log = [
        {
            "url": f"https://cdn.example.invalid/media/clip-{index}.mp4",
            "response_status": 200,
            "response_headers": {"content-type": "video/mp4"},
        }
        for index in range(101)
    ]
    network_log.extend([
        {
            "url": "https://user:pass@cdn.example.invalid/media/final.mp4",
            "response_status": 200,
            "response_headers": {"content-type": "video/mp4"},
        },
        {
            "url": (
                "https://cdn.example.invalid/eyJhbGciOiJIUzI1NiJ9."
                "eyJzdWIiOiJmaXh0dXJlIn0.signature/video.mp4"
            ),
            "response_status": 200,
            "response_headers": {"content-type": "video/mp4"},
        },
        {
            "url": (
                "https://cdn.example.invalid/media/signed.mp4?"
                "hdnea=exp=999~acl=*~hmac=opaque-fixture-hmac"
            ),
            "response_status": 200,
            "response_headers": {"content-type": "video/mp4"},
        },
    ])
    evidence = _subject().media_network_evidence(network_log)
    assert len(evidence) == 100
    encoded = json.dumps(evidence)
    assert "clip-0.mp4" not in encoded
    assert "clip-100.mp4" in encoded
    assert "user:pass" not in encoded
    assert "eyJhbGciOiJIUzI1NiJ9" not in encoded
    assert "opaque-fixture-hmac" not in encoded


def test_capture_recorders_populate_redacted_page_local_load_buffer(
    browser, monkeypatch
):
    from bulk_downloader import dom_recorder, session_capture
    from tools import capture_session

    monkeypatch.setattr(session_capture, "capture_via_cdp", lambda *args, **kwargs: None)
    monkeypatch.setattr(dom_recorder, "attach_dom_recorder", lambda *args, **kwargs: None)
    context = browser.new_context()
    page = context.new_page()
    capture = SimpleNamespace()
    raw_url = (
        "https://service.gammaapis.com/user/history/download/site/"
        "load-time-zero-entropy-fixture-token"
    )

    def fulfill(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            headers={
                "access-control-allow-origin": "*",
                "set-cookie": "credential-shaped-fixture=must-not-be-buffered",
            },
            body="{}",
        )

    context.route("https://service.gammaapis.com/**", fulfill)
    try:
        wired = capture_session._attach_recorders(
            context, page, capture, redact=True)
        assert page in wired
        assert page.evaluate(
            "Number(window.__bdAffordanceOperatorActivity363 || 0)"
        ) > 0
        page.set_content(f"<script>fetch('{raw_url}')</script>")
        page.wait_for_timeout(100)
        rows = capture._affordance_page_network[page]
        relevant = [row for row in rows if "/user/history/download/" in row["url"]]
        assert len(relevant) == 1
        encoded = json.dumps(relevant)
        assert "load-time-zero-entropy-fixture-token" not in encoded
        assert "credential-shaped-fixture" not in encoded
        assert relevant[0]["response_headers"] == {
            "content-type": "application/json",
        }
    finally:
        context.close()


def test_runner_network_log_uses_the_same_path_aware_redaction_boundary():
    from bulk_downloader.runner_telemetry import TelemetryMixin

    callbacks = {}
    events = []

    class FakePage:
        def on(self, event, callback):
            callbacks[event] = callback

    fake_runner = SimpleNamespace(
        config={"log_network": True},
        log_event=lambda *args, **kwargs: events.append((args, kwargs)),
    )
    page = FakePage()
    TelemetryMixin._install_event_listeners(
        fake_runner,
        page,
        "https://member:password@members.example.invalid/scene/example",
    )
    callbacks["response"](SimpleNamespace(
        status=200,
        url=(
            "https://service.gammaapis.com/user/history/streaming/site/"
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmaXh0dXJlIn0.signature"
        ),
        headers={"content-type": "application/json", "content-length": "2"},
        request=SimpleNamespace(resource_type="xhr", method="GET"),
    ))
    assert len(events) == 1
    encoded = json.dumps(events)
    assert "member:password" not in encoded
    assert "eyJhbGciOiJIUzI1NiJ9" not in encoded
    assert "<scrubbed>" in encoded


def test_live_bridge_includes_request_emitted_by_the_learning_click(browser, tmp_path):
    page = browser.new_page()
    old_url = "https://media.example.invalid/old-before-learning.mp4"
    capture = SimpleNamespace(network_log=[{
        "url": old_url,
        "response_status": 200,
        "response_headers": [{"name": "content-type", "value": "video/mp4"}],
    }], _affordance_page_network={page: []})
    media_url = "https://service.example.invalid/user/history/download/site/zero-entropy-fixture-token"

    fulfilled = []

    def fulfill(route):
        fulfilled.append(route.request.url)
        route.fulfill(status=200, content_type="application/json", body="{}")

    def record(response):
        if "/user/history/download/" in response.url:
            capture.network_log.append({
                "url": response.url,
                "response_status": response.status,
                "response_headers": [{"name": "content-type", "value": "application/json"}],
            })

    page.route("https://service.example.invalid/**", fulfill)
    page.on("response", record)
    page.set_content(
        "<button class='ScenePlayerHeaderPlus-IconItem-Button' id='download'>"
        "<span class='Icon-DownloadRevamp'></span>Download</button><div id='rows'></div>"
        f"<script>document.getElementById('download').onclick=()=>{{fetch('{media_url}');"
        "document.getElementById('rows').innerHTML=\"<a class='DownloadOption' "
        "href='/movieaction/download/fixture/1080p/mp4'>Full HD 1080p 1.96 GB</a>\";};</script>"
    )
    subject = _subject()
    try:
        armed = subject.request_live_action(
            tmp_path, "learn", {"quality_preference": "1080", "min_resolution": 720})
        assert len(capture.network_log) == 1, "old evidence is a pre-arm precondition"
        assert subject.maybe_service_live_request([page], capture, tmp_path) is True
        envelope = subject.consume_live_result(tmp_path, armed["request_id"])
        assert envelope and envelope["state"] == "found"
        evidence = envelope["result"]["network_evidence"]
        assert len(evidence) == 1
        assert fulfilled == [], "corroboration observes the request without fetching it"
        assert old_url not in json.dumps(evidence)
        assert evidence[0]["kind"] == "download_history"
        assert "zero-entropy-fixture-token" not in evidence[0]["url"]
        assert envelope["result"]["corroboration"]["status"] == "DISAGREE"
    finally:
        page.close()


def test_live_bridge_includes_only_active_page_load_time_media_evidence(
    browser, tmp_path
):
    active_page = _page(browser, "bar_rendered.html")
    stale_page = browser.new_page()
    active_history = (
        "https://service.gammaapis.com/user/history/download/site/"
        "active-page-zero-entropy-fixture-token"
    )
    active_media = (
        "https://media.example.invalid/movie/active/1080p/mp4?"
        "hdnea=active-page-fixture-signature"
    )
    stale_media = "https://media.example.invalid/stale-tab/2160p.mp4"
    capture = SimpleNamespace(
        network_log=[{
            "url": stale_media,
            "response_status": 200,
            "response_headers": {"content-type": "video/mp4"},
        }],
        _affordance_page_network={
            active_page: [
                {
                    "url": active_history,
                    "response_status": 200,
                    "response_headers": {"content-type": "application/json"},
                },
                {
                    "url": active_media,
                    "response_status": 200,
                    "response_headers": {"content-type": "video/mp4"},
                },
            ],
            stale_page: [{
                "url": stale_media,
                "response_status": 200,
                "response_headers": {"content-type": "video/mp4"},
            }],
        },
    )
    subject = _subject()
    try:
        armed = subject.request_live_action(
            tmp_path, "learn", {"quality_preference": "best", "min_resolution": 720}
        )
        assert subject.maybe_service_live_request(
            [stale_page, active_page], capture, tmp_path
        ) is True
        envelope = subject.consume_live_result(tmp_path, armed["request_id"])
        assert envelope and envelope["state"] == "found"
        result = envelope["result"]
        assert result["shape"] == "BAR"
        assert result["options"]
        evidence = result["network_evidence"]
        assert {row["kind"] for row in evidence} == {
            "download_history", "download",
        }
        encoded = json.dumps(evidence)
        assert "stale-tab" not in encoded
        assert "active-page-fixture-signature" not in encoded
        assert "active-page-zero-entropy-fixture-token" not in encoded
        assert result["corroboration"]["status"] == "DISAGREE"
    finally:
        stale_page.close()
        active_page.close()


def test_live_bridge_prefers_the_operator_focused_popup_over_the_launch_tab(
    browser, tmp_path
):
    """Production passes the launch page last; browser focus must outrank it."""
    context = browser.new_context()
    launch_page = context.new_page()
    popup_page = context.new_page()
    try:
        launch_page.set_content("<main>Login/launch tab with no affordance</main>")
        popup_page.goto(
            _fixture("bar_rendered.html").as_uri(), wait_until="domcontentloaded"
        )
        subject = _subject()
        assert subject.attach_page_activity_marker(launch_page) is True
        assert subject.attach_page_activity_marker(popup_page) is True
        popup_page.bring_to_front()
        popup_page.mouse.click(20, 20)
        assert subject._active_page([popup_page, launch_page]) is popup_page

        armed = subject.request_live_action(
            tmp_path, "learn", {"quality_preference": "1080", "min_resolution": 720}
        )
        capture = SimpleNamespace(
            network_log=[],
            _affordance_page_network={popup_page: [], launch_page: []},
        )
        # This is the production ordering from capture_session: every other
        # wired page first, then its stale launch-page cursor last.
        assert subject.maybe_service_live_request(
            [popup_page, launch_page], capture, tmp_path
        ) is True
        envelope = subject.consume_live_result(tmp_path, armed["request_id"])
        assert envelope and envelope["state"] == "found"
        assert envelope["result"]["shape"] == "BAR"
        assert len(envelope["result"]["options"]) == 8
    finally:
        context.close()


def test_standalone_network_action_uses_only_the_focused_page_buffer(
    browser, tmp_path
):
    page = _page(browser, "bar_rendered.html")
    current_url = "https://media.example.invalid/current-page/1080p.mp4"
    stale_url = "https://media.example.invalid/stale-background-tab/2160p.mp4"
    capture = SimpleNamespace(
        network_log=[{
            "url": stale_url,
            "response_status": 200,
            "response_headers": {"content-type": "video/mp4"},
        }],
        _affordance_page_network={
            page: [{
                "url": current_url,
                "response_status": 200,
                "response_headers": {"content-type": "video/mp4"},
            }],
        },
    )
    subject = _subject()
    try:
        armed = subject.request_live_action(tmp_path, "network", {})
        assert subject.maybe_service_live_request([page], capture, tmp_path) is True
        envelope = subject.consume_live_result(tmp_path, armed["request_id"])
        assert envelope and envelope["state"] == "found"
        encoded = json.dumps(envelope["result"]["network_evidence"])
        assert "current-page" in encoded
        assert "stale-background-tab" not in encoded
    finally:
        page.close()


def test_page_network_buffer_excludes_an_earlier_document_in_the_same_tab(browser):
    context = browser.new_context()
    page = context.new_page()
    capture = SimpleNamespace()
    subject = _subject()
    old_url = "https://media.example.invalid/old-document/2160p.mp4"
    current_url = "https://media.example.invalid/current-document/1080p.mp4"
    reloaded_url = "https://media.example.invalid/reloaded-document/720p.mp4"

    def fulfill(route):
        route.fulfill(
            status=200,
            content_type="video/mp4",
            headers={"access-control-allow-origin": "*"},
            body="fixture",
        )

    context.route("https://media.example.invalid/**", fulfill)
    try:
        assert subject.attach_page_network_buffer(page, capture) == []
        page.set_content(f"<script>fetch('{old_url}')</script>")
        page.wait_for_timeout(100)
        page.goto(_fixture("bar_rendered.html").as_uri(), wait_until="domcontentloaded")
        page.evaluate("url => fetch(url)", current_url)
        page.wait_for_timeout(100)
        evidence = subject.media_network_evidence(
            subject._active_page_network(capture, page)
        )
        encoded = json.dumps(evidence)
        assert "current-document" in encoded
        assert "old-document" not in encoded

        # A URL-derived key cannot distinguish a same-URL reload.  The next
        # document must start with a clean evidence epoch even though page.url
        # is unchanged.
        page.reload(wait_until="domcontentloaded")
        page.evaluate("url => fetch(url)", reloaded_url)
        page.wait_for_timeout(100)
        evidence = subject.media_network_evidence(
            subject._active_page_network(capture, page)
        )
        encoded = json.dumps(evidence)
        assert "reloaded-document" in encoded
        assert "current-document" not in encoded
        assert "old-document" not in encoded
    finally:
        context.close()


def test_page_network_buffer_survives_same_document_spa_navigation(browser):
    context = browser.new_context()
    page = context.new_page()
    capture = SimpleNamespace()
    subject = _subject()
    media_url = "https://media.example.invalid/same-document/1080p.mp4"

    context.route(
        media_url,
        lambda route: route.fulfill(
            status=200,
            content_type="video/mp4",
            headers={"access-control-allow-origin": "*"},
            body="fixture",
        ),
    )
    try:
        assert subject.attach_page_network_buffer(page, capture) == []
        page.goto(_fixture("bar_rendered.html").as_uri(), wait_until="domcontentloaded")
        page.evaluate("url => fetch(url)", media_url)
        page.wait_for_timeout(100)
        before = subject.media_network_evidence(
            subject._active_page_network(capture, page)
        )
        assert "same-document" in json.dumps(before)

        page.evaluate("history.pushState({}, '', '#same-document-route')")
        after = subject.media_network_evidence(
            subject._active_page_network(capture, page)
        )
        assert "same-document" in json.dumps(after)
    finally:
        context.close()


@pytest.mark.parametrize("mode", ["network", "learn"])
def test_missing_page_network_recorder_is_failed_not_found_nothing(
    browser, tmp_path, mode
):
    page = _page(browser, "bar_rendered.html")
    subject = _subject()
    try:
        armed = subject.request_live_action(tmp_path, mode, {})
        assert subject.maybe_service_live_request(
            [page], SimpleNamespace(network_log=[]), tmp_path
        ) is True
        envelope = subject.consume_live_result(tmp_path, armed["request_id"])
        assert envelope and envelope["state"] == "failed"
        assert "network capture is unavailable" in envelope["error"].lower()
        assert "found_nothing" not in json.dumps(envelope)
    finally:
        page.close()


def test_saved_template_is_selector_only_and_tainted_negative_control_is_caught(
    browser, tmp_path
):
    page = _page(browser, "dropdown_rendered.html")
    try:
        learned = _subject().learn_from_page(
            page, quality_preference="1080,720", min_resolution=720
        )
    finally:
        page.close()
    assert learned["status"] == "FOUND"

    saved = _subject().stage_learned_template(
        learned,
        host="members.example.invalid",
        quality_preference="1080,720",
        min_resolution=720,
        drafts_dir=tmp_path,
    )
    path = tmp_path / saved["file"]
    assert path.is_file() and path.stat().st_size > 0
    template = json.loads(path.read_text(encoding="utf-8"))
    assert template["status"] == "draft_review_required"
    assert template["patterns"] == [r"members\.example\.invalid"]
    assert template["learned"]["download"] == {
        "trigger_selectors": [
            "[class*='ScenePlayerHeaderPlus-IconItem']"
            ":has([class*='Icon-Download'])"
        ],
        "row_selectors": ["a[class*='DownloadOption']"],
        "url_attribute": "href",
    }
    assert template["config_defaults"] == {
        "quality_preference": "1080,720",
        "min_resolution": 720,
    }
    assert template["network_patterns"] == []
    assert template["learning_evidence"]["dom_options_proven"] is True
    from bulk_downloader.template_manager import promote_draft, promote_gate_errors
    assert promote_gate_errors(template) == []
    generic_without_network_proof = copy.deepcopy(template)
    generic_without_network_proof["schema_version"] = "3"
    assert "network_patterns must be a non-empty list" in promote_gate_errors(
        generic_without_network_proof
    )
    encoded = json.dumps(template).lower()
    for forbidden in (
        "cookie_file",
        "cookies",
        "password",
        "credential",
        "zero-entropy-fixture-token",
        "/movieaction/download/291403",
    ):
        assert forbidden not in encoded
    assert _subject().template_safety_findings(template) == []

    tainted = copy.deepcopy(template)
    tainted["cookie_file"] = "/tmp/zero-entropy-cookie.json"
    findings = _subject().template_safety_findings(tainted)
    assert findings
    assert any("cookie_file" in finding for finding in findings)
    with pytest.raises(ValueError, match="credential|cookie|secret"):
        _subject().write_learned_template(tainted, drafts_dir=tmp_path)

    for key in ("authToken", "cookieJar"):
        keyed_taint = copy.deepcopy(template)
        keyed_taint[key] = "opaque-fixture-value"
        assert any(
            key in finding
            for finding in _subject().template_safety_findings(keyed_taint)
        )

    for unsafe_selector in (
        "a[data-auth='opaque-fixture-value']",
        "a[href*='movieaction/download/member-slug']",
    ):
        selector_taint = copy.deepcopy(template)
        selector_taint["learned"]["download"]["row_selectors"] = [unsafe_selector]
        assert _subject().template_safety_findings(selector_taint), unsafe_selector

    concrete = copy.deepcopy(learned)
    concrete["row_selector"] = "a[href^='/movieaction/download/291403/']"
    with pytest.raises(ValueError, match="member URL|member URL safety"):
        _subject().build_learned_template(
            concrete,
            host="members.example.invalid",
            quality_preference="1080,720",
            min_resolution=720,
        )
    concrete_template = copy.deepcopy(template)
    concrete_template["learned"]["download"]["row_selectors"] = [
        "a[href^='/movieaction/download/291403/']"]
    assert any(
        "member URL" in finding
        for finding in _subject().template_safety_findings(concrete_template)
    )
    with pytest.raises(ValueError, match="member URL"):
        _subject().write_learned_template(concrete_template, drafts_dir=tmp_path)

    proof_tamper = copy.deepcopy(template)
    proof_tamper["learned"]["download"]["row_selectors"] = [
        "a[class*='AlternateDownloadOption']"
    ]
    assert _subject().template_safety_findings(proof_tamper) == []
    assert any(
        "proof digest" in error
        for error in _subject().learned_template_gate_errors(proof_tamper)
    )
    assert any("proof digest" in error for error in promote_gate_errors(proof_tamper))
    with pytest.raises(ValueError, match="integrity|digest"):
        _subject().write_learned_template(proof_tamper, drafts_dir=tmp_path)
    path.write_text(json.dumps(proof_tamper), encoding="utf-8")
    promoted = promote_draft(
        saved["file"], drafts_dir=tmp_path, reviewed_dir=tmp_path / "reviewed"
    )
    assert promoted["ok"] is False
    assert "proof digest" in json.dumps(promoted)

    extra_field = copy.deepcopy(template)
    extra_field["memberPath"] = "/movieaction/download/291403/opaque-member-token"
    extra_field["learning_evidence"]["proof_digest"] = (
        _subject().learned_template_digest(extra_field)
    )
    shape_errors = _subject().learned_template_gate_errors(extra_field)
    assert any("memberPath" in error and "not allowed" in error for error in shape_errors)
    with pytest.raises(ValueError, match="not allowed"):
        _subject().write_learned_template(extra_field, drafts_dir=tmp_path)

    with pytest.raises(ValueError, match="between 0 and 8640"):
        _subject().build_learned_template(
            learned,
            host="members.example.invalid",
            quality_preference="1080,720",
            min_resolution=-7,
        )
    with pytest.raises(ValueError, match="quality_preference"):
        _subject().build_learned_template(
            learned,
            host="members.example.invalid",
            quality_preference="zero-entropy-fixture-token",
            min_resolution=720,
        )


def test_same_host_drafts_are_digest_named_and_never_overwritten(browser, tmp_path):
    subject = _subject()
    page = _page(browser, "bar_rendered.html")
    try:
        learned = subject.learn_from_page(
            page, quality_preference="1080", min_resolution=720)
    finally:
        page.close()
    first = subject.build_learned_template(
        learned,
        host="members.example.invalid",
        quality_preference="1080",
        min_resolution=720,
    )
    second = subject.build_learned_template(
        learned,
        host="members.example.invalid",
        quality_preference="720",
        min_resolution=720,
    )
    first_digest = subject.learned_template_digest(first)
    second_digest = subject.learned_template_digest(second)
    assert first_digest != second_digest

    first_write = subject.write_learned_template(first, drafts_dir=tmp_path)
    first_path = tmp_path / first_write["file"]
    first_bytes = first_path.read_bytes()
    assert first_digest[:12] in first_write["file"]

    second_write = subject.write_learned_template(second, drafts_dir=tmp_path)
    second_path = tmp_path / second_write["file"]
    assert second_digest[:12] in second_write["file"]
    assert second_path != first_path
    assert first_path.read_bytes() == first_bytes

    idempotent = subject.write_learned_template(first, drafts_dir=tmp_path)
    assert idempotent["file"] == first_write["file"]
    assert first_path.read_bytes() == first_bytes

    collision_dir = tmp_path / "collision"
    collision_dir.mkdir()
    collision_path = collision_dir / first_write["file"]
    planted = b"operator-owned unreviewed draft\n"
    collision_path.write_bytes(planted)
    with pytest.raises(ValueError, match="exists|collision|overwrite"):
        subject.write_learned_template(first, drafts_dir=collision_dir)
    assert collision_path.read_bytes() == planted


@pytest.mark.parametrize("attribute", [
    "data-key",
    "data-signature",
    "data-api-key",
    "data-csrf",
])
def test_template_rejects_key_and_signature_credential_selectors(attribute):
    learned = {
        "status": "FOUND",
        "shape": "BAR",
        "row_selector": f"a[{attribute}='uQ7mV9xR2pL4nK8z']",
        "trigger_selector": None,
        "options": [{
            "height": 1080,
            "href": "/movieaction/download/fixture/1080p/mp4",
            "label": "Full HD 1080p",
        }],
    }
    with pytest.raises(ValueError, match="credential|token"):
        _subject().build_learned_template(
            learned,
            host="members.example.invalid",
            quality_preference="1080",
            min_resolution=720,
        )


def test_stage_route_ignores_client_learned_payload_and_uses_nonce_proof(
    browser, tmp_path, monkeypatch
):
    page = _page(browser, "dropdown_rendered.html")
    try:
        proof = _subject().learn_from_page(
            page, quality_preference="1080,720", min_resolution=720)
    finally:
        page.close()
    proof["page_host"] = "members.example.invalid"

    from flask import Flask
    from bulk_downloader import app_captures, template_manager
    from tools import cockpit_core

    requested = []
    discarded = []
    monkeypatch.setattr(app_captures, "_app_s_cfg", lambda: {
        "site363": {
            "quality_preference": "1080,720",
            "min_resolution": 720,
        },
    })
    monkeypatch.setattr(template_manager, "DRAFTS_DIR", tmp_path)
    monkeypatch.setattr(
        cockpit_core,
        "get_live_learning_proof",
        lambda task_id, request_id: requested.append((task_id, request_id)) or copy.deepcopy(proof),
    )
    monkeypatch.setattr(
        cockpit_core,
        "discard_live_learning_proof",
        lambda task_id, request_id: discarded.append((task_id, request_id)),
    )
    app = Flask("row363-stage")
    app.register_blueprint(app_captures.captures_bp)
    request_id = "a" * 32
    response = app.test_client().post("/api/captures/stage_learning", json={
        "task_id": "t_server_owned",
        "request_id": request_id,
        "site_id": "site363",
        "host": "attacker.example.invalid",
        "quality_preference": "160",
        "min_resolution": 0,
        "learned": {
            "status": "FOUND",
            "row_selector": "a[href^='https://attacker.example.invalid/member/token']",
            "cookie_file": "/tmp/should-never-cross.json",
        },
    })
    assert response.status_code == 200, response.get_json()
    assert requested == [("t_server_owned", request_id)]
    assert discarded == [("t_server_owned", request_id)]
    body = response.get_json()
    template = body["template"]
    assert template["host"] == "members.example.invalid"
    assert template["config_defaults"]["min_resolution"] == 720
    assert template["learned"]["download"]["row_selectors"] == [
        "a[class*='DownloadOption']"]
    encoded = json.dumps(template).lower()
    assert "attacker" not in encoded
    assert "cookie_file" not in encoded


def test_live_bridge_serializes_actions_and_retains_nonce_bound_proof(tmp_path, monkeypatch):
    from tools import cockpit_core

    task_id = "t_row363_proof"
    monkeypatch.setattr(cockpit_core, "get_task", lambda value: {
        "task_id": value,
        "category": "capture",
        "out_dir": str(tmp_path),
    } if value == task_id else None)
    cockpit_core._LIVE_LEARNING_ACTIVE.pop(task_id, None)
    cockpit_core._LIVE_LEARNING_DEADLINES.pop(task_id, None)
    for key in list(cockpit_core._LIVE_LEARNING_PROOFS):
        if key[0] == task_id:
            cockpit_core._LIVE_LEARNING_PROOFS.pop(key, None)

    armed = cockpit_core.live_learning_capture(task_id, mode="learn", action="arm")
    request_id = armed["request_id"]
    with pytest.raises(cockpit_core.ValidationError, match="already running"):
        cockpit_core.live_learning_capture(task_id, mode="network", action="arm")

    result_path = _subject().live_result_path(tmp_path, request_id)
    result_path.write_text(json.dumps({
        "request_id": request_id,
        "mode": "learn",
        "state": "found",
        "result": {"status": "FOUND", "row_selector": "a.DownloadOption"},
    }), encoding="utf-8")
    polled = cockpit_core.live_learning_capture(
        task_id, action="poll", mode="learn", request_id=request_id)
    assert polled["state"] == "found"
    assert cockpit_core.get_live_learning_proof(task_id, request_id)["row_selector"] == "a.DownloadOption"
    with pytest.raises(cockpit_core.ValidationError, match="no server-proven"):
        cockpit_core.get_live_learning_proof(task_id, "b" * 32)
    cockpit_core.discard_live_learning_proof(task_id, request_id)

    oversized = cockpit_core.live_learning_capture(
        task_id, mode="crawl", action="arm")
    oversized_result_path = _subject().live_result_path(
        tmp_path, oversized["request_id"])
    oversized_result_path.write_bytes(b"x" * (_subject()._MAX_BRIDGE_BYTES + 1))
    failed = cockpit_core.live_learning_capture(
        task_id,
        action="poll",
        mode="crawl",
        request_id=oversized["request_id"],
    )
    assert failed["state"] == "failed"
    assert "bounded bridge" in failed["response"]["error"]
    rearmed = cockpit_core.live_learning_capture(
        task_id, mode="network", action="arm")
    assert rearmed["state"] == "running"
    cockpit_core._LIVE_LEARNING_DEADLINES[task_id] = 0
    replacement = cockpit_core.live_learning_capture(
        task_id, mode="learn", action="arm")
    assert replacement["state"] == "running"
    assert replacement["request_id"] != rearmed["request_id"]
    cancelled = cockpit_core.live_learning_capture(
        task_id,
        mode="learn",
        action="cancel",
        request_id=replacement["request_id"],
    )
    assert cancelled["state"] == "cancelled"
    assert task_id not in cockpit_core._LIVE_LEARNING_ACTIVE
    assert task_id not in cockpit_core._LIVE_LEARNING_DEADLINES
    assert not (tmp_path / _subject().REQUEST_FILE).exists()


def test_cancelled_consumed_request_cannot_poison_a_new_bridge_action(
    tmp_path, monkeypatch
):
    from tools import cockpit_core

    subject = _subject()
    task_id = "t_row363_cancel_race"
    monkeypatch.setattr(cockpit_core, "get_task", lambda value: {
        "task_id": value,
        "category": "capture",
        "out_dir": str(tmp_path),
    } if value == task_id else None)
    cockpit_core._LIVE_LEARNING_ACTIVE.pop(task_id, None)
    cockpit_core._LIVE_LEARNING_DEADLINES.pop(task_id, None)
    for key in list(cockpit_core._LIVE_LEARNING_PROOFS):
        if key[0] == task_id:
            cockpit_core._LIVE_LEARNING_PROOFS.pop(key, None)

    old_started = threading.Event()
    release_old = threading.Event()
    worker_results = []

    class BlockingCapture:
        @property
        def _affordance_page_network(self):
            old_started.set()
            release_old.wait(timeout=5)
            return {live_page: []}

    class LivePage:
        def is_closed(self):
            return False

    live_page = LivePage()
    old = cockpit_core.live_learning_capture(
        task_id, mode="network", action="arm")
    old_request_id = old["request_id"]
    worker = threading.Thread(
        target=lambda: worker_results.append(subject.maybe_service_live_request(
            [live_page], BlockingCapture(), tmp_path
        )),
        name="row363-old-live-action",
        daemon=True,
    )
    worker.start()
    try:
        assert old_started.wait(timeout=2), "the old worker must consume and enter the request"
        assert not (tmp_path / subject.REQUEST_FILE).exists(), (
            "the cancellation race requires an already-consumed request"
        )
        cancelled = cockpit_core.live_learning_capture(
            task_id,
            mode="network",
            action="cancel",
            request_id=old_request_id,
        )
        assert cancelled["state"] == "cancelled"

        new = cockpit_core.live_learning_capture(
            task_id, mode="network", action="arm")
        new_request_id = new["request_id"]
        assert new_request_id != old_request_id
        request_path = tmp_path / subject.REQUEST_FILE
        assert json.loads(request_path.read_text(encoding="utf-8"))["request_id"] == (
            new_request_id
        )

        release_old.set()
        worker.join(timeout=5)
        assert not worker.is_alive(), "the old completion must reach its cancellation check"
        assert worker_results == [True]
        assert not subject.live_result_path(tmp_path, old_request_id).exists()
        assert subject.consume_live_result(tmp_path, old_request_id) is None
        assert json.loads(request_path.read_text(encoding="utf-8"))["request_id"] == (
            new_request_id
        ), "the old completion must not remove or replace the new request"

        assert subject.maybe_service_live_request(
            [live_page], SimpleNamespace(
                network_log=[], _affordance_page_network={live_page: []}
            ), tmp_path
        ) is True
        polled = cockpit_core.live_learning_capture(
            task_id,
            action="poll",
            mode="network",
            request_id=new_request_id,
        )
        assert polled["state"] == "found_nothing"
        assert polled["response"]["request_id"] == new_request_id
    finally:
        release_old.set()
        worker.join(timeout=5)
        cockpit_core._LIVE_LEARNING_ACTIVE.pop(task_id, None)
        cockpit_core._LIVE_LEARNING_DEADLINES.pop(task_id, None)
        for key in list(cockpit_core._LIVE_LEARNING_PROOFS):
            if key[0] == task_id:
                cockpit_core._LIVE_LEARNING_PROOFS.pop(key, None)


def test_legitimate_no_affordance_returns_unknown(browser):
    page = _page(browser, "no_affordance_rendered.html")
    try:
        assert page.locator("video").get_attribute("src").startswith("blob:")
        result = _subject().learn_from_page(
            page, network_log=[], quality_preference="1080", min_resolution=720
        )
        assert result["status"] == "UNKNOWN"
        assert result["shape"] == "UNKNOWN"
        assert result["options"] == []
        assert result["interaction_count"] == 0
    finally:
        page.close()


def test_one_stable_trigger_substring_matches_both_gamma_siblings(browser):
    subject = _subject()
    selector = "[class*='ScenePlayerHeaderPlus-IconItem']"
    page = browser.new_page()
    try:
        page.set_content(
            "<div class='ScenePlayerHeaderPlus-IconItem styles_hash'>"
            "<span class='Icon-Download'></span></div>"
        )
        assert subject.probe_page_selectors(page, [selector])[0]["status"] == "PROVEN"
        page.set_content(
            "<button class='ScenePlayerHeaderPlus-IconItem-Button styles_other'>"
            "<span class='Icon-DownloadRevamp'></span></button>"
        )
        assert subject.probe_page_selectors(page, [selector])[0]["status"] == "PROVEN"
    finally:
        page.close()


def test_saved_gamma_trigger_replays_on_adulttime_sibling_and_ignores_hidden_rows(browser):
    subject = _subject()
    gamma = _page(browser, "dropdown_rendered.html")
    try:
        first = subject.learn_from_page(
            gamma, quality_preference="1080,720", min_resolution=720)
    finally:
        gamma.close()
    proven = first["trigger_selector"]
    assert proven and ":has([class*='Icon-Download'])" in proven

    adulttime = _page(browser, "adulttime_hidden_dropdown_rendered.html")
    try:
        assert adulttime.locator("a[class*='DownloadOption']").count() == 3
        assert adulttime.locator("a[class*='DownloadOption']:visible").count() == 0
        replay = subject.learn_from_page(
            adulttime,
            quality_preference="1080,720",
            min_resolution=720,
            trigger_selectors=[proven],
        )
        assert replay["status"] == "FOUND"
        assert replay["shape"] == "DROPDOWN", "hidden DOM rows are not an in-page BAR"
        assert replay["trigger_selector"] == proven
        assert [option["height"] for option in replay["options"]] == [540, 720, 1080]
        assert adulttime.evaluate("window.__row363AdulttimeClicks") == 1
    finally:
        adulttime.close()


def test_transform_control_imports_parser_without_judging_height_priority():
    """Mutation transform control: importability says nothing about precedence."""
    assert callable(_subject().parse_height)
