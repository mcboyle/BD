"""v3.66.27 — Phase 5 P5-2: candidate-URL honeypot scoring.

Tests the deterministic-rules scorer added in
``bulk_downloader/honeypot_score.py``. Pure-function module — no
HTTP, no env reads — so tests are mostly table-driven over (input,
expected_score, expected_reason_subset) triples.

What's covered
--------------

  TestTrackerHost
      Pre-fetch rule: URL host on KNOWN_TRACKER_HOSTS. Includes
      subdomain handling (ads.doubleclick.net matches), edge boundary
      cases (mydoubleclick.net does NOT), and full hostname suffix
      attacks (notreallya-doubleclick.net does NOT).

  TestPixelPath
      Pre-fetch rule: URL path contains a PIXEL_PATH_TOKENS substring.

  TestEmptyPathOnlyQs
      Pre-fetch rule: ``/?v=...``-style cache-buster pixel shape.
      Negative cases ensure legitimate "/?id=abc" doesn't trip.

  TestMediaButHtml
      Post-fetch rule: media URL extension but probe Content-Type is
      HTML. Verifies manifest extensions (.m3u8 / .mpd) are exempt
      because they're inherently text.

  TestTinyMediaBody
      Post-fetch rule: media URL extension but probe Content-Length
      under 1KB. Verifies manifest extensions are exempt.

  TestCookieOnMedia
      Post-fetch rule: probe sets cookies on a media URL. Weak signal
      (0.40) — verified by its score, not just firing.

  TestHostClassRedirect
      Post-fetch rule: media URL redirects to a different host that
      is itself on KNOWN_TRACKER_HOSTS. Same-host redirects don't
      fire; redirects to non-tracker hosts don't fire.

  TestScoreCombination
      The rules combine by MAX, not sum. Verifies a candidate that
      hits multiple rules doesn't end up at score > 1.0.

  TestClassifyScore
      The ``classify_score`` zone mapping: drop / downscore / keep.

  TestMalformedInputs
      Robustness: non-dict candidate, missing url, empty url,
      malformed url, non-dict probe, non-dict headers, etc. All
      should return (0.0, "") rather than crashing.
"""
from __future__ import annotations

import pytest

from bulk_downloader import honeypot_score as hs
from bulk_downloader.honeypot_score import (
    classify_score,
    score_candidate,
    DEFAULT_DROP_THRESHOLD,
    DEFAULT_DOWNSCORE_THRESHOLD,
    KNOWN_TRACKER_HOSTS,
    PIXEL_PATH_TOKENS,
    MEDIA_EXTENSIONS,
    MANIFEST_EXTENSIONS,
    TINY_BODY_THRESHOLD_BYTES,
)


# ────────────────────────────────────────────────────────────────────
# TestTrackerHost: KNOWN_TRACKER_HOSTS rule (pre-fetch)
# ────────────────────────────────────────────────────────────────────

class TestTrackerHost:

    def test_exact_match(self):
        s, r = score_candidate({"url": "https://doubleclick.net/foo"})
        assert s >= 0.95
        assert "tracker_host" in r

    def test_subdomain_match(self):
        s, r = score_candidate(
            {"url": "https://ads.doubleclick.net/imp.gif"})
        assert s >= 0.95
        assert "tracker_host" in r

    def test_deep_subdomain_match(self):
        s, r = score_candidate(
            {"url": "https://eu.tracker.adnxs.com/foo"})
        assert s >= 0.95
        assert "tracker_host" in r

    def test_substring_attack_does_not_match(self):
        # mydoubleclick.net is NOT a subdomain of doubleclick.net
        s, r = score_candidate(
            {"url": "https://mydoubleclick.net/video.mp4"})
        assert "tracker_host" not in r

    def test_prefix_attack_does_not_match(self):
        s, r = score_candidate(
            {"url": "https://notreallya-doubleclick.net/video.mp4"})
        assert "tracker_host" not in r

    def test_clean_cdn_does_not_match(self):
        # A real CDN that ALSO carries content. Important to keep
        # these OUT of the tracker list.
        s, r = score_candidate(
            {"url": "https://d3rmlpe9o9z9b.cloudfront.net/video.mp4"})
        assert s == 0.0
        assert r == ""

    def test_case_insensitive_host(self):
        s, r = score_candidate(
            {"url": "https://ADS.Doubleclick.NET/imp.gif"})
        assert s >= 0.95
        assert "tracker_host" in r


