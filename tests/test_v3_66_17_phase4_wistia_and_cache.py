"""v3.66.17 — Phase 4 P4 continued: Wistia resolver + persistent cache.

Tests two things:

  1. The new ``resolve_wistia`` resolver (network path, error paths,
     parsing variants of the Wistia medias.json schema).

  2. The new persistent resolution cache wired into
     ``resolve_provider_embed`` via ``site_memory`` (read) and the new
     ``cache_write=`` callback (write). Plus the writer factory in
     ``learn.py``.

Test classes
============

  TestWistiaResolver
    Pure-function tests of ``resolve_wistia``. Feed it canned JSON
    bodies through an injected ``http_get`` and assert on the
    candidate dicts it produces — mirrors TestVimeoResolver from the
    v3.66.16 file.

  TestWistiaCacheRead
    Cache lookups via ``site_memory``. Hit vs miss, id-mismatch,
    expired entries, and various malformed site_memory shapes.

  TestWistiaCacheWrite
    The ``cache_write=`` callback fires on successful resolution and
    not on failure. Verifies arguments passed are (provider, id, url, ts).

  TestProviderCacheWriterFactory
    ``learn.make_provider_cache_writer`` produces a callback that
    correctly mutates the bound config. Includes the "no prior
    sighting" path that auto-initializes the learned.deep_detect
    block, and the defensive no-op factory result for non-dict inputs.

  TestCacheIntegration
    End-to-end: an empty config -> first deep_detect run resolves via
    network and writes cache -> second run reads the same config and
    hits cache (no network).

All tests are deterministic; no network. The fake ``http_get`` is a
closure capturing canned responses; ``_now`` is monkeypatched for
TTL determinism.
"""
from __future__ import annotations

import json

import pytest

from bulk_downloader import provider_resolve as pr
from bulk_downloader import learn as ln


# Per the same convention as v3.66.16's P4 test file.
pytestmark = pytest.mark.bd_module_wipe


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A realistic Wistia medias.json response trimmed to the fields the
# resolver actually reads. Mirrors what fast.wistia.net returns for
# a public media: a `media.assets` array containing progressive MP4
# renditions plus an `hls_playlist` asset.
WISTIA_MEDIA_OK = {
    "media": {
        "name": "Example",
        "duration": 245.6,
        "assets": [
            # Progressive renditions
            {
                "type": "original",
                "slug": "original",
                "url": "https://embed-ssl.wistia.com/deliveries/AAA.bin",
                "width": 1920, "height": 1080,
                "bitrate": 4500,
                "content_type": "video/mp4",
                "fileSize": 50_000_000,
            },
            {
                "type": "hd_mp4_video",
                "slug": "hd_mp4_video",
                "url": "https://embed-ssl.wistia.com/deliveries/BBB.bin",
                "width": 1280, "height": 720,
                "bitrate": 2500,
                "content_type": "video/mp4",
                "fileSize": 25_000_000,
            },
            {
                "type": "md_mp4_video",
                "slug": "md_mp4_video",
                "url": "https://embed-ssl.wistia.com/deliveries/CCC.bin",
                "width": 640, "height": 360,
                "bitrate": 1000,
                "content_type": "video/mp4",
                "fileSize": 6_000_000,
            },
            # HLS master
            {
                "type": "hls_playlist",
                "slug": "hls_playlist",
                "url": "https://embed-ssl.wistia.com/deliveries/HHH.m3u8",
                "content_type": "application/vnd.apple.mpegurl",
            },
            # Things we should skip
            {
                "type": "still_image",
                "url": "https://embed-ssl.wistia.com/deliveries/poster.jpg",
                "content_type": "image/jpeg",
            },
            {
                "type": "flash_outbound_audio_descriptions",
                "url": "https://example/dont-touch.flv",
                "content_type": "video/x-flv",
            },
        ],
    },
}


def make_http_get(responses):
    """Build a fake http_get from a {url_substring: (status, body)}
    map. Mirrors the helper in the v3.66.16 test file."""
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


