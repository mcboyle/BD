"""Cut 933: JavaScript Player Inline Configuration Object Extractor.

Row 933 Acceptance criteria:
(1) extraction of source lists from inline player configs
(2) quality ladder parsing
(3) zero live playback simulation required
"""

from __future__ import annotations

import pytest
try:
    from bulk_downloader.player_config import (
        PlayerConfig,
        PlayerSource,
        QualityTier,
        extract_player_configs,
        parse_js_object,
    )
except ImportError:
    # Stub definitions to ensure clean collection and true behavioral RED assertions on base
    PlayerConfig = None  # type: ignore
    PlayerSource = None  # type: ignore
    QualityTier = None  # type: ignore

    def extract_player_configs(text: str) -> list:  # type: ignore
        return []

    def parse_js_object(text: str) -> dict | None:  # type: ignore
        return None

BD_GATE_SCOPE = "module"


SAMPLE_JWPLAYER_HTML = """
<!DOCTYPE html>
<html>
<head><title>JWPlayer Test</title></head>
<body>
<div id="my-video-player"></div>
<script type="text/javascript">
jwplayer("my-video-player").setup({
    playlist: [{
        title: "Test Feature Stream",
        image: "https://media.example.org/thumbs/feature.jpg",
        sources: [
            { file: "https://media.example.org/video-360p.mp4", label: "360p", height: 360, width: 640, bitrate: 800000, type: "video/mp4" },
            { file: "https://media.example.org/video-720p.mp4", label: "720p", height: 720, width: 1280, bitrate: 2500000, type: "video/mp4" },
            { file: "https://media.example.org/video-1080p.mp4", label: "1080p", height: 1080, width: 1920, bitrate: 5000000, type: "video/mp4" }
        ]
    }],
    autostart: false,
    token: "jwt_sec_token_9876543210"
});
</script>
</body>
</html>
"""

SAMPLE_VIDEOJS_HTML = """
<!DOCTYPE html>
<html>
<body>
<video id="vid1" class="video-js" data-setup='{"controls": true, "sources": [{"src": "https://cdn.example.com/hls/master.m3u8?token=sig_abc123", "type": "application/x-mpegURL", "label": "Auto"}]}'>
</video>
<script>
var player = videojs('vid2', {
    sources: [
        { src: "https://cdn.example.com/vod/720p.mp4", type: "video/mp4", label: "720p", height: 720 },
        { src: "https://cdn.example.com/vod/1080p.mp4", type: "video/mp4", label: "1080p", height: 1080 }
    ],
    playbackRates: [0.5, 1, 1.5, 2]
});
</script>
</body>
</html>
"""

SAMPLE_GENERIC_PLAYER_JS = """
window.__INITIAL_PLAYER_STATE__ = {
    "mediaId": "media-9921",
    "auth": {
        "accessToken": "bearer-token-alpha-99",
        "signature": "sig-hex-778899"
    },
    "stream": {
        "hlsUrl": "https://stream.example.net/live/index.m3u8",
        "qualities": [
            {"res": "480p", "url": "https://stream.example.net/live/480.m3u8", "bitrate": 1200000, "height": 480},
            {"res": "1080p", "url": "https://stream.example.net/live/1080.m3u8", "bitrate": 4500000, "height": 1080}
        ]
    }
};
"""


def test_jwplayer_source_list_extraction():
    """AC 1: Extract source lists from inline JWPlayer setup config."""
    configs = extract_player_configs(SAMPLE_JWPLAYER_HTML)
    assert len(configs) >= 1, "Expected at least one player config extracted"

    jw_cfg = next((c for c in configs if c.player_type == "jwplayer"), None)
    assert jw_cfg is not None, "JWPlayer config was not identified"
    assert len(jw_cfg.sources) == 3, f"Expected 3 sources, got {len(jw_cfg.sources)}"

    urls = [s.url for s in jw_cfg.sources]
    assert "https://media.example.org/video-360p.mp4" in urls
    assert "https://media.example.org/video-720p.mp4" in urls
    assert "https://media.example.org/video-1080p.mp4" in urls


def test_quality_ladder_parsing():
    """AC 2: Parse quality ladder into structured tiers."""
    configs = extract_player_configs(SAMPLE_JWPLAYER_HTML)
    jw_cfg = next(c for c in configs if c.player_type == "jwplayer")

    ladder = jw_cfg.quality_ladder
    assert len(ladder) == 3
    # Ladder should be sorted by resolution or bitrate (highest first or lowest first)
    heights = [tier.height for tier in ladder if tier.height is not None]
    assert set(heights) == {360, 720, 1080}

    top_tier = max(ladder, key=lambda t: t.height or 0)
    assert top_tier.label == "1080p"
    assert top_tier.height == 1080
    assert top_tier.width == 1920
    assert top_tier.bitrate == 5000000
    assert top_tier.url == "https://media.example.org/video-1080p.mp4"


def test_videojs_extraction_both_modes():
    """AC 1 & 2: VideoJS config from data-setup attribute and videojs() call."""
    configs = extract_player_configs(SAMPLE_VIDEOJS_HTML)
    vjs_configs = [c for c in configs if c.player_type == "videojs"]
    assert len(vjs_configs) >= 2, f"Expected 2 VideoJS configs, got {len(vjs_configs)}"

    # data-setup config
    hls_cfg = next(c for c in vjs_configs if any("m3u8" in s.url for s in c.sources))
    assert hls_cfg.sources[0].url == "https://cdn.example.com/hls/master.m3u8?token=sig_abc123"
    assert hls_cfg.sources[0].mime_type == "application/x-mpegURL"

    # JS call config
    vod_cfg = next(c for c in vjs_configs if len(c.sources) == 2)
    labels = {s.quality_label for s in vod_cfg.sources}
    assert labels == {"720p", "1080p"}


