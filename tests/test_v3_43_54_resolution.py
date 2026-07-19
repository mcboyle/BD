"""v3.43.54: comprehensive resolution detection + wow template fix.

Bug: wowgirls 5K (5568×3132) and 8K downloads kept failing because
the CDN is unreliable for those tiers. The template's
quality_preference="4320,2160,1080,720" was correct (4K wins when
8K isn't an explicit candidate), but new sites added via the
wizard had no quality_preference at all → fell through to
"highest score" → picked the broken 5K/8K candidates.

Fix:
  1. RESOLUTION_TIERS (heuristic_scoring.py) expanded to include
     5K (5120×2880), 5K cinema (4096×2160), 6K (5760×3240,
     5568×3132, 6144×3160, 6016×3384), plus less-common heights
     (1200, 900, 540, 360, 240). Detection is now comprehensive.
  2. DEFAULTS["quality_preference"] = "2160,1080,720" — new sites
     prefer 4K → 1080p → 720p out of the box. To chase 5K+/8K
     users can prepend the higher tiers in the site edit form.
  3. wowgirls template's quality_preference flipped to
     "2160,1080,720" (matches the new default).
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent

def _APP_SRC():
    """app.py + extracted app_*.py modules (Phase 4 thin-core-shell; DECOMP-R2a kernels)."""
    import bulk_downloader as _bd, pathlib as _pl
    _pkg = _pl.Path(_bd.__file__).parent
    _parts = [(_pkg / 'app.py').read_text(encoding='utf-8')]
    _parts += [p.read_text(encoding='utf-8') for p in sorted(_pkg.glob('app_*.py'))]
    return '\n'.join(_parts)



# ── Comprehensive detection in heuristic_scoring.py ─────────────


def test_heuristic_detects_5k_iMac():
    """5120×2880 (iMac 5K) detects as 5K tier."""
    from bulk_downloader.heuristic_scoring import detect_resolution_tier
    tier, label = detect_resolution_tier("Download 5120 x 2880 (HEVC)")
    assert label == "5K"
    assert tier == 65


def test_heuristic_detects_5k_cinema():
    """4096×2160 (DCI 4K, often labeled '5K cinema') detects as 5K."""
    from bulk_downloader.heuristic_scoring import detect_resolution_tier
    tier, label = detect_resolution_tier("4096 x 2160 download")
    assert label == "5K"


def test_heuristic_detects_6k_canonical():
    """5760×3240 is canonical 6K."""
    from bulk_downloader.heuristic_scoring import detect_resolution_tier
    tier, label = detect_resolution_tier("Download 5760 x 3240")
    assert label == "6K"


def test_heuristic_detects_6k_wowgirls():
    """5568×3132 is the HEVC-friendly 6K wowgirls serves."""
    from bulk_downloader.heuristic_scoring import detect_resolution_tier
    tier, label = detect_resolution_tier("5568 x 3132")
    assert label == "6K"


def test_heuristic_detects_8k():
    """7680×4320 is 8K UHD."""
    from bulk_downloader.heuristic_scoring import detect_resolution_tier
    tier, label = detect_resolution_tier("7680 x 4320")
    assert label == "8K"


def test_heuristic_detects_4k_explicit():
    """3840×2160 is the canonical UHD 4K."""
    from bulk_downloader.heuristic_scoring import detect_resolution_tier
    tier, label = detect_resolution_tier("3840 x 2160")
    assert label == "4K"


def test_heuristic_detects_1080p():
    from bulk_downloader.heuristic_scoring import detect_resolution_tier
    tier, label = detect_resolution_tier("1920 x 1080 Full HD")
    assert label == "1080p"


def test_heuristic_detects_lesser_resolutions():
    """1200p, 900p, 540p, 480p, 360p, 240p all detect."""
    from bulk_downloader.heuristic_scoring import detect_resolution_tier
    cases = [
        ("1920 x 1200", "1200p"),
        ("1600 x 900",  "900p"),
        ("960 x 540",   "540p"),
        ("854 x 480",   "480p"),
        ("640 x 360",   "360p"),
        ("426 x 240",   "240p"),
    ]
    for text, expected in cases:
        _, label = detect_resolution_tier(text)
        assert label == expected, f"{text!r} → {label!r}, expected {expected!r}"


def test_heuristic_tier_ordering():
    """Higher resolutions return higher tier scores."""
    from bulk_downloader.heuristic_scoring import detect_resolution_tier
    tier_8k, _ = detect_resolution_tier("8K")
    tier_6k, _ = detect_resolution_tier("6K")
    tier_5k, _ = detect_resolution_tier("5K")
    tier_4k, _ = detect_resolution_tier("4K")
    tier_2k, _ = detect_resolution_tier("2K")
    tier_1080, _ = detect_resolution_tier("1920 x 1080")
    tier_720, _ = detect_resolution_tier("1280 x 720")
    assert tier_8k > tier_6k > tier_5k > tier_4k > tier_2k > tier_1080 > tier_720


def test_heuristic_picks_highest_among_multiple():
    """Text with multiple resolutions returns the highest tier."""
    from bulk_downloader.heuristic_scoring import detect_resolution_tier
    _, label = detect_resolution_tier(
        "Available: 5568 x 3132, 3840 x 2160, 1920 x 1080")
    assert label == "6K"


# ── detect.py res_score (download-time path) ─────────────────────


def test_detect_res_score_5k_height():
    """5568x3132 returns the height 3132 from detect.res_score."""
    from bulk_downloader.detect import res_score
    assert res_score("5568 x 3132") == 3132


def test_detect_res_score_4k_height():
    from bulk_downloader.detect import res_score
    assert res_score("3840 x 2160") == 2160


def test_detect_res_score_8k_height():
    from bulk_downloader.detect import res_score
    assert res_score("7680 x 4320") == 4320


# ── Wow template defaults ────────────────────────────────────────


def test_wow_template_prefers_4k():
    """The wowgirls template must order quality_preference so 4K
    wins by default (not 8K or 5K). This is the key behavioral fix
    that prevents the failing 5K/8K downloads."""
    from bulk_downloader import templates
    tpl = templates.get("wowgirls_network")
    assert tpl is not None
    qpref = tpl["config_defaults"]["quality_preference"]
    # 2160 must be the first preference (or appear before any higher tier)
    prefs = [p.strip() for p in qpref.split(",")]
    assert "2160" in prefs
    pos_2160 = prefs.index("2160")
    # No 4320 or 3132 (5K/6K) anywhere — these are the broken tiers
    for tier in ("4320", "3132", "2880", "3240"):
        assert tier not in prefs, (
            f"template should not auto-prefer {tier} (flaky CDN tier)")
    # And 1080 / 720 come AFTER 2160 (fallback order)
    if "1080" in prefs:
        assert prefs.index("1080") > pos_2160
    if "720" in prefs:
        assert prefs.index("720") > pos_2160


def test_wow_template_lists_all_observed_tiers():
    """tier_labels_seen should document what the network actually
    serves. After v3.43.54 we KNOW they serve 5K (5568×3132) and 6K."""
    from bulk_downloader import templates
    tpl = templates.get("wowgirls_network")
    seen = tpl["learned"]["download"]["tier_labels_seen"]
    # We know they serve at least these
    for tier in ("8K", "5K", "4K", "1080p"):
        assert tier in seen, f"missing tier label: {tier}"


# ── DEFAULTS for newly-created sites ────────────────────────────


def test_default_quality_preference_is_safe():
    """New sites must NOT default to empty / "highest available with no
    cascade". The default must include at least one explicit tier so
    flaky CDNs that randomly serve the top tier badly still have a
    fallback. v3.43.65 flipped this to start with 4320 (8K-first
    cascade) per Matt's hardware; this test just guards "not empty"
    and "mentions 2160" as the floor for being a useful default."""
    src = _APP_SRC()
    # Find the DEFAULTS dict assignment
    pos = src.find("DEFAULTS=")
    assert pos > 0
    # v3.43.65: widened scan window to 12000 — the DEFAULTS block has
    # grown materially since v3.43.54 (metadata fields, VPN, captcha
    # relay, widgets, live recorder, tier probe, etc.) so a generous
    # cap avoids whack-a-mole on future releases.
    block = src[pos:pos + 12000]
    # quality_preference must be set in DEFAULTS
    qpref_pos = block.find('"quality_preference"')
    assert qpref_pos > 0
    # Get the line + a bit
    line = block[qpref_pos:qpref_pos + 200]
    # Must contain 2160 (4K) and NOT be empty string
    assert "2160" in line
    # The pre-fix default was '""', which is the line we DON'T want
    assert '"quality_preference":""' not in line and \
           '"quality_preference": ""' not in line


# ── Integration: full candidate selection on wowgirls HTML ───────


def test_full_pipeline_picks_4k_when_5k_available():
    """Simulating the wowgirls case: page has 5K (3132 score),
    4K (2160 score), and 1080p (1080 score). With the default
    quality_preference, the runner picks the 4K candidate."""
    # We can't easily run the full runner (needs Playwright); but
    # we can verify _apply_quality_preference does the right thing
    # with the new default.
    from bulk_downloader.runner import SiteRunner
    # Build a fake "best" with _all_candidates
    fake_best = {
        "score": 3132, "size": 5_000_000_000,
        "text": "5568 x 3132", "locator": object(),
        "_all_candidates": [
            {"score": 3132, "size": 5_000_000_000, "text": "5568 x 3132",
              "locator": object()},
            {"score": 2160, "size": 3_500_000_000, "text": "3840 x 2160",
              "locator": object()},
            {"score": 1080, "size": 1_500_000_000, "text": "1920 x 1080",
              "locator": object()},
        ],
    }
    # Stub a runner with just the needed bits
    runner = object.__new__(SiteRunner)
    runner.config = {"quality_preference": "2160,1080,720"}
    result = runner._apply_quality_preference(fake_best, "2160,1080,720")
    assert result["score"] == 2160, (
        f"expected 4K (2160) winner, got {result['score']}")


def test_full_pipeline_picks_1080_when_4k_absent():
    """If only 5K and 1080p exist, default preference 2160,1080,720
    skips 5K (no match) and picks 1080p."""
    from bulk_downloader.runner import SiteRunner
    fake_best = {
        "score": 3132, "size": 5_000_000_000,
        "text": "5568 x 3132", "locator": object(),
        "_all_candidates": [
            {"score": 3132, "size": 5_000_000_000, "text": "5568 x 3132",
              "locator": object()},
            {"score": 1080, "size": 1_500_000_000, "text": "1920 x 1080",
              "locator": object()},
        ],
    }
    runner = object.__new__(SiteRunner)
    runner.config = {"quality_preference": "2160,1080,720"}
    result = runner._apply_quality_preference(fake_best, "2160,1080,720")
    assert result["score"] == 1080


def test_full_pipeline_falls_back_to_5k_when_only_5k_available():
    """If quality_preference matches nothing AND the only candidate
    is 5K, we still pick it (better than nothing). User can re-teach
    or adjust preference if they want different behavior."""
    from bulk_downloader.runner import SiteRunner
    fake_best = {
        "score": 3132, "size": 5_000_000_000,
        "text": "5568 x 3132", "locator": object(),
        "_all_candidates": [
            {"score": 3132, "size": 5_000_000_000, "text": "5568 x 3132",
              "locator": object()},
        ],
    }
    runner = object.__new__(SiteRunner)
    runner.config = {"quality_preference": "2160,1080,720"}
    result = runner._apply_quality_preference(fake_best, "2160,1080,720")
    # Falls back to best (the 5K)
    assert result["score"] == 3132
