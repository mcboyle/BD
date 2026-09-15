"""Row 719: the scene classifier under-selects real scene links, and the
selection rule is not data a site template can extend.

The wowgirls population is the CAPTURED one: a verbatim copy of the campaign
harness report bd-persist/roles-prompts/campaign/wowgirls/fresh-20260904T1430Z/
site/discovery_report.json (md5 533d14bb11d87517fe86a0d760db963a, 2026-09-04),
whose `selected` list is the 159 title-bearing anchors the fallback rule picked
and whose `selected_by_product_scene_rule` is the zero the register names.
The ultrafilms population is the tracked row 374 crawler fixture.
"""

BD_GATE_SCOPE = "repo-wide"

import json
import re
from pathlib import Path
from urllib.parse import urlparse

from bulk_downloader.playlist_extractor import (_LISTING_KEYWORDS,
                                                _NON_SCENE_HINTS,
                                                _SCENE_URL_HINTS,
                                                _looks_like_scene_url)

_FIXTURES = Path(__file__).parent / "fixtures"
_REPORT = (_FIXTURES / "row719_scene_classifier"
           / "wowgirls_fresh_20260904T1430Z_discovery_report.json")
_ULTRAFILMS = _FIXTURES / "row374_scene_crawler" / "ultrafilms.html"

_FILM_PATH = re.compile(r"/film/[A-Za-z0-9]+/[a-z0-9-]+")
_HREF = re.compile(r'href="([^"]*)"')


def _report():
    data = json.loads(_REPORT.read_text(encoding="utf-8"))
    # The fixture built the shape the register describes, or nothing below
    # measures the captured population.
    assert data["link_population"] == 482
    assert data["base_host"] == "venus.wowgirls.com"
    assert data["selected_by_product_scene_rule"] == 0
    assert data["selected_by_title_fallback"] == 159
    selected = [entry["url"] for entry in data["selected"]]
    assert len(selected) == 159 and len(set(selected)) == 159
    return data, selected


def _product_rule(url, host):
    """The harness's rule 2, replayed: same host, path depth >= 2, AND the
    product's own scene classifier.  Anchor text is not consulted."""
    parsed = urlparse(url)
    depth = len([part for part in parsed.path.split("/") if part])
    return parsed.netloc == host and depth >= 2 and _looks_like_scene_url(url)


def test_row719_the_product_rule_names_every_film_in_the_captured_population():
    data, selected = _report()
    films = sorted(u for u in selected if _FILM_PATH.search(u))
    assert len(films) == 16, "the capture's selected list names 16 /film/<id>/<slug>"
    # The report names a seventeenth /film/<id>/<slug> only as a masked link
    # shape (its thumbnail anchor carried no title, so the fallback never
    # listed it): the classifier sees the same path either way.
    shapes = [shape for shape, _count in data["top_link_shapes"]
              if _FILM_PATH.search(shape)]
    assert shapes == ["https://venus.wowgirls.com/film/uNcNc/naughty-di"]
    named = films + shapes
    assert len(set(named)) == 17
    accepted = [u for u in named if _product_rule(u, data["base_host"])]
    assert len(accepted) == 17, (
        "selected_by_product_scene_rule would still be 0: the product "
        "classifier refuses %d of the 17 captured /film/<id>/<slug> links: %r"
        % (17 - len(accepted), sorted(set(named) - set(accepted))))


def test_row719_the_product_rule_refuses_the_navigation_the_fallback_picked():
    data, selected = _report()
    picked = [u for u in selected if _product_rule(u, data["base_host"])]
    assert len(picked) == 16 and all(_FILM_PATH.search(u) for u in picked)
    refused = [u for u in selected if u not in picked]
    assert len(refused) == 143
    # Every navigation family the fallback picked is present in the refused
    # set (nonzero), so the refusal is measured on the real shapes.
    families = {"/girl/": 98, "/updates/genre/": 38, "/my/": 3, "/goto/site/": 3,
                "/gallery/": 1}
    for family, count in families.items():
        assert sum(family in u for u in refused) == count, family
    assert not any(_looks_like_scene_url(u) for u in refused)


