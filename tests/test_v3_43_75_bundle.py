"""v3.43.75: tests for playlist fan-out + CommunityScrapers +
yt-dlp archive + MutationObserver lazy-player wait.

Four modules tested:
  1. yt_dlp_archive.py     — read/write yt-dlp's download_archive
  2. playlist_extractor.py — fan out listing URLs to scenes
  3. community_scrapers.py — fetch + install Stash scrapers
  4. lazy_player_wait.py   — MutationObserver wrapper

All modules fail-open; no network required for tests (HTTP mocked).
"""
from __future__ import annotations

from contextlib import nullcontext
import os
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest


# ─── yt_dlp_archive ────────────────────────────────────────────────


class TestYtDlpDeriveId:
    def test_youtube_watch(self):
        from bulk_downloader import yt_dlp_archive as y
        assert y.derive_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == ("youtube", "dQw4w9WgXcQ")

    def test_youtube_shorts(self):
        from bulk_downloader import yt_dlp_archive as y
        assert y.derive_id("https://www.youtube.com/shorts/abc12345678") == ("youtube", "abc12345678")

    def test_youtube_shortener(self):
        from bulk_downloader import yt_dlp_archive as y
        assert y.derive_id("https://youtu.be/dQw4w9WgXcQ") == ("youtube", "dQw4w9WgXcQ")

    def test_pornhub_viewkey(self):
        from bulk_downloader import yt_dlp_archive as y
        assert y.derive_id("https://www.pornhub.com/view_video.php?viewkey=ph12345") == ("pornhub", "ph12345")

    def test_pornhub_with_other_query(self):
        from bulk_downloader import yt_dlp_archive as y
        # viewkey not necessarily first param
        assert y.derive_id("https://www.pornhub.com/view_video.php?utm=x&viewkey=ph999") == ("pornhub", "ph999")

    def test_xvideos(self):
        from bulk_downloader import yt_dlp_archive as y
        result = y.derive_id("https://www.xvideos.com/video.abc123/some-title")
        assert result == ("xvideos", "abc123")

    def test_xhamster(self):
        from bulk_downloader import yt_dlp_archive as y
        assert y.derive_id("https://www.xhamster.com/videos/scene-name-987654") == ("xhamster", "987654")

    def test_unknown_returns_none(self):
        from bulk_downloader import yt_dlp_archive as y
        assert y.derive_id("https://random.example.com/no-pattern") is None

    def test_empty_returns_none(self):
        from bulk_downloader import yt_dlp_archive as y
        assert y.derive_id("") is None
        assert y.derive_id(None) is None  # type: ignore[arg-type]


