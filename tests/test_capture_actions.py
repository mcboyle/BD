"""Item 3 follow-on -- per-capture ACTIONS on the capture browser.

Once captures are discoverable (the 517 read-only browser), the operator can act
on one: build a REVIEW-ONLY template draft from a capture, or scrub a raw wacz to
its redacted twin. Both resolve the capture by the recursive subpath token and
never enable anything.

  POST /api/captures/build_draft  {token} -> build_template_from_wacz -> drafts/
  POST /api/captures/scrub        {token} -> capture_scrub_hook.scrub_on_capture

Seeded fake capture tree (the sandbox has no real captures). The draft write is
pointed at a temp dir so the build never lands in the source tree.
"""
from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path

import bulk_downloader.app as bd_app
import bulk_downloader.app_captures as ac
import bulk_downloader.dom_analyzer as da


def _capture_dict():
    # Minimal but real: a download-API capture (build_template surfaces it).
    return {
        "url": "https://app.example.com/movie/1", "host": "app.example.com",
        "captured_at": "2026-06-08T00:00:00Z", "dom_log_count": 0, "network_log_count": 2,
        "dom_log": [],
        "network_log": [
            {"method": "GET",
             "url": "https://api2.example.com/api/v1/movie/55/download-resolution/1080?sig=SYN"},
            {"method": "GET",
             "url": "https://api2.example.com/api/v1/movie/55/download-resolution/720?sig=SYN2"},
        ],
    }


def _seed(monkeypatch) -> Path:
    root = Path(tempfile.mkdtemp())
    d = root / "captures" / "template_onboarding" / "app.example.com_s1_20250101_aa"
    d.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(d / "x.wacz", "w") as z:
        z.writestr("archive/capture.json", json.dumps(_capture_dict()))
    monkeypatch.setattr(da, "_project_root", lambda: root)
    ac._SCAN_CACHE["rows"] = []
    ac._SCAN_CACHE["summary"] = None
    ac._SCAN_CACHE["built_at"] = None
    return root


TOKEN = "captures/template_onboarding/app.example.com_s1_20250101_aa/x.wacz"


# ── dom_analyzer.build_draft_from_token ──────────────────────────────────────
def test_build_draft_writes_review_only_draft(tmp_path, monkeypatch):
    root = _seed(monkeypatch)
    drafts = tmp_path / "drafts"
    res = da.build_draft_from_token(TOKEN, root=root, drafts_dir=drafts)
    assert res["ok"] is True
    assert res["host"] == "app.example.com"
    written = drafts / "app.example.com.template-draft.json"
    assert written.is_file()
    # a draft is review-only: it is NOT status=enabled
    tpl = json.loads(written.read_text(encoding="utf-8"))
    assert tpl.get("status") != "enabled"


def test_build_draft_rejects_bad_token(tmp_path, monkeypatch):
    _seed(monkeypatch)
    res = da.build_draft_from_token("captures/../../etc/passwd",
                                    drafts_dir=tmp_path / "d")
    assert res["ok"] is False


# ── dom_analyzer.scrub_capture_token ─────────────────────────────────────────
def test_scrub_returns_status(monkeypatch):
    root = _seed(monkeypatch)
    res = da.scrub_capture_token(TOKEN, root=root)
    assert res["ok"] is True
    # scrub_on_capture is fail-soft: a status dict comes back either way
    assert "result" in res
    assert "ran" in res["result"]


def test_scrub_rejects_bad_token(monkeypatch):
    _seed(monkeypatch)
    assert da.scrub_capture_token("/etc/passwd")["ok"] is False


# ── routes ───────────────────────────────────────────────────────────────────
def test_build_draft_route(fresh_app, monkeypatch, tmp_path):
    _seed(monkeypatch)
    # point the drafts dir at a temp dir so nothing lands in the source tree
    import bulk_downloader.template_manager as tm
    monkeypatch.setattr(tm, "DRAFTS_DIR", tmp_path / "drafts")
    fresh_app.post("/api/captures/scan", json={})
    r = fresh_app.post("/api/captures/build_draft", json={"token": TOKEN})
    assert r.status_code == 200, r.get_data(as_text=True)
    b = r.get_json()
    assert b["ok"] is True
    assert b["host"] == "app.example.com"
    assert (tmp_path / "drafts" / "app.example.com.template-draft.json").is_file()


def test_build_draft_route_unknown_token(fresh_app, monkeypatch, tmp_path):
    _seed(monkeypatch)
    import bulk_downloader.template_manager as tm
    monkeypatch.setattr(tm, "DRAFTS_DIR", tmp_path / "drafts")
    fresh_app.post("/api/captures/scan", json={})
    r = fresh_app.post("/api/captures/build_draft", json={"token": "captures/nope.wacz"})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_scrub_route(fresh_app, monkeypatch):
    _seed(monkeypatch)
    fresh_app.post("/api/captures/scan", json={})
    r = fresh_app.post("/api/captures/scrub", json={"token": TOKEN})
    assert r.status_code == 200, r.get_data(as_text=True)
    b = r.get_json()
    assert b["ok"] is True
    assert "result" in b


# ── FE wiring (static scan: the 2 action literals are spa_wired) ─────────────
def test_spa_capture_actions_wired():
    panel = (Path(__file__).resolve().parent.parent
             / "frontend" / "src" / "components" / "CaptureBrowser.tsx")
    src = panel.read_text(encoding="utf-8")
    assert "/api/captures/build_draft" in src
    assert "/api/captures/scrub" in src