def test_row719_ultrafilms_content_item_links_are_scene_urls():
    hrefs = _HREF.findall(_ULTRAFILMS.read_text(encoding="utf-8"))
    items = [h for h in hrefs if "/content/item/" in h]
    assert len(items) == 4, "row 374 fixture carries four /members/content/item links"
    assert "/account/logout" in hrefs
    # The register's corpus: <8hex>-<slug>, <id>-<slug> and a bare <slug>.
    corpus = ["https://ultrafilms.example" + h for h in items] + [
        "https://ultrafilms.example/members/content/item/82144c7c-with-leo-in-bed",
        "https://ultrafilms.example/content/item/7c-with-leo-in-bed",
        "https://ultrafilms.example/members/content/item/with-leo-in-bed",
    ]
    refused = [u for u in corpus if not _looks_like_scene_url(u)]
    assert refused == [], (
        "the scene classifier under-selects ultrafilms: %d of %d "
        "/content/item/ links refused: %r" % (len(refused), len(corpus), refused))
    # The hint is /content/item/, not /content/ or /item/: the section index,
    # the members home and the account link the same fixture carries refuse.
    for url in ("https://ultrafilms.example/members/content",
                "https://ultrafilms.example/members/content/",
                "https://ultrafilms.example/members/home/whatsnew",
                "https://ultrafilms.example/members/content/items/",
                "https://ultrafilms.example/account/logout"):
        assert not _looks_like_scene_url(url), url


def test_row719_the_scene_hints_are_data_a_template_can_extend():
    """The acceptance verbatim: template-extensible scene-rule data.  A
    site-specific scene route the defaults have never heard of must be
    accepted once the template declares it -- and the SAME url must be
    refused without the template, or this proves nothing about the
    extension."""
    url = "https://x.example/stream/ab12cd/some-slug"
    assert "/stream/" not in _SCENE_URL_HINTS
    assert not any("stream" in kw for kw in _LISTING_KEYWORDS + _NON_SCENE_HINTS)
    assert not _looks_like_scene_url(url), (
        "precondition lost: the defaults already accept this route, so a "
        "template extension cannot be told apart from them")
    assert _looks_like_scene_url(url, template={"scene_url_hints": ["/stream/"]}), (
        "a template-declared scene hint did not reach the decision: the "
        "scene rule is still a closed list")
    # The declared hints are normalised, not matched raw.
    assert _looks_like_scene_url(url, template={"scene_url_hints": [" /STREAM/ "]})
    # A template that declares none changes nothing, and a blank entry is
    # not a hint that every url contains.
    assert not _looks_like_scene_url(url, template={"scene_url_hints": []})
    assert not _looks_like_scene_url(url, template={"scene_url_hints": ["", "  ", 7]})
    assert not _looks_like_scene_url(url, template={})
    # Extension is a UNION: the defaults keep accepting and refusing.
    assert _looks_like_scene_url("https://x.example/video/some-title/12345",
                                 template={"scene_url_hints": ["/stream/"]})
    assert not _looks_like_scene_url("https://x.example/stream/sort/latest",
                                     template={"scene_url_hints": ["/stream/"]})
    # A template's url_patterns stay the whole rule when it declares them.
    both = {"url_patterns": [r"/only-this-scene/"], "scene_url_hints": ["/stream/"]}
    assert _looks_like_scene_url("https://x.example/only-this-scene/42", template=both)
    assert not _looks_like_scene_url(url, template=both)


def test_row719_row704_pagination_controls_still_refuse():
    controls = (
        "https://www.evilangel.com/en/videos/sort/latest",
        "https://www.evilangel.com/en/videos/sort/latest/page/2",
        "https://www.evilangel.com/en/videos/page/3",
        "https://www.evilangel.com/en/videos/page/337",
    )
    assert len(controls) == 4
    for url in controls:
        assert not _looks_like_scene_url(url), url
        assert not _looks_like_scene_url(
            url, template={"scene_url_hints": ["/videos/", "/content/item/"]}), url


