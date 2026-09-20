"""RED-first tests for Row 898: client configuration profile standardization.

bulk_downloader.client_config.standard_profile() returns a fixed canvas /
WebGL / audio-context / header profile, identical across calls, so
successive headless instances render and report the same values (unlike the
existing randomized fingerprint pool in bulk_downloader.constants, which is
deliberate anti-fingerprinting variance).
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"


def test_uniform_client_profile_attributes_across_successive_instances():
    from bulk_downloader import client_config as CC
    a = CC.standard_profile()
    b = CC.standard_profile()
    assert a == b
    assert a is not b
    assert a["canvas"] is not b["canvas"]


def test_valid_webgl_context_generation():
    from bulk_downloader import client_config as CC
    webgl = CC.standard_profile()["webgl"]
    assert webgl["vendor"] and isinstance(webgl["vendor"], str)
    assert webgl["renderer"] and isinstance(webgl["renderer"], str)
    assert webgl["version"].startswith("WebGL")


def test_consistent_dom_metrics():
    from bulk_downloader import client_config as CC
    profiles = [CC.standard_profile() for _ in range(5)]
    canvases = [p["canvas"] for p in profiles]
    assert all(c == canvases[0] for c in canvases)
    assert canvases[0]["width"] > 0 and canvases[0]["height"] > 0
    audios = [p["audio"] for p in profiles]
    assert all(a == audios[0] for a in audios)


def test_standard_headers_present():
    from bulk_downloader import client_config as CC
    headers = CC.standard_profile()["headers"]
    assert "User-Agent" in headers
    assert "Accept-Language" in headers