def wistia_embed(hashed_id="abc123xyz"):
    """Synthesize the dict shape extract_provider_embeds produces for
    a Wistia embed."""
    return {
        "provider": "wistia",
        "source_type": "wistia_embed",
        "ids": {"hashed_id": hashed_id},
        "url": f"https://fast.wistia.com/embed/medias/{hashed_id}",
        "found_in": "<script wistia>",
    }


# ---------------------------------------------------------------------------
# TestWistiaResolver
# ---------------------------------------------------------------------------

class TestWistiaResolver:
    def test_resolves_progressive_renditions(self):
        http = make_http_get({"abc123xyz.json": (200, WISTIA_MEDIA_OK)})
        cands, err = pr.resolve_wistia(
            {"hashed_id": "abc123xyz"},
            embed=wistia_embed(),
            http_get=http,
        )
        assert err is None
        prog = [c for c in cands if c["source_type"] == "wistia_resolved"]
        assert len(prog) == 3, f"expected 3 progressive, got {len(prog)}: {prog!r}"
        urls = {c["url"] for c in prog}
        assert "https://embed-ssl.wistia.com/deliveries/AAA.bin" in urls
        assert "https://embed-ssl.wistia.com/deliveries/BBB.bin" in urls
        assert "https://embed-ssl.wistia.com/deliveries/CCC.bin" in urls

    def test_resolves_hls_master(self):
        http = make_http_get({"abc123xyz.json": (200, WISTIA_MEDIA_OK)})
        cands, err = pr.resolve_wistia(
            {"hashed_id": "abc123xyz"},
            embed=wistia_embed(),
            http_get=http,
        )
        assert err is None
        hls = [c for c in cands if c["source_type"] == "wistia_resolved_hls"]
        assert len(hls) == 1
        assert hls[0]["url"] == "https://embed-ssl.wistia.com/deliveries/HHH.m3u8"
        # Score parity with Vimeo HLS (90)
        assert hls[0]["score"] == 90

    def test_skips_non_video_assets(self):
        """`still_image` and `flash_*` assets should not produce
        candidates even though they have URLs."""
        http = make_http_get({"abc.json": (200, WISTIA_MEDIA_OK)})
        cands, _err = pr.resolve_wistia(
            {"hashed_id": "abc"},
            embed=wistia_embed("abc"),
            http_get=http,
        )
        for c in cands:
            assert "poster.jpg" not in c["url"]
            assert "dont-touch.flv" not in c["url"]

    def test_score_graduates_by_height(self):
        http = make_http_get({"abc.json": (200, WISTIA_MEDIA_OK)})
        cands, _err = pr.resolve_wistia(
            {"hashed_id": "abc"},
            embed=wistia_embed("abc"),
            http_get=http,
        )
        prog_by_h = {c["resolution"]["height"]: c["score"]
                     for c in cands if c["source_type"] == "wistia_resolved"}
        # 80 + height//100 per scoring spec
        assert prog_by_h[1080] == 80 + 10
        assert prog_by_h[720] == 80 + 7
        assert prog_by_h[360] == 80 + 3

    def test_resolved_from_propagates_embed_url(self):
        http = make_http_get({"abc.json": (200, WISTIA_MEDIA_OK)})
        emb = wistia_embed("abc")
        cands, _err = pr.resolve_wistia(
            {"hashed_id": "abc"}, embed=emb, http_get=http)
        for c in cands:
            assert c["resolved_from"] == emb["url"]
            assert c["provider_resolved"] is True
            assert c["found_in"] == "provider_resolved:wistia"

    def test_missing_hashed_id_returns_error(self):
        cands, err = pr.resolve_wistia(
            {}, embed=wistia_embed("anything"),
            http_get=lambda u: pytest.fail("should not call http_get"))
        assert cands == []
        assert "missing hashed_id" in err

    def test_404_returns_invalid_id_error(self):
        http = make_http_get({"abc.json": (404, b"not found")})
        cands, err = pr.resolve_wistia(
            {"hashed_id": "abc"}, embed=wistia_embed("abc"), http_get=http)
        assert cands == []
        assert "404" in err
        assert "hashed_id may be invalid" in err

    def test_403_returns_restricted_error(self):
        http = make_http_get({"abc.json": (403, b"forbidden")})
        cands, err = pr.resolve_wistia(
            {"hashed_id": "abc"}, embed=wistia_embed("abc"), http_get=http)
        assert cands == []
        assert "403" in err
        assert "private" in err or "restricted" in err

    def test_500_returns_generic_http_error(self):
        http = make_http_get({"abc.json": (500, b"server fail")})
        cands, err = pr.resolve_wistia(
            {"hashed_id": "abc"}, embed=wistia_embed("abc"), http_get=http)
        assert cands == []
        assert "HTTP 500" in err

    def test_non_json_body_returns_error(self):
        http = make_http_get({"abc.json": (200, b"<html>nope</html>")})
        cands, err = pr.resolve_wistia(
            {"hashed_id": "abc"}, embed=wistia_embed("abc"), http_get=http)
        assert cands == []
        assert "non-JSON" in err

    def test_json_array_returns_error(self):
        http = make_http_get({"abc.json": (200, [1, 2, 3])})
        cands, err = pr.resolve_wistia(
            {"hashed_id": "abc"}, embed=wistia_embed("abc"), http_get=http)
        assert cands == []
        assert "not an object" in err

    def test_empty_assets_returns_no_streams_error(self):
        http = make_http_get({"abc.json": (200, {"media": {"assets": []}})})
        cands, err = pr.resolve_wistia(
            {"hashed_id": "abc"}, embed=wistia_embed("abc"), http_get=http)
        assert cands == []
        assert "no progressive or hls streams" in err

    def test_assets_not_a_list_returns_error(self):
        http = make_http_get({"abc.json": (200, {"media": {"assets": "oops"}})})
        cands, err = pr.resolve_wistia(
            {"hashed_id": "abc"}, embed=wistia_embed("abc"), http_get=http)
        assert cands == []
        assert "not a list" in err

    def test_content_type_fallback_infers_hls(self):
        """If `type` is missing but content_type is mpegurl, treat as HLS."""
        body = {"media": {"assets": [
            {"url": "https://x/master.m3u8",
             "content_type": "application/vnd.apple.mpegurl"},
        ]}}
        http = make_http_get({"abc.json": (200, body)})
        cands, err = pr.resolve_wistia(
            {"hashed_id": "abc"}, embed=wistia_embed("abc"), http_get=http)
        assert err is None
        assert len(cands) == 1
        assert cands[0]["source_type"] == "wistia_resolved_hls"

    def test_content_type_fallback_infers_mp4(self):
        """If `type` is unknown but content_type is video/mp4, treat as
        progressive MP4."""
        body = {"media": {"assets": [
            {"url": "https://x/clip.mp4",
             "content_type": "video/mp4",
             "height": 480, "width": 854},
        ]}}
        http = make_http_get({"abc.json": (200, body)})
        cands, err = pr.resolve_wistia(
            {"hashed_id": "abc"}, embed=wistia_embed("abc"), http_get=http)
        assert err is None
        assert len(cands) == 1
        assert cands[0]["source_type"] == "wistia_resolved"
        assert cands[0]["resolution"]["height"] == 480

    def test_asset_without_url_is_skipped(self):
        body = {"media": {"assets": [
            {"type": "mp4_video", "height": 720},  # no url
            {"type": "mp4_video", "url": "",       # empty url
             "height": 480},
            {"type": "mp4_video", "url": "https://x/good.mp4",
             "height": 360, "content_type": "video/mp4"},
        ]}}
        http = make_http_get({"abc.json": (200, body)})
        cands, err = pr.resolve_wistia(
            {"hashed_id": "abc"}, embed=wistia_embed("abc"), http_get=http)
        assert err is None
        assert len(cands) == 1
        assert cands[0]["url"] == "https://x/good.mp4"

    def test_request_url_uses_hashed_id(self):
        captured = {}
        def http(url):
            captured["url"] = url
            return 200, {}, json.dumps(WISTIA_MEDIA_OK).encode()
        pr.resolve_wistia(
            {"hashed_id": "weird/value?with&special"},
            embed=wistia_embed(), http_get=http)
        # The hashed_id should be url-quoted into the path
        assert "weird" in captured["url"]
        assert "fast.wistia.net" in captured["url"]
        assert ".json" in captured["url"]


