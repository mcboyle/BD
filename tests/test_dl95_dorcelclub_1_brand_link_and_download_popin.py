"""dl95-dorcelclub-1 (HIGH; O1513 live run 2026-09-28 23:38Z, test2 v3.66.1709):
both dorcelclub scenes ended needs_review

    below 1080p; got 720p; saw: 720p(?):Dorcel Club : HD porn videos s
                                | auto(?):DOWNLOAD THE VIDEO #download

Two defects, both reproduced on origin/main 717b3cab against the saved public
scene HTML (harness-work/FIX/dl95-dorcelclub-1-bd-worker-B13-B/
MEASURE-public-page-base.txt):

1. RANKER: the brand logo ``<a href="/en/" class="logo" title="Dorcel Club :
   HD porn videos streaming and download by Dorcel">`` scored 720p from the
   word "HD" in its title. "/en/" is the site's (locale) homepage, but the
   homepage rejection only knew "/", so the logo was admitted and won.
2. REVEAL: the real control is ``<a href="#download" class="btn-dl"
   data-pop-in>Download the video</a>``; its click opens a pop-in whose rows
   are href-less ``div.filter[data-quality][data-slug=<file>.mp4]``. The G20
   reveal opener only accepted a bare BUTTON with no href, and its option CSS
   never looked at ``[data-quality]`` rows, so the pop-in was never expanded.

Local headless chromium, inline fixtures served by ``page.route``. NO LIVE
SITE IS TOUCHED.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://fixture-dl95-dorcelclub.test"
SCENE_PATH = "/en/scene/892090/the-temple-of-desire"
SCENE_URL = ORIGIN + SCENE_PATH
CONTROL_URL = ORIGIN + "/en/scene/892091/control-real-hd-link"
SECTION_URL = ORIGIN + "/en/scene/892092/fragment-without-popin"
OFFSITE_URL = ORIGIN + "/en/scene/892093/download-link-to-a-page"
SLUG = ORIGIN + "/dl/scene/892090/the-temple-of-desire/full/%s.mp4?lang=en"
BRAND_TITLE = "Dorcel Club : HD porn videos streaming and download by Dorcel"

_HEADER = """
<div class="main"><div class="inner">
  <div class="logo">
    <div class="open-nav"></div>
    <a href="/en/" class="logo no-redirect" title="%s"><img src="/pics/logo.png" alt="%s" height="40"></a>
  </div>
  <div class="navigation header"><div class="items">
    <a href="/en/news-videos-x-marc-dorcel" class="active">Videos</a>
    <a href="/en/pornstars">Models</a>
  </div></div>
</div></div>
""" % (BRAND_TITLE, BRAND_TITLE)

# The pop-in is built by the trigger's click handler (the live saw-list shows
# no quality row before the click). A row click only SELECTS; the pop-in's own
# button "consumes the pick" (row 722s) -- the file URL sits in data-slug.
_POPIN_JS = """
<script>
var TIERS = [["2160", "MP4 - 4K 2160p (3.4 GB)"], ["1080", "MP4 - Full HD 1080p (921.4 MB)"],
             ["720", "MP4 - HD 720p (512.0 MB)"], ["480", "MP4 - SD 480p (240.2 MB)"]];
document.addEventListener('click', function (ev) {
  var t = ev.target.closest('a.btn-dl');
  if (t) {
    ev.preventDefault();
    var pop = document.getElementById('download');
    if (pop && !pop.querySelector('.qualities')) {
      var q = document.createElement('div');
      q.className = 'qualities';
      TIERS.forEach(function (tier) {
        var f = document.createElement('div');
        f.className = 'filter';
        f.setAttribute('data-slug', '%s'.replace('%%s', tier[0]));
        f.setAttribute('data-lang', 'en');
        f.setAttribute('data-quality', tier[0]);
        f.textContent = tier[1];
        q.appendChild(f);
      });
      pop.appendChild(q);
      var b = document.createElement('button');
      b.type = 'button'; b.className = 'btn-action'; b.textContent = 'Download';
      pop.appendChild(b);
    }
    if (pop) pop.style.display = 'block';
    return;
  }
  var f = ev.target.closest('div.filter, button.btn-action');
  if (f) {
    window.__bd_test_clicked = (window.__bd_test_clicked || []).concat([f.textContent.trim()]);
  }
});
</script>
<style>a.btn-dl{text-transform:uppercase} #download{display:none}</style>
""" % SLUG

SCENE_HTML = """<!doctype html><html><head><title>The temple of desire</title>
%s</head><body>%s
<div class="content">
  <h1 class="title">The temple of desire</h1>
  <div class="content-text"><p>Behind closed doors, they discover a mesmerizing orgy.</p></div>
  <div class="content-more">
    <a href="#more" class="btn-more-description">See more</a>
    <a href="#less" class="btn-less-description">See less</a>
  </div>
  <a href="#download" class="btn-dl" data-pop-in="">Download the video <div class="icon"></div></a>
