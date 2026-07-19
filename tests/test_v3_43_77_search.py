"""v3.43.77: tests for Search-and-add by query."""
from __future__ import annotations

from unittest.mock import MagicMock  # [SAST 3:13pm 13 may] dropped: patch

# [SAST 3:13pm 13 may] removed unused: import pytest


# ─── build_search_url ─────────────────────────────────────────────


class TestBuildSearchUrl:
    def test_simple_substitution(self):
        from bulk_downloader import search_extractor as s
        tpl = {"search_url_pattern": "https://x.com/?s={query}"}
        assert s.build_search_url(tpl, "blonde") == "https://x.com/?s=blonde"

    def test_url_encodes_spaces(self):
        from bulk_downloader import search_extractor as s
        tpl = {"search_url_pattern": "https://x.com/?s={query}"}
        # quote_plus uses + for spaces
        assert s.build_search_url(tpl, "blonde scenes") == "https://x.com/?s=blonde+scenes"

    def test_url_encodes_special(self):
        from bulk_downloader import search_extractor as s
        tpl = {"search_url_pattern": "https://x.com/?s={query}"}
        url = s.build_search_url(tpl, "model & co")
        assert "%26" in url  # &

    def test_uppercase_placeholder_works(self):
        from bulk_downloader import search_extractor as s
        tpl = {"search_url_pattern": "https://x/?s={QUERY}"}
        assert s.build_search_url(tpl, "test") == "https://x/?s=test"

    def test_empty_query_returns_empty(self):
        from bulk_downloader import search_extractor as s
        tpl = {"search_url_pattern": "https://x/?s={query}"}
        assert s.build_search_url(tpl, "") == ""
        assert s.build_search_url(tpl, None) == ""  # type: ignore[arg-type]

    def test_no_placeholder_returns_empty(self):
        from bulk_downloader import search_extractor as s
        tpl = {"search_url_pattern": "https://x.com/"}
        assert s.build_search_url(tpl, "blonde") == ""

    def test_no_pattern_returns_empty(self):
        from bulk_downloader import search_extractor as s
        assert s.build_search_url({}, "blonde") == ""
        assert s.build_search_url({"search_url_pattern": ""}, "blonde") == ""

    def test_non_dict_template(self):
        from bulk_downloader import search_extractor as s
        assert s.build_search_url(None, "blonde") == ""  # type: ignore[arg-type]
        assert s.build_search_url("not a dict", "blonde") == ""  # type: ignore[arg-type]


# ─── is_site_searchable ───────────────────────────────────────────


class TestIsSiteSearchable:
    def test_yes(self):
        from bulk_downloader import search_extractor as s
        assert s.is_site_searchable(
            {"search_url_pattern": "https://x/?s={query}"}) is True

    def test_empty_pattern(self):
        from bulk_downloader import search_extractor as s
        assert s.is_site_searchable({"search_url_pattern": ""}) is False

    def test_no_placeholder(self):
        from bulk_downloader import search_extractor as s
        assert s.is_site_searchable(
            {"search_url_pattern": "https://x.com/search"}) is False

    def test_empty_template(self):
        from bulk_downloader import search_extractor as s
        assert s.is_site_searchable({}) is False

    def test_none_template(self):
        from bulk_downloader import search_extractor as s
        assert s.is_site_searchable(None) is False  # type: ignore[arg-type]


# ─── search_site fail-open ────────────────────────────────────────


class TestSearchSiteFailOpen:
    def test_empty_query(self):
        from bulk_downloader import search_extractor as s
        tpl = {"search_url_pattern": "https://x/?s={query}"}
        r = s.search_site(None, "test", "", tpl)
        assert r.ok is False
        assert r.error == "empty_query"

    def test_whitespace_only_query(self):
        from bulk_downloader import search_extractor as s
        tpl = {"search_url_pattern": "https://x/?s={query}"}
        r = s.search_site(None, "test", "   ", tpl)
        assert r.ok is False
        assert r.error == "empty_query"

    def test_non_searchable_template(self):
        from bulk_downloader import search_extractor as s
        r = s.search_site(None, "test", "blonde", {})
        assert r.ok is False
        assert r.error == "site_not_searchable"

    def test_none_page(self):
        from bulk_downloader import search_extractor as s
        tpl = {"search_url_pattern": "https://x/?s={query}"}
        r = s.search_site(None, "test", "blonde", tpl)
        assert r.ok is False
        assert r.error == "page_is_none"

    def test_navigate_failure(self):
        from bulk_downloader import search_extractor as s
        tpl = {"search_url_pattern": "https://x/?s={query}"}
        page = MagicMock()
        page.goto.side_effect = RuntimeError("net down")
        r = s.search_site(page, "test", "blonde", tpl)
        assert r.ok is False
        assert "navigate_failed" in r.error
        assert "RuntimeError" in r.error


