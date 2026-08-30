"""A confirmed photo gallery is not a failed video page.

The live row-399 incident exposed two independent mistakes: a ``/films-6K/``
navigation link reached the resolution ranker, and the resulting failure was
described as a broken video control even though the rendered page was a photo
set.  These tests keep the two decisions conservative:

* a resolution-only link is chrome only when the DOM proves navigation context;
* absence is claimed only for a fully rendered, positively identified photo
  gallery, never because an open-ended list of strings happened not to match.
"""

BD_GATE_SCOPE = "module"

from contextlib import contextmanager
import threading

import pytest

from bulk_downloader import candidate_filter as candidate_filter
from bulk_downloader import runner as runner_module
from bulk_downloader.detect import (
    find_best_download,
    parse_size_bytes,
    res_score,
)


GALLERY_URL = (
    "https://venus.wowgirls.com/gallery/x15b9ab4/stunned-by-each-other"
)

_NAV = """
<section class="ps_main_menu">
  <section><div class="main_menu"><ul class="navigation">
    <li class="mi_films_4K"><a href="/films-4K/">4K</a></li>
    <li class="mi_films_5K"><a href="/films-5K/">5K</a></li>
    <li class="mi_films_6K"><a href="/films-6K/">6K</a></li>
    <li class="mi_girls"><a href="/models/">Models</a></li>
    <li class="mi_home"><a href="/">Home</a></li>
  </ul></div></section>
</section>
"""

_PHOTO_GRID = """
<main class="gallery">
  <h1>Stunned By Each Other</h1>
  <a href="/gallery/x15b9ab4/photo/1"><img src="/img/a.jpg" alt="still one"></a>
  <a href="/gallery/x15b9ab4/photo/2"><img src="/img/b.jpg" alt="still two"></a>
  <a href="/gallery/x15b9ab4/photo/3"><img src="/img/c.jpg" alt="still three"></a>
</main>
"""

_GALLERY = (
    f'<!doctype html><html class="js video no-touchevents"><body>'
    f"{_NAV}{_PHOTO_GRID}</body></html>"
)

_SCENE = f"""<!doctype html><html><body>
{_NAV}
<div class="content_download video_downloads">
  <div class="row">
    <a class="ct_dl_button" href="https://cdn.example/dl/1280x720_60FPS.mp4">1280 x 720</a>
    <div class="caption">HD &bull; H264 &bull; 60fps &bull; 395MB</div>
  </div>
  <div class="row">
    <a class="ct_dl_button" href="https://cdn.example/dl/1920x1080_60FPS.mp4">1920 x 1080</a>
    <div class="caption">FHD &bull; H264 &bull; 60fps &bull; 1.99GB</div>
  </div>
  <div class="row">
    <a class="ct_dl_button" href="https://cdn.example/dl/3840x2160_60FPS.mp4">3840 x 2160</a>
    <div class="caption">4K &bull; H264 &bull; 60fps &bull; 3.44GB</div>
  </div>
  <div class="row">
    <a class="ct_dl_button" href="https://cdn.example/dl/7680x4320_60FPS.mp4">7680 x 4320</a>
    <div class="caption">8K &bull; HEVC &bull; 60fps &bull; 3.01GB</div>
  </div>
</div>
</body></html>"""

_OPAQUE_SIGNED_CONTROL = """<!doctype html><html><body>
<main>
  <section class="quality-picker">
    <a class="quality-option" data-signed-url-key="opaque-asset-token">6K</a>
  </section>
</main>
</body></html>"""


@contextmanager
def _page(html, url=None):
    sync_playwright = pytest.importorskip(
        "playwright.sync_api").sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            if url:
                page.route("**/*", lambda route: route.fulfill(
                    status=200, content_type="text/html", body=html))
                page.goto(url, wait_until="load")
                assert page.url == url
            else:
                page.set_content(html, wait_until="load")
            yield page
        finally:
            browser.close()


