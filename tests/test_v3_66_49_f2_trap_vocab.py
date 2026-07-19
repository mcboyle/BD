"""v3.66.49 — F2 (R-P5-2 vocabulary extension): challenge-control +
trap-URL terms.

Covers the F2 delta on top of what v3.66.27 already shipped:

  TestTrapUrlTerm
      New pre-fetch rule ``trap_url_term`` (honeypot_score): a media
      candidate whose path routes through a redirect/interstitial
      term (/click, /clk, /go, /out, /redirect, /tracking, ...).
      Scored at 0.70 — downscores but never drops on its own.

  TestBrandTrapTerm
      New pre-fetch rule ``brand_trap_term`` (honeypot_score): brand-
      workflow terms (subscribe/checkout/verify/...). The F2 caveat is
      load-bearing here — these MUST be weak downscore signals, never
      drop, so a legitimate "Subscribe to get download" flow survives.
      Verified by score (0.55, in the downscore band) AND by
      classify_score returning "downscore", never "drop".

  TestTrapTermsBelowDrop
      Neither new rule alone reaches the drop threshold. A stronger
      rule firing alongside still wins via MAX.

  TestDeepDetectVocab
      deep_detect.py: challenge vendors (DataDome/PerimeterX/Kasada/
      Akamai/Imperva/Incapsula/Cloudflare) are present in
      BOT_DEFENSE_MARKERS, and the redirect infixes /clk? and
      /tracking? were added to SUSPICIOUS_URL_PATTERNS.
"""
from __future__ import annotations

import pytest

from bulk_downloader.honeypot_score import (
    classify_score,
    score_candidate,
    TRAP_URL_TERMS,
    BRAND_TRAP_TERMS,
    DEFAULT_DROP_THRESHOLD,
    DEFAULT_DOWNSCORE_THRESHOLD,
)
from bulk_downloader import deep_detect as dd


# ────────────────────────────────────────────────────────────────────
# TestTrapUrlTerm: redirect/interstitial path terms (pre-fetch)
# ────────────────────────────────────────────────────────────────────

class TestTrapUrlTerm:

    def test_click_path_fires(self):
        s, r = score_candidate(
            {"url": "https://x.example.com/click/video.mp4"})
        assert "trap_url_term" in r
        assert s >= 0.70

    def test_clk_path_fires(self):
        s, r = score_candidate(
            {"url": "https://x.example.com/clk/abc"})
        assert "trap_url_term" in r

    def test_redirect_path_fires(self):
        s, r = score_candidate(
            {"url": "https://x.example.com/redirect?u=video.mp4"})
        assert "trap_url_term" in r

    def test_tracking_path_fires(self):
        s, r = score_candidate(
            {"url": "https://x.example.com/tracking/pixel"})
        assert "trap_url_term" in r

    def test_clean_path_does_not_fire(self):
        s, r = score_candidate(
            {"url": "https://cdn.example.com/media/video.mp4"})
        assert "trap_url_term" not in r

    def test_case_insensitive(self):
        s, r = score_candidate(
            {"url": "https://x.example.com/RedirEct/v.mp4"})
        assert "trap_url_term" in r

    def test_score_is_below_drop(self):
        # A lone trap_url_term must downscore, not drop.
        s, r = score_candidate(
            {"url": "https://x.example.com/go/asset.mp4"})
        assert s < DEFAULT_DROP_THRESHOLD
        assert classify_score(s) == "downscore"


# ────────────────────────────────────────────────────────────────────
# TestBrandTrapTerm: weak brand-workflow signal (pre-fetch)
# ────────────────────────────────────────────────────────────────────