# ---------------------------------------------------------------------------
# TestWistiaCacheRead
# ---------------------------------------------------------------------------

class TestWistiaCacheRead:
    def _site_memory_with_cache(self, *, provider="vimeo", embed_id="76979871",
                                 url="https://cdn.cached/v.mp4",
                                 at=None, count=3, last_id=None):
        import time as _t
        if at is None:
            at = _t.time() - 60  # 1 minute old; well within TTL
        return {
            "provider_embeds_seen": {
                provider: {
                    "count": count,
                    "last_id": last_id or embed_id,
                    "last_resolved": {"id": embed_id, "url": url, "at": at},
                },
            },
        }

    def test_fresh_cache_hits_without_network(self):
        sm = self._site_memory_with_cache()
        # Network call MUST NOT happen
        def http(u):
            raise AssertionError(f"network call on cache hit: {u}")
        cands, err = pr.resolve_provider_embed(
            {"provider": "vimeo", "ids": {"clip_id": "76979871"},
             "url": "https://player.vimeo.com/video/76979871"},
            http_get=http,
            site_memory=sm,
        )
        assert err is None
        assert len(cands) == 1
        assert cands[0]["url"] == "https://cdn.cached/v.mp4"
        assert cands[0]["source_type"] == "vimeo_resolved_cached"
        assert cands[0]["cached"] is True
        assert cands[0]["found_in"] == "provider_resolved_cache:vimeo"

    def test_cache_miss_when_no_site_memory(self):
        # No site_memory -> dispatcher must call resolver
        called = {"n": 0}
        def http(u):
            called["n"] += 1
            return 404, {}, b""
        pr.resolve_provider_embed(
            {"provider": "vimeo", "ids": {"clip_id": "1"}, "url": "x"},
            http_get=http, site_memory=None,
        )
        assert called["n"] == 1

    def test_cache_miss_when_id_differs(self):
        # Cache has clip_id 76979871 but the embed is for a different one.
        sm = self._site_memory_with_cache(embed_id="76979871")
        called = {"n": 0}
        def http(u):
            called["n"] += 1
            return 200, {}, json.dumps({"request": {"files": {}}}).encode()
        pr.resolve_provider_embed(
            {"provider": "vimeo", "ids": {"clip_id": "OTHER"},
             "url": "https://player.vimeo.com/video/OTHER"},
            http_get=http, site_memory=sm,
        )
        assert called["n"] == 1, "cache for a different id must not hit"

    def test_expired_cache_falls_through_to_network(self):
        import time as _t
        # 7 hours old (default TTL is 6h)
        sm = self._site_memory_with_cache(at=_t.time() - (7 * 3600))
        called = {"n": 0}
        def http(u):
            called["n"] += 1
            return 404, {}, b""  # arbitrary; just want to confirm called
        pr.resolve_provider_embed(
            {"provider": "vimeo", "ids": {"clip_id": "76979871"}, "url": "x"},
            http_get=http, site_memory=sm,
        )
        assert called["n"] == 1, "expired cache must NOT hit"

    def test_custom_ttl_overrides_default(self):
        import time as _t
        # 5 minutes old; default TTL would pass it, but we set TTL=60s
        sm = self._site_memory_with_cache(at=_t.time() - 300)
        called = {"n": 0}
        def http(u):
            called["n"] += 1
            return 404, {}, b""
        pr.resolve_provider_embed(
            {"provider": "vimeo", "ids": {"clip_id": "76979871"}, "url": "x"},
            http_get=http, site_memory=sm, cache_ttl_seconds=60,
        )
        assert called["n"] == 1

    def test_cache_miss_when_last_resolved_missing(self):
        sm = {"provider_embeds_seen": {"vimeo": {
            "count": 1, "last_id": "76979871",
        }}}  # No last_resolved
        called = {"n": 0}
        def http(u):
            called["n"] += 1
            return 404, {}, b""
        pr.resolve_provider_embed(
            {"provider": "vimeo", "ids": {"clip_id": "76979871"}, "url": "x"},
            http_get=http, site_memory=sm,
        )
        assert called["n"] == 1

    def test_cache_miss_on_malformed_shapes(self):
        """Defensive: all of these should miss-not-crash."""
        bad_shapes = [
            {"provider_embeds_seen": "not a dict"},
            {"provider_embeds_seen": {"vimeo": "not a dict"}},
            {"provider_embeds_seen": {"vimeo": {"last_resolved": "not a dict"}}},
            {"provider_embeds_seen": {"vimeo": {"last_resolved":
                {"id": "76979871", "url": "x", "at": "not a number"}}}},
            {"provider_embeds_seen": {"vimeo": {"last_resolved":
                {"id": "76979871", "url": "", "at": 0}}}},  # empty url
        ]
        for sm in bad_shapes:
            called = {"n": 0}
            def http(u):
                called["n"] += 1
                return 404, {}, b""
            pr.resolve_provider_embed(
                {"provider": "vimeo", "ids": {"clip_id": "76979871"},
                 "url": "x"},
                http_get=http, site_memory=sm,
            )
            assert called["n"] == 1, f"malformed shape leaked through: {sm!r}"

    def test_wistia_cache_hits_with_hashed_id(self):
        """Symmetric check: Wistia uses hashed_id, not clip_id."""
        sm = self._site_memory_with_cache(
            provider="wistia", embed_id="abc123xyz",
            url="https://cached.wistia/x.mp4",
        )
        def http(u):
            raise AssertionError("must not network")
        cands, err = pr.resolve_provider_embed(
            wistia_embed("abc123xyz"), http_get=http, site_memory=sm,
        )
        assert err is None
        assert len(cands) == 1
        assert cands[0]["source_type"] == "wistia_resolved_cached"
        assert cands[0]["url"] == "https://cached.wistia/x.mp4"