class TestYtDlpArchiveIO:
    def setup_method(self):
        self._tmpdir = tempfile.mkdtemp(prefix="ytdlp_test_")
        self.archive_path = os.path.join(self._tmpdir, "archive.txt")

    def teardown_method(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        # Clear module cache so cross-test bleeding doesn't happen
        from bulk_downloader import yt_dlp_archive as y
        with y._cache_lock:
            y._archive_cache.clear()

    def test_read_missing_file_returns_empty(self):
        from bulk_downloader import yt_dlp_archive as y
        assert y.read_archive("/totally/nonexistent/file") == set()

    def test_append_and_read(self):
        from bulk_downloader import yt_dlp_archive as y
        assert y.append_entry(self.archive_path, "youtube", "abc123") is True
        entries = y.read_archive(self.archive_path)
        assert "youtube abc123" in entries

    def test_dedup_on_append(self):
        from bulk_downloader import yt_dlp_archive as y
        y.append_entry(self.archive_path, "youtube", "abc")
        y.append_entry(self.archive_path, "youtube", "abc")  # dup
        entries = y.read_archive(self.archive_path)
        assert len(entries) == 1

    def test_is_in_archive(self):
        from bulk_downloader import yt_dlp_archive as y
        y.append_entry(self.archive_path, "youtube", "abc")
        assert y.is_in_archive(self.archive_path, "youtube", "abc") is True
        assert y.is_in_archive(self.archive_path, "youtube", "xyz") is False

    def test_try_skip_for_url_known(self):
        from bulk_downloader import yt_dlp_archive as y
        y.append_entry(self.archive_path, "youtube", "dQw4w9WgXcQ")
        assert y.try_skip_for_url(
            self.archive_path,
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        ) is True

    def test_try_skip_for_url_unknown_returns_false(self):
        from bulk_downloader import yt_dlp_archive as y
        assert y.try_skip_for_url(
            self.archive_path,
            "https://www.youtube.com/watch?v=zzzzzzzzzzz"
        ) is False

    def test_try_skip_unrecognized_url_returns_false(self):
        """URL that can't be ID-derived shouldn't be skipped."""
        from bulk_downloader import yt_dlp_archive as y
        assert y.try_skip_for_url(
            self.archive_path,
            "https://random.example.com/no-pattern"
        ) is False

    def test_skips_blank_and_comments(self):
        from bulk_downloader import yt_dlp_archive as y
        with open(self.archive_path, "w", encoding="utf-8") as f:
            f.write("# this is a comment\n")
            f.write("\n")
            f.write("youtube abc\n")
            f.write("# another comment\n")
            f.write("pornhub xyz\n")
        entries = y.read_archive(self.archive_path)
        assert entries == {"youtube abc", "pornhub xyz"}

    def test_stats(self):
        from bulk_downloader import yt_dlp_archive as y
        y.append_entry(self.archive_path, "youtube", "a")
        y.append_entry(self.archive_path, "youtube", "b")
        y.append_entry(self.archive_path, "pornhub", "c")
        s = y.stats_for_archive(self.archive_path)
        assert s["total"] == 3
        assert s["by_extractor"]["youtube"] == 2
        assert s["by_extractor"]["pornhub"] == 1


# ─── playlist_extractor ───────────────────────────────────────────


class TestPlaylistDetection:
    def test_obvious_listing_keywords(self):
        from bulk_downloader import playlist_extractor as p
        for url in (
            "https://wowgirls.com/category/blonde/",
            "https://brazzers.com/pornstar/jane-doe",
            "https://vixen.com/series/cliche",
            "https://x.com/search?q=foo",
            "https://x.com/tag/blonde",
            "https://x.com/model/jane",
        ):
            assert p.is_likely_listing_url(url), f"missed: {url}"

    def test_scene_urls_not_flagged(self):
        from bulk_downloader import playlist_extractor as p
        for url in (
            "https://example.com/video/12345",
            "https://example.com/scene/abc",
            "https://example.com/watch?v=xyz",
        ):
            assert not p.is_likely_listing_url(url), f"false positive: {url}"

    def test_template_patterns_win(self):
        """When site template declares playlist_url_patterns, only
        those count."""
        from bulk_downloader import playlist_extractor as p
        tpl = {"playlist_url_patterns": [r"/playlist/", r"/album/"]}
        # /category/ is in default heuristic but NOT in template patterns
        # → not a listing
        assert p.is_likely_listing_url(
            "https://x.com/category/blonde", template=tpl) is False
        assert p.is_likely_listing_url(
            "https://x.com/playlist/abc", template=tpl) is True

    def test_invalid_regex_doesnt_crash(self):
        from bulk_downloader import playlist_extractor as p
        # Invalid regex in template → skipped silently, no crash
        tpl = {"playlist_url_patterns": [r"(unclosed"]}
        assert p.is_likely_listing_url(
            "https://x.com/playlist/abc", template=tpl) is False

    def test_empty_returns_false(self):
        from bulk_downloader import playlist_extractor as p
        assert p.is_likely_listing_url("") is False
        assert p.is_likely_listing_url(None) is False  # type: ignore[arg-type]


class TestSceneUrlFilter:
    def test_obvious_scene_urls(self):
        from bulk_downloader import playlist_extractor as p
        for url in (
            "https://x/video/abc",
            "https://x/scene/123",
            "https://x/watch/foo",
            "https://x/episode/12",
        ):
            assert p._looks_like_scene_url(url), f"missed: {url}"

    def test_rejects_listings(self):
        from bulk_downloader import playlist_extractor as p
        for url in (
            "https://x/category/blonde",
            "https://x/model/jane",
        ):
            assert not p._looks_like_scene_url(url), f"false pos: {url}"

    def test_rejects_non_scene_links(self):
        from bulk_downloader import playlist_extractor as p
        for url in (
            "https://x/login",
            "https://x/signup",
            "https://x/account/billing",
            "javascript:void(0)",
            "mailto:foo@bar.com",
        ):
            assert not p._looks_like_scene_url(url), f"false pos: {url}"


class TestSameEtld1:
    def test_same_host(self):
        from bulk_downloader import playlist_extractor as p
        assert p._is_same_etld1("https://wow.com/a", "https://wow.com/b") is True

    def test_subdomain_matches(self):
        from bulk_downloader import playlist_extractor as p
        assert p._is_same_etld1("https://www.wow.com/a", "https://wow.com/b") is True

    def test_different_sites(self):
        from bulk_downloader import playlist_extractor as p
        assert p._is_same_etld1("https://a.com", "https://b.com") is False

    def test_garbage(self):
        from bulk_downloader import playlist_extractor as p
        assert p._is_same_etld1("not a url", "also not") is False
        assert p._is_same_etld1("", "") is False


class TestPaginationGuesser:
    def test_appends_query(self):
        from bulk_downloader import playlist_extractor as p
        assert p._guess_next_page_url(
            "https://x/category/blonde/", 2) == "https://x/category/blonde/?page=2"

    def test_increments_existing_query(self):
        from bulk_downloader import playlist_extractor as p
        result = p._guess_next_page_url(
            "https://x/category/?page=1", 3)
        assert "page=3" in result

    def test_replaces_path_segment(self):
        from bulk_downloader import playlist_extractor as p
        result = p._guess_next_page_url(
            "https://x/category/page/1/", 2)
        assert "/page/2/" in result

    def test_empty_url(self):
        from bulk_downloader import playlist_extractor as p
        assert p._guess_next_page_url("", 2) == ""

    def test_page_1_returns_empty(self):
        """Page 1 IS the current page; no next-page needed."""
        from bulk_downloader import playlist_extractor as p
        assert p._guess_next_page_url("https://x/", 1) == ""


class TestExtractFailOpen:
    def test_empty_url(self):
        from bulk_downloader import playlist_extractor as p
        r = p.extract_playlist_urls(None, "")
        assert r.ok is False
        assert r.error == "empty_url"

    def test_no_page(self):
        from bulk_downloader import playlist_extractor as p
        r = p.extract_playlist_urls(None, "https://x.com/cat/blonde")
        assert r.ok is False
        assert r.error == "page_is_none"

    class _Page:
        def __init__(self, *, fail_on_goto):
            self.fail_on_goto = fail_on_goto
            self.goto_calls = []
            self.evaluate_calls = 0

        def goto(self, url, *, wait_until, timeout):
            self.goto_calls.append((url, wait_until, timeout))
            if len(self.goto_calls) == self.fail_on_goto:
                raise RuntimeError("fixture middle page failed")

        def evaluate(self, javascript):
            assert "querySelectorAll" in javascript
            self.evaluate_calls += 1
            return [{
                "url": "https://example.com/video/fixture.mp4",
                "title": "Fixture scene",
            }]

    @staticmethod
    def _assert_partial_refused(result):
        assert result.ok is False, f"partial playlist must be non-ok: {result}"
        assert result.truncated is True
        assert result.error == "page_load_failed:RuntimeError"

    def test_row735_middle_page_failure_is_truncated_and_non_ok(self):
        from bulk_downloader import playlist_extractor as p
        listing = "https://example.com/category/fixture"
        scene = "https://example.com/video/fixture.mp4"
        failed = self._Page(fail_on_goto=2)
        partial = p.extract_playlist_urls(failed, listing, max_pages=5)

        assert len(failed.goto_calls) == 2
        assert failed.evaluate_calls == 1
        assert partial.page_count == 1
        assert partial.urls == [scene]
        assert partial.titles == {scene: "Fixture scene"}
        self._assert_partial_refused(partial)

        complete_page = self._Page(fail_on_goto=None)
        complete = p.extract_playlist_urls(complete_page, listing, max_pages=1)
        assert len(complete_page.goto_calls) == 1
        assert complete_page.evaluate_calls == 1
        assert complete.page_count == 1
        assert complete.urls == [scene]
        assert complete.ok is True
        assert complete.truncated is False
        assert complete.error == ""
        with pytest.raises(AssertionError, match="partial playlist must be non-ok"):
            self._assert_partial_refused(complete)


# ─── community_scrapers ───────────────────────────────────────────


class TestStashDirValidation:
    def test_empty_dir(self):
        from bulk_downloader import community_scrapers as cs
        ok, err = cs._validate_stash_dir("")
        assert ok is False
        assert "not_configured" in err

    def test_nonexistent_dir(self):
        from bulk_downloader import community_scrapers as cs
        ok, err = cs._validate_stash_dir("/totally/nonexistent")
        assert ok is False
        assert "not_a_directory" in err

    def test_existing_dir(self):
        from bulk_downloader import community_scrapers as cs
        with tempfile.TemporaryDirectory() as tmp:
            ok, err = cs._validate_stash_dir(tmp)
            assert ok is True
            assert err == ""


class TestCommunityScrapersFailOpen:
    def test_fetch_index_no_httpx(self):
        from bulk_downloader import community_scrapers as cs
        with patch.object(cs, "_httpx_available", return_value=False):
            entries, err = cs.fetch_index(force_refresh=True)
            assert entries == []
            assert "httpx" in err

    def test_install_no_stash_dir(self):
        from bulk_downloader import community_scrapers as cs
        result = cs.install_one("anything", stash_dir="")
        assert result.ok is False
        assert "stash_scrapers_dir_not_configured" in result.error

    def test_install_scraper_not_in_index(self):
        from bulk_downloader import community_scrapers as cs
        with tempfile.TemporaryDirectory() as tmp:
            result = cs.install_one(
                "nonexistent_scraper",
                stash_dir=tmp,
                entries=[],  # empty index
            )
            assert result.ok is False
            assert "scraper_not_found" in result.error


class TestCompanionFileParser:
    def test_finds_python_companion(self):
        from bulk_downloader import community_scrapers as cs
        yml = b"""
scrapers:
  Foo:
    script:
      - "FooScraper.py"
"""
        refs = cs._find_companion_files(yml)
        assert "FooScraper.py" in refs

    def test_finds_js_companion(self):
        from bulk_downloader import community_scrapers as cs
        yml = b"""
scrapers:
  Foo:
    script:
      - "scripts/foo.js"
"""
        refs = cs._find_companion_files(yml)
        assert "scripts/foo.js" in refs

    def test_no_script_returns_empty(self):
        from bulk_downloader import community_scrapers as cs
        yml = b"""
name: "Foo"
performerByURL:
  - action: scrapeXPath
"""
        assert cs._find_companion_files(yml) == []

    def test_empty_yml(self):
        from bulk_downloader import community_scrapers as cs
        assert cs._find_companion_files(b"") == []


class TestManifestEmpty:
    def setup_method(self):
        # Use isolated cache dir for these tests
        self._tmpdir = tempfile.mkdtemp(prefix="scrapers_test_")
        self._original_cache_dir = None

    def teardown_method(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_load_manifest_empty_when_missing(self):
        from bulk_downloader import community_scrapers as cs
        # Patch cache dir
        with patch.object(cs, "_cache_dir",
                          return_value=type(cs._cache_dir())(self._tmpdir)):
            m = cs.load_manifest()
            assert m == {"installed": {}}


# ─── lazy_player_wait ─────────────────────────────────────────────


class TestLazyPlayerFailOpen:
    def test_empty_selector(self):
        from bulk_downloader import lazy_player_wait as l
        found, reason = l.wait_for_selector_via_observer(None, "")
        assert found is False
        assert reason == "empty_selector"

    def test_none_page(self):
        from bulk_downloader import lazy_player_wait as l
        found, reason = l.wait_for_selector_via_observer(None, "video")
        assert found is False
        assert reason == "page_is_none"

    def test_evaluate_raises(self):
        from bulk_downloader import lazy_player_wait as l
        page = MagicMock()
        page.evaluate.side_effect = RuntimeError("page closed")
        found, reason = l.wait_for_selector_via_observer(page, "video")
        assert found is False
        assert "evaluate_raised" in reason
        assert "RuntimeError" in reason

    def test_happy_path(self):
        from bulk_downloader import lazy_player_wait as l
        page = MagicMock()
        page.evaluate.return_value = {"found": True, "reason": "mutation_match"}
        found, reason = l.wait_for_selector_via_observer(page, "video")
        assert found is True
        assert reason == "mutation_match"

    def test_timeout_path(self):
        from bulk_downloader import lazy_player_wait as l
        page = MagicMock()
        page.evaluate.return_value = {"found": False, "reason": "timeout"}
        found, reason = l.wait_for_selector_via_observer(page, "video")
        assert found is False
        assert reason == "timeout"

    def test_unexpected_result_type(self):
        from bulk_downloader import lazy_player_wait as l
        page = MagicMock()
        page.evaluate.return_value = "unexpected string"
        found, reason = l.wait_for_selector_via_observer(page, "video")
        assert found is False
        assert "unexpected_result_type" in reason

    def test_wait_for_any_video_uses_combined_selector(self):
        """The convenience wrapper should pass a comma-joined selector."""
        from bulk_downloader import lazy_player_wait as l
        page = MagicMock()
        page.evaluate.return_value = {"found": True, "reason": "already_present"}
        l.wait_for_any_video_via_observer(page,
                                            extra_selectors=["iframe.player"])
        call_args = page.evaluate.call_args
        # call_args is (args, kwargs); first positional is JS, second is dict
        params = call_args[0][1]
        assert "iframe.player" in params["selector"]
        assert "video[src]" in params["selector"]


# ─── Flask routes ─────────────────────────────────────────────────


class TestFlaskRoutes:
    def _rules(self):
        from bulk_downloader.app import app
        return {r.rule for r in app.url_map.iter_rules()}

    def test_ytdlp_archive_stats(self):
        assert "/api/ytdlp_archive/stats" in self._rules()

    def test_ytdlp_archive_derive(self):
        assert "/api/ytdlp_archive/derive" in self._rules()

    def test_community_scrapers_status(self):
        assert "/api/community_scrapers/status" in self._rules()

    def test_community_scrapers_index(self):
        assert "/api/community_scrapers/index" in self._rules()

    def test_community_scrapers_install(self):
        assert "/api/community_scrapers/install" in self._rules()

    def test_community_scrapers_remove(self):
        assert "/api/community_scrapers/remove" in self._rules()

    def test_community_scrapers_bulk_install(self):
        assert "/api/community_scrapers/bulk_install" in self._rules()


# ─── Config fields ────────────────────────────────────────────────


class TestConfigFields:
    def test_playlist_fields(self):
        from bulk_downloader.app_kernel import CFG_FIELDS, DEFAULTS
        assert "use_playlist_extractor" in CFG_FIELDS
        assert "playlist_max_pages" in CFG_FIELDS
        assert DEFAULTS.get("use_playlist_extractor") is False
        assert DEFAULTS.get("playlist_max_pages") == 1

    def test_ytdlp_archive_fields(self):
        from bulk_downloader.app_kernel import CFG_FIELDS, DEFAULTS
        assert "use_ytdlp_archive" in CFG_FIELDS
        assert "ytdlp_archive_path" in CFG_FIELDS
        assert DEFAULTS.get("use_ytdlp_archive") is False
        assert DEFAULTS.get("ytdlp_archive_path") == ""

    def test_mutation_observer_fields(self):
        from bulk_downloader.app_kernel import CFG_FIELDS, DEFAULTS
        assert "use_mutation_observer" in CFG_FIELDS
        assert "mutation_observer_timeout_ms" in CFG_FIELDS
        assert DEFAULTS.get("use_mutation_observer") is False
        assert DEFAULTS.get("mutation_observer_timeout_ms") == 15000


# ─── Runner integration ───────────────────────────────────────────


class TestRunnerHooks:
    def test_playlist_available_flag(self):
        from bulk_downloader import runner
        assert hasattr(runner, "_PLAYLIST_AVAILABLE")

    def test_ytdlp_arch_available_flag(self):
        from bulk_downloader import runner
        assert hasattr(runner, "_YTDLP_ARCH_AVAILABLE")

    def test_lazy_player_available_flag(self):
        from bulk_downloader import runner
        assert hasattr(runner, "_LAZY_PLAYER_AVAILABLE")

    def test_playlist_expand_method(self):
        from bulk_downloader.runner import SiteRunner
        assert hasattr(SiteRunner, "_playlist_expand_one")

    def test_row735_runner_refuses_partial_non_ok_playlist(self, monkeypatch):
        from bulk_downloader import runner

        listing = "https://example.com/category/fixture"
        scene = "https://example.com/video/fixture.mp4"
        extractor_calls = []
        pauses = []

        def extract(page, listing_url, *, template, max_pages):
            extractor_calls.append((page, listing_url, template, max_pages))
            return SimpleNamespace(
                ok=False,
                urls=[scene],
                titles={scene: "must not be consumed"},
            )

        fake_cloak = SimpleNamespace(
            cloaked_page=lambda **kwargs: nullcontext("fixture-page")
        )
        fake_keeper = SimpleNamespace(
            pause_site_keepers=lambda site_id: pauses.append(site_id)
        )
        package = sys.modules["bulk_downloader"]
        monkeypatch.setitem(sys.modules, "bulk_downloader.cloak", fake_cloak)
        monkeypatch.setitem(sys.modules, "bulk_downloader.session_keeper", fake_keeper)
        monkeypatch.setattr(package, "cloak", fake_cloak, raising=False)
        monkeypatch.setattr(package, "session_keeper", fake_keeper, raising=False)
        monkeypatch.setattr(runner, "_PLAYLIST_AVAILABLE", True)
        monkeypatch.setattr(
            runner, "_playlist", SimpleNamespace(extract_playlist_urls=extract)
        )
        subject = SimpleNamespace(
            config={"playlist_max_pages": 5},
            site_id="row735",
            _lock=nullcontext(),
            _listing_titles={},
        )

        result = runner.SiteRunner._playlist_expand_one(subject, listing)

        assert pauses == ["row735"]
        assert len(extractor_calls) == 1
        assert extractor_calls[0][1:] == (listing, subject.config, 5)
        assert result == []
        assert subject._listing_titles == {}

        successful_playlist = SimpleNamespace(
            extract_playlist_urls=lambda *args, **kwargs: SimpleNamespace(
                ok=True,
                urls=[scene],
                titles={scene: "Fixture scene"},
            )
        )
        monkeypatch.setattr(runner, "_playlist", successful_playlist)
        successful = runner.SiteRunner._playlist_expand_one(subject, listing)
        scene_count = successful.count(scene)
        assert scene_count == 1, (
            f"playlist consumer returned the extracted scene "
            f"{scene_count} times")

    def test_lazy_video_method(self):
        from bulk_downloader.runner import SiteRunner
        assert hasattr(SiteRunner, "_wait_for_lazy_video")


# ─── bdctl integration ───────────────────────────────────────────


class TestBdctl:
    def test_cmd_ytdlp_archive_stats(self):
        import bdctl
        assert hasattr(bdctl, "cmd_ytdlp_archive_stats")

    def test_cmd_scrapers_list(self):
        import bdctl
        assert hasattr(bdctl, "cmd_scrapers_list")

    def test_cmd_scrapers_available(self):
        import bdctl
        assert hasattr(bdctl, "cmd_scrapers_available")

    def test_cmd_scrapers_install(self):
        import bdctl
        assert hasattr(bdctl, "cmd_scrapers_install")