# ────────────────────────────────────────────────────────────────────
# TestPixelPath: PIXEL_PATH_TOKENS rule (pre-fetch)
# ────────────────────────────────────────────────────────────────────

class TestPixelPath:

    def test_pixel_in_path(self):
        s, r = score_candidate(
            {"url": "https://cdn.example.com/pixel/track.gif"})
        assert s >= 0.85
        assert "pixel_path" in r

    def test_beacon_in_path(self):
        s, r = score_candidate(
            {"url": "https://cdn.example.com/api/beacon"})
        assert s >= 0.85
        assert "pixel_path" in r

    def test_imp_gif_in_path(self):
        s, r = score_candidate(
            {"url": "https://cdn.example.com/serve/imp.gif?x=1"})
        assert s >= 0.85
        assert "pixel_path" in r

    def test_p_gif_in_path(self):
        s, r = score_candidate(
            {"url": "https://cdn.example.com/p.gif"})
        assert s >= 0.85
        assert "pixel_path" in r

    def test_tracker_substring_in_filename_does_not_fire(self):
        # A real filename that happens to contain the substring
        # "track" but isn't a pixel route. E.g. "soundtrack.mp4".
        # The rule fires on the path token "/track" only if it appears
        # as a substring of the path. "/audio/soundtrack.mp4" does NOT
        # contain "/track" — the slash-prefix on the tokens enforces a
        # path-boundary match. This is the desired behavior.
        s, r = score_candidate(
            {"url": "https://cdn.example.com/audio/soundtrack.mp4"})
        assert "pixel_path" not in r
        assert s == 0.0

    def test_no_pixel_token_in_path(self):
        s, r = score_candidate(
            {"url": "https://cdn.example.com/video/scene1.mp4"})
        assert "pixel_path" not in r
        assert s == 0.0


# ────────────────────────────────────────────────────────────────────
# TestEmptyPathOnlyQs: cache-buster pixel shape (pre-fetch)
# ────────────────────────────────────────────────────────────────────

class TestEmptyPathOnlyQs:

    def test_cache_buster_v_param(self):
        s, r = score_candidate(
            {"url": "https://t.example.com/?v=abc123"})
        assert s >= 0.70
        assert "empty_path_only_qs" in r

    def test_utm_param_only(self):
        s, r = score_candidate(
            {"url": "https://t.example.com/?utm_source=foo&utm_medium=bar"})
        assert s >= 0.70
        assert "empty_path_only_qs" in r

    def test_cb_param(self):
        s, r = score_candidate(
            {"url": "https://t.example.com/?cb=1234567890"})
        assert s >= 0.70
        assert "empty_path_only_qs" in r

    def test_legitimate_id_lookup_does_not_fire(self):
        # ?id=abc on the root path is a legit "look up this object"
        # API shape. Should NOT fire just because path is "/".
        s, r = score_candidate(
            {"url": "https://api.example.com/?id=abc123"})
        assert "empty_path_only_qs" not in r

    def test_real_path_does_not_fire_even_with_cache_buster(self):
        # /video/scene.mp4?v=abc is fine — the v= is just a CDN
        # cache-buster for a real asset.
        s, r = score_candidate(
            {"url": "https://cdn.example.com/video/scene.mp4?v=abc"})
        assert "empty_path_only_qs" not in r

    def test_empty_path_no_query_does_not_fire(self):
        # Bare root URL with no query — not a pixel pattern.
        s, r = score_candidate({"url": "https://t.example.com/"})
        assert "empty_path_only_qs" not in r


# ────────────────────────────────────────────────────────────────────
# TestMediaButHtml: post-fetch Content-Type mismatch
# ────────────────────────────────────────────────────────────────────

