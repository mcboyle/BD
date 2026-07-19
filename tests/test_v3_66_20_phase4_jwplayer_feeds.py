"""v3.66.20 — Phase 4 P4: JWPlayer feeds endpoint.

Tests the new feeds-endpoint branch added to ``resolve_jwplayer``.
The cloud-media branch is covered by ``test_v3_66_18_phase4_jwplayer.py``;
this file covers only the new ``ids["feed_id"]`` path.

Test classes
------------

  TestJWPlayerFeedsResolver
      Unit tests on the feeds branch: multi-entry playlist handling,
      per-candidate playlist_index / playlist_media_id tagging,
      shared source-classifier behaviour between feeds and cloud-media.

  TestJWPlayerFeedsErrors
      Error paths specific to the feeds endpoint: 404 on a feed_id
      that doesn't exist, 403 on signed feeds, empty playlist.

  TestSharedSourceClassifier
      Pure-function tests on ``_jwplayer_emit_sources`` shared between
      the cloud-media and feeds paths.

The cloud-media path is still tested by v3.66.18's test file; we
re-verify the refactor didn't break it via the regression run, not
via duplicate tests here.
"""
from __future__ import annotations

import json
from typing import List

import pytest

from bulk_downloader import provider_resolve as pr
from bulk_downloader.provider_resolve import (
    _jwplayer_emit_sources,
    resolve_jwplayer,
    resolve_provider_embed,
)


# Fixtures -------------------------------------------------------------------

def _feed_response(entries: List[dict], status: int = 200) -> tuple:
    """Wrap a list of playlist entries into a feeds-endpoint response.

    Each entry should have at minimum ``mediaid`` and ``sources``.
    Returns the (status, headers, body_bytes) triple our fake http_get
    contract expects.
    """
    payload = {"playlist": entries, "kind": "feed"}
    return (status, {}, json.dumps(payload).encode("utf-8"))


def _fake_get(triple):
    captured = {}
    def _get(url):
        captured["url"] = url
        return triple
    _get.captured = captured  # type: ignore[attr-defined]
    return _get


# ---------------------------------------------------------------------------
# TestJWPlayerFeedsResolver
# ---------------------------------------------------------------------------

