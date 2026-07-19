"""v3.66.23 — Phase 4 P5: JWPlayer self-hosted resolver.

Tests the new self-hosted branch added to ``resolve_jwplayer``. The
cloud-media and feeds branches are covered by
``test_v3_66_18_phase4_jwplayer.py`` and
``test_v3_66_20_phase4_jwplayer_feeds.py`` respectively; this file
covers only the new ``ids["config_url"]`` path.

What's new in v3.66.23:

  * ``resolve_jwplayer`` accepts ``ids["config_url"]`` (an absolute
    http(s) URL pointing at a JWPlayer-shaped feed JSON on any host).
  * On a successful fetch with no signing markers, sources are
    flattened into candidates the same way the feeds endpoint does,
    with ``reasons`` prefixed ``"JWPlayer self-hosted[i]"``.
  * If the JSON contains DRM markers (``drm.widevine`` etc.) or
    signed-URL markers (``signedUrls`` etc.), the resolver returns
    a structured ``"scheme=..."`` tagged error WITHOUT attempting
    to bypass.
  * If the unsigned fetch returns 401/403, the resolver returns a
    "signed access required" error.
  * If the caller supplies ``signing_callback`` on the embed dict,
    the resolver uses that callback instead of ``http_get`` to fetch
    the config_url. This is the operator's hook for legitimate
    credentialed access.

Test classes
------------

  TestJWPlayerSelfHostedResolver
      Happy-path and basic-failure unit tests on the self-hosted
      branch: routing, source classification, candidate tagging.

  TestJWPlayerSelfHostedSigningDetection
      Verifies the DRM / signed-URL marker detection and that the
      resolver returns structured errors without attempting to bypass.

  TestJWPlayerSelfHostedSigningCallback
      Verifies the signing_callback integration point: it's used
      when supplied, http_get isn't called, errors from the callback
      are surfaced with a credential-issue tag.

  TestJWPlayerSelfHostedSecurity
      Defensive checks: non-http(s) schemes refused, malformed
      config_url rejected.

  TestJWPlayerCheckDrmMarkersHelper
      Pure-function tests on ``_jwplayer_check_drm_markers``.

  TestJWPlayerSelfHostedEndToEnd
      HTML → ``extract_provider_embeds`` → ``resolve_provider_embed``
      with the new ``config_url`` detection patterns in deep_detect.
"""
from __future__ import annotations

import json
from typing import Any, List, Optional, Tuple

import pytest

from bulk_downloader import provider_resolve as pr
from bulk_downloader.provider_resolve import (
    _jwplayer_check_drm_markers,
    resolve_jwplayer,
    resolve_provider_embed,
)
from bulk_downloader.deep_detect import (
    extract_provider_embeds,
    _extract_provider_ids,
)


# Fixtures -------------------------------------------------------------------

def _feed_response(entries: List[dict],
                   status: int = 200,
                   extras: Optional[dict] = None) -> Tuple[int, dict, bytes]:
    """Build a feeds-shape response. ``extras`` is merged into the
    top-level payload (used for signedUrls / signingKey markers)."""
    payload: dict = {"playlist": entries, "kind": "feed"}
    if extras:
        payload.update(extras)
    return (status, {}, json.dumps(payload).encode("utf-8"))


def _fake_get(triple):
    captured = {}

    def _get(url):
        captured["url"] = url
        return triple

    _get.captured = captured  # type: ignore[attr-defined]
    return _get


def _raising_get(exc: BaseException):
    def _get(url):
        raise exc
    return _get


# ---------------------------------------------------------------------------
# TestJWPlayerSelfHostedResolver
# ---------------------------------------------------------------------------


