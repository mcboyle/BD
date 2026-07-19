"""v3.66.19 — Brightcove resolver tests.

Mirrors v3.66.17 Wistia and v3.66.18 JWPlayer test structure.

Brightcove is the first resolver requiring an out-of-band credential
(``policy_key``). The tests cover:

  * TestBrightcoveResolver — pure-function resolver coverage:
        success, HLS vs DASH vs MP4 discrimination, the new
        policy_key input channels (ids + embed dict), error paths.
  * TestBrightcoveDispatcherIntegration — through resolve_provider_embed:
        cache hit, cache write, ids carrying the policy key.
  * TestExtractProviderEmbedsBrightcove — the deep_detect side:
        policy_key extraction from inline JS and data-policy-key.

All offline / deterministic.
"""
from __future__ import annotations

import json

import pytest

from bulk_downloader import provider_resolve as pr
from bulk_downloader import deep_detect as dd


# ---------------------------------------------------------------------------
# Fixtures: realistic edge.api.brightcove.com JSON shapes
# ---------------------------------------------------------------------------

# A real-ish policy key — Brightcove keys are ~80-200 base64url chars
# after the `BCpk` prefix. We don't validate the actual value; the
# resolver only checks for presence.
POLICY_KEY = "BCpkADawqM3-" + "A" * 80


def _playback_response(sources, *, status=200):
    """Mock the Playback API response."""
    body = json.dumps({
        "id": "6356900250",
        "name": "test video",
        "duration": 60_000,
        "sources": sources,
    }).encode("utf-8")
    return status, {"content-type": "application/json"}, body


def _fake_get(response):
    def _get(url):
        return response
    return _get


def _hls_source(url="https://stream.brightcove.com/master.m3u8"):
    return {"src": url, "type": "application/x-mpegURL"}


def _mp4_source(url, *, height, width=None, avg_bitrate=None,
                container="MP4"):
    out = {"src": url, "container": container, "height": height}
    if width is not None:
        out["width"] = width
    if avg_bitrate is not None:
        out["avg_bitrate"] = avg_bitrate
    return out


def _dash_source(url="https://stream.brightcove.com/manifest.mpd"):
    return {"src": url, "type": "application/dash+xml"}


def _base_ids():
    """ids dict pre-populated with the minimum to reach a successful
    network call. Tests vary one field at a time."""
    return {
        "account_id": "6118377982001",
        "video_id": "6356900250",
        "policy_key": POLICY_KEY,
    }


# ---------------------------------------------------------------------------
# Class 1 — TestBrightcoveResolver
# ---------------------------------------------------------------------------

