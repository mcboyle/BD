"""A-T2 — production capture: DOM/behavioral stream, PII redaction,
frame_path, storage deltas, body-cap policy, chunking, WACZ export.
"""

import json

import pytest

from bulk_downloader.dom_capture import (
    CaptureChunker,
    DomCapture,
    DEFAULT_BODY_CAP_BYTES,
    PII_BLOCK_CLASSES,
    PII_MASK_CLASSES,
    RR_INPUT,
    RR_MUTATION,
    redact_dom_node,
    should_capture_body,
)
from bulk_downloader.wacz_export import (
    WACZ_VERSION,
    build_wacz_bytes,
    verify_wacz_bytes,
)


# ── DOM node redaction ────────────────────────────────────────────

class TestDomNodeRedaction:

    def test_mask_class_masks_text(self):
        node = {"tagName": "span", "attributes": {"class": "bd-mask"},
                "textContent": "secret@example.com"}
        out = redact_dom_node(node)
        assert set(out["textContent"]) == {"*"}
        assert out["_bd_redacted"] == "mask"

    def test_rr_mask_class_also_masks(self):
        node = {"attributes": {"class": "rr-mask"}, "textContent": "pii"}
        assert redact_dom_node(node)["_bd_redacted"] == "mask"

    def test_block_class_drops_subtree(self):
        node = {"attributes": {"class": "bd-block"},
                "textContent": "card 4111111111111111",
                "childNodes": [{"tagName": "b", "textContent": "x"}]}
        out = redact_dom_node(node)
        assert out["childNodes"] == []
        assert "textContent" not in out
        assert out["_bd_redacted"] == "block"

    def test_mask_value_attribute(self):
        node = {"attributes": {"class": "bd-mask", "value": "hunter2pass"}}
        out = redact_dom_node(node)
        assert set(out["attributes"]["value"]) == {"*"}

    def test_recurses_into_children(self):
        node = {"tagName": "div", "childNodes": [
            {"attributes": {"class": "bd-mask"}, "textContent": "pii"},
            {"tagName": "p", "textContent": "public"},
        ]}
        out = redact_dom_node(node)
        assert out["childNodes"][0]["_bd_redacted"] == "mask"
        assert out["childNodes"][1]["textContent"] == "public"

    def test_unmarked_node_untouched(self):
        node = {"tagName": "p", "textContent": "public copy"}
        out = redact_dom_node(node)
        assert out["textContent"] == "public copy"
        assert "_bd_redacted" not in out


# ── body-capture policy ───────────────────────────────────────────

class TestBodyCapturePolicy:

    def test_video_excluded(self):
        cap, trunc, reason = should_capture_body("video/mp4", 5_000_000_000)
        assert cap is False and reason == "binary_media_excluded"

    def test_audio_excluded(self):
        assert should_capture_body("audio/mpeg", 1000)[0] is False

    def test_large_json_truncated(self):
        cap, trunc, reason = should_capture_body(
            "application/json", DEFAULT_BODY_CAP_BYTES + 1)
        assert cap is True and trunc == DEFAULT_BODY_CAP_BYTES
        assert reason == "truncated_to_cap"

    def test_small_text_captured_whole(self):
        cap, trunc, reason = should_capture_body("text/html", 2048)
        assert cap is True and trunc is None and reason == "captured"

    def test_custom_cap(self):
        cap, trunc, _ = should_capture_body("application/json", 600, cap=512)
        assert trunc == 512


# ── DomCapture: inheritance + DOM stream + frame_path ─────────────

