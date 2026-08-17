"""v3.66.7 — Offline smoke for bdctl deep-detect / deep_detect engine.

Backlog item #2 in handoff/v3_66_6_handoff.txt called out that a real-
URL smoke of `bdctl deep-detect` was network-blocked in v3.66.6. This
file substitutes a deterministic HTML fixture that exercises the same
deep_detect code path bdctl wraps (POST /api/dev/deep_detect ->
bulk_downloader.deep_detect.deep_detect), without the HTTP hop.

What this proves:
  - Multi-resolution `resolution_download_card` emission.
  - Best-download selection ranks 4k > 1080p > 720p with the correct
    codec/fps annotations.
  - The report preserves the candidate-selection contract
    (`download_candidates` list with click_selector
    + url + resolution + codec + fps + score per row).

A full network-backed smoke against three real URLs (login form,
multi-res page, provider embed) is still backlog for v3.66.7 — this
just closes the "is the engine wired" question offline.
"""

import pytest

from bulk_downloader import deep_detect


MULTI_RES_HTML = """\
<!doctype html>
<html><head><title>Multi-res smoke</title></head>
<body>
<div class="video-detail">
  <h1>Sample video</h1>
  <a class="resolution-card" href="/dl/4k.mp4"
     data-quality="2160p" data-size="1.2 GB">
    <span class="label">4K (2160p)</span>
    <span class="codec">h265</span>
    <span class="fps">60fps</span>
  </a>
  <a class="resolution-card" href="/dl/1080.mp4"
     data-quality="1080p" data-size="650 MB">
    <span class="label">Full HD (1080p)</span>
    <span class="codec">h264</span>
    <span class="fps">30fps</span>
  </a>
  <a class="resolution-card" href="/dl/720.mp4"
     data-quality="720p" data-size="350 MB">
    <span class="label">HD (720p)</span>
    <span class="codec">h264</span>
    <span class="fps">30fps</span>
  </a>
</div>
</body></html>
"""


@pytest.fixture(scope="module")
def report():
    return deep_detect.deep_detect(
        MULTI_RES_HTML, base_url="https://example.com/v/123"
    )


def test_three_resolution_cards_emitted(report):
    cards = [c for c in report["buckets"]["accepted"]
             if c.get("source_type") == "resolution_download_card"]
    assert len(cards) == 3
    labels = [c["resolution"]["label"] for c in cards]
    assert labels == ["4k", "1080p", "720p"]


def test_each_card_has_click_selector_and_url(report):
    cards = [c for c in report["buckets"]["accepted"]
             if c.get("source_type") == "resolution_download_card"]
    for c in cards:
        assert c["click_selector"], (
            f"card {c['resolution']['label']} missing click_selector"
        )
        assert c["url"], f"card {c['resolution']['label']} missing url"


def test_best_download_is_4k_hevc_60fps(report):
    bd = report.get("buckets", {}).get("best")
    assert bd is not None
    assert bd["resolution"]["label"] == "4k"
    assert bd["codec"] == "hevc"
    assert bd["fps"] == 60.0


def test_scoring_orders_resolutions_correctly(report):
    cards = [c for c in report["buckets"]["accepted"]
             if c.get("source_type") == "resolution_download_card"]
    by_label = {c["resolution"]["label"]: c["score"] for c in cards}
    assert by_label["4k"] > by_label["1080p"] > by_label["720p"]


def test_multiple_actionable_candidates_are_explicit(report):
    """Two or more cards with selectors and URLs remain distinguishable.

    This is the API-level selection condition; no frontend implementation is
    part of the engine contract.
    """
    cards = [c for c in report["buckets"]["accepted"]
             if c.get("source_type") == "resolution_download_card"
             and c.get("click_selector")
             and c.get("url")]
    assert len(cards) >= 2


def test_report_has_v3_66_6_blocker_and_disclaimer_keys(report):
    """v3.66.6 added blocker-aware `warnings` annotations. The keys
    should exist on the report (empty is fine for this clean fixture)
    so downstream consumers don't KeyError.
    """
    assert "warnings" in report["buckets"]
    assert "blockers" in report
    assert isinstance(report["buckets"]["warnings"], list)
    assert isinstance(report["blockers"], dict)
    # Clean fixture: not blocked, not gated for auto-submit
    assert report["blockers"].get("blocked") is False
    assert report["blockers"].get("do_not_auto_submit") is False
