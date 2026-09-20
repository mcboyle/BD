"""Row 906: SINGLE-ENTITY-VS-AGGREGATE-DATASET-STRUCTURAL-CLASSIFIER.

Collection index pages (a grid of many similarly-shaped "cards") were
frequently misclassified as individual media items, so BD tried a
single-asset extraction against a listing and failed. The URL-string
heuristics in this module (`is_likely_listing_url`) already sniff the
URL text; this adds a DOM-TOPOLOGY signal that classifies the PAGE
ITSELF (via `card_count`, a Playwright-evaluated repeated-sibling
count under one parent) independent of what the URL looks like, then
composes it with the existing `extract_playlist_urls` fan-out so an
aggregate page's children are queued in one call (row906 acceptance
#2, "recursive child queue expansion": the pagination walk in
`extract_playlist_urls` is itself recursive across pages).

Fixture pages never execute the evaluated JS -- `page.evaluate` is a
fake returning a canned value directly, the same pattern
`tests/test_v3_43_75_bundle.py::TestExtractFailOpen._Page` uses for
`extract_playlist_urls`.
"""
from __future__ import annotations

import pytest

BD_GATE_SCOPE = "module"


class _Page:
    """Fixture Playwright page: fake goto/evaluate, no real browser."""

    def __init__(self, *, card_count=None, evaluate_raises=None,
                 goto_raises=None, links=None, site=None):
        # `site`: optional {url: {"card_count": n, "links": [...]}} map so
        # one fake page can model a whole tree of collection/scene pages.
        self.card_count = card_count
        self.evaluate_raises = evaluate_raises
        self.goto_raises = goto_raises
        self.links = links or []
        self.site = site or {}
        self.current = ""
        self.goto_calls = []
        self.evaluate_calls = []

    def goto(self, url, *, wait_until, timeout):
        self.goto_calls.append(url)
        self.current = url
        if self.goto_raises:
            raise self.goto_raises

    def evaluate(self, javascript):
        self.evaluate_calls.append(javascript)
        if self.evaluate_raises:
            raise self.evaluate_raises
        node = self.site.get(self.current, {})
        if "querySelectorAll('a[href]')" in javascript or "el.querySelector('a')" in javascript:
            return node.get("links", self.links)
        return node.get("card_count", self.card_count)


def _scene(n):
    return {"url": f"https://x.com/video/s{n}", "title": f"s{n}"}


def _real_probe(html):
    """Run the module's real density probe in headless Chromium against
    `html` (FIXER O928: E1/E2 need the JS itself measured, not a canned
    integer). Fails closed if the browser cannot launch. Returns the probe's
    {card_count, player} dict."""
    from playwright.sync_api import sync_playwright
    from bulk_downloader import playlist_extractor as p
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(html)
            return page.evaluate(p._CARD_GRID_DENSITY_JS)
        finally:
            browser.close()


def _real_card_count(html):
    return _real_probe(html)["card_count"]


_CARD = '<div class="{cls}"><a href="/video/s{n}"><img src="t{n}.jpg"></a></div>'
_NAV3 = ('<nav><a href="/">home</a><a href="/category/a">a</a>'
         '<a href="/login">login</a></nav>')


# ─── classify_dataset_topology ──────────────────────────────────────


