"""A page with no video says so, instead of scoring a 6K non-candidate.

BD_GATE_SCOPE is a module-level ASSIGNMENT below, not a docstring line -- the
classifier in tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py parses the
assignment, and a docstring marker leaves the file undeclared.

MEASURED on test6 2026-08-29 at v3.66.1348, history row 125, the FIRST download
attempted after the row-388 hold was lifted:

    url     https://venus.wowgirls.com/gallery/x15b9ab4/stunned-by-each-other
    status  needs_review   filename ""   file_size 0   bytes_fetched null
    message no dl event; scored ok but no download fired; saw: 6K(?):6K /films-6K/

THE REFUSAL WAS CORRECT AND IS NOT WEAKENED HERE. Nothing wrong was saved; the
chain declined rather than writing a mislabelled file. What was wrong was the
DIAGNOSIS. bd-shoot.py against that live page reported ANCHORS 136,
AFFORDANCES 143, MEDIA AFFORDANCES 0 -- `/gallery/` is a PHOTO SET and there is
no video on the page at all. "scored ok but no download fired" describes a video
page whose control failed, so the operator goes looking for a broken selector on
a page with nothing to select.

THE 6K WAS A GHOST. The only candidate scored was the site-navigation link
`<a href="/films-6K/">6K</a>`, and its size parsed unknown -- which is what the
`(?)` in the message records. Row 381 stopped photo PIXEL DIMENSIONS being read
as a video resolution; this is the sibling shape, a named tier inside a
NAVIGATION HREF.

Reproduced on the fixture below against 9626b7e (origin/main): winner text
'6K /films-6K/', score 3160, size 0, admitted through the ANCESTOR WALK, whose
`[24568]K` text-matcher accepts '6K' even though the wide sweep's own res_re
does not list 6k at all.
"""

# The gate parses a module-level ASSIGNMENT, not a docstring line.
BD_GATE_SCOPE = "module"

import ast
from contextlib import contextmanager
from pathlib import Path

import pytest

from bulk_downloader.constants import NON_VIDEO_RE
from bulk_downloader.detect import (
    CONTROL_DID_NOT_FIRE_STATE,
    NO_VIDEO_STATE,
    _DL_WORD_RE,
    _EXPLICIT_HEIGHT_RE,
    _is_chrome_href,
    find_best_download,
    fmt_bytes,
    is_site_chrome_link,
    page_media_census,
    page_media_verdict,
    parse_size_bytes,
    res_label,
    res_score,
)

ROOT = Path(__file__).resolve().parents[1]

# The exact candidate text the deploy host recorded.
GHOST_TEXT = "6K /films-6K/"

# The site chrome, in the shape the incident page carries it: a menu whose items
# name the site's tiers. `/films-6K/` is the ghost; `/films-4K/` is its sibling
# and proves the population is not a single accident.
_NAV = """
<header class="site-header"><nav class="main-menu">
  <a href="/films/">Films</a>
  <a href="/films-4K/">4K</a>
  <a href="/films-6K/">6K</a>
  <a href="/models/">Models</a>
  <a href="/gallery/">Galleries</a>
</nav></header>
"""

# A PHOTO SET: three stills, no video of any kind.
_GALLERY = """<!doctype html><html><body>
""" + _NAV + """
<main><h1>Stunned By Each Other</h1>
  <div class="gallery">
    <a href="/gallery/x15b9ab4/stunned-by-each-other/1.jpg"><img src="/thumb/1.jpg" alt="still 1"></a>
    <a href="/gallery/x15b9ab4/stunned-by-each-other/2.jpg"><img src="/thumb/2.jpg" alt="still 2"></a>
    <a href="/gallery/x15b9ab4/stunned-by-each-other/3.jpg"><img src="/thumb/3.jpg" alt="still 3"></a>
  </div>
</main>
<footer><a href="/support/">Support</a></footer>
</body></html>"""