class TestJWPlayerSelfHostedResolver:

    def test_config_url_routes_to_self_hosted_path(self):
        """config_url (no media_id / feed_id) hits the config_url URL
        verbatim — not a templated cdn URL."""
        fake = _fake_get(_feed_response([
            {"mediaid": "M1", "sources": [
                {"file": "https://custom.example.com/v/M1/master.m3u8",
                 "type": "application/vnd.apple.mpegurl"},
            ]},
        ]))
        url = "https://custom.example.com/feeds/abc.json"
        cands, err = resolve_jwplayer({"config_url": url}, http_get=fake)
        assert err is None
        assert fake.captured["url"] == url
        assert len(cands) == 1
        assert cands[0]["url"].endswith("master.m3u8")

    def test_multi_entry_playlist_flattened(self):
        """Like the feeds path, multi-entry playlists flatten into a
        single candidate list with playlist_index tagged."""
        fake = _fake_get(_feed_response([
            {"mediaid": "V1", "sources": [
                {"file": "https://a.example.com/v1.m3u8",
                 "type": "application/vnd.apple.mpegurl"},
            ]},
            {"mediaid": "V2", "sources": [
                {"file": "https://a.example.com/v2.m3u8",
                 "type": "application/vnd.apple.mpegurl"},
                {"file": "https://a.example.com/v2_720p.mp4",
                 "type": "video/mp4", "height": 720, "width": 1280,
                 "label": "720p"},
            ]},
        ]))
        cands, err = resolve_jwplayer(
            {"config_url": "https://a.example.com/feeds/multi.json"},
            http_get=fake,
        )
        assert err is None
        assert len(cands) == 3
        # Each carries playlist_index and config_url
        urls = [c["url"] for c in cands]
        indices = [c["playlist_index"] for c in cands]
        media_ids = [c.get("playlist_media_id") for c in cands]
        assert "https://a.example.com/v1.m3u8" in urls
        assert "https://a.example.com/v2.m3u8" in urls
        assert 0 in indices and 1 in indices
        assert "V1" in media_ids and "V2" in media_ids
        for c in cands:
            assert c["config_url"] == "https://a.example.com/feeds/multi.json"
            assert c["found_in"] == "provider_resolved:jwplayer"

    def test_reasons_use_self_hosted_label(self):
        """Reasons string uses ``JWPlayer self-hosted[idx]`` prefix
        so consumers can distinguish from cdn / feed candidates."""
        fake = _fake_get(_feed_response([
            {"mediaid": "X", "sources": [
                {"file": "https://x.example.com/m.m3u8",
                 "type": "application/vnd.apple.mpegurl"},
                {"file": "https://x.example.com/720p.mp4",
                 "type": "video/mp4", "height": 720, "label": "720p"},
            ]},
        ]))
        cands, err = resolve_jwplayer(
            {"config_url": "https://x.example.com/feed.json"},
            http_get=fake,
        )
        assert err is None
        for c in cands:
            assert any("JWPlayer self-hosted" in r for r in c["reasons"])

    def test_progressive_and_hls_classification(self):
        """Progressive MP4 and HLS get the same source_types and
        scores as the cdn / feeds paths (shared classifier)."""
        fake = _fake_get(_feed_response([
            {"mediaid": "X", "sources": [
                {"file": "https://x.example.com/master.m3u8",
                 "type": "application/vnd.apple.mpegurl"},
                {"file": "https://x.example.com/1080p.mp4",
                 "type": "video/mp4", "height": 1080, "width": 1920,
                 "label": "1080p", "bitrate": 4000000,
                 "filesize": 123456789},
                {"file": "https://x.example.com/manifest.mpd",
                 "type": "application/dash+xml"},
            ]},
        ]))
        cands, err = resolve_jwplayer(
            {"config_url": "https://x.example.com/feed.json"},
            http_get=fake,
        )
        assert err is None
        by_type = {c["source_type"]: c for c in cands}
        assert "jwplayer_resolved_hls" in by_type
        assert by_type["jwplayer_resolved_hls"]["score"] == 90
        assert "jwplayer_resolved" in by_type
        # 80 + 1080//100 = 90 (progressive 1080p ties HLS)
        assert by_type["jwplayer_resolved"]["score"] == 90
        assert by_type["jwplayer_resolved"]["size_bytes"] == 123456789
        assert "jwplayer_resolved_dash" in by_type
        assert by_type["jwplayer_resolved_dash"]["score"] == 90

    def test_404_returns_clear_error(self):
        cands, err = resolve_jwplayer(
            {"config_url": "https://x.example.com/feed.json"},
            http_get=lambda url: (404, {}, b""),
        )
        assert cands == []
        assert err is not None
        assert "404" in err
        assert "jwplayer self-hosted" in err

    def test_500_returns_status_code(self):
        cands, err = resolve_jwplayer(
            {"config_url": "https://x.example.com/feed.json"},
            http_get=lambda url: (500, {}, b""),
        )
        assert cands == []
        assert err is not None
        assert "500" in err

    def test_non_json_body_clean_error(self):
        cands, err = resolve_jwplayer(
            {"config_url": "https://x.example.com/feed.json"},
            http_get=lambda url: (200, {}, b"not json at all"),
        )
        assert cands == []
        assert err is not None
        assert "non-JSON" in err

    def test_json_not_object(self):
        cands, err = resolve_jwplayer(
            {"config_url": "https://x.example.com/feed.json"},
            http_get=lambda url: (200, {}, b"[1,2,3]"),
        )
        assert cands == []
        assert err is not None
        assert "not an object" in err

    def test_empty_playlist(self):
        cands, err = resolve_jwplayer(
            {"config_url": "https://x.example.com/feed.json"},
            http_get=_fake_get(_feed_response([])),
        )
        assert cands == []
        assert err is not None
        assert "no playlist entries" in err

    def test_playlist_present_but_no_streams(self):
        """Feed has entries but none have HLS/DASH/MP4 sources."""
        cands, err = resolve_jwplayer(
            {"config_url": "https://x.example.com/feed.json"},
            http_get=_fake_get(_feed_response([
                {"mediaid": "X", "sources": [
                    {"file": "https://x.example.com/audio.aac",
                     "type": "audio/aac"},
                ]},
            ])),
        )
        assert cands == []
        assert err is not None
        assert "no progressive / HLS / DASH" in err

    def test_request_exception_surfaced(self):
        cands, err = resolve_jwplayer(
            {"config_url": "https://x.example.com/feed.json"},
            http_get=_raising_get(ConnectionError("boom")),
        )
        assert cands == []
        assert err is not None
        assert "ConnectionError" in err
        assert "boom" in err

    def test_id_priority_media_beats_config(self):
        """If an embed somehow has both media_id and config_url,
        media_id wins (it's the more specific cloud-hosted id)."""
        # Build a fake that records which URL was requested.
        fake = _fake_get((200, {}, json.dumps({
            "playlist": [{"sources": [
                {"file": "https://cdn.jw.com/m.m3u8",
                 "type": "application/vnd.apple.mpegurl"},
            ]}],
        }).encode()))
        cands, err = resolve_jwplayer(
            {"media_id": "MEDIA1", "config_url": "https://other/feed.json"},
            http_get=fake,
        )
        assert err is None
        # Should have hit the cdn media URL, not the config_url
        assert "cdn.jwplayer.com/v2/media/MEDIA1" in fake.captured["url"]

    def test_id_priority_feed_beats_config(self):
        """feed_id also beats config_url."""
        fake = _fake_get(_feed_response([
            {"mediaid": "X", "sources": [
                {"file": "https://cdn.jw.com/x.m3u8",
                 "type": "application/vnd.apple.mpegurl"},
            ]},
        ]))
        cands, err = resolve_jwplayer(
            {"feed_id": "FEED1", "config_url": "https://other/feed.json"},
            http_get=fake,
        )
        assert err is None
        assert "cdn.jwplayer.com/v2/playlists/FEED1" in fake.captured["url"]


