"""Row 701: the quality scan may only score candidates that belong to the
requested scene.

The defect: `_all_candidates` was assembled from every scored match, so a
Related-Videos tile belonging to ANOTHER work both drove the min-resolution
verdict and named itself in the `Saw:` line the operator reads -- the
africancasting lane recorded a 240p item titled for a different scene driving
the refusal.  Row 388 delivered a TIE-BREAK, not a SCOPE, and its own comment
says so; this cut adds the scope.

Row 701's acceptance has TWO clauses and both are pinned here:
  * a candidate that cannot be shown to belong to the scene is EXCLUDED from
    the deciding population, not merely sorted below it, and
  * a candidate whose affinity is UNKNOWN is REFUSED rather than SILENTLY
    admitted.

The second clause is bounded by the operator ruling on
ASK-701-population-predicate (2026-09-06): `work > 0` decides whenever ANY
candidate on the page proves affinity, and only when NOTHING proves affinity
may the UNKNOWN tier be admitted -- carrying `_no_identity_proof` into the run
record, so the admission is marked rather than silent.  The measurement behind
that ruling is in DONE.md: the affinity prober reads only
`_CANDIDATE_URL_ATTRS` and matches only on slug tokens, so a scene whose own
tiers are signed direct-CDN paths can never be PROVEN in scope, and refusing
there converts a prober limitation into a total download outage -- the hazard
row 388's docstring names.
"""

from contextlib import contextmanager

import pytest

from bulk_downloader.detect import (
    _candidate_names_another_work,
    _candidate_route_identity,
    _candidate_work_affinity,
    find_best_download,
    page_work_tokens,
    res_label,
    res_score,
    work_affinity,
)

BD_GATE_SCOPE = "repo-wide"

# ── Zero-entropy synthetic corpus.  Every host is `.example`; every signing
# value is a documented zero-entropy constant, never a realistic secret. ──
_SCENE_URL = "https://x.example/scenes/midnight-harbor-drift/42"
_SIG = "key=" + "0" * 32 + ",end=" + "0" * 10
_OWN_1080 = ("https://cdn.x.example/dl/%s/midnight-harbor-drift_1920x1080.mp4"
             % _SIG)
_OWN_720 = ("https://cdn.x.example/dl/%s/midnight-harbor-drift_1280x720.mp4"
            % _SIG)
# Other scenes on the same site: ROUTES, so the stamp can prove they name a
# DIFFERENT work rather than merely failing to match.
_FOREIGN_4K = "https://x.example/scenes/harbour-lights-after-dark/43"
_FOREIGN_240 = "https://x.example/scenes/quiet-tide-morning/44"
# A signed direct-CDN asset that carries no slug at all: UNKNOWN, not foreign.
_UNKNOWN_4K = "https://cdn2.example/signed/%s/asset_3840x2160.mp4" % _SIG
# A page that names no work at all.  Every `page.set_content` fixture in this
# tree is one of these, so this is the common case and not an exotic one.
_ANONYMOUS_PAGE = "about:blank"
_OPAQUE_CONTROL_VALUE = "opaque-asset-token"
# ONE learned group holding BOTH a proven tier and an unattributable one: the
# shape where seam 1 admits the group and must still carry what it refused.
_MIXED_URL = "https://members.example/scene/target-work"
_FIX_MIXED_ONE_GROUP = (
    "<!doctype html><html><body><main>"
    '<a href="https://cdn.example/media/target-work-1080p.mp4">1080p</a>'
    '<a href="https://cdn.example/media/unrelated-scene-4320p.mp4">4320p</a>'
    "</main></body></html>")


def _a(href, label):
    return ('<li><a class="dl" href="%s"><span class="res">%s</span>'
            "</a></li>" % (href, label))


def _page_html(own, related):
    own_rows = "".join(_a(h, t) for h, t in own)
    rel_rows = "".join(
        '<div class="related-card"><ul class="tiers">%s</ul></div>' % _a(h, t)
        for h, t in related)
    return ("<!doctype html><html><body><h1>Now Watching</h1>"
            '<section class="scene"><ul class="tiers">%s</ul></section>'
            '<h2>Related Videos</h2><section class="related">%s</section>'
            "</body></html>" % (own_rows, rel_rows))


_OWN = [(_OWN_1080, "1920x1080&nbsp;FHD&nbsp;1080p&nbsp;1.75&nbsp;GB"),
        (_OWN_720, "1280x720&nbsp;HD&nbsp;720p&nbsp;1.2&nbsp;GB")]
_RELATED = [(_FOREIGN_4K, "3840x2160&nbsp;4K&nbsp;UHD&nbsp;2160p&nbsp;5&nbsp;GB"),
            (_FOREIGN_240, "426x240&nbsp;LQ&nbsp;240p&nbsp;96&nbsp;MB")]