# The real wowgirls SCENE block, reduced to its load-bearing shape (the same
# fixture tests/test_row380_wrapper_never_outranks_leaf.py measures).
_SCENE_BLOCK = """
<div class="content_download video_downloads">
  <div class="ct_dl_title">FULL MOVIE DOWNLOAD</div>
  <div class="row">
    <a class="ct_dl_button" href="https://cdn.example/dl/1920x1080_60FPS.mp4">1920 x 1080</a>
    <div class="caption">FHD &bull; H264 &bull; 60fps &bull; 1.99GB</div>
  </div>
  <div class="row">
    <a class="ct_dl_button" href="https://cdn.example/dl/1280x720_60FPS.mp4">1280 x 720</a>
    <div class="caption">HD &bull; H264 &bull; 60fps &bull; 395MB</div>
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
"""
_SCENE = "<!doctype html><html><body>" + _SCENE_BLOCK + "</body></html>"
_SCENE_WITH_CHROME = ("<!doctype html><html><body>" + _NAV + _SCENE_BLOCK
                      + "</body></html>")

# The ranking the current tree produces for the scene block, measured at
# 9626b7e BEFORE this row's change. "exactly as it does today" is pinned as
# numbers, not as prose.
_SCENE_RANKING_TODAY = [(4325, 0), (2165, 0), (1085, 0), (725, 0)]


@contextmanager
def _page(html):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch()
        try:
            pg = br.new_page(viewport={"width": 1400, "height": 900})
            pg.set_content(html, wait_until="load")
            yield pg
        finally:
            br.close()


# ── Preconditions: the ghost is real, and no existing mechanism sees it ──────

def test_precondition_the_ghost_scores_6k_and_parses_no_size():
    """The measured message is reconstructed from the fixture text, so the
    fixture provably carries the incident's own subject."""
    assert res_score(GHOST_TEXT) == 3160
    assert res_label(3160) == "6K"
    assert parse_size_bytes(GHOST_TEXT) == 0
    assert fmt_bytes(0) == ""
    seen = "%s(%s):%s" % (res_label(res_score(GHOST_TEXT)),
                          fmt_bytes(parse_size_bytes(GHOST_TEXT)) or "?",
                          GHOST_TEXT[:30])
    assert seen == "6K(?):6K /films-6K/", (
        "the fixture text no longer reproduces the recorded message %r" % seen)


def test_precondition_no_existing_filter_can_see_the_ghost():
    """Otherwise this row is solving a problem another mechanism solves."""
    assert not NON_VIDEO_RE.search(GHOST_TEXT), (
        "NON_VIDEO_RE already rejects the ghost; the candidate population is "
        "not the seam")
    assert not _DL_WORD_RE.search(GHOST_TEXT), (
        "the ghost carries a download word, so it was not admitted by the "
        "resolution matcher alone")
    assert not _EXPLICIT_HEIGHT_RE.search(GHOST_TEXT), (
        "the ghost carries an explicit pixel height, so it is row 381's shape "
        "and not this one")


def test_the_gallery_fixture_really_is_a_page_with_no_video():
    """Precondition, asserted on the DOM so it stays meaningful after the
    ghost is correctly filtered out."""
    with _page(_GALLERY) as pg:
        assert pg.locator("nav a").count() == 5
        ghost = pg.locator("a[href='/films-6K/']")
        assert ghost.count() == 1 and ghost.first.is_visible()
        assert pg.locator("div.gallery a").count() == 3
        assert pg.locator("video, audio, source").count() == 0
        assert pg.locator("a[href$='.mp4']").count() == 0
        affordances, media = page_media_census(pg)
        assert affordances == 9, (
            "the census denominator moved; a zero media count is only "
            "evidence over a known nonzero denominator (got %d)" % affordances)
        assert media == 0


# ── RED: the ghost wins a page that has nothing to download ──────────────────

def test_a_photo_gallery_yields_no_candidate_at_all():
    """RED on 9626b7e: `find_best_download` returns `6K /films-6K/`, score
    3160, size 0 -- and acceptance (c), a candidate whose size is unparseable
    must not outrank nothing at all."""
    with _page(_GALLERY) as pg:
        best = find_best_download(pg, "", learned=None, runner=None)
        assert best is None, (
            "a photo gallery produced a download candidate: text=%r score=%r "
            "size=%r -- clicking it navigates the site menu and fires no "
            "download event" % (best.get("text"), best.get("score"),
                                best.get("size")))


