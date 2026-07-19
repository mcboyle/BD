"""F2.6 DOM Analyzer Workbench — endpoint family contract tests.

Routes: GET /api/analyzer/captures · POST /api/analyzer/{load,tree,pin}.
Selector testing reuses /api/playground/test (no analyzer test route).

A bare test client carries no ``bd_session`` cookie, so ``_check_csrf`` skips
(its documented no-session bypass) — POSTs work without a token, same as the
settings-center slice tests.

The happy-path load stages a synthetic capture under the real PROJECT_ROOT
capture dir (that's what the route resolves against) and removes it in a
finally, so the tree/namelist stays clean.
"""

import json
from pathlib import Path

from bulk_downloader import app as app_mod
from bulk_downloader import dom_analyzer as da
from bulk_downloader.template_registry import PROJECT_ROOT


def _client():
    return app_mod.app.test_client()


def test_captures_list_ok():
    r = _client().get("/api/analyzer/captures")
    assert r.status_code == 200
    b = json.loads(r.data)
    assert b["ok"] is True
    assert isinstance(b["captures"], list)


def test_load_missing_capture_400():
    r = _client().post("/api/analyzer/load", json={})
    assert r.status_code == 400


def test_load_unknown_capture_404():
    r = _client().post("/api/analyzer/load", json={"capture": "capture_does_not_exist.json"})
    assert r.status_code == 404
    assert json.loads(r.data)["error"] == "unknown capture"


def test_tree_missing_capture_400():
    r = _client().post("/api/analyzer/tree", json={})
    assert r.status_code == 400


def test_pin_missing_fields_400():
    r = _client().post("/api/analyzer/pin", json={"capture": "x"})
    assert r.status_code == 400


def test_analyzer_test_missing_fields_400():
    assert _client().post("/api/analyzer/test", json={"capture": "x"}).status_code == 400
    assert _client().post("/api/analyzer/test", json={"selectors": ["a"]}).status_code == 400


def test_analyzer_test_unknown_capture_404():
    r = _client().post("/api/analyzer/test",
                       json={"capture": "capture_nope.json", "selectors": ["a"]})
    assert r.status_code == 404


def _planted_min():
    def el(tag, attrs=None, children=None):
        return {"type": 2, "tagName": tag, "attributes": attrs or {}, "childNodes": children or []}
    body = el("body", {}, [
        el("a", {"href": "https://cdn.x/v.mp4?signature=ABC123&Expires=9", "id": "dl"},
           [{"type": 3, "textContent": "Download"}]),
        el("button", {"id": "go", "class": "primary"}, [{"type": 3, "textContent": "Go"}]),
    ])
    return {"url": "https://site.example.com/watch",
            "dom_log": [{"type": 2, "data": {"node": {"type": 0, "childNodes": [el("html", {}, [body])]}}}]}


def test_load_and_pin_happy_path():
    cdir = Path(PROJECT_ROOT) / "captures"
    created_dir = not cdir.exists()
    cdir.mkdir(parents=True, exist_ok=True)
    cap = cdir / "capture_endpointtest.json"
    draft = Path(PROJECT_ROOT) / "templates" / "drafts" / "site.example.com.template-draft.json"
    try:
        cap.write_text(json.dumps(_planted_min()), "utf-8")
        c = _client()
        # load → clean tree, signed url stripped
        r = c.post("/api/analyzer/load", json={"capture": "capture_endpointtest.json"})
        assert r.status_code == 200
        b = json.loads(r.data)
        assert b["ok"] is True and b["has_dom"] is True
        assert b["residual_count"] == 0
        assert "signature=ABC123" not in b["html"]
        # selector test against the CAPTURED dom (not a live fetch)
        rt = c.post("/api/analyzer/test", json={
            "capture": "capture_endpointtest.json",
            "selectors": ["button#go", ".missing"]})
        assert rt.status_code == 200
        bt = json.loads(rt.data)
        assert bt["ok"] is True
        by = {x["selector"]: x for x in bt["results"]}
        assert by["button#go"].get("count", 0) >= 1
        assert by[".missing"].get("count", 0) == 0
        # pin → review-only draft, host derived from capture url
        r2 = c.post("/api/analyzer/pin", json={
            "capture": "capture_endpointtest.json",
            "selector": "button#go", "role": "download", "name": "button"})
        assert r2.status_code == 200
        b2 = json.loads(r2.data)
        assert b2["ok"] is True
        assert b2["status"] == "draft_review_required"
        assert b2["enabled"] is False
        assert draft.is_file()
        on_disk = json.loads(draft.read_text("utf-8"))
        assert on_disk["status"] != "enabled"
    finally:
        if cap.exists():
            cap.unlink()
        if draft.exists():
            draft.unlink()
        if created_dir and cdir.exists() and not any(cdir.iterdir()):
            cdir.rmdir()