# (i) the scene's own tiers, (ii) a related grid whose tiles belong to other
# works and carry their own quality text.  This is the hazard: delete the
# related grid and the fixture can no longer go red for row 701.
_FIX_RELATED_GRID = _page_html(_OWN, _RELATED)
# NEGATIVE CONTROL: the identical page with the hazard removed.
_FIX_NO_GRID = _page_html(_OWN, [])
# Condition 1's pin: one PROVEN candidate and one UNKNOWN of higher apparent
# quality.  The proven one must decide and the UNKNOWN must not be in the
# deciding population.
_FIX_PROVEN_PLUS_UNKNOWN = _page_html(
    [(_OWN_1080, "1920x1080&nbsp;FHD&nbsp;1080p&nbsp;1.75&nbsp;GB"),
     (_UNKNOWN_4K, "3840x2160&nbsp;4K&nbsp;UHD&nbsp;2160p&nbsp;5&nbsp;GB")],
    [])
# Every candidate FOREIGN: nothing on this page can be attributed to the
# scene, and that is a DISTINCT outcome.
_FIX_ALL_FOREIGN = _page_html([], _RELATED)
# Nothing proves affinity in EITHER direction: the ruling admits this tier and
# requires the admission to be MARKED.
_FIX_ALL_UNKNOWN = _page_html(
    [(_UNKNOWN_4K, "3840x2160&nbsp;4K&nbsp;UHD&nbsp;2160p&nbsp;5&nbsp;GB")], [])


@contextmanager
def _page(url, html):
    """Serve `html` AT `url` without touching the network.

    `set_content` would leave page.url == about:blank, and the page's own URL
    is the identity signal under test.  Every host is `.example`, so a missed
    route can never reach a real site (A6)."""
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


class _AttrEl:
    """The only thing the affinity stamp asks of an element."""

    def __init__(self, **attrs):
        self._attrs = {k.replace("_", "-"): v for k, v in attrs.items()}
        self.reads = 0

    def get_attribute(self, name):
        self.reads += 1
        return self._attrs.get(name)


def _href(entry):
    loc = entry.get("locator")
    if loc is None:
        return ""
    try:
        return loc.get_attribute("href") or ""
    except Exception:
        return ""


def _hrefs(entries):
    return [_href(c) for c in entries]


# ══ PRECONDITIONS: the corpus really carries the hazard ═══════════════════
def test_the_fixture_carries_the_hazard_the_row_names():
    """A corpus without the hazard cannot go red for it, so prove the three
    things the row's RED depends on before asserting any verdict."""
    assert work_affinity(_SCENE_URL, _OWN_1080) == 1, (
        "the scene's own tier no longer proves affinity, so 'excluded vs "
        "sorted below' cannot be told apart on this fixture")
    for foreign in (_FOREIGN_4K, _FOREIGN_240):
        assert work_affinity(_SCENE_URL, foreign) == 0
    # (ii) the related tiles CARRY quality text, or they could never decide.
    assert res_score("3840x2160 4K UHD 2160p 5 GB") == 2160
    assert res_score("426x240 LQ 240p 96 MB") == 240
    # (i) the scene's own tier is ABOVE the 1080 min_resolution default.
    assert res_score("1920x1080 FHD 1080p 1.75 GB") == 1080
    assert res_label(2160) == "4K"
    # (iii) the related grid EXISTS in the DOM under test.
    assert _FIX_RELATED_GRID.count('class="related-card"') == 2
    assert _FIX_NO_GRID.count('class="related-card"') == 0


def test_the_three_affinity_values_are_distinct_and_measured():
    """The whole cut rests on UNKNOWN existing as a value distinct from
    FOREIGN: you cannot refuse UNKNOWN until UNKNOWN exists."""
    own = _AttrEl(href=_OWN_1080)
    foreign = _AttrEl(href=_FOREIGN_4K)
    unknown = _AttrEl(href=_UNKNOWN_4K)
    assert _candidate_work_affinity(own, _SCENE_URL) == 1
    assert _candidate_work_affinity(foreign, _SCENE_URL) == -1
    assert _candidate_work_affinity(unknown, _SCENE_URL) == 0
    assert own.reads > 0 and foreign.reads > 0 and unknown.reads > 0, (
        "the stamp never read an attribute -- vacuous")


def test_foreign_is_a_finding_not_a_default():
    """FOREIGN must mean 'an attribute RESOLVED TO A DIFFERENT WORK'.

    The stamp used to return -1 on the first attribute whose path was not a
    media extension, so an ordinary nav control on the requested page and a
    JS-driven download control were both condemned by an incidental href."""
    # An incidental, identity-free href does not make a candidate foreign.
    assert _candidate_work_affinity(_AttrEl(href="/login"), _SCENE_URL) == 0
    assert _candidate_work_affinity(_AttrEl(href="#"), _SCENE_URL) == 0
    # A JS control on the REQUESTED scene: the whole attribute list is read,
    # so the in-scope data-download decides, not the placeholder href.
    js = _AttrEl(href="#", data_download=_OWN_1080)
    assert _candidate_work_affinity(js, _SCENE_URL) == 1
    js2 = _AttrEl(href="javascript:void(0)", data_download=_OWN_1080)
    assert _candidate_work_affinity(js2, _SCENE_URL) == 1
    # And a genuinely different work IS foreign, read from any attribute.
    assert _candidate_work_affinity(
        _AttrEl(href="#", data_url=_FOREIGN_4K), _SCENE_URL) == -1



