"""v3.66.652 -- S3.1 final: live EME page-hook recorder (DETECTION ONLY).

eme_detect installs a document-start hook on every BrowserContext that records
navigator.requestMediaKeySystemAccess calls on window.__bd_eme, then calls through.
classify_eme_records / summarize_eme map the recorded key-systems to protection
categories via drm_detect. Detection only -- never requests a license, drives a CDM,
or alters playback. Closes the case of EME pages with no parseable manifest.

Deterministic units: pure classification + the ungated install wiring. (The live
browser path is exercised on-stash; the JS hook is a document-start constant.)
"""
from __future__ import annotations

import bulk_downloader.eme_detect as eme
import bulk_downloader.drm_detect as dd


def test_classify_eme_records_maps_systems():
    recs = [{"keySystem": "com.widevine.alpha"},
            {"keySystem": "org.w3.clearkey"},
            {"keySystem": "com.microsoft.playready"}]
    out = eme.classify_eme_records(recs)
    cats = {c["key_system"]: c["category"] for c in out}
    assert cats["com.widevine.alpha"] == dd.CAT_CDM, out
    assert cats["org.w3.clearkey"] == dd.CAT_CLEARKEY, out
    assert cats["com.microsoft.playready"] == dd.CAT_CDM, out


def test_summarize_eme_takes_strongest_category():
    recs = [{"keySystem": "org.w3.clearkey"},
            {"keySystem": "com.widevine.alpha"}]
    s = eme.summarize_eme(recs)
    assert s["eme_used"] is True
    assert s["category"] == dd.CAT_CDM, s          # cdm-drm outranks clearkey
    assert "widevine" in s["systems"] and "clearkey" in s["systems"]


def test_summarize_eme_empty_is_not_used():
    s = eme.summarize_eme([])
    assert s == {"eme_used": False, "category": "none", "systems": []}, s


def test_eme_init_js_is_observation_only():
    # The hook records + calls through; it must not request a license / drive a CDM.
    js = eme.EME_INIT_JS
    assert "requestMediaKeySystemAccess" in js
    assert "__bd_eme" in js
    for forbidden in ("createSession", "generateRequest", "setServerCertificate",
                      "update("):
        assert forbidden not in js, f"hook must not {forbidden}"


def test_install_stealth_installs_eme_recorder_even_when_stealth_off():
    from bulk_downloader.runner_browser import BrowserMixin
    scripts = []

    class Ctx:
        def add_init_script(self, s):
            scripts.append(s)

    class FakeRunner:
        config = {"use_stealth": False}   # stealth OFF -> EME must STILL install

    BrowserMixin._install_stealth(FakeRunner(), Ctx())
    assert eme.EME_INIT_JS in scripts, "EME recorder must install independent of stealth"
    # stealth itself is gated off, so only the EME script is present
    from bulk_downloader.constants import STEALTH_JS
    assert STEALTH_JS not in scripts
