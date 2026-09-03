"""`quality_preference=best` refused a 4K scene as "best is 240p".

MEASURED on test6 2026-09-03 (campaign evidence
`campaign/test6/teenmegaworld/9/{record.json,verdict.md,page.png}`), site
teenmegaworld, page
``https://members.teenmegaworld.net/scenes/Winning-and-fucking-on-the-floor_vids.html``
with ``quality_preference=best`` and ``min_resolution=720``.  The queue row
terminated ``needs_review``:

    Best is 240p (below 720p) - Approve to force. Saw:
      240p(?):Winning and fucking on the flo | 4K(3.2 GB):4K UHD 2160p 3.24 GB
      https://r | 4K(?):Ultra HD https://members.teenm | 4K(?):4K 2160p4K 2160
      | 4K(?):4K UHD | 4K(?):2160p

The rendered page (`page.png`) shows the scene's own Download control, an
`Ultra HD` tag and a 205-photo button; the recorded candidate list carries five
`royalcontentstore` tiers whose hrefs name
`Creampie-Angels_Tiny_Teen_3840x2160.mp4` down to `_640x360.mp4`.

THE MECHANISM IS THE `work` STAMP, NOT THE PARSER AND NOT A FILTER.

* The `seen` list is `best["_all_candidates"][:6]` in the order
  `find_best_download` sorted them: `(work, score, size)` DESCENDING with
  `work` -- only ever 0 or 1 -- as the LEADING key (`detect.py:1573`).  A
  candidate scoring 240 can therefore stand ahead of five scoring 2160 in
  exactly one way: it read `work == 1` and they read `work == 0`.
* The parser is exonerated by the same line: `res_label(c["score"])` printed
  `4K` for all five tiers, so every 4K label had already been scored 2160.
  `test_every_visible_4k_label_is_scored_and_ranked_as_4k` re-measures that.
* No filter dropped the 4K rows: they are IN the printed candidate list.

Of the 40 recorded candidates exactly one URL carries the page's own slug and
is not a media file: the 205-photo button,
``.../scenes/Winning-and-fucking-on-the-floor_highres.html`` -- an HTML
DOCUMENT.  ``work_affinity`` reads its slug, returns 1, and a navigation link
then outranks every real tier; `_apply_quality_preference` narrows to the
same-work subset FIRST, so `best` cannot reach past it either.

The exact text token that scored that element on the live page is UNKNOWN --
the harness recorded only `text[:30]` ("205"); the recorded refusal message
names it the 240p leader, so the fixture reconstructs a 240p token and asserts
the arithmetic rather than claiming the live token.  The fixture therefore
carries an explicit `240p` token and ASSERTS the arithmetic it needs (the
document link scores below every tier) rather than claiming a provenance the
record does not contain.

The correction is at the CANDIDATE stamp (`_candidate_work_affinity`), not in
the pure `work_affinity` -- which `_is_navigation_resolution_ghost`
(`detect.py:1072`, row 499) also consumes -- and not in
`_apply_quality_preference`, whose same-work contract is pinned by
`tests/test_row388_a_link_belongs_to_the_scene_on_the_page.py` and is re-pinned
here so this cut provably does not weaken it.
"""

# The gate parses a module-level ASSIGNMENT, not a docstring line.
BD_GATE_SCOPE = "module"

from contextlib import contextmanager

import pytest

from bulk_downloader.detect import (
    _candidate_work_affinity,
    find_best_download,
    page_work_tokens,
    res_label,
    res_score,
    work_affinity,
)

# ── The measured strings, re-hosted on .example with zero-entropy signing ──
_PAGE = ("https://members.teenmegaworld.example/scenes/"
         "Winning-and-fucking-on-the-floor_vids.html")
# The 205-photo button: the page's own slug, on the page's own host, naming an
# HTML DOCUMENT rather than a media resource.
_DOC = ("https://members.teenmegaworld.example/scenes/"
        "Winning-and-fucking-on-the-floor_highres.html")