def test_a_page_that_names_no_work_cannot_condemn_anything_as_foreign():
    """FOREIGN answers "does this name ANOTHER work". A page that names NO
    work cannot ask it, and answering it True turns UNKNOWN into a finding.

    MEASURED REGRESSION, not a hypothetical. On the clean tip a8267e5d,
    tests/test_row484_ancestor_class_token_is_not_selector_authority.py and
    tests/test_row399_a_photo_gallery_is_not_a_failed_video_page.py give
    88 passed. Those gates drive `find_best_download` over `page.set_content`
    fixtures, whose page URL is `about:blank`, and one of them taught a single
    opaque control carrying `data-signed-url-key="opaque-asset-token"`. With
    this cut and WITHOUT the guard below, `page_work_tokens("about:blank")` is
    `()` -- the page names no work -- and that control was still stamped
    FOREIGN. Clause 2's fallback admits UNKNOWN and never FOREIGN, so the only
    candidate on the page was dropped and `find_best_download` returned the
    sentinel: 5 failed / 83 passed, five contracts broken by this cut.

    An unanswerable question returns UNKNOWN (CLAUDE.md A2, A7). UNKNOWN is
    exactly what the marked fallback exists to carry, so the row 701 ruling is
    served by this guard rather than weakened by it.
    """
    # PRECONDITION: the page really names nothing, so the test is not vacuous.
    assert page_work_tokens(_ANONYMOUS_PAGE) == (), (
        "precondition failed: this page names a work, so the guard under test "
        "is not the thing being exercised")
    # POSITIVE CONTROL for the same probe: it CAN find an identity.
    assert page_work_tokens(_SCENE_URL), (
        "the probe cannot say YES, so its NO above measures nothing")
    # PRECONDITION: the value itself DOES resolve to tokens, so the page's
    # missing identity is the ONLY thing standing between it and FOREIGN.
    assert _candidate_route_identity(_OPAQUE_CONTROL_VALUE), (
        "precondition failed: the value names no work either, so this test "
        "would pass for the wrong reason")

    assert _candidate_names_another_work(
        _ANONYMOUS_PAGE, _OPAQUE_CONTROL_VALUE) is False
    opaque = _AttrEl(data_signed_url_key=_OPAQUE_CONTROL_VALUE)
    assert _candidate_work_affinity(opaque, _ANONYMOUS_PAGE) == 0, (
        "an anonymous page condemned a candidate as FOREIGN")
    assert opaque.reads > 0, "the stamp never read an attribute -- vacuous"

    # AND THE GUARD IS NARROW: the very same value against a page that DOES
    # name a work is still FOREIGN. This is not "stop calling things foreign".
    named = _AttrEl(data_signed_url_key=_OPAQUE_CONTROL_VALUE)
    assert _candidate_work_affinity(named, _SCENE_URL) == -1, (
        "the guard leaked past anonymous pages and disabled FOREIGN outright")
    assert _candidate_names_another_work(
        _SCENE_URL, _OPAQUE_CONTROL_VALUE) is True


def test_the_admitted_group_carries_its_excluded_evidence_too():
    """Excluding a candidate must not DELETE it.

    Seam 1 breaks on the first ADMITTED group, and `learned_excluded.extend`
    sat AFTER that break -- so the winning group's own excluded list was
    discarded. On a page whose single learned selector matches both the
    scene's proven tier and an unattributable one, the unattributable link
    vanished from the record entirely: absent from `_all_candidates`, absent
    from `_excluded_candidates`, and therefore absent from the operator's
    view. That is the opposite of what row 701 promises -- refused evidence is
    PRESERVED WITH A REASON -- and it is a defect this cut introduced rather
    than one it inherited.

    Measured, not argued: tests/test_row399_a_photo_gallery_is_not_a_failed_
    video_page.py::test_learned_candidates_use_same_work_before_resolution
    saw a harvest of 1 on a page carrying 2 links.
    """
    with _page(_MIXED_URL, _FIX_MIXED_ONE_GROUP) as pg:
        best = find_best_download(pg, learned={"row_selectors": ["a"]})
        assert best is not None, "every candidate was refused -- OUTAGE"
        assert best.get("_via_learned") is True, (
            "precondition: this must be the LEARNED seam, not the wide sweep")
        allc = best.get("_all_candidates") or []
        exc = best.get("_excluded_candidates") or []
        # The proven tier decides, alone.
        assert [c.get("work") for c in allc] == [1], allc
        assert "target-work" in (best["locator"].get_attribute("href") or "")
        # And the refused one is still on the record, with its reason.
        assert len(exc) == 1, (
            "the admitted group's excluded evidence was dropped: %r" % (exc,))
        assert exc[0].get("work") == 0, exc
        assert exc[0].get("reason") == "unknown", exc
        # EXACT COUNT: the page carries two links and the record accounts for
        # both -- one deciding, one refused. Nothing is silently gone.
        assert len(allc) + len(exc) == 2, (allc, exc)