class TestMediaButHtml:

    def test_mp4_returns_html(self):
        s, r = score_candidate(
            {"url": "https://cdn.example.com/video.mp4"},
            probe={
                "status": 200,
                "headers": {"Content-Type": "text/html"},
            },
        )
        assert s >= 0.90
        assert "media_but_html" in r

    def test_webm_returns_xhtml(self):
        s, r = score_candidate(
            {"url": "https://cdn.example.com/clip.webm"},
            probe={
                "status": 200,
                "headers": {"Content-Type": "application/xhtml+xml"},
            },
        )
        assert s >= 0.90
        assert "media_but_html" in r

    def test_html_with_charset_still_fires(self):
        s, r = score_candidate(
            {"url": "https://cdn.example.com/video.mp4"},
            probe={
                "status": 200,
                "headers": {"Content-Type": "text/html; charset=utf-8"},
            },
        )
        assert s >= 0.90
        assert "media_but_html" in r

    def test_m3u8_with_text_html_does_NOT_fire(self):
        # Manifest extensions are inherently text — a server returning
        # text/* for them is unsurprising and not a trap signal.
        s, r = score_candidate(
            {"url": "https://cdn.example.com/master.m3u8"},
            probe={
                "status": 200,
                "headers": {"Content-Type": "text/html"},
            },
        )
        assert "media_but_html" not in r

    def test_mpd_with_text_html_does_NOT_fire(self):
        # Same for DASH manifests.
        s, r = score_candidate(
            {"url": "https://cdn.example.com/manifest.mpd"},
            probe={
                "status": 200,
                "headers": {"Content-Type": "text/html"},
            },
        )
        assert "media_but_html" not in r

    def test_mp4_with_video_content_type_does_not_fire(self):
        s, r = score_candidate(
            {"url": "https://cdn.example.com/video.mp4"},
            probe={
                "status": 200,
                "headers": {"Content-Type": "video/mp4"},
            },
        )
        assert "media_but_html" not in r
        assert s == 0.0


# ────────────────────────────────────────────────────────────────────
# TestTinyMediaBody: post-fetch Content-Length too small
# ────────────────────────────────────────────────────────────────────

class TestTinyMediaBody:

    def test_mp4_under_1kb(self):
        s, r = score_candidate(
            {"url": "https://cdn.example.com/video.mp4"},
            probe={
                "status": 200,
                "headers": {"Content-Length": "200"},
            },
        )
        assert s >= 0.80
        assert "tiny_media_body" in r

    def test_mp4_exactly_at_threshold_does_not_fire(self):
        # 1024 bytes is the boundary — strict "<" check.
        s, r = score_candidate(
            {"url": "https://cdn.example.com/video.mp4"},
            probe={
                "status": 200,
                "headers": {"Content-Length": str(TINY_BODY_THRESHOLD_BYTES)},
            },
        )
        assert "tiny_media_body" not in r

    def test_mp4_above_threshold_does_not_fire(self):
        s, r = score_candidate(
            {"url": "https://cdn.example.com/video.mp4"},
            probe={
                "status": 200,
                "headers": {"Content-Length": "5000000"},
            },
        )
        assert "tiny_media_body" not in r

    def test_zero_length_does_not_fire(self):
        # 0-length is a different signal (likely 304 or HEAD-only).
        # We require > 0 to fire — distinguishes empty from tiny.
        s, r = score_candidate(
            {"url": "https://cdn.example.com/video.mp4"},
            probe={
                "status": 200,
                "headers": {"Content-Length": "0"},
            },
        )
        assert "tiny_media_body" not in r

    def test_m3u8_at_200_bytes_does_NOT_fire(self):
        # Real .m3u8 master playlists are ~200 bytes. Must not fire.
        s, r = score_candidate(
            {"url": "https://cdn.example.com/master.m3u8"},
            probe={
                "status": 200,
                "headers": {"Content-Length": "200"},
            },
        )
        assert "tiny_media_body" not in r

    def test_mpd_at_500_bytes_does_NOT_fire(self):
        s, r = score_candidate(
            {"url": "https://cdn.example.com/manifest.mpd"},
            probe={
                "status": 200,
                "headers": {"Content-Length": "500"},
            },
        )
        assert "tiny_media_body" not in r

    def test_malformed_content_length_does_not_crash(self):
        s, r = score_candidate(
            {"url": "https://cdn.example.com/video.mp4"},
            probe={
                "status": 200,
                "headers": {"Content-Length": "not-a-number"},
            },
        )
        # Should not crash; rule simply doesn't fire.
        assert "tiny_media_body" not in r


# ────────────────────────────────────────────────────────────────────
# TestCookieOnMedia: post-fetch Set-Cookie on media URL
# ────────────────────────────────────────────────────────────────────

