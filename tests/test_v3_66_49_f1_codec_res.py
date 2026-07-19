"""v3.66.49 — F1 (resolution vocab + codec-quality scoring).

The resolution half of F1 (8K/6K/5K vocab) already shipped in the
RESOLUTION_TIERS / _RES_LABEL_PATTERNS tables (INV-005); this file
confirms it stays present and adds coverage for the new codec-quality
tie-breaker.

  TestHighResVocab
      Confirms 8K/6K/5K are recognised and ranked above 4K in BOTH
      the heuristic_scoring tier table and detect.res_score — a guard
      so a future edit can't silently drop them.

  TestCodecScoreDetect
      detect.codec_score / codec_label: AV1 > HEVC > VP9 > H.264, and
      remux/source highest. Unknown / empty → 0.

  TestCodecTierHeuristic
      heuristic_scoring.detect_codec_tier ranking parity.

  TestCodecNeverOverridesResolution
      The load-bearing F1 caveat: a better codec at a LOWER resolution
      must never outrank a higher resolution. Verified via the full
      score_text_signals path.
"""
from __future__ import annotations

import pytest

from bulk_downloader import detect as dt
from bulk_downloader import heuristic_scoring as hs


# ────────────────────────────────────────────────────────────────────
# TestHighResVocab: 8K/6K/5K present and correctly ranked
# ────────────────────────────────────────────────────────────────────

class TestHighResVocab:

    def test_res_score_8k_label(self):
        assert dt.res_score("Download 8K") >= 4320
        assert dt.res_label(dt.res_score("8K")) == "8K"

    def test_res_score_6k_label(self):
        assert dt.res_score("6K version") >= 3160
        assert dt.res_label(dt.res_score("6K")) == "6K"

    def test_res_score_5k_label(self):
        assert dt.res_score("5K") >= 2880
        assert dt.res_label(dt.res_score("5K")) == "5K"

    def test_explicit_pixel_heights(self):
        assert dt.res_score("7680x4320") >= 4320
        assert dt.res_score("5120x2880") >= 2880
        assert dt.res_score("4320p") >= 4320

    def test_heuristic_tier_ordering(self):
        t8, _ = hs.detect_resolution_tier("8K")
        t6, _ = hs.detect_resolution_tier("6K")
        t5, _ = hs.detect_resolution_tier("5K")
        t4, _ = hs.detect_resolution_tier("4K")
        assert t8 > t6 > t5 > t4

    def test_8k_beats_4k_in_mixed_string(self):
        tier, label = hs.detect_resolution_tier("Download 4K or 8K UHD")
        assert label == "8K"


# ────────────────────────────────────────────────────────────────────
# TestCodecScoreDetect: detect.codec_score / codec_label
# ────────────────────────────────────────────────────────────────────

class TestCodecScoreDetect:

    def test_av1_highest_real_codec(self):
        assert dt.codec_score("AV1") == 4

    def test_codec_ranking(self):
        assert (dt.codec_score("AV1")
                > dt.codec_score("HEVC")
                > dt.codec_score("VP9")
                > dt.codec_score("H.264"))

    def test_hevc_aliases(self):
        assert dt.codec_score("H.265") == 3
        assert dt.codec_score("x265") == 3
        assert dt.codec_score("HEVC") == 3

    def test_h264_aliases(self):
        assert dt.codec_score("H264") == 1
        assert dt.codec_score("AVC") == 1
        assert dt.codec_score("x264") == 1

    def test_remux_source_highest(self):
        assert dt.codec_score("REMUX") == 4
        assert dt.codec_score("ProRes") == 4
        assert dt.codec_score("source") == 4

    def test_no_codec_is_zero(self):
        assert dt.codec_score("just some text") == 0
        assert dt.codec_score("") == 0
        assert dt.codec_score(None) == 0

    def test_codec_label_roundtrip(self):
        # remux/source and AV1 share the top bonus (4), so codec_label(4)
        # returns the first match; just assert it's a real top-tier
        # label. Distinct tiers below remain unambiguous.
        assert dt.codec_label(dt.codec_score("AV1")) in ("AV1", "remux/source")
        assert dt.codec_label(dt.codec_score("HEVC")) == "HEVC"
        assert dt.codec_label(dt.codec_score("VP9")) == "VP9"
        assert dt.codec_label(0) == ""

    def test_picks_best_when_multiple(self):
        # "H.264 / AV1" → AV1 wins
        assert dt.codec_score("available in H.264 and AV1") == 4


# ────────────────────────────────────────────────────────────────────
# TestCodecTierHeuristic: heuristic_scoring.detect_codec_tier
# ────────────────────────────────────────────────────────────────────

class TestCodecTierHeuristic:

    def test_ranking_parity(self):
        av1, _ = hs.detect_codec_tier("AV1")
        hevc, _ = hs.detect_codec_tier("HEVC")
        vp9, _ = hs.detect_codec_tier("VP9")
        h264, _ = hs.detect_codec_tier("H.264")
        assert av1 > hevc > vp9 > h264 > 0

    def test_no_codec(self):
        bonus, label = hs.detect_codec_tier("download now")
        assert bonus == 0 and label == ""

    def test_label_returned(self):
        bonus, label = hs.detect_codec_tier("encoded in AV1")
        assert label == "AV1" and bonus == 4

    def test_max_bonus_below_resolution_step(self):
        # The caveat-enforcing invariant: the largest codec bonus must
        # be smaller than the smallest adjacent resolution-tier gap
        # among REAL download tiers (720p=25 and up). The only sub-5
        # gaps are between junk preview tiers (353p/240p/360p/480p)
        # where codec scoring is irrelevant. This guarantees a better
        # codec at a lower real resolution can never override it.
        max_codec = max(b for _, b, _ in hs.CODEC_TIERS)
        real_tiers = sorted(
            t for _, t, _ in hs.RESOLUTION_TIERS if t >= 25)
        min_gap = min(b - a for a, b in zip(real_tiers, real_tiers[1:])
                      if b > a)
        assert max_codec < min_gap, (
            f"codec bonus {max_codec} >= min real-tier gap {min_gap} "
            "— codec could override resolution")


# ────────────────────────────────────────────────────────────────────
# TestCodecNeverOverridesResolution: the load-bearing F1 caveat
# ────────────────────────────────────────────────────────────────────

class TestCodecNeverOverridesResolution:

    def _score(self, text):
        total, _ = hs.score_text_signals(text, "", "", "a")
        return total

    def test_higher_res_h264_beats_lower_res_av1(self):
        # 1440p H.264 must beat 1080p AV1 — resolution dominates.
        hi = self._score("Download 1440p H.264")
        lo = self._score("Download 1080p AV1")
        assert hi > lo

    def test_4k_h264_beats_1080p_av1(self):
        hi = self._score("Download 4K H.264")
        lo = self._score("Download 1080p AV1")
        assert hi > lo

    def test_codec_breaks_tie_within_resolution(self):
        # Same resolution: AV1 should edge out H.264.
        av1 = self._score("Download 1080p AV1")
        h264 = self._score("Download 1080p H.264")
        assert av1 > h264

    def test_codec_bonus_is_small(self):
        # The codec delta at a fixed resolution is just the bonus.
        av1 = self._score("Download 1080p AV1")
        h264 = self._score("Download 1080p H.264")
        assert (av1 - h264) <= 4
