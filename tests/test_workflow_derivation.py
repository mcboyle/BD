"""workflow derivation + trigger from timeline (A6-1) — characterization tests.

Pins what A6-1 ADDS to the builder: an observed `workflow.derived_steps` mined
from the capture timeline and a `download.trigger_candidate` = the element clicked
immediately before a modal opened. Synthetic serialized-node captures (the real
cloakbrowser encoding) with a scripted click->modal->download sequence. Structure
only — asserts no URL/value leaks. Browser-free; stdlib + project modules.
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools"))

import build_template_from_wacz as BTW
from bulk_downloader.wacz_export import write_wacz

_T = 1_000_000


def _el(tag, attrs=None, kids=None, _c=[100]):
    _c[0] += 1
    return {"id": _c[0], "type": 2, "tagName": tag,
            "attributes": attrs or {}, "childNodes": kids or []}


def _capture_click_then_modal():
    """full_snapshot WITHOUT a modal -> click the open button -> incremental
    mutation ADDS the modal. The trigger must resolve to the clicked button."""
    button = _el("button", {"class": "download-open"})         # id assigned by _el
    root = _el("div", {"class": "app"}, [button])
    bid = button["id"]
    modal = _el("div", {"class": "ant-modal", "role": "dialog"},
                [_el("ul", {}, [_el("li", {"role": "listitem"}) for _ in range(2)])])
    return {
        "host": "demo.example", "url": "https://demo.example/v/9", "title": "t",
        "network_log": [
            {"timestamp": _T + 3, "type": "xhr", "method": "GET",
             "url": "https://api.demo.example/v1/movie/9/download-resolution/1080",
             "response_status": 200},
            {"timestamp": _T + 4, "type": "media", "method": "GET",
             "url": "https://cdn.demo.example/9/file_1080.mp4", "response_status": 200},
        ],
        "dom_log": [
            {"timestamp": _T, "type": "meta", "data": {"href": "https://demo.example/v/9",
                                                       "width": 1, "height": 1}},
            {"timestamp": _T + 1, "type": "full_snapshot", "data": {"node": root}},
            {"timestamp": _T + 2, "type": "incremental",
             "data": {"source": 2, "type": 2, "id": bid}},          # MouseInteraction click
            {"timestamp": _T + 3, "type": "incremental",
             "data": {"source": 0, "adds": [{"parentId": root["id"], "node": modal}]}},  # modal opens
        ],
    }, bid


def test_trigger_candidate_from_click_before_modal():
    cap, bid = _capture_click_then_modal()
    with tempfile.TemporaryDirectory() as td:
        w = Path(td) / "c.wacz"
        write_wacz(cap, str(w))
        draft = BTW.build_template(w)
    dl = (draft.get("selectors") or {}).get("download") or {}
    assert dl.get("trigger_candidate") == "button.download-open"
    wf = draft.get("workflow") or {}
    assert wf.get("trigger_evidence")            # evidence recorded
    steps = wf.get("derived_steps") or []
    joined = " | ".join(steps)
    # ordered observation: navigate -> render -> click -> modal -> download
    assert "navigate" in joined and "render" in joined
    assert "interact: click" in joined and "modal" in joined
    assert "download-resolution" in joined and "media rendition" in joined
    assert steps.index(next(s for s in steps if "click" in s)) < \
           steps.index(next(s for s in steps if "modal" in s))


def test_no_trigger_when_modal_in_initial_snapshot():
    # modal already present at snapshot, no click event -> no trigger candidate
    modal = _el("div", {"class": "ant-modal", "role": "dialog"}, [])
    root = _el("div", {"class": "app"}, [_el("button", {"class": "x"}), modal])
    cap = {"host": "d.example", "url": "https://d.example/v", "title": "t",
           "network_log": [],
           "dom_log": [{"timestamp": _T, "type": "meta", "data": {"href": "https://d.example/v"}},
                       {"timestamp": _T + 1, "type": "full_snapshot", "data": {"node": root}}]}
    with tempfile.TemporaryDirectory() as td:
        w = Path(td) / "c.wacz"
        write_wacz(cap, str(w))
        draft = BTW.build_template(w)
    dl = (draft.get("selectors") or {}).get("download") or {}
    assert "trigger_candidate" not in dl


def test_selector_for_element_preferences():
    el = lambda **a: {"type": 2, "tagName": "button", "attributes": a}
    assert BTW._selector_for_element(el(id="dl-btn")) == "#dl-btn"
    assert BTW._selector_for_element(el(**{"class": "open-modal"})) == "button.open-modal"
    # generic state/layout class is skipped in favour of role
    assert BTW._selector_for_element(el(**{"class": "active", "role": "button"})) == 'button[role="button"]'
    # volatile id (long digit run) is rejected -> falls through to tag
    assert BTW._selector_for_element(el(id="x12345")) == "button"
    # non-element node -> None
    assert BTW._selector_for_element({"type": 3, "textContent": "hi"}) is None


def test_derived_steps_carry_no_values():
    cap, _ = _capture_click_then_modal()
    with tempfile.TemporaryDirectory() as td:
        w = Path(td) / "c.wacz"
        write_wacz(cap, str(w))
        draft = BTW.build_template(w)
    wf_blob = json.dumps(draft.get("workflow"))
    # the derived steps are structural labels — no urls/hosts/file names
    for needle in ("https://", "demo.example", "file_1080.mp4", "cdn."):
        assert needle not in wf_blob, f"value leaked into workflow: {needle}"


# ── A6.2: passive-capture effect-correlation fallback ─────────────────────────
# When NO modal-open is serialized (the common real-data case) and NO operator
# action_timeline exists, the trigger is the click IMMEDIATELY preceding the
# first download-resolution/manifest response. Fires only on a real download
# signal (never fabricates), resolves a structural selector, leaks no values.

def _capture_click_then_downloadapi_no_modal():
    """download button -> click -> download-resolution response AFTER the click;
    NO modal-open event, NO action_timeline. The effect-correlation fallback
    must resolve the trigger to the clicked button."""
    button = _el("button", {"class": "dl-trigger"})
    root = _el("div", {"class": "app"}, [button])
    bid = button["id"]
    return {
        "host": "demo.example", "url": "https://demo.example/v/9", "title": "t",
        "network_log": [
            {"timestamp": _T + 5, "type": "xhr", "method": "GET",
             "url": "https://api.demo.example/v1/movie/9/download-resolution/1080",
             "response_status": 200},
        ],
        "dom_log": [
            {"timestamp": _T, "type": "meta",
             "data": {"href": "https://demo.example/v/9", "width": 1, "height": 1}},
            {"timestamp": _T + 1, "type": "full_snapshot", "data": {"node": root}},
            {"timestamp": _T + 4, "type": "incremental",
             "data": {"source": 2, "type": 2, "id": bid}},   # MouseInteraction click
        ],
    }, bid


def test_passive_trigger_from_click_before_download_api():
    cap, bid = _capture_click_then_downloadapi_no_modal()
    with tempfile.TemporaryDirectory() as td:
        w = Path(td) / "c.wacz"
        write_wacz(cap, str(w))
        draft = BTW.build_template(w)
    dl = (draft.get("selectors") or {}).get("download") or {}
    assert dl.get("trigger_candidate") == "button.dl-trigger"
    wf = draft.get("workflow") or {}
    assert "effect-correlated" in (wf.get("trigger_evidence") or "")


def test_passive_no_trigger_without_download_signal():
    # strip the download-api response -> NO media signal -> must NOT fabricate
    cap, bid = _capture_click_then_downloadapi_no_modal()
    cap["network_log"] = [{"timestamp": _T + 5, "type": "xhr", "method": "GET",
                           "url": "https://api.demo.example/v1/ping",
                           "response_status": 200}]
    with tempfile.TemporaryDirectory() as td:
        w = Path(td) / "c.wacz"
        write_wacz(cap, str(w))
        draft = BTW.build_template(w)
    dl = (draft.get("selectors") or {}).get("download") or {}
    assert "trigger_candidate" not in dl


def test_passive_trigger_is_value_free():
    cap, _ = _capture_click_then_downloadapi_no_modal()
    res = BTW._derive_workflow_timeline(cap["dom_log"], cap["network_log"])
    blob = json.dumps(res)
    # genuine value leaks only — NOT the structural step label, which legitimately
    # contains the words "download-resolution / manifest response observed".
    for needle in ("https://", "demo.example", "movie/9", "/1080"):
        assert needle not in blob, f"value leaked into trigger result: {needle}"


def test_passive_trigger_resolves_text_target_to_ancestor():
    """increment-2a: rrweb records the click on the TEXT node inside the button.
    The text node doesn't resolve; the fallback must walk up to the button and
    flag that it resolved via an ancestor (review-only weak signal)."""
    text = {"id": 9001, "type": 3, "textContent": "Download"}
    button = _el("button", {"class": "dl-trigger"}, [text])   # text is a child of the button
    root = _el("div", {"class": "app"}, [button])
    return_cap = {
        "host": "demo.example", "url": "https://demo.example/v/9", "title": "t",
        "network_log": [
            {"timestamp": _T + 5, "type": "xhr", "method": "GET",
             "url": "https://api.demo.example/v1/movie/9/download-resolution/1080",
             "response_status": 200},
        ],
        "dom_log": [
            {"timestamp": _T, "type": "meta",
             "data": {"href": "https://demo.example/v/9", "width": 1, "height": 1}},
            {"timestamp": _T + 1, "type": "full_snapshot", "data": {"node": root}},
            {"timestamp": _T + 4, "type": "incremental",
             "data": {"source": 2, "type": 2, "id": 9001}},   # click on the TEXT node
        ],
    }
    with tempfile.TemporaryDirectory() as td:
        w = Path(td) / "c.wacz"
        write_wacz(return_cap, str(w))
        draft = BTW.build_template(w)
    dl = (draft.get("selectors") or {}).get("download") or {}
    assert dl.get("trigger_candidate") == "button.dl-trigger"      # walked up from the text node
    wf = draft.get("workflow") or {}
    assert "nearest clickable ancestor" in (wf.get("trigger_evidence") or "")