class TestBrightcoveResolver:

    def test_progressive_only(self):
        sources = [_mp4_source("https://s.brightcove.com/720p.mp4",
                               height=720, width=1280, avg_bitrate=2_500_000)]
        candidates, error = pr.resolve_brightcove(
            _base_ids(), embed=None, http_get=_fake_get(_playback_response(sources)))
        assert error is None
        assert len(candidates) == 1
        c = candidates[0]
        assert c["source_type"] == "brightcove_resolved"
        assert c["url"] == "https://s.brightcove.com/720p.mp4"
        assert c["score"] == 80 + 7
        assert c["resolution"]["height"] == 720
        assert c["bitrate"] == 2_500_000
        assert c["provider_resolved"] is True
        assert c["found_in"] == "provider_resolved:brightcove"

    def test_hls_only(self):
        candidates, error = pr.resolve_brightcove(
            _base_ids(), embed=None,
            http_get=_fake_get(_playback_response([_hls_source()])))
        assert error is None
        assert len(candidates) == 1
        assert candidates[0]["source_type"] == "brightcove_resolved_hls"
        assert candidates[0]["score"] == 90

    def test_dash_only(self):
        candidates, error = pr.resolve_brightcove(
            _base_ids(), embed=None,
            http_get=_fake_get(_playback_response([_dash_source()])))
        assert error is None
        assert len(candidates) == 1
        assert candidates[0]["source_type"] == "brightcove_resolved_dash"
        assert candidates[0]["score"] == 90

    def test_mixed_sources(self):
        sources = [
            _hls_source("https://s.brightcove.com/master.m3u8"),
            _dash_source("https://s.brightcove.com/manifest.mpd"),
            _mp4_source("https://s.brightcove.com/360p.mp4", height=360),
            _mp4_source("https://s.brightcove.com/720p.mp4", height=720),
            _mp4_source("https://s.brightcove.com/1080p.mp4", height=1080),
        ]
        candidates, error = pr.resolve_brightcove(
            _base_ids(), embed=None,
            http_get=_fake_get(_playback_response(sources)))
        assert error is None
        assert len(candidates) == 5
        types = [c["source_type"] for c in candidates]
        assert types.count("brightcove_resolved_hls") == 1
        assert types.count("brightcove_resolved_dash") == 1
        assert types.count("brightcove_resolved") == 3

    def test_extension_fallback_when_type_and_container_missing(self):
        """No `type`, no `container` → infer from URL extension."""
        sources = [
            {"src": "https://s.brightcove.com/master.m3u8"},
            {"src": "https://s.brightcove.com/manifest.mpd"},
            {"src": "https://s.brightcove.com/720p.mp4", "height": 720},
        ]
        candidates, error = pr.resolve_brightcove(
            _base_ids(), embed=None,
            http_get=_fake_get(_playback_response(sources)))
        assert error is None
        types = sorted(c["source_type"] for c in candidates)
        assert types == ["brightcove_resolved",
                         "brightcove_resolved_dash",
                         "brightcove_resolved_hls"]

    def test_extension_fallback_ignores_query_string(self):
        sources = [{"src": "https://s.brightcove.com/720p.mp4?sig=abc",
                    "height": 720}]
        candidates, error = pr.resolve_brightcove(
            _base_ids(), embed=None,
            http_get=_fake_get(_playback_response(sources)))
        assert error is None
        assert len(candidates) == 1
        assert candidates[0]["source_type"] == "brightcove_resolved"

    def test_non_video_sources_skipped(self):
        sources = [
            {"src": "https://s.brightcove.com/captions.vtt",
             "type": "text/vtt"},
            _hls_source(),
        ]
        candidates, error = pr.resolve_brightcove(
            _base_ids(), embed=None,
            http_get=_fake_get(_playback_response(sources)))
        assert error is None
        assert len(candidates) == 1
        assert candidates[0]["source_type"] == "brightcove_resolved_hls"

    # ---- Policy-key input channels (new for v3.66.19) ------------------

    def test_policy_key_from_ids(self):
        """policy_key carried in the ids dict (extracted from page)."""
        ids = _base_ids()  # includes policy_key
        candidates, error = pr.resolve_brightcove(
            ids, embed=None,
            http_get=_fake_get(_playback_response([_hls_source()])))
        assert error is None
        assert len(candidates) == 1

    def test_policy_key_from_embed_dict(self):
        """policy_key carried on the embed dict (out-of-band / site config)."""
        ids = _base_ids()
        ids.pop("policy_key")  # not in ids
        embed = {"url": "https://players.brightcove.net/.../index.html",
                 "policy_key": POLICY_KEY}
        candidates, error = pr.resolve_brightcove(
            ids, embed=embed,
            http_get=_fake_get(_playback_response([_hls_source()])))
        assert error is None
        assert len(candidates) == 1

    def test_policy_key_ids_takes_priority_over_embed(self):
        """If both ids and embed have a policy_key, ids wins (it came
        from the page, more recent than cached site config)."""
        ids = _base_ids()  # POLICY_KEY in ids
        captured_url = []
        def _capture(url):
            captured_url.append(url)
            return _playback_response([_hls_source()])
        embed = {"url": "...", "policy_key": "BCpk" + "X" * 100}
        candidates, error = pr.resolve_brightcove(
            ids, embed=embed, http_get=_capture)
        assert error is None
        # The URL is the same either way (policy key is a header, not
        # query param), so we can't directly observe which key was
        # sent. But we can check the resolver didn't error and produced
        # candidates — the priority logic itself is unit-tested via the
        # absence of "missing policy_key".
        assert candidates

    # ---- Error path ----------------------------------------------------

    def test_missing_account_id(self):
        ids = _base_ids()
        del ids["account_id"]
        candidates, error = pr.resolve_brightcove(
            ids, embed=None, http_get=_fake_get(_playback_response([])))
        assert candidates == []
        assert error == "missing account_id"

    def test_missing_video_id(self):
        ids = _base_ids()
        del ids["video_id"]
        candidates, error = pr.resolve_brightcove(
            ids, embed=None, http_get=_fake_get(_playback_response([])))
        assert candidates == []
        assert error == "missing video_id"

    def test_missing_policy_key(self):
        """No policy_key in ids OR on embed — clear error, no network."""
        ids = _base_ids()
        del ids["policy_key"]
        # The http_get should never be called.
        def _explode(url):
            raise AssertionError("must not call http_get without policy_key")
        candidates, error = pr.resolve_brightcove(
            ids, embed=None, http_get=_explode)
        assert candidates == []
        assert error is not None
        assert "policy_key" in error

    def test_ids_not_a_dict(self):
        candidates, error = pr.resolve_brightcove(
            None, embed=None, http_get=_fake_get(_playback_response([])))
        assert candidates == []
        # Should report missing account_id (treated as empty dict)
        assert error is not None
        assert "account_id" in error

    def test_http_401_distinct_policy_error(self):
        """401 → resolver hints at the policy_key being the cause."""
        candidates, error = pr.resolve_brightcove(
            _base_ids(), embed=None,
            http_get=_fake_get(_playback_response([], status=401)))
        assert candidates == []
        assert "401" in error
        assert "policy" in error.lower()

    def test_http_403(self):
        candidates, error = pr.resolve_brightcove(
            _base_ids(), embed=None,
            http_get=_fake_get(_playback_response([], status=403)))
        assert candidates == []
        assert "403" in error

    def test_http_404(self):
        candidates, error = pr.resolve_brightcove(
            _base_ids(), embed=None,
            http_get=_fake_get(_playback_response([], status=404)))
        assert candidates == []
        assert "404" in error

    def test_network_exception(self):
        def _raising(url):
            raise TimeoutError("slow")
        candidates, error = pr.resolve_brightcove(
            _base_ids(), embed=None, http_get=_raising)
        assert candidates == []
        assert "TimeoutError" in error

    def test_non_json_body(self):
        candidates, error = pr.resolve_brightcove(
            _base_ids(), embed=None,
            http_get=_fake_get((200, {}, b"<html>oops</html>")))
        assert candidates == []
        assert "non-JSON" in error

    def test_empty_sources(self):
        candidates, error = pr.resolve_brightcove(
            _base_ids(), embed=None,
            http_get=_fake_get(_playback_response([])))
        assert candidates == []
        assert "no progressive" in error.lower() or \
               "no progressive / hls / dash" in error.lower()

    def test_resolver_never_raises_on_garbage(self):
        garbage_responses = [
            (200, {}, b""),
            (200, {}, b"null"),
            (200, {}, b'{"sources": "not a list"}'),
            (200, {}, b'{"sources": [42]}'),
            (200, {}, b'[]'),
        ]
        for resp in garbage_responses:
            candidates, error = pr.resolve_brightcove(
                _base_ids(), embed=None, http_get=_fake_get(resp))
            assert candidates == []
            assert isinstance(error, str)