class TestCookieOnMedia:

    def test_mp4_sets_cookie(self):
        s, r = score_candidate(
            {"url": "https://cdn.example.com/video.mp4"},
            probe={
                "status": 200,
                "headers": {
                    "Content-Type": "video/mp4",
                    "Set-Cookie": "session=abc123; HttpOnly",
                },
            },
        )
        assert "cookie_on_media" in r
        # Weak signal — verify the score is 0.40, not higher.
        # (If only this rule fires.)
        assert s == pytest.approx(0.40, abs=0.01)

    def test_non_media_url_does_not_fire(self):
        s, r = score_candidate(
            {"url": "https://api.example.com/auth/login"},
            probe={
                "status": 200,
                "headers": {"Set-Cookie": "session=abc"},
            },
        )
        assert "cookie_on_media" not in r

    def test_no_cookie_header_does_not_fire(self):
        s, r = score_candidate(
            {"url": "https://cdn.example.com/video.mp4"},
            probe={
                "status": 200,
                "headers": {"Content-Type": "video/mp4"},
            },
        )
        assert "cookie_on_media" not in r
        assert s == 0.0

    def test_empty_cookie_header_does_not_fire(self):
        s, r = score_candidate(
            {"url": "https://cdn.example.com/video.mp4"},
            probe={
                "status": 200,
                "headers": {"Set-Cookie": ""},
            },
        )
        assert "cookie_on_media" not in r

    def test_cookie_on_m3u8_fires(self):
        # Manifest extension IS still a media extension by our list.
        # A real CDN setting cookies on .m3u8 is unusual.
        s, r = score_candidate(
            {"url": "https://cdn.example.com/master.m3u8"},
            probe={
                "status": 200,
                "headers": {
                    "Content-Type": "application/vnd.apple.mpegurl",
                    "Set-Cookie": "session=xyz",
                },
            },
        )
        assert "cookie_on_media" in r

    def test_case_insensitive_set_cookie(self):
        s, r = score_candidate(
            {"url": "https://cdn.example.com/video.mp4"},
            probe={
                "status": 200,
                "headers": {"set-cookie": "x=1"},  # lowercase key
            },
        )
        assert "cookie_on_media" in r


# ────────────────────────────────────────────────────────────────────
# TestHostClassRedirect: media URL → tracker host redirect
# ────────────────────────────────────────────────────────────────────

class TestHostClassRedirect:

    def test_mp4_redirects_to_tracker(self):
        s, r = score_candidate(
            {"url": "https://cdn.example.com/video.mp4"},
            probe={
                "status": 302,
                "headers": {},
                "redirected_to": "https://ads.doubleclick.net/imp.gif",
            },
        )
        assert s >= 0.75
        assert "host_class_redirect" in r

    def test_same_host_redirect_does_not_fire(self):
        # Same host self-redirect (token refresh, etc.) is fine.
        s, r = score_candidate(
            {"url": "https://cdn.example.com/video.mp4"},
            probe={
                "status": 302,
                "headers": {},
                "redirected_to": "https://cdn.example.com/video.mp4?token=x",
            },
        )
        assert "host_class_redirect" not in r

    def test_redirect_to_non_tracker_does_not_fire(self):
        # Redirecting from one CDN to another (failover, geo-routing)
        # is normal.
        s, r = score_candidate(
            {"url": "https://cdn1.example.com/video.mp4"},
            probe={
                "status": 302,
                "headers": {},
                "redirected_to": "https://cdn2.example.com/video.mp4",
            },
        )
        assert "host_class_redirect" not in r

    def test_non_media_url_does_not_fire(self):
        # Redirect from a non-media URL doesn't trip this rule
        # regardless of where it lands.
        s, r = score_candidate(
            {"url": "https://example.com/page"},
            probe={
                "status": 302,
                "headers": {},
                "redirected_to": "https://ads.doubleclick.net/imp.gif",
            },
        )
        assert "host_class_redirect" not in r

    def test_malformed_redirect_url_does_not_crash(self):
        s, r = score_candidate(
            {"url": "https://cdn.example.com/video.mp4"},
            probe={
                "status": 302,
                "headers": {},
                "redirected_to": "://not-a-url",
            },
        )
        # Should not crash; rule simply doesn't fire.
        assert "host_class_redirect" not in r

    def test_no_redirect_does_not_fire(self):
        s, r = score_candidate(
            {"url": "https://cdn.example.com/video.mp4"},
            probe={
                "status": 200,
                "headers": {"Content-Type": "video/mp4"},
            },
        )
        assert "host_class_redirect" not in r


# ────────────────────────────────────────────────────────────────────
# TestScoreCombination: max not sum
# ────────────────────────────────────────────────────────────────────