# ══ RED 1: the deciding population is scoped, not merely re-sorted ════════
def test_the_related_grid_never_reaches_the_deciding_population():
    """RED on base: `_all_candidates` carries the two foreign tiles, so the
    operator's `Saw:` line names another scene and the 4K tile in it reads as
    a tier BD failed to pick."""
    with _page(_SCENE_URL, _FIX_RELATED_GRID) as pg:
        best = find_best_download(pg, "", learned=None, runner=None)
        assert best is not None, "every candidate was refused -- OUTAGE"
        cands = best.get("_all_candidates") or []
        excluded = best.get("_excluded_candidates") or []
        # Denominator: the sweep harvested all four, and it is nonzero.
        assert len(cands) + len(excluded) == 4, (
            "the population under judgement is not the fixture's four "
            "candidates; in=%r out=%r" % (_hrefs(cands), _hrefs(excluded)))
        assert len(cands) == 2 and len(excluded) == 2, (
            "expected exactly the scene's own two tiers to decide; in=%r "
            "out=%r" % (_hrefs(cands), _hrefs(excluded)))
        assert all(c.get("work") == 1 for c in cands)
        for foreign in (_FOREIGN_4K, _FOREIGN_240):
            assert foreign not in _hrefs(cands), (
                "a tile from another scene is in the population that DECIDES "
                "quality and names itself in the operator's Saw: line")
            assert foreign in _hrefs(excluded), (
                "row 701 excludes foreign tiles but must not blind the "
                "operator: the evidence is kept under a distinct key")
        assert {c["reason"] for c in excluded} == {"foreign"}
        # The winner is the scene's own best tier, and the 5 GB / 4K foreign
        # tile did not outrank it by size.
        assert _href(best) == _OWN_1080
        assert best["score"] == 1080
        assert not best.get("_no_identity_proof"), (
            "this selection was made on PROOF, so it must not be marked as "
            "unproven")


def test_the_gate_and_the_saw_list_see_only_in_scope_candidates():
    """A4: the min-resolution refusal may not be driven by, or name, a
    candidate from another scene.  Reconstructed exactly as runner.py builds
    it (`best['_all_candidates'][:6]`)."""
    with _page(_SCENE_URL, _FIX_RELATED_GRID) as pg:
        best = find_best_download(pg, "", learned=None, runner=None)
        seen = " | ".join(
            "%s:%s" % (res_label(c["score"]), c["text"][:30])
            for c in best.get("_all_candidates", [])[:6])
        assert seen, "an empty Saw: list cannot prove anything"
        assert "4K" not in seen, (
            "the refusal names a 4K tier that belongs to another scene: %r"
            % seen)
        assert "240p" not in seen, seen
        # And the gate itself still decides, on the in-scope population.
        min_res = 2160
        assert best["score"] < min_res, "precondition: the gate would fire"
        refused_on = best["score"]
        assert refused_on == 1080, (
            "the refusal must be driven by the scene's own best tier")


# ══ RED 2: an UNKNOWN candidate is refused, not silently admitted ═════════
def test_an_unknown_candidate_is_refused_when_anything_proves_affinity():
    """Operator ruling condition 1.  One PROVEN candidate plus one UNKNOWN of
    HIGHER apparent quality: the proven one decides and the UNKNOWN is not in
    the deciding population.

    RED on base: `_all_candidates` contained the UNKNOWN 4K entry, so an
    unattributable tile of higher apparent quality sat in the population the
    quality scan and the operator's Saw: line read."""
    with _page(_SCENE_URL, _FIX_PROVEN_PLUS_UNKNOWN) as pg:
        best = find_best_download(pg, "", learned=None, runner=None)
        assert best is not None
        cands = best.get("_all_candidates") or []
        excluded = best.get("_excluded_candidates") or []
        assert len(cands) == 1 and len(excluded) == 1, (
            "in=%r out=%r" % (_hrefs(cands), _hrefs(excluded)))
        assert _UNKNOWN_4K not in _hrefs(cands), (
            "a candidate whose affinity is UNKNOWN was silently admitted to "
            "the population that decides quality")
        assert excluded[0]["reason"] == "unknown", (
            "the refusal reason must distinguish 'cannot tell' from 'belongs "
            "to another work'; got %r" % excluded[0]["reason"])
        assert excluded[0]["work"] == 0
        assert _href(best) == _OWN_1080 and best["score"] == 1080, (
            "the PROVEN 1080 tier decides, not the UNKNOWN 4K one")


