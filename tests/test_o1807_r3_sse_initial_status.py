"""O1807 R3: /api/stream sends its initial ``status`` event.

``app._status_snapshot`` still named ``api_status`` after P4 moved that view to
app_status.py, so every call raised NameError; the SSE generator swallowed it
and the client never got the initial status push. The fix also brings the
snapshot's ``login_status`` redaction in line with /api/status (D1), since the
initial push now actually carries it.
"""

import json


BD_GATE_SCOPE = "module"

_SECRET = "o1807-r3-password-not-for-sse"


def _site_with_login_status(fresh_app):
    from bulk_downloader.app_state import runners

    sid = fresh_app.post("/api/sites", json={"name": "o1807-r3-sse"}).get_json()["id"]
    runners[sid]._login_status = (
        "Expected URL contains '/members', got "
        f"https://login.example.invalid/complete?username=alice&password={_SECRET}&next=home")
    return sid


def _initial_events(fresh_app, count=2):
    """The first ``count`` SSE frames of /api/stream, as (event, data) pairs."""
    resp = fresh_app.get("/api/stream", buffered=False)
    frames = []
    try:
        for chunk in resp.response:
            text = chunk.decode() if isinstance(chunk, bytes) else chunk
            if text.startswith(":"):
                continue
            head, _, data = text.partition("\ndata: ")
            frames.append((head.removeprefix("event: "), data.strip()))
            if len(frames) == count:
                break
    finally:
        resp.close()
    return frames


def test_status_snapshot_does_not_raise(fresh_app):
    from bulk_downloader import app as app_mod

    sid = _site_with_login_status(fresh_app)
    snap = app_mod._status_snapshot(light=True)
    assert sid in snap
    assert snap[sid]["name"] == "o1807-r3-sse"


def test_stream_initial_push_includes_status(fresh_app):
    sid = _site_with_login_status(fresh_app)
    frames = _initial_events(fresh_app)
    assert [event for event, _ in frames] == ["dashboard", "status"], frames
    status = json.loads(frames[1][1])
    assert sid in status


def test_stream_initial_status_redacts_login_status(fresh_app):
    sid = _site_with_login_status(fresh_app)
    frames = dict(_initial_events(fresh_app))
    assert _SECRET not in frames["status"]
    login_status = json.loads(frames["status"])[sid]["login_status"]
    assert "password=<REDACTED>" in login_status
    assert "next=home" in login_status  # noncredential positive control
    assert login_status == fresh_app.get("/api/status").get_json()[sid]["login_status"]
