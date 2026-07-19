"""v3.66.20 — Phase 4 P4: YouTube resolver.

Tests the new ``resolve_youtube`` resolver promoted from stub in
this release. Mirrors the test layout for Brightcove (v3.66.19) and
JWPlayer (v3.66.18) — direct-resolver unit tests plus a dispatcher
integration class.

Test classes
------------

  TestYouTubeResolver
      Unit tests on ``resolve_youtube``: id resolution, watch-page
      parsing, ytInitialPlayerResponse slicing, playabilityStatus
      gating, hlsManifestUrl / formats / adaptiveFormats classification,
      signed-format skipping, and the error paths.

  TestSliceBalancedJson
      Pure-function coverage of the ``_slice_balanced_json`` helper:
      string-literal awareness, escaped quotes, unbalanced input.

  TestYouTubeDispatcherIntegration
      End-to-end through ``resolve_provider_embed``: cache hit/write,
      tie-breaker behaviour on the HLS preference, network-path
      caching with the correct key.

Mocks
-----

The watch-page response is fabricated as a string of the form
``"<html>...var ytInitialPlayerResponse = {...};...</html>"``. We do
NOT attempt to mock the real YouTube HTML — only the part the
resolver actually inspects (the JSON blob). ``_now`` is monkeypatched
where cache-TTL determinism matters.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

import pytest

from bulk_downloader import provider_resolve as pr
from bulk_downloader.provider_resolve import (
    _slice_balanced_json,
    resolve_provider_embed,
    resolve_youtube,
)


# Fixtures -------------------------------------------------------------------

def _make_watch_html(player_response: dict) -> bytes:
    """Wrap a player_response dict in HTML the resolver can parse."""
    body = json.dumps(player_response)
    return (
        "<!doctype html><html><body><script>"
        "(function(){var ytInitialPlayerResponse = "
        f"{body};"
        "window.ytplayer = {};})();"
        "</script></body></html>"
    ).encode("utf-8")


def _ok_player_response(
    *,
    hls: bool = False,
    formats: List[dict] = None,
    adaptive: List[dict] = None,
) -> dict:
    streaming: Dict[str, Any] = {}
    if hls:
        streaming["hlsManifestUrl"] = (
            "https://manifest.googlevideo.com/api/manifest/hls_variant/"
            "expire/12345/master.m3u8"
        )
    if formats is not None:
        streaming["formats"] = formats
    if adaptive is not None:
        streaming["adaptiveFormats"] = adaptive
    return {
        "playabilityStatus": {"status": "OK"},
        "streamingData": streaming,
    }


def _fake_get(status: int, body: bytes):
    def _get(url):
        return (status, {}, body)
    return _get


# ---------------------------------------------------------------------------
# TestYouTubeResolver
# ---------------------------------------------------------------------------

class TestYouTubeResolver:

    # Happy paths --------------------------------------------------------

    def test_hls_only(self):
        body = _make_watch_html(_ok_player_response(hls=True))
        cands, err = resolve_youtube(
            {"video_id": "dQw4w9WgXcQ"}, http_get=_fake_get(200, body))
        assert err is None
        assert len(cands) == 1
        assert cands[0]["source_type"] == "youtube_resolved_hls"
        assert cands[0]["score"] == 90
        assert ".m3u8" in cands[0]["url"]

    def test_progressive_only(self):
        body = _make_watch_html(_ok_player_response(formats=[
            {"itag": 18, "url": "https://r1.gvt.com/p?id=A",
             "mimeType": "video/mp4", "qualityLabel": "360p",
             "width": 640, "height": 360, "fps": 30,
             "bitrate": 500000, "contentLength": "1234567"},
            {"itag": 22, "url": "https://r1.gvt.com/p?id=B",
             "mimeType": "video/mp4", "qualityLabel": "720p",
             "width": 1280, "height": 720, "fps": 30,
             "bitrate": 2000000, "contentLength": "2345678"},
        ]))
        cands, err = resolve_youtube(
            {"video_id": "dQw4w9WgXcQ"}, http_get=_fake_get(200, body))
        assert err is None
        assert len(cands) == 2
        types = [c["source_type"] for c in cands]
        assert types == ["youtube_resolved", "youtube_resolved"]
        scores = [c["score"] for c in cands]
        assert scores == [83, 87]  # 80 + h//100
        # Sanity on the resolution payload
        assert cands[1]["resolution"]["height"] == 720
        assert cands[1]["resolution"]["label"] == "720p"
        assert cands[1]["bitrate"] == 2000000
        assert cands[1]["size_bytes"] == 2345678
        assert cands[1]["fps"] == 30.0
        assert cands[1]["itag"] == 22

    def test_mixed_hls_progressive_adaptive(self):
        body = _make_watch_html(_ok_player_response(
            hls=True,
            formats=[
                {"itag": 18, "url": "https://r1.gvt.com/p?id=A",
                 "mimeType": "video/mp4", "qualityLabel": "360p",
                 "height": 360},
            ],
            adaptive=[
                {"itag": 137, "url": "https://r1.gvt.com/a?id=C",
                 "mimeType": "video/mp4", "qualityLabel": "1080p",
                 "height": 1080},
            ],
        ))
        cands, err = resolve_youtube(
            {"video_id": "x"}, http_get=_fake_get(200, body))
        assert err is None
        types = [c["source_type"] for c in cands]
        assert "youtube_resolved_hls" in types
        assert "youtube_resolved" in types
        assert "youtube_resolved_adaptive" in types

    # Signed-format handling --------------------------------------------

    def test_signed_format_skipped_with_clear_error(self):
        """All formats are signed (signatureCipher) — no direct urls.
        Resolver returns ([], "...signatureCipher...")"""
        body = _make_watch_html(_ok_player_response(
            adaptive=[
                {"itag": 137, "signatureCipher":
                    "s=ENCRYPTED_SIG&sp=sig&url=https://x.com/p",
                 "mimeType": "video/mp4", "qualityLabel": "1080p"},
                {"itag": 248, "cipher": "alt-form-of-the-same",
                 "mimeType": "video/webm", "qualityLabel": "1080p"},
            ],
        ))
        cands, err = resolve_youtube(
            {"video_id": "x"}, http_get=_fake_get(200, body))
        assert cands == []
        assert err is not None
        assert "signed" in err.lower() or "signatureCipher" in err
        assert "2" in err  # "2 signed format(s)"

    def test_direct_progressive_alongside_signed_adaptive(self):
        """Direct progressive present + signed adaptive present →
        progressive is emitted; signed adaptive is silently skipped."""
        body = _make_watch_html(_ok_player_response(
            formats=[
                {"itag": 18, "url": "https://r1.gvt.com/p?id=A",
                 "mimeType": "video/mp4", "qualityLabel": "360p",
                 "height": 360},
            ],
            adaptive=[
                {"itag": 137, "signatureCipher": "s=...",
                 "mimeType": "video/mp4", "qualityLabel": "1080p"},
            ],
        ))
        cands, err = resolve_youtube(
            {"video_id": "x"}, http_get=_fake_get(200, body))
        assert err is None
        assert len(cands) == 1
        assert cands[0]["source_type"] == "youtube_resolved"

    # ID handling --------------------------------------------------------

    def test_canonical_video_id(self):
        body = _make_watch_html(_ok_player_response(hls=True))
        cands, err = resolve_youtube(
            {"video_id": "ABC"}, http_get=_fake_get(200, body))
        assert err is None
        assert cands

    def test_legacy_video_id_embed_fallback(self):
        """The dispatcher passes canonical 'video_id'; older callers
        may wire legacy 'video_id_embed' / 'video_id_short' / 'video_id_v'.
        Resolver falls back to those when no canonical key is present."""
        body = _make_watch_html(_ok_player_response(hls=True))
        for legacy_key in ("video_id_embed", "video_id_short", "video_id_v"):
            cands, err = resolve_youtube(
                {legacy_key: "ABC"}, http_get=_fake_get(200, body))
            assert err is None, (legacy_key, err)
            assert cands, legacy_key

    def test_missing_video_id(self):
        cands, err = resolve_youtube(
            {}, http_get=_fake_get(200, _make_watch_html({})))
        assert cands == []
        assert err == "missing video_id"

    def test_empty_ids_not_a_dict(self):
        cands, err = resolve_youtube(
            None, http_get=_fake_get(200, _make_watch_html({})))
        assert cands == []
        assert err == "missing video_id"

    # HTTP error paths --------------------------------------------------

    def test_http_404(self):
        cands, err = resolve_youtube(
            {"video_id": "x"}, http_get=_fake_get(404, b"not found"))
        assert cands == []
        assert "404" in err

    def test_http_5xx(self):
        cands, err = resolve_youtube(
            {"video_id": "x"}, http_get=_fake_get(503, b""))
        assert cands == []
        assert "503" in err

    def test_network_exception(self):
        def broken_get(url):
            raise ConnectionError("DNS failed")
        cands, err = resolve_youtube(
            {"video_id": "x"}, http_get=broken_get)
        assert cands == []
        assert "youtube watch request failed" in err
        assert "ConnectionError" in err
        assert "DNS failed" in err

    # ytInitialPlayerResponse parse paths -------------------------------

    def test_page_missing_player_response(self):
        """No ytInitialPlayerResponse in the HTML — consent page,
        region block, age gate, etc."""
        body = b"<html><body>Consent required</body></html>"
        cands, err = resolve_youtube(
            {"video_id": "x"}, http_get=_fake_get(200, body))
        assert cands == []
        assert "ytInitialPlayerResponse" in err

    def test_player_response_unbalanced(self):
        """The assignment is present but braces don't balance."""
        body = (b"<html><script>var ytInitialPlayerResponse = "
                b"{\"streamingData\": {\"formats\": [</script></html>")
        cands, err = resolve_youtube(
            {"video_id": "x"}, http_get=_fake_get(200, body))
        assert cands == []
        assert "unbalanced" in err.lower() or "parseable" in err.lower()

    def test_player_response_not_object(self):
        """JSON is structurally valid but not a dict — defensive path.

        Our anchor regex requires a literal '{' after the assignment,
        so an array opener falls through to the "did not contain
        ytInitialPlayerResponse" branch. Verified here so a future
        regex-relaxation that admits '[' triggers a deliberate test
        update with a thought about what should happen.
        """
        body = (b"<html><script>var ytInitialPlayerResponse = "
                b"[\"not\", \"an\", \"object\"];</script></html>")
        cands, err = resolve_youtube(
            {"video_id": "x"}, http_get=_fake_get(200, body))
        assert cands == []
        assert ("did not contain ytInitialPlayerResponse" in err
                or "not an object" in err)

    # Playability gating ------------------------------------------------

    def test_playability_login_required(self):
        body = _make_watch_html({
            "playabilityStatus": {
                "status": "LOGIN_REQUIRED",
                "reason": "Sign in to confirm your age",
            },
        })
        cands, err = resolve_youtube(
            {"video_id": "x"}, http_get=_fake_get(200, body))
        assert cands == []
        assert "LOGIN_REQUIRED" in err
        assert "age-gated" in err or "DRM" in err

    def test_playability_region_block(self):
        body = _make_watch_html({
            "playabilityStatus": {
                "status": "UNPLAYABLE",
                "reason": "Not available in your country",
            },
        })
        cands, err = resolve_youtube(
            {"video_id": "x"}, http_get=_fake_get(200, body))
        assert cands == []
        assert "UNPLAYABLE" in err
        assert "Not available" in err

    def test_playability_ok_with_no_streaming_data(self):
        """playabilityStatus=OK but streamingData is absent — rare
        edge case (premieres, deleted between page fetches)."""
        body = _make_watch_html({"playabilityStatus": {"status": "OK"}})
        cands, err = resolve_youtube(
            {"video_id": "x"}, http_get=_fake_get(200, body))
        assert cands == []
        assert "no usable formats" in err.lower()

    def test_no_signed_skipped_when_streaming_empty(self):
        """Empty streamingData → no signed_skipped count surfaced
        (error message about no usable formats, not about signed)."""
        body = _make_watch_html(_ok_player_response(formats=[]))
        cands, err = resolve_youtube(
            {"video_id": "x"}, http_get=_fake_get(200, body))
        assert cands == []
        assert "no usable formats" in err.lower()
        assert "signed" not in err.lower()

    # Body-decoding edge cases ------------------------------------------

    def test_non_utf8_body(self):
        """Body has some non-utf8 bytes but the player-response section
        is valid JSON; errors='replace' should keep parsing functional."""
        prefix = b"\xff\xfe<html><script>var ytInitialPlayerResponse = "
        suffix = b";</script></html>\xff"
        body = (prefix + json.dumps(_ok_player_response(hls=True)).encode()
                + suffix)
        cands, err = resolve_youtube(
            {"video_id": "x"}, http_get=_fake_get(200, body))
        assert err is None
        assert cands

    # Crash containment -------------------------------------------------

    def test_resolver_never_raises_on_garbage(self):
        """Resolver returns ([], err) for any garbage input. The
        dispatcher's try/except is the next line of defense, but the
        resolver itself shouldn't propagate exceptions to it."""
        cases = [
            (b"", 200),
            (b"\x00\x01\x02\x03", 200),
            (b"definitely not html", 200),
            (b"<html></html>", 200),
            (b'<script>ytInitialPlayerResponse = "string"</script>', 200),
        ]
        for body, status in cases:
            cands, err = resolve_youtube(
                {"video_id": "x"}, http_get=_fake_get(status, body))
            # We don't care about the specific error string here, just
            # that the resolver returned cleanly.
            assert isinstance(cands, list)
            assert err is None or isinstance(err, str)