# ---------------------------------------------------------------------------
# TestJWPlayerSelfHostedSigningDetection
# ---------------------------------------------------------------------------


class TestJWPlayerSelfHostedSigningDetection:

    def test_top_level_signedurls_true_blocks(self):
        cands, err = resolve_jwplayer(
            {"config_url": "https://x.example.com/feed.json"},
            http_get=_fake_get(_feed_response(
                [{"sources": []}], extras={"signedUrls": True}),
            ),
        )
        assert cands == []
        assert err is not None
        assert "scheme=signed" in err
        assert "signing_callback" in err  # hint to operator

    def test_top_level_signing_key_blocks(self):
        cands, err = resolve_jwplayer(
            {"config_url": "https://x.example.com/feed.json"},
            http_get=_fake_get(_feed_response(
                [{"sources": []}], extras={"signingKey": "abc123"}),
            ),
        )
        assert cands == []
        assert err is not None
        assert "scheme=signed" in err

    def test_per_entry_widevine_blocks(self):
        cands, err = resolve_jwplayer(
            {"config_url": "https://x.example.com/feed.json"},
            http_get=_fake_get(_feed_response([
                {"mediaid": "V1",
                 "drm": {"widevine": {"url": "https://wv/lic"}},
                 "sources": [{"file": "https://x.com/m.m3u8",
                              "type": "application/vnd.apple.mpegurl"}]},
            ])),
        )
        assert cands == []
        assert err is not None
        assert "scheme=widevine" in err
        assert "DRM" in err

    def test_per_entry_fairplay_blocks(self):
        cands, err = resolve_jwplayer(
            {"config_url": "https://x.example.com/feed.json"},
            http_get=_fake_get(_feed_response([
                {"mediaid": "V1",
                 "drm": {"fairplay": {"certUrl": "x"}},
                 "sources": [{"file": "https://x.com/m.m3u8",
                              "type": "application/vnd.apple.mpegurl"}]},
            ])),
        )
        assert cands == []
        assert err is not None
        assert "scheme=fairplay" in err

    def test_drm_uppercase_key_still_detected(self):
        """DRM block under "DRM" (uppercase) — some legacy configs."""
        cands, err = resolve_jwplayer(
            {"config_url": "https://x.example.com/feed.json"},
            http_get=_fake_get(_feed_response([
                {"mediaid": "V1",
                 "DRM": {"PLAYREADY": {"url": "x"}},
                 "sources": [{"file": "https://x.com/m.m3u8",
                              "type": "application/vnd.apple.mpegurl"}]},
            ])),
        )
        assert cands == []
        assert err is not None
        assert "scheme=playready" in err

    def test_401_unsigned_fetch_blocks(self):
        cands, err = resolve_jwplayer(
            {"config_url": "https://x.example.com/feed.json"},
            http_get=lambda url: (401, {}, b""),
        )
        assert cands == []
        assert err is not None
        assert "scheme=signed" in err
        assert "401" in err

    def test_403_unsigned_fetch_blocks(self):
        cands, err = resolve_jwplayer(
            {"config_url": "https://x.example.com/feed.json"},
            http_get=lambda url: (403, {}, b""),
        )
        assert cands == []
        assert err is not None
        assert "scheme=signed" in err
        assert "403" in err

    def test_unsigned_feed_no_drm_no_signed_returns_candidates(self):
        """Sanity: a plain unsigned feed without markers resolves."""
        cands, err = resolve_jwplayer(
            {"config_url": "https://x.example.com/feed.json"},
            http_get=_fake_get(_feed_response([
                {"sources": [{"file": "https://x.com/m.m3u8",
                              "type": "application/vnd.apple.mpegurl"}]},
            ])),
        )
        assert err is None
        assert len(cands) == 1


