"""v3.43.65: tests for the 8K-first quality cascade changes.

Covers:
  1. Default quality_preference is the new 8K-first cascade
  2. Proportional ±tolerance: 8K gets ±216, 4K gets ±108, 1080 gets ±54
  3. New res_score patterns for Vixen-style URLs
  4. New res_score patterns for VIP4K / Bang.com / PornPros
  5. _353P preview tier scored below 240p
  6. 60fps tiebreaker boost
  7. NON_VIDEO_RE rejects v3.43.65 preview URL shapes
  8. 8K tier label in RESOLUTION_TIERS
"""
from __future__ import annotations

# [SAST 3:13pm 13 may] removed unused: import pytest

from bulk_downloader.detect import res_score  # [SAST 3:13pm 13 may] dropped: res_label
from bulk_downloader.heuristic_scoring import (
    RESOLUTION_TIERS, detect_resolution_tier,
)
from bulk_downloader.constants import NON_VIDEO_RE


# ─── Default cascade ────────────────────────────────────────────────


class TestDefaultCascade:
    def test_default_starts_with_4320(self):
        from bulk_downloader.app_kernel import DEFAULTS
        qpref = DEFAULTS["quality_preference"]
        first = qpref.split(",")[0].strip()
        assert first == "4320", \
            f"v3.43.65 default should start with 8K, got {first!r}"

    def test_default_covers_8k_through_720(self):
        from bulk_downloader.app_kernel import DEFAULTS
        qpref = DEFAULTS["quality_preference"]
        tiers = [int(t.strip()) for t in qpref.split(",") if t.strip().isdigit()]
        # Must include the major tiers
        assert 4320 in tiers
        assert 2160 in tiers
        assert 1080 in tiers
        assert 720 in tiers


# ─── Proportional tolerance ─────────────────────────────────────────


class TestProportionalTolerance:
    """Verify _apply_quality_preference uses target * 0.05 tolerance."""

    def _make_runner(self):
        from bulk_downloader.runner import SiteRunner
        r = SiteRunner.__new__(SiteRunner)
        return r

    def test_8k_target_matches_4080_within_tolerance(self):
        """target=4320, candidate=4080, tolerance = max(50, 4320*0.05=216).
        Difference is 240, so should NOT match — too far even at ±216."""
        r = self._make_runner()
        best = {"score": 0, "locator": None, "_all_candidates": [
            {"score": 4080, "locator": object(), "size": 0, "text": "..."}
        ]}
        chosen = r._apply_quality_preference(best, "4320")
        # 4080 is 240 below 4320 — outside ±216, should fall through
        # to the original best
        assert chosen is best or chosen.get("score", 0) == 0

    def test_8k_target_matches_4500_within_tolerance(self):
        """target=4320, candidate=4500. Difference 180 < 216. MATCH."""
        r = self._make_runner()
        loc = object()
        best = {"score": 0, "locator": None, "_all_candidates": [
            {"score": 4500, "locator": loc, "size": 0, "text": "..."}
        ]}
        chosen = r._apply_quality_preference(best, "4320")
        assert chosen.get("score") == 4500

    def test_4k_target_matches_4096_dci_within_tolerance(self):
        """target=2160, candidate=4096 (DCI 4K width-stretched —
        sometimes shows as 4096-pixel height). Should fail because
        4096 is way above 2160 ± 108."""
        r = self._make_runner()
        loc = object()
        best = {"score": 0, "locator": None, "_all_candidates": [
            {"score": 4096, "locator": loc, "size": 0, "text": "..."}
        ]}
        chosen = r._apply_quality_preference(best, "2160")
        # 4096 - 2160 = 1936, way outside ±108
        assert chosen is best or chosen.get("score", 0) == 0

    def test_1080_legacy_tolerance_stays_50(self):
        """target=1080, candidate=1088 (off-by-mod-8). 1088 - 1080 = 8,
        well within max(50, 54) = 54. Should match."""
        r = self._make_runner()
        loc = object()
        best = {"score": 0, "locator": None, "_all_candidates": [
            {"score": 1088, "locator": loc, "size": 0, "text": "..."}
        ]}
        chosen = r._apply_quality_preference(best, "1080")
        assert chosen.get("score") == 1088

    def test_480_uses_50_floor_not_proportional(self):
        """target=480, candidate=540. Proportional would give ±24, but
        the floor is 50. Difference 60 > 50, should NOT match."""
        r = self._make_runner()
        loc = object()
        best = {"score": 0, "locator": None, "_all_candidates": [
            {"score": 540, "locator": loc, "size": 0, "text": "..."}
        ]}
        chosen = r._apply_quality_preference(best, "480")
        # 540 - 480 = 60, just outside floor of 50
        assert chosen is best or chosen.get("score", 0) == 0