def test_the_chrome_filter_is_scoped_to_the_wide_sweep_only():
    """The learned row_selectors and the operator's custom selector keep full
    authority: the operator taught those. Exactly one call site."""
    src = (ROOT / "bulk_downloader" / "detect.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "is_site_chrome_link"]
    assert len(calls) == 1, (
        "expected exactly one call site (inside `add`), found %d" % len(calls))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "add")
    assert any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
               and c.func.id == "is_site_chrome_link" for c in ast.walk(fn)), (
        "the filter is not on the candidate-construction path")


# ── Negative controls: every clause individually rescues a real control ──────

# (label, html for the control, attribute to read back, expected value)
RESCUE_CASES = [
    ("media file name",
     '<a href="/films-6K/movie.mp4">6K</a>', "href", "/films-6K/movie.mp4"),
    ("a data-* affordance of its own",
     '<a href="/films-6K/" data-href="https://cdn.example/x">6K</a>',
     "data-href", "https://cdn.example/x"),
    ("a download word",
     '<a href="/download-6K/">6K</a>', "href", "/download-6K/"),
    ("an explicit pixel height",
     '<a href="/films-2160p/">2160p</a>', "href", "/films-2160p/"),
    ("a parsed size",
     '<a href="/films-6K/">6K &bull; 3.01GB</a>', "href", "/films-6K/"),
    ("a deep id-bearing path",
     '<a href="/get/6K/12345">6K</a>', "href", "/get/6K/12345"),
    ("a query string",
     '<a href="/films-6K/?token=abc">6K</a>', "href", "/films-6K/?token=abc"),
    ("a foreign host",
     '<a href="https://cdn.example/films-6K/">6K</a>', "href",
     "https://cdn.example/films-6K/"),
    ("an onclick handler",
     '<a href="/films-6K/" onclick="dl()">6K</a>', "href", "/films-6K/"),
    ("not an anchor at all",
     '<div data-href="/films-6K/">6K</div>', "data-href", "/films-6K/"),
]


def test_the_rescue_denominator_is_the_one_this_row_claims():
    assert len(RESCUE_CASES) == 10


@pytest.mark.parametrize("label,html,attr,expected",
                         RESCUE_CASES,
                         ids=[c[0].replace(" ", "-") for c in RESCUE_CASES])
def test_each_clause_individually_rescues_a_real_control(label, html, attr,
                                                         expected):
    """A control that differs from the ghost by ONE clause must survive.

    This is the proof that "every real download control fails at least one
    clause" is enumerated rather than asserted: silently deleting the
    operator's real download is the one outcome this filter must never
    produce.
    """
    doc = ("<!doctype html><html><body>" + _NAV + "<main>" + html
           + "</main></body></html>")
    with _page(doc) as pg:
        best = find_best_download(pg, "", learned=None, runner=None)
        assert best is not None, (
            "the chrome filter deleted a control that differs by %s" % label)
        assert best["locator"].get_attribute(attr) == expected, (
            "winner is %r, not the %s control" % (best["text"], label))


def test_the_scene_page_still_ranks_its_tiers_exactly_as_it_does_today():
    """Negative control (d): a real wowgirls SCENE page, with the very same
    site chrome on it, is untouched -- and the ghost is gone from the list."""
    with _page(_SCENE) as pg:
        clean = find_best_download(pg, "", learned=None, runner=None)
        assert clean is not None
        clean_rank = [(c["score"], c["size"])
                      for c in clean["_all_candidates"]]
    assert clean_rank == _SCENE_RANKING_TODAY, (
        "the scene ranking moved on a page with no chrome at all: %r"
        % (clean_rank,))

    with _page(_SCENE_WITH_CHROME) as pg:
        best = find_best_download(pg, "", learned=None, runner=None)
        assert best is not None, "the scene's own download block was deleted"
        texts = [c["text"] for c in best["_all_candidates"]]
        assert not any("/films-6K/" in t for t in texts), (
            "the site-navigation ghost is still in the candidate list: %r"
            % (texts,))
        assert [(c["score"], c["size"]) for c in best["_all_candidates"]] == \
            _SCENE_RANKING_TODAY, (
            "chrome on the page changed the scene's ranking: %r"
            % ([(c["score"], c["size"]) for c in best["_all_candidates"]],))
        assert "7680x4320" in (best["locator"].get_attribute("href") or ""), (
            "winner is no longer the 8K tier: %r" % (best["text"],))