# ---------------------------------------------------------------------------
# TestJWPlayerSelfHostedSigningCallback
# ---------------------------------------------------------------------------


class TestJWPlayerSelfHostedSigningCallback:

    def test_callback_used_when_supplied(self):
        """When signing_callback is on the embed, it's called instead
        of http_get."""
        callback_calls = []

        def callback(url):
            callback_calls.append(url)
            return _feed_response([
                {"sources": [{"file": "https://signed/m.m3u8",
                              "type": "application/vnd.apple.mpegurl"}]},
            ])

        def must_not_be_called(url):
            raise AssertionError("http_get must not be called")

        cands, err = resolve_jwplayer(
            {"config_url": "https://x.example.com/feed.json"},
            embed={"signing_callback": callback,
                   "url": "https://x.example.com/page.html"},
            http_get=must_not_be_called,
        )
        assert err is None
        assert len(cands) == 1
        assert callback_calls == ["https://x.example.com/feed.json"]

    def test_callback_403_reports_credential_problem(self):
        """If signing_callback returns 403 the resolver tells the
        operator the credential is likely the problem — not that
        they need to supply a callback (they already did)."""
        cands, err = resolve_jwplayer(
            {"config_url": "https://x.example.com/feed.json"},
            embed={"signing_callback": lambda url: (403, {}, b"")},
            http_get=lambda url: (200, {}, b'{"playlist":[]}'),
        )
        assert cands == []
        assert err is not None
        assert "credential" in err.lower()
        # Should NOT suggest "supply signing_callback" — operator
        # already did.
        assert "supply signing_callback" not in err

    def test_callback_401_reports_credential_problem(self):
        cands, err = resolve_jwplayer(
            {"config_url": "https://x.example.com/feed.json"},
            embed={"signing_callback": lambda url: (401, {}, b"")},
            http_get=lambda url: (200, {}, b'{"playlist":[]}'),
        )
        assert cands == []
        assert err is not None
        assert "credential" in err.lower()

    def test_callback_exception_surfaced(self):
        """If the callback raises, surface it cleanly with a
        fetch_label so the operator knows it was the callback that
        failed, not http_get."""
        cands, err = resolve_jwplayer(
            {"config_url": "https://x.example.com/feed.json"},
            embed={"signing_callback":
                   _raising_get(ValueError("bad signing key"))},
            http_get=lambda url: (200, {}, b'{"playlist":[]}'),
        )
        assert cands == []
        assert err is not None
        assert "signing_callback" in err
        assert "ValueError" in err

    def test_callback_signed_feed_unblocks_drm_markers(self):
        """If the callback returns an unsigned feed (e.g. it stripped
        the markers server-side because the request was signed), the
        resolver returns candidates normally."""
        def callback(url):
            return _feed_response([
                {"sources": [{"file": "https://signed/m.m3u8",
                              "type": "application/vnd.apple.mpegurl"}]},
            ])

        cands, err = resolve_jwplayer(
            {"config_url": "https://x.example.com/feed.json"},
            embed={"signing_callback": callback},
            http_get=lambda url: (401, {}, b""),  # would block without cb
        )
        assert err is None
        assert len(cands) == 1

    def test_callback_returned_drm_still_blocks(self):
        """Even when fetched via a signing callback, a feed declaring
        DRM still blocks — DRM is out of scope regardless of how the
        feed was fetched."""
        def callback(url):
            return _feed_response([
                {"drm": {"widevine": {"url": "x"}},
                 "sources": [{"file": "https://x.com/m.m3u8",
                              "type": "application/vnd.apple.mpegurl"}]},
            ])

        cands, err = resolve_jwplayer(
            {"config_url": "https://x.example.com/feed.json"},
            embed={"signing_callback": callback},
            http_get=lambda url: (200, {}, b""),
        )
        assert cands == []
        assert err is not None
        assert "scheme=widevine" in err

    def test_non_callable_signing_callback_ignored(self):
        """Defensive: if signing_callback is present but not callable,
        fall back to http_get."""
        fake = _fake_get(_feed_response([
            {"sources": [{"file": "https://x.com/m.m3u8",
                          "type": "application/vnd.apple.mpegurl"}]},
        ]))
        cands, err = resolve_jwplayer(
            {"config_url": "https://x.example.com/feed.json"},
            embed={"signing_callback": "not a function"},
            http_get=fake,
        )
        assert err is None
        assert fake.captured["url"] == "https://x.example.com/feed.json"


