"""Pin/host derivation — filename fallback + /load host contract.

Repro of the offline Build-draft blocker: /api/analyzer/pin derived host ONLY
from the capture's ARCHIVE content (capture['url']), which is empty for a
redacted/scrubbed capture. The picker already derives host from the FILENAME
({host}_{siteid}_{ts}) via _capture_host; pin never consulted it, so a
host-named-but-redacted capture 400'd ("host required (capture had none)").

These assert:
  * pin derives host from the FILENAME when the archive content has none,
  * /api/analyzer/load returns that host in its response contract,
  * a genuinely host-less capture (no url AND no filename host) still 400s.

Bare test client carries no bd_session cookie, so _check_csrf skips (documented
no-session bypass). Fixtures stage a synthetic WACZ under the real PROJECT_ROOT
capture dir (what the route resolves against) and clean up in a finally.
"""

import io
import json
import zipfile
from pathlib import Path

from bulk_downloader import app as app_mod
from bulk_downloader import dom_analyzer as da
from bulk_downloader.template_registry import PROJECT_ROOT


def _client():
    return app_mod.app.test_client()


def _el(tag, attrs=None, children=None):
    return {"type": 2, "tagName": tag, "attributes": attrs or {}, "childNodes": children or []}


def _redacted_capture_no_url():
    """A capture with a valid DOM but NO url/page_url — i.e. the URL was scrubbed
    by redaction, so archive-content host derivation (capture_host) is empty."""
    body = _el("body", {}, [
        _el("button", {"id": "go", "class": "primary"}, [{"type": 3, "textContent": "Go"}]),
    ])
    return {"dom_log": [{"type": 2, "data": {"node": {"type": 0,
            "childNodes": [_el("html", {}, [body])]}}}]}  # note: NO "url" key


def _wacz_bytes(capture_dict):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("archive/capture.json", json.dumps(capture_dict))
    return buf.getvalue()


def _stage_wacz(name, capture_dict):
    cdir = Path(PROJECT_ROOT) / "captures"
    created = not cdir.exists()
    cdir.mkdir(parents=True, exist_ok=True)
    cap = cdir / name
    cap.write_bytes(_wacz_bytes(capture_dict))
    return cdir, cap, created


def _cleanup(cdir, created, *paths):
    for p in paths:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    if created and cdir.exists() and not any(cdir.iterdir()):
        cdir.rmdir()


def test_archive_host_is_empty_but_filename_host_resolves():
    """The two derivations diverge: archive-content host is empty on a redacted
    capture, while the filename derivation the picker uses returns the host."""
    name = "qa-derive.example_qasid_20250101.wacz"
    cdir, cap, created = _stage_wacz(name, _redacted_capture_no_url())
    try:
        assert da.capture_host(da.load_capture(cap)) == ""          # archive: empty
        assert da.capture_host_from_name(name) == "qa-derive.example"  # filename: host
    finally:
        _cleanup(cdir, created, cap)


def test_pin_derives_host_from_filename_when_archive_has_none():
    """RED: redacted WACZ named host_siteid_date.wacz, pinned with NO body host
    -> must succeed via the filename fallback (currently 400s)."""
    name = "qa-pin.example_qasid_20250101.wacz"
    cdir, cap, created = _stage_wacz(name, _redacted_capture_no_url())
    draft = Path(PROJECT_ROOT) / "templates" / "drafts" / "qa-pin.example.template-draft.json"
    try:
        r = _client().post("/api/analyzer/pin", json={
            "capture": name, "selector": "button#go", "role": "download", "name": "button"})
        assert r.status_code == 200, r.data
        b = json.loads(r.data)
        assert b["ok"] is True
        assert b["status"] == "draft_review_required"
        assert b["enabled"] is False
    finally:
        _cleanup(cdir, created, cap, draft)


def test_load_returns_host_from_filename():
    """RED: /api/analyzer/load must include host (from the filename) in its
    response so the UI can display/confirm it."""
    name = "qa-load.example_qasid_20250101.wacz"
    cdir, cap, created = _stage_wacz(name, _redacted_capture_no_url())
    try:
        r = _client().post("/api/analyzer/load", json={"capture": name})
        assert r.status_code == 200, r.data
        b = json.loads(r.data)
        assert b.get("host") == "qa-load.example"
    finally:
        _cleanup(cdir, created, cap)


def test_pin_honors_explicit_body_host():
    """Regression: an explicit body host is still used verbatim."""
    name = "qa-explicit.example_qasid_20250101.wacz"
    cdir, cap, created = _stage_wacz(name, _redacted_capture_no_url())
    draft = Path(PROJECT_ROOT) / "templates" / "drafts" / "override.example.template-draft.json"
    try:
        r = _client().post("/api/analyzer/pin", json={
            "capture": name, "selector": "button#go", "role": "download",
            "name": "button", "host": "override.example"})
        assert r.status_code == 200, r.data
        assert json.loads(r.data)["ok"] is True
    finally:
        _cleanup(cdir, created, cap, draft)


def test_pin_still_400_when_no_host_anywhere():
    """Regression: a capture with no url AND no filename host (capture_*.json
    stem carries no host) still 400s — the fix must not fabricate a host."""
    cdir = Path(PROJECT_ROOT) / "captures"
    created = not cdir.exists()
    cdir.mkdir(parents=True, exist_ok=True)
    cap = cdir / "capture_qanohost.json"
    try:
        cap.write_text(json.dumps(_redacted_capture_no_url()), "utf-8")
        r = _client().post("/api/analyzer/pin", json={
            "capture": "capture_qanohost.json", "selector": "button#go", "role": "download"})
        assert r.status_code == 400
        assert "host required" in json.loads(r.data)["error"]
    finally:
        _cleanup(cdir, created, cap)
