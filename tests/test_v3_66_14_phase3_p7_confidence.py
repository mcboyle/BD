"""v3.66.14 — P7 tests for confidence calibration.

Verifies the score→confidence curve is well-behaved (monotone,
continuous, bounded, hits its documented breakpoints) and that
deep_detect candidates carry the new `confidence` field across all
three input paths (HLS-as-input, DASH-as-input, HTML page).
"""
import pytest

from bulk_downloader.deep_detect import (
    _attach_confidence,
    _CONFIDENCE_BREAKPOINTS,
    _score_to_confidence,
    deep_detect,
)

pytestmark = pytest.mark.bd_module_wipe


# ── curve shape ──────────────────────────────────────────────────────


@pytest.mark.parametrize("score,expected", _CONFIDENCE_BREAKPOINTS)
def test_breakpoints_round_trip(score, expected):
    """Every documented breakpoint maps to its declared confidence."""
    assert _score_to_confidence(score) == pytest.approx(expected)


def test_floor_at_or_below_zero():
    """Scores <= 0 (the documented floor) map to 0.0."""
    assert _score_to_confidence(0) == 0.0
    assert _score_to_confidence(-1) == 0.0
    assert _score_to_confidence(-9999) == 0.0


def test_asymptote_above_max_breakpoint():
    """Scores at or above the top breakpoint asymptote at that
    breakpoint's confidence; never reach 1.0 (headroom)."""
    top_score, top_conf = _CONFIDENCE_BREAKPOINTS[-1]
    assert _score_to_confidence(top_score) == top_conf
    assert _score_to_confidence(top_score + 1) == top_conf
    assert _score_to_confidence(99999) == top_conf
    assert top_conf < 1.0, "asymptote should leave headroom (curve never hits 1.0)"


def test_monotone_non_decreasing():
    """The curve is monotone non-decreasing: sorting by confidence
    is rank-equivalent to sorting by score."""
    prev = -1.0
    for s in range(-50, 1000, 5):
        c = _score_to_confidence(s)
        assert c >= prev, f"non-monotone at score={s}: {c} < {prev}"
        prev = c


def test_continuity_at_breakpoints():
    """No discontinuities: c(s) at a breakpoint equals c(s-1) plus the
    next segment's marginal contribution. We check by comparing
    1-unit-below-breakpoint vs 1-unit-above-breakpoint stays close."""
    for i in range(1, len(_CONFIDENCE_BREAKPOINTS) - 1):
        s_bp, _ = _CONFIDENCE_BREAKPOINTS[i]
        below = _score_to_confidence(s_bp - 1)
        at = _score_to_confidence(s_bp)
        above = _score_to_confidence(s_bp + 1)
        # The bigger jump must be at most the average of the two
        # adjacent segments' slopes — proxy for "no jagged corners".
        assert at - below < 0.2
        assert above - at < 0.2


def test_linear_inside_segment():
    """Inside a segment, the curve is linear: midpoint of an
    80..100 segment should equal the midpoint of 0.50..0.70."""
    mid_score = 90
    expected = (0.50 + 0.70) / 2  # midpoint
    assert _score_to_confidence(mid_score) == pytest.approx(expected, abs=1e-4)


# ── _attach_confidence behavior ──────────────────────────────────────


def test_attach_confidence_decorates_in_place():
    cands = [
        {"score": 100, "source_type": "hls_manifest", "url": "u1"},
        {"score": 80, "source_type": "direct_file", "url": "u2"},
    ]
    out = _attach_confidence(cands)
    assert out is cands  # returns same list (chain-friendly)
    assert cands[0]["confidence"] == 0.7
    assert cands[1]["confidence"] == 0.5


def test_attach_confidence_handles_missing_score():
    cands = [
        {"source_type": "direct_file", "url": "u"},  # no score key at all
        {"score": None, "source_type": "direct_file", "url": "u2"},
    ]
    _attach_confidence(cands)
    assert cands[0]["confidence"] == 0.0
    assert cands[1]["confidence"] == 0.0


# ── End-to-end: deep_detect attaches confidence everywhere ───────────


HLS_MASTER = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720
720p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080
1080p.m3u8
"""


DASH_MPD = """<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static">
  <Period>
    <AdaptationSet contentType="video" mimeType="video/mp4">
      <Representation id="v" bandwidth="2000000" width="1280" height="720">
        <BaseURL>v720.mp4</BaseURL>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>
"""


PAGE_WITH_STATE_BLOB = """<!doctype html><html><head></head>
<body>
<script id="__NEXT_DATA__" type="application/json">
{"source": {"hls": "https://cdn.example.test/master.m3u8"}}
</script>
</body></html>
"""


PAGE_WITH_LOGIN = """<!doctype html><html><body>
<form action="/login" method="post">
  <input name="email" type="email" required>
  <input name="password" type="password" required>
  <button type="submit">Sign in</button>
</form>
</body></html>
"""


def test_hls_input_attaches_confidence():
    r = deep_detect(HLS_MASTER, base_url="https://cdn.example.test/m.m3u8")
    cands = r["buckets"]["accepted"]
    assert cands, "expected variants"
    for c in cands:
        assert "confidence" in c
        assert 0.0 <= c["confidence"] <= 1.0
    assert r["buckets"]["best"]["confidence"] == cands[0]["confidence"]


def test_dash_input_attaches_confidence():
    r = deep_detect(DASH_MPD, base_url="https://cdn.example.test/m.mpd")
    cands = r["buckets"]["accepted"]
    assert cands, "expected reps"
    for c in cands:
        assert "confidence" in c
        assert 0.0 <= c["confidence"] <= 1.0


def test_html_input_attaches_confidence():
    r = deep_detect(PAGE_WITH_STATE_BLOB,
                    base_url="https://example.test/page")
    cands = r["buckets"]["accepted"]
    assert cands, "expected at least one candidate from the state blob"
    for c in cands:
        assert "confidence" in c
        assert 0.0 <= c["confidence"] <= 1.0


def test_login_candidates_attach_confidence_calibrated():
    """Login candidates already use a `confidence` field (int 0-100).
    The new float lives on `confidence_calibrated` to avoid clobbering."""
    r = deep_detect(PAGE_WITH_LOGIN, base_url="https://example.test/login")
    assert r["best_login"], "expected best_login"
    # Existing int field still there
    assert isinstance(r["best_login"]["confidence"], int)
    # New calibrated float was attached
    assert "confidence_calibrated" in r["best_login"]
    assert 0.0 <= r["best_login"]["confidence_calibrated"] <= 1.0
    for lc in r["login_candidates"]:
        assert "confidence_calibrated" in lc


def test_confidence_ordering_matches_score_ordering():
    """Within a single report, confidence is rank-equivalent to score:
    sorting by either gives the same order.

    Uses the HLS master input, which yields ≥2 accepted variants
    (distinct bandwidths → distinct scores), so the ordering is actually
    exercised rather than skipped on a single-candidate input.
    """
    r = deep_detect(HLS_MASTER,
                    base_url="https://cdn.example.test/m.m3u8")
    cands = r["buckets"]["accepted"]
    assert len(cands) >= 2, "expected ≥2 variants from the HLS master"
    scores = [c["score"] for c in cands]
    confs = [c["confidence"] for c in cands]
    # Both sequences should be sorted descending — same as the report
    assert scores == sorted(scores, reverse=True)
    assert confs == sorted(confs, reverse=True)
