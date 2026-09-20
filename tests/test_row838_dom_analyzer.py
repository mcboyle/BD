"""Contract tests for Row 838: offline DOM tree analyzer and selector suggester.

SCOPE:
Offline DOM structure analysis passing redacted captured DOM trees to
10.0.70.228:11434 (local inference assistant on Tesla T4 #3) to produce candidate
CSS selector strings into existing draft template lanes for review.
0 site logins touched (Rule 21).

ACCEPTANCE:
(1) candidate selector matches simulated DOM test tree
(2) fallback to rule-based parser if :11434 is offline
(3) zero external egress beyond LAN
"""
from __future__ import annotations

import json
from pathlib import Path
from bs4 import BeautifulSoup
import pytest

BD_GATE_SCOPE = "module"

UPSTREAM_SEAM = "bulk_downloader.ai_provider.OllamaProvider._http_post"

SIMULATED_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Sample Media Page</title>
</head>
<body>
    <div id="wrapper">
        <header>
            <nav class="site-nav">Nav Menu</nav>
            <input type="hidden" name="csrf_token" value="sensitive_csrf_secret_123">
        </header>
        <main class="content-area">
            <article class="media-article" data-id="art-492">
                <h1 class="article-title">High Quality Sample Video</h1>
                <div class="video-container" id="player-box" data-token="secret-token-xyz">
                    <video class="main-video-player" src="https://media.local/stream1.mp4" controls></video>
                </div>
                <div class="download-section">
                    <a class="download-button" href="https://media.local/download/full.mp4">Download MP4</a>
                </div>
            </article>
        </main>
        <footer>
            <input type="password" name="auth_pass" value="super_secret_password">
        </footer>
    </div>
</body>
</html>
"""


def test_redact_dom_tree_removes_sensitive_attributes_and_tokens():
    from bulk_downloader import dom_structure_analyzer as dsa

    redacted = dsa.redact_dom_tree(SIMULATED_HTML)
    assert "sensitive_csrf_secret_123" not in redacted
    assert "super_secret_password" not in redacted
    assert "secret-token-xyz" not in redacted
    # Structural layout retained
    assert "video-container" in redacted
    assert "main-video-player" in redacted
    assert "download-button" in redacted


def test_candidate_selector_matches_simulated_dom_test_tree(monkeypatch):
    from bulk_downloader import dom_structure_analyzer as dsa

    # Positive control: prove the fixture built the shape
    soup = BeautifulSoup(SIMULATED_HTML, "html.parser")
    initial_videos = soup.select("video.main-video-player")
    assert len(initial_videos) == 1, "Fixture must contain exactly 1 video element"

    mock_selectors = [".video-container .main-video-player", "video.main-video-player"]

    def _mock_post(self, url, body, headers, timeout):
        payload = {
            "response": json.dumps({"selectors": mock_selectors})
        }
        return True, 200, payload, 42

    monkeypatch.setattr(UPSTREAM_SEAM, _mock_post)

    result = dsa.analyze_dom_structure(
        SIMULATED_HTML,
        target_desc="video player",
        endpoint="http://10.0.70.228:11434"
    )

    assert result["ok"] is True
    assert result["source"] == "inference"
    assert len(result["selectors"]) == 2
    # Verify candidate selector matches simulated DOM test tree
    candidate = result["selectors"][0]
    matched = soup.select(candidate)
    assert len(matched) >= 1
    assert matched[0].name == "video"


def test_fallback_to_rule_based_parser_if_endpoint_offline(monkeypatch):
    from bulk_downloader import dom_structure_analyzer as dsa

    soup = BeautifulSoup(SIMULATED_HTML, "html.parser")
    assert len(soup.select("video")) == 1, "Fixture must contain video"

    # Simulate 10.0.70.228:11434 being offline (connection error / HTTP 0)
    def _mock_offline(self, url, body, headers, timeout):
        return False, 0, "network: connection refused", 5

    monkeypatch.setattr(UPSTREAM_SEAM, _mock_offline)

    result = dsa.analyze_dom_structure(
        SIMULATED_HTML,
        target_desc="video",
        endpoint="http://10.0.70.228:11434"
    )

    assert result["ok"] is True
    assert result["source"] == "rule_based_fallback"
    assert len(result["selectors"]) >= 1

    # Verify fallback candidate selector matches simulated DOM test tree
    for sel in result["selectors"]:
        matched = soup.select(sel)
        assert len(matched) >= 1


def test_zero_external_egress_beyond_lan(monkeypatch):
    from bulk_downloader import dom_structure_analyzer as dsa

    # Mock post to ensure it is NEVER called when an external endpoint is provided
    called = []

    def _mock_post(self, url, body, headers, timeout):
        called.append(url)
        return True, 200, {"response": "{}"}, 10

    monkeypatch.setattr(UPSTREAM_SEAM, _mock_post)

    # External IP (8.8.8.8) and external domain (example.com)
    for bad_endpoint in ["http://8.8.8.8:11434", "https://example.com/api", "http://1.1.1.1:11434"]:
        with pytest.raises(ValueError) as excinfo:
            dsa.analyze_dom_structure(
                SIMULATED_HTML,
                target_desc="video",
                endpoint=bad_endpoint
            )
        assert "egress forbidden" in str(excinfo.value).lower()

    # Zero egress verified
    assert len(called) == 0, "Zero external egress must be strictly enforced"


def test_candidate_saved_to_draft_lane(tmp_path, monkeypatch):
    from bulk_downloader import dom_structure_analyzer as dsa

    def _mock_offline(self, url, body, headers, timeout):
        return False, 0, "offline", 1

    monkeypatch.setattr(UPSTREAM_SEAM, _mock_offline)

    host = "sample-site.local"
    result = dsa.analyze_dom_structure(
        SIMULATED_HTML,
        target_desc="video",
        endpoint="http://10.0.70.228:11434",
        save_draft_host=host,
        drafts_dir=tmp_path
    )

    draft_file = tmp_path / f"{host}.template-draft.json"
    assert draft_file.is_file(), "Draft template must be written to draft lane"

    draft_data = json.loads(draft_file.read_text("utf-8"))
    assert draft_data["schema"] == "bulk_downloader.template.draft.v1"
    assert draft_data["status"] == "draft_review_required"
    assert draft_data["review_required"] is True
    assert "selectors" in draft_data
    assert len(draft_data["selectors"]) >= 1


def test_negative_control_selector_probe_and_exact_count():
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(SIMULATED_HTML, "html.parser")

    # Negative control: prove selector probe can say NO
    non_existent = soup.select(".non-existent-element-class")
    assert len(non_existent) == 0, "Negative control: non-existent selector must match 0 elements"

    # Exact count assertion on fixture elements
    video_count = len(soup.select("video"))
    article_count = len(soup.select("article"))
    download_count = len(soup.select(".download-button"))
    assert (video_count, article_count, download_count) == (1, 1, 1)