# ---------------------------------------------------------------------------
# TestSliceBalancedJson
# ---------------------------------------------------------------------------

class TestSliceBalancedJson:
    def test_simple_object(self):
        text = 'prefix = {"a": 1} ; suffix'
        i = text.index("{")
        out = _slice_balanced_json(text, i)
        assert out == '{"a": 1}'

    def test_nested(self):
        text = 'x = {"a": {"b": {"c": 3}}, "d": 4} ;'
        i = text.index("{")
        out = _slice_balanced_json(text, i)
        assert out == '{"a": {"b": {"c": 3}}, "d": 4}'

    def test_brace_inside_string(self):
        text = 'x = {"a": "}", "b": 1};'
        i = text.index("{")
        out = _slice_balanced_json(text, i)
        assert out == '{"a": "}", "b": 1}'
        # Round-trips through json
        assert json.loads(out) == {"a": "}", "b": 1}

    def test_escaped_quote_in_string(self):
        text = 'x = {"a": "he said \\"hi\\"", "b": 2};'
        i = text.index("{")
        out = _slice_balanced_json(text, i)
        assert json.loads(out) == {"a": 'he said "hi"', "b": 2}

    def test_unbalanced_returns_none(self):
        text = 'x = {"a": 1, "b": '
        i = text.index("{")
        assert _slice_balanced_json(text, i) is None

    def test_not_pointing_at_brace_returns_none(self):
        text = 'abc {"a": 1}'
        assert _slice_balanced_json(text, 0) is None  # 'a', not '{'

    def test_index_past_end_returns_none(self):
        assert _slice_balanced_json("{}", 99) is None