def test_unknown_is_admitted_only_when_nothing_proves_affinity_and_is_marked():
    """Operator ruling condition 2.  When the prober cannot tell for ANY
    candidate, the tier is admitted -- refusing it would turn a prober
    limitation into a total outage -- and every such selection carries
    `_no_identity_proof` so the admission is recorded, never silent."""
    with _page(_SCENE_URL, _FIX_ALL_UNKNOWN) as pg:
        best = find_best_download(pg, "", learned=None, runner=None)
        assert best is not None, "every candidate was refused -- OUTAGE"
        cands = best.get("_all_candidates") or []
        assert len(cands) == 1 and _href(best) == _UNKNOWN_4K
        assert all(c.get("work") == 0 for c in cands)
        assert best.get("_no_identity_proof") is True, (
            "an UNKNOWN candidate was admitted SILENTLY: row 701's second "
            "clause requires the admission to be marked")


def test_a_page_whose_every_candidate_is_foreign_is_a_distinct_outcome():
    """The nothing-in-scope outcome: not a silent success, not a 240p
    refusal, and not 'no download button found'."""
    with _page(_SCENE_URL, _FIX_ALL_FOREIGN) as pg:
        best = find_best_download(pg, "", learned=None, runner=None)
        assert best is not None, "the sentinel must be consumable, not None"
        assert best.get("_no_in_scope_candidates") is True
        assert not best, (
            "the sentinel must be FALSY so the four legacy callers' "
            "`if not best:` guards cannot mistake it for a find")
        # Consumable: every key a caller reads before testing.
        for key in ("score", "size", "text", "locator"):
            assert key in best, "KeyError waiting to happen on %r" % key
        assert best["score"] == 0 and best["locator"] is None
        assert best.get("_all_candidates") == []
        excluded = best.get("_excluded_candidates") or []
        assert len(excluded) == 2 and {c["reason"] for c in excluded} == {
            "foreign"}


# ══ NEGATIVE CONTROLS ════════════════════════════════════════════════════
def test_a_page_with_no_related_grid_selects_exactly_as_before():
    """Delete the hazard and nothing changes: same winner, same score, same
    size, same Saw: list.  If this diverges, the cut changed selection rather
    than scoping it."""
    with _page(_SCENE_URL, _FIX_NO_GRID) as pg:
        best = find_best_download(pg, "", learned=None, runner=None)
        assert best is not None
        cands = best.get("_all_candidates") or []
        assert _hrefs(cands) == [_OWN_1080, _OWN_720]
        assert (best["score"], _href(best)) == (1080, _OWN_1080)
        assert (best.get("_excluded_candidates") or []) == []
        assert not best.get("_no_identity_proof")


def test_the_gate_still_refuses_a_page_that_is_genuinely_low_quality():
    """Scoping the population must not disable the gate: an IN-SCOPE-only page
    below min_resolution still lands below the bar."""
    low = _page_html([(_OWN_720, "1280x720&nbsp;HD&nbsp;720p&nbsp;1.2&nbsp;GB")],
                     [])
    with _page(_SCENE_URL, low) as pg:
        best = find_best_download(pg, "", learned=None, runner=None)
        assert best is not None
        assert best["score"] == 720 and best["score"] < 1080, (
            "the min-resolution refusal arm would no longer fire")
        assert (best.get("_all_candidates") or [])[0]["work"] == 1


def test_the_population_predicate_is_the_only_thing_that_admits():
    """The decision, in isolation, with both reasons reachable."""
    from bulk_downloader.detect import (_candidate_is_foreign,
                                        _candidate_is_in_scope,
                                        _scoped_candidates)
    proven = {"work": 1, "text": "p", "score": 1080, "size": 1, "locator": None}
    unknown = {"work": 0, "text": "u", "score": 2160, "size": 9, "locator": None}
    foreign = {"work": -1, "text": "f", "score": 2160, "size": 9,
               "locator": None}
    assert _candidate_is_in_scope(proven) and not _candidate_is_in_scope(unknown)
    assert not _candidate_is_in_scope(foreign)
    assert _candidate_is_foreign(foreign) and not _candidate_is_foreign(unknown)
    in_scope, excluded = _scoped_candidates([proven, unknown, foreign])
    assert in_scope == [proven]
    assert [e["reason"] for e in excluded] == ["unknown", "foreign"], (
        "both refusal reasons must be REACHABLE; a reason string no branch "
        "can produce is a diagnostic that lies")
    # Nothing proves affinity: the UNKNOWN tier is the population, the
    # FOREIGN one is still refused.
    in_scope, excluded = _scoped_candidates([unknown, foreign])
    assert in_scope == [unknown] and [e["reason"] for e in excluded] == \
        ["foreign"]
    # Nothing left at all -> the caller emits the distinct outcome.
    in_scope, excluded = _scoped_candidates([foreign])
    assert in_scope == [] and len(excluded) == 1
    # A candidate with no `work` key at all behaves as UNKNOWN, not as a find.
    assert not _candidate_is_in_scope({"score": 1})