class TestJWPlayerFeedsResolver:

    def test_feed_id_routes_to_feeds_endpoint(self):
        """feed_id (no media_id) hits the feeds URL, not the media URL."""
        fake = _fake_get(_feed_response([
            {"mediaid": "M1", "sources": [
                {"file": "https://cdn.jw.com/M1/master.m3u8",
                 "type": "application/vnd.apple.mpegurl"},
            ]},
        ]))
        cands, err = resolve_jwplayer(
            {"feed_id": "FEED1234"}, http_get=fake)
        assert err is None
        assert len(cands) == 1
        url = fake.captured["url"]
        assert "/playlists/FEED1234" in url
        assert "/media/" not in url

    def test_single_entry_feed(self):
        cands, err = resolve_jwplayer(
            {"feed_id": "FEED1234"},
            http_get=_fake_get(_feed_response([
                {"mediaid": "MID1", "sources": [
                    {"file": "https://cdn.jw.com/m1/master.m3u8",
                     "type": "application/vnd.apple.mpegurl"},
                    {"file": "https://cdn.jw.com/m1/720.mp4",
                     "type": "video/mp4", "height": 720, "width": 1280},
                ]},
            ])))
        assert err is None
        assert len(cands) == 2
        for c in cands:
            assert c["playlist_index"] == 0
            assert c["playlist_media_id"] == "MID1"
            assert c["feed_id"] == "FEED1234"
            assert c["found_in"] == "provider_resolved:jwplayer"

    def test_multi_entry_feed_flattens_with_indices(self):
        cands, err = resolve_jwplayer(
            {"feed_id": "FEED1234"},
            http_get=_fake_get(_feed_response([
                {"mediaid": "M1", "sources": [
                    {"file": "https://cdn.jw.com/m1/master.m3u8",
                     "type": "application/vnd.apple.mpegurl"},
                ]},
                {"mediaid": "M2", "sources": [
                    {"file": "https://cdn.jw.com/m2/master.m3u8",
                     "type": "application/vnd.apple.mpegurl"},
                    {"file": "https://cdn.jw.com/m2/480.mp4",
                     "type": "video/mp4", "height": 480, "width": 854},
                ]},
                {"mediaid": "M3", "sources": [
                    {"file": "https://cdn.jw.com/m3/manifest.mpd",
                     "type": "application/dash+xml"},
                ]},
            ])))
        assert err is None
        assert len(cands) == 4
        # Group by playlist_index
        idx_to_mid = {c["playlist_index"]: c["playlist_media_id"]
                      for c in cands}
        assert idx_to_mid == {0: "M1", 1: "M2", 2: "M3"}
        # Source types
        type_counts = {}
        for c in cands:
            type_counts[c["source_type"]] = \
                type_counts.get(c["source_type"], 0) + 1
        assert type_counts == {
            "jwplayer_resolved_hls": 2,
            "jwplayer_resolved": 1,
            "jwplayer_resolved_dash": 1,
        }

    def test_feed_reasons_string_distinguishes_from_cdn(self):
        """The feeds branch tags reasons with 'JWPlayer feed[i]' so
        consumers can distinguish from the cloud-media path."""
        cands, _ = resolve_jwplayer(
            {"feed_id": "F"},
            http_get=_fake_get(_feed_response([
                {"mediaid": "M1", "sources": [
                    {"file": "https://cdn.jw.com/m1/master.m3u8",
                     "type": "application/vnd.apple.mpegurl"},
                ]},
            ])))
        assert cands[0]["reasons"][0].startswith("JWPlayer feed[0]")

    def test_entry_without_mediaid_still_emits_with_null(self):
        """Defensive: entry has sources but no mediaid → emitted with
        playlist_media_id absent."""
        cands, _ = resolve_jwplayer(
            {"feed_id": "F"},
            http_get=_fake_get(_feed_response([
                {"sources": [
                    {"file": "https://cdn.jw.com/anon/master.m3u8",
                     "type": "application/vnd.apple.mpegurl"},
                ]},
            ])))
        assert len(cands) == 1
        assert "playlist_media_id" not in cands[0]
        assert cands[0]["playlist_index"] == 0

    def test_media_id_takes_priority_over_feed_id(self):
        """If both ids are present, media_id wins (cdn-media path)."""
        # Cloud-media response shape
        media_resp = (200, {}, json.dumps({
            "playlist": [{"sources": [
                {"file": "https://cdn.jw.com/cloud/master.m3u8",
                 "type": "application/vnd.apple.mpegurl"},
            ]}],
        }).encode())
        fake = _fake_get(media_resp)
        cands, err = resolve_jwplayer(
            {"media_id": "MID", "feed_id": "FID"}, http_get=fake)
        assert err is None
        assert "/media/MID" in fake.captured["url"]
        assert "/playlists/" not in fake.captured["url"]
        # Reason string should be the cdn variant, not feed[i]
        assert cands[0]["reasons"][0].startswith("JWPlayer cdn")


# ---------------------------------------------------------------------------
# TestJWPlayerFeedsErrors
# ---------------------------------------------------------------------------

