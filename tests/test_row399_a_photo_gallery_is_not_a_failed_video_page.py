"""A photo gallery is not a failed video page.

BD_GATE_SCOPE is a module-level ASSIGNMENT below, not a docstring line -- the
classifier in tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py parses the
assignment, and a docstring marker leaves the file undeclared.

MEASURED on test6 2026-08-29 (v3.66.1348, history row 125), the FIRST download
after the row-388 hold lifted:

    url     https://venus.wowgirls.com/gallery/x15b9ab4/stunned-by-each-other
    status  needs_review   filename ""   file_size 0
    message no dl event; scored ok but no download fired; saw: 6K(?):6K /films-6K/

bd-shoot against that live page reports ANCHORS 136  AFFORDANCES 143  MEDIA
AFFORDANCES 0 -- there is NO video on the page at all; /gallery/ is a PHOTO set.
The only candidate scored was the site-navigation link ``/films-6K/`` -- a menu
item, not a download -- and its size parsed unknown (the ``(?)``). ``res_score``
read "6K" out of a URL SEGMENT in the site chrome. Row 381 stopped photo PIXEL
DIMENSIONS being read as a video resolution; this is the sibling shape, a named
tier inside a navigation href.

Two defects, one feature:

  1. a candidate whose size is unknown AND whose href is site chrome (no media /
     manifest / download / api URL signal, no download word) must not reach the
     ranker -- so a photo gallery yields ZERO candidates, not one 6K ghost that
     outranks nothing at all (rows exercising ``find_best_download`` below);

  2. a page whose affordances carry ZERO media must report a DISTINCT state --
     no-video-on-page -- separate from "a control existed and did not fire", and
     the message must name which of the two it is (the pure-classifier and
     message rows below).

The negative control is a real wowgirls SCENE page -- which carries the SAME
``/films-*`` nav -- and it must still rank its tiers exactly as it does today.
"""

BD_GATE_SCOPE = "module"

from contextlib import contextmanager

import pytest

from bulk_downloader.detect import find_best_download, res_score, parse_size_bytes

# ── the measured page: a photo gallery. Site-chrome nav carrying resolution
# TIER labels (/films-4K/../films-6K/ -- wowgirls categorises films by tier),
# plus a photo grid. There is no <video>, no .mp4, no manifest anywhere. ──
_GALLERY = """<!doctype html><html><body>
<nav class="site-header">
  <a href="/films-4K/">4K</a>
  <a href="/films-5K/">5K</a>
  <a href="/films-6K/">6K</a>
  <a href="/models/">Models</a>
  <a href="/">Home</a>
</nav>
<main class="gallery">
  <h1>Stunned By Each Other</h1>
  <a href="/gallery/x15b9ab4/photo/1"><img src="/img/a.jpg" alt="still one"></a>
  <a href="/gallery/x15b9ab4/photo/2"><img src="/img/b.jpg" alt="still two"></a>
  <a href="/gallery/x15b9ab4/photo/3"><img src="/img/c.jpg" alt="still three"></a>
</main>
</body></html>
"""

# ── the negative control: a real wowgirls SCENE page. Row 380's load-bearing
# shape (leaf anchors whose href IS the media file, size in a SIBLING caption),
# PLUS the identical /films-* nav the gallery carried. Removing the nav ghost
# must not perturb the tier ranking. ──
_SCENE = """<!doctype html><html><body>
<nav class="site-header">
  <a href="/films-4K/">4K</a>
  <a href="/films-5K/">5K</a>
  <a href="/films-6K/">6K</a>
  <a href="/models/">Models</a>
  <a href="/">Home</a>
</nav>
<div class="content_download video_downloads">
  <div class="ct_dl_title">FULL MOVIE DOWNLOAD</div>
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
</body></html>
"""


