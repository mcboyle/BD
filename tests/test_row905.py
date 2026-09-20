"""Row 905 -- DYNAMIC-QUALITY-AND-CODEC-NEGOTIATION-POLICY.

Arbitrary format selection leads to sub-optimal bitrate choices or excessive
bandwidth consumption. ``detect.select_quality_tier`` ranks candidate streams
against a structured quality ladder (2160p:AV1 > 1080p:HEVC > 1080p:AVC >
720p:ANY), reusing the existing ``res_score``/``codec_score`` signals rather
than re-deriving resolution/codec detection.
"""
from __future__ import annotations

import pytest

from bulk_downloader import detect
from bulk_downloader.constants import QUALITY_LADDER

BD_GATE_SCOPE = "module"


def test_ladder_is_the_scope_order():
    assert [tier[0] for tier in QUALITY_LADDER] == [
        "2160p:AV1", "1080p:HEVC", "1080p:AVC", "720p:ANY",
    ]


def test_optimal_tier_selection_across_candidate_streams():
    candidates = [
        "720p H.264 (300 MB)",
        "1080p HEVC x265 (500 MB)",
        "3840x2160 4K AV1 (2 GB)",
        "1080p H.264 (450 MB)",
    ]
    assert detect.select_quality_tier(candidates) == "3840x2160 4K AV1 (2 GB)"


def test_optimal_tier_selection_falls_through_the_ladder_when_top_absent():
    candidates = [
        "720p H.264 (300 MB)",
        "1080p HEVC x265 (500 MB)",
        "1080p H.264 (450 MB)",
    ]
    assert detect.select_quality_tier(candidates) == "1080p HEVC x265 (500 MB)"


def test_user_override_policy_adherence_restricts_to_requested_resolution_and_codec():
    candidates = [
        "3840x2160 4K AV1 (2 GB)",
        "1080p HEVC x265 (500 MB)",
        "1080p H.264 (450 MB)",
        "720p H.264 (300 MB)",
    ]
    # Operator explicitly wants 1080p H.264, not the ladder's top pick.
    picked = detect.select_quality_tier(
        candidates, target_resolution=1080, target_codec="H.264",
    )
    assert picked == "1080p H.264 (450 MB)"


def test_user_override_with_no_matching_candidate_falls_back_to_full_pool():
    candidates = [
        "3840x2160 4K AV1 (2 GB)",
        "720p H.264 (300 MB)",
    ]
    # No candidate is HEVC at any resolution -- override cannot be honored,
    # so the policy falls back to ranking the whole pool rather than
    # returning nothing.
    picked = detect.select_quality_tier(candidates, target_codec="HEVC")
    assert picked == "3840x2160 4K AV1 (2 GB)"


def test_graceful_fallback_when_target_tier_is_unavailable():
    # Nothing clears the ladder's 720p floor -- the policy must still return
    # the best available candidate, not None and not raise.
    candidates = ["480p VP9 (150 MB)", "360p H.264 (80 MB)"]
    assert detect.select_quality_tier(candidates) == "480p VP9 (150 MB)"


def test_empty_candidate_pool_returns_none():
    assert detect.select_quality_tier([]) is None


def test_ladder_tier_outranks_raw_resolution():
    # A 2160p VP9 stream scores higher on raw resolution than a 1080p AV1
    # stream, but VP9 doesn't clear the codec floor for any tier above
    # 1080p:AVC while AV1 clears 1080p:HEVC -- the ladder must prefer the
    # better-tiered 1080p AV1 stream, not the higher-resolution one.
    candidates = ["2160p VP9 (1.5 GB)", "1080p AV1 (800 MB)"]
    assert detect.select_quality_tier(candidates) == "1080p AV1 (800 MB)"


# ── FIXER (row905 REFUTE E1/E2/E3) ───────────────────────────────────────


_MIXED_POOL = [
    "3840x2160 4K HEVC (2 GB)",
    "3840x2160 4K VP9 (1.8 GB)",
    "1080p AV1 (400 MB)",
    "1080p H.264 (450 MB)",
    "1080p REMUX (9 GB)",
]


def test_target_codec_av1_selects_an_av1_candidate():
    """E1: codec_label(codec_score) never says "AV1" (score 4 is shared with
    remux/source), so target_codec="AV1" silently fell back to 2160p HEVC."""
    assert detect.select_quality_tier(_MIXED_POOL, target_codec="AV1") == "1080p AV1 (400 MB)"


@pytest.mark.parametrize("target", ["AVC", "avc", "H.264", "h264", "x264"])
def test_target_codec_avc_matches_h264_candidates(target):
    """E2: the ladder calls the tier "1080p:AVC" while the label is "H.264";
    the user's spelling must not matter."""
    assert detect.select_quality_tier(_MIXED_POOL, target_codec=target) == "1080p H.264 (450 MB)"


def test_target_codec_remux_does_not_match_av1():
    """E3: "remux/source" and "AV1" share a score; a remux request must pick
    only a remux candidate, and never an AV1 one when no remux exists."""
    assert detect.select_quality_tier(_MIXED_POOL, target_codec="remux/source") == "1080p REMUX (9 GB)"
    no_remux = [c for c in _MIXED_POOL if "REMUX" not in c]
    # override matches nothing -> documented full-pool fallback (top ladder tier), not the AV1 file
    assert detect.select_quality_tier(no_remux, target_codec="remux/source") != "1080p AV1 (400 MB)"