# The signed CDN path exactly as recorded, with every signature segment
# replaced by a zero-entropy constant (A4: security fixtures carry documented
# zero-entropy values).
_CDN = ("https://royalcontentstore.example/"
        "key=00000000000000000000000000000000,end=0000000000,ip=0.0.0.0/"
        "speed=0/buffer=1.0/download2=Creampie-Angels_Tiny_Teen_%s.mp4/"
        "ca/20190403_ca_tiny_teen/%sp/video.mp4")

# (WxH in the CDN filename, tier segment, operator-visible label, height)
_TIERS = [
    ("3840x2160", "2160", "4K UHD 2160p 3.24 GB", 2160),
    ("1920x1080", "1080", "FHD 1080p 1.75 GB", 1080),
    ("1280x720", "720", "HD 720p 1.2 GB", 720),
    ("854x480", "480", "SD 480p 534.43 MB", 480),
    ("640x360", "360", "LQ 360p 304.21 MB", 360),
]


def _tier_anchors(tiers):
    return "".join(
        '<a class="download-element d-flex" href="%s">%s</a>'
        % (_CDN % (wxh, seg), label)
        for wxh, seg, label, _h in tiers)


def _scene_page(tiers):
    """The teenmegaworld shape: an actions bar carrying the same-slug photo
    button, and a download list carrying the CDN tiers."""
    return (
        "<!doctype html><html><body>"
        '<h1>Winning and fucking on the floor</h1>'
        '<div class="video-actions">'
        '<a class="btn btn-reset video-actions-button" href="%s">'
        'Winning and fucking on the floor 240p</a>'
        "</div>"
        '<div class="download-element-list">%s</div>'
        "</body></html>" % (_DOC, _tier_anchors(tiers)))


_FIX_FULL_MENU = _scene_page(_TIERS)
# A genuinely low-quality page: the ONLY media tier really is 240p, so the
# min-resolution refusal must survive this cut unchanged.
_FIX_REALLY_240 = _scene_page(
    [("426x240", "240", "LQ 240p 96.10 MB", 240)])


class _AttrEl:
    """The only thing `_candidate_work_affinity` asks of an element."""

    def __init__(self, **attrs):
        self._attrs = attrs
        self.reads = 0

    def get_attribute(self, name):
        self.reads += 1
        return self._attrs.get(name)


@contextmanager
def _page(url, html):
    """Serve `html` AT `url` without touching the network.

    `set_content` would leave page.url == about:blank and the page's own URL is
    the identity signal under test.  Every host is `.example`, so a missed
    route can never reach a real members domain (A6)."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch()
        try:
            pg = br.new_page(viewport={"width": 1400, "height": 900})
            pg.route("**/*", lambda route: route.fulfill(
                status=200, content_type="text/html", body=html))
            pg.goto(url, wait_until="load")
            assert pg.url == url, (
                "route interception did not serve the fixture at its own URL; "
                "got %r" % pg.url)
            yield pg
        finally:
            br.close()


def _runner():
    from bulk_downloader.runner import SiteRunner
    return SiteRunner.__new__(SiteRunner)


def _href(candidate_or_best):
    loc = candidate_or_best.get("locator")
    if loc is None:
        return ""
    try:
        return loc.get_attribute("href") or ""
    except Exception:
        return ""


# ── Preconditions: the recorded arithmetic is real, not assumed ────────────
def test_the_recorded_labels_outscore_the_document_link():
    """Every tier the operator can see scores above the 240 the runner chose,
    so nothing about this defect is a scoring failure."""
    assert res_score("Winning and fucking on the floor 240p") == 240
    heights = [res_score(label) for _w, _s, label, _h in _TIERS]
    assert heights == [h for _w, _s, _l, h in _TIERS], heights
    assert min(heights) > 240


def test_the_document_link_is_the_only_same_slug_candidate_url():
    """The page slug is in the photo button's href and in NONE of the five
    CDN hrefs -- which is why the stamp singles that one link out."""
    assert page_work_tokens(_PAGE) == (
        "winning", "and", "fucking", "on", "the", "floor", "vids")
    assert work_affinity(_PAGE, _DOC) == 1, (
        "precondition lost: the fixture's photo button no longer shares the "
        "page slug, so the defect cannot be reproduced")
    for wxh, seg, _label, _h in _TIERS:
        assert work_affinity(_PAGE, _CDN % (wxh, seg)) == 0


# ── The seam that must change: the candidate stamp ────────────────────────
def test_a_document_url_is_not_evidence_that_a_candidate_is_the_work():
    """A link to another HTML page names the same WORK and is not a DOWNLOAD
    of it.  Stamping it 1 puts a navigation link ahead of every real tier."""
    el = _AttrEl(href=_DOC)
    assert _candidate_work_affinity(el, _PAGE) == 0, (
        "the 205-photo button (an .html document on the page's own slug) "
        "claimed to be the work's download")
    assert el.reads > 0, "the stamp never read an attribute -- vacuous"


def test_the_pure_predicate_is_untouched_so_row_499_and_dedup_are_untouched():
    """`work_affinity` is also read by `_is_navigation_resolution_ghost`
    (detect.py:1072) to PROTECT a same-work link from chrome deletion.  The
    narrowing belongs to the candidate stamp alone."""
    assert work_affinity(_PAGE, _DOC) == 1


def test_a_media_url_on_the_page_slug_still_stamps_the_work():
    """The narrowing may not become a blanket zero: row 388's whole subject is
    a candidate whose MEDIA url names the page's work."""
    own = ("https://cdn.nubilefilms.example/exclusive/"
           "seeing_red_with_octavia_red/videos/nubilefilms_seeing_red_3840.mp4")
    page = ("https://members.nubilefilms.example/video/watch/254796/"
            "seeing-red-s50e30")
    assert _candidate_work_affinity(_AttrEl(href=own), page) == 1