class TestSearchSiteResultShape:
    def test_fields_present(self):
        from bulk_downloader import search_extractor as s
        r = s.search_site(None, "test", "", {})
        for f in ("ok", "site_id", "query", "search_url", "hits",
                  "elapsed_s", "error"):
            assert hasattr(r, f), f"missing field {f}"
        # hits is a list
        assert isinstance(r.hits, list)

    def test_count_property(self):
        from bulk_downloader import search_extractor as s
        r = s.SearchResult(hits=[s.SearchHit(url="https://x/a"),
                                 s.SearchHit(url="https://x/b")])
        assert r.count == 2

    def test_count_empty(self):
        from bulk_downloader import search_extractor as s
        r = s.SearchResult()
        assert r.count == 0


# ─── Result extraction (with mocked page) ────────────────────────


class TestExtractWithMockedPage:
    def test_happy_path(self):
        """Mocked page returns 3 candidates, 2 are scene-looking +
        same-site, 1 is off-site."""
        from bulk_downloader import search_extractor as s
        tpl = {"search_url_pattern": "https://wow.com/?s={query}"}
        page = MagicMock()
        page.goto.return_value = None
        # evaluate is called once for link extraction
        page.evaluate.return_value = [
            {"url": "https://wow.com/video/scene1",
             "title": "Scene 1", "thumb": "https://cdn.wow.com/1.jpg"},
            {"url": "https://wow.com/video/scene2",
             "title": "Scene 2", "thumb": ""},
            {"url": "https://other-site.com/video/scene3",
             "title": "Off-site", "thumb": ""},
            {"url": "https://wow.com/login",  # navigation link, filtered out
             "title": "Login", "thumb": ""},
        ]
        r = s.search_site(page, "wow", "blonde", tpl)
        assert r.ok is True
        assert r.count == 2  # 2 scene-looking same-site URLs
        urls = [h.url for h in r.hits]
        assert "https://wow.com/video/scene1" in urls
        assert "https://wow.com/video/scene2" in urls
        # Title preserved
        assert any(h.title == "Scene 1" for h in r.hits)
        # Thumbnail preserved
        assert any(h.thumbnail_url == "https://cdn.wow.com/1.jpg"
                   for h in r.hits)

    def test_deduplication(self):
        """The same URL appearing twice in results should appear once
        in hits."""
        from bulk_downloader import search_extractor as s
        tpl = {"search_url_pattern": "https://wow.com/?s={query}"}
        page = MagicMock()
        page.evaluate.return_value = [
            {"url": "https://wow.com/video/scene1",
             "title": "First", "thumb": ""},
            {"url": "https://wow.com/video/scene1",  # dup
             "title": "Also", "thumb": ""},
        ]
        r = s.search_site(page, "wow", "blonde", tpl)
        assert r.count == 1

    def test_max_results_respected(self):
        from bulk_downloader import search_extractor as s
        tpl = {"search_url_pattern": "https://wow.com/?s={query}"}
        page = MagicMock()
        # 100 candidate URLs
        page.evaluate.return_value = [
            {"url": f"https://wow.com/video/{i}", "title": str(i), "thumb": ""}
            for i in range(100)
        ]
        r = s.search_site(page, "wow", "blonde", tpl, max_results=10)
        assert r.count == 10

    def test_zero_results_still_ok(self):
        """An empty result set isn't an error — query just didn't match."""
        from bulk_downloader import search_extractor as s
        tpl = {"search_url_pattern": "https://wow.com/?s={query}"}
        page = MagicMock()
        page.evaluate.return_value = []
        r = s.search_site(page, "wow", "obscure-query", tpl)
        assert r.ok is True
        assert r.count == 0


# ─── search_all_sites ────────────────────────────────────────────


