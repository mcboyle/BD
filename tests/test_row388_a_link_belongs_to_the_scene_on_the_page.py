"""A download link must belong to the SCENE ON THE PAGE, not to a related card.

MEASURED on test6 2026-08-29 at v3.66.1346, live and read-only, against
https://members.nubilefilms.com/video/watch/254796/seeing-red-s50e30 .  The page
carries 159 media links; SIX of them are the requested work.  Running
``find_best_download`` against that live page returned:

  WINNER  score=2160  size=5368709120
    href=.../exclusive/new_years_with_my_ex_with_octavia_red/videos/
         nfbusty_new_years_with_my_ex_3840.mp4
  ...and candidates 1-10 were ALL related scenes, every one of them
  score=2160 with the identical label text '3840x2160 4K MP4 (5 GB)'.

The requested scene's OWN 4K tier was not in the top ten at all:

  a[href*='nubilefilms_seeing_red']  score=2160  size=3221225472  '4K MP4 (3 GB)'

So this is NOT a scoring failure.  Every related card publishes a full download
menu with the same 4K label, the score TIES at 2160, and ``(score, size)`` then
resolves the tie toward whichever scene on the page happens to have the biggest
file -- a related one.  History row 121 read `done`, library row 103 read
'Nubile Films - Seeing Red - S50:E30', and 5,102,802,950 bytes of a different
scene were on disk.  Nothing in the ranking ever asked whether the candidate and
the page name the same work.

The second observed page, /254257/we-should-share-your-boyfriends-cum-s51e1, is
the control that must not move: there the scene's own 4K IS the biggest file on
the page (5 GB), so today's ranking already picks correctly and must keep doing
so.  Measured the same way, same host, same hour.

Preconditions are asserted on the arithmetic (the tie is real, the related file
really is larger) rather than on the pre-fix winner, because the sort key itself
is what this cut changes.
"""

# The gate parses a module-level ASSIGNMENT, not a docstring line.
BD_GATE_SCOPE = "module"

from contextlib import contextmanager

import pytest

from bulk_downloader.detect import (
    find_best_download,
    page_work_tokens,
    parse_size_bytes,
    res_score,
    work_affinity,
)

# ── The measured strings ────────────────────────────────────────────────────
# Page URL and hrefs exactly as read off the live pages (signatures elided).
_PAGE_SEEING_RED = ("https://members.nubilefilms.com/video/watch/254796/"
                    "seeing-red-s50e30")
_OWN_SEEING_RED = ("https://content2a.nubilefilms.com/exclusive/"
                   "seeing_red_with_octavia_red/videos/"
                   "nubilefilms_seeing_red_3840.mp4?st=xqXF4Azwovb&e=1788033600")
_RELATED_NEW_YEARS = ("https://content2a.nubilefilms.com/exclusive/"
                      "new_years_with_my_ex_with_octavia_red/videos/"
                      "nfbusty_new_years_with_my_ex_3840.mp4?st=GwVkyK95&e=1788033600")
_RELATED_MEMORIAL = (
    "https://content2a.nubilefilms.com/exclusive/"
    "memorial_day_menage_a_trois_with_octavia_red_scarlett_alexis/videos/"
    "nfbusty_memorial_day_menage_a_trois_3840.mp4?st=3O5m32ON&e=1788033600")

_PAGE_SHARE = ("https://members.nubilefilms.com/video/watch/254257/"
               "we-should-share-your-boyfriends-cum-s51e1")
_OWN_SHARE = ("https://content2a.nubilefilms.com/exclusive/"
              "we_should_share_your_boyfriends_cum_with_gracey_snow_aubree_blair/"
              "videos/nubilefilms_we_should_share_your_boyfriends_cum_3840.mp4")