# ── The href predicate, as a pure function ───────────────────────────────────

CHROME_HREFS = ["/films-6K/", "/films-6K", "films-6K/", "/tour/films-6K/",
                "https://venus.example/films-6K/"]
NOT_CHROME_HREFS = ["", "#", "javascript:void(0)", "mailto:a@b.c",
                    "/films-6K/movie.mp4", "/films-6K/?token=abc",
                    "/a/b/c", "/scene/12345", "/films-6K.m3u8",
                    "https://cdn.example/films-6K/"]


@pytest.mark.parametrize("href", CHROME_HREFS)
def test_a_section_path_is_chrome(href):
    assert _is_chrome_href(href, "https://venus.example/gallery/x/y") is True


@pytest.mark.parametrize("href", NOT_CHROME_HREFS)
def test_anything_that_is_not_a_section_path_is_kept(href):
    assert _is_chrome_href(href, "https://venus.example/gallery/x/y") is False


def test_the_href_predicate_denominator_is_nonzero_and_disjoint():
    assert len(CHROME_HREFS) == 5 and len(NOT_CHROME_HREFS) == 10
    assert not (set(CHROME_HREFS) & set(NOT_CHROME_HREFS))


# ── The census: a measured zero, over a nonzero denominator, or UNKNOWN ──────

class _RaisingPage:
    def evaluate(self, *a, **k):
        raise RuntimeError("page is gone")


class _GarbagePage:
    def evaluate(self, *a, **k):
        return {"rows": "not a list", "players": 0}


def test_a_gallery_page_reports_the_no_video_state():
    with _page(_GALLERY) as pg:
        assert page_media_verdict(pg) == (True, 9, 0)


def test_a_scene_page_does_not_report_the_no_video_state():
    with _page(_SCENE) as pg:
        verdict, affordances, media = page_media_verdict(pg)
        assert verdict is False
        assert affordances == 4 and media == 4


def test_a_page_carrying_a_player_is_never_called_no_video():
    """A <video> element is media even though bd-shoot's attribute set cannot
    see its `src`. Strictly conservative: it can only make the verdict fire
    less often."""
    doc = ('<!doctype html><html><body><a href="/films-6K/">6K</a>'
           '<video src="/stream/x.webm"></video></body></html>')
    with _page(doc) as pg:
        verdict, affordances, media = page_media_verdict(pg)
        assert affordances == 1 and media == 1
        assert verdict is False


def test_zero_affordances_of_any_kind_is_unknown_not_no_video():
    """A7, and bd-shoot's own rule: a page that did not render, or a session
    that is not authenticated, must never be read as 'there is no video'."""
    doc = "<!doctype html><html><body><p>nothing here</p></body></html>"
    with _page(doc) as pg:
        assert page_media_census(pg) == (0, 0)
        assert page_media_verdict(pg) == (None, 0, 0)


def test_an_unmeasurable_page_is_unknown_not_no_video():
    assert page_media_census(_RaisingPage()) is None
    assert page_media_verdict(_RaisingPage()) == (None, -1, -1)
    assert page_media_census(_GarbagePage()) is None
    assert page_media_verdict(_GarbagePage()) == (None, -1, -1)


def test_is_site_chrome_link_fails_open_on_a_locator_that_raises():
    class _RaisingLocator:
        def locator(self, *a, **k):
            raise RuntimeError("detached")

        def get_attribute(self, *a, **k):
            raise RuntimeError("detached")

    assert is_site_chrome_link(_RaisingLocator(), GHOST_TEXT, "") is False


# ── The two states are distinct, and each message names which one it is ──────

def _docstring_node(fn):
    body = getattr(fn, "body", None) or []
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        return body[0].value
    return None