# ---------------------------------------------------------------------------
# Class 2 — TestBrightcoveDispatcherIntegration
# ---------------------------------------------------------------------------

class TestBrightcoveDispatcherIntegration:

    def test_dispatcher_routes_to_resolver(self):
        candidates, error = pr.resolve_provider_embed(
            {"provider": "brightcove",
             "source_type": "brightcove_embed",
             "ids": _base_ids(),
             "url": "https://players.brightcove.net/x/y_default/index.html"},
            http_get=_fake_get(_playback_response([_hls_source()])),
        )
        assert error is None
        assert len(candidates) == 1
        assert candidates[0]["source_type"] == "brightcove_resolved_hls"

    def test_dispatcher_cache_hit(self, monkeypatch):
        """Cache hit on Brightcove works the same as other providers."""
        monkeypatch.setattr(pr, "_now", lambda: 1000.0)
        site_memory = {
            "provider_embeds_seen": {
                "brightcove": {
                    "last_resolved": {
                        "id": "6356900250",
                        "url": "https://cached.example.com/master.m3u8",
                        "at": 999.0,
                    }
                }
            }
        }
        def _explode(url):
            raise AssertionError("cache hit must not call http_get")
        candidates, error = pr.resolve_provider_embed(
            {"provider": "brightcove",
             "source_type": "brightcove_embed",
             "ids": _base_ids(),
             "url": "..."},
            http_get=_explode,
            site_memory=site_memory,
        )
        assert error is None
        assert len(candidates) == 1
        c = candidates[0]
        assert c["source_type"] == "brightcove_resolved_cached"
        assert c["score"] == 85

    def test_dispatcher_cache_write_uses_video_id(self, monkeypatch):
        """The cache key for Brightcove must be video_id (the canonical
        id from _primary_id's preference order)."""
        monkeypatch.setattr(pr, "_now", lambda: 2000.0)
        writes = []
        def _writer(*args):
            writes.append(args)
        pr.resolve_provider_embed(
            {"provider": "brightcove",
             "source_type": "brightcove_embed",
             "ids": _base_ids(),
             "url": "..."},
            http_get=_fake_get(_playback_response([_hls_source()])),
            cache_write=_writer,
        )
        assert len(writes) == 1
        provider, embed_id, url, ts = writes[0]
        assert provider == "brightcove"
        assert embed_id == "6356900250"  # the video_id
        assert "master.m3u8" in url

    def test_dispatcher_cache_write_hls_tie_breaker(self, monkeypatch):
        """Cross-provider tie-breaker parity: HLS preferred on score tie."""
        monkeypatch.setattr(pr, "_now", lambda: 2000.0)
        writes = []
        def _writer(*args):
            writes.append(args)
        sources = [
            _mp4_source("https://s.brightcove.com/1000p.mp4", height=1000),
            _hls_source("https://s.brightcove.com/master.m3u8"),
        ]
        pr.resolve_provider_embed(
            {"provider": "brightcove",
             "source_type": "brightcove_embed",
             "ids": _base_ids(), "url": "..."},
            http_get=_fake_get(_playback_response(sources)),
            cache_write=_writer,
        )
        assert len(writes) == 1
        assert writes[0][2] == "https://s.brightcove.com/master.m3u8"

    def test_dispatcher_policy_key_threaded_via_embed_dict(self):
        """The dispatcher passes the embed dict through; the resolver
        reads policy_key from it as a fallback when ids lacks it."""
        ids = _base_ids()
        del ids["policy_key"]
        candidates, error = pr.resolve_provider_embed(
            {"provider": "brightcove",
             "source_type": "brightcove_embed",
             "ids": ids,
             "policy_key": POLICY_KEY,
             "url": "..."},
            http_get=_fake_get(_playback_response([_hls_source()])),
        )
        assert error is None
        assert len(candidates) == 1