# Negative control 1: teenmegaworld names the file studio_performer_resolution,
# with nothing of the page slug in it.  Measured 2026-08-29: the page
# 'After-shower satisfaction' correctly saves TeenSexMania_Adell_3840x2160.mp4,
# and a filename audit that called this a mismatch was itself wrong.
_PAGE_TMW = "https://teenmegaworld.example/video/after-shower-satisfaction"
_OWN_TMW = ("https://cdn.teenmegaworld.example/vids/"
            "TeenSexMania_Adell_3840x2160.mp4")

# Negative control 2: wowgirls concatenates the title into CamelCase.
_PAGE_WOW = "https://wowgirls.example/video/dreaming-of-japan"
_OWN_WOW = "https://cdn.wowgirls.example/dl/DreamingOfJapan_7680x4320_60FPS.mp4"


# ── Unit: the pure function over the measured string pairs ─────────────────
class TestWorkAffinityIsPureOverTwoStrings:
    def test_the_page_and_its_own_file_are_the_same_work(self):
        """The stem 'seeing red' is present in both strings."""
        assert page_work_tokens(_PAGE_SEEING_RED) == ("seeing", "red", "s50e30")
        assert work_affinity(_PAGE_SEEING_RED, _OWN_SEEING_RED) == 1

    def test_a_related_card_on_the_same_page_is_not_the_same_work(self):
        """Both related hrefs carry 'octavia_red', so a bag-of-words overlap
        would call them a match on 'red'.  A CONTIGUOUS run may not."""
        assert "red" in _RELATED_NEW_YEARS and "red" in _RELATED_MEMORIAL
        assert work_affinity(_PAGE_SEEING_RED, _RELATED_NEW_YEARS) == 0
        assert work_affinity(_PAGE_SEEING_RED, _RELATED_MEMORIAL) == 0

    def test_the_second_observed_page_matches_its_own_file(self):
        assert work_affinity(_PAGE_SHARE, _OWN_SHARE) == 1
        # ...and does not match the other page's scene.
        assert work_affinity(_PAGE_SHARE, _OWN_SEEING_RED) == 0
        assert work_affinity(_PAGE_SEEING_RED, _OWN_SHARE) == 0

    def test_a_studio_named_file_yields_no_identity_rather_than_a_refusal(self):
        """teenmegaworld: NOTHING of the page slug is in the file name, so no
        candidate can be shown to belong to the page.  That must read 0
        (unknown, rank unchanged) for the right file too -- the check may not
        single the correct file out as wrong."""
        assert page_work_tokens(_PAGE_TMW) == ("after", "shower", "satisfaction")
        assert work_affinity(_PAGE_TMW, _OWN_TMW) == 0

    def test_a_concatenated_camelcase_title_is_the_same_work(self):
        """wowgirls: page dreaming-of-japan, file DreamingOfJapan..."""
        assert work_affinity(_PAGE_WOW, _OWN_WOW) == 1

    def test_no_identity_is_derivable_from_a_non_http_page(self):
        """Every set_content / about:blank test in this repo lands here, and
        must be provably unaffected."""
        for pu in ("about:blank", "", None, "data:text/html,<b>x</b>",
                   "file:///tmp/x.html"):
            assert page_work_tokens(pu) == ()
            assert work_affinity(pu, _OWN_SEEING_RED) == 0

    def test_a_short_or_absent_candidate_url_is_unknown_not_different(self):
        for cu in ("", None, "#", "javascript:void(0)", "/red/"):
            assert work_affinity(_PAGE_SEEING_RED, cu) == 0

    def test_a_single_shared_token_is_not_enough(self):
        """One token in common is coincidence, not identity."""
        assert work_affinity("https://s.example/v/red-october",
                             "https://cdn.example/red/other_scene.mp4") == 0

    def test_a_numeric_last_segment_falls_back_to_the_named_one(self):
        assert page_work_tokens("https://s.example/scene/seeing-red/254796") == \
            ("seeing", "red")

    def test_the_function_never_raises_on_junk(self):
        for a in (None, "", 7, "http://", "https:///", "http://x/%%%"):
            for b in (None, "", 7, "\x00\x01", "http://" + "a" * 5000):
                assert work_affinity(a, b) in (0, 1)