</div>
<div id="download" class="pop-in"></div>
</body></html>""" % (_POPIN_JS, _HEADER)

# Positive control (ranker): a REAL rendition link labelled only "HD" beside
# the same brand logo is still a scored candidate -- the fix rejects the locale
# ROOT, not the word "HD".
CONTROL_HTML = """<!doctype html><html><head><title>Control</title></head><body>%s
<h1>Control</h1>
<a href="/en/files/scene_892091_hd.mp4" class="file">Download HD</a>
</body></html>""" % _HEADER

# Negative control (reveal): a fragment trigger whose click reveals nothing
# download-like (GEN 3: the target is hidden, so it IS a reveal trigger; a
# VISIBLE target is a jump link and is never pre-clicked at all).
SECTION_HTML = """<!doctype html><html><head><title>Section</title></head><body>
<h1>Section</h1>
<a href="#download" class="btn-dl">Download the video</a>
<div id="download" style="display:none"><p>Members only.</p></div>
<script>document.querySelector('a.btn-dl').addEventListener('click', function (e) {
  e.preventDefault(); document.getElementById('download').style.display = 'block'; });</script>
</body></html>"""

# Negative control (reveal): the same label on a NAVIGATING href is not a
# reveal trigger -- the widening is for in-page fragments only.
OFFSITE_HTML = """<!doctype html><html><head><title>Offsite</title></head><body>
<h1>Offsite</h1>
<a href="/en/presignup" class="btn-dl">Download the video</a>
</body></html>"""

# GEN 2 (review finding MED, href='#'): the JS-download placeholder anchor.
# Pre-clicking it as a "reveal" fires the download OUTSIDE the capture, and
# the capture click then fetches the file a second time.
HASH_URL = ORIGIN + "/en/scene/892094/js-download-placeholder"
HASH_FILE = ORIGIN + "/get/scene_892094.mp4"
HASH_HTML = """<!doctype html><html><head><title>Hash</title></head><body>
<h1>Hash</h1>
<a href="#" id="dl" onclick="location.href='/get/scene_892094.mp4';return false;">Download</a>
</body></html>"""

# Placeholder / unnamed / target-less fragments: none is a reveal trigger.
# Controls on the same page: a named fragment with a pop-in marker, and a
# named fragment pointing at an element on the page, both still are.
FRAGMENTS_URL = ORIGIN + "/en/scene/892095/fragment-shapes"
FRAGMENTS_HTML = """<!doctype html><html><head><title>Fragments</title></head><body>
<h1>Fragments</h1>
<a href="#" id="f-bare" onclick="return false;">Download</a>
<a href="#!" id="f-bang" onclick="return false;">Download</a>
<a href="#0" id="f-zero" onclick="return false;">Download</a>
<a href="#/" id="f-slash" onclick="return false;">Download</a>
<a href="#dl-js" id="f-untargeted" onclick="return false;">Download</a>
<a href="#pop" id="f-popin" data-pop-in="">Download</a>
<a href="#panel" id="f-idtarget">Download</a>
<div id="pop" style="display:none"></div>
<div id="panel" style="display:none"></div>
<a href="#shown" id="f-visible-target">Download</a>
<div id="shown"></div>
<div id="wrap"><a href="#wrap" id="f-wrapper">Download</a></div>
<a href="#panel" id="f-onclick" onclick="return false;">Download</a>
<a href="#start" id="f-aria-visible" aria-controls="status">Download</a>
<span id="status" aria-live="polite"></span>
</body></html>"""

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


def _serve(hits):
    pages = {SCENE_URL: SCENE_HTML, CONTROL_URL: CONTROL_HTML,
             SECTION_URL: SECTION_HTML, OFFSITE_URL: OFFSITE_HTML,
             HASH_URL: HASH_HTML, FRAGMENTS_URL: FRAGMENTS_HTML}

    def handler(route, request):
        url = request.url.split("#", 1)[0]
        hits.append(url)
        if url in pages:
            route.fulfill(status=200, content_type="text/html", body=pages[url])
        elif url == HASH_FILE:
            route.fulfill(status=200, headers={
                "Content-Type": "video/mp4",
                "Content-Disposition": 'attachment; filename="scene_892094.mp4"'},
                body=b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
        elif url.endswith(".png"):
            route.fulfill(status=200, content_type="image/png", body=b"")
        else:
            route.fulfill(status=200, content_type="text/html",
                          body="<html><body>other</body></html>")
    return handler


@contextmanager
def _page(url=SCENE_URL):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            hits = []
            page.route(ORIGIN + "/**", _serve(hits))
            page.goto(url, wait_until="load")
            yield page, hits
        finally:
            browser.close()


def _best(page):
    from bulk_downloader.detect import find_best_download
    best = find_best_download(page)
    assert best, "DL95_DORCELCLUB_1_NOTHING_FOUND: the wide sweep found nothing on the fixture"
    return best


def _clicked(page):
    return page.evaluate("() => window.__bd_test_clicked || []")


def _file_url_of(option, page_url):
    """What _do_download does with an href-less option (row 722s)."""
    from bulk_downloader.runner_transport import TransportMixin as T
    attrs = option["locator"].evaluate(
        "el => Array.from(el.attributes).map(a => [a.name, a.value])")
    value = T._winner_url_value(attrs, page_url)
    durl, _name = T._direct_media_route(value, page_url)
    return durl


# ── 1. ranker ────────────────────────────────────────────────────────────────

def test_the_brand_logo_link_is_never_a_rendition_candidate():
    """THE ROW (ranker half): the "/en/" logo link whose title says "HD" is
    not a candidate; the winner is the score-0 "Download the video" trigger,
    so the min-resolution gate no longer files the scene needs_review."""
    with _page() as (page, _hits):
        best = _best(page)
        texts = [c.get("text", "") for c in best.get("_all_candidates", [])]
        assert not any("HD porn videos" in t for t in texts), (
            "DL95_DORCELCLUB_1_BRAND_LINK_RANKED: the brand logo link "
            "href='/en/' (title %r) is still a quality candidate: %r"
            % (BRAND_TITLE, texts))
        assert best["score"] == 0 and "download the video" in best["text"].lower(), (
            "DL95_DORCELCLUB_1_WRONG_WINNER: expected the score-0 "
            "'Download the video' trigger, got %r (score %r)"
            % (best["text"], best["score"]))


def test_positive_control_a_real_hd_file_link_beside_the_logo_is_still_ranked():
    with _page(CONTROL_URL) as (page, _hits):
        best = _best(page)
        assert best["score"] == 720 and "scene_892091_hd.mp4" in best["text"], (
            "DL95_DORCELCLUB_1_CONTROL_LOST: a real 'Download HD' .mp4 link "
            "must still win at 720p; got %r (score %r)"
            % (best["text"], best["score"]))
        texts = [c.get("text", "") for c in best.get("_all_candidates", [])]
        assert not any("HD porn videos" in t for t in texts), (
            "DL95_DORCELCLUB_1_BRAND_LINK_RANKED: the logo link is a candidate "
            "beside the real file link: %r" % (texts,))


@pytest.mark.parametrize("url, expected", [
    ("https://www.dorcelclub.com/en/", True),
    ("https://www.dorcelclub.com/en", True),
    ("https://www.dorcelclub.com/fr-fr/", True),
    ("https://www.dorcelclub.com/pt_BR/index.php", True),
    ("https://www.dorcelclub.com/", True),
    ("https://www.dorcelclub.com/en/scene/892090/the-temple-of-desire", False),
    ("https://www.dorcelclub.com/en/videos", False),
    ("https://www.dorcelclub.com/dl", False),
    ("https://www.dorcelclub.com/hd/", False),
    ("https://www.dorcelclub.com/end/", False),
    ("https://www.dorcelclub.com/xx/", False),
])
def test_a_locale_root_is_the_homepage_and_nothing_else_is(url, expected):
    from bulk_downloader.candidate_filter import _is_homepage
    assert _is_homepage(url) is expected, (
        "DL95_DORCELCLUB_1_LOCALE_ROOT: _is_homepage(%r) should be %r" % (url, expected))


# ── 2. reveal ────────────────────────────────────────────────────────────────

def _open(page, quality_preference="best", min_resolution=1080):
    from bulk_downloader.runner_transport import _open_dropdown_download_options
    best = _best(page)
    assert best["score"] == 0, (
        "DL95_DORCELCLUB_1_WRONG_WINNER: the fixture must reach the transport "
        "as a score-0 trigger; got %r (score %r)" % (best["text"], best["score"]))
    return _open_dropdown_download_options(page, best, quality_preference,
                                           min_resolution)


def test_the_download_fragment_opens_the_popin_and_the_top_rendition_is_taken(capsys):
    """THE ROW (reveal half): the #download anchor is clicked as a reveal
    trigger, the pop-in's href-less quality rows are the options, and the
    top rendition's data-slug file is what the transfer fetches."""
    with _page() as (page, _hits):
        picked = _open(page, "best", 1080)
        assert picked and picked.get("option"), (
            "DL95_DORCELCLUB_1_POPIN_NOT_EXPANDED: the '#download' trigger was "
            "not opened as a reveal, or its quality rows were not options: %r"
            % (picked,))
        opt = picked["option"]
        assert opt["score"] == 2160 and "2160p" in opt["text"], (
            "DL95_DORCELCLUB_1_NOT_TOP_RENDITION: picked %r" % (opt["text"],))
        assert _file_url_of(opt, SCENE_URL) == SLUG % "2160", (
            "DL95_DORCELCLUB_1_NO_FILE_URL: the picked row's data-slug is not "
            "the transfer URL: %r" % (_file_url_of(opt, SCENE_URL),))
        assert _clicked(page) == [], (
            "DL95_DORCELCLUB_1_ROW_CLICKED: a quality row was clicked; its file "
            "URL is fetched, never clicked: %r" % (_clicked(page),))
        err = capsys.readouterr().err
        assert "download: opened reveal 'DOWNLOAD THE VIDEO' -> 5 option(s)" in err, err