class TestJWPlayerFeedsErrors:

    def test_http_404(self):
        cands, err = resolve_jwplayer(
            {"feed_id": "BAD"}, http_get=_fake_get((404, {}, b"")))
        assert cands == []
        assert "404" in err
        assert "feed" in err.lower()

    def test_http_403(self):
        cands, err = resolve_jwplayer(
            {"feed_id": "SIGNED"}, http_get=_fake_get((403, {}, b"")))
        assert cands == []
        assert "403" in err

    def test_http_5xx(self):
        cands, err = resolve_jwplayer(
            {"feed_id": "F"}, http_get=_fake_get((502, {}, b"")))
        assert cands == []
        assert "502" in err

    def test_non_json_body(self):
        cands, err = resolve_jwplayer(
            {"feed_id": "F"},
            http_get=_fake_get((200, {}, b"<html>not json</html>")))
        assert cands == []
        assert "non-JSON" in err

    def test_json_not_object(self):
        cands, err = resolve_jwplayer(
            {"feed_id": "F"},
            http_get=_fake_get((200, {}, b"[1,2,3]")))
        assert cands == []
        assert "not an object" in err

    def test_empty_playlist(self):
        cands, err = resolve_jwplayer(
            {"feed_id": "F"},
            http_get=_fake_get((200, {}, b'{"playlist": []}')))
        assert cands == []
        assert "no playlist entries" in err

    def test_playlist_with_only_invalid_entries(self):
        """Entries are present but none have usable sources."""
        cands, err = resolve_jwplayer(
            {"feed_id": "F"},
            http_get=_fake_get(_feed_response([
                {"mediaid": "M1", "sources": "not-a-list"},
                {"mediaid": "M2", "sources": [
                    # Non-recognized MIME, no fallback extension
                    {"file": "https://x.com/file.xyz", "type": "audio/wav"},
                ]},
            ])))
        assert cands == []
        assert "no progressive / HLS / DASH" in err

    def test_network_exception(self):
        def boom(url):
            raise TimeoutError("upstream timeout")
        cands, err = resolve_jwplayer(
            {"feed_id": "F"}, http_get=boom)
        assert cands == []
        assert "jwplayer feeds request failed" in err
        assert "TimeoutError" in err

    def test_missing_both_ids(self):
        cands, err = resolve_jwplayer({}, http_get=_fake_get((200, {}, b"")))
        assert cands == []
        # v3.66.23: config_url is a third valid alternative.
        assert err == "missing media_id, feed_id, or config_url"


# ---------------------------------------------------------------------------
# TestSharedSourceClassifier
# ---------------------------------------------------------------------------

class TestSharedSourceClassifier:
    """Tests that the same per-source classification logic powers both
    paths. We exercise it directly here to lock in the behaviour without
    going through an HTTP fake."""

    def test_extra_fields_merged_into_candidates(self):
        out = _jwplayer_emit_sources(
            [{"file": "https://x/m.m3u8",
              "type": "application/vnd.apple.mpegurl"}],
            embed_url="https://embed.x/",
            label_prefix="JWPlayer test",
            extra_fields={"foo": "bar", "playlist_index": 7},
        )
        assert len(out) == 1
        assert out[0]["foo"] == "bar"
        assert out[0]["playlist_index"] == 7
        assert out[0]["source_type"] == "jwplayer_resolved_hls"

    def test_extension_fallback_without_type(self):
        """Source missing ``type`` → classifier falls back to extension."""
        out = _jwplayer_emit_sources(
            [{"file": "https://x/master.m3u8"}],
            embed_url=None, label_prefix="JWPlayer test",
        )
        assert len(out) == 1
        assert out[0]["source_type"] == "jwplayer_resolved_hls"

    def test_query_string_doesnt_break_extension_match(self):
        out = _jwplayer_emit_sources(
            [{"file": "https://x/master.m3u8?token=abc&exp=12345"}],
            embed_url=None, label_prefix="t",
        )
        assert out and out[0]["source_type"] == "jwplayer_resolved_hls"

    def test_dash_classified_separately_from_hls(self):
        out = _jwplayer_emit_sources(
            [
                {"file": "https://x/hls/master.m3u8",
                 "type": "application/vnd.apple.mpegurl"},
                {"file": "https://x/dash/manifest.mpd",
                 "type": "application/dash+xml"},
            ],
            embed_url=None, label_prefix="t",
        )
        types = sorted(c["source_type"] for c in out)
        assert types == ["jwplayer_resolved_dash", "jwplayer_resolved_hls"]
        # Both at score 90
        assert all(c["score"] == 90 for c in out)

    def test_progressive_height_drives_score(self):
        out = _jwplayer_emit_sources(
            [
                {"file": "https://x/360.mp4", "type": "video/mp4",
                 "height": 360},
                {"file": "https://x/720.mp4", "type": "video/mp4",
                 "height": 720},
                {"file": "https://x/1080.mp4", "type": "video/mp4",
                 "height": 1080},
            ],
            embed_url=None, label_prefix="t",
        )
        scores = [c["score"] for c in out]
        assert scores == [83, 87, 90]  # 80 + h//100

    def test_unrecognized_mime_skipped(self):
        out = _jwplayer_emit_sources(
            [
                {"file": "https://x/random.bin", "type": "x-unknown/x"},
                {"file": "https://x/audio.aac", "type": "audio/aac"},
            ],
            embed_url=None, label_prefix="t",
        )
        assert out == []

    def test_non_dict_sources_skipped(self):
        out = _jwplayer_emit_sources(
            ["not a dict", None, 123,
             {"file": "https://x/m.m3u8",
              "type": "application/vnd.apple.mpegurl"}],
            embed_url=None, label_prefix="t",
        )
        assert len(out) == 1
        assert out[0]["source_type"] == "jwplayer_resolved_hls"

    def test_missing_file_field_skipped(self):
        out = _jwplayer_emit_sources(
            [{"type": "application/vnd.apple.mpegurl"},
             {"file": "",
              "type": "application/vnd.apple.mpegurl"}],
            embed_url=None, label_prefix="t",
        )
        assert out == []


