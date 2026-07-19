"""v3.66.139 — cockpit /cockpit/api/captures/build-template route.

Exercises the in-process synth -> workbench -> (optional freeze) pipeline
through the cockpit blueprint with a bare Flask host (no host before_request,
so no CSRF to satisfy — CSRF is inherited from the real host app). Captures
are built inline with the same shape capture_synth's own fixtures use.
"""
import os

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

from flask import Flask  # noqa: E402

from bulk_downloader.session_capture import SessionCapture  # noqa: E402
from tools import cockpit_console  # noqa: E402

URL = "/cockpit/api/captures/build-template"


def _capture(item_id, secret, expires_val):
    host = "members.ex.com"
    cap = SessionCapture(url=f"https://{host}/watch?v={item_id}")
    cap.set_page_context(host=host)
    cap.record_network(type="xhr", method="GET",
                       url=f"https://{host}/api/item?id={item_id}")
    cap.record_network(
        type="xhr", method="GET",
        url=f"https://cdn.ex.com/v/master.m3u8?id={item_id}"
            f"&token={secret}&expires={expires_val}",
        request_headers=[{"name": "Cookie", "value": "sid=topsecret"}])
    return cap.to_capture_dict()


def _client():
    app = Flask(__name__)
    cockpit_console.register_routes(app)
    return app.test_client()


def test_build_template_draft_from_inline_captures():
    a = _capture("10001", "SECRETA", "111")
    b = _capture("10002", "SECRETB", "222")
    resp = _client().post(URL, json={"cap_a": a, "cap_b": b})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["ok"] is True
    assert "draft" in data
    assert data["synth"]["host"] == "members.ex.com"
    assert data["synth"]["request_count"] >= 1
    # draft only — no frozen template unless asked
    assert "template" not in data


def test_freeze_returns_template():
    a = _capture("10001", "SECRETA", "111")
    b = _capture("10002", "SECRETB", "222")
    resp = _client().post(URL, json={"cap_a": a, "cap_b": b, "freeze": True})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["ok"] is True
    assert data.get("template") is not None
    assert isinstance(data["template"], dict)


def test_missing_captures_is_400():
    resp = _client().post(URL, json={})
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_nonexistent_filenames_are_rejected():
    # filename path (no inline dicts) for files that aren't under the root
    resp = _client().post(URL, json={"a": "../etc/passwd", "b": "nope.wacz"})
    assert resp.status_code == 400
    assert "error" in resp.get_json()