@contextmanager
def _page(html):
    sync_playwright = pytest.importorskip(
        "playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch()
        try:
            pg = br.new_page(viewport={"width": 1400, "height": 900})
            pg.set_content(html, wait_until="load")
            yield pg
        finally:
            br.close()


# ── preconditions: prove the fixture built the exact defect shape ────────────

def test_precondition_the_gallery_has_no_video_media_at_all():
    """If the gallery secretly carried a video, a green 'no candidates' would be
    correct for the wrong reason. Prove the page is a pure photo set."""
    with _page(_GALLERY) as pg:
        assert pg.locator("video, source").count() == 0
        assert pg.locator("a[href*='.mp4'], a[href*='.m3u8']").count() == 0
        # the chrome ghost really is present and reachable by the ancestor
        # walk's text-matches selector (the path that harvested it live).
        assert pg.locator("a[href='/films-6K/']").count() == 1
        assert pg.locator(
            ":text-matches('\\\\b[24568]K\\\\b','i')").count() >= 1


def test_precondition_the_6K_chrome_link_scores_like_a_video_tier_today():
    """The defect's arithmetic, pinned. ``res_score`` reads 6K out of the menu
    label / path segment and gives it a real 6K height, while its size parses
    unknown -- the (score>0, size==0) shape that reached the ranker live."""
    ghost = "6K /films-6K/"
    assert res_score(ghost) == 3160, (
        "the 6K nav label no longer scores as a video tier; this row's premise "
        "has changed and the assertions below prove nothing")
    assert parse_size_bytes(ghost) == 0, "the chrome link must parse no size"


# ── RED on the current tree: the gallery yields the 6K ghost, not None ───────

def test_the_photo_gallery_yields_zero_download_candidates():
    """RED on v3.66.1348: ``find_best_download`` returns the ``/films-6K/`` ghost
    (score 3160, size 0). A photo page with zero media must yield NOTHING, so the
    chain declines with 'no video' rather than clicking a menu item that fires no
    download event."""
    with _page(_GALLERY) as pg:
        best = find_best_download(pg, "", learned=None, runner=None)
        assert best is None, (
            "a photo gallery produced a download candidate: "
            "score=%r size=%r text=%r -- the 6K site-chrome ghost still reaches "
            "the ranker" % (
                (best or {}).get("score"), (best or {}).get("size"),
                (best or {}).get("text")))


# ── negative control: the real scene still ranks its tiers as it does today ──

def test_negative_control_scene_winner_and_tier_order_are_unchanged():
    """The scene page carries the identical /films-* nav. Excluding the chrome
    ghost must leave the winner (8K leaf) and the real-tier ordering intact, and
    the ghost must be absent from the candidate list."""
    with _page(_SCENE) as pg:
        best = find_best_download(pg, "", learned=None, runner=None)
        assert best is not None, "the scene's real tiers were all dropped"
        href = best["locator"].get_attribute("href") or ""
        assert "7680x4320" in href, (
            "winner is not the 8K leaf: href=%r text=%r" % (href, best["text"]))

        cands = best.get("_all_candidates") or []
        # the chrome ghost must not appear among the harvested candidates.
        for c in cands:
            loc = c.get("locator")
            chref = (loc.get_attribute("href") or "") if loc is not None else ""
            assert "/films-6K/" not in chref and "/films-5K/" not in chref \
                and "/films-4K/" not in chref, (
                "a /films-* chrome link is still a candidate: %r" % c.get("text"))

        # the four real tiers, in strict descending height, survive intact.
        tier_scores = []
        for c in cands:
            loc = c.get("locator")
            chref = (loc.get_attribute("href") or "") if loc is not None else ""
            if ".mp4" in chref:
                tier_scores.append(c["score"])
        assert tier_scores == sorted(tier_scores, reverse=True), (
            "the real tiers are no longer in descending order: %r" % tier_scores)
        assert len(tier_scores) == 4, (
            "expected all four real .mp4 tiers, got %d: %r" % (
                len(tier_scores), tier_scores))
        # 8K (4320 + 5 for 60fps) is strictly the top, and it beats the 6K ghost
        # height (3160) that used to sit at #2.
        assert max(tier_scores) == 4325 and tier_scores[0] == 4325


# ── PART 1 pure predicate: which candidates are site-chrome ghosts ───────────

from bulk_downloader.detect import is_chrome_resolution_ghost
from bulk_downloader import candidate_filter as cf
from bulk_downloader.runner import _AFFORDANCE_SCAN_JS, _AFFORDANCE_ATTRS


def test_the_films_6K_nav_link_is_a_chrome_ghost():
    """The measured case: size unknown, no download word, href carries no media
    signal, score comes only from the 6K label in the path segment."""
    assert is_chrome_resolution_ghost("6K /films-6K/", "/films-6K/") is True


# (text, url, why it must be KEPT) -- every real download shape.
_KEEP = [
    # the wowgirls data-signed-url-key tier: its VALUE contains "download",
    # so the download-word guard keeps it (measured: dl_re matches "downloads").
    ("6K downloads.6K", "downloads.6K", "signed-url-key value has 'download'"),
    # a leaf whose href IS the media file (row 380 shape).
    ("7680 x 4320 https://cdn.example/dl/7680x4320_60FPS.mp4",
     "https://cdn.example/dl/7680x4320_60FPS.mp4", "media extension"),
    # a resolution label WITH a parseable size is real evidence.
    ("6K 1.99GB", "/films-6K/", "a byte size is real evidence"),
    # a manifest URL is media even with no size and no download word.
    ("1080p /hls/scene/master.m3u8", "/hls/scene/master.m3u8", "manifest url"),
    # a bare 'Download' button with no quality info (score 0, dl word present).
    ("Download", "/get", "download word + download path"),
]


@pytest.mark.parametrize("text,url,why", _KEEP)
def test_a_real_download_shape_is_never_a_chrome_ghost(text, url, why):
    assert is_chrome_resolution_ghost(text, url) is False, (
        "%r (%s) was misclassified as a chrome ghost" % (text, why))


def test_negative_control_a_url_less_resolution_trigger_is_kept():
    """A bare '6K' trigger button whose modal fires on click has no chrome href
    to judge. The has-URL gate is load-bearing: dropping it would delete a
    possible real control (find_best_download would return None on a real video
    page). It must be KEPT."""
    assert is_chrome_resolution_ghost("6K", "") is False
    assert is_chrome_resolution_ghost("6K", None) is False


# ── PART 2 pure classifier: which affordances are media ──────────────────────

# (value, text, is_media, why)
_MEDIA_TABLE = [
    ("https://cdn.example/x.mp4", "", True, "video file"),
    ("https://cdn.example/x.mp4?st=1&e=2", "", True, "signed video file"),
    ("/hls/scene/master.m3u8", "1080p", True, "manifest"),
    ("/api/media/source?id=1", "", True, "media api"),
    ("", "1920x1080", True, "video WxH"),
    ("/films-6K/", "6K", False, "nav chrome tier label"),
    ("/img/a.jpg", "still one", False, "photo image"),
    ("/", "Home", False, "homepage nav"),
    ("/img/big.jpg", "Large 6000x4000px", False, "photo PIXEL dimension (row381)"),
]


@pytest.mark.parametrize("value,text,expect,why", _MEDIA_TABLE)
def test_is_media_affordance(value, text, expect, why):
    assert cf.is_media_affordance(value, text) is expect, (
        "is_media_affordance(%r,%r) != %r (%s)" % (value, text, expect, why))


def test_page_reports_no_video_needs_affordances_but_zero_media():
    gallery = [
        {"val": "/films-6K/", "txt": "6K"},
        {"val": "/models/", "txt": "Models"},
        {"val": "/img/a.jpg", "txt": "still one"},
        {"val": "/gallery/x/photo/1", "txt": "photo"},
    ]
    total, media = cf.classify_page_affordances(gallery)
    assert total == 4 and media == 0, (total, media)
    assert cf.page_reports_no_video(gallery) is True


def test_page_reports_no_video_is_false_when_any_media_is_present():
    scene = [
        {"val": "/films-6K/", "txt": "6K"},
        {"val": "https://cdn.example/dl/7680x4320_60FPS.mp4", "txt": "7680 x 4320"},
    ]
    total, media = cf.classify_page_affordances(scene)
    assert total == 2 and media == 1, (total, media)
    assert cf.page_reports_no_video(scene) is False


def test_negative_control_zero_affordances_is_UNKNOWN_not_no_video():
    """A page that rendered nothing / is not authenticated has ZERO affordances
    of any kind. That is UNKNOWN, never a 'no video on page' claim (A7). This is
    the negative control: it must FAIL to produce the no-video state, and for the
    intended reason -- total == 0, not media == 0."""
    total, media = cf.classify_page_affordances([])
    assert total == 0, "the empty page must have zero total affordances"
    assert cf.page_reports_no_video([]) is False
    assert cf.page_reports_no_video(None) is False


# ── end-to-end: the runner's actual scan JS over the real fixtures ───────────

def test_the_scan_js_reads_the_gallery_as_no_video():
    """The production _AFFORDANCE_SCAN_JS + classifier, run against the gallery
    fixture the runner would see: affordances present, none media."""
    with _page(_GALLERY) as pg:
        rows = pg.evaluate(_AFFORDANCE_SCAN_JS, list(_AFFORDANCE_ATTRS))
        total, media = cf.classify_page_affordances(rows)
        assert total > 0, "the gallery must expose affordances (nav, photos)"
        assert media == 0, (
            "the photo gallery must carry ZERO media affordances; got %d: %r"
            % (media, [r for r in rows if cf.is_media_affordance(
                r.get("val", ""), r.get("txt", ""))]))
        assert cf.page_reports_no_video(rows) is True


def test_the_scan_js_reads_the_scene_as_having_media():
    """The same scan over the scene fixture must find its .mp4 tiers, so the
    scene never reports no-video (it also has a winning candidate and never
    reaches this branch, but the classifier must still not misread it)."""
    with _page(_SCENE) as pg:
        rows = pg.evaluate(_AFFORDANCE_SCAN_JS, list(_AFFORDANCE_ATTRS))
        total, media = cf.classify_page_affordances(rows)
        assert total > 0 and media >= 4, (
            "expected the four .mp4 tiers as media affordances; got %d of %d"
            % (media, total))
        assert cf.page_reports_no_video(rows) is False


# ── the two diagnostics must NAME which state they describe ──────────────────

# The transport timeout path's distinctive hint for "a control existed and did
# not fire" -- asserted verbatim so a reword there is caught here too.
_CONTROL_FIRED_HINT = "scored ok but no download fired"


def test_the_no_video_message_names_its_own_state():
    msg = cf.NO_MEDIA_AFFORDANCE_MESSAGE.lower()
    assert "no video on this page" in msg, (
        "the no-video message must name the no-video state")
    assert "media affordance" in msg


def test_the_two_diagnostics_are_distinct_in_both_directions():
    """A page with zero media (no-video) and a control that fired no download
    must NEVER share a diagnostic -- the operator must be able to tell which of
    the two it is from the message alone."""
    no_video = cf.NO_MEDIA_AFFORDANCE_MESSAGE.lower()
    # the no-video message must not claim a control fired-and-failed.
    assert _CONTROL_FIRED_HINT not in no_video
    assert "scored ok" not in no_video and "did not fire" not in no_video
    # and the control-fired hint must not claim there is no video.
    assert "no video on this page" not in _CONTROL_FIRED_HINT
    assert "media affordance" not in _CONTROL_FIRED_HINT


def test_the_control_fired_hint_still_lives_in_the_transport_path():
    """Guard the cross-check above: if the transport reword drops this hint, the
    both-directions test would pass vacuously. Prove the string is really the
    one the timeout path emits."""
    import bulk_downloader.runner_transport as rt
    import inspect
    src = inspect.getsource(rt)
    assert _CONTROL_FIRED_HINT in src, (
        "the transport timeout hint changed; update _CONTROL_FIRED_HINT and "
        "re-verify the two diagnostics are still distinct")