def _candidate_hrefs(best):
    hrefs = []
    for candidate in (best or {}).get("_all_candidates", []):
        locator = candidate.get("locator")
        hrefs.append((locator.get_attribute("href") or "") if locator else "")
    return hrefs


def _media_snapshot(page):
    return page.evaluate(runner_module._PAGE_MEDIA_SNAPSHOT_JS)


def test_precondition_gallery_reproduces_the_measured_6k_ghost():
    ghost = "6K /films-6K/"
    assert res_score(ghost) == 3160
    assert parse_size_bytes(ghost) == 0

    with _page(_GALLERY) as page:
        assert page.locator("nav,header,footer,[role='navigation']").count() == 0
        assert page.locator(".ps_main_menu .main_menu ul.navigation a").count() == 5
        assert page.locator("a[href='/films-6K/']").count() == 1
        assert page.locator("main.gallery img").count() == 3
        assert page.locator(
            "video,audio,source,iframe,embed,object").count() == 0


def test_wide_scan_drops_the_navigation_ghost():
    """RED on the pinned main: the ancestor walk returns ``/films-6K/``."""
    with _page(_GALLERY) as page:
        best = find_best_download(page, learned=None)
        assert best is None, (
            "photo gallery produced a candidate: score=%r size=%r text=%r"
            % ((best or {}).get("score"), (best or {}).get("size"),
               (best or {}).get("text")))


def test_broad_learned_anchor_selector_uses_the_same_chrome_filter():
    """RED: learned ``a`` currently returns before candidate filtering."""
    with _page(_GALLERY) as page:
        best = find_best_download(
            page, learned={"row_selectors": ["a"]})
        assert best is None, (
            "broad learned selector escaped filtering: score=%r text=%r"
            % ((best or {}).get("score"), (best or {}).get("text")))


@pytest.mark.parametrize(
    "six_k_attributes",
    [
        'href="/films-6K/" onclick=""',
        'href="/films-6K/" onclick="analytics.track(\'header-nav\')"',
        'href="/films-6K/?utm_source=header"',
        'href="/films-6K/?utm_campaign=download"',
        'href="/films-6K/#top"',
    ],
    ids=[
        "empty-onclick",
        "tracking-onclick",
        "tracking-query",
        "download-word-in-tracking-value",
        "fragment",
    ],
)
@pytest.mark.parametrize("learned", [None, {"row_selectors": ["a"]}],
                         ids=["wide", "broad-learned"])
def test_navigation_decoration_does_not_manufacture_download_authority(
        six_k_attributes, learned):
    html = _GALLERY.replace('href="/films-6K/"', six_k_attributes)
    with _page(html) as page:
        best = find_best_download(page, learned=learned)
    assert best is None, (
        "decorated navigation ghost escaped filtering: "
        f"{(best or {}).get('text')!r}")


@pytest.mark.parametrize("learned", [None, {"row_selectors": ["a"]}],
                         ids=["wide", "broad-learned"])
@pytest.mark.parametrize(
    "attributes",
    [
        'href="/opaque/quality/6K" onclick="startDownload()"',
        'href="/opaque/quality/6K?download=1"',
    ],
    ids=["download-handler", "download-query"],
)
def test_navigation_control_with_download_intent_is_kept(attributes, learned):
    html = (
        "<!doctype html><html><body><nav>"
        f"<a {attributes}>6K</a>"
        "</nav></body></html>"
    )
    with _page(html) as page:
        best = find_best_download(page, learned=learned)
    assert best is not None
    assert best["score"] == 3160


def test_learned_candidates_use_same_work_before_resolution():
    """A broad learned selector must not resurrect related-scene ranking."""
    page_url = "https://members.example/scene/target-work"
    html = """<!doctype html><html><body><main>
      <a href="https://cdn.example/media/target-work-1080p.mp4">1080p</a>
      <a href="https://cdn.example/media/unrelated-scene-4320p.mp4">4320p</a>
    </main></body></html>"""
    with _page(html, page_url) as page:
        best = find_best_download(
            page, learned={"row_selectors": ["a"]})
        assert best is not None
        assert "target-work" in (
            best["locator"].get_attribute("href") or "")
        candidates = best.get("_all_candidates") or []
        assert len(candidates) == 2
        assert [candidate.get("work") for candidate in candidates] == [1, 0]


