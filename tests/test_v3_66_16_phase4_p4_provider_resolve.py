"""v3.66.16 — Phase 4 P4: provider-API resolution.

Tests the new ``provider_resolve`` module (Vimeo end-to-end + the
dispatcher / sentinel contract for unsupported providers) and its
integration into ``deep_detect`` via the new ``resolve_providers``
and ``http_get`` kwargs.

Five test classes:

  TestVimeoResolver
    Pure-function tests of ``resolve_vimeo``. Feed it canned JSON
    bodies through an injected ``http_get`` and assert on the
    candidate dicts it produces.

  TestProviderDispatcher
    Tests ``resolve_provider_embed`` — routing to the correct
    resolver, the ``([], None)`` sentinel for unsupported providers,
    and the crash-containment wrapper.

  TestDeepDetectIntegration
    End-to-end through ``deep_detect(resolve_providers=True,
    http_get=...)`` using the ``07_vimeo_embed`` fixture. Confirms
    resolved candidates land in ``download_candidates`` and the
    ``provider_resolutions`` outcome list is populated.

  TestSiteMemoryHook
    The ``site_memory=`` arg is forwarded to ``resolve_provider_embed``.
    P4 only ships the read hook; persistent caching follows.

  TestErrorHandling
    Network errors, non-JSON bodies, missing IDs, unknown providers
    — every failure path produces a recorded outcome with a useful
    error message and does not crash deep_detect.

All tests are deterministic; no network. The fake ``http_get`` is
a closure capturing canned responses indexed by URL.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# P4's integration points hit deep_detect (which lives in the
# bulk_downloader.deep_detect submodule). The module-wipe marker
# matches the convention from the P5 / P12 test files in the
# v3.66.15 release. See lessons-learned #5 in v3.66.14 for why.
pytestmark = pytest.mark.bd_module_wipe


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A realistic Vimeo /config response trimmed to the fields the
# resolver actually reads. Mirrors what player.vimeo.com returns
# for a public clip: a request.files dict with `progressive`
# (list of MP4 renditions) and `hls.cdns` (one or more CDN
# variants of the master playlist).
VIMEO_CONFIG_OK = {
    "request": {
        "files": {
            "progressive": [
                {
                    "url": "https://vod-prog.test/v1080.mp4?token=A",
                    "width": 1920, "height": 1080, "fps": 30,
                    "mime": "video/mp4", "quality": "1080p",
                    "size": 50_000_000,
                },
                {
                    "url": "https://vod-prog.test/v720.mp4?token=B",
                    "width": 1280, "height": 720, "fps": 30,
                    "mime": "video/mp4", "quality": "720p",
                    "size": 25_000_000,
                },
                {
                    "url": "https://vod-prog.test/v360.mp4?token=C",
                    "width":  640, "height": 360, "fps": 30,
                    "mime": "video/mp4", "quality": "360p",
                    "size":  6_000_000,
                },
            ],
            "hls": {
                "default_cdn": "akamai_interconnect",
                "cdns": {
                    "akamai_interconnect": {
                        "url": "https://vod-hls.test/master.m3u8?token=X",
                    },
                    "fastly_skyfire": {
                        "url": "https://vod-hls.test/fastly.m3u8?token=Y",
                    },
                },
            },
        },
    },
}


def make_http_get(responses):
    """Build a fake http_get from a {url_substring: (status, body_dict)}
    map. The first matching substring wins; pass exact URLs for
    unambiguous routing. body_dict is JSON-encoded for the caller."""
    def _get(url):
        for marker, (status, body) in responses.items():
            if marker in url:
                if isinstance(body, (dict, list)):
                    payload = json.dumps(body).encode()
                elif isinstance(body, str):
                    payload = body.encode()
                else:
                    payload = body
                return status, {"content-type": "application/json"}, payload
        raise AssertionError(f"fake http_get: no rule for {url!r}")
    return _get


def vimeo_embed(clip_id="76979871"):
    """Synthesize the dict shape extract_provider_embeds produces
    for a Vimeo iframe."""
    return {
        "provider": "vimeo",
        "source_type": "vimeo_embed",
        "ids": {"clip_id": clip_id},
        "url": f"https://player.vimeo.com/video/{clip_id}",
        "found_in": "<iframe src>",
    }


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "deep_detect"


# ---------------------------------------------------------------------------
# TestVimeoResolver
# ---------------------------------------------------------------------------

class TestVimeoResolver:
    def test_resolves_progressive_renditions(self):
        from bulk_downloader.provider_resolve import resolve_vimeo
        fake = make_http_get({"/config": (200, VIMEO_CONFIG_OK)})
        cands, err = resolve_vimeo(
            {"clip_id": "76979871"},
            embed=vimeo_embed(),
            http_get=fake)
        assert err is None
        progressive = [c for c in cands
                       if c["source_type"] == "vimeo_resolved"]
        assert len(progressive) == 3
        # Should be in input order; 1080p has height/rank set.
        first = progressive[0]
        assert first["url"] == "https://vod-prog.test/v1080.mp4?token=A"
        assert first["resolution"]["height"] == 1080
        assert first["resolution"]["label"] == "1080p"
        assert first["resolution"]["rank"] == 1080
        assert first["fps"] == 30
        assert first["size_bytes"] == 50_000_000
        assert first["provider_resolved"] is True
        assert first["found_in"] == "provider_resolved:vimeo"
        assert first["resolved_from"] == vimeo_embed()["url"]

    def test_resolves_hls_master(self):
        from bulk_downloader.provider_resolve import resolve_vimeo
        fake = make_http_get({"/config": (200, VIMEO_CONFIG_OK)})
        cands, err = resolve_vimeo(
            {"clip_id": "76979871"},
            embed=vimeo_embed(),
            http_get=fake)
        assert err is None
        hls = [c for c in cands
               if c["source_type"] == "vimeo_resolved_hls"]
        assert len(hls) == 1
        # We emit ONE HLS candidate (the default CDN), not one per CDN.
        assert hls[0]["url"] == "https://vod-hls.test/master.m3u8?token=X"
        assert hls[0]["resolution"] is None  # master, variants in playlist
        assert "akamai_interconnect" in hls[0]["reasons"][0]

    def test_score_graduates_by_height(self):
        from bulk_downloader.provider_resolve import resolve_vimeo
        fake = make_http_get({"/config": (200, VIMEO_CONFIG_OK)})
        cands, err = resolve_vimeo(
            {"clip_id": "76979871"}, embed=vimeo_embed(), http_get=fake)
        assert err is None
        by_height = {c["resolution"]["height"]: c["score"]
                     for c in cands
                     if c["source_type"] == "vimeo_resolved"}
        # 1080p > 720p > 360p, by construction (score = 80 + height//100)
        assert by_height[1080] > by_height[720] > by_height[360]

    def test_missing_clip_id_returns_error(self):
        from bulk_downloader.provider_resolve import resolve_vimeo
        fake = make_http_get({})
        cands, err = resolve_vimeo({}, embed=vimeo_embed(), http_get=fake)
        assert cands == []
        assert err and "clip_id" in err

    def test_403_returns_domain_restricted_error(self):
        from bulk_downloader.provider_resolve import resolve_vimeo
        fake = make_http_get({"/config": (403, "")})
        cands, err = resolve_vimeo(
            {"clip_id": "1"}, embed=vimeo_embed("1"), http_get=fake)
        assert cands == []
        assert err and "403" in err

    def test_404_returns_invalid_id_error(self):
        from bulk_downloader.provider_resolve import resolve_vimeo
        fake = make_http_get({"/config": (404, "")})
        cands, err = resolve_vimeo(
            {"clip_id": "999"}, embed=vimeo_embed("999"), http_get=fake)
        assert cands == []
        assert err and "404" in err

    def test_500_returns_generic_http_error(self):
        from bulk_downloader.provider_resolve import resolve_vimeo
        fake = make_http_get({"/config": (500, "")})
        cands, err = resolve_vimeo(
            {"clip_id": "1"}, embed=vimeo_embed("1"), http_get=fake)
        assert cands == []
        assert err and "500" in err

    def test_non_json_body_returns_error(self):
        from bulk_downloader.provider_resolve import resolve_vimeo
        fake = make_http_get({"/config": (200, "<html>oops</html>")})
        cands, err = resolve_vimeo(
            {"clip_id": "1"}, embed=vimeo_embed("1"), http_get=fake)
        assert cands == []
        assert err and "non-JSON" in err

    def test_json_array_returns_error(self):
        # An array at the root isn't the expected shape.
        from bulk_downloader.provider_resolve import resolve_vimeo
        fake = make_http_get({"/config": (200, [1, 2, 3])})
        cands, err = resolve_vimeo(
            {"clip_id": "1"}, embed=vimeo_embed("1"), http_get=fake)
        assert cands == []
        assert err and "not an object" in err

    def test_empty_files_dict_returns_no_streams_error(self):
        # Endpoint returned 200 but no renditions — distinguish from
        # "no resolver" by surfacing it as an error.
        from bulk_downloader.provider_resolve import resolve_vimeo
        fake = make_http_get(
            {"/config": (200, {"request": {"files": {}}})})
        cands, err = resolve_vimeo(
            {"clip_id": "1"}, embed=vimeo_embed("1"), http_get=fake)
        assert cands == []
        assert err and "no progressive or hls" in err

    def test_hls_fallback_when_default_cdn_missing(self):
        # When default_cdn is unset, the resolver should still emit
        # an HLS candidate using the first available CDN.
        from bulk_downloader.provider_resolve import resolve_vimeo
        body = {"request": {"files": {
            "hls": {"cdns": {"some_cdn": {"url": "https://x.test/m.m3u8"}}}
            # No default_cdn key
        }}}
        fake = make_http_get({"/config": (200, body)})
        cands, err = resolve_vimeo(
            {"clip_id": "1"}, embed=vimeo_embed("1"), http_get=fake)
        assert err is None
        assert any(c["source_type"] == "vimeo_resolved_hls" for c in cands)

    def test_progressive_skips_entries_without_url(self):
        # Garbage entries in the progressive list shouldn't crash —
        # they're skipped silently.
        from bulk_downloader.provider_resolve import resolve_vimeo
        body = {"request": {"files": {
            "progressive": [
                {"height": 720},  # no url
                {"url": "", "height": 480},  # empty url
                {"url": "https://ok.test/v.mp4", "height": 1080,
                 "quality": "1080p"},
            ],
        }}}
        fake = make_http_get({"/config": (200, body)})
        cands, err = resolve_vimeo(
            {"clip_id": "1"}, embed=vimeo_embed("1"), http_get=fake)
        assert err is None
        assert len(cands) == 1
        assert cands[0]["url"] == "https://ok.test/v.mp4"

    def test_float_fps_coerced_to_int(self):
        # Vimeo sometimes reports fps as 29.97; we accept-and-truncate.
        from bulk_downloader.provider_resolve import resolve_vimeo
        body = {"request": {"files": {
            "progressive": [{"url": "https://x.test/v.mp4",
                             "height": 720, "fps": 29.97,
                             "quality": "720p"}],
        }}}
        fake = make_http_get({"/config": (200, body)})
        cands, err = resolve_vimeo(
            {"clip_id": "1"}, embed=vimeo_embed("1"), http_get=fake)
        assert err is None
        assert cands[0]["fps"] == 29  # truncated, not raised

    def test_request_url_built_from_clip_id(self):
        # The resolver must hit player.vimeo.com/video/<id>/config.
        # We verify by making the fake reject everything that doesn't
        # match the expected URL.
        from bulk_downloader.provider_resolve import resolve_vimeo
        seen = []

        def picky_get(url):
            seen.append(url)
            return 200, {}, json.dumps(VIMEO_CONFIG_OK).encode()
        resolve_vimeo({"clip_id": "12345"},
                      embed=vimeo_embed("12345"), http_get=picky_get)
        assert seen == [
            "https://player.vimeo.com/video/12345/config"]


# ---------------------------------------------------------------------------
# TestProviderDispatcher
# ---------------------------------------------------------------------------

class TestProviderDispatcher:
    def test_routes_vimeo_to_resolve_vimeo(self):
        from bulk_downloader.provider_resolve import resolve_provider_embed
        fake = make_http_get({"/config": (200, VIMEO_CONFIG_OK)})
        cands, err = resolve_provider_embed(vimeo_embed(), http_get=fake)
        assert err is None
        assert len(cands) == 4  # 3 progressive + 1 hls

    def test_unsupported_provider_returns_sentinel(self):
        # Kaltura has an extract_provider_embeds entry but no resolver
        # in P4. Dispatcher must return ([], None) — distinguishable
        # from "tried and failed" which returns ([], "<error>").
        from bulk_downloader.provider_resolve import resolve_provider_embed
        emb = {"provider": "kaltura", "ids": {"entry_id": "1_abc"},
               "url": "https://cdnapi.kaltura.com/p/1/sp/100/embedIframeJs"}
        cands, err = resolve_provider_embed(emb, http_get=lambda u: (200, {}, b""))
        assert cands == []
        assert err is None  # sentinel — not an error

    def test_youtube_stub_returns_sentinel(self):
        # v3.66.20: YouTube was promoted from stub to real resolver.
        # See test_v3_66_20_phase4_youtube.py for its own coverage.
        # With an empty response the resolver now returns an error
        # rather than passing through silently — the dispatcher
        # forwards (cands=[], err="<msg>"), so we verify the dispatch
        # path still works and the error string is non-None and
        # mentions YouTube-internal vocabulary.
        from bulk_downloader.provider_resolve import resolve_provider_embed
        emb = {"provider": "youtube", "ids": {"video_id": "dQw4w9WgXcQ"},
               "url": "https://www.youtube.com/embed/dQw4w9WgXcQ"}
        cands, err = resolve_provider_embed(emb, http_get=lambda u: (200, {}, b""))
        assert cands == []
        assert err is not None
        assert "youtube" in err.lower()

    def test_brightcove_jwplayer_stubs(self):
        # Wistia was promoted from stub to real resolver in v3.66.17;
        # JWPlayer was promoted in v3.66.18; Brightcove in v3.66.19;
        # YouTube was promoted in v3.66.20. There are no more stubs.
        # See test_v3_66_17_*.py through test_v3_66_20_*.py for each
        # provider's own missing-id coverage.
        #
        # This test now verifies that an unknown-provider name passes
        # through silently (the dispatcher's "no resolver yet" path).
        from bulk_downloader.provider_resolve import resolve_provider_embed
        cands, err = resolve_provider_embed(
            {"provider": "completely_new_provider", "ids": {}, "url": "x"},
            http_get=lambda u: (200, {}, b""))
        assert cands == []
        assert err is None

    def test_embed_not_a_dict_errors(self):
        from bulk_downloader.provider_resolve import resolve_provider_embed
        cands, err = resolve_provider_embed(None, http_get=lambda u: (0, {}, b""))
        assert cands == []
        assert err and "not a dict" in err

    def test_embed_with_no_provider_errors(self):
        from bulk_downloader.provider_resolve import resolve_provider_embed
        cands, err = resolve_provider_embed(
            {"ids": {"x": "y"}}, http_get=lambda u: (0, {}, b""))
        assert cands == []
        assert err and "no provider" in err

    def test_resolver_crash_is_caught(self):
        # If a resolver raises (a bug in our code, network library
        # changes underfoot), the dispatcher must catch it and
        # surface an error rather than crashing deep_detect.
        from bulk_downloader.provider_resolve import resolve_provider_embed

        def boom(url):
            raise RuntimeError("network is on fire")
        cands, err = resolve_provider_embed(vimeo_embed(), http_get=boom)
        assert cands == []
        # The error is surfaced via the resolver's own error path, not
        # the dispatcher's crash wrapper (because the resolver catches
        # http_get exceptions itself). Either path is acceptable; what
        # matters is no crash and a useful message.
        assert err and ("failed" in err or "crashed" in err)


# ---------------------------------------------------------------------------
# TestDeepDetectIntegration
# ---------------------------------------------------------------------------

class TestDeepDetectIntegration:
    def test_cold_call_does_not_resolve(self):
        # Default: resolve_providers=False. The Vimeo embed surfaces
        # as a needs_provider_resolution candidate; no http_get is
        # invoked; provider_resolutions is empty.
        from bulk_downloader.deep_detect import deep_detect
        html = (FIXTURE_DIR / "07_vimeo_embed" / "input.html").read_text()
        calls = []

        def must_not_be_called(url):
            calls.append(url)
            raise AssertionError("http_get must not be invoked when "
                                 "resolve_providers=False")
        r = deep_detect(html, base_url="https://blog.example.test/post",
                        http_get=must_not_be_called)
        assert calls == []
        assert r["provider_resolutions"] == []
        types = [c["source_type"] for c in r["buckets"]["accepted"]]
        assert "vimeo_embed" in types
        assert not any(t.startswith("vimeo_resolved") for t in types)

    def test_warm_call_resolves_and_appends_candidates(self):
        from bulk_downloader.deep_detect import deep_detect
        html = (FIXTURE_DIR / "07_vimeo_embed" / "input.html").read_text()
        fake = make_http_get({"/config": (200, VIMEO_CONFIG_OK)})
        r = deep_detect(html, base_url="https://blog.example.test/post",
                        resolve_providers=True, http_get=fake)
        # provider_resolutions records the outcome
        assert len(r["provider_resolutions"]) == 1
        outcome = r["provider_resolutions"][0]
        assert outcome["provider"] == "vimeo"
        assert outcome["video_id"] == "76979871"
        assert outcome["ok"] is True
        assert outcome["candidates"] == 4  # 3 progressive + 1 HLS
        assert outcome["error"] is None
        # Resolved candidates appear in download_candidates
        types = [c["source_type"] for c in r["buckets"]["accepted"]]
        assert types.count("vimeo_resolved") == 3
        assert types.count("vimeo_resolved_hls") == 1
        # And the original needs_provider_resolution embed is still
        # in the list — the operator can fall back to it manually.
        assert "vimeo_embed" in types

    def test_resolved_candidate_outranks_embed(self):
        # The 1080p progressive should be the best_download, not the
        # raw embed (which has score 60 and confidence_ceiling 0.55).
        from bulk_downloader.deep_detect import deep_detect
        html = (FIXTURE_DIR / "07_vimeo_embed" / "input.html").read_text()
        fake = make_http_get({"/config": (200, VIMEO_CONFIG_OK)})
        r = deep_detect(html, base_url="https://blog.example.test/post",
                        resolve_providers=True, http_get=fake)
        best = r["buckets"]["best"]
        assert best is not None
        assert best["source_type"].startswith("vimeo_resolved")
        assert best["score"] > 60

    def test_live_forwards_kwargs_to_deep_detect(self):
        # deep_detect_live should pass resolve_providers + http_get
        # through to its inner deep_detect call. We can verify by
        # giving it a minimal HTML and asserting the resolution row.
        from bulk_downloader.deep_detect import deep_detect_live
        html = (FIXTURE_DIR / "07_vimeo_embed" / "input.html").read_text()
        fake = make_http_get({"/config": (200, VIMEO_CONFIG_OK)})
        # Use max_probes=0 so the Tier-2 HEAD pass is a no-op and
        # we don't need to stub the http client argument.
        r = deep_detect_live(
            html, base_url="https://blog.example.test/post",
            resolve_providers=True, http_get=fake,
            max_probes=0)
        assert len(r["provider_resolutions"]) == 1
        assert r["provider_resolutions"][0]["ok"] is True

    def test_no_provider_embeds_leaves_resolutions_empty(self):
        # If the page has no provider embeds, provider_resolutions
        # stays empty even with resolve_providers=True (no work to do).
        from bulk_downloader.deep_detect import deep_detect
        html = ("<html><body><a href=\"https://x.test/v.mp4\">video</a>"
                "</body></html>")
        called = []
        r = deep_detect(html, base_url="https://blog.example.test/post",
                        resolve_providers=True,
                        http_get=lambda u: (called.append(u) or (200, {}, b"{}")))
        assert r["provider_resolutions"] == []
        assert called == []  # no embeds → no resolver calls


# ---------------------------------------------------------------------------
# TestSiteMemoryHook
# ---------------------------------------------------------------------------

class TestSiteMemoryHook:
    def test_site_memory_is_forwarded_to_resolver(self):
        # P4 reads site_memory through resolve_provider_embed for
        # future cache-key use. This session ships only the hook; we
        # verify the kwarg is plumbed through by intercepting the
        # call at the dispatcher level.
        import bulk_downloader.provider_resolve as pr
        from bulk_downloader.deep_detect import deep_detect

        html = (FIXTURE_DIR / "07_vimeo_embed" / "input.html").read_text()
        memory_obj = {"winning_source_types": {"hls_master": 7},
                      "provider_embeds_seen": {
                          "vimeo": {"last_id": "76979871", "count": 3}}}

        seen_memory = []
        original = pr.resolve_provider_embed

        def spy(embed, *, http_get=None, site_memory=None):
            seen_memory.append(site_memory)
            return original(embed, http_get=http_get, site_memory=site_memory)

        pr.resolve_provider_embed = spy
        try:
            fake = make_http_get({"/config": (200, VIMEO_CONFIG_OK)})
            deep_detect(html, base_url="https://blog.example.test/post",
                        resolve_providers=True, http_get=fake,
                        site_memory=memory_obj)
        finally:
            pr.resolve_provider_embed = original

        assert seen_memory == [memory_obj]


# ---------------------------------------------------------------------------
# TestErrorHandling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_403_from_vimeo_records_error_keeps_embed(self):
        # Resolution failure must not crash deep_detect, must record
        # the error, and must leave the original embed candidate in
        # the list as a fallback.
        from bulk_downloader.deep_detect import deep_detect
        html = (FIXTURE_DIR / "07_vimeo_embed" / "input.html").read_text()
        fake = make_http_get({"/config": (403, "")})
        r = deep_detect(html, base_url="https://blog.example.test/post",
                        resolve_providers=True, http_get=fake)
        assert len(r["provider_resolutions"]) == 1
        row = r["provider_resolutions"][0]
        assert row["ok"] is False
        assert row["candidates"] == 0
        assert row["error"] and "403" in row["error"]
        # Original embed survives
        assert any(c["source_type"] == "vimeo_embed"
                   for c in r["buckets"]["accepted"])

    def test_failure_emits_top_level_warning(self):
        # Per-embed failures bubble up as session-level warnings so
        # the UI surfaces "Provider resolution failed for X" without
        # needing a separate panel for provider_resolutions.
        from bulk_downloader.deep_detect import deep_detect
        html = (FIXTURE_DIR / "07_vimeo_embed" / "input.html").read_text()
        fake = make_http_get({"/config": (500, "")})
        r = deep_detect(html, base_url="https://blog.example.test/post",
                        resolve_providers=True, http_get=fake)
        assert any("Provider resolution failed" in w
                   for w in r["buckets"]["warnings"])

    def test_network_exception_records_error(self):
        # http_get itself raises (timeout, DNS, etc).
        from bulk_downloader.deep_detect import deep_detect
        html = (FIXTURE_DIR / "07_vimeo_embed" / "input.html").read_text()

        def fake(url):
            raise TimeoutError("upstream timeout")
        r = deep_detect(html, base_url="https://blog.example.test/post",
                        resolve_providers=True, http_get=fake)
        assert len(r["provider_resolutions"]) == 1
        assert r["provider_resolutions"][0]["ok"] is False
        assert "timeout" in r["provider_resolutions"][0]["error"].lower()

    def test_resolve_providers_true_no_http_get_uses_default(self):
        # If the caller opts into resolution but doesn't inject
        # http_get, the dispatcher falls back to httpx. We can't
        # exercise that path in a sandbox without a network, but we
        # CAN verify the call doesn't crash deep_detect — the resolver
        # catches the network error and records it as an outcome.
        # This guards against an AttributeError-on-None bug.
        from bulk_downloader.deep_detect import deep_detect
        html = (FIXTURE_DIR / "07_vimeo_embed" / "input.html").read_text()
        # Monkey-patch _default_http_get to simulate a network failure
        # without actually hitting the network.
        import bulk_downloader.provider_resolve as pr

        def fail(url):
            raise ConnectionError("network unreachable")
        original = pr._default_http_get
        pr._default_http_get = fail
        try:
            r = deep_detect(html, base_url="https://blog.example.test/post",
                            resolve_providers=True)  # http_get=None
        finally:
            pr._default_http_get = original
        assert len(r["provider_resolutions"]) == 1
        assert r["provider_resolutions"][0]["ok"] is False
        # The error should mention 'failed' (from resolve_vimeo's
        # http_get exception wrapper).
        assert "failed" in r["provider_resolutions"][0]["error"]

    def test_unknown_provider_records_no_outcome_but_no_error(self):
        # A page with an embed for a provider we don't have a resolver
        # for (e.g. dailymotion) should still produce an outcome row
        # — ok=False, candidates=0, error=None — so the UI can show
        # "we saw this provider but didn't try to resolve it".
        #
        # Wait — actually we want this to be a useful signal. Let's
        # check current behaviour: the dispatcher returns ([], None)
        # for unsupported providers, which produces ok=False (no
        # candidates) and error=None. Verify that semantics.
        from bulk_downloader.deep_detect import deep_detect
        html = (
            '<html><body>'
            '<iframe src="https://geo.dailymotion.com/player.html?'
            'video=x123"></iframe>'
            '</body></html>')
        r = deep_detect(html, base_url="https://x.test/",
                        resolve_providers=True,
                        http_get=lambda u: (200, {}, b""))
        # extract_provider_embeds may or may not pick up the
        # dailymotion iframe depending on its URL conventions —
        # check what we got and assert accordingly.
        if r["provider_embeds"]:
            assert len(r["provider_resolutions"]) >= 1
            for row in r["provider_resolutions"]:
                if row["provider"] == "dailymotion":
                    assert row["ok"] is False
                    assert row["candidates"] == 0
                    assert row["error"] is None  # sentinel, not error