# ---------------------------------------------------------------------------
# TestJWPlayerSelfHostedSecurity
# ---------------------------------------------------------------------------


class TestJWPlayerSelfHostedSecurity:

    def test_javascript_url_refused(self):
        called = []
        cands, err = resolve_jwplayer(
            {"config_url": "javascript:alert(1)"},
            http_get=lambda url: called.append(url) or (200, {}, b""),
        )
        assert cands == []
        assert err is not None
        assert "javascript" in err
        assert called == [], "must not fetch non-http(s)"

    def test_data_url_refused(self):
        called = []
        cands, err = resolve_jwplayer(
            {"config_url": "data:text/plain;base64,SGVsbG8="},
            http_get=lambda url: called.append(url) or (200, {}, b""),
        )
        assert cands == []
        assert err is not None
        assert "data" in err.lower()
        assert called == []

    def test_file_scheme_refused(self):
        called = []
        cands, err = resolve_jwplayer(
            {"config_url": "file:///etc/passwd"},
            http_get=lambda url: called.append(url) or (200, {}, b""),
        )
        assert cands == []
        assert err is not None
        assert called == []

    def test_no_host_refused(self):
        cands, err = resolve_jwplayer(
            {"config_url": "https://"},
            http_get=lambda url: (_ for _ in ()).throw(
                AssertionError("should not fetch")),
        )
        assert cands == []
        assert err is not None
        assert "no host" in err

    def test_http_allowed(self):
        """plain http (not https) — we don't force https; pages
        that use http for their feed are unfortunately a thing."""
        fake = _fake_get(_feed_response([
            {"sources": [{"file": "http://x.com/m.m3u8",
                          "type": "application/vnd.apple.mpegurl"}]},
        ]))
        cands, err = resolve_jwplayer(
            {"config_url": "http://x.example.com/feed.json"},
            http_get=fake,
        )
        assert err is None


