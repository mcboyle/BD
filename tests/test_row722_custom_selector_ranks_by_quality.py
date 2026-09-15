"""Row 722 G30 (vip4k, test2 2026-09-15, logged in): the site config carries
``dl_selector = a.download__item`` and the scene page renders FIVE such
anchors (320p .. 4K, every href ``javascript:void(0)``), with
``quality_preference = "1080,720"`` and ``min_resolution = 1080``.

The dry-run inspector named 1080p; the runner saved 360p.mp4 --
"INSPECTOR-TIER-DIVERGENCE".  ``find_best_download``'s custom branch took
``page.locator(custom).first`` at score 9999, so neither the quality
preference nor the min-resolution gate was ever consulted: 9999 satisfies
every gate, and ``_all_candidates`` held a single synthetic row.

Now a selector that matches several elements is ranked like the wide sweep
ranks its own candidates (res_score / size), the runner's own
``_apply_quality_preference`` is applied over the REAL tiers, and the
returned score is the picked tier so the existing below-min refusal in
runner._process_one fires when nothing meets ``min_resolution``.  A single
match keeps today's behaviour.

The page is a local inline fixture served by ``page.route``.  NO LIVE SITE IS
TOUCHED; a missing browser SKIPS.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://fixture-722-custom.test"
SCENE_URL = ORIGIN + "/video/1234"
SINGLE_URL = ORIGIN + "/video/1235"
LOW_URL = ORIGIN + "/video/1236"
UNTIERED_URL = ORIGIN + "/video/1237"

_ITEM = ('<a class="download__item" href="javascript:void(0)" '
         'data-quality="%s">%s</a>')

SCENE_HTML = """<!doctype html><html><head><title>Scene 1234 | VIP</title>
</head><body><h1>Scene 1234</h1><div class="download">
%s
</div></body></html>""" % "\n".join(
    _ITEM % (q, q) for q in ("320p", "480p", "720p", "1080p", "4K"))

SINGLE_HTML = """<!doctype html><html><head><title>Scene 1235</title>
</head><body><h1>Scene 1235</h1>
<a class="download__item" href="/video/1235/download">Download</a>
</body></html>"""

LOW_HTML = """<!doctype html><html><head><title>Scene 1236</title>
</head><body><h1>Scene 1236</h1><div class="download">
%s
</div></body></html>""" % "\n".join(
    _ITEM % (q, q) for q in ("320p", "480p", "720p"))

UNTIERED_HTML = """<!doctype html><html><head><title>Scene 1237</title>
</head><body><h1>Scene 1237</h1>
<a class="download__item" href="/video/1237/a">Part one</a>
<a class="download__item" href="/video/1237/b">Part two</a>
</body></html>"""

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


_PAGES = {SCENE_URL: SCENE_HTML, SINGLE_URL: SINGLE_HTML,
          LOW_URL: LOW_HTML, UNTIERED_URL: UNTIERED_HTML}


def _serve(route, request):
    path = request.url.split("?", 1)[0]
    route.fulfill(status=200, content_type="text/html",
                  body=_PAGES.get(path, SCENE_HTML))


@contextmanager
def _scene_page(url=SCENE_URL):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": 1000, "height": 760})
            page.route(ORIGIN + "/**", _serve)
            page.goto(url, wait_until="load")
            yield page
        finally:
            browser.close()


def _runner(quality_preference="", min_resolution=0):
    """The runner handle find_best_download receives: config + the Phase 67
    preference method, exactly as SiteRunner exposes them."""
    from bulk_downloader.runner_integrity import IntegrityMixin

    class _Runner(IntegrityMixin):
        config = {"name": "fixture", "dl_selector": "a.download__item",
                  "quality_preference": quality_preference,
                  "min_resolution": min_resolution}

        def log_event(self, *a, **k):
            return None

    return _Runner()


def _below_min_refusal_fires(best, min_res):
    """The runner._process_one gate, verbatim: refuses when the winner's
    score is a real tier under min_resolution."""
    return bool(min_res > 0 and best["score"] > 0 and best["score"] < min_res)


def test_five_matches_with_preference_1080_720_pick_1080(capsys):
    """THE ROW: quality_preference '1080,720' over five tiers -> 1080p."""
    from bulk_downloader.detect import find_best_download
    with _scene_page() as page:
        best = find_best_download(page, "a.download__item",
                                  runner=_runner("1080,720", 1080))
        assert best, "custom selector matched nothing on the fixture"
        assert best["score"] != 9999, (
            "INSPECTOR-TIER-DIVERGENCE: the custom branch returned the "
            "synthetic 9999 score, i.e. `.first` with no ranking: %r"
            % ({k: v for k, v in best.items() if k != "locator"},))
        assert best["score"] == 1080, best["text"]
        assert best["locator"].get_attribute("data-quality") == "1080p"
        assert len(best["_all_candidates"]) == 5
        assert sorted(c["score"] for c in best["_all_candidates"]) == [
            320, 480, 720, 1080, 2160]
        assert not _below_min_refusal_fires(best, 1080)
        err = capsys.readouterr().err
        assert ("download: custom selector matched 5 option(s); picked "
                "'1080p" in err and "(1080p)" in err), err


def test_no_preference_picks_the_highest_tier_not_the_first():
    from bulk_downloader.detect import find_best_download
    with _scene_page() as page:
        best = find_best_download(page, "a.download__item", runner=_runner())
        assert best["score"] == 2160, best["text"]
        assert best["locator"].get_attribute("data-quality") == "4K"


def test_no_runner_handle_still_ranks_by_tier():
    """auto_detect / manual callers pass no runner: rank on the tiers alone."""
    from bulk_downloader.detect import find_best_download
    with _scene_page() as page:
        best = find_best_download(page, "a.download__item")
        assert best["score"] == 2160, best["text"]


def test_nothing_meets_min_resolution_reaches_the_refusal_path():
    """320/480/720 only, min 1080: the score is the real best tier (720), so
    the runner's existing below-min refusal fires instead of a silent 320p."""
    from bulk_downloader.detect import find_best_download
    with _scene_page(LOW_URL) as page:
        best = find_best_download(page, "a.download__item",
                                  runner=_runner("1080,720", 1080))
        assert best["score"] == 720, best["text"]
        assert _below_min_refusal_fires(best, 1080), (
            "the runner's min_resolution gate would not fire: %r"
            % (best["score"],))
        assert best["locator"].get_attribute("data-quality") != "320p"


def test_single_match_keeps_the_9999_custom_contract():
    """Negative control: one match is the operator's explicit choice."""
    from bulk_downloader.detect import find_best_download
    with _scene_page(SINGLE_URL) as page:
        best = find_best_download(page, "a.download__item",
                                  runner=_runner("1080,720", 1080))
        assert best["score"] == 9999, best
        assert best["text"] == "a.download__item"
        assert best["_all_candidates"] == [
            {"text": "custom: a.download__item", "score": 9999, "size": 0}]


def test_untiered_multi_match_keeps_first():
    """Negative control: several matches but no tier anywhere -> nothing to
    rank on; today's `.first` behaviour is kept."""
    from bulk_downloader.detect import find_best_download
    with _scene_page(UNTIERED_URL) as page:
        best = find_best_download(page, "a.download__item", runner=_runner())
        assert best["score"] == 9999, best
        assert best["locator"].get_attribute("href").endswith("/a")
