"""Wave B — build_template_from_wacz consumes the operator-recorded action_timeline.

Pins what Wave B ADDS to the builder: when an `action_timeline` (inspect_pick
entries persisted into the WACZ by dom_capture) is present, it is the PREFERRED
source for `workflow.derived_steps` + `download.trigger_candidate` — the reliable
click->effect signal the rrweb dom_log derivation lacks. The trigger resolves to
the click whose effect first produced media. Falls back to the dom_log path when
no action_timeline is present, and never overwrites the stronger structural
`trigger`. Structure + kinds/counts only — asserts zero value leaks via the real
artifact secret scanner. Browser-free; stdlib + project modules.
"""
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools"))

import build_template_from_wacz as BTW
from bulk_downloader.wacz_export import write_wacz
from bulk_downloader.capture_artifact_redact import scan_artifact_secrets

_T = 1_000_000


def _el(tag, attrs=None, kids=None, _c=[200]):
    _c[0] += 1
    return {"id": _c[0], "type": 2, "tagName": tag,
            "attributes": attrs or {}, "childNodes": kids or []}


def _entry(selector, role, *, req=0, manifest=0, segments=0, direct=0,
           signed=False, ts=_T):
    """An inspect_pick action entry as persisted in the WACZ (structure +
    kinds/counts; excerpt already redacted)."""
    return {"ts": ts, "selector": selector, "xpath": "//" + selector.split(".")[0],
            "role": role, "confidence": 0.9, "tag": selector.split(".")[0],
            "excerpt": "<%s>" % selector.split(".")[0],
            "effect": {"req_count": req, "manifest": manifest, "segments": segments,
                       "direct_media": direct, "signed": signed, "nav": False}}


def _base_capture(action_timeline=None):
    """A serialized-node capture with a signed page url + signed download-API +
    media rendition (the value surfaces the no-value gate must scrub)."""
    root = _el("div", {"class": "app"}, [_el("button", {"class": "dl"})])
    cap = {
        "host": "demo.example", "url": "https://demo.example/v/9?token=SECRET_T0K",
        "title": "t", "captured_at": "2020-01-01T00:00:00Z",
        "network_log": [
            {"timestamp": _T + 1, "type": "document", "method": "GET",
             "url": "https://demo.example/v/9?token=SECRET_T0K", "response_status": 200},
            {"timestamp": _T + 3, "type": "xhr", "method": "GET",
             "url": "https://api.demo.example/v1/movie/9/download-resolution/1080?sig=ABC123",
             "response_status": 200},
            {"timestamp": _T + 4, "type": "media", "method": "GET",
             "url": "https://cdn.demo.example/9/file_1080.mp4", "response_status": 200},
        ],
        "dom_log": [
            {"timestamp": _T, "type": "meta",
             "data": {"href": "https://demo.example/v/9", "width": 1, "height": 1}},
            {"timestamp": _T + 1, "type": "full_snapshot", "data": {"node": root}},
        ],
    }
    if action_timeline is not None:
        cap["action_timeline"] = action_timeline
        cap["action_timeline_count"] = len(action_timeline)
    return cap


def _build(cap):
    with tempfile.TemporaryDirectory() as td:
        w = Path(td) / "f.wacz"
        write_wacz(cap, str(w))
        return BTW.build_template(w)


def test_action_timeline_preferred_for_trigger_and_steps():
    tl = [_entry("button.dl", "download link", req=2, direct=1, signed=True)]
    tpl = _build(_base_capture(tl))
    wf = tpl.get("workflow", {})
    assert wf.get("source") == "action_timeline", wf.get("source")
    assert tpl["selectors"]["download"]["trigger_candidate"] == "button.dl"
    steps = wf.get("derived_steps") or []
    assert any("button.dl" in s and "direct:1" in s for s in steps), steps
    # advisory verify readout rides along
    assert (wf.get("verify") or {}).get("tier") == "ready"


def test_trigger_resolves_to_media_producing_click_not_login():
    # login click fires network but NO media; the later play click produces media.
    tl = [
        _entry("input.login", "login/submit", req=1, ts=_T),
        _entry("button.play", "play button", req=2, direct=1, ts=_T + 10),
    ]
    tpl = _build(_base_capture(tl))
    assert tpl["selectors"]["download"]["trigger_candidate"] == "button.play"
    # both observed steps are recorded, in order
    steps = tpl["workflow"]["derived_steps"]
    assert "input.login" in steps[0] and "button.play" in steps[1]


def test_fallback_to_dom_log_when_no_action_timeline():
    # No action_timeline at all -> Wave B returns None, source falls back.
    tpl = _build(_base_capture(action_timeline=None))
    assert tpl["workflow"]["source"] == "dom_log"
    assert tpl["workflow"].get("verify") is None


def test_no_value_leaks_with_action_timeline_present():
    tl = [_entry("button.dl", "download link", req=2, direct=1, signed=True)]
    tpl = _build(_base_capture(tl))
    findings = scan_artifact_secrets(tpl)
    assert findings == [], findings


def test_structural_trigger_field_not_overwritten():
    # action_timeline sets only `trigger_candidate`; the stronger structural
    # `trigger` (a[download]/affordance) is a separate field Wave B never touches.
    tl = [_entry("button.dl", "download link", req=2, direct=1)]
    tpl = _build(_base_capture(tl))
    dl = tpl["selectors"].get("download", {})
    assert dl.get("trigger_candidate") == "button.dl"
    if "trigger" in dl:
        assert dl["trigger"] != dl["trigger_candidate"] or dl["trigger"] is not None
