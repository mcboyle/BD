import json

from bulk_downloader.har_compactor import compact_har

BD_GATE_SCOPE = "module"


def _entry(url, mime, body=""):
    return {"request": {"url": url}, "response": {"content": {"mimeType": mime, "text": body}}}


def test_compactor_reduces_noise_and_retains_diagnostic_entries():
    har = {"log": {"version": "1.2", "creator": {"name": "test"}, "entries": [
        _entry("https://x/api/items", "application/json", "{}"),
        _entry("https://x/video/master.m3u8", "application/vnd.apple.mpegurl", "#EXTM3U"),
        _entry("https://x/page", "text/html", "<html/>"),
        *[_entry(f"https://x/analytics/pixel{i}.png", "image/png", "x" * 5000) for i in range(30)],
    ]}}
    compacted = compact_har(har)
    assert len(compacted["log"]["entries"]) == 3
    urls = {entry["request"]["url"] for entry in compacted["log"]["entries"]}
    assert "https://x/api/items" in urls and "https://x/video/master.m3u8" in urls
    assert len(json.dumps(compacted)) < len(json.dumps(har)) * .2
    assert json.loads(json.dumps(compacted))["log"]["version"] == "1.2"


# ── FIXER (row902 REFUTE E1/E2) ──────────────────────────────────────────

import pytest

from bulk_downloader.har_compactor import should_keep_entry


@pytest.mark.parametrize("url,mime", [
    ("https://x/blog/tracking-your-orders", "text/html; charset=utf-8"),   # E1 P1
    ("https://x/v2/analytics/config", "application/json; charset=utf-8"),  # E1 P2
    ("https://x/pixel-policy", "TEXT/HTML;charset=ISO-8859-1"),
])
def test_document_and_api_bodies_with_mime_parameters_are_kept(url, mime):
    """E1: real captures spell `text/html; charset=utf-8`; the keep rule must
    match the media type, even when the URL carries a drop token."""
    assert should_keep_entry(_entry(url, mime, "body")) is True


@pytest.mark.parametrize("url", [
    "https://x/master.m3u8?tracking=1",          # E2 P4: signed/query manifest
    "https://x/seg/0001.m4s?token=abc&pixel=1",
    "https://x/live/stream.mpd?analytics=on",
])
def test_manifests_and_segments_with_query_strings_are_kept(url):
    """E2: the media suffix is judged on the URL path, not the full URL."""
    assert should_keep_entry(_entry(url, "application/octet-stream", "x")) is True


def test_noise_is_still_dropped():
    assert should_keep_entry(_entry("https://x/analytics/pixel.png", "image/png; q=1", "x")) is False
    assert should_keep_entry(_entry("https://x/tracking.js?v=2", "text/javascript", "x")) is False
    assert should_keep_entry(_entry("https://x/site.css?ver=3", "text/css; charset=utf-8", "x")) is False
