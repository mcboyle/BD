"""v3.66.139 — rrweb/snapdom capture wiring (dom_recorder).

Covers the pure, browser-free surface: the rrweb-event -> record_dom_event
mapping, that ingest applies PII redaction to rrweb-shaped nodes, the snapdom
snapshot sink, and vendored-asset integrity. The live injection round-trip
(Playwright add_init_script + binding) needs a real browser and is not
exercised here — same boundary as session_capture.capture_via_cdp.
"""
from bulk_downloader import dom_recorder
from bulk_downloader.dom_capture import DomCapture


def test_full_snapshot_maps_to_full():
    ev = {"type": 2, "timestamp": 111,
          "data": {"node": {"id": 1, "type": 2, "tagName": "div",
                            "attributes": {}, "childNodes": []},
                   "initialOffset": {"top": 0, "left": 0}}}
    kw = dom_recorder.rrweb_to_record_kwargs(ev)
    assert kw is not None
    assert kw["is_full_snapshot"] is True
    assert kw["source"] == -1
    assert kw["timestamp"] == 111
    assert "node" in kw["data"]


def test_incremental_carries_source():
    ev = {"type": 3, "timestamp": 222,
          "data": {"source": 5, "id": 9, "text": "hello"}}
    kw = dom_recorder.rrweb_to_record_kwargs(ev)
    assert kw["is_full_snapshot"] is False
    assert kw["source"] == 5
    assert kw["data"]["text"] == "hello"


def test_non_dom_events_dropped():
    # type 4 (Meta) is now RETAINED (see test_meta_* below); load/custom/plugin
    # (0/1/5/6) and non-dicts still carry nothing we use and map to None.
    assert dom_recorder.rrweb_to_record_kwargs({"type": 0}) is None
    assert dom_recorder.rrweb_to_record_kwargs({"type": 1, "data": {}}) is None
    assert dom_recorder.rrweb_to_record_kwargs({"type": 5, "data": {}}) is None
    assert dom_recorder.rrweb_to_record_kwargs({"type": 6, "data": {}}) is None
    assert dom_recorder.rrweb_to_record_kwargs("not-a-dict") is None
    assert dom_recorder.rrweb_to_record_kwargs({"type": 3, "data": {"source": "bad"}})["source"] == -1


def test_ingest_redacts_masked_node_full_snapshot():
    node = {"id": 1, "type": 2, "tagName": "div",
            "attributes": {"class": "bd-mask"},
            "textContent": "secret@email.com", "childNodes": []}
    ev = {"type": 2, "timestamp": 111, "data": {"node": node}}
    cap = DomCapture(redact=True)
    stored = cap.record_dom_event(**dom_recorder.rrweb_to_record_kwargs(ev))
    assert stored["type"] == "full_snapshot"
    n = stored["data"]["node"]
    assert n["_bd_redacted"] == "mask"
    assert n["textContent"] == "*" * 8
    assert "secret" not in n["textContent"]


def test_ingest_blocks_subtree_in_mutation_adds():
    add_node = {"id": 2, "type": 2, "tagName": "span",
                "attributes": {"class": "bd-block"},
                "childNodes": [{"type": 3, "textContent": "leak"}]}
    ev = {"type": 3, "timestamp": 222,
          "data": {"source": 0,
                   "adds": [{"parentId": 1, "nextId": None, "node": add_node}]}}
    cap = DomCapture(redact=True)
    stored = cap.record_dom_event(**dom_recorder.rrweb_to_record_kwargs(ev))
    assert stored["type"] == "incremental" and stored["source"] == 0
    blocked = stored["data"]["adds"][0]["node"]
    assert blocked["_bd_redacted"] == "block"
    assert blocked["childNodes"] == []


def test_snapshot_sink_in_capture_dict():
    cap = DomCapture(redact=True)
    cap.record_dom_event(**dom_recorder.rrweb_to_record_kwargs(
        {"type": 2, "timestamp": 1, "data": {"node": {"type": 2, "tagName": "html",
                                                       "attributes": {}, "childNodes": []}}}))
    cap.record_dom_snapshot("data:image/png;base64,AAAA", label="end")
    out = cap.to_capture_dict()
    assert out["dom_log_count"] == 1
    assert out["dom_snapshot_count"] == 1
    assert out["dom_snapshots"][0]["label"] == "end"
    assert out["dom_snapshots"][0]["image"].startswith("data:image/png")


def test_no_snapshots_key_when_none_taken():
    cap = DomCapture(redact=True)
    out = cap.to_capture_dict()
    assert "dom_snapshots" not in out  # absent unless something was recorded


def test_vendored_assets_present_and_bootstrap_wired():
    st = dom_recorder.get_status()
    assert st["rrweb_present"] and st["rrweb_bytes"] > 100_000
    assert st["snapdom_present"] and st["snapdom_bytes"] > 50_000
    script = dom_recorder.recorder_script()
    assert "rrweb.record" in script
    # v3.66.165: emit buffers into the in-page __bd_dom_buf (drained by
    # drain_dom_events), replacing the old __bd_dom_event Playwright binding.
    assert "__bd_dom_buf" in script
    assert "__bd_dom_event" not in script
    # redaction classes are configured browser-side
    assert "bd-mask" in script and "bd-block" in script