# ── The fixture DOM ─────────────────────────────────────────────────────────
def _menu(base, tiers):
    """One nubilefilms download menu: <a> per tier, label then size in-anchor."""
    rows = []
    for href_res, label, size in tiers:
        rows.append(
            '<li><a class="dl" href="%s_%s.mp4?st=sig&e=1788033600&dl=file.mp4">'
            '<span class="res">%s</span><br>MP4&nbsp;(%s)</a></li>'
            % (base, href_res, label, size))
    return '<ul class="tiers">%s</ul>' % "".join(rows)


_OWN_TIERS = [("3840", "3840x2160&nbsp;4K", "3&nbsp;GB"),
              ("1920", "1920x1080&nbsp;HD", "1&nbsp;GB"),
              ("1280", "1280x720&nbsp;HD", "740&nbsp;MB")]
_REL_TIERS = [("3840", "3840x2160&nbsp;4K", "5&nbsp;GB"),
              ("1920", "1920x1080&nbsp;HD", "2&nbsp;GB")]

_CDN = "https://content2a.nubilefilms.example/exclusive"

_RELATED_SLUGS = ("new_years_with_my_ex_with_octavia_red/videos/"
                  "nfbusty_new_years_with_my_ex",
                  "memorial_day_menage_a_trois_with_octavia_red_scarlett_alexis/"
                  "videos/nfbusty_memorial_day_menage_a_trois",
                  "christmas_party_passion_with_octavia_red_hailey_rose/videos/"
                  "nfbusty_christmas_party_passion",
                  "october_2023_fantasy_of_the_month_with_octavia_red/videos/"
                  "nubilefilms_october_2023_fantasy_of_the_month")


def _scene_page(own_base, own_tiers, related_bases, related_tiers):
    cards = "".join(
        '<div class="related-card">%s</div>' % _menu(b, related_tiers)
        for b in related_bases)
    return ("<!doctype html><html><body>"
            '<h1>Now Watching</h1>'
            '<section class="scene">%s</section>'
            '<h2>Related Videos</h2><section class="related">%s</section>'
            "</body></html>") % (_menu(own_base, own_tiers), cards)


_FIX_SEEING_RED = _scene_page(
    _CDN + "/seeing_red_with_octavia_red/videos/nubilefilms_seeing_red",
    _OWN_TIERS,
    [_CDN + "/" + s for s in _RELATED_SLUGS],
    _REL_TIERS)

# The control page: the scene's own 4K IS the biggest file here, so today's
# ranking already wins.  Related cards are smaller.
_FIX_SHARE = _scene_page(
    _CDN + "/we_should_share_your_boyfriends_cum_with_gracey_snow_aubree_blair"
           "/videos/nubilefilms_we_should_share_your_boyfriends_cum",
    [("3840", "3840x2160&nbsp;4K", "5&nbsp;GB"),
     ("1920", "1920x1080&nbsp;HD", "2&nbsp;GB")],
    [_CDN + "/" + s for s in _RELATED_SLUGS[:2]],
    [("3840", "3840x2160&nbsp;4K", "4&nbsp;GB")])

# The unknown-identity page: nothing of the slug is in any href, and the
# correct file is simply the biggest.  Ordering must be byte-identical to
# today's, which means the 4K TeenSexMania file still wins.
_FIX_TMW = ("<!doctype html><html><body>"
            '<section class="scene">%s</section>'
            "</body></html>") % _menu(
    "https://cdn.teenmegaworld.example/vids/TeenSexMania_Adell",
    [("3840x2160", "3840x2160&nbsp;4K", "4&nbsp;GB"),
     ("1920x1080", "1920x1080&nbsp;HD", "1&nbsp;GB")])

_URL_SEEING_RED = ("https://members.nf.example/video/watch/254796/"
                   "seeing-red-s50e30")
_URL_SHARE = ("https://members.nf.example/video/watch/254257/"
              "we-should-share-your-boyfriends-cum-s51e1")