class TestScoreCombination:

    def test_multiple_rules_max_not_sum(self):
        # Tracker host + pixel path + tiny body — score must NOT
        # exceed 1.0. With max semantics, we get 0.95 (the strongest).
        s, r = score_candidate(
            {"url": "https://ads.doubleclick.net/pixel/imp.gif"},
            probe={
                "status": 200,
                "headers": {"Content-Length": "200"},
            },
        )
        assert s <= 1.0
        assert s >= 0.95  # tracker_host is the strongest
        assert "tracker_host" in r
        assert "pixel_path" in r

    def test_all_rules_fire_score_still_bounded(self):
        # Stress: every rule fires.
        s, r = score_candidate(
            {"url": "https://ads.doubleclick.net/pixel/imp.mp4"},
            probe={
                "status": 302,
                "headers": {
                    "Content-Type": "text/html",
                    "Content-Length": "100",
                    "Set-Cookie": "x=1",
                },
                "redirected_to": "https://taboola.com/pixel",
            },
        )
        assert s <= 1.0
        assert s >= 0.95
        # Reason should list multiple rules
        assert r.count(",") >= 2


# ────────────────────────────────────────────────────────────────────
# TestClassifyScore: threshold zones
# ────────────────────────────────────────────────────────────────────

class TestClassifyScore:

    def test_high_score_drops(self):
        assert classify_score(0.9) == "drop"

    def test_exact_drop_threshold_drops(self):
        assert classify_score(DEFAULT_DROP_THRESHOLD) == "drop"

    def test_just_below_drop_downscores(self):
        assert classify_score(DEFAULT_DROP_THRESHOLD - 0.01) == "downscore"

    def test_exact_downscore_threshold_downscores(self):
        assert classify_score(DEFAULT_DOWNSCORE_THRESHOLD) == "downscore"

    def test_just_below_downscore_keeps(self):
        assert classify_score(DEFAULT_DOWNSCORE_THRESHOLD - 0.01) == "keep"

    def test_zero_keeps(self):
        assert classify_score(0.0) == "keep"

    def test_custom_thresholds(self):
        # Aggressive: drop everything at or above 0.4.
        assert classify_score(0.45, drop_threshold=0.4,
                              downscore_threshold=0.2) == "drop"
        assert classify_score(0.3, drop_threshold=0.4,
                              downscore_threshold=0.2) == "downscore"
        assert classify_score(0.1, drop_threshold=0.4,
                              downscore_threshold=0.2) == "keep"

    def test_equal_thresholds_no_downscore_zone(self):
        # Operator sets both to 0.7 — pure drop-or-keep, no
        # downscore zone.
        assert classify_score(0.8, drop_threshold=0.7,
                              downscore_threshold=0.7) == "drop"
        assert classify_score(0.7, drop_threshold=0.7,
                              downscore_threshold=0.7) == "drop"
        assert classify_score(0.69, drop_threshold=0.7,
                              downscore_threshold=0.7) == "keep"


# ────────────────────────────────────────────────────────────────────
# TestMalformedInputs: robustness against junk input
# ────────────────────────────────────────────────────────────────────

class TestMalformedInputs:

    def test_non_dict_candidate(self):
        s, r = score_candidate("not a dict")  # type: ignore[arg-type]
        assert s == 0.0
        assert r == ""

    def test_none_candidate(self):
        s, r = score_candidate(None)  # type: ignore[arg-type]
        assert s == 0.0
        assert r == ""

    def test_empty_candidate(self):
        s, r = score_candidate({})
        assert s == 0.0
        assert r == ""

    def test_url_is_none(self):
        s, r = score_candidate({"url": None})
        assert s == 0.0
        assert r == ""

    def test_url_is_int(self):
        s, r = score_candidate({"url": 42})
        assert s == 0.0
        assert r == ""

    def test_url_is_empty_string(self):
        s, r = score_candidate({"url": ""})
        assert s == 0.0
        assert r == ""

    def test_url_is_garbage(self):
        # urlparse handles this; result should be no rules fire
        # because hostname is empty.
        s, r = score_candidate({"url": "not://a-real-url"})
        # No tracker host match, no pixel path, no empty-path
        # pattern. Score should be 0.
        assert s == 0.0

    def test_probe_is_not_dict(self):
        # probe="garbage" should be silently ignored, pre-fetch
        # rules still fire.
        s, r = score_candidate(
            {"url": "https://doubleclick.net/foo"},
            probe="not a dict",  # type: ignore[arg-type]
        )
        assert s >= 0.95
        assert "tracker_host" in r

    def test_probe_headers_not_dict(self):
        # Probe present but headers is a list — should not crash;
        # post-fetch rules silently no-op.
        s, r = score_candidate(
            {"url": "https://cdn.example.com/video.mp4"},
            probe={"headers": ["not", "a", "dict"], "status": 200},
        )
        # No post-fetch rule fires; clean URL → 0.0
        assert s == 0.0

    def test_probe_missing_headers(self):
        s, r = score_candidate(
            {"url": "https://cdn.example.com/video.mp4"},
            probe={"status": 200},
        )
        # No headers → no post-fetch rules fire
        assert s == 0.0