def test_the_learned_fast_path_is_scoped_at_its_own_assembly_seam():
    """Seam 1.  A learned selector that matches ONLY foreign tiles may not
    seed `best`: retaining it as an unscoped fallback is row 701's defect on
    the learned path.  The wide sweep then judges the whole page."""
    learned = {"row_selectors": [".related-card a.dl"]}
    with _page(_SCENE_URL, _FIX_RELATED_GRID) as pg:
        best = find_best_download(pg, "", learned=learned, runner=None)
        assert best is not None
        assert not best.get("_via_learned"), (
            "a learned group with nothing in scope seeded `best` anyway")
        cands = best.get("_all_candidates") or []
        assert _hrefs(cands) == [_OWN_1080, _OWN_720], _hrefs(cands)
    # And a learned selector that DOES match in-scope tiles still wins there.
    learned_ok = {"row_selectors": ["section.scene a.dl"]}
    with _page(_SCENE_URL, _FIX_RELATED_GRID) as pg:
        best = find_best_download(pg, "", learned=learned_ok, runner=None)
        assert best.get("_via_learned") is True
        assert _hrefs(best.get("_all_candidates") or []) == [_OWN_1080,
                                                            _OWN_720]
        assert (best.get("_excluded_candidates") or []) == []


# ══ THE FIFTH CALLER: the named outcome must be REACHED, not dead code ════
class _RecordingRunner:
    """The only surface `_handle_nothing_in_scope` touches."""

    site_id = "row701-scope"
    config = {"name": "Row 701 Scope"}

    def __init__(self):
        self.updates = []
        self.shots = 0

    def _screenshot(self, page, url):
        self.shots += 1
        return "shot.png"

    def _update_job(self, url, status, message, **kw):
        self.updates.append((url, status, message, kw.get("screenshot")))


def _nothing_in_scope_best():
    from bulk_downloader.detect import _no_in_scope_result
    return _no_in_scope_result([
        {"text": "4K UHD 2160p 5 GB", "score": 2160, "size": 5, "work": -1,
         "locator": None, "reason": "foreign"},
        {"text": "LQ 240p 96 MB", "score": 240, "size": 1, "work": -1,
         "locator": None, "reason": "foreign"},
    ])


def test_the_named_outcome_is_reached_and_says_what_actually_happened(
        monkeypatch):
    """A4.  The distinct outcome must not read as 'best is 240p' and must not
    read as 'No download button found' -- a download control WAS found."""
    from bulk_downloader import runner as runner_module
    from bulk_downloader.runner import SiteRunner

    logged = []
    monkeypatch.setattr(runner_module, "db_log",
                        lambda *a, **k: logged.append(a))
    subject = _RecordingRunner()
    handled = SiteRunner._handle_nothing_in_scope(
        subject, object(), "https://x.example/scenes/midnight-harbor-drift/42",
        _nothing_in_scope_best())
    assert handled is True, "the sentinel fell through to the not-best guards"
    assert subject.shots == 1
    assert len(subject.updates) == 1 and len(logged) == 1
    _url, status, msg, shot = subject.updates[0]
    assert status == "needs_review" and shot == "shot.png"
    assert "Nothing in scope" in msg, msg
    assert "foreign" in msg, msg
    assert "below" not in msg and "No download button" not in msg, (
        "the distinct outcome collapsed into an existing, wrong diagnostic "
        "(CLAUDE.md A7): %r" % msg)
    assert logged[0][3] == "needs_review" and "nothing in scope" in logged[0][6]


def test_the_handler_declines_every_result_that_is_not_the_sentinel():
    """Negative control, and it fails for the intended reason: a real find and
    a genuine no-find must both fall through to the existing arms."""
    from bulk_downloader.runner import SiteRunner
    subject = _RecordingRunner()
    for other in (None, {}, {"score": 1080, "_all_candidates": []}):
        assert SiteRunner._handle_nothing_in_scope(
            subject, object(), "https://x.example/s/1", other) is False
    assert subject.updates == [] and subject.shots == 0


def test_the_named_outcome_is_consulted_before_the_not_best_guards():
    """REACHABILITY.  The sentinel is falsy, so if `if not best:` runs first
    the whole named outcome is dead code and the run reports 'No download
    button found'.  That is exactly how it shipped, so pin the order."""
    import inspect
    from bulk_downloader.runner import SiteRunner

    src = inspect.getsource(SiteRunner._process_one)
    lines = src.splitlines()
    call = [i for i, ln in enumerate(lines)
            if "self._handle_nothing_in_scope(page, url, best)" in ln]
    assert len(call) == 1, "the handler is no longer called exactly once"
    guards = [i for i, ln in enumerate(lines) if ln.strip() == "if not best:"]
    assert len(guards) == 2, (
        "the two `if not best:` guards this ordering is about moved; found %d"
        % len(guards))
    assert call[0] < min(guards), (
        "the falsy sentinel is consumed by `if not best:` before the named "
        "outcome is read -- the outcome is dead code again")
    gate = [i for i, ln in enumerate(lines) if "min_res>0 and best" in ln]
    assert len(gate) == 1 and call[0] < gate[0], (
        "the named outcome must precede the min-resolution gate")
    gate = "\n".join(lines[gate[0]:gate[0] + 6])
    assert "and not forced" in gate, (
        "force_download must still force past the min-resolution gate")