class TestSearchAllSites:
    def test_empty_query(self):
        from bulk_downloader import search_extractor as s
        results = s.search_all_sites(lambda: None, "", {})
        assert results == {}

    def test_empty_sites(self):
        from bulk_downloader import search_extractor as s
        results = s.search_all_sites(lambda: None, "blonde", {})
        assert results == {}

    def test_non_callable_factory(self):
        from bulk_downloader import search_extractor as s
        results = s.search_all_sites(None, "blonde", {})  # type: ignore[arg-type]
        assert results == {}

    def test_skips_non_searchable_sites(self):
        from bulk_downloader import search_extractor as s
        sites = {
            "searchable": {"search_url_pattern": "https://x/?s={query}"},
            "not_searchable": {"search_url_pattern": ""},
        }
        results = s.search_all_sites(MagicMock, "q", sites)
        assert "not_searchable" in results
        assert results["not_searchable"].ok is False
        assert results["not_searchable"].error == "site_not_searchable"

    def test_factory_failure_per_site(self):
        from bulk_downloader import search_extractor as s
        sites = {
            "a": {"search_url_pattern": "https://x/?s={query}"},
            "b": {"search_url_pattern": "https://x/?s={query}"},
        }
        def bad_factory():
            raise RuntimeError("nope")
        results = s.search_all_sites(bad_factory, "q", sites)
        for sid, r in results.items():
            assert r.ok is False
            assert "page_factory_failed" in r.error

    def test_sites_filter(self):
        from bulk_downloader import search_extractor as s
        sites = {
            "a": {"search_url_pattern": "https://x/?s={query}"},
            "b": {"search_url_pattern": "https://x/?s={query}"},
            "c": {"search_url_pattern": "https://x/?s={query}"},
        }
        results = s.search_all_sites(
            MagicMock, "q", sites, sites_filter=["a", "c"])
        # Only a and c, not b
        assert "a" in results
        assert "c" in results
        assert "b" not in results


# ─── aggregate_stats ─────────────────────────────────────────────


class TestAggregateStats:
    def test_empty(self):
        from bulk_downloader import search_extractor as s
        st = s.aggregate_stats({})
        assert st["sites_searched"] == 0
        assert st["total_hits"] == 0

    def test_non_dict(self):
        from bulk_downloader import search_extractor as s
        st = s.aggregate_stats(None)  # type: ignore[arg-type]
        assert st["sites_searched"] == 0

    def test_mixed_results(self):
        from bulk_downloader import search_extractor as s
        results = {
            "ok_with_hits": s.SearchResult(
                ok=True, hits=[s.SearchHit(url="a"), s.SearchHit(url="b")]),
            "ok_no_hits": s.SearchResult(ok=True, hits=[]),
            "skipped": s.SearchResult(ok=False, error="site_not_searchable"),
            "failed": s.SearchResult(ok=False, error="navigate_failed:..."),
        }
        st = s.aggregate_stats(results)
        assert st["sites_searched"] == 2
        assert st["sites_with_results"] == 1
        assert st["total_hits"] == 2
        assert st["sites_skipped"] == 1
        assert st["sites_failed"] == 1


# ─── Flask routes ────────────────────────────────────────────────


class TestFlaskRoutes:
    def _rules(self):
        from bulk_downloader.app import app
        return {r.rule for r in app.url_map.iter_rules()}

    def test_search_site_route(self):
        assert "/api/search/site" in self._rules()

    def test_search_all_route(self):
        assert "/api/search/all" in self._rules()

    def test_search_sites_available_route(self):
        assert "/api/search/sites_available" in self._rules()


# ─── Config fields ───────────────────────────────────────────────


class TestConfigFields:
    def test_use_search_extractor_registered(self):
        from bulk_downloader.app_kernel import CFG_FIELDS, DEFAULTS
        assert "use_search_extractor" in CFG_FIELDS
        assert DEFAULTS.get("use_search_extractor") is False

    def test_search_url_pattern_registered(self):
        from bulk_downloader.app_kernel import CFG_FIELDS, DEFAULTS
        assert "search_url_pattern" in CFG_FIELDS
        assert DEFAULTS.get("search_url_pattern") == ""

    def test_search_result_selector_registered(self):
        from bulk_downloader.app_kernel import CFG_FIELDS, DEFAULTS
        assert "search_result_selector" in CFG_FIELDS


# ─── Runner integration ──────────────────────────────────────────


class TestRunnerHooks:
    def test_search_available_flag(self):
        from bulk_downloader import runner
        assert hasattr(runner, "_SEARCH_AVAILABLE")

    def test_search_site_method(self):
        from bulk_downloader.runner import SiteRunner
        assert hasattr(SiteRunner, "_search_site")


# ─── bdctl integration ──────────────────────────────────────────


class TestBdctl:
    def test_cmd_search_site_exists(self):
        import bdctl
        assert hasattr(bdctl, "cmd_search_site")

    def test_cmd_search_all_exists(self):
        import bdctl
        assert hasattr(bdctl, "cmd_search_all")