def test_quality_preference_picks_the_1080p_row():
    with _page() as (page, _hits):
        picked = _open(page, "1080p", 1080)
        assert picked and picked.get("option"), picked
        assert picked["option"]["score"] == 1080, picked["option"]
        assert _file_url_of(picked["option"], SCENE_URL) == SLUG % "1080"


def test_negative_a_fragment_trigger_that_reveals_nothing_yields_no_option(capsys):
    with _page(SECTION_URL) as (page, _hits):
        picked = _open(page, "best", 1080)
        assert picked is not None and not picked.get("option"), (
            "DL95_DORCELCLUB_1_PHANTOM_OPTION: nothing was revealed, yet %r" % (picked,))
        assert "revealed no download option" in picked["reason"], picked
        assert "revealed no download option" in capsys.readouterr().err


def test_negative_a_navigating_href_is_never_a_reveal_trigger():
    from bulk_downloader.runner_transport import _reveal_trigger_for
    with _page(OFFSITE_URL) as (page, hits):
        loc = page.locator("a.btn-dl")
        assert _reveal_trigger_for(loc) is None, (
            "DL95_DORCELCLUB_1_NAV_AS_TRIGGER: an anchor that navigates "
            "(href='/en/presignup') was taken as a reveal trigger")
        assert not any(u.endswith("/en/presignup") for u in hits), hits


