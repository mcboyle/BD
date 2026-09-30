"""A quality-PINNED learned row selector must not cap the pick below quality_preference.

MEASURED on test1 2026-09-30 (T175 final sweep, results/test1/SWEEP-T175.md row dfxtra, evidence
results/test1/dfxtra_T175_evidence-C1-C.json): dfxtra scene 291197 saved
``House Dick Pleasures [1080p]_1.mp4`` 1920x1080; the prior PASS (T165) was ``[4K].mp4`` 3840x2160.
The site's quality_preference is ``4320,3160,2880,2160,1440,1080,720``.  The applied O1517 template
(bulk_downloader/site_templates/_data_learned_o1517.py, dfxtra) teaches two row selectors:

    [0] a.VideoJSPlayer-DownloadOption-Link[href*='/movieaction/download/'][href*='/1080p/']
    [1] a.VideoJSPlayer-DownloadOption-Link[href*='/movieaction/download/']

App log: ``learned hit via [...][href*='/1080p/']``.  find_best_download's learned pass takes the FIRST
selector group holding an in-scope row and keeps only that group's rows in ``_all_candidates``, so the
4K row -- matched by selector [1] -- never reached _apply_quality_preference, which can only choose
among ``_all_candidates``.  Selector [0] names a strict subset of selector [1]'s rows: it is a
quality pin, not a different control.

Fixture: the dfxtra option list reduced to its load-bearing shape (four movieaction option links).
"""

# The gate parses a module-level ASSIGNMENT, not a docstring line.
BD_GATE_SCOPE = "module"
from contextlib import contextmanager

import pytest

from bulk_downloader.detect import find_best_download
from bulk_downloader.runner_integrity import IntegrityMixin

PINNED = "a.VideoJSPlayer-DownloadOption-Link[href*='/movieaction/download/'][href*='/1080p/']"
GENERIC = "a.VideoJSPlayer-DownloadOption-Link[href*='/movieaction/download/']"
DFXTRA_QPREF = "4320,3160,2880,2160,1440,1080,720"

_FIX = """<!doctype html><html><body>
<div class="VideoJSPlayer-DownloadOptions">
  <a class="VideoJSPlayer-DownloadOption-Link" href="https://members.example/movieaction/download/291197/2160p/mp4?codec=h264">4K 2160p 4.74 GB</a>
  <a class="VideoJSPlayer-DownloadOption-Link" href="https://members.example/movieaction/download/291197/1080p/mp4?codec=h264">1080p 1.28 GB</a>
  <a class="VideoJSPlayer-DownloadOption-Link" href="https://members.example/movieaction/download/291197/720p/mp4?codec=h264">720p 640 MB</a>
  <a class="VideoJSPlayer-DownloadOption-Link" href="https://members.example/movieaction/download/291197/480p/mp4?codec=h264">480p 310 MB</a>
</div>
<div class="bonus">
  <a class="other-download" href="https://members.example/bonus/291197/2160p/extras.mp4">Bonus 2160p 6.10 GB</a>
  <a class="other-download" href="https://members.example/bonus/291197/1080p/bts.mp4">Bonus 1080p 2.40 GB</a>
</div>
</body></html>
"""


# Lens D1-D probe G: a learned BROAD row after the generic one also reaches the bonus links.
BROAD = "a[href*='/291197/']"
# Same kind of control as the pin, but NOT a superset of its rows.
SAME_KIND_DISJOINT = "a.VideoJSPlayer-DownloadOption-Link:not([href*='/1080p/'])"
DUP_OF_PIN = "a[href*='/movieaction/download/291197/1080p/']"
TWO_TIERS = ("a.VideoJSPlayer-DownloadOption-Link[href*='/2160p/'], "
             "a.VideoJSPlayer-DownloadOption-Link[href*='/1080p/']")

# A real scene URL so work affinity is measured: the slug-bearing option links are IN SCOPE (1), the
# other scene's link is refused once the page proves affinity (measured with _candidate_work_affinity).
SCENE_URL = "https://members.example/en/video/blacksonblondes/House-Dick-Pleasures/291197"
_OPT = ('<a class="VideoJSPlayer-DownloadOption-Link" '
        'href="/en/video/blacksonblondes/{slug}/{sid}/download/{tier}/mp4">{label}</a>')
_MINE_1080 = _OPT.format(slug="House-Dick-Pleasures", sid=291197, tier="1080p", label="1080p 1.28 GB")
_MINE_2160 = _OPT.format(slug="House-Dick-Pleasures", sid=291197, tier="2160p", label="4K 2160p 4.74 GB")
_OTHER_2160 = _OPT.format(slug="Pool-Party-Surprise", sid=288000, tier="2160p", label="4K 2160p 6.10 GB")


SCENE_PIN = "a.VideoJSPlayer-DownloadOption-Link[href*='/1080p/']"
SCENE_ALL = "a.VideoJSPlayer-DownloadOption-Link"