def test_auth_token_extraction():
    """Extract auth tokens from config fields and query parameters."""
    # From JWPlayer setup config
    jw_configs = extract_player_configs(SAMPLE_JWPLAYER_HTML)
    jw_cfg = jw_configs[0]
    assert "token" in jw_cfg.auth_tokens or "jwt_sec_token_9876543210" in jw_cfg.auth_tokens.values()

    # From VideoJS URL query param
    vjs_configs = extract_player_configs(SAMPLE_VIDEOJS_HTML)
    hls_source = next(s for c in vjs_configs for s in c.sources if "m3u8" in s.url)
    assert hls_source.auth_token == "sig_abc123"

    # From generic initial state
    generic_configs = extract_player_configs(SAMPLE_GENERIC_PLAYER_JS)
    assert len(generic_configs) >= 1
    g_cfg = generic_configs[0]
    tokens = g_cfg.auth_tokens
    assert "bearer-token-alpha-99" in tokens.values() or "accessToken" in tokens
    assert "sig-hex-778899" in tokens.values() or "signature" in tokens


def test_zero_live_playback_simulation():
    """AC 3: Verify extraction operates purely on static text without network/browser."""
    # Ensure purely synchronous offline parsing
    raw_js = 'jwplayer().setup({ file: "https://offline.test/stream.mp4", label: "480p", height: 480 });'
    configs = extract_player_configs(raw_js)
    assert len(configs) == 1
    assert configs[0].sources[0].url == "https://offline.test/stream.mp4"
    assert configs[0].sources[0].height == 480


def test_resilient_parsing_on_malformed_and_empty():
    """Parser must not crash on malformed JS, non-matching text, or empty input."""
    assert extract_player_configs("") == []
    assert extract_player_configs("<html><body>No player here</body></html>") == []
    assert extract_player_configs("jwplayer('vid').setup({ broken json syntax ...") == []

    parsed = parse_js_object("{ unquotedKey: 'single_quote_val', boolVal: true, num: 123, }")
    assert parsed == {"unquotedKey": "single_quote_val", "boolVal": True, "num": 123}


# ---- fixer (O928): correctness REFUTE E1/E2 ------------------------------

def test_e1_non_numeric_height_or_bitrate_never_takes_the_extractor_down():
    """E1: real players emit height "auto"/"720p" and bitrate "2500k"; one
    such field must not raise out of extract_player_configs. The source is
    kept, the numeric part is used when there is one, else None."""
    page = """
    <script>
    jwplayer("p").setup({sources: [
        {file: "https://cdn.example.com/auto.m3u8", height: "auto", bitrate: "2500k", label: "Auto"},
        {file: "https://cdn.example.com/720.mp4", height: "720p", width: "1280px", bitrate: 1800.0},
        {file: "https://cdn.example.com/0.mp4", height: 0, bitrate: ""}
    ]});
    </script>
    <video data-setup='{"sources":[{"src":"https://cdn.example.com/v.m3u8","type":"application/x-mpegURL","height":"720p"}]}'></video>
    """
    configs = extract_player_configs(page)
    jw = next(c for c in configs if c.player_type == "jwplayer")
    by_url = {s.url: s for s in jw.sources}
    assert set(by_url) == {"https://cdn.example.com/auto.m3u8", "https://cdn.example.com/720.mp4", "https://cdn.example.com/0.mp4"}
    assert by_url["https://cdn.example.com/auto.m3u8"].height is None
    assert by_url["https://cdn.example.com/auto.m3u8"].bitrate == 2500
    assert by_url["https://cdn.example.com/720.mp4"].height == 720
    assert by_url["https://cdn.example.com/720.mp4"].width == 1280
    assert by_url["https://cdn.example.com/720.mp4"].bitrate == 1800
    assert by_url["https://cdn.example.com/0.mp4"].height is None and by_url["https://cdn.example.com/0.mp4"].bitrate is None
    vjs = next(c for c in configs if c.player_type == "videojs")
    assert vjs.sources[0].height == 720
    # positive control: a plain integer still parses as before
    assert extract_player_configs('jwplayer("p").setup({sources:[{file:"https://cdn.example.com/x.mp4", height: 1080}]});')[0].sources[0].height == 1080


def test_e2_entity_escaped_data_setup_is_decoded_before_parsing():
    """E2: JSON inside a double-quoted data-setup attribute arrives as
    &quot;/&amp; from every HTML-escaping template; it must yield the same
    config as the single-quoted form (control)."""
    escaped = ('<video class="video-js" data-setup="{&quot;sources&quot;:[{&quot;src&quot;:'
               '&quot;https://cdn.example.com/hls/master.m3u8?token=sig_abc123&amp;e=1&quot;,'
               '&quot;type&quot;:&quot;application/x-mpegURL&quot;}]}"></video>')
    plain = ('<video class="video-js" data-setup=\'{"sources":[{"src":'
             '"https://cdn.example.com/hls/master.m3u8?token=sig_abc123&e=1",'
             '"type":"application/x-mpegURL"}]}\'></video>')
    got, control = extract_player_configs(escaped), extract_player_configs(plain)
    assert len(control) == 1 and control[0].sources[0].url == "https://cdn.example.com/hls/master.m3u8?token=sig_abc123&e=1"
    assert len(got) == 1 and got[0].player_type == "videojs"
    assert got[0].sources[0].url == control[0].sources[0].url
    assert got[0].sources[0].mime_type == "application/x-mpegURL"
    assert got[0].sources[0].auth_token == control[0].sources[0].auth_token == "sig_abc123"