# ── 3. GEN 2: review finding (MED) -- href="#" JS-download placeholder ─────

@pytest.mark.parametrize("anchor_id", [
    "f-bare", "f-bang", "f-zero", "f-slash", "f-untargeted",
    # GEN 3 (lens B16-B F1): a fragment whose target is already VISIBLE, is
    # the anchor's own wrapper, or whose anchor carries its own onclick, or
    # an aria-controls status line, does not reveal anything.
    "f-visible-target", "f-wrapper", "f-onclick", "f-aria-visible"])
def test_a_placeholder_or_targetless_fragment_is_never_a_reveal_trigger(anchor_id):
    from bulk_downloader.runner_transport import _reveal_trigger_for
    with _page(FRAGMENTS_URL) as (page, _hits):
        loc = page.locator("#" + anchor_id)
        href = loc.get_attribute("href")
        assert _reveal_trigger_for(loc) is None, (
            "DL95_DORCELCLUB_1_HASH_PLACEHOLDER_TRIGGER: the anchor href=%r "
            "(no named, existing target and no pop-in marker) was taken as a "
            "reveal trigger; its pre-click would fire a JS download outside "
            "the capture" % (href,))


@pytest.mark.parametrize("anchor_id", ["f-popin", "f-idtarget"])
def test_positive_control_a_named_targeted_fragment_is_still_a_trigger(anchor_id):
    from bulk_downloader.runner_transport import _reveal_trigger_for
    with _page(FRAGMENTS_URL) as (page, _hits):
        loc = page.locator("#" + anchor_id)
        assert _reveal_trigger_for(loc) is not None, (
            "DL95_DORCELCLUB_1_NAMED_FRAGMENT_LOST: href=%r (pop-in marker or "
            "an element with that id) must stay a reveal trigger"
            % (loc.get_attribute("href"),))


def test_positive_control_the_dorcelclub_trigger_is_still_a_trigger():
    from bulk_downloader.runner_transport import _reveal_trigger_for
    with _page() as (page, _hits):
        assert _reveal_trigger_for(page.locator("a.btn-dl")) is not None, (
            "DL95_DORCELCLUB_1_NAMED_FRAGMENT_LOST: dorcelclub's "
            "<a href='#download' data-pop-in> is no longer a reveal trigger")


def test_a_js_download_behind_href_hash_is_fetched_exactly_once():
    """Replays _do_download's order: the score-0 reveal opener, then the
    capture click under expect_download. The file must be fetched once."""
    with _page(HASH_URL) as (page, hits):
        best = _best(page)
        assert best["score"] == 0, (
            "DL95_DORCELCLUB_1_WRONG_WINNER: fixture precondition, got %r "
            "(score %r)" % (best["text"], best["score"]))
        from bulk_downloader.runner_transport import _open_dropdown_download_options
        dd = _open_dropdown_download_options(page, best, "best", 1080)
        page.wait_for_timeout(300)
        pre = [u for u in hits if u == HASH_FILE]
        assert pre == [], (
            "DL95_DORCELCLUB_1_HASH_DOUBLE_FETCH: the reveal opener pre-clicked "
            "the href='#' JS download (%d file GET(s) before the capture); "
            "dd=%r" % (len(pre), dd))
        with page.expect_download(timeout=10000) as info:
            best["locator"].click()
        info.value
        page.wait_for_timeout(300)
        got = [u for u in hits if u == HASH_FILE]
        assert len(got) == 1, (
            "DL95_DORCELCLUB_1_HASH_DOUBLE_FETCH: the JS download behind "
            "href='#' fired %d times; dd=%r" % (len(got), dd))


# ── 4. GEN 3: lens B16-B REFUTE (F1-F5) negative controls ───────────────────
# Fixtures b5 b6 a1 a2 a3 c1 c2 c3 b1 b7 d2 are the lens's
# (harness-work/PLAN-2040/lens-dl95-dorcelclub-1-B16-B/overmatch/probe/
# fixtures.py); b8 isolates the F4 scope rule. _replay runs _do_download's
# score-0 order: find_best_download, the dropdown/reveal opener, then the
# winner's direct route or the capture click under expect_download.

_LENS_FILE_PREFIXES = (ORIGIN + "/members/get.php", ORIGIN + "/trailers/")
_A1_FILE = ORIGIN + "/en/?action=getfile&id=123"
_A2_FILE = ORIGIN + "/de/index.php?option=com_media&task=get.file&id=55"