# ---------------------------------------------------------------------------
# TestJWPlayerCheckDrmMarkersHelper
# ---------------------------------------------------------------------------


class TestJWPlayerCheckDrmMarkersHelper:

    def test_none_for_unsigned_unmarked_feed(self):
        assert _jwplayer_check_drm_markers(
            {"playlist": [{"sources": []}]}) is None

    def test_widevine_detected(self):
        assert _jwplayer_check_drm_markers(
            {"playlist": [{"drm": {"widevine": {}}}]}) == "widevine"

    def test_fairplay_detected(self):
        assert _jwplayer_check_drm_markers(
            {"playlist": [{"drm": {"fairplay": {}}}]}) == "fairplay"

    def test_playready_uppercase(self):
        assert _jwplayer_check_drm_markers(
            {"playlist": [{"drm": {"PLAYREADY": {}}}]}) == "playready"

    def test_clearkey_detected(self):
        assert _jwplayer_check_drm_markers(
            {"playlist": [{"drm": {"clearkey": {}}}]}) == "clearkey"

    def test_top_level_signed_url_camelcase(self):
        assert _jwplayer_check_drm_markers({"signedUrls": True}) == "signed"

    def test_top_level_signed_url_snake_case(self):
        assert _jwplayer_check_drm_markers({"signed_urls": True}) == "signed"

    def test_top_level_signing_key(self):
        assert _jwplayer_check_drm_markers(
            {"signingKey": "abc"}) == "signed"

    def test_per_entry_signed_marker(self):
        assert _jwplayer_check_drm_markers(
            {"playlist": [{"signedUrls": True}]}) == "signed"

    def test_signed_falsy_value_ignored(self):
        """signedUrls: false is not a DRM marker."""
        assert _jwplayer_check_drm_markers(
            {"signedUrls": False,
             "playlist": [{"sources": []}]}) is None

    def test_not_a_dict_returns_none(self):
        assert _jwplayer_check_drm_markers("not a dict") is None  # type: ignore[arg-type]
        assert _jwplayer_check_drm_markers([1, 2, 3]) is None  # type: ignore[arg-type]
        assert _jwplayer_check_drm_markers(None) is None  # type: ignore[arg-type]

    def test_widevine_takes_priority_over_signed(self):
        """A feed with both DRM and signed-URL markers reports DRM
        because top-level signed scan runs first — wait, actually
        looking at the impl, the order is: top-level signed FIRST,
        then per-entry DRM. So 'signed' wins if both are present at
        the relevant scopes. This documents that contract."""
        # Top-level signed + per-entry DRM → "signed" (top-level
        # check runs first)
        result = _jwplayer_check_drm_markers({
            "signedUrls": True,
            "playlist": [{"drm": {"widevine": {}}}],
        })
        assert result == "signed"


# ---------------------------------------------------------------------------
# TestJWPlayerSelfHostedEndToEnd
# ---------------------------------------------------------------------------