def test_learned_work_affinity_is_global_across_selector_chain():
    page_url = "https://members.example/scene/target-work"
    html = """<!doctype html><html><body><main>
      <a href="https://cdn.example/media/unrelated-scene-4320p.mp4">4320p</a>
      <button class="download"
        data-href="https://cdn.example/media/target-work-1080p.mp4">1080p</button>
    </main></body></html>"""
    with _page(html, page_url) as page:
        best = find_best_download(page, learned={
            "row_selectors": ["a", "button.download"],
        })
        assert best is not None
        assert "target-work" in (
            best["locator"].get_attribute("data-href") or "")
        assert best.get("_learned_sel") == "button.download"


@pytest.mark.parametrize("page_url", [None, GALLERY_URL],
                         ids=["both-unknown-work", "both-same-work"])
def test_learned_selector_priority_breaks_equal_work_ties(page_url):
    first_href = "/media/first-1080p.mp4"
    second_href = "/media/second-4320p.mp4"
    if page_url:
        first_href = "/media/stunned-by-each-other-first-1080p.mp4"
        second_href = "/media/stunned-by-each-other-second-4320p.mp4"
    html = f"""<!doctype html><html><body><main>
      <a class="first" href="{first_href}">1080p</a>
      <button class="second" data-href="{second_href}">4320p</button>
    </main></body></html>"""
    with _page(html, page_url) as page:
        best = find_best_download(page, learned={
            "row_selectors": ["a.first", "button.second"],
        })
        assert best is not None
        assert best.get("_learned_sel") == "a.first"
        assert best["locator"].get_attribute("class") == "first"


def test_precise_learned_opaque_control_keeps_explicit_authority():
    """Signal admission narrows broad selectors, not a precise taught row."""
    html = """<!doctype html><html><body><main>
      <a class="opaque-control" data-signed-url-key="opaque-asset-token">
        Original
      </a>
    </main></body></html>"""
    with _page(html) as page:
        best = find_best_download(
            page, learned={"row_selectors": ["a.opaque-control"]})
        assert best is not None
        assert best["locator"].get_attribute(
            "data-signed-url-key") == "opaque-asset-token"


@pytest.mark.parametrize(
    "selector",
    [
        '[role="dialog"] li[role="menuitem"]',
        '[role="dialog"] li:nth-child(1)',
        'li:has-text("Original")',
    ],
    ids=["scoped-menu-role", "positive-structural", "positive-text"],
)
def test_precise_positive_learned_selector_keeps_reviewed_authority(selector):
    html = """<!doctype html><html><body>
      <div role="dialog"><ul><li role="menuitem">Original</li></ul></div>
    </body></html>"""
    with _page(html) as page:
        best = find_best_download(
            page, learned={"row_selectors": [selector]})
        assert best is not None
        assert best["locator"].get_attribute("role") == "menuitem"
        assert "Original" in best["text"]


@pytest.mark.parametrize(
    "scope,tag,role",
    [
        ('role="dialog"', "li", "listitem"),
        ('role="dialog"', "li", "menuitemradio"),
        ('role="dialog"', "tr", "row"),
        ('aria-modal="true"', "li", "listitem"),
    ],
    ids=["listitem", "menuitemradio", "row", "aria-modal-listitem"],
)
def test_builder_scoped_opaque_row_roles_keep_authority(scope, tag, role):
    selector = (
        f'[aria-modal="true"] {tag}[role="{role}"]'
        if scope.startswith("aria-modal")
        else f'[role="dialog"] {tag}[role="{role}"]'
    )
    row_markup = f"<{tag} role=\"{role}\">Original</{tag}>"
    if tag == "tr":
        row_markup = (
            f"<table><tbody><tr role=\"{role}\"><td>Original</td></tr>"
            "</tbody></table>"
        )
    html = (
        f"<!doctype html><html><body><div {scope}>"
        f"{row_markup}</div></body></html>"
    )
    with _page(html) as page:
        best = find_best_download(
            page, learned={"row_selectors": [selector]})
        assert best is not None
        assert best["locator"].get_attribute("role") == role