_B5_HTML = """<!doctype html><html><body>
<h1>Scene ten</h1>
<div id="download" class="download-box"><a href="#download" class="dl-btn" data-id="10">Download</a></div>
<script>document.querySelector('a.dl-btn').addEventListener('click', function (e) {
  e.preventDefault(); location.href = '/members/get.php?id=10&token=once'; });</script>
</body></html>"""

_B6_HTML = """<!doctype html><html><body>
<h1>Scene eleven</h1>
<a href="#start" class="dl-btn" aria-controls="dl-status">Download</a><span id="dl-status" aria-live="polite"></span>
<script>document.querySelector('a.dl-btn').addEventListener('click', function (e) {
  e.preventDefault(); document.getElementById('dl-status').textContent = 'Starting...';
  location.href = '/members/get.php?id=11&token=once'; });</script>
</body></html>"""

# GEN 4 (lens B7-B F1-residual): an addEventListener JS download beside a
# HIDDEN element that carries the fragment's target id (no onclick/data-href/
# data-url anywhere). The attributes cannot tell these from the pop-in; the
# pre-click's aborted top-frame navigation can.
_X1_HTML = """<!doctype html><html><body><h1>Scene 21</h1>
<a href="#download" class="dl-btn">Download</a>
<div id="download" class="modal" style="display:none">Your download is starting...</div>
<script>document.querySelector('a.dl-btn').addEventListener('click', function (e) {
  e.preventDefault(); document.getElementById('download').style.display='block';
  location.href = '/members/get.php?id=21&token=once'; });</script></body></html>"""

_X2_HTML = """<!doctype html><html><body><h1>Scene 22</h1>
<a href="#start" class="dl-btn" aria-controls="dl-tip">Download</a>
<div id="dl-tip" hidden>Downloads count against your daily quota</div>
<script>document.querySelector('a.dl-btn').addEventListener('click', function (e) {
  e.preventDefault(); location.href = '/members/get.php?id=22&token=once'; });</script></body></html>"""

_X3_HTML = """<!doctype html><html><body><h1>Scene 23</h1>
<a href="#download" class="dl-btn">Download</a>
<section id="download" style="display:none"><h2>Download help</h2><p>Use a modern browser.</p></section>
<script>document.querySelector('a.dl-btn').addEventListener('click', function (e) {
  e.preventDefault(); location.href = '/members/get.php?id=23&token=once'; });</script></body></html>"""

# GEN 5 (lens B7-B F1-residual-2): the same shape fetching through a hidden
# iframe (x4), fetch()+blob+a[download] (x6) and window.open (x7).
def _jsdl_html(n, body):
    return ("""<!doctype html><html><body><h1>Scene %d</h1><a href="#download" class="dl-btn">Download</a>
<div id="download" class="modal" style="display:none">Your download is starting...</div>
<script>document.querySelector('a.dl-btn').addEventListener('click', function (e) { e.preventDefault(); %s });</script>
</body></html>""" % (n, body))


_X4_HTML = _jsdl_html(24, "var f = document.createElement('iframe'); f.style.display='none';"
                      " f.src = '/members/get.php?id=24&token=once'; document.body.appendChild(f);")
_X6_HTML = _jsdl_html(26, "fetch('/members/get.php?id=26&token=once').then(function (r) { return r.blob(); })"
                      ".then(function (b) { var a = document.createElement('a'); a.href = URL.createObjectURL(b);"
                      " a.download = 's26.mp4'; document.body.appendChild(a); a.click(); });")
_X7_HTML = _jsdl_html(27, "window.open('/members/get.php?id=27&token=once', '_blank');")

_D2_HTML = """<!doctype html><html><body>
<h1>Scene twelve</h1>
<button class="dl" type="button">Download this video</button>
<script>document.querySelector('button.dl').addEventListener('click', function () {
  location.href = '/members/get.php?id=12&token=once'; });</script>
</body></html>"""

_A1_HTML = """<!doctype html><html><body>
<h1>Scene 123</h1>
<a href="/en/?action=getfile&id=123" class="btn">Download</a>
</body></html>"""

_A2_HTML = """<!doctype html><html><body>
<h1>Szene 55</h1>
<a href="/de/index.php?option=com_media&task=get.file&id=55" class="btn">Download</a>
</body></html>"""

_A3_HTML = _B5_HTML.replace("id=10&token=once", "id=8812")

_C1_HTML = """<!doctype html><html><head>
<style>.dropdown-menu{display:none}.dropdown-menu.show{display:block} i.q{display:inline-block;width:8px;height:8px}</style></head><body>
<h1>Scene five</h1>
<div class="dl"><button class="dropdown-toggle" data-toggle="dropdown">Download</button>
<ul class="dropdown-menu"><li role="menuitem" data-q="1080">MP4 1080p <i class="q" data-quality="hd"></i></li>
<li role="menuitem" data-q="720">MP4 720p <i class="q" data-quality="hd"></i></li></ul></div>
<script>
document.querySelector('.dropdown-toggle').addEventListener('click', function () { document.querySelector('.dropdown-menu').classList.add('show'); });
document.querySelectorAll('li[data-q]').forEach(function (li) { li.addEventListener('click', function () {
  location.href = '/members/get.php?q=' + li.getAttribute('data-q'); }); });
</script></body></html>"""