class TestClassifyDatasetTopology:
    def test_aggregate_page_many_repeated_cards_is_classified_aggregate(self):
        """A collection directory: 24 sibling cards under one grid parent."""
        from bulk_downloader import playlist_extractor as p
        page = _Page(card_count=24)
        result = p.classify_dataset_topology(page, "https://x.com/category/blonde")
        assert result.ok is True
        assert result.is_aggregate is True
        assert result.card_count == 24

    def test_single_item_page_one_card_is_never_aggregate(self):
        """A single scene detail page: no repeated sibling grid at all."""
        from bulk_downloader import playlist_extractor as p
        page = _Page(card_count=1)
        result = p.classify_dataset_topology(page, "https://x.com/video/some-scene")
        assert result.ok is True
        assert result.is_aggregate is False
        assert result.card_count == 1

    def test_zero_single_asset_false_positives_below_threshold(self):
        """Acceptance #3: a page with a couple of similar-looking widgets
        (e.g. two related-content thumbnails) must never be misread as a
        collection -- only >= min_cards counts as an aggregate."""
        from bulk_downloader import playlist_extractor as p
        for count in (0, 1, 2):
            result = p.classify_dataset_topology(
                _Page(card_count=count), "https://x.com/video/some-scene", min_cards=3)
            assert result.is_aggregate is False, f"{count} cards falsely called aggregate"
        result = p.classify_dataset_topology(
            _Page(card_count=3), "https://x.com/category/x", min_cards=3)
        assert result.is_aggregate is True

    def test_evaluated_javascript_measures_repeated_sibling_structure(self):
        """The DOM probe is a structural (sibling-group) signal, not a text
        scrape -- distinctive diagnostic distinguishing this from a link
        scan (guards against a decoy that just counts <a> tags)."""
        from bulk_downloader import playlist_extractor as p
        page = _Page(card_count=5)
        p.classify_dataset_topology(page, "https://x.com/category/x")
        assert page.evaluate_calls, "never called page.evaluate"
        js = page.evaluate_calls[0]
        assert "querySelectorAll" in js
        assert "parentElement" in js  # sibling grouping requires the parent

    def test_page_load_failure_is_reported_not_raised(self):
        from bulk_downloader import playlist_extractor as p
        page = _Page(goto_raises=RuntimeError("boom"))
        result = p.classify_dataset_topology(page, "https://x.com/category/x")
        assert result.ok is False
        assert result.error == "page_load_failed:RuntimeError"
        assert not page.evaluate_calls  # never evaluated on an unloaded page

    def test_evaluate_failure_is_reported_not_raised(self):
        from bulk_downloader import playlist_extractor as p
        page = _Page(evaluate_raises=ValueError("boom"))
        result = p.classify_dataset_topology(page, "https://x.com/category/x")
        assert result.ok is False
        assert result.error == "evaluate_failed:ValueError"

    def test_non_integer_response_is_bad_response_not_a_crash(self):
        from bulk_downloader import playlist_extractor as p
        page = _Page(card_count="not-a-number")
        result = p.classify_dataset_topology(page, "https://x.com/category/x")
        assert result.ok is False
        assert result.error == "bad_response"

    def test_empty_url_and_no_page_fail_open(self):
        from bulk_downloader import playlist_extractor as p
        assert p.classify_dataset_topology(_Page(), "").error == "empty_url"
        assert p.classify_dataset_topology(None, "https://x.com/category/x").error == "page_is_none"


# ─── route_dataset_page (classification + recursive child expansion) ─

class TestRouteDatasetPage:
    def test_single_entity_page_routes_to_itself_not_a_batch_queue(self):
        """Negative control: a single-item page must route to ITSELF, with
        an empty child queue -- proves the aggregate path is not silently
        always taken."""
        from bulk_downloader import playlist_extractor as p
        page = _Page(card_count=1)
        result = p.route_dataset_page(page, "https://x.com/video/some-scene")
        assert result.ok is True
        assert result.is_aggregate is False
        assert result.single_url == "https://x.com/video/some-scene"
        assert result.child_urls == []
        assert len(page.goto_calls) == 1  # classification only, no fan-out call

    def test_aggregate_page_recursively_expands_into_a_child_queue(self):
        """Acceptance #1 + #2: a collection directory (card_count >= threshold)
        is classified aggregate AND its scene children are expanded via the
        existing (paginated -> recursive) extract_playlist_urls fan-out."""
        from bulk_downloader import playlist_extractor as p
        page = _Page(card_count=6, links=[
            {"url": "https://x.com/video/a", "title": "A"},
            {"url": "https://x.com/video/b", "title": "B"},
            {"url": "https://x.com/category/nav", "title": "nav"},  # filtered: listing, not scene
        ])
        result = p.route_dataset_page(page, "https://x.com/category/blonde")
        assert result.ok is True
        assert result.is_aggregate is True
        assert result.card_count == 6
        assert result.child_urls == [
            "https://x.com/video/a", "https://x.com/video/b",
        ]
        assert result.single_url == ""
        # classification navigates once, extract_playlist_urls navigates again
        # for its own extraction pass; the /category/nav listing link is a
        # candidate child collection: classified (goto) and, being an
        # aggregate on this uniform fake, extracted (goto) -- its scenes
        # dedup against the root's. Nothing is visited twice.
        assert page.goto_calls == [
            "https://x.com/category/blonde", "https://x.com/category/blonde",
            "https://x.com/category/nav", "https://x.com/category/nav",
        ]
        assert result.collections == [
            "https://x.com/category/blonde", "https://x.com/category/nav",
        ]

    def test_aggregate_classification_but_zero_extractable_children_is_not_ok(self):
        """An aggregate DOM shape whose links all fail the scene-URL filter
        (e.g. a grid of category tiles, not scene cards) must not silently
        report success with an empty queue."""
        from bulk_downloader import playlist_extractor as p
        page = _Page(card_count=8, links=[
            {"url": "https://x.com/category/other", "title": "other"},
        ])
        result = p.route_dataset_page(page, "https://x.com/category/blonde")
        assert result.is_aggregate is True
        assert result.child_urls == []
        assert result.ok is False  # extract_playlist_urls.ok is False on 0 urls

    def test_classification_failure_short_circuits_before_any_fan_out(self):
        from bulk_downloader import playlist_extractor as p
        page = _Page(goto_raises=RuntimeError("boom"))
        result = p.route_dataset_page(page, "https://x.com/category/blonde")
        assert result.ok is False
        assert result.error == "page_load_failed:RuntimeError"
        assert result.is_aggregate is False
        assert len(page.goto_calls) == 1  # never reached the fan-out's own goto