# ---------------------------------------------------------------------------
# TestWistiaCacheWrite
# ---------------------------------------------------------------------------

class TestWistiaCacheWrite:
    def test_callback_fires_on_successful_resolution(self):
        http = make_http_get({"abc.json": (200, WISTIA_MEDIA_OK)})
        calls = []
        def cw(provider, embed_id, url, ts):
            calls.append((provider, embed_id, url, ts))
        cands, err = pr.resolve_provider_embed(
            wistia_embed("abc"), http_get=http, cache_write=cw,
        )
        assert err is None
        assert cands
        assert len(calls) == 1
        provider, embed_id, url, ts = calls[0]
        assert provider == "wistia"
        assert embed_id == "abc"
        # Cache-write policy: on score ties between HLS (90) and the
        # 1080p MP4 (80+10=90), prefer HLS. Rationale: a single
        # playlist URL covers all renditions, so a stale signed
        # token doesn't trap callers at one specific quality.
        assert ".m3u8" in url, f"tie-breaker should prefer HLS, got {url}"
        assert isinstance(ts, float)

    def test_callback_prefers_hls_on_score_tie(self):
        """Explicit spec test for the cache-write tie-breaker.

        Construct an artificial case where progressive 4K matches HLS
        on score: 80 + 4000//100 = 120, vs HLS at 90 -- progressive
        wins. So we use a contrived smaller progressive that ties at
        90 exactly.
        """
        # Progressive at 1080p scores 80 + 10 = 90, same as HLS.
        # The dispatcher's cache_pref must pick HLS on this tie.
        body = {"media": {"assets": [
            {"type": "mp4_video", "url": "https://x/prog1080.mp4",
             "height": 1080, "width": 1920, "content_type": "video/mp4"},
            {"type": "hls_playlist", "url": "https://x/master.m3u8",
             "content_type": "application/vnd.apple.mpegurl"},
        ]}}
        http = make_http_get({"abc.json": (200, body)})
        calls = []
        def cw(*args):
            calls.append(args)
        pr.resolve_provider_embed(
            wistia_embed("abc"), http_get=http, cache_write=cw,
        )
        assert len(calls) == 1
        _provider, _id, url, _ts = calls[0]
        assert url == "https://x/master.m3u8"

    def test_callback_prefers_higher_score_over_hls(self):
        """Inverse of the tie-breaker: a strictly-higher progressive
        score wins over HLS. Confirms the secondary key only kicks
        in on actual ties, not for any HLS asset."""
        # Progressive at 4K scores 80 + 40 = 120, well above HLS=90.
        body = {"media": {"assets": [
            {"type": "mp4_video", "url": "https://x/prog4k.mp4",
             "height": 4000, "width": 7680, "content_type": "video/mp4"},
            {"type": "hls_playlist", "url": "https://x/master.m3u8",
             "content_type": "application/vnd.apple.mpegurl"},
        ]}}
        http = make_http_get({"abc.json": (200, body)})
        calls = []
        def cw(*args):
            calls.append(args)
        pr.resolve_provider_embed(
            wistia_embed("abc"), http_get=http, cache_write=cw,
        )
        assert len(calls) == 1
        _provider, _id, url, _ts = calls[0]
        assert url == "https://x/prog4k.mp4"

    def test_callback_does_not_fire_on_resolution_failure(self):
        http = make_http_get({"abc.json": (404, b"")})
        calls = []
        def cw(provider, embed_id, url, ts):
            calls.append((provider, embed_id, url, ts))
        cands, err = pr.resolve_provider_embed(
            wistia_embed("abc"), http_get=http, cache_write=cw,
        )
        assert cands == []
        assert err
        assert calls == [], "must not cache failed resolutions"

    def test_callback_does_not_fire_on_cache_hit(self):
        """A cache hit should not re-write the cache. Important: a
        second write would refresh `at` and turn the TTL into a
        sliding window. We want absolute expiry."""
        import time as _t
        sm = {"provider_embeds_seen": {"wistia": {
            "count": 1, "last_id": "abc",
            "last_resolved": {"id": "abc", "url": "https://cached/x",
                              "at": _t.time() - 30},
        }}}
        calls = []
        def cw(*args):
            calls.append(args)
        cands, _err = pr.resolve_provider_embed(
            wistia_embed("abc"),
            http_get=lambda u: pytest.fail("no network expected"),
            site_memory=sm, cache_write=cw,
        )
        assert cands and cands[0]["cached"] is True
        assert calls == [], "cache hit should not invoke cache_write"

    def test_callback_exception_does_not_break_resolution(self):
        """A cache-write failure must never crash the resolution path."""
        http = make_http_get({"abc.json": (200, WISTIA_MEDIA_OK)})
        def bad_cw(*args):
            raise RuntimeError("write failed")
        cands, err = pr.resolve_provider_embed(
            wistia_embed("abc"), http_get=http, cache_write=bad_cw,
        )
        # Resolution still succeeds; cache write quietly dropped.
        assert err is None
        assert cands

    def test_callback_skipped_when_no_primary_id(self):
        """No id -> no cache key -> no write."""
        # Vimeo embed with no clip_id -> resolver returns missing-id
        # error -> cache_write must not fire (no successful candidates,
        # and also no id to key by).
        calls = []
        def cw(*args):
            calls.append(args)
        cands, err = pr.resolve_provider_embed(
            {"provider": "vimeo", "ids": {}, "url": "x"},
            http_get=lambda u: (200, {}, b"{}"),
            cache_write=cw,
        )
        assert cands == []
        assert err
        assert calls == []