def test_row719_the_scene_hint_rule_is_data():
    """Reads the default scene-hint DATA and never drives the classifier, so
    it is also the transform control's band."""
    assert isinstance(_SCENE_URL_HINTS, tuple)
    assert {"/film/", "/films/", "/content/item/"} <= set(_SCENE_URL_HINTS)
    assert len(set(_SCENE_URL_HINTS)) == len(_SCENE_URL_HINTS)


# ── The two production callers of the rule (self-mutation seams) ───────────
# The classifier is only worth anything at the two places that FOLLOW its
# answer: the playlist walker (playlist_extractor.extract_playlist_urls) and
# the search walker (search_extractor.search_site, through its deferred
# wrapper). Each is driven with a duck-typed page over the captured wowgirls
# population so an inverted or short-circuited branch at either call site
# changes the selection, not just the predicate.

class _DuckPage:
    """`goto` is a no-op and `evaluate` answers the link snapshot the walker's
    own JS would have produced -- the page is never a live site."""

    def __init__(self, links):
        self._links = links
        self.evaluate_calls = 0

    def goto(self, url, **kw):
        return None

    def evaluate(self, js, *args):
        self.evaluate_calls += 1
        return [dict(link) for link in self._links]


def _captured_links():
    """Films the register names plus the navigation the fallback picked, in
    one list the way a listing page hands them to the walkers."""
    data, selected = _report()
    films = sorted(u for u in selected if _FILM_PATH.search(u))
    nav = [u for u in selected
           if any(k in u for k in ("/my/favorites", "/my/activity",
                                   "/goto/", "?page=", "/sort/"))]
    assert len(films) == 16 and nav, "the population holds both classes"
    return films, nav, [{"url": u, "title": "captured"} for u in films + nav]


def test_row719_the_playlist_walker_keeps_the_films_and_refuses_the_navigation():
    from bulk_downloader.playlist_extractor import extract_playlist_urls
    films, nav, links = _captured_links()
    page = _DuckPage(links)
    result = extract_playlist_urls(page, "https://venus.wowgirls.com/",
                                   max_pages=1)
    assert page.evaluate_calls == 1, "the walker read exactly one listing"
    assert result.ok, result.error
    assert sorted(result.urls) == films, (
        f"the walker selected {sorted(result.urls)}; the register names "
        f"{films}")
    assert not set(result.urls) & set(nav), "navigation is never a scene"


def test_row719_the_search_walker_applies_the_same_scene_rule():
    from bulk_downloader import search_extractor
    films, nav, links = _captured_links()
    # The deferred wrapper answers exactly as the product rule does.
    assert all(search_extractor._looks_like_scene_url(u) for u in films)
    assert not any(search_extractor._looks_like_scene_url(u) for u in nav)
    page = _DuckPage(links)
    result = search_extractor.search_site(
        page, "wowgirls", "girl",
        {"search_url_pattern": "https://venus.wowgirls.com/search?q={query}"},
    )
    assert page.evaluate_calls == 1, "the walker read exactly one result page"
    assert result.ok, result.error
    got = sorted(hit.url for hit in result.hits)
    assert got == films, f"search selected {got}; the register names {films}"


def test_row810_template_scene_hints_forward_through_both_walkers():
    """A template-only scene route must survive both real forwarding sites."""
    from bulk_downloader import search_extractor
    from bulk_downloader.playlist_extractor import extract_playlist_urls

    scene = "https://x.example/stream/ab12cd/some-slug"
    navigation = "https://x.example/stream/sort/latest"
    template = {"scene_url_hints": ["/stream/"],
                "search_url_pattern": "https://x.example/search?q={query}"}
    links = [{"url": scene, "title": "scene"},
             {"url": navigation, "title": "navigation"}]

    playlist = extract_playlist_urls(
        _DuckPage(links), "https://x.example/listing", template=template,
        max_pages=1)
    assert playlist.ok, playlist.error
    assert playlist.urls == [scene]
    assert navigation not in playlist.urls

    search = search_extractor.search_site(
        _DuckPage(links), "x", "scene", template)
    assert search.ok, search.error
    assert [hit.url for hit in search.hits] == [scene]
    assert navigation not in [hit.url for hit in search.hits]