_C2_HTML = """<!doctype html><html><head>
<style>#m{display:none}#m.show{display:block}</style></head><body>
<h1>Scene six</h1>
<button id="open">Download</button>
<div id="m"><button class="fmt" data-q="1080">MP4 1080p <small data-quality="fhd">Full HD</small></button>
<button class="fmt" data-q="720">MP4 720p <small data-quality="hd">HD</small></button></div>
<script>
document.getElementById('open').addEventListener('click', function () { document.getElementById('m').classList.add('show'); });
document.querySelectorAll('button.fmt').forEach(function (b) { b.addEventListener('click', function () {
  location.href = '/members/get.php?q=' + b.getAttribute('data-q'); }); });
</script></body></html>"""

_C3_HTML = """<!doctype html><html><head>
<style>#m{display:none}#m.show{display:block}</style></head><body>
<h1>Scene seven</h1>
<button id="open">Download</button>
<div id="m"><div class="preview"><video width="160" height="90"></video><div class="vjs-quality-badge" data-quality="2160">4K</div></div>
<button class="fmt" data-q="1080">MP4 1080p</button><button class="fmt" data-q="720">MP4 720p</button></div>
<script>
document.getElementById('open').addEventListener('click', function () { document.getElementById('m').classList.add('show'); });
document.querySelectorAll('button.fmt').forEach(function (b) { b.addEventListener('click', function () {
  location.href = '/members/get.php?q=' + b.getAttribute('data-q'); }); });
</script></body></html>"""

_LAZY_JS = """<script>
new IntersectionObserver(function (es, o) {
  if (!es.some(function (e) { return e.isIntersecting; })) return;
  o.disconnect();
  %s
}).observe(document.getElementById('%s'));
</script>"""

_B1_HTML = """<!doctype html><html><body>
<h1>Scene one</h1>
<div class="scene-actions"><a class="jump" href="#download">Download</a> <a href="#comments">Comments</a></div>
<div style="height:2500px">player</div>
<div id="download"><h2>Files</h2><a href="/members/get.php?id=5&t=abc" class="btn">Download MP4</a></div>
<div id="related"></div>
<div style="height:1500px"></div>%s</body></html>""" % (_LAZY_JS % (
    "var r = document.getElementById('related');"
    " ['4K 29:17 Other scene', '1080p 12:03 Another'].forEach(function (t, i) {"
    " var a = document.createElement('a'); a.href = '/scene/9' + i; a.textContent = t;"
    " a.style.display = 'block'; r.appendChild(a); });", "related"))

_TRAILER_JS = _LAZY_JS % (
    "var a = document.createElement('a'); a.href = '/trailers/scene13_2160.mp4';"
    " a.textContent = '4K Trailer'; a.style.display = 'block';"
    " document.getElementById('extras').appendChild(a);", "extras")

_B7_HTML = """<!doctype html><html><body>
<h1>Scene thirteen</h1>
<a class="jump" href="#download">Download</a>
<div style="height:2500px">player</div>
<div id="download"><h2>Files</h2><p>Log in to see files.</p></div>
<div id="extras"></div><div style="height:1500px"></div>%s</body></html>""" % _TRAILER_JS

# b8 (F4 scope): the fragment target IS hidden (a real reveal), but the
# reveal also scrolls a lazy 4K trailer into view OUTSIDE the target.
_B8_HTML = """<!doctype html><html><body>
<h1>Scene fourteen</h1>
<a class="jump" href="#download">Download</a>
<div style="height:2500px">player</div>
<div id="download" style="display:none"><h2>Files</h2><p>Log in to see files.</p></div>
<div id="extras"></div><div style="height:1500px"></div>%s
<script>document.querySelector('a.jump').addEventListener('click', function () {
  document.getElementById('download').style.display = 'block'; });</script>
</body></html>""" % _TRAILER_JS


def _lens_is_file(url):
    return url in (_A1_FILE, _A2_FILE) or url.startswith(_LENS_FILE_PREFIXES)