# ---------------------------------------------------------------------------
# TestProviderCacheWriterFactory
# ---------------------------------------------------------------------------

class TestProviderCacheWriterFactory:
    def test_factory_returns_callable_for_dict_config(self):
        config = {}
        w = ln.make_provider_cache_writer(config)
        assert callable(w)
        w("vimeo", "76979871", "https://cdn/x.mp4", 1700000000.0)
        block = config["learned"]["deep_detect"]
        entry = block["provider_embeds_seen"]["vimeo"]
        assert entry["last_resolved"] == {
            "id": "76979871",
            "url": "https://cdn/x.mp4",
            "at": 1700000000.0,
        }

    def test_factory_returns_noop_for_non_dict(self):
        # Must not raise; must not write anywhere
        for bad in (None, 42, "string", [1, 2, 3]):
            w = ln.make_provider_cache_writer(bad)
            w("vimeo", "id", "https://x", 0.0)  # no-op

    def test_writer_initializes_block_when_absent(self):
        """If learned.deep_detect doesn't exist yet, the writer should
        create it on first write."""
        config = {"some_other_key": "preserved"}
        w = ln.make_provider_cache_writer(config)
        w("wistia", "abc", "https://x/x.mp4", 1700000000.0)
        assert config["some_other_key"] == "preserved"
        block = config["learned"]["deep_detect"]
        assert "provider_embeds_seen" in block
        assert "winning_source_types" in block  # full _dd_init_block shape

    def test_writer_preserves_existing_count_and_last_id(self):
        config = {"learned": {"deep_detect": {
            "provider_embeds_seen": {"vimeo": {
                "count": 5, "last_id": "76979871",
            }},
            # other init fields can be absent; the writer shouldn't
            # touch them
        }}}
        w = ln.make_provider_cache_writer(config)
        w("vimeo", "76979871", "https://cdn/x.mp4", 1700000000.0)
        entry = config["learned"]["deep_detect"]["provider_embeds_seen"]["vimeo"]
        assert entry["count"] == 5  # preserved
        assert entry["last_id"] == "76979871"  # preserved
        assert entry["last_resolved"]["url"] == "https://cdn/x.mp4"

    def test_writer_creates_provider_entry_when_first_seen(self):
        config = {"learned": {"deep_detect": {"provider_embeds_seen": {}}}}
        w = ln.make_provider_cache_writer(config)
        w("brightcove", "vidA", "https://cdn/x.mp4", 1700000000.0)
        entry = config["learned"]["deep_detect"]["provider_embeds_seen"]["brightcove"]
        assert entry["last_id"] == "vidA"
        assert entry["count"] == 0  # starts at zero; record_deep_detect_outcome will bump
        assert entry["last_resolved"]["id"] == "vidA"

    def test_writer_validates_inputs_defensively(self):
        """The writer must never crash on bad input — runs on the
        resolution hot path."""
        config = {}
        w = ln.make_provider_cache_writer(config)
        w("", "id", "https://x", 0.0)  # empty provider
        w("vimeo", "", "https://x", 0.0)  # empty id
        w("vimeo", "id", "", 0.0)  # empty url
        w("vimeo", "id", "https://x", "not a number")  # bad ts
        w(None, None, None, None)
        # None of those should have written anything
        block = config.get("learned", {}).get("deep_detect", {})
        pes = block.get("provider_embeds_seen", {})
        assert pes == {} or all("last_resolved" not in v for v in pes.values())