# ---------------------------------------------------------------------------
# TestJWPlayerFeedsDispatcherIntegration
# ---------------------------------------------------------------------------

class TestJWPlayerFeedsDispatcherIntegration:
    """End-to-end through ``resolve_provider_embed`` for the feeds
    branch. Mirrors the equivalent class for v3.66.18 (cloud-media)
    and v3.66.19 (Brightcove)."""

    def test_dispatcher_routes_feeds_through(self):
        emb = {
            "provider": "jwplayer",
            "ids": {"feed_id": "FEED1234"},
            "url": "https://content.jwplatform.com/feeds/FEED1234.html",
        }
        fake = _fake_get(_feed_response([
            {"mediaid": "M1", "sources": [
                {"file": "https://cdn.jw.com/m1/master.m3u8",
                 "type": "application/vnd.apple.mpegurl"},
            ]},
        ]))
        cands, err = resolve_provider_embed(emb, http_get=fake)
        assert err is None
        assert len(cands) == 1
        assert cands[0]["source_type"] == "jwplayer_resolved_hls"
        assert cands[0]["playlist_index"] == 0
        assert cands[0]["feed_id"] == "FEED1234"

    def test_dispatcher_cache_key_for_feed_id(self, monkeypatch):
        """The dispatcher uses _primary_id to key the cache. The feeds
        path doesn't have a single canonical 'media_id' to cache —
        verify the dispatcher still attempts a sensible key write
        (the first non-empty id value, which is the feed_id itself)."""
        monkeypatch.setattr(pr, "_now", lambda: 5_000_000.0)
        writes = []
        emb = {
            "provider": "jwplayer",
            "ids": {"feed_id": "FEED99"},
            "url": "https://x.com/feed/FEED99",
        }
        cands, err = resolve_provider_embed(
            emb,
            http_get=_fake_get(_feed_response([
                {"mediaid": "M1", "sources": [
                    {"file": "https://cdn.jw.com/m1/master.m3u8",
                     "type": "application/vnd.apple.mpegurl"},
                ]},
            ])),
            cache_write=lambda *a: writes.append(a),
        )
        assert err is None
        assert len(writes) == 1
        # _primary_id falls back to the first non-empty value when
        # no canonical key is present.
        assert writes[0][1] == "FEED99"