class TestDomCapture:

    def test_inherits_network_capture(self):
        c = DomCapture(url="https://members.x.com/", redact=True)
        c.record_network(url="https://x/v.mp4?token=SECRET",
                         response_status=200)
        d = c.to_capture_dict()
        # A-T1 network behaviour intact + redaction applied
        assert d["network_log_count"] == 1
        assert "token=<scrubbed>" in d["network_log"][0]["url"]
        assert d["capture_kind"] == "dom+network"

    def test_dom_event_default_frame_path(self):
        c = DomCapture(redact=False)
        ev = c.record_dom_event(source=RR_MUTATION, data={"adds": []})
        assert ev["frame_path"] == ["main"]
        assert ev["type"] == "incremental"

    def test_dom_event_iframe_frame_path(self):
        c = DomCapture(redact=False)
        ev = c.record_dom_event(source=RR_MOUSE_INTERACTION if False else 2,
                                data={}, frame_path=["main", "player_iframe"])
        assert ev["frame_path"] == ["main", "player_iframe"]

    def test_full_snapshot_flag(self):
        c = DomCapture(redact=False)
        ev = c.record_dom_event(source=RR_MUTATION, data={"node": {}},
                                is_full_snapshot=True)
        assert ev["type"] == "full_snapshot"

    def test_dom_event_redacts_node(self):
        c = DomCapture(redact=True)
        c.record_dom_event(source=RR_MUTATION, data={
            "node": {"attributes": {"class": "bd-mask"},
                     "textContent": "secret"}})
        ev = c.to_capture_dict()["dom_log"][0]
        assert ev["data"]["node"]["_bd_redacted"] == "mask"

    def test_dom_event_redacts_mutation_adds(self):
        c = DomCapture(redact=True)
        c.record_dom_event(source=RR_MUTATION, data={"adds": [
            {"parentId": 1, "node": {"attributes": {"class": "bd-block"},
                                     "textContent": "pii"}}]})
        add = c.to_capture_dict()["dom_log"][0]["data"]["adds"][0]
        assert add["node"]["_bd_redacted"] == "block"

    def test_input_masked_text(self):
        c = DomCapture(redact=True)
        c.record_dom_event(source=RR_INPUT,
                           data={"text": "hunter2", "_masked": True})
        ev = c.to_capture_dict()["dom_log"][0]
        assert set(ev["data"]["text"]) == {"*"}

    def test_dom_log_shares_clock_with_network(self):
        c = DomCapture(redact=False)
        c.record_network(url="https://x/a", timestamp=1000)
        c.record_dom_event(source=RR_MUTATION, data={}, timestamp=1500)
        d = c.to_capture_dict()
        assert d["network_log"][0]["timestamp"] == 1000
        assert d["dom_log"][0]["timestamp"] == 1500


# ── storage deltas ────────────────────────────────────────────────

class TestStorageDeltas:

    def test_snapshot_and_delta_reconstruction(self):
        c = DomCapture(redact=False)
        c.snapshot_storage(local={"theme": "dark"}, session={})
        c.record_storage_delta(area="local", key="cart", new_value="abc",
                               timestamp=2000)
        c.record_storage_delta(area="local", key="theme", new_value="light",
                               timestamp=3000)
        # state before the deltas
        s0 = c.storage_at(1000)
        assert s0["local"] == {"theme": "dark"}
        # state after both
        s2 = c.storage_at(3000)
        assert s2["local"] == {"theme": "light", "cart": "abc"}

    def test_delta_removal(self):
        c = DomCapture(redact=False)
        c.snapshot_storage(local={"k": "v"})
        c.record_storage_delta(area="local", key="k", new_value=None,
                               timestamp=2000)
        assert c.storage_at(2000)["local"] == {}

    def test_delta_value_redacted_by_default(self):
        c = DomCapture(redact=True)
        c.record_storage_delta(area="local", key="auth_token",
                               new_value="JWT.SECRET.SIG")
        assert c.storage_deltas[0]["new_value"] == "<scrubbed>"


# ── chunking ──────────────────────────────────────────────────────

class TestCaptureChunker:

    def test_count_boundary(self):
        ch = CaptureChunker(max_events=3, capture_id="cap")
        finalized = [ch.add({"timestamp": i}) for i in range(7)]
        produced = [f for f in finalized if f]
        # 7 events, chunk every 3 → 2 finalized during add (at 4th, 7th)
        assert len(produced) == 2
        assert produced[0]["chunk_id"] == "cap_0000"
        assert produced[0]["event_count"] == 3
        assert produced[1]["continuation_of"] == "cap_0000"
        last = ch.finalize()
        assert last["event_count"] == 1
        assert last["continuation_of"] == "cap_0001"

    def test_span_boundary(self):
        ch = CaptureChunker(max_events=10_000, max_span_ms=1000,
                            capture_id="cap")
        assert ch.add({"timestamp": 0}) is None
        assert ch.add({"timestamp": 500}) is None
        # this event is 1000ms past the first → previous chunk flushes
        f = ch.add({"timestamp": 1000})
        assert f is not None and f["event_count"] == 2

    def test_finalize_empty_returns_none(self):
        ch = CaptureChunker()
        assert ch.finalize() is None


# ── WACZ export ───────────────────────────────────────────────────