_URL_TMW = "https://teenmegaworld.example/video/after-shower-satisfaction"


@contextmanager
def _page(url, html):
    """A page served AT `url` without touching the network.

    ``set_content`` would leave page.url == about:blank, and the whole point of
    this cut is that the page's own URL is the identity signal.  The hosts are
    all `.example` on purpose: if route interception ever missed, a real
    members domain would send a formal test at an authenticated site.
    """
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


def _hrefs(best):
    out = []
    for c in (best.get("_all_candidates") or []):
        loc = c.get("locator")
        h = ""
        if loc is not None:
            try:
                h = loc.get_attribute("href") or ""
            except Exception:
                h = ""
        out.append(h)
    return out


def _winner_href(best):
    return best["locator"].get_attribute("href") or ""


# ── Preconditions: the defect's arithmetic is real, not assumed ────────────
def test_the_tie_and_the_size_inversion_are_real():
    own = "3840x2160\xa04K\nMP4 (3\xa0GB) " + _OWN_SEEING_RED
    rel = "3840x2160\xa04K\nMP4 (5\xa0GB) " + _RELATED_NEW_YEARS
    assert res_score(own) == res_score(rel) == 2160, (
        "the related card must TIE the scene's own tier on score, or (score, "
        "size) would never have reached the size tiebreaker")
    assert parse_size_bytes(rel) > parse_size_bytes(own) > 0, (
        "the related file must be LARGER, or the tiebreaker would already "
        "have picked correctly")
    assert (res_score(rel), parse_size_bytes(rel)) > \
           (res_score(own), parse_size_bytes(own)), (
        "today's exact sort key must rank the related card first")


def test_the_fixture_presents_both_works_to_the_wide_sweep():
    """Precondition on the DOM and on the harvest, asserted independently of
    which one wins -- so it stays meaningful after the fix."""
    with _page(_URL_SEEING_RED, _FIX_SEEING_RED) as pg:
        own = pg.locator("a[href*='nubilefilms_seeing_red_3840']")
        rel = pg.locator("a[href*='nfbusty_new_years_with_my_ex_3840']")
        assert own.count() == 1 and rel.count() == 1
        assert own.first.is_visible() and rel.first.is_visible()

        best = find_best_download(pg, "", learned=None, runner=None)
        assert best is not None, "no candidate at all -- the fixture is wrong"
        hrefs = _hrefs(best)
        assert any("nubilefilms_seeing_red_3840" in h for h in hrefs), (
            "the scene's own 4K tier was never harvested; hrefs=%r" % hrefs)
        assert any("nfbusty_new_years_with_my_ex_3840" in h for h in hrefs), (
            "the related 4K tier was never harvested; hrefs=%r" % hrefs)


# ── RED on v3.66.1346 ──────────────────────────────────────────────────────
def test_the_winner_belongs_to_the_scene_on_the_page():
    """RED before this cut: the winner is a RELATED scene's 4K file."""
    with _page(_URL_SEEING_RED, _FIX_SEEING_RED) as pg:
        best = find_best_download(pg, "", learned=None, runner=None)
        assert best is not None
        href = _winner_href(best)
        assert "seeing_red_with_octavia_red" in href, (
            "the winner names a DIFFERENT WORK than the page "
            "(seeing-red-s50e30); href=%s" % href)
        assert best.get("score") == 2160, (
            "and it must still be the 4K tier, not a demotion to a lower one")


def test_every_same_work_candidate_outranks_every_other_work_candidate():
    with _page(_URL_SEEING_RED, _FIX_SEEING_RED) as pg:
        best = find_best_download(pg, "", learned=None, runner=None)
        cands = best.get("_all_candidates") or []
        assert cands, "empty candidate list cannot prove an ordering"
        works = [c.get("work", 0) for c in cands]
        assert works.count(1) >= 3, (
            "the scene's own three tiers must all be recognised; works=%r "
            "hrefs=%r" % (works, _hrefs(best)))
        assert works == sorted(works, reverse=True), (
            "a candidate of unknown work sorted above a same-work one: "
            "works=%r" % works)