def test_a_media_attribute_beside_a_document_href_still_stamps_the_work():
    """Only the DOCUMENT value is disqualified, not the element."""
    own = ("https://cdn.teenmegaworld.example/vids/"
           "Winning-and-fucking-on-the-floor_3840x2160.mp4")
    el = _AttrEl(href=_DOC, **{"data-href": own})
    assert _candidate_work_affinity(el, _PAGE) == 1


@pytest.mark.parametrize("value", [
    "https://members.teenmegaworld.example/scenes/"
    "Winning-and-fucking-on-the-floor_highres.htm",
    "https://members.teenmegaworld.example/scenes/"
    "Winning-and-fucking-on-the-floor_highres.php",
    "https://members.teenmegaworld.example/scenes/"
    "Winning-and-fucking-on-the-floor_highres.aspx",
    "/scenes/Winning-and-fucking-on-the-floor_highres.html?tab=1",
    "https://members.teenmegaworld.example/scenes/"
    "Winning-and-fucking-on-the-floor_highres.html#top",
])
def test_every_document_spelling_of_the_same_link_is_refused(value):
    assert _candidate_work_affinity(_AttrEl(href=value), _PAGE) == 0


@pytest.mark.parametrize("value", [
    # a script-served download: the last segment ends in .php but the query
    # names the work's media file -- it IS the work (shape lens, 2026-09-03)
    "https://cdn.teenmegaworld.example/dl.php?file="
    "Winning-and-fucking-on-the-floor_3840x2160.mp4",
    "/download.aspx?f=Winning-and-fucking-on-the-floor_1920x1080.mp4&x=1",
    # a signed-download query key on a page-extension path
    "https://cdn.teenmegaworld.example/get.php?token=0000000000000000&file="
    "Winning-and-fucking-on-the-floor_3840x2160.mp4",
])
def test_a_script_served_download_naming_the_media_still_stamps_the_work(value):
    """The false side of the document veto must not be silent: a URL that
    carries media evidence anywhere is never a document, whatever its path
    extension says."""
    assert _candidate_work_affinity(_AttrEl(href=value), _PAGE) == 1