# ────────────────────────────────────────────────────────────────────
# Module-level invariants
# ────────────────────────────────────────────────────────────────────

class TestModuleInvariants:

    def test_tracker_hosts_are_all_lowercase(self):
        # KNOWN_TRACKER_HOSTS must be lowercase or the case-
        # insensitive match logic breaks.
        for h in KNOWN_TRACKER_HOSTS:
            assert h == h.lower(), f"tracker host {h!r} not lowercase"

    def test_pixel_path_tokens_all_start_with_slash(self):
        # Path tokens must be path-rooted to avoid matching inside
        # filenames or query strings (we only search path).
        for t in PIXEL_PATH_TOKENS:
            assert t.startswith("/"), \
                f"pixel token {t!r} should start with /"

    def test_manifest_exts_are_subset_of_media_exts(self):
        # MANIFEST_EXTENSIONS must be a subset of MEDIA_EXTENSIONS,
        # because the exemption logic in score_candidate filters
        # MANIFEST out of MEDIA — if a manifest ext weren't in media,
        # the filter would do nothing.
        for ext in MANIFEST_EXTENSIONS:
            assert ext in MEDIA_EXTENSIONS, \
                f"manifest ext {ext!r} not in MEDIA_EXTENSIONS"

    def test_default_thresholds_match_spec(self):
        # P5-2 spec B3 specifies drop=0.8, downscore=0.5
        assert DEFAULT_DROP_THRESHOLD == 0.8
        assert DEFAULT_DOWNSCORE_THRESHOLD == 0.5

    def test_threshold_byte_count_is_positive(self):
        assert TINY_BODY_THRESHOLD_BYTES > 0


# ════════════════════════════════════════════════════════════════════
# Integration tests: provider_resolve wiring (B3)
# ════════════════════════════════════════════════════════════════════
#
# These exercise the actual env-var-controlled drop/downscore wiring
# in ``provider_resolve.resolve_provider_embed``. We use a synthetic
# resolver injected via the _RESOLVERS dict so we can control the
# candidate list directly without depending on Vimeo/Wistia fixtures.


# Synthesize a candidate dict matching the resolver output shape.
def _make_candidate(url: str, score: int = 80,
                    source_type: str = "test_resolved"):
    return {
        "url": url,
        "source_type": source_type,
        "score": score,
        "resolution": None,
        "codec": None,
        "fps": None,
        "size_bytes": None,
        "found_in": "test",
        "resolved_from": "test",
        "provider_resolved": True,
        "reasons": ["test fixture"],
        "warnings": [],
        "requires_click": False,
    }


@pytest.fixture
def fake_resolver():
    """Register a synthetic resolver in provider_resolve._RESOLVERS
    for the test, then clean up after."""
    from bulk_downloader import provider_resolve as pr

    candidates_to_return: list = []

    def _resolver(ids, *, embed=None, http_get=None):
        # Returns a copy of whatever the test set up
        return list(candidates_to_return), None

    original = pr._RESOLVERS.copy()
    pr._RESOLVERS["test_fake"] = _resolver
    try:
        yield candidates_to_return  # tests append to this list
    finally:
        pr._RESOLVERS.clear()
        pr._RESOLVERS.update(original)


@pytest.fixture
def fake_embed():
    """A synthetic provider embed dict matching extract_provider_embeds
    output shape."""
    return {
        "provider": "test_fake",
        "source_type": "test_fake_embed",
        "ids": {"clip_id": "fake-abc"},
        "url": "https://test-fake.example.com/video/fake-abc",
        "found_in": "<iframe>",
    }