def _scene(*rows):
    return ('<!doctype html><html><body><div class="VideoJSPlayer-DownloadOptions">'
            + "".join(rows) + "</div></body></html>")


@contextmanager
def _page(html=_FIX, url=None):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch()
        try:
            pg = br.new_page(viewport={"width": 1400, "height": 900})
            if url:
                pg.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=html))
                pg.goto(url, wait_until="load")
            else:
                pg.set_content(html, wait_until="load")
            yield pg
        finally:
            br.close()


def _href(cand):
    return cand["locator"].get_attribute("href") or ""


def _learned(*sels):
    return {"row_selectors": list(sels), "url_attribute": "href"}


def _tiers(best):
    return sorted(_href(c).split("/")[-2] for c in best.get("_all_candidates", []))


def test_pinned_selector_really_is_a_strict_subset_of_the_generic_one():
    """Precondition: the measured selectors name 1 and 4 rows of the same list."""
    with _page() as pg:
        assert pg.locator(PINNED).count() == 1
        assert pg.locator(GENERIC).count() == 4
        assert "/2160p/" in (pg.locator(GENERIC).first.get_attribute("href") or "")


def test_pinned_first_selector_does_not_hide_the_4k_row():
    with _page() as pg:
        best = find_best_download(pg, learned=_learned(PINNED, GENERIC))
        assert best and best.get("_via_learned"), f"no learned pick: {best!r}"
        assert "2160p" in _tiers(best), (
            f"QUALITY-PIN: learned candidates {_tiers(best)} via [{best.get('_learned_sel')}] "
            "omit the 2160p row selector [1] matched")
        assert "/2160p/" in _href(best), f"QUALITY-PIN: learned pick is {_href(best)}"


def test_dfxtra_quality_preference_gets_2160p():
    with _page() as pg:
        best = find_best_download(pg, learned=_learned(PINNED, GENERIC))
        chosen = IntegrityMixin._apply_quality_preference(None, best, DFXTRA_QPREF)
        assert "/2160p/" in _href(chosen), (
            f"QUALITY-PIN: preference {DFXTRA_QPREF} chose {_href(chosen)} from {_tiers(best)}")


def test_negative_control_a_1080_preference_still_gets_1080p():
    """Widening the population must not override an explicit lower preference."""
    with _page() as pg:
        best = find_best_download(pg, learned=_learned(PINNED, GENERIC))
        chosen = IntegrityMixin._apply_quality_preference(None, best, "1080,720")
        assert "/1080p/" in _href(chosen), _href(chosen)


def test_negative_control_a_pin_with_no_broader_selector_is_unchanged():
    with _page() as pg:
        best = find_best_download(pg, learned=_learned(PINNED))
        assert best and best.get("_learned_sel") == PINNED
        assert _tiers(best) == ["1080p"], _tiers(best)


def test_negative_control_a_different_control_does_not_replace_the_winner():
    """Two bonus rows (lens D1-D R1): the width check alone cannot refuse them."""
    with _page() as pg:
        assert pg.locator("a.other-download").count() == 2
        best = find_best_download(pg, learned=_learned(PINNED, "a.other-download"))
        assert best and best.get("_learned_sel") == PINNED, (
            f"DISJOINT-REPLACED: {best.get('_learned_sel')} {_href(best)}")
        assert "/1080p/" in _href(best), _href(best)


def test_negative_control_a_same_kind_selector_that_is_not_a_superset_is_refused():
    """Same option links, but the pinned row is missing: not the pin removed (kills a dropped subset check)."""
    with _page() as pg:
        assert pg.locator(SAME_KIND_DISJOINT).count() == 3
        best = find_best_download(pg, learned=_learned(PINNED, SAME_KIND_DISJOINT))
        assert best and best.get("_learned_sel") == PINNED, (
            f"NOT-A-SUPERSET-REPLACED: {best.get('_learned_sel')} {_href(best)}")


def test_negative_control_a_selector_naming_the_same_rows_does_not_replace_the_winner():
    """Equal row sets are not wider (kills < -> <= with > -> >=)."""
    with _page() as pg:
        assert pg.locator(DUP_OF_PIN).count() == 1
        best = find_best_download(pg, learned=_learned(PINNED, DUP_OF_PIN))
        assert best and best.get("_learned_sel") == PINNED, best.get("_learned_sel")


def test_probe_g_a_broad_selector_reaching_the_bonus_links_is_never_the_widening():
    """Lens D1-D probe G: the broadest superset also holds the 6.10 GB bonus file -- not the scene."""
    with _page() as pg:
        best = find_best_download(pg, learned=_learned(PINNED, GENERIC, BROAD))
        chosen = IntegrityMixin._apply_quality_preference(None, best, DFXTRA_QPREF)
        assert best.get("_learned_sel") == GENERIC, f"WRONG-CONTROL: widened to {best.get('_learned_sel')}"
        assert "/movieaction/download/291197/2160p/" in _href(chosen), f"WRONG-FILE: {_href(chosen)}"