@pytest.mark.parametrize(
    "selector",
    ["a, .download", ":is(a,.download)", ":where(a,.download)"],
)
def test_mixed_selector_authority_stays_with_the_matching_positive_branch(
        selector):
    html = """<!doctype html><html><body>
      <div class="download">Original</div>
    </body></html>"""
    with _page(html) as page:
        best = find_best_download(
            page, learned={"row_selectors": [selector]})
        assert best is not None
        assert best["locator"].get_attribute("class") == "download"


def test_download_constrained_onclick_selector_keeps_reviewed_authority():
    html = """<!doctype html><html><body>
      <a onclick="startDownload()">Original</a>
    </body></html>"""
    with _page(html) as page:
        best = find_best_download(page, learned={
            "row_selectors": ['a[onclick*="Download"]'],
        })
        assert best is not None
        assert "Original" in best["text"]


def test_unscoped_menu_role_does_not_create_opaque_selector_authority():
    html = """<!doctype html><html><body>
      <li role="menuitem">Terms</li>
    </body></html>"""
    with _page(html) as page:
        best = find_best_download(
            page, learned={"row_selectors": ['[role="menuitem"]']})
        assert best is None


@pytest.mark.parametrize(
    "selector",
    [
        "body a",
        "main a",
        "a, button",
        ":is(a,button)",
        "a:not(.missing)",
        "a[target]",
        "[onclick]",
        "a, .download",
        ":is(a,.download)",
        ":where(a,.download)",
        "a:not(:is(.missing,.also-missing))",
        'a[href$=".css"]',
    ],
)
def test_intent_free_learned_selector_still_needs_media_evidence(selector):
    html = """<!doctype html><html><body><main>
      <a href="/assets/terms.css" target="_self"
         onclick="analytics.track('terms')">Terms</a>
      <button type="button">Account</button>
    </main></body></html>"""
    with _page(html) as page:
        best = find_best_download(
            page, learned={"row_selectors": [selector]})
        assert best is None, (
            f"intent-free selector {selector!r} returned "
            f"{(best or {}).get('text')!r}")


@pytest.mark.parametrize("learned", [None, {"row_selectors": ["a"]}],
                         ids=["wide", "broad-learned"])
def test_non_navigation_opaque_signed_resolution_control_is_kept(learned):
    """A resolution-only control is not chrome without navigation ancestry."""
    with _page(_OPAQUE_SIGNED_CONTROL) as page:
        best = find_best_download(page, learned=learned)
        assert best is not None, "opaque signed control was globally dropped"
        assert best["score"] == 3160
        assert best["locator"].get_attribute(
            "data-signed-url-key") == "opaque-asset-token"


@pytest.mark.parametrize("learned", [None, {"row_selectors": ["a"]}],
                         ids=["wide", "broad-learned"])
def test_boolean_download_authority_survives_navigation_context(learned):
    """Attribute presence is authority even when its HTML value is empty."""
    html = """<!doctype html><html><body><nav>
      <a class="quality-option" href="/opaque/quality/6K" download>6K</a>
    </nav></body></html>"""
    with _page(html) as page:
        best = find_best_download(page, learned=learned)
        assert best is not None
        assert best["locator"].get_attribute("download") == ""
        assert best["score"] == 3160


@pytest.mark.parametrize("learned", [None, {"row_selectors": ["a"]}],
                         ids=["wide", "broad-learned"])