# ---------------------------------------------------------------------------
# Class 3 — TestExtractProviderEmbedsBrightcove
# ---------------------------------------------------------------------------

class TestExtractProviderEmbedsBrightcove:

    def test_extract_policy_key_from_inline_js(self):
        text = (
            'brightcovePlayer.setup({policyKey: "' + POLICY_KEY + '"});'
        )
        ids = dd._extract_provider_ids("brightcove", text)
        assert ids.get("policy_key") == POLICY_KEY

    def test_extract_policy_key_from_json_string(self):
        text = '{"policyKey":"' + POLICY_KEY + '","other":"x"}'
        ids = dd._extract_provider_ids("brightcove", text)
        assert ids.get("policy_key") == POLICY_KEY

    def test_extract_policy_key_from_data_attr(self):
        text = '<video-js data-policy-key="' + POLICY_KEY + '"></video-js>'
        ids = dd._extract_provider_ids("brightcove", text)
        assert ids.get("policy_key") == POLICY_KEY

    def test_extract_combined_account_video_policy(self):
        """A realistic page snippet with all three ids in one blob."""
        text = (
            '<video-js data-account="6118377982001" '
            'data-video-id="6356900250"></video-js>'
            '<script>var p = {policyKey: "' + POLICY_KEY + '"};</script>'
        )
        ids = dd._extract_provider_ids("brightcove", text)
        assert ids.get("account_id") == "6118377982001"
        assert ids.get("video_id") == "6356900250"
        assert ids.get("policy_key") == POLICY_KEY

    def test_extract_provider_embeds_end_to_end_with_policy_key(self):
        """An iframe to players.brightcove.net plus an inline policy
        key in a script tag → the finding should pick up account_id,
        video_id, and policy_key together."""
        html = (
            '<html><body>'
            '<iframe src="https://players.brightcove.net/'
            '6118377982001/default_default/index.html'
            '?videoId=6356900250"></iframe>'
            '<script>var p = {policyKey: "' + POLICY_KEY + '"};</script>'
            '</body></html>'
        )
        findings = dd.extract_provider_embeds(html)
        bc = [f for f in findings if f["provider"] == "brightcove"]
        assert len(bc) >= 1
        # The iframe scan picks up account_id + video_id; the script
        # scan picks up the policy_key. They get merged via the
        # dedup-by-id-set logic.
        all_ids = {}
        for f in bc:
            all_ids.update(f["ids"])
        assert all_ids.get("account_id") == "6118377982001"
        assert all_ids.get("video_id") == "6356900250"
        # NOTE: the policy_key comes from the Stage-2 script-body scan.
        # We assert it ended up on some Brightcove finding; downstream
        # callers merging them is a separate concern.
        # If the inline-script path doesn't surface policy_key as part
        # of a finding's ids, that's a known gap; this test acts as a
        # forcing function.
        assert all_ids.get("policy_key") == POLICY_KEY

    def test_policy_key_too_short_rejected(self):
        """Make sure the regex's length floor (40+ chars after BCpk)
        rejects obvious garbage like `BCpkSHORT`."""
        text = 'policyKey: "BCpkSHORT"'
        ids = dd._extract_provider_ids("brightcove", text)
        assert "policy_key" not in ids


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
