"""bulk_downloader.client_config -- Row 898: standardized client profile.

Headless browser instances in this codebase already pick RANDOMIZED
fingerprint attributes per site (``constants.make_fingerprint``) and
randomized per-call canvas/audio noise (``constants.STEALTH_JS``) -- both
deliberate anti-fingerprinting variance for evading bot detection. That
variance is the wrong default for runner consistency: a task that measures
rendering or DOM behavior across successive headless instances needs the
SAME canvas, WebGL, audio-context and header profile every time, not a fresh
roll. This module is that fixed profile -- a standard consumer
configuration, not a random one -- for callers that need reproducibility
rather than fingerprint diversity. It does not replace or disable the
existing stealth/fingerprint machinery; it is a separate, deterministic
profile for callers that opt into it.
"""
from __future__ import annotations

CANVAS_PROFILE = {
    "width": 300,
    "height": 150,
    "pixel_ratio": 1.0,
}

WEBGL_PROFILE = {
    "vendor": "Intel Inc.",
    "renderer": "Intel Iris OpenGL Engine",
    "version": "WebGL 1.0",
    "shading_language_version": "WebGL GLSL ES 1.0",
}

AUDIO_PROFILE = {
    "sample_rate": 44100,
    "base_latency": 0.01,
    "output_latency": 0.02,
}

STANDARD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-CH-UA": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-CH-UA-Platform": '"Windows"',
}


def standard_profile() -> dict:
    """Return the canonical client configuration profile -- canvas, WebGL,
    audio-context and header attributes -- identical on every call, so
    successive headless instances render and report the same values."""
    return {
        "canvas": dict(CANVAS_PROFILE),
        "webgl": dict(WEBGL_PROFILE),
        "audio": dict(AUDIO_PROFILE),
        "headers": dict(STANDARD_HEADERS),
    }