def test_negative_control_scene_keeps_exact_real_tier_population(learned):
    with _page(_SCENE) as page:
        best = find_best_download(page, learned=learned)
        assert best is not None, "the four real tiers were all dropped"
        hrefs = _candidate_hrefs(best)
        media_hrefs = [href for href in hrefs if href.endswith(".mp4")]
        assert len(media_hrefs) == 4, media_hrefs
        assert all("/films-" not in href for href in hrefs), hrefs
        assert "7680x4320" in (
            best["locator"].get_attribute("href") or "")


def test_rendered_photo_gallery_is_the_only_confirmed_no_video_shape():
    """RED: current main has no conservative page-media state classifier."""
    with _page(_GALLERY) as page:
        snapshot = _media_snapshot(page)
    assert snapshot["ready_state"] == "complete"
    assert snapshot["affordance_count"] == 8
    assert snapshot["gallery_marker_count"] == 1
    assert snapshot["photo_count"] == 3
    assert snapshot["possible_media_count"] == 0
    assert snapshot["media_url_count"] == 0
    assert snapshot["pending_shell_count"] == 0
    assert candidate_filter.classify_page_media_snapshot(
        snapshot, GALLERY_URL) == candidate_filter.PAGE_MEDIA_CONFIRMED_ABSENT


@pytest.mark.parametrize(
    "extra,expected_possible,expected_media_urls",
    [
        ('<video src="blob:https://example.test/media-id"></video>', 1, 1),
        ('<iframe src="/embed/player"></iframe>', 1, 1),
        ('<div class="video-player-shell"></div>', 1, 0),
        ('<div id="video"></div>', 1, 0),
        ('<div class="video-container"></div>', 1, 0),
        ('<div id="videoplayer"></div>', 1, 0),
        ('<div class="playerContainer"></div>', 1, 0),
        ('<div class="plyr"></div>', 1, 0),
        ('<div data-plyr-provider="youtube"></div>', 1, 0),
        ('<div class="clappr"></div>', 1, 0),
        ('<div data-video="opaque-player-id"></div>', 1, 0),
    ],
    ids=[
        "blob-video",
        "iframe",
        "player-shell",
        "video-id",
        "video-container",
        "video-player-concatenated",
        "player-camel-case",
        "plyr-shell",
        "plyr-provider",
        "clappr-shell",
        "opaque-player-data",
    ],
)
def test_video_player_evidence_can_never_be_inferred_absent(
        extra, expected_possible, expected_media_urls):
    html = f"<!doctype html><html><body>{_NAV}{_PHOTO_GRID}{extra}</body></html>"
    with _page(html) as page:
        snapshot = _media_snapshot(page)
    assert snapshot["photo_count"] == 3
    assert snapshot["possible_media_count"] == expected_possible
    assert snapshot["media_url_count"] == expected_media_urls
    assert candidate_filter.classify_page_media_snapshot(
        snapshot, GALLERY_URL) == candidate_filter.PAGE_MEDIA_PRESENT


@pytest.mark.parametrize(
    "opaque_control",
    [
        '<a data-signed-url-key="opaque-token">Original</a>',
        '<a download>Original</a>',
        '<div data-download="">Original</div>',
    ],
    ids=["signed", "boolean-download", "opaque-data-download"],
)
def test_explicit_download_authority_blocks_a_no_video_claim(opaque_control):
    html = (
        f"<!doctype html><html><body>{_NAV}{_PHOTO_GRID}"
        f"{opaque_control}</body></html>"
    )
    with _page(html) as page:
        best = find_best_download(page, learned=None)
        snapshot = _media_snapshot(page)
    assert best is None
    assert snapshot["affordance_count"] == 9
    assert snapshot["photo_count"] == 3
    assert snapshot["possible_media_count"] == 1
    assert snapshot["media_url_count"] == 0
    assert candidate_filter.classify_page_media_snapshot(
        snapshot, GALLERY_URL) == candidate_filter.PAGE_MEDIA_PRESENT


