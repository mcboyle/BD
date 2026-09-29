"""O1567 fx-nubiles-wrong-media (nubiles-porn, test3big 2026-09-29 20:54Z).

Row https://members.nubiles-porn.com/video/watch/257407/bath-time-discipline-s27e2
saved ``momsteachsex_i_want_to_know_if_his_dick_is_crooked_1920.mp4`` -- a
DIFFERENT scene.  The public page for 257407 names its own media folder
``bath_time_discipline`` (``.../bath_time_discipline/videos/momsteachsex_
bath_time_discipline_trailer...``).  App log: "custom selector matched 43
option(s); picked '1920x1080 HD MP4 (2 GB) https://content2a...'" where a
normal scene page matches 17.

``find_best_download``'s custom (dl_selector) branch ranks every match by
res_score/size only: each candidate is stamped ``work: 0`` and never scoped,
so a 1080p option of another scene on the same page ties/outranks the
scene's own tiers.  The learned path and the wide sweep already stamp work
affinity and refuse UNKNOWN candidates when the page proves affinity
(``_scoped_candidates``); the custom branch now does the same.  It also
scored only the first 40 matches, fewer than the 43 measured.

Local inline fixture via ``page.route``; NO LIVE SITE IS TOUCHED.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://fixture-o1567-nubiles.test"
CDN = "https://content2a.fixture-o1567-nubiles.test/exclusive"
SCENE_URL = ORIGIN + "/video/watch/257407/bath-time-discipline-s27e2"
LATE_URL = ORIGIN + "/video/watch/257408/bath-time-discipline-s27e3"
SIGNED_URL = ORIGIN + "/video/watch/257409/signed-only-scene-s1e1"
FOREIGN_URL = ORIGIN + "/video/watch/257410/bath-time-discipline-s27e4"
WEAK_URL = ORIGIN + "/video/watch/256651/my-stepsis-is-a-hot-mess"
SEL = "a.dl-opt"

_TIERS = (("3840x2160 4K MP4 (6 GB)", "3840"),
          ("1920x1080 HD MP4 (2 GB)", "1920"),
          ("1280x720 HD MP4 (1 GB)", "1280"),
          ("960x540 SD MP4 (500 MB)", "960"))


def _opts(folder, prefix, tiers):
    return "\n".join(
        '<a class="dl-opt" href="%s/%s/videos/%s_%s_%s.mp4">%s</a>'
        % (CDN, folder, prefix, folder, suffix, label, )
        for label, suffix in tiers)


def _page(own, foreign_before, foreign_count=1):
    foreign = "\n".join(
        _opts("i_want_to_know_if_his_dick_is_crooked", "momsteachsex",
              _TIERS[1:2]) for _ in range(foreign_count))
    body = (foreign + "\n" + own) if foreign_before else (own + "\n" + foreign)
    return ("<!doctype html><html><body><h1>Bath Time Discipline</h1>"
            "<div class='dropdown-menu'>%s</div></body></html>" % body)


# The scene's own tiers stop at 1080p; the other scene's option is 1080p too
# and comes FIRST in document order (a tie the old sort resolved by order).
SCENE_HTML = _page(_opts("bath_time_discipline", "momsteachsex",
                         _TIERS[1:]), foreign_before=True)
# 168 foreign options precede the scene's own three: 171 matches, the
# largest count measured live (test3big), past the old 40 cap.
LATE_HTML = _page(_opts("bath_time_discipline", "momsteachsex",
                        _TIERS[1:]), foreign_before=True, foreign_count=168)
# Negative control: nothing carries an identity (signed CDN paths) -> the
# UNKNOWN fallback still ranks by tier, marked _no_identity_proof.
SIGNED_HTML = ("<!doctype html><html><body><div>%s</div></body></html>"
               % "\n".join('<a class="dl-opt" href="%s/x/%s.mp4?st=q">%s</a>'
                           % (CDN, s, label) for label, s in _TIERS))

# Only another scene's options, each linking back to that scene's own watch
# route (same route, another id): positively FOREIGN.
FOREIGN_HTML = ("<!doctype html><html><body><div>%s</div></body></html>"
                % "\n".join('<a class="dl-opt" href="/video/watch/257400/'
                            'i-want-to-know-s27e1" data-url="%s/crooked/%s.mp4">'
                            '%s</a>' % (CDN, s, label) for label, s in _TIERS))

# Live 256651 (test3big 22:3xZ, patched build): a related scene's folder
# `stepsis_is_so_tiny` shares the run ('stepsis','is') with the page slug --
# 2 tokens, 9 chars, over the bar -- so it was IN SCOPE beside the page's own
# `my_stepsis_is_a_hot_mess` and won on size.
WEAK_HTML = ("<!doctype html><html><body><div>%s\n%s</div></body></html>"
             % (_opts("stepsis_is_so_tiny", "stepsiblingscaught",
                      (("1920x1080 HD MP4 (2 GB)", "1920"),)),
                _opts("my_stepsis_is_a_hot_mess", "stepsiblingscaught",
                      (("1920x1080 HD MP4 (1 GB)", "1920"),
                       ("1280x720 HD MP4 (600 MB)", "1280")))))

_PAGES = {SCENE_URL: SCENE_HTML, LATE_URL: LATE_HTML, SIGNED_URL: SIGNED_HTML,
          FOREIGN_URL: FOREIGN_HTML, WEAK_URL: WEAK_HTML}
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


def _serve(route, request):
    route.fulfill(status=200, content_type="text/html",
                  body=_PAGES[request.url.split("?", 1)[0]])


@contextmanager
def _scene_page(url):
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


def _runner(quality_preference="1080,720"):
    from bulk_downloader.runner_integrity import IntegrityMixin

    class _Runner(IntegrityMixin):
        config = {"name": "nubiles-porn", "dl_selector": SEL,
                  "quality_preference": quality_preference,
                  "min_resolution": 0}

        def log_event(self, *a, **k):
            return None

    return _Runner()


def _href(best):
    return best["locator"].get_attribute("href") or ""


def test_other_scenes_1080p_option_is_not_picked(capsys):
    """THE ROW: the scene's own 1080p wins, not the other scene's."""
    from bulk_downloader.detect import find_best_download
    with _scene_page(SCENE_URL) as page:
        best = find_best_download(page, SEL, runner=_runner())
        assert best, "custom selector matched nothing on the fixture"
        assert "bath_time_discipline" in _href(best), (
            "WRONG-MEDIA: custom branch picked another scene's option: %s"
            % _href(best))
        assert best["score"] == 1080, best["text"]
        assert all("crooked" not in c["text"]
                   for c in best["_all_candidates"]), best["_all_candidates"]
        assert any("crooked" in c["text"]
                   for c in best["_excluded_candidates"])
        assert not best.get("_no_identity_proof")
        err = capsys.readouterr().err
        assert ("custom selector matched 4 option(s)" in err
                and "excluded 1 not this page's work (0 foreign, 1 unproven, "
                "0 weaker match)"
                in err), err


def test_own_options_past_the_40th_match_are_still_ranked():
    from bulk_downloader.detect import find_best_download
    with _scene_page(LATE_URL) as page:
        assert page.locator(SEL).count() == 171
        best = find_best_download(page, SEL, runner=_runner())
        assert best and "bath_time_discipline" in _href(best), _href(best)


def test_no_identity_anywhere_keeps_tier_ranking_marked():
    """Negative control: an unprovable page is not refused (row 388)."""
    from bulk_downloader.detect import find_best_download
    with _scene_page(SIGNED_URL) as page:
        best = find_best_download(page, SEL, runner=_runner(""))
        assert best and best["score"] == 2160, best and best["text"]
        assert len(best["_all_candidates"]) == 4
        assert best.get("_no_identity_proof") is True


def test_all_foreign_matches_do_not_fall_back_to_first():
    """Every tiered option names another scene: `.first` (9999) is refused."""
    from bulk_downloader.detect import find_best_download
    with _scene_page(FOREIGN_URL) as page:
        best = find_best_download(page, SEL, runner=_runner(""))
        assert not (best and "257400" in _href(best)), _href(best)


def test_weaker_slug_match_loses_to_the_pages_own_folder():
    from bulk_downloader.detect import find_best_download
    with _scene_page(WEAK_URL) as page:
        best = find_best_download(page, SEL, runner=_runner())
        assert best and "my_stepsis_is_a_hot_mess" in _href(best), (
            "WRONG-MEDIA: weaker slug match picked: %s" % _href(best))
        assert any("stepsis_is_so_tiny" in c["text"]
                   for c in best["_excluded_candidates"])