class TestBrandTrapTerm:

    def test_subscribe_fires_weakly(self):
        s, r = score_candidate(
            {"url": "https://site.example.com/subscribe/download"})
        assert "brand_trap_term" in r
        assert s == pytest.approx(0.55)

    def test_checkout_fires(self):
        s, r = score_candidate(
            {"url": "https://site.example.com/checkout"})
        assert "brand_trap_term" in r

    def test_verify_fires(self):
        s, r = score_candidate(
            {"url": "https://site.example.com/verify/me"})
        assert "brand_trap_term" in r

    def test_brand_term_downscores_never_drops(self):
        # The load-bearing F2 caveat: a real membership/subscribe flow
        # must be KEPT (downscored), never dropped.
        s, r = score_candidate(
            {"url": "https://members.example.com/premium/subscribe"})
        assert classify_score(s) == "downscore"
        assert classify_score(s) != "drop"

    def test_clean_path_does_not_fire(self):
        s, r = score_candidate(
            {"url": "https://cdn.example.com/v/movie.mp4"})
        assert "brand_trap_term" not in r

    def test_score_in_downscore_band(self):
        s, r = score_candidate(
            {"url": "https://site.example.com/offer"})
        assert DEFAULT_DOWNSCORE_THRESHOLD <= s < DEFAULT_DROP_THRESHOLD


# ────────────────────────────────────────────────────────────────────
# TestTrapTermsBelowDrop: MAX-combination + no auto-drop
# ────────────────────────────────────────────────────────────────────

class TestTrapTermsBelowDrop:

    def test_trap_and_brand_together_still_below_drop(self):
        # Both new rules fire; MAX is the trap term (0.70), still < drop.
        s, r = score_candidate(
            {"url": "https://x.example.com/click/subscribe/v.mp4"})
        assert "trap_url_term" in r
        assert "brand_trap_term" in r
        assert s < DEFAULT_DROP_THRESHOLD

    def test_tracker_host_still_wins_over_trap_term(self):
        # A genuine drop-tier rule (tracker_host 0.95) co-firing with a
        # trap term resolves to drop via MAX.
        s, r = score_candidate(
            {"url": "https://ads.doubleclick.net/click/v.mp4"})
        assert "tracker_host" in r
        assert "trap_url_term" in r
        assert s >= 0.95
        assert classify_score(s) == "drop"


# ────────────────────────────────────────────────────────────────────
# TestDeepDetectVocab: challenge vendors + new redirect infixes
# ────────────────────────────────────────────────────────────────────

class TestDeepDetectVocab:

    def test_all_challenge_vendors_present(self):
        markers = " ".join(dd.BOT_DEFENSE_MARKERS).lower()
        for vendor in ("datadome", "perimeterx", "kasada",
                       "akamai", "imperva", "incapsula", "cloudflare"):
            assert vendor in markers, f"missing challenge vendor: {vendor}"

    def test_clk_infix_added(self):
        assert "/clk?" in dd.SUSPICIOUS_URL_PATTERNS

    def test_tracking_infix_added(self):
        assert "/tracking?" in dd.SUSPICIOUS_URL_PATTERNS

    def test_preexisting_infixes_intact(self):
        # Regression: the additions didn't drop existing terms.
        for infix in ("/go?", "/out?", "/redirect?", "/click?",
                      "/track?"):
            assert infix in dd.SUSPICIOUS_URL_PATTERNS


# ────────────────────────────────────────────────────────────────────
# TestVocabConstants: exported frozensets carry the spec'd terms
# ────────────────────────────────────────────────────────────────────

class TestVocabConstants:

    def test_trap_url_terms_content(self):
        for t in ("/click", "/clk", "/go", "/out", "/redirect",
                  "/tracking"):
            assert t in TRAP_URL_TERMS

    def test_brand_trap_terms_content(self):
        for t in ("subscribe", "checkout", "verify", "premium",
                  "offer"):
            assert t in BRAND_TRAP_TERMS

    def test_no_overlap_between_tiers(self):
        # A term shouldn't be in both tiers (would double-count).
        assert not (TRAP_URL_TERMS & BRAND_TRAP_TERMS)