def test_probe_g_without_the_generic_row_the_pin_stands():
    with _page() as pg:
        best = find_best_download(pg, learned=_learned(PINNED, BROAD))
        assert best.get("_learned_sel") == PINNED, f"WRONG-CONTROL: widened to {best.get('_learned_sel')}"
        assert "/movieaction/download/291197/1080p/" in _href(best), _href(best)


def test_the_broadest_same_control_superset_lets_the_preference_decide():
    """Kills first-superset-wins: only the generic row offers 720p."""
    with _page() as pg:
        assert pg.locator(TWO_TIERS).count() == 2
        best = find_best_download(pg, learned=_learned(PINNED, TWO_TIERS, GENERIC))
        chosen = IntegrityMixin._apply_quality_preference(None, best, "720,1080")
        assert best.get("_learned_sel") == GENERIC, best.get("_learned_sel")
        assert "/720p/" in _href(chosen), f"QUALITY-PIN: preference 720 chose {_href(chosen)}"


def test_the_wider_groups_refused_rows_stay_recorded():
    """The other scene's link in the widened group is refused and must be RECORDED, not dropped."""
    with _page(_scene(_MINE_2160, _MINE_1080, _OTHER_2160), url=SCENE_URL) as pg:
        best = find_best_download(pg, learned=_learned(SCENE_PIN, SCENE_ALL))
        assert best.get("_learned_sel") == SCENE_ALL, best.get("_learned_sel")
        assert "/291197/download/2160p/" in _href(best), _href(best)
        refused = [c.get("text", "") for c in best.get("_excluded_candidates", [])]
        assert any("Pool-Party-Surprise" in t for t in refused), f"EXCLUDED-LOST: {refused}"


def test_refused_rows_do_not_make_a_group_wider():
    """In scope the generic group is exactly the pinned row: the other scene's refused link must not widen it."""
    with _page(_scene(_MINE_1080, _OTHER_2160), url=SCENE_URL) as pg:
        best = find_best_download(pg, learned=_learned(SCENE_PIN, SCENE_ALL))
        assert best.get("_learned_sel") == SCENE_PIN, f"UNSCOPED-WIDENED: {best.get('_learned_sel')}"
        assert "/291197/download/1080p/" in _href(best), _href(best)


# Rework r3 (bd-worker-A11-A): the guards the r2 battery left unexercised (evidence MUTATIONS-A11.log).
TIER_720 = ("a.VideoJSPlayer-DownloadOption-Link[href*='/720p/'], "
            "a.VideoJSPlayer-DownloadOption-Link[href*='/1080p/']")
SAME_CLASS_BONUS = _FIX.replace(
    '<a class="other-download" href="https://members.example/bonus/291197/2160p/extras.mp4">',
    '<a class="VideoJSPlayer-DownloadOption-Link" href="https://members.example/bonus/291197/2160p/extras.mp4">')


def test_equally_broad_supersets_keep_the_template_order():
    """Two same-control supersets of equal width: the first taught one wins (kills > -> >=)."""
    with _page() as pg:
        assert pg.locator(TWO_TIERS).count() == pg.locator(TIER_720).count() == 2
        best = find_best_download(pg, learned=_learned(PINNED, TWO_TIERS, TIER_720))
        chosen = IntegrityMixin._apply_quality_preference(None, best, DFXTRA_QPREF)
        assert best.get("_learned_sel") == TWO_TIERS, f"TIE-REORDERED: {best.get('_learned_sel')}"
        assert "/movieaction/download/291197/2160p/" in _href(chosen), _href(chosen)


def test_a_same_class_link_under_another_parent_is_not_the_pin_removed():
    """The bonus link wears the option class but sits in div.bonus: the parent is part of the control."""
    with _page(SAME_CLASS_BONUS) as pg:
        assert pg.locator("div.bonus a.VideoJSPlayer-DownloadOption-Link").count() == 1
        best = find_best_download(pg, learned=_learned(PINNED, SCENE_ALL))
        assert best.get("_learned_sel") == PINNED, f"PARENT-IGNORED: widened to {best.get('_learned_sel')}"
        assert "/movieaction/download/291197/1080p/" in _href(best), _href(best)


@pytest.mark.parametrize("unreadable", ["/1080p/", "/720p/"], ids=["winner-row", "later-row"])
def test_an_unreadable_row_never_widens(monkeypatch, unreadable):
    """A row whose DOM identity cannot be read proves nothing: the pin stands (winner row or later row)."""
    from bulk_downloader import detect
    real = detect._dom_node_key
    monkeypatch.setattr(detect, "_dom_node_key",
                        lambda c: None if unreadable in _href(c) else real(c))
    with _page() as pg:
        best = find_best_download(pg, learned=_learned(PINNED, GENERIC))
        assert best.get("_learned_sel") == PINNED, f"UNREADABLE-WIDENED: {best.get('_learned_sel')}"
        assert "/movieaction/download/291197/1080p/" in _href(best), _href(best)