# ─── FIXER (O928) controls for VERDICT-correctness E1/E2/E3 ────────


class TestDensityProbeRealDom:
    """E1/E2: the probe is executed in a real headless Chromium so the
    JS itself is under test (the fake `_Page` cannot see a regression in
    the JS -- it returns a canned integer)."""

    def test_positive_control_three_media_cards_count_three(self):
        html = "<body><main>" + "".join(
            _CARD.format(cls="card", n=i) for i in range(3)) + "</main></body>"
        assert _real_card_count(html) == 3

    def test_e1_single_video_with_three_nav_anchors_is_not_a_grid(self):
        """E1: one <video> plus a three-anchor nav bar -> card_count 0,
        the page stays a single item (nav anchors are not media cards)."""
        html = ("<body>" + _NAV3 +
                '<main><video src="/media/x.mp4" controls></video></main></body>')
        assert _real_card_count(html) == 0

    def test_e1_bare_anchor_lists_outside_nav_are_not_cards_either(self):
        """A sidebar of plain text links (no image/video) is chrome, not a
        card grid, even without a <nav> wrapper."""
        html = ('<body><ul class="links"><li><a href="/x">x</a></li>'
                '<li><a href="/y">y</a></li><li><a href="/z">z</a></li>'
                '<li><a href="/w">w</a></li></ul>'
                '<main><video src="/media/x.mp4"></video></main></body>')
        assert _real_card_count(html) == 0

    def test_e1_pagination_anchors_do_not_inflate_a_small_page(self):
        html = ('<body><main><video src="/m.mp4"></video></main>'
                '<div class="pagination"><a href="?page=1"><img src="1.png"></a>'
                '<a href="?page=2"><img src="2.png"></a>'
                '<a href="?page=3"><img src="3.png"></a></div></body>')
        assert _real_card_count(html) == 0

    def test_e2_class_token_order_is_invariant(self):
        """E2: 'card tile-N' vs 'tile-N card' vs 'tile-N  card' (extra
        whitespace) are the same card shape."""
        cards = [
            '<div class="card tile-0"><a href="/video/s0"><img src="0.jpg"></a></div>',
            '<div class="tile-0 card"><a href="/video/s1"><img src="1.jpg"></a></div>',
            '<div class="tile-0  card "><a href="/video/s2"><img src="2.jpg"></a></div>',
        ]
        html = "<body><main>" + "".join(cards) + "</main></body>"
        assert _real_card_count(html) == 3
        # control: a genuinely different class SET is a different shape
        html2 = html.replace('class="tile-0  card "', 'class="tile-9 card"')
        assert _real_card_count(html2) == 2

    def test_anchor_cards_without_a_wrapper_div_count(self):
        html = "<body><div class=\"grid\">" + "".join(
            f'<a class="thumb" href="/video/s{i}"><img src="{i}.jpg"></a>'
            for i in range(5)) + "</div></body>"
        assert _real_card_count(html) == 5


class TestRecursiveChildCollections:
    """E3: a root collection whose children are collections must reach
    the leaf scenes; the traversal is bounded and cycle-safe."""

    def _tree(self):
        root = "https://x.com/category/root"
        subs = [f"https://x.com/category/sub{i}" for i in range(3)]
        site = {root: {"card_count": 3,
                       "links": [{"url": u, "title": u} for u in subs]}}
        for i, sub in enumerate(subs):
            site[sub] = {"card_count": 3,
                         "links": [_scene(3 * i + j) for j in range(3)]
                         + [{"url": root, "title": "up"}]}   # back-link: cycle
        return root, subs, site

    def test_e3_root_of_three_collections_yields_nine_leaf_scenes(self):
        from bulk_downloader import playlist_extractor as p
        root, subs, site = self._tree()
        result = p.route_dataset_page(_Page(site=site), root)
        assert result.ok is True
        assert result.is_aggregate is True
        assert result.child_urls == [_scene(n)["url"] for n in range(9)]
        assert result.collections == [root] + subs

    def test_e3_cycle_back_links_are_visited_once(self):
        from bulk_downloader import playlist_extractor as p
        root, subs, site = self._tree()
        page = _Page(site=site)
        p.route_dataset_page(page, root)
        # root: classify + extract; each sub: classify + extract; never again
        assert page.goto_calls.count(root) == 2
        for sub in subs:
            assert page.goto_calls.count(sub) == 2

    def test_e3_depth_bound_stops_expansion(self):
        from bulk_downloader import playlist_extractor as p
        root, subs, site = self._tree()
        page = _Page(site=site)
        result = p.route_dataset_page(page, root, max_depth=0)
        assert result.ok is False and result.child_urls == []
        assert result.collections == [root]
        assert page.goto_calls == [root, root]

    def test_e3_collection_budget_bounds_navigations(self):
        from bulk_downloader import playlist_extractor as p
        root, subs, site = self._tree()
        page = _Page(site=site)
        result = p.route_dataset_page(page, root, max_collections=1)
        assert result.collections == [root, subs[0]]
        assert result.child_urls == [_scene(n)["url"] for n in range(3)]
        assert result.error == "expansion_budget_exhausted"
        assert result.ok is False

    def test_e3_child_listing_that_classifies_single_is_not_expanded(self):
        from bulk_downloader import playlist_extractor as p
        root, subs, site = self._tree()
        site[subs[1]]["card_count"] = 1     # a "category" URL that is one item
        page = _Page(site=site)
        result = p.route_dataset_page(page, root)
        assert subs[1] not in result.collections
        assert page.goto_calls.count(subs[1]) == 1      # classified only
        assert len(result.child_urls) == 6