# ── Negative control: the page whose winner was ALREADY correct ────────────
def test_the_already_correct_page_stays_correct():
    with _page(_URL_SHARE, _FIX_SHARE) as pg:
        best = find_best_download(pg, "", learned=None, runner=None)
        assert best is not None
        href = _winner_href(best)
        assert "we_should_share_your_boyfriends_cum" in href, href
        assert best.get("score") == 2160
        assert best.get("size") == parse_size_bytes("5 GB")


# ── Negative control: no identity anywhere -> today's ordering, unchanged ──
def test_an_underivable_page_falls_back_to_todays_ordering():
    """teenmegaworld shape.  Refusing every candidate here would turn one
    wrong file into a total outage; the correct answer is to rank nothing and
    let (score, size) decide exactly as it does today."""
    with _page(_URL_TMW, _FIX_TMW) as pg:
        best = find_best_download(pg, "", learned=None, runner=None)
        assert best is not None, "every candidate was refused -- OUTAGE"
        cands = best.get("_all_candidates") or []
        assert cands
        assert all(c.get("work", 0) == 0 for c in cands), (
            "no identity is derivable on this page, so nothing may claim one")
        href = _winner_href(best)
        assert "TeenSexMania_Adell_3840x2160" in href, (
            "the biggest/highest tier must still win; href=%s" % href)


# ── The second consumer: an explicit quality preference ────────────────────
class TestQualityPreferenceRespectsTheWork:
    def _runner(self):
        from bulk_downloader.runner import SiteRunner
        return SiteRunner.__new__(SiteRunner)

    def test_preference_picks_the_pages_own_tier_not_a_related_one(self):
        own = {"score": 1080, "size": 1073741824, "work": 1,
               "locator": object(), "text": "own 1080"}
        rel = {"score": 1080, "size": 2147483648, "work": 0,
               "locator": object(), "text": "related 1080"}
        best = {"score": 2160, "locator": object(), "work": 1,
                "_all_candidates": [rel, own]}
        chosen = self._runner()._apply_quality_preference(best, "1080")
        assert chosen is own, (
            "the preference selected a candidate belonging to another work")

    def test_best_keyword_also_respects_the_work(self):
        own = {"score": 1080, "size": 0, "work": 1, "locator": object()}
        rel = {"score": 2160, "size": 0, "work": 0, "locator": object()}
        best = {"score": 1080, "locator": object(), "work": 1,
                "_all_candidates": [rel, own]}
        chosen = self._runner()._apply_quality_preference(best, "best")
        assert chosen is own

    def test_candidates_without_work_keys_behave_exactly_as_today(self):
        """Every existing caller and test builds candidates with no `work`
        key at all.  Those must not be reinterpreted."""
        c_2160 = {"score": 2160, "size": 0, "locator": object()}
        c_1080 = {"score": 1080, "size": 0, "locator": object()}
        best = {"score": 0, "locator": None,
                "_all_candidates": [c_1080, c_2160]}
        r = self._runner()
        assert r._apply_quality_preference(best, "best") is c_2160
        assert r._apply_quality_preference(best, "1080") is c_1080

    def test_a_preference_with_no_same_work_match_falls_back_to_best(self):
        """The page's own tiers stop at 1080; the operator asked for 2160 and
        only a related card has one.  Fall back to `best` (the same-work
        winner) rather than downloading the wrong scene."""
        own = {"score": 1080, "size": 0, "work": 1, "locator": object()}
        rel = {"score": 2160, "size": 0, "work": 0, "locator": object()}
        best = {"score": 1080, "locator": object(), "work": 1,
                "_all_candidates": [rel, own]}
        chosen = self._runner()._apply_quality_preference(best, "2160")
        assert chosen is best, (
            "a 2160 preference reached across to another work; got %r" % chosen)