def test_unrendered_javascript_shell_is_unknown_not_no_video():
    shell = f"""<!doctype html><html><body>{_NAV}{_PHOTO_GRID}
    <div id="root" data-reactroot></div>
    <script>window.__BOOTSTRAP_PENDING__ = true;</script>
    </body></html>"""
    with _page(shell) as page:
        snapshot = _media_snapshot(page)
    assert snapshot["affordance_count"] == 8
    assert snapshot["gallery_marker_count"] == 1
    assert snapshot["photo_count"] == 3
    assert snapshot["possible_media_count"] == 0
    assert snapshot["media_url_count"] == 0
    assert snapshot["pending_shell_count"] == 1
    assert candidate_filter.classify_page_media_snapshot(
        snapshot, GALLERY_URL) == candidate_filter.PAGE_MEDIA_UNKNOWN


@pytest.mark.parametrize(
    "root_attrs,pending_markup",
    [
        ('aria-busy="true"', ""),
        ("", '<div class="loading-skeleton"></div>'),
    ],
    ids=["aria-busy", "loading-skeleton"],
)
def test_populated_but_pending_javascript_shell_is_unknown(
        root_attrs, pending_markup):
    html = f"""<!doctype html><html><body>{_NAV}
    <div id="root" data-reactroot {root_attrs}>
      {_PHOTO_GRID}{pending_markup}
    </div></body></html>"""
    with _page(html) as page:
        snapshot = _media_snapshot(page)
    assert snapshot["affordance_count"] == 8
    assert snapshot["gallery_marker_count"] == 1
    assert snapshot["photo_count"] == 3
    assert snapshot["possible_media_count"] == 0
    assert snapshot["pending_shell_count"] == 1
    assert candidate_filter.classify_page_media_snapshot(
        snapshot, GALLERY_URL) == candidate_filter.PAGE_MEDIA_UNKNOWN


@pytest.mark.parametrize(
    "root_open,root_close",
    [
        ('<div id="app" aria-busy="true">', "</div>"),
        ('<app-root aria-busy="true">', "</app-root>"),
        ('<div id="__nuxt" class="loading">', "</div>"),
    ],
    ids=["app", "angular-app-root", "nuxt"],
)
def test_other_busy_application_roots_are_unknown(root_open, root_close):
    html = (
        f"<!doctype html><html><body>{_NAV}{root_open}"
        f"{_PHOTO_GRID}{root_close}</body></html>"
    )
    with _page(html) as page:
        snapshot = _media_snapshot(page)
    assert snapshot["gallery_marker_count"] == 1
    assert snapshot["photo_count"] == 3
    assert snapshot["possible_media_count"] == 0
    assert snapshot["pending_shell_count"] == 1
    assert candidate_filter.classify_page_media_snapshot(
        snapshot, GALLERY_URL) == candidate_filter.PAGE_MEDIA_UNKNOWN


@pytest.mark.parametrize(
    "noise",
    [
        '<div class="loading-message">Archived notice</div>',
        '<div class="not-loading">Already rendered</div>',
        '<img class="lazyloading" src="/img/rendered.jpg">',
    ],
    ids=["loading-message", "not-loading", "lazyloading-image"],
)
def test_non_pending_loading_substrings_do_not_hide_a_proven_gallery(noise):
    html = f"<!doctype html><html><body>{_NAV}{_PHOTO_GRID}{noise}</body></html>"
    with _page(html) as page:
        snapshot = _media_snapshot(page)
    assert snapshot["pending_shell_count"] == 0
    assert candidate_filter.classify_page_media_snapshot(
        snapshot, GALLERY_URL) == candidate_filter.PAGE_MEDIA_CONFIRMED_ABSENT


