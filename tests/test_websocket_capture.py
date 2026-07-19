"""WebSocket capture — tests (metadata-only path).

Synthetic CDP events drive the pure router (`feed_cdp_event`) and the
`record_ws_*` methods — the same browser-free strategy the existing network
tests use. Verifies connection lifecycle, frame metadata, the no-payload
default, opt-in redacted payloads, frame caps, URL/header redaction, additive
export, and downstream tolerance. The live `client.on` wiring is browser-only
(stash) and not exercised here.
"""
import json
import sys
import tempfile
import zipfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools"))

from bulk_downloader.session_capture import (
    SessionCapture, feed_cdp_event, _WS_MAX_FRAMES,
    _CDP_WS_CREATED, _CDP_WS_HS_RESP, _CDP_WS_FRAME_SENT,
    _CDP_WS_FRAME_RECV, _CDP_WS_CLOSED,
)
from bulk_downloader.capture_redact import PLACEHOLDER
from bulk_downloader.wacz_export import write_wacz, verify_wacz_bytes


# ── connection lifecycle via the router (the live delegate) ───────
def test_ws_lifecycle_via_router():
    c = SessionCapture(redact=True)
    feed_cdp_event(c, _CDP_WS_CREATED, {"requestId": "w1", "url": "wss://api2.reptyle.com/live", "timestamp": 1.0})
    feed_cdp_event(c, _CDP_WS_HS_RESP, {"requestId": "w1", "response": {"status": 101, "headers": {}}, "timestamp": 1.1})
    feed_cdp_event(c, _CDP_WS_FRAME_SENT, {"requestId": "w1", "response": {"opcode": 1, "payloadData": "hello"}, "timestamp": 1.2})
    feed_cdp_event(c, _CDP_WS_FRAME_RECV, {"requestId": "w1", "response": {"opcode": 1, "payloadData": "world"}, "timestamp": 1.3})
    feed_cdp_event(c, _CDP_WS_CLOSED, {"requestId": "w1", "timestamp": 2.0})
    assert len(c.websocket_log) == 1
    conn = c.websocket_log[0]
    assert conn["handshake_status"] == 101
    assert conn["frame_count"] == 2 and conn["closed_ms"] is not None
    dirs = [f["dir"] for f in conn["frames"]]
    assert dirs == ["sent", "received"]
    assert all(f["opcode"] == 1 and f["len"] == 5 for f in conn["frames"])


# ── metadata-only by default: no payload bytes on disk ────────────
def test_ws_no_payload_by_default():
    c = SessionCapture(redact=True)
    c.record_ws_created(request_id="w1", url="wss://x/y", ts=1)
    c.record_ws_frame(request_id="w1", direction="received", opcode=1,
                      payload_len=6, ts=2, payload="SECRET")
    f = c.websocket_log[0]["frames"][0]
    assert "payload" not in f          # default: metadata only
    assert f["len"] == 6 and f["opcode"] == 1


# ── opt-in payloads are redacted; binary never captured ───────────
def test_ws_payload_optin_redacted_text_only():
    c = SessionCapture(redact=True)
    c.capture_ws_payloads = True
    c.record_ws_created(request_id="w1", url="wss://x/y", ts=1)
    c.record_ws_frame(request_id="w1", direction="sent", opcode=1,
                      payload_len=20, ts=2, payload="token=ABC.SECRET.SIG")
    c.record_ws_frame(request_id="w1", direction="received", opcode=2,
                      payload_len=99, ts=3, payload="\x00\x01binary")
    frames = c.websocket_log[0]["frames"]
    # text frame: payload present but scrubbed (no raw secret)
    assert "ABC.SECRET.SIG" not in json.dumps(frames[0])
    # binary frame (opcode 2): never carries a payload, even with flag on
    assert "payload" not in frames[1]


# ── redaction: URL query + handshake headers ──────────────────────
def test_ws_url_query_redacted():
    c = SessionCapture(redact=True)
    c.record_ws_created(request_id="w1", url="wss://api2.reptyle.com/live?token=SECRET", ts=1)
    url = c.websocket_log[0]["url"]
    assert "SECRET" not in url and "token=" + PLACEHOLDER in url


def test_ws_handshake_headers_redacted():
    c = SessionCapture(redact=True)
    c.record_ws_created(request_id="w1", url="wss://x/y", ts=1)
    c.record_ws_handshake(request_id="w1", status=101,
                          headers=[{"name": "Cookie", "value": "sid=SECRET"}], ts=2)
    assert "SECRET" not in json.dumps(c.websocket_log[0]["handshake_headers"])


# ── frame cap bounds storage/perf ─────────────────────────────────
def test_ws_frame_cap():
    c = SessionCapture(redact=True)
    c.record_ws_created(request_id="w1", url="wss://x/y", ts=1)
    for i in range(_WS_MAX_FRAMES + 25):
        c.record_ws_frame(request_id="w1", direction="received", opcode=1, payload_len=1, ts=i)
    conn = c.websocket_log[0]
    assert conn["frames_capped"] is True
    assert len(conn["frames"]) == _WS_MAX_FRAMES        # stored frames bounded
    assert conn["frame_count"] == _WS_MAX_FRAMES + 25    # but total counted


def test_ws_frame_for_unknown_connection_ignored():
    c = SessionCapture(redact=True)
    assert c.record_ws_frame(request_id="ghost", direction="sent", opcode=1, payload_len=1, ts=1) is None
    assert c.websocket_log == []


# ── additive export / WACZ / downstream tolerance ─────────────────
def test_export_omits_ws_key_when_empty():
    c = SessionCapture(redact=True)
    d = c.to_capture_dict()
    assert "websocket_log" not in d  # membership-safe: absent when unused


def test_export_and_wacz_compat_with_ws():
    c = SessionCapture(url="https://app.reptyle.com", redact=True)
    c.record_ws_created(request_id="w1", url="wss://api2.reptyle.com/live?token=SECRET", ts=1)
    c.record_ws_frame(request_id="w1", direction="received", opcode=1, payload_len=4, ts=2, payload="data")
    d = c.to_capture_dict()
    assert d["websocket_log_count"] == 1 and "websocket_log" in d
    with tempfile.TemporaryDirectory() as tdir:
        out = str(Path(tdir) / "cap.wacz")
        write_wacz(d, out)
        raw = Path(out).read_bytes()
        assert verify_wacz_bytes(raw).get("ok") is True
        with zipfile.ZipFile(out) as z:
            name = next(n for n in z.namelist() if n.endswith("capture.json"))
            archived = z.read(name)
    assert b"SECRET" not in archived  # redacted url, no payload
    cap2 = json.loads(archived)
    assert cap2["websocket_log"][0]["frame_count"] == 1


def test_downstream_template_builder_tolerates_ws():
    import build_template_from_wacz as B
    c = SessionCapture(url="https://app.reptyle.com", redact=True)
    c.record_ws_created(request_id="w1", url="wss://x/y", ts=1)
    d = c.to_capture_dict()
    d["dom_log"] = [{
        "dom_seq": 0, "timestamp": 1, "iso": "x", "type": "full_snapshot",
        "source": -1, "frame_path": ["main"], "data": {},
        "html": "<html><body><a download href='/x.mp4'>Download</a></body></html>",
    }]
    with tempfile.TemporaryDirectory() as tdir:
        out = str(Path(tdir) / "cap.wacz")
        write_wacz(d, out)
        draft = B.build_template(Path(out))
    assert isinstance(draft, dict)