# ─── New res_score patterns ─────────────────────────────────────────


class TestVixenStylePatterns:
    def test_mp4_2160_path_segment(self):
        url = "https://cdn.vixen.com/video/mp4_2160/106491/foo.mp4"
        assert res_score(url) == 2160

    def test_mp4_4320_path_segment_is_8k(self):
        url = "https://cdn.vixen.com/video/mp4_4320/123/bar.mp4"
        assert res_score(url) == 4320

    def test_vixen_caps_tier_suffix(self):
        url = "https://cdn.vixen.com/video/mp4_2160/.../VIXEN_106491_2160P.mp4"
        # Should pick up 2160 from either pattern; max wins
        assert res_score(url) == 2160

    def test_vixen_480p_caps_suffix(self):
        url = "https://cdn.vixen.com/video/.../VIXEN_106491_480P.mp4"
        # _480P pattern recognized
        assert res_score(url) == 480


class TestVip4kBangPatterns:
    def test_vip4k_path_only_tier(self):
        url = "https://video.vip4k.com/secure/.../1080p.mp4?token=abc"
        assert res_score(url) == 1080

    def test_bang_dot_separated_tier(self):
        url = "https://vz56.bngcdn.com/.../requests-natalia-starr-scene-1.1080p.mp4"
        assert res_score(url) == 1080


class TestPornProsPattern:
    def test_stream_mp4_1080(self):
        url = "https://cdn-videos.r1.cdn.pornpros.com/.../stream_mp4_1080.mp4?val"
        assert res_score(url) == 1080


class TestPreviewSuppression:
    def test_353p_recognized_as_preview_tier(self):
        """Vixen's _353P should map to a score that LOSES to real
        variants. The RESOLUTION_TIERS table assigns it tier=2 which
        is below every real tier."""
        tier, label = detect_resolution_tier("VIXEN_106491_353P.mp4")
        # tier should be 2 (preview) — below 240p (tier 3)
        assert tier == 2
        assert "preview" in label.lower()

    def test_real_480_beats_353_preview(self):
        """Make sure regular 480 still gets tier 10."""
        tier_480, _ = detect_resolution_tier("VIXEN_106491_480P.mp4")
        tier_353, _ = detect_resolution_tier("VIXEN_106491_353P.mp4")
        assert tier_480 > tier_353

    def test_4k_beats_everything(self):
        tier_4k, _ = detect_resolution_tier("VIXEN_106491_2160P.mp4")
        tier_1080, _ = detect_resolution_tier("VIXEN_106491_1080P.mp4")
        assert tier_4k > tier_1080


class Test60FpsTiebreaker:
    def test_60fps_boosts_score(self):
        """A 1080p_60FPS variant scores higher than a plain 1080p so
        the tiebreaker picks the 60fps encode."""
        s_60 = res_score("1280x720_60FPS.mp4")
        s_plain = res_score("1280x720.mp4")
        # 60fps gets +5 boost on the digit-extracted score
        assert s_60 > s_plain
        # But still in the 720p ballpark
        assert 720 <= s_60 <= 735

    def test_60fps_modifier_only_with_real_resolution(self):
        """Bare '60fps' with no resolution shouldn't pretend to be HD."""
        s = res_score("just some text 60fps blah")
        # No digit found → falls to label patterns → -1 (no label match)
        assert s == -1


# ─── NON_VIDEO_RE additions ─────────────────────────────────────────