def test_the_other_all_candidates_readers_still_see_a_list():
    """The four other `_all_candidates` readers -- runner.py's Saw: line,
    runner_transport.py:1243 and runner_integrity.py:125/157 -- all read a
    list of dicts with text/score/size/locator, and the scoped population is
    still exactly that."""
    with _page(_SCENE_URL, _FIX_RELATED_GRID) as pg:
        best = find_best_download(pg, "", learned=None, runner=None)
    cands = best.get("_all_candidates") or []
    assert cands, "an empty list cannot prove the readers still work"
    for c in cands:
        assert set(("text", "score", "size", "work", "locator")) <= set(c)
        assert isinstance(c["text"], str) and isinstance(c["score"], int)
    # runner_integrity.py:157 copies these keys onto a replacement result.
    carried = {k: best[k] for k in ("_all_candidates",) if k in best}
    assert carried["_all_candidates"] is cands


# ══ R1: the affinity question is about the PAGE, not about one learned group ══
# The scene's own PROVEN 1080 tier lives under section.scene; an
# unattributable direct-CDN 4K asset lives under .related-card.  Learned
# row_selectors list the related grid FIRST.
_FIX_LEARNED_GRID_FIRST = _page_html(
    [(_OWN_1080, "1920x1080&nbsp;FHD&nbsp;1080p&nbsp;1.75&nbsp;GB")],
    [(_UNKNOWN_4K, "3840x2160&nbsp;4K&nbsp;UHD&nbsp;2160p&nbsp;5&nbsp;GB")])


def test_the_unknown_fallback_is_a_page_question_not_a_learned_group_one():
    """REGRESSION GUARD, and it went red against BASE as well as against the
    first version of this cut.

    `_scoped_candidates` is called once per `row_selectors` entry and the loop
    stops at the first group it admits.  Asking "does anything prove affinity"
    of the GROUP rather than of the PAGE let the UNKNOWN fallback open inside
    the related grid while the scene's own proven tier sat in a later group
    that was never scored -- an unattributable 4K asset beating a proven 1080
    one, which is exactly the harm row 701 exists to prevent."""
    learned = {"row_selectors": [".related-card a.dl", "section.scene a.dl"]}
    with _page(_SCENE_URL, _FIX_LEARNED_GRID_FIRST) as pg:
        best = find_best_download(pg, "", learned=learned, runner=None)
        assert best is not None
        # Precondition: both groups exist and the hazard ordering is real.
        assert pg.locator(".related-card a.dl").count() == 1
        assert pg.locator("section.scene a.dl").count() == 1
        assert _href(best) == _OWN_1080, (
            "the UNKNOWN fallback opened inside the first learned group while "
            "a LATER group held the scene's own proven tier; href=%s"
            % _href(best))
        assert best["score"] == 1080, best["score"]
        assert not best.get("_no_identity_proof"), (
            "this page DOES prove affinity, so no selection on it may be "
            "marked unproven")
        cands = best.get("_all_candidates") or []
        assert len(cands) == 1 and cands[0]["work"] == 1
        assert _UNKNOWN_4K in _hrefs(best.get("_excluded_candidates") or [])


def test_the_fallback_still_opens_when_the_whole_page_proves_nothing():
    """The other side of the same predicate: with no proven candidate ANYWHERE
    the learned path still admits the UNKNOWN tier, marked -- so the page-level
    question narrowed the fallback rather than deleting it."""
    learned = {"row_selectors": [".related-card a.dl", "section.scene a.dl"]}
    only_unknown = _page_html(
        [], [(_UNKNOWN_4K, "3840x2160&nbsp;4K&nbsp;UHD&nbsp;2160p&nbsp;5&nbsp;GB")])
    with _page(_SCENE_URL, only_unknown) as pg:
        best = find_best_download(pg, "", learned=learned, runner=None)
        assert best is not None, "every candidate was refused -- OUTAGE"
        assert _href(best) == _UNKNOWN_4K
        assert best.get("_no_identity_proof") is True


# ══ R2: the mark reaches the RUN RECORD on the success path ═══════════════
class _RecordingJobRunner(_RecordingRunner):
    """Adds the two attributes `_record_no_identity_proof` touches."""

    def __init__(self):
        import threading
        super().__init__()
        self._lock = threading.Lock()
        self.jobs = {}