def test_confirmed_no_video_return_resets_stale_no_button_counter(monkeypatch):
    """RED: the historical early return left the site-global streak stale."""
    updates = []
    history = []

    class RunnerStub:
        site_id = "wowgirls"
        config = {"name": "Wow Girls"}
        _consec_no_btn = 4

        def _update_job(self, url, status, message, screenshot=None):
            updates.append((url, status, message, screenshot))

    runner = RunnerStub()
    monkeypatch.setattr(
        runner_module, "db_log", lambda *args: history.append(args))

    with _page(_GALLERY) as page:
        handled = runner_module._handle_confirmed_no_video_page(
            runner, page, GALLERY_URL, "gallery.png")

    assert handled is True
    assert runner._consec_no_btn == 0
    assert len(updates) == 1 and updates[0][1] == "needs_review"
    assert len(history) == 1 and history[0][3] == "needs_review"
    diagnostic = updates[0][2].lower()
    assert "no video on this page" in diagnostic
    assert "confirmed photo gallery" in diagnostic
    assert "scored ok but no download fired" not in diagnostic
    assert "did not fire" not in diagnostic


def test_stale_no_video_outcome_cannot_reset_or_log_for_a_new_run(
        monkeypatch, capsys):
    history = []

    class RunnerStub:
        site_id = "wowgirls"
        config = {"name": "Wow Girls"}
        _consec_no_btn = 2

        def _update_job(self, *args, **kwargs):
            return False

    runner = RunnerStub()
    monkeypatch.setattr(
        runner_module, "db_log", lambda *args: history.append(args))

    with _page(_GALLERY) as page:
        handled = runner_module._handle_confirmed_no_video_page(
            runner, page, GALLERY_URL, "gallery.png")

    assert handled is True
    assert runner._consec_no_btn == 2
    assert history == []
    assert capsys.readouterr().err == ""


def test_resume_after_no_button_pause_starts_a_fresh_streak(monkeypatch):
    """A resumed site must not re-pause after the very next isolated miss."""
    class RunnerStub:
        _state = "paused_no_button"
        _consec_no_btn = 5
        _pause = threading.Event()

    runner = RunnerStub()
    monkeypatch.setattr(
        runner_module._download_hold, "downloads_allowed",
        lambda: (True, {"state": "released", "reason": "test"}))

    runner_module.SiteRunner.resume(runner)

    assert runner._state == "running"
    assert runner._pause.is_set()
    assert runner._consec_no_btn == 0


def test_any_published_done_outcome_breaks_the_no_button_streak():
    """Extractor/library early successes publish ``done`` before returning."""
    published = []

    class RunnerStub:
        _consec_no_btn = 4

        def _worker_write_generation(self, explicit_generation):
            return None

        def _update_job_current(self, url, status, message, **extra):
            published.append((url, status, message, extra))

    runner = RunnerStub()
    runner_module.SiteRunner._update_job(
        runner, "https://example.test/scene", "done", "Saved")

    assert len(published) == 1 and published[0][1] == "done"
    assert runner._consec_no_btn == 0


def test_worker_published_done_outcome_breaks_the_no_button_streak():
    """The real worker path carries a run generation through publication."""
    published = []

    class RunnerStub:
        _consec_no_btn = 4
        _run_lifecycle_lock = threading.Lock()

        def _worker_write_generation(self, explicit_generation):
            return 7

        def _worker_generation_is_current(self, run_generation):
            return run_generation == 7

        def _update_job_current(self, url, status, message, **extra):
            published.append((url, status, message, extra))

    runner = RunnerStub()
    runner_module.SiteRunner._update_job(
        runner, "https://example.test/scene", "done", "Saved")

    assert len(published) == 1 and published[0][1] == "done"
    assert runner._consec_no_btn == 0


def test_stale_done_outcome_does_not_reset_the_new_run_streak():
    """Only an outcome actually published by the current run may reset it."""
    class RunnerStub:
        _consec_no_btn = 4
        _run_lifecycle_lock = threading.Lock()

        def _worker_write_generation(self, explicit_generation):
            return 6

        def _worker_generation_is_current(self, run_generation):
            return False

        def _update_job_current(self, *args, **kwargs):
            raise AssertionError("stale worker must not publish")

    runner = RunnerStub()
    result = runner_module.SiteRunner._update_job(
        runner, "https://example.test/scene", "done", "Saved")

    assert result is False
    assert runner._consec_no_btn == 4