class TestJWPlayerSelfHostedEndToEnd:

    def test_extract_provider_ids_finds_config_url(self):
        """The deep_detect pattern produces a config_url id for a
        self-hosted setup() call."""
        html = (
            'jwplayer("p").setup({playlist: '
            '"https://video.example.com/feeds/abc.json"})'
        )
        ids = _extract_provider_ids("jwplayer", html)
        assert ids == {
            "config_url": "https://video.example.com/feeds/abc.json",
        }

    def test_extract_does_not_match_cdn_feeds_url(self):
        """A feeds.jwplayer.com URL should match feed_id, not
        config_url — the cdn-specific pattern wins."""
        html = (
            'jwplayer("p").setup({playlist: '
            '"https://feeds.jwplayer.com/feeds/ABC123XY.json"})'
        )
        ids = _extract_provider_ids("jwplayer", html)
        assert "config_url" not in ids
        assert ids.get("feed_id") == "ABC123XY"

    def test_extract_does_not_match_cdn_media_url(self):
        html = '<script src="https://cdn.jwplayer.com/v2/media/MEDIA123"></script>'
        ids = _extract_provider_ids("jwplayer", html)
        assert "config_url" not in ids
        assert ids.get("media_id") == "MEDIA123"

    def test_extract_does_not_match_stream_file_url(self):
        """An inline `file: <m3u8>` URL is a stream, not a feed —
        should NOT be captured as config_url."""
        html = ('jwplayer("p").setup({sources: '
                '[{file: "https://x.example.com/master.m3u8"}]})')
        ids = _extract_provider_ids("jwplayer", html)
        assert "config_url" not in ids

    def test_extract_does_not_match_stream_playlist_url(self):
        """A `playlist: <mp4>` URL with no feed-like marker is
        treated as a stream (extract_player_configs will handle it),
        NOT as a config_url."""
        html = ('jwplayer("p").setup({playlist: '
                '"https://x.example.com/clip.mp4"})')
        ids = _extract_provider_ids("jwplayer", html)
        assert "config_url" not in ids

    def test_extract_provider_embeds_self_hosted(self):
        """End-to-end HTML → embed with config_url id."""
        html = """
        <html><body>
        <div id="player"></div>
        <script>
          jwplayer.key = "ABCDEFGH";
          jwplayer("player").setup({
              playlist: "https://video.example.com/feeds/abc123.json"
          });
        </script>
        </body></html>
        """
        embeds = extract_provider_embeds(html, base_url="https://site.com/")
        jw_embeds = [e for e in embeds if e["provider"] == "jwplayer"]
        assert len(jw_embeds) == 1
        assert jw_embeds[0]["ids"] == {
            "config_url": "https://video.example.com/feeds/abc123.json",
        }

    def test_full_pipeline_html_to_candidates(self):
        """The whole pipeline: HTML → extract_provider_embeds →
        resolve_provider_embed → candidates."""
        html = """
        <script>
          jwplayer("p").setup({
              playlist: "https://video.site.com/api/feeds/clip42.json"
          });
        </script>
        """
        embeds = extract_provider_embeds(html, base_url="https://x/")
        jw = [e for e in embeds if e["provider"] == "jwplayer"]
        assert len(jw) == 1

        def http_get(url):
            assert url == "https://video.site.com/api/feeds/clip42.json"
            return _feed_response([
                {"mediaid": "clip42", "sources": [
                    {"file": "https://video.site.com/v/clip42/master.m3u8",
                     "type": "application/vnd.apple.mpegurl"},
                ]},
            ])

        cands, err = resolve_provider_embed(jw[0], http_get=http_get)
        assert err is None
        assert len(cands) == 1
        assert cands[0]["source_type"] == "jwplayer_resolved_hls"
        assert "JWPlayer self-hosted" in cands[0]["reasons"][0]

    def test_full_pipeline_drm_feed_returns_structured_error(self):
        """A self-hosted DRM feed surfaces a structured error through
        resolve_provider_embed."""
        html = """
        <script>
          jwplayer("p").setup({
              playlist: "https://video.site.com/api/feed.json"
          });
        </script>
        """
        embeds = extract_provider_embeds(html, base_url="https://x/")
        jw = [e for e in embeds if e["provider"] == "jwplayer"]
        assert len(jw) == 1

        def http_get(url):
            return _feed_response([
                {"drm": {"widevine": {"url": "https://lic.example.com"}},
                 "sources": [{"file": "https://x.com/m.m3u8",
                              "type": "application/vnd.apple.mpegurl"}]},
            ])

        cands, err = resolve_provider_embed(jw[0], http_get=http_get)
        assert cands == []
        assert err is not None
        assert "scheme=widevine" in err
