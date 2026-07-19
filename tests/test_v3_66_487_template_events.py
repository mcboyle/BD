"""v3.66.487 E1 (plugin-v3, non-guard slice): template lifecycle events.

Widens the plugin event surface at a clean, sandbox-verifiable producer:
``template_manager.promote_draft``. Every successful promote makes a draft a
**reviewed** template (``template.reviewed``); a promote with ``enable=True``
additionally takes it **live** (``template.promoted``). Both fire through the
canonical ``plugins.emit(event, payload)`` producer seam (documented-event
validation + the isolated fire_hook path), so a throwing consumer never breaks
a promote.

This is the first, fully in-sandbox-verifiable slice of E1's non-guard event
surface. The remaining non-guard events (queue.*, vpn.*, review.*,
download.progress/retry) sit at producers that are not clean single-function
seams and/or need on-stash live-fire to verify; the ``capture.*`` events tap the
``session_capture.py`` release guard and ship in their own declared guard cut.
Those are deferred -- NOT silently registered -- so the golden never advertises
an event no producer fires.

Runner-safe: zero-arg fns, no pytest builtins, paths from __file__, tempfile.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
for _p in (str(_REPO), str(_REPO / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from bulk_downloader import plugins as P  # noqa: E402
from bulk_downloader.template_manager import promote_draft  # noqa: E402

_WELLFORMED_RAW = {
    "schema": "bulk_downloader.template_draft.v1",
    "host": "example.com",
    "selectors": {"download": {"button": "button.download-btn"}},
    "network_patterns": ["https://example.com/video/play_1080.mp4"],
    "resolutions": [1080, 720],
}


def _stage(host="example.com"):
    dd = tempfile.mkdtemp()
    rd = tempfile.mkdtemp()
    fn = f"{host}.template-draft.json"
    with open(os.path.join(dd, fn), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(_WELLFORMED_RAW))
    return fn, dd, rd


def test_emit_helper_exists_and_validates():
    """plugins.emit is the canonical producer seam; an undocumented event warns
    but still fires (a producer is never silently dropped)."""
    P.reset()
    seen = {"n": 0}
    P.register_hook("totally.unknown.event", lambda p: seen.__setitem__("n", seen["n"] + 1))
    P.emit("totally.unknown.event", {"x": 1})
    assert seen["n"] == 1
    P.reset()


def test_promote_enabled_fires_reviewed_and_promoted():
    """enable=True -> template.reviewed AND template.promoted each fire once."""
    P.reset()
    got = {"reviewed": [], "promoted": []}
    P.register_hook("template.reviewed", lambda p: got["reviewed"].append(p))
    P.register_hook("template.promoted", lambda p: got["promoted"].append(p))
    fn, dd, rd = _stage()
    res = promote_draft(fn, enable=True, reviewed_dir=rd, drafts_dir=dd)
    assert res.get("ok"), res
    assert len(got["reviewed"]) == 1, got
    assert len(got["promoted"]) == 1, got
    rp = got["reviewed"][0]
    assert rp.get("host") == "example.com" and rp.get("enabled") is True
    assert "filename" in rp and "ts" in rp
    assert got["promoted"][0].get("host") == "example.com"
    P.reset()


def test_promote_disabled_fires_reviewed_not_promoted():
    """enable=False -> template.reviewed fires, template.promoted does NOT."""
    P.reset()
    got = {"reviewed": 0, "promoted": 0}
    P.register_hook("template.reviewed", lambda p: got.__setitem__("reviewed", got["reviewed"] + 1))
    P.register_hook("template.promoted", lambda p: got.__setitem__("promoted", got["promoted"] + 1))
    fn, dd, rd = _stage()
    res = promote_draft(fn, enable=False, reviewed_dir=rd, drafts_dir=dd)
    assert res.get("ok"), res
    assert got["reviewed"] == 1, got
    assert got["promoted"] == 0, got
    P.reset()


def test_throwing_consumer_does_not_break_promote():
    """Exception isolation: a throwing hook consumer never breaks the producer."""
    P.reset()

    def boom(payload):
        raise RuntimeError("consumer blew up")

    P.register_hook("template.promoted", boom)
    fn, dd, rd = _stage()
    res = promote_draft(fn, enable=True, reviewed_dir=rd, drafts_dir=dd)
    assert res.get("ok"), res     # promote still succeeds despite the bad consumer
    P.reset()


def test_new_events_documented_and_in_golden():
    """Both events are in HOOK_EVENTS AND pinned in the R3 golden (contract locked)."""
    ev = P.known_events()["hooks"]
    assert "template.reviewed" in ev and "template.promoted" in ev
    golden = json.load(open(_REPO / "tests" / "golden" / "hook_payloads.golden.json"))
    assert "template.reviewed" in golden and "template.promoted" in golden