# ---------------------------------------------------------------------------
# TestYouTubeDispatcherIntegration
# ---------------------------------------------------------------------------

class TestYouTubeDispatcherIntegration:
    """End-to-end through resolve_provider_embed: cache layer +
    tie-breaker behaviour. Same shape as the equivalent classes in
    Brightcove (v3.66.19) and JWPlayer (v3.66.18) tests."""

    def _embed(self, video_id="dQw4w9WgXcQ"):
        return {
            "provider": "youtube",
            "source_type": "youtube_embed",
            "ids": {"video_id": video_id},
            "url": f"https://www.youtube.com/embed/{video_id}",
        }

    def test_dispatcher_routes_to_resolver(self):
        body = _make_watch_html(_ok_player_response(
            formats=[{"itag": 18, "url": "https://r1.gvt.com/p?id=A",
                      "mimeType": "video/mp4", "qualityLabel": "360p",
                      "height": 360}],
        ))
        cands, err = resolve_provider_embed(
            self._embed(), http_get=_fake_get(200, body))
        assert err is None
        assert len(cands) == 1
        assert cands[0]["source_type"] == "youtube_resolved"
        assert cands[0]["found_in"] == "provider_resolved:youtube"

    def test_dispatcher_cache_hit(self, monkeypatch):
        """Pre-warmed site_memory cache → no network call."""
        monkeypatch.setattr(pr, "_now", lambda: 1_000_000.0)
        site_memory = {
            "provider_embeds_seen": {
                "youtube": {
                    "last_resolved": {
                        "id": "dQw4w9WgXcQ",
                        "url": "https://cached.example/manifest.m3u8",
                        "at": 1_000_000.0 - 60,  # 60s old, fresh
                    },
                },
            },
        }
        # Network would 500 — we should NOT see it
        def boom(url):
            raise AssertionError("dispatcher hit network on cache hit")
        cands, err = resolve_provider_embed(
            self._embed(), http_get=boom, site_memory=site_memory)
        assert err is None
        assert len(cands) == 1
        assert cands[0]["source_type"] == "youtube_resolved_cached"
        assert cands[0]["url"] == "https://cached.example/manifest.m3u8"
        assert cands[0]["found_in"] == "provider_resolved_cache:youtube"

    def test_dispatcher_cache_write_hls_tie_breaker(self, monkeypatch):
        """HLS + same-score progressive present → cache write picks HLS
        as the canonical URL because the tie-breaker prefers it."""
        monkeypatch.setattr(pr, "_now", lambda: 2_000_000.0)
        body = _make_watch_html(_ok_player_response(
            hls=True,
            formats=[
                # Score 90 from HLS; this progressive scores 80+10=90 too
                {"itag": 313, "url": "https://r1.gvt.com/p?id=4k",
                 "mimeType": "video/mp4", "qualityLabel": "2160p",
                 "height": 1000},  # exactly h=1000 so score=80+10=90
            ],
        ))
        writes = []
        def cache_write(provider, embed_id, url, ts):
            writes.append((provider, embed_id, url, ts))
        cands, err = resolve_provider_embed(
            self._embed(), http_get=_fake_get(200, body),
            cache_write=cache_write)
        assert err is None
        assert len(writes) == 1
        p, eid, url, ts = writes[0]
        assert p == "youtube"
        assert eid == "dQw4w9WgXcQ"
        assert ".m3u8" in url  # HLS won the tie
        assert ts == 2_000_000.0

    def test_dispatcher_cache_write_uses_video_id(self, monkeypatch):
        """Even when the embed only has legacy id keys, the resolver's
        fallback resolves them; ``_primary_id`` consults canonical
        keys, so the cache write should use canonical 'video_id'."""
        monkeypatch.setattr(pr, "_now", lambda: 3_000_000.0)
        body = _make_watch_html(_ok_player_response(
            formats=[{"itag": 18, "url": "https://r1.gvt.com/p?id=A",
                      "mimeType": "video/mp4", "qualityLabel": "360p",
                      "height": 360}],
        ))
        writes = []
        emb = {
            "provider": "youtube", "ids": {"video_id": "VID7"},
            "url": "https://www.youtube.com/embed/VID7",
        }
        cands, err = resolve_provider_embed(
            emb, http_get=_fake_get(200, body),
            cache_write=lambda *a: writes.append(a))
        assert err is None
        assert writes[0][1] == "VID7"  # canonical video_id used as key