def _replay(url, html, capture=True):
    from playwright.sync_api import sync_playwright
    from bulk_downloader.detect import find_best_download
    from bulk_downloader.runner_transport import (
        _open_dropdown_download_options, TransportMixin as T)

    hits = []

    def handler(route, request):
        u = request.url.split("#", 1)[0]
        hits.append(u)
        if u == url:
            route.fulfill(status=200, content_type="text/html", body=html)
        elif _lens_is_file(u):
            route.fulfill(status=200, headers={
                "Content-Type": "video/mp4",
                "Content-Disposition": 'attachment; filename="f.mp4"'},
                body=b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
        else:
            route.fulfill(status=200, content_type="text/html",
                          body="<html><body>other</body></html>")

    out = {"best": None, "dd_option": None, "pre": [], "direct": "",
           "download": "", "gets": []}
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            # GEN 5: routed on the CONTEXT so a popup's or an iframe's file
            # GET is counted too (a page route misses the popup).
            page.context.route(ORIGIN + "/**", handler)
            page.goto(url, wait_until="load")
            page.wait_for_timeout(300)
            best = find_best_download(page)
            if not best:
                return out
            out["best"] = (best.get("text", ""), best.get("score"))
            dd = None
            if best.get("score", 0) == 0 and not T._winner_href_routes(best, page.url):
                dd = _open_dropdown_download_options(page, best, "best", 0)
            if dd and dd.get("option"):
                out["dd_option"] = dd["option"]["text"]
                best = dict(dd["option"], **{k: v for k, v in best.items()
                                             if k not in dd["option"]})
            page.wait_for_timeout(300)
            out["pre"] = [u for u in hits if _lens_is_file(u)]
            attrs = best["locator"].evaluate(
                "el => Array.from(el.attributes).map(a => [a.name, a.value])")
            href = T._winner_url_value(attrs, page.url)
            out["direct"] = (T._direct_media_route(href, page.url)[0] or "") if href else ""
            if best.get("_adopted_download") is not None:
                # GEN 5: the reveal probe already fired the download; the
                # product takes it un-clicked (runner_transport standard path).
                out["download"] = best["_adopted_download"].url
            elif capture and not out["direct"]:
                try:
                    with page.expect_download(timeout=10000) as info:
                        best["locator"].click(timeout=5000)
                    out["download"] = info.value.url
                except Exception as e:
                    out["download"] = "FAIL " + str(e).splitlines()[0][:90]
                page.wait_for_timeout(300)
            out["gets"] = [u for u in hits if _lens_is_file(u)]
            return out
        finally:
            browser.close()


# F1 HIGH / F5 MED: a JS download is never pre-clicked as a reveal.
@pytest.mark.parametrize("case, path, html, file_url, code", [
    ("b5", "/scene/10", _B5_HTML, ORIGIN + "/members/get.php?id=10&token=once",
     "DL95_DORCELCLUB_1_NAMED_FRAGMENT_DOUBLE_FETCH"),
    ("b6", "/scene/11", _B6_HTML, ORIGIN + "/members/get.php?id=11&token=once",
     "DL95_DORCELCLUB_1_ARIA_CONTROLS_DOUBLE_FETCH"),
    ("d2", "/scene/12", _D2_HTML, ORIGIN + "/members/get.php?id=12&token=once",
     "DL95_DORCELCLUB_1_JS_BUTTON_AS_REVEAL"),
    ("x1", "/scene/21", _X1_HTML, ORIGIN + "/members/get.php?id=21&token=once",
     "DL95_DORCELCLUB_1_HIDDEN_STATUS_MODAL_DOUBLE_FETCH"),
    ("x2", "/scene/22", _X2_HTML, ORIGIN + "/members/get.php?id=22&token=once",
     "DL95_DORCELCLUB_1_HIDDEN_TOOLTIP_DOUBLE_FETCH"),
    ("x3", "/scene/23", _X3_HTML, ORIGIN + "/members/get.php?id=23&token=once",
     "DL95_DORCELCLUB_1_HIDDEN_HELP_PANEL_DOUBLE_FETCH"),
    ("x4", "/scene/24", _X4_HTML, ORIGIN + "/members/get.php?id=24&token=once",
     "DL95_DORCELCLUB_1_HIDDEN_IFRAME_DOUBLE_FETCH"),
    ("x7", "/scene/27", _X7_HTML, ORIGIN + "/members/get.php?id=27&token=once",
     "DL95_DORCELCLUB_1_WINDOW_OPEN_DOUBLE_FETCH"),
])
def test_a_js_download_is_never_pre_clicked_as_a_reveal(case, path, html, file_url, code):
    out = _replay(ORIGIN + path, html)
    assert out["pre"] == [] and out["gets"] == [file_url] \
            and out["download"] == file_url, (
        "%s: lens fixture %s -- the file must be fetched ONCE, by the capture "
        "click (a one-time token dies on the pre-click); got %r"
        % (code, case, out))


# GEN 5 (x6): the pre-click's fetch() spends the token (the file response
# is the proof); the blob download it fires is ADOPTED, never re-clicked.
def test_a_fetched_blob_download_is_adopted_not_reclicked():
    out = _replay(ORIGIN + "/scene/26", _X6_HTML)
    file_url = ORIGIN + "/members/get.php?id=26&token=once"
    assert out["gets"] == [file_url] and out["download"].startswith("blob:"), (
        "DL95_DORCELCLUB_1_FETCH_BLOB_DOUBLE_FETCH: the file must be fetched "
        "ONCE and the blob download adopted; got %r" % (out,))


# GEN 4 positive control beside the x1-x3 zeros: the guard aborts the
# navigation and names the trigger as the option; the pop-in shape (SCENE_URL)
# is exercised above and never trips the guard.
def test_an_aborted_navigation_names_the_trigger_as_the_download():
    from bulk_downloader.runner_transport import _open_dropdown_download_options
    with _page(ORIGIN + "/scene/21") as (page, hits):
        page.route(ORIGIN + "/**", lambda route, request: route.fulfill(
            status=200, content_type="text/html", body=_X1_HTML)
            if request.url.split("#", 1)[0] == ORIGIN + "/scene/21" else route.fallback())
        page.goto(ORIGIN + "/scene/21", wait_until="load")
        best = _best(page)
        dd = _open_dropdown_download_options(page, best, "best", 0)
        page.wait_for_timeout(300)
        assert dd and dd.get("option") and "JS download" in dd.get("reason", ""), (
            "DL95_DORCELCLUB_1_ABORTED_NAVIGATION_NOT_ADOPTED: %r" % (dd,))
        assert [u for u in hits if u.startswith(_LENS_FILE_PREFIXES)] == [], (
            "DL95_DORCELCLUB_1_HIDDEN_STATUS_MODAL_DOUBLE_FETCH: the aborted "
            "pre-click still reached the server: %r" % (hits,))
        assert page.url.split("#", 1)[0] == ORIGIN + "/scene/21", page.url


# F2 MED: a locale root WITH a query is a page, not the homepage.
@pytest.mark.parametrize("case, url, html, file_url, code", [
    ("a1", ORIGIN + "/en/scene.php?id=123", _A1_HTML, _A1_FILE,
     "DL95_DORCELCLUB_1_LOCALE_QUERY_LINK_REJECTED"),
    ("a2", ORIGIN + "/de/video.php?id=55", _A2_HTML, _A2_FILE,
     "DL95_DORCELCLUB_1_LOCALE_INDEXPHP_QUERY_REJECTED"),
    ("a3", ORIGIN + "/en/?v=8812", _A3_HTML, ORIGIN + "/members/get.php?id=8812",
     "DL95_DORCELCLUB_1_SCENE_AT_LOCALE_ROOT_REJECTED"),
])
def test_a_locale_root_with_a_query_is_still_downloaded(case, url, html, file_url, code):
    out = _replay(url, html)
    assert out["best"] is not None and out["download"] == file_url \
            and out["gets"] == [file_url], (
        "%s: lens fixture %s -- the download link was rejected as a "
        "'homepage link' (or fetched twice); got %r" % (code, case, out))


@pytest.mark.parametrize("url", [
    "https://www.dorcelclub.com/en/?action=getfile&id=123",
    "https://www.dorcelclub.com/de/index.php?option=com_media&task=get.file&id=55",
    "https://www.dorcelclub.com/en/?v=8812#download",
])
def test_a_locale_root_with_a_query_is_not_the_homepage(url):
    from bulk_downloader.candidate_filter import _is_homepage
    assert _is_homepage(url) is False, (
        "DL95_DORCELCLUB_1_LOCALE_QUERY_HOMEPAGE: %r is a page with a query, "
        "not the locale homepage" % (url,))


# F3 MED: a [data-quality] badge/chip is not an option and does not make its
# <li>/<button> a wrapper.
@pytest.mark.parametrize("case, path, html, code", [
    ("c1", "/scene/5", _C1_HTML, "DL95_DORCELCLUB_1_BADGE_MARKS_LI_WRAPPER"),
    ("c2", "/scene/6", _C2_HTML, "DL95_DORCELCLUB_1_CHIP_MARKS_BUTTON_WRAPPER"),
    ("c3", "/scene/7", _C3_HTML, "DL95_DORCELCLUB_1_QUALITY_CHIP_AS_OPTION"),
])
def test_a_quality_badge_is_neither_an_option_nor_a_wrapper(case, path, html, code):
    out = _replay(ORIGIN + path, html)
    want = ORIGIN + "/members/get.php?q=1080"
    assert out["dd_option"] and "1080p" in out["dd_option"] \
            and out["download"] == want, (
        "%s: lens fixture %s -- the MP4 1080p row must be picked and "
        "downloaded; got %r" % (code, case, out))


# F4 MED: lazily visible page content outside the revealed target is never
# the reveal's option.
@pytest.mark.parametrize("case, path, html, bad, code", [
    ("b7", "/scene/13", _B7_HTML, "trailers/scene13_2160.mp4",
     "DL95_DORCELCLUB_1_JUMP_LINK_LAZY_TRAILER"),
    ("b1", "/scene/1", _B1_HTML, "/scene/9",
     "DL95_DORCELCLUB_1_JUMP_LINK_LAZY_RELATED"),
    ("b8", "/scene/14", _B8_HTML, "trailers/scene13_2160.mp4",
     "DL95_DORCELCLUB_1_OPTION_OUTSIDE_TARGET"),
])
def test_lazy_content_outside_the_reveal_target_is_never_an_option(case, path, html, bad, code):
    out = _replay(ORIGIN + path, html, capture=False)
    assert bad not in (out["dd_option"] or "") and bad not in out["direct"], (
        "%s: lens fixture %s -- a lazily loaded %r outside the fragment "
        "target was taken as the download; got %r" % (code, case, bad, out))


def test_a_js_button_labelled_like_dorcelclub_is_not_a_reveal_trigger():
    from bulk_downloader.runner_transport import _reveal_trigger_for
    html = _D2_HTML.replace("Download this video", "Download the video")
    with _page_html(ORIGIN + "/scene/15", html) as page:
        assert _reveal_trigger_for(page.locator("button.dl")) is None, (
            "DL95_DORCELCLUB_1_JS_BUTTON_TEXT_WIDENED: a <button> 'Download "
            "the video' was taken as a reveal trigger; the new label is for "
            "the fragment-anchor shape only")


@contextmanager
def _page_html(url, html):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.route(ORIGIN + "/**", lambda route, request: route.fulfill(
                status=200, content_type="text/html",
                body=html if request.url.split("#", 1)[0] == url else "x"))
            page.goto(url, wait_until="load")
            yield page
        finally:
            browser.close()