class TestHoneypotIntegrationDefaultOff:
    """When env var is unset, the filter must be a no-op."""

    def test_default_off_no_behavior_change(
            self, fake_resolver, fake_embed, monkeypatch):
        monkeypatch.delenv("BD_HONEYPOT_SCORE_THRESHOLD", raising=False)
        from bulk_downloader.provider_resolve import resolve_provider_embed

        # A candidate that WOULD score high (tracker host)
        fake_resolver.append(
            _make_candidate("https://ads.doubleclick.net/imp.gif"))
        # Plus a clean one
        fake_resolver.append(
            _make_candidate("https://cdn.example.com/video.mp4"))

        cands, err = resolve_provider_embed(fake_embed)
        assert err is None
        # Both kept; no honeypot fields stamped
        assert len(cands) == 2
        for c in cands:
            assert "_honeypot_score" not in c
            assert "_honeypot_reason" not in c

    def test_empty_env_var_is_off(self, fake_resolver, fake_embed, monkeypatch):
        monkeypatch.setenv("BD_HONEYPOT_SCORE_THRESHOLD", "")
        from bulk_downloader.provider_resolve import resolve_provider_embed
        fake_resolver.append(
            _make_candidate("https://ads.doubleclick.net/imp.gif"))
        cands, err = resolve_provider_embed(fake_embed)
        assert len(cands) == 1  # not dropped
        assert err is None

    def test_invalid_env_var_is_off(self, fake_resolver, fake_embed, monkeypatch):
        monkeypatch.setenv("BD_HONEYPOT_SCORE_THRESHOLD", "not-a-number")
        from bulk_downloader.provider_resolve import resolve_provider_embed
        fake_resolver.append(
            _make_candidate("https://ads.doubleclick.net/imp.gif"))
        cands, err = resolve_provider_embed(fake_embed)
        assert len(cands) == 1  # not dropped
        assert err is None

    def test_zero_threshold_is_off(self, fake_resolver, fake_embed, monkeypatch):
        # Defensive: a misconfiguration that would drop everything is
        # treated as off, not as "drop everything."
        monkeypatch.setenv("BD_HONEYPOT_SCORE_THRESHOLD", "0")
        from bulk_downloader.provider_resolve import resolve_provider_embed
        fake_resolver.append(
            _make_candidate("https://cdn.example.com/video.mp4"))
        cands, err = resolve_provider_embed(fake_embed)
        assert len(cands) == 1
        assert err is None

    def test_above_one_threshold_is_off(self, fake_resolver, fake_embed,
                                        monkeypatch):
        # Threshold > 1.0 is functionally "never drop" — treat as off.
        monkeypatch.setenv("BD_HONEYPOT_SCORE_THRESHOLD", "1.5")
        from bulk_downloader.provider_resolve import resolve_provider_embed
        fake_resolver.append(
            _make_candidate("https://ads.doubleclick.net/imp.gif"))
        cands, err = resolve_provider_embed(fake_embed)
        assert len(cands) == 1
        assert err is None


class TestHoneypotIntegrationDrop:
    """When env var is set, high-scoring candidates are dropped."""

    def test_tracker_host_dropped_at_default_threshold(
            self, fake_resolver, fake_embed, monkeypatch, capsys):
        monkeypatch.setenv("BD_HONEYPOT_SCORE_THRESHOLD", "0.8")
        from bulk_downloader.provider_resolve import resolve_provider_embed

        fake_resolver.append(
            _make_candidate("https://ads.doubleclick.net/imp.gif"))
        fake_resolver.append(
            _make_candidate("https://cdn.example.com/video.mp4"))

        cands, err = resolve_provider_embed(fake_embed)
        # The tracker is dropped; the clean cdn survives
        assert len(cands) == 1
        assert cands[0]["url"] == "https://cdn.example.com/video.mp4"
        assert err is None
        # stderr should mention the drop
        captured = capsys.readouterr()
        assert "honeypot_drop" in captured.err
        assert "doubleclick.net" in captured.err

    def test_all_dropped_yields_error(
            self, fake_resolver, fake_embed, monkeypatch):
        monkeypatch.setenv("BD_HONEYPOT_SCORE_THRESHOLD", "0.8")
        from bulk_downloader.provider_resolve import resolve_provider_embed

        # Every candidate is a tracker
        fake_resolver.append(
            _make_candidate("https://doubleclick.net/imp.gif"))
        fake_resolver.append(
            _make_candidate("https://taboola.com/track.gif"))

        cands, err = resolve_provider_embed(fake_embed)
        assert cands == []
        assert err is not None
        assert "honeypot" in err.lower()

    def test_aggressive_threshold(
            self, fake_resolver, fake_embed, monkeypatch):
        # Set threshold low enough that empty_path_only_qs (0.70)
        # also drops.
        monkeypatch.setenv("BD_HONEYPOT_SCORE_THRESHOLD", "0.6")
        from bulk_downloader.provider_resolve import resolve_provider_embed

        fake_resolver.append(_make_candidate("https://t.example.com/?v=abc"))
        fake_resolver.append(
            _make_candidate("https://cdn.example.com/video.mp4"))

        cands, err = resolve_provider_embed(fake_embed)
        assert len(cands) == 1
        assert cands[0]["url"] == "https://cdn.example.com/video.mp4"