def _fn_containing(tree, needle):
    """The function that BUILDS a message containing *needle* -- never one that
    merely documents it.

    A docstring is an ast.Constant too, and the first draft of this selector
    resolved `no dl event` to `_direct_media_route`, whose docstring quotes the
    message it was written to prevent. That is the row-353 mutation-anchor
    shape: an anchor that resolves to the wrong site reports on code nobody
    asked about. Docstrings are excluded, and an f-string message is matched on
    its literal parts so prose can never satisfy this.
    """
    for n in ast.walk(tree):
        if not isinstance(n, ast.FunctionDef):
            continue
        doc = _docstring_node(n)
        for c in ast.walk(n):
            if isinstance(c, ast.JoinedStr):
                for v in c.values:
                    if (isinstance(v, ast.Constant)
                            and isinstance(v.value, str) and needle in v.value):
                        return n
            if (isinstance(c, ast.Constant) and c is not doc
                    and isinstance(c.value, str) and needle in c.value):
                return n
    return None


def _calls(fn, name):
    return sum(1 for n in ast.walk(fn)
               if isinstance(n, ast.Call)
               and ((isinstance(n.func, ast.Name) and n.func.id == name)
                    or (isinstance(n.func, ast.Attribute)
                        and n.func.attr == name)))


def _names(fn, name):
    return sum(1 for n in ast.walk(fn)
               if isinstance(n, ast.Name) and n.id == name)


def test_the_two_states_are_distinct_named_constants():
    assert NO_VIDEO_STATE == "no-video-on-page"
    assert CONTROL_DID_NOT_FIRE_STATE == "a control existed and did not fire"
    assert NO_VIDEO_STATE != CONTROL_DID_NOT_FIRE_STATE


def test_the_ast_predicates_can_return_zero():
    """Negative control for the gate below: a function without the call must
    score zero, and a module without the message must yield no function."""
    empty = ast.parse("def f():\n    return 1\n").body[0]
    assert _calls(empty, "page_media_verdict") == 0
    assert _names(empty, "NO_VIDEO_STATE") == 0
    assert _fn_containing(ast.parse("x = 1\n"), "no dl event") is None
    # A function that only DOCUMENTS the message must not be selected, and one
    # that builds it must be -- the exact confusion this selector was rewritten
    # for.
    doc_only = ast.parse('def f():\n    """no dl event happened."""\n    return 1\n')
    assert _fn_containing(doc_only, "no dl event") is None
    builder = ast.parse('def g(h):\n    return f"no dl event; {h}"\n')
    assert _fn_containing(builder, "no dl event").name == "g"


def test_the_no_button_path_asks_the_page_before_blaming_a_selector():
    tree = ast.parse((ROOT / "bulk_downloader" / "runner.py").read_text(
        encoding="utf-8"))
    fn = _fn_containing(tree, "No download button found")
    assert fn is not None, "the no-download-button path is gone -- re-derive"
    assert _calls(fn, "page_media_verdict") == 1, (
        "%s decides the page has no download control without ever ASKING the "
        "page whether it carries any media at all" % fn.name)
    assert _names(fn, "NO_VIDEO_STATE") == 3, (
        "%s does not name the no-video state in its operator message, its "
        "stderr line and its history row" % fn.name)


def test_the_no_dl_event_hint_names_which_of_the_two_states_it_is():
    tree = ast.parse((ROOT / "bulk_downloader" / "runner_transport.py")
                     .read_text(encoding="utf-8"))
    fn = _fn_containing(tree, "no dl event")
    assert fn is not None, "the 'no dl event' message is gone -- re-derive"
    assert _calls(fn, "page_media_verdict") == 1, (
        "%s cannot tell a page with no video from a control that did not "
        "fire" % fn.name)
    assert _names(fn, "NO_VIDEO_STATE") == 1
    assert _names(fn, "CONTROL_DID_NOT_FIRE_STATE") == 1
    # Regression: row 819's manifest check must still be in the same ladder.
    assert _calls(fn, "is_streaming_url") == 1, (
        "the streaming-manifest hint lost its call to is_streaming_url")
