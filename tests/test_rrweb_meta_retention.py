"""rrweb Meta (type-4) retention — tests.

Verifies type-4 Meta events (viewport width/height + URL/navigation href) are
retained as ``event_type="meta"`` records, that FullSnapshot/Incremental handling
is unchanged, that downstream consumers tolerate Meta, that exports stay valid,
and that template generation still works. Browser-free (pure mapping + ingest +
export); the live injection path is not exercised (no live capture testing).
"""
import json
import sys
import tempfile
import zipfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools"))

from bulk_downloader import dom_recorder as DR
from bulk_downloader.dom_capture import DomCapture
from bulk_downloader.wacz_export import write_wacz, verify_wacz_bytes


# ── mapper: Meta retained ─────────────────────────────────────────
def test_meta_event_mapped():
    ev = {"type": 4, "timestamp": 111,
          "data": {"href": "https://app.reptyle.com/movies/9?t=tok#frag",
                   "width": 1280, "height": 720}}
    kw = DR.rrweb_to_record_kwargs(ev)
    assert kw is not None and kw["event_type"] == "meta"
    assert kw["timestamp"] == 111 and kw["is_full_snapshot"] is False
    assert kw["data"]["width"] == 1280 and kw["data"]["height"] == 720
    assert kw["data"]["href"].startswith("https://app.reptyle.com/movies/9")


def test_meta_partial_data():
    # missing keys are simply absent, no crash
    kw = DR.rrweb_to_record_kwargs({"type": 4, "data": {"width": 800}})
    assert kw["event_type"] == "meta" and kw["data"] == {"width": 800}


# ── existing handling unchanged ───────────────────────────────────
def test_full_snapshot_mapping_unchanged():
    kw = DR.rrweb_to_record_kwargs({"type": 2, "timestamp": 1, "data": {"node": {}}})
    assert kw["is_full_snapshot"] is True and kw["source"] == -1
    assert "event_type" not in kw  # full snapshots are not relabeled


def test_incremental_mapping_unchanged():
    kw = DR.rrweb_to_record_kwargs({"type": 3, "timestamp": 2, "data": {"source": 0}})
    assert kw["is_full_snapshot"] is False and kw["source"] == 0
    assert "event_type" not in kw


# ── ingest: record_dom_event Meta shape + redaction ───────────────
def test_record_meta_event_shape_and_types():
    cap = DomCapture(url="https://app.reptyle.com", redact=False)
    cap.record_dom_event(source=-1, event_type="meta",
                         data={"href": "https://app.reptyle.com/m/9?t=x", "width": 100, "height": 50},
                         timestamp=1)
    cap.record_dom_event(source=-1, is_full_snapshot=True, data={"node": {}}, timestamp=2)
    cap.record_dom_event(source=0, data={"source": 0}, timestamp=3)
    types = [e["type"] for e in cap.dom_log]
    assert types == ["meta", "full_snapshot", "incremental"]
    # redact=False keeps the full href (incl. query)
    assert cap.dom_log[0]["data"]["href"].endswith("?t=x")


def test_meta_href_query_stripped_under_redaction():
    cap = DomCapture(url="https://app.reptyle.com", redact=True)
    cap.record_dom_event(source=-1, event_type="meta",
                         data={"href": "https://app.reptyle.com/m/9?token=SECRET#f",
                               "width": 100, "height": 50}, timestamp=1)
    href = cap.dom_log[0]["data"]["href"]
    assert href == "https://app.reptyle.com/m/9", href
    assert "token" not in href and "SECRET" not in href
    # viewport preserved
    assert cap.dom_log[0]["data"]["width"] == 100


# ── mixed streams ─────────────────────────────────────────────────
def _feed(cap, events):
    for ev in events:
        kw = DR.rrweb_to_record_kwargs(ev)
        if kw is not None:
            cap.record_dom_event(**kw)


def test_mixed_meta_fullsnapshot_stream():
    cap = DomCapture(redact=True)
    _feed(cap, [
        {"type": 4, "timestamp": 1, "data": {"href": "https://x/y", "width": 9, "height": 9}},
        {"type": 2, "timestamp": 2, "data": {"node": {"tagName": "html"}}},
    ])
    types = [e["type"] for e in cap.dom_log]
    assert types == ["meta", "full_snapshot"]
    # the full snapshot is still extractable by a type-filtering consumer
    assert any(e["type"] == "full_snapshot" for e in cap.dom_log)


def test_mixed_meta_incremental_stream():
    cap = DomCapture(redact=True)
    _feed(cap, [
        {"type": 4, "timestamp": 1, "data": {"href": "https://x/y", "width": 9, "height": 9}},
        {"type": 3, "timestamp": 2, "data": {"source": 2}},
        {"type": 0, "timestamp": 3},  # load — dropped
    ])
    types = [e["type"] for e in cap.dom_log]
    assert types == ["meta", "incremental"]  # load dropped, others retained


# ── export compatibility ──────────────────────────────────────────
def _capture_with_meta():
    cap = DomCapture(url="https://app.reptyle.com", redact=True)
    cap.set_page_context(title="Reptyle")
    cap.record_dom_event(source=-1, event_type="meta",
                         data={"href": "https://app.reptyle.com/m/9", "width": 1280, "height": 720}, timestamp=1)
    cap.record_dom_event(source=-1, is_full_snapshot=True,
                         data={"node": {"tagName": "html"}, "html": "<html></html>"}, timestamp=2)
    cap.record_dom_event(source=0, data={"source": 0}, timestamp=3)
    return cap.to_capture_dict()


def test_export_compat_with_meta():
    capd = _capture_with_meta()
    with tempfile.TemporaryDirectory() as d:
        out = str(Path(d) / "cap.wacz")
        write_wacz(capd, out)
        wacz = Path(out).read_bytes()
        res = verify_wacz_bytes(wacz)
        assert res.get("ok") is True, res
        # meta survived serialization
        with zipfile.ZipFile(out) as z:
            name = next(n for n in z.namelist() if n.endswith("capture.json"))
            cap2 = json.loads(z.read(name))
        assert any(e.get("type") == "meta" for e in (cap2.get("dom_log") or []))


# ── template-generation compatibility ─────────────────────────────
def test_template_generation_tolerates_meta():
    import build_template_from_wacz as B
    capd = _capture_with_meta()
    # give the full snapshot real html so the builder has something to parse
    for e in capd["dom_log"]:
        if e["type"] == "full_snapshot":
            e["html"] = "<html><body><a download href='/x.mp4'>Download</a></body></html>"
    with tempfile.TemporaryDirectory() as d:
        out = str(Path(d) / "cap.wacz")
        write_wacz(capd, out)
        draft = B.build_template(Path(out))  # must not choke on the meta event
    assert isinstance(draft, dict)
    # the builder consumed full_snapshot(s); meta was ignored, not fatal
    assert draft.get("host") or draft.get("selectors") is not None