class TestHoneypotIntegrationDownscore:
    """The middle zone keeps the candidate but multiplies score by 0.5
    and appends a warning."""

    def test_downscore_zone_kept_with_warning(
            self, fake_resolver, fake_embed, monkeypatch):
        # Drop threshold 0.8 → empty_path_only_qs at 0.70 lands in
        # the downscore zone (>= 0.5 < 0.8).
        monkeypatch.setenv("BD_HONEYPOT_SCORE_THRESHOLD", "0.8")
        from bulk_downloader.provider_resolve import resolve_provider_embed

        fake_resolver.append(
            _make_candidate("https://t.example.com/?v=abc", score=80))
        cands, err = resolve_provider_embed(fake_embed)
        assert err is None
        assert len(cands) == 1
        c = cands[0]
        # Original score 80 → downscored to 40
        assert c["score"] == 40
        assert any("honeypot signal" in w for w in c["warnings"])
        # Honeypot fields stamped for downstream inspection
        assert "_honeypot_score" in c
        assert "_honeypot_reason" in c

    def test_clean_candidate_not_touched(
            self, fake_resolver, fake_embed, monkeypatch):
        monkeypatch.setenv("BD_HONEYPOT_SCORE_THRESHOLD", "0.8")
        from bulk_downloader.provider_resolve import resolve_provider_embed

        fake_resolver.append(
            _make_candidate("https://cdn.example.com/video.mp4", score=85))
        cands, err = resolve_provider_embed(fake_embed)
        assert len(cands) == 1
        c = cands[0]
        # Score unchanged, no warning, no honeypot fields stamped
        assert c["score"] == 85
        assert c["warnings"] == []
        assert "_honeypot_score" not in c


class TestHoneypotIntegrationCacheWrite:
    """A dropped candidate must NOT be persisted to the resolution
    cache."""

    def test_dropped_url_not_cached(
            self, fake_resolver, fake_embed, monkeypatch):
        monkeypatch.setenv("BD_HONEYPOT_SCORE_THRESHOLD", "0.8")
        from bulk_downloader.provider_resolve import resolve_provider_embed

        # Tracker URL + clean URL. Clean has higher score so would
        # be the cache write target anyway, but the test would still
        # catch a regression where we tried to cache the tracker.
        fake_resolver.append(
            _make_candidate("https://ads.doubleclick.net/imp.gif",
                            score=95))
        fake_resolver.append(
            _make_candidate("https://cdn.example.com/video.mp4",
                            score=80))

        cache_calls: list = []

        def cache_write(provider, embed_id, url, ts):
            cache_calls.append({"provider": provider,
                                "embed_id": embed_id,
                                "url": url, "ts": ts})

        cands, err = resolve_provider_embed(
            fake_embed, cache_write=cache_write)
        assert err is None
        assert len(cands) == 1
        # Cache write happened — but with the CLEAN url, not the
        # dropped tracker URL.
        assert len(cache_calls) == 1
        assert cache_calls[0]["url"] == "https://cdn.example.com/video.mp4"

    def test_all_dropped_no_cache_write(
            self, fake_resolver, fake_embed, monkeypatch):
        monkeypatch.setenv("BD_HONEYPOT_SCORE_THRESHOLD", "0.8")
        from bulk_downloader.provider_resolve import resolve_provider_embed

        fake_resolver.append(
            _make_candidate("https://doubleclick.net/imp.gif"))

        cache_calls: list = []
        def cache_write(*a):
            cache_calls.append(a)

        cands, err = resolve_provider_embed(
            fake_embed, cache_write=cache_write)
        assert cands == []
        assert err is not None
        assert cache_calls == []  # nothing cached


class TestHoneypotIntegrationFailSafe:
    """If the scorer somehow raises, we must keep the candidate
    (fail open), not drop it."""

    def test_scorer_exception_keeps_candidate(
            self, fake_resolver, fake_embed, monkeypatch):
        monkeypatch.setenv("BD_HONEYPOT_SCORE_THRESHOLD", "0.8")
        from bulk_downloader import provider_resolve as pr
        from bulk_downloader import honeypot_score as _hs

        fake_resolver.append(
            _make_candidate("https://cdn.example.com/video.mp4"))

        def boom(*a, **kw):
            raise RuntimeError("synthesized scorer crash")

        monkeypatch.setattr(_hs, "score_candidate", boom)
        cands, err = pr.resolve_provider_embed(fake_embed)
        # Candidate survives — fail-open
        assert len(cands) == 1
        assert err is None
