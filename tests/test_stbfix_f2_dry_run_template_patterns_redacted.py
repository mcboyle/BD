"""stb-fix F2: template_dry_run's ``template`` summary must not carry
unredacted network patterns. ``network_patterns`` was already redacted, but
``template.patterns`` (from template_summary) echoed the raw values, so a
signed/token-bearing pattern reached the dry-run response verbatim."""
from __future__ import annotations

import json

from bulk_downloader import dry_run
from bulk_downloader import template_registry as tr

BD_GATE_SCOPE = "module"

SECRETS = ("SECRET123", "SIGVALUE", "hunter2")
FAKE = {
    "host": "x.test",
    "status": "enabled",
    "selectors": {"download": {"trigger": "a.dl"}},
    "resolutions": [1080],
    "network_patterns": [
        "media/clip?token=SECRET123",
        "cdn/hls/master.m3u8#sig=SIGVALUE",
        "api/secret-hunter2/video",
        "api/video",
    ],
}


def _run(monkeypatch, template):
    monkeypatch.setattr(tr, "find_template_for_url",
                        lambda url, html=None: template)
    return dry_run.template_dry_run("https://x.test/watch/1")


def test_template_summary_patterns_are_redacted(monkeypatch):
    res = _run(monkeypatch, FAKE)
    assert res["template_matched"] is True
    blob = json.dumps(res)
    leaked = [s for s in SECRETS if s in blob]
    assert not leaked, (
        f"F2_TEMPLATE_PATTERNS_UNREDACTED: {leaked} in "
        f"template.patterns={res['template']['patterns']!r}")
    assert res["template"]["patterns"] == res["network_patterns"]
    assert res["template"]["patterns"] == [
        "media/clip", "cdn/hls/master.m3u8", "api/<redacted>/video", "api/video"]


def test_summary_other_fields_unchanged(monkeypatch):
    res = _run(monkeypatch, FAKE)
    assert res["template"]["enabled"] is True
    assert res["template"]["host"] == "x.test"
    assert res["template"]["selectors"] == ["download"]
    assert res["template"]["resolutions"] == [1080]


def test_no_template_summary_unchanged(monkeypatch):
    res = _run(monkeypatch, None)
    assert res["template_matched"] is False
    assert res["template"] == {"enabled": False, "host": None,
                               "selectors": [], "resolutions": [],
                               "patterns": []}
    assert res["network_patterns"] == []