# ---------------------------------------------------------------------------
# TestCacheIntegration
# ---------------------------------------------------------------------------

class TestCacheIntegration:
    """End-to-end: first resolution writes the cache; second
    resolution reads it. Uses the public dispatcher + learn.py writer
    factory to make sure the read/write halves agree on key shape."""

    def test_warm_path_after_first_run(self):
        config = {}
        http_calls = []
        def http(url):
            http_calls.append(url)
            return 200, {}, json.dumps(WISTIA_MEDIA_OK).encode()
        writer = ln.make_provider_cache_writer(config)

        # First run: cache empty, network is called
        sm1 = ln.deep_detect_site_memory(config)
        cands1, err1 = pr.resolve_provider_embed(
            wistia_embed("abc"),
            http_get=http, site_memory=sm1, cache_write=writer,
        )
        assert err1 is None
        assert len(cands1) >= 1
        assert len(http_calls) == 1
        # No cached-flag on the first run's candidates
        assert not any(c.get("cached") for c in cands1)

        # Second run: read freshly from the now-updated config
        sm2 = ln.deep_detect_site_memory(config)
        cands2, err2 = pr.resolve_provider_embed(
            wistia_embed("abc"),
            http_get=http, site_memory=sm2, cache_write=writer,
        )
        assert err2 is None
        assert len(cands2) == 1, "cache hit produces a single candidate"
        assert cands2[0]["cached"] is True
        assert cands2[0]["source_type"] == "wistia_resolved_cached"
        # Network must not have been called a second time
        assert len(http_calls) == 1

    def test_different_id_same_provider_misses_cache(self):
        """A second embed for the same provider but different id must
        miss cache and hit network."""
        config = {}
        http_calls = []
        def http(url):
            http_calls.append(url)
            return 200, {}, json.dumps(WISTIA_MEDIA_OK).encode()
        writer = ln.make_provider_cache_writer(config)

        # Resolve "abc"
        sm1 = ln.deep_detect_site_memory(config)
        pr.resolve_provider_embed(
            wistia_embed("abc"), http_get=http,
            site_memory=sm1, cache_write=writer,
        )
        # Resolve "xyz" — different id, same provider
        sm2 = ln.deep_detect_site_memory(config)
        pr.resolve_provider_embed(
            wistia_embed("xyz"), http_get=http,
            site_memory=sm2, cache_write=writer,
        )
        assert len(http_calls) == 2, (
            "different ids on the same provider should both hit network"
        )

    def test_ttl_expiry_triggers_network_refresh(self, monkeypatch):
        """Fast-forward time past TTL: next run should call network
        again."""
        config = {}
        http_calls = []
        def http(url):
            http_calls.append(url)
            return 200, {}, json.dumps(WISTIA_MEDIA_OK).encode()
        writer = ln.make_provider_cache_writer(config)

        # Run 1 at t=1000.0
        clock = {"t": 1000.0}
        monkeypatch.setattr(pr, "_now", lambda: clock["t"])
        sm1 = ln.deep_detect_site_memory(config)
        pr.resolve_provider_embed(
            wistia_embed("abc"), http_get=http,
            site_memory=sm1, cache_write=writer,
        )
        assert len(http_calls) == 1

        # Run 2 at t=1000+10s — within TTL -> cache hit
        clock["t"] = 1010.0
        sm2 = ln.deep_detect_site_memory(config)
        pr.resolve_provider_embed(
            wistia_embed("abc"), http_get=http,
            site_memory=sm2, cache_write=writer,
        )
        assert len(http_calls) == 1  # still no new call

        # Run 3 at t=1000 + 7h — past 6h TTL -> network refresh
        clock["t"] = 1000.0 + (7 * 3600)
        sm3 = ln.deep_detect_site_memory(config)
        pr.resolve_provider_embed(
            wistia_embed("abc"), http_get=http,
            site_memory=sm3, cache_write=writer,
        )
        assert len(http_calls) == 2

        # And the cache should have been refreshed by run 3
        entry = config["learned"]["deep_detect"]["provider_embeds_seen"]["wistia"]
        assert entry["last_resolved"]["at"] == 1000.0 + (7 * 3600)