class TestFixerRound2:
    """Correctness REFUTE (03:xx) E1 / E2."""

    def test_e2_player_page_with_related_cards_is_a_single_entity(self):
        """E2: a player page with a >=3-card "related videos" grid beside it
        used to classify as an aggregate (card_count >= min_cards)."""
        from bulk_downloader import playlist_extractor as p
        html = ('<body><main><div class="player"><video src="/m.mp4" controls></video></div>'
                '<section class="related">' + "".join(
                    _CARD.format(cls="card", n=i) for i in range(4)) + '</section></main></body>')
        probe = _real_probe(html)
        assert probe == {"card_count": 4, "player": True}
        page = _Page(card_count=probe)
        topo = p.classify_dataset_topology(page, "https://x.com/watch/123")
        assert topo.ok and topo.card_count == 4 and topo.has_player is True
        assert topo.is_aggregate is False
        route = p.route_dataset_page(page, "https://x.com/watch/123")
        assert route.is_aggregate is False and route.single_url == "https://x.com/watch/123"

    def test_e2_hover_preview_videos_inside_grid_tiles_are_not_a_player(self):
        """Control: a listing grid whose tiles carry <video> hover previews
        is still an aggregate -- a card-wrapped video is not the subject."""
        from bulk_downloader import playlist_extractor as p
        tile = '<div class="card"><a href="/video/s{n}"><img src="{n}.jpg"><video src="/p{n}.mp4" muted></video></a></div>'
        html = "<body><main>" + "".join(tile.format(n=i) for i in range(4)) + "</main></body>"
        probe = _real_probe(html)
        assert probe == {"card_count": 4, "player": False}
        assert p.classify_dataset_topology(_Page(card_count=probe), "https://x.com/models/x").is_aggregate is True

    def test_e2_embedded_iframe_player_counts_as_the_subject(self):
        html = ('<body><main><iframe src="https://cdn.example/embed/abc"></iframe>'
                '<div class="grid">' + "".join(_CARD.format(cls="card", n=i) for i in range(6))
                + '</div></main></body>')
        assert _real_probe(html) == {"card_count": 6, "player": True}

    def test_e1_child_collection_without_a_listing_keyword_is_still_expanded(self):
        """E1: a model page at /jane-doe (no /models/ or /gallery/ in the
        path) that classifies as an aggregate by topology is expanded; URL
        text only orders the candidates, it never gates them."""
        from bulk_downloader import playlist_extractor as p
        root, child = "https://x.com/category/root", "https://x.com/jane-doe"
        site = {root: {"card_count": 3, "links": [{"url": child, "title": "jane"}]},
                child: {"card_count": 3, "links": [_scene(n) for n in range(3)]}}
        assert not p.is_likely_listing_url(child)
        result = p.route_dataset_page(_Page(site=site), root)
        assert result.ok is True
        assert result.collections == [root, child]
        assert result.child_urls == [_scene(n)["url"] for n in range(3)]

    def test_e1_listing_keyword_links_are_classified_first_within_the_budget(self):
        from bulk_downloader import playlist_extractor as p
        root = "https://x.com/category/root"
        plain, keyword = "https://x.com/jane-doe", "https://x.com/models/amy"
        site = {root: {"card_count": 3, "links": [{"url": plain, "title": "p"}, {"url": keyword, "title": "k"}]},
                plain: {"card_count": 3, "links": [_scene(1)]},
                keyword: {"card_count": 3, "links": [_scene(2)]}}
        page = _Page(site=site)
        result = p.route_dataset_page(page, root, max_collections=1)
        assert result.collections == [root, keyword]      # keyword link went first
        assert result.error == "expansion_budget_exhausted"
        assert page.goto_calls.count(plain) == 0