class TestWaczExport:

    def _capture(self):
        c = DomCapture(url="https://members.x.com/video/123", redact=True)
        c.set_page_context(title="Demo")
        c.record_network(url="https://cdn.x.com/v.mp4", response_status=200)
        c.record_dom_event(source=RR_MUTATION, data={"adds": []})
        return c.to_capture_dict()

    def test_build_and_verify_roundtrip(self):
        wacz = build_wacz_bytes(self._capture())
        result = verify_wacz_bytes(wacz)
        assert result["ok"] is True, result["errors"]
        assert result["resources"] >= 2

    def test_contains_required_members(self):
        import io, zipfile
        wacz = build_wacz_bytes(self._capture())
        with zipfile.ZipFile(io.BytesIO(wacz)) as zf:
            names = set(zf.namelist())
        for required in ("datapackage.json", "datapackage-digest.json",
                         "archive/capture.json", "pages/pages.jsonl"):
            assert required in names

    def test_datapackage_has_version_and_digests(self):
        import io, zipfile
        wacz = build_wacz_bytes(self._capture())
        with zipfile.ZipFile(io.BytesIO(wacz)) as zf:
            dp = json.loads(zf.read("datapackage.json"))
        assert dp["wacz_version"] == WACZ_VERSION
        assert all(r["hash"].startswith("sha256:") for r in dp["resources"])
        assert dp["mainPageURL"] == "https://members.x.com/video/123"

    def test_tamper_detected(self):
        import io, zipfile
        wacz = build_wacz_bytes(self._capture())
        # rebuild the zip with a tampered capture.json
        buf = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(wacz)) as zin:
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zout:
                for n in zin.namelist():
                    data = zin.read(n)
                    if n == "archive/capture.json":
                        data = data + b" "  # tamper
                    zout.writestr(n, data)
        result = verify_wacz_bytes(buf.getvalue())
        assert result["ok"] is False
        assert any("hash_mismatch" in e for e in result["errors"])

    def test_pages_jsonl_shape(self):
        import io, zipfile
        wacz = build_wacz_bytes(self._capture())
        with zipfile.ZipFile(io.BytesIO(wacz)) as zf:
            lines = zf.read("pages/pages.jsonl").decode().strip().split("\n")
        header = json.loads(lines[0])
        page = json.loads(lines[1])
        assert header["format"] == "json-pages-1.0"
        assert page["url"] == "https://members.x.com/video/123"

    def test_extra_resources_included_and_hashed(self):
        cap = self._capture()
        extra = {"archive/chunk_0001.json": b'{"events":[]}'}
        wacz = build_wacz_bytes(cap, extra_resources=extra)
        assert verify_wacz_bytes(wacz)["ok"] is True


# ── operator CLI (pure parts; live leg not exercised) ─────────────

class TestCaptureSessionCLI:

    def _load(self):
        import importlib.util, os
        # tests/ lives at repo root; tools/ is its sibling.
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "tools", "capture_session.py")
        spec = importlib.util.spec_from_file_location("capture_session", path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def test_parser_requires_url_and_out(self):
        m = self._load()
        p = m._build_parser()
        with pytest.raises(SystemExit):
            p.parse_args([])

    def test_parser_defaults(self):
        m = self._load()
        args = m._build_parser().parse_args(
            ["--url", "https://x", "--out", "c.wacz"])
        assert args.body_cap_mib == 1
        assert args.chunk_events == 10000
        assert args.system_chrome is False
        # --no-redact was removed in v3.66.59: redaction is always on in the
        # release; raw capture is dev-only via bd_dev_inspect + BD_CAPTURE_RAW.
        assert not hasattr(args, "no_redact")

    def test_parser_flags(self):
        m = self._load()
        args = m._build_parser().parse_args(
            ["--url", "https://x", "--out", "c.wacz",
             "--system-chrome", "--body-cap-mib", "4"])
        assert args.system_chrome is True
        assert args.body_cap_mib == 4

    def test_no_redact_flag_is_gone(self):
        # The release CLI must not accept a redaction-disable flag.
        m = self._load()
        with pytest.raises(SystemExit):
            m._build_parser().parse_args(
                ["--url", "https://x", "--out", "c.wacz", "--no-redact"])

    def test_run_without_playwright_returns_2(self, monkeypatch):
        # If Playwright import fails, run() must exit 2 cleanly, not crash.
        m = self._load()
        import builtins
        real_import = builtins.__import__
        def fake_import(name, *a, **k):
            if name.startswith("playwright"):
                raise ImportError("no playwright")
            return real_import(name, *a, **k)
        monkeypatch.setattr(builtins, "__import__", fake_import)
        rc = m.run(["--url", "https://x", "--out", "/tmp/x.wacz"])
        assert rc == 2


def test_action_timeline_persists_in_capture_dict():
    """Wave A: record_action -> to_capture_dict carries the observational
    action timeline (structure + kinds/counts); the field is omitted when empty
    so read-only captures are unchanged; non-dict entries are ignored."""
    import importlib
    dc = importlib.import_module("bulk_downloader.dom_capture")
    c = dc.DomCapture(url="https://x", redact=True)
    assert "action_timeline" not in c.to_capture_dict()   # positive control: omitted when empty
    c.record_action({"selector": "button.play-btn", "role": "play button",
                     "effect": {"req_count": 143, "manifest": 1, "segments": 142}})
    c.record_action("not-a-dict")                          # ignored, never raises
    out = c.to_capture_dict()
    assert out["action_timeline_count"] == 1
    assert out["action_timeline"][0]["selector"] == "button.play-btn"