def test_an_unproven_admission_is_recorded_when_the_run_proceeds():
    """Operator ruling condition 2, second destination.  The min-resolution
    message only fires BELOW min_resolution; the motivating case is a marked
    2160 winner against the 1080 default, which is ABOVE the bar.  Assert the
    RECORD, not the dict that was passed in."""
    from bulk_downloader.runner import SiteRunner
    from bulk_downloader.runner_util import DEFAULT_MIN_RESOLUTION

    subject = _RecordingJobRunner()
    url = _SCENE_URL
    best = {"score": 2160, "size": 5, "locator": None,
            "_no_identity_proof": True, "_all_candidates": []}
    assert best["score"] > DEFAULT_MIN_RESOLUTION, (
        "precondition: this winner is ABOVE the bar, so the min-resolution "
        "message can never be what records it")
    note = SiteRunner._record_no_identity_proof(subject, url, best)
    assert subject.jobs[url]["no_identity_proof"] is True, (
        "the admission was made without identity proof and the run record "
        "does not say so anywhere")
    assert "no identity proof" in note, note
    # A LATER update merges into the job rather than replacing it, so the
    # stamp survives the download overwriting the message.
    subject.jobs[url].update({"status": "done", "message": "Saved"})
    assert subject.jobs[url]["no_identity_proof"] is True


def test_a_proven_selection_records_nothing_and_says_nothing():
    """Negative control: it fails for the intended reason if the mark is
    stamped unconditionally."""
    from bulk_downloader.runner import SiteRunner
    subject = _RecordingJobRunner()
    for proven in ({"score": 2160}, {"score": 2160, "_no_identity_proof": False},
                   None):
        assert SiteRunner._record_no_identity_proof(
            subject, _SCENE_URL, proven) == ""
    assert subject.jobs == {}


def test_the_run_record_is_stamped_before_the_download_is_started():
    """REACHABILITY for the success path: the stamp must happen on the way to
    `_do_download`, not inside a branch the run only reaches when it fails."""
    import inspect
    from bulk_downloader.runner import SiteRunner

    lines = inspect.getsource(SiteRunner._process_one).splitlines()
    stamp = [i for i, ln in enumerate(lines)
             if "self._record_no_identity_proof(url, best)" in ln]
    assert len(stamp) == 1, "the success-path stamp is no longer called once"
    clicking = [i for i, ln in enumerate(lines) if 'Clicking [{lbl}]' in ln]
    downloads = [i for i, ln in enumerate(lines) if "self._do_download(" in ln]
    assert clicking and downloads, "the success path moved"
    assert stamp[0] < min(clicking), (
        "the run record is stamped after the message it belongs in")
    assert stamp[0] < min(downloads), (
        "the download starts before the admission is recorded")
    gate = [i for i, ln in enumerate(lines) if "min_res>0 and best" in ln]
    assert gate and gate[0] < stamp[0], (
        "the success-path stamp must sit AFTER the min-resolution gate: "
        "above the bar is exactly the case the gate's own message misses")


# -- the gate must actually run, WITH A BROWSER, in CI ------------------------

def test_this_gate_is_scheduled_in_ci_on_a_shard_that_has_chromium():
    """A gate CI does not run does not exist -- and a Chromium-driven gate on a
    shard without Chromium is the same thing with a longer traceback.

    This file launches real Chromium (`p.chromium.launch()`), and the shard it
    was first declared on installed no browser, so all twelve cases failed in
    CI on `Executable doesn't exist` while every one of them passed locally on
    a host that happens to have the binary.  `importorskip` does not save it:
    the playwright PACKAGE is in requirements-test.txt, so the import succeeds
    and only the LAUNCH fails.  The shard-coverage gate proves declaration and
    scheduling; nothing else checks that the browser install step reaches the
    shard, so each browser-driven gate asserts its own pairing -- the same
    self-check test_row386_the_download_chain_is_gated.py and
    test_login_session_does_not_cover_the_scene_host.py carry.
    """
    from pathlib import Path

    try:
        import yaml
    except ImportError as exc:                    # pragma: no cover - see below
        # NOT importorskip.  PyYAML is in requirements-test.txt and CI installs
        # it; if it is absent this gate cannot read its own CI wiring, and
        # "could not measure" is UNKNOWN, which fails.
        pytest.fail(f"UNKNOWN: PyYAML is unavailable ({exc}), so this gate "
                    "cannot check that CI schedules it on a browser shard")
    root = Path(__file__).resolve().parents[1]
    wf = yaml.safe_load(
        (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    job = wf["jobs"]["gate-suites"]
    me = "tests/" + Path(__file__).name
    shards = [s for s in job["strategy"]["matrix"]["include"]
              if me in s["suites"].split()]
    assert len(shards) == 1, (
        f"{me} appears in {len(shards)} gate-suites shards; it must be in "
        "exactly one or it runs nowhere / twice")
    name = shards[0]["name"]
    steps = [s for s in job["steps"]
             if "playwright install" in str(s.get("run", ""))]
    assert steps, "no step installs a browser on any gate shard"
    covered = [s for s in steps if name in str(s.get("if", ""))]
    assert covered, (
        f"shard {name!r} runs this gate but no 'playwright install' step's "
        f"condition names it, so every case would fail on a missing browser")