class TestNonVideoRePatterns:
    def test_rejects_gamma_trailer_sm(self):
        assert NON_VIDEO_RE.search("trailers-fame.gammacdn.com/.../tr_127673_sm.mp4")

    def test_rejects_gamma_trailer_big(self):
        assert NON_VIDEO_RE.search("trailers-fame.gammacdn.com/.../tr_127673_big.mp4")

    def test_rejects_newsensations_mini_bite(self):
        assert NON_VIDEO_RE.search("mini-bite_S.mp4")
        assert NON_VIDEO_RE.search("MiniBite_S.mp4")

    def test_rejects_reptyle_bio_small(self):
        assert NON_VIDEO_RE.search("/brt/tour/pics/.../bio_small.mp4")

    def test_rejects_vixen_353p_tier(self):
        assert NON_VIDEO_RE.search("VIXEN_106491_353P.mp4")

    def test_rejects_naughty_america_teasers(self):
        assert NON_VIDEO_RE.search("ofmsagewill_desktop10sec.mp4")
        assert NON_VIDEO_RE.search("ofmsagewill_mobile10sec.mp4")

    def test_rejects_vixen_previewvideos_path(self):
        assert NON_VIDEO_RE.search("cdn.blacked.com/previewvideos/106488/...")

    def test_accepts_real_video(self):
        """A real 1080p video URL should NOT match the reject pattern."""
        assert not NON_VIDEO_RE.search(
            "https://cdn.vixen.com/video/mp4_1080/.../VIXEN_106491_1080P.mp4")


# ─── 8K tier still present in RESOLUTION_TIERS ──────────────────────


class Test8KTierIntact:
    def test_8k_label_exists(self):
        labels = [label for _, _, label in RESOLUTION_TIERS]
        assert "8K" in labels

    def test_8k_has_highest_tier_value(self):
        max_tier = max(t for _, t, _ in RESOLUTION_TIERS)
        # 8K is tier 80
        eight_k = next(t for _, t, lab in RESOLUTION_TIERS if lab == "8K")
        assert eight_k == max_tier

    def test_8k_path_segment_pattern_present(self):
        """v3.43.65 added mp4_4320 to the 8K pattern."""
        eight_k_pat = next(p for p, _, lab in RESOLUTION_TIERS if lab == "8K")
        assert eight_k_pat.search("mp4_4320")

    def test_4k_uhd_lowercase_label_present(self):
        """v3.43.65 added _uhd lowercase label."""
        four_k_pat = next(p for p, _, lab in RESOLUTION_TIERS if lab == "4K")
        assert four_k_pat.search("video_uhd_clip.mp4")


# ─── Integration: full cascade picks the highest ────────────────────


class TestFullCascade:
    def test_cascade_picks_4k_when_4k_4080_and_1080_present(self):
        """Simulate: candidates at scores 2160, 4080, 1080. Cascade
        starts at 4320 — 4080 within tol = 216? max(50, 216)=216, but
        4320-4080 = 240, so NO. Then 3160 — way too far. Then 2880,
        2160. 2160 should pick the score=2160 candidate."""
        from bulk_downloader.runner import SiteRunner
        r = SiteRunner.__new__(SiteRunner)
        c_4080 = {"score": 4080, "locator": object(), "size": 0, "text": "4K-ish"}
        c_2160 = {"score": 2160, "locator": object(), "size": 0, "text": "4K"}
        c_1080 = {"score": 1080, "locator": object(), "size": 0, "text": "1080p"}
        best = {"score": 0, "locator": None,
                "_all_candidates": [c_4080, c_2160, c_1080]}
        chosen = r._apply_quality_preference(
            best, "4320,3160,2880,2160,1440,1080")
        # 2160 step picks score=2160 (4080 is outside 2160+108 but
        # included if we widen — let me recompute. tol=max(50,108)=108,
        # 4080-2160=1920, 2160-2160=0. So 2160 matches at distance 0.
        assert chosen.get("score") == 2160

    def test_cascade_falls_to_best_when_no_match(self):
        """If no preference matches, the original best is returned."""
        from bulk_downloader.runner import SiteRunner
        r = SiteRunner.__new__(SiteRunner)
        only_540 = {"score": 540, "locator": object(), "size": 0, "text": "540p"}
        best = {"score": 540, "locator": object(),
                "_all_candidates": [only_540]}
        chosen = r._apply_quality_preference(best, "4320,2160,1080")
        # None of 4320/2160/1080 match → return original best
        assert chosen is best

    def test_best_keyword_picks_highest_score(self):
        from bulk_downloader.runner import SiteRunner
        r = SiteRunner.__new__(SiteRunner)
        c_4080 = {"score": 4080, "locator": object(), "size": 0, "text": "x"}
        c_1080 = {"score": 1080, "locator": object(), "size": 0, "text": "y"}
        best = {"score": 0, "locator": None,
                "_all_candidates": [c_4080, c_1080]}
        chosen = r._apply_quality_preference(best, "best")
        assert chosen.get("score") == 4080