# ── The operator's verdict: `best` reaches the 4K tier ────────────────────
def test_best_selects_the_visible_4k_tier_over_the_same_slug_page_link():
    with _page(_PAGE, _FIX_FULL_MENU) as pg:
        best = find_best_download(pg)
        assert best is not None, "every candidate was refused -- OUTAGE"
        cands = best.get("_all_candidates") or []
        # Preconditions: BOTH populations reached the ranking.
        hrefs = [_href(c) for c in cands]
        assert _DOC in hrefs, (
            "the same-slug document link was never a candidate, so this "
            "fixture cannot reproduce the defect; saw %r" % (hrefs,))
        tier_hrefs = [h for h in hrefs if "Creampie-Angels_Tiny_Teen" in h]
        assert len(tier_hrefs) == len(_TIERS), (
            "expected %d CDN tiers among the candidates, saw %d"
            % (len(_TIERS), len(tier_hrefs)))
        doc = [c for c in cands if _href(c) == _DOC][0]
        assert doc["score"] < 2160

        chosen = _runner()._apply_quality_preference(best, "best")
        assert res_score("2160p") == 2160  # the bar `best` must clear
        assert chosen["score"] == 2160, (
            "quality_preference=best chose %s while a 4K tier was on the page"
            % res_label(chosen["score"]))
        assert "3840x2160" in _href(chosen), _href(chosen)


def test_the_unpreferred_winner_is_also_the_4k_tier():
    """`find_best_download` alone must not hand a 240p page link to a caller
    that never sets a preference."""
    with _page(_PAGE, _FIX_FULL_MENU) as pg:
        best = find_best_download(pg)
        assert best is not None
        assert "3840x2160" in _href(best), _href(best)


def test_a_genuinely_240p_page_is_still_refused_with_the_same_message():
    """NEGATIVE CONTROL.  The min-resolution refusal is not what this cut
    changes: when the page really carries nothing above 240p, `best` must
    still land below 720 so runner.py's needs_review arm still fires."""
    with _page(_PAGE, _FIX_REALLY_240) as pg:
        best = find_best_download(pg)
        assert best is not None
        chosen = _runner()._apply_quality_preference(best, "best")
        assert chosen["score"] == 240, chosen["score"]
        assert res_label(chosen["score"]) == "240p"
        assert chosen["score"] < 720, (
            "the refusal arm at runner.py's min-resolution gate would no "
            "longer fire for a genuinely low-quality page")


# ── Re-pinned: row 388's same-work protection is not weakened ─────────────
def test_a_related_cards_higher_tier_still_loses_to_the_pages_own_tier():
    """`tests/test_row388_...::test_best_keyword_also_respects_the_work`
    verbatim in shape: a work=1 1080 must still beat a work=0 2160."""
    own = {"score": 1080, "size": 0, "work": 1, "locator": object()}
    rel = {"score": 2160, "size": 0, "work": 0, "locator": object()}
    best = {"score": 1080, "locator": object(), "work": 1,
            "_all_candidates": [rel, own]}
    assert _runner()._apply_quality_preference(best, "best") is own


# ── Control: the label vocabulary was never the mechanism ─────────────────
_LABELS = [
    ("Ultra HD", 2160, "4K"),
    ("4K", 2160, "4K"),
    ("4K UHD", 2160, "4K"),
    ("2160p", 2160, "4K"),
    ("3840x2160", 2160, "4K"),
    ("4K UHD 2160p 3.24 GB", 2160, "4K"),
    ("FHD 1080p 1.75 GB", 1080, "1080p"),
    ("1920x1080", 1080, "1080p"),
    ("HD 720p 1.2 GB", 720, "720p"),
    ("SD 480p 534.43 MB", 480, "480p"),
    ("LQ 360p 304.21 MB", 360, "360p"),
    ("240p", 240, "240p"),
]


@pytest.mark.parametrize("text,height,label", _LABELS,
                         ids=[t for t, _h, _l in _LABELS])
def test_every_visible_4k_label_is_scored_and_ranked_as_4k(text, height, label):
    """Every rung the operator can read on the recorded page, scored.  This is
    a CONTROL, green before and after the fix: it refutes "the parser could not
    see Ultra HD" as the cause of the 240p pick."""
    assert res_score(text) == height
    assert res_label(res_score(text)) == label


def test_the_label_table_ranks_the_four_k_rungs_above_every_other():
    ranked = sorted(_LABELS, key=lambda row: res_score(row[0]), reverse=True)
    assert [t for t, _h, _l in ranked][:6] == [
        t for t, h, _l in _LABELS if h == 2160]
    assert len({h for _t, h, _l in _LABELS}) == 6, "the table lost a rung"
