"""v3.66.158 #3+#4 — capture→build→normalize onboarding + review-candidate surface.

#4: POST /api/captures/normalize builds a rich draft from a captured .wacz and
    normalizes it into a runtime-shape review candidate (in-process, scrubbed,
    never enabled), written to templates/review_candidates/.
#3: GET /api/review-candidates lists those candidates (read-only) with status,
    review notes, and the exact promote command.

Synthetic wacz only; no browser/network. The candidate dir (cc._ROOT) is
redirected to a temp tree per test so the repo is never written.
"""
from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from tools import cockpit_console as console  # noqa: E402
import flask  # noqa: E402

cc = console.cc
_ORIG_ROOT = cc._ROOT

_HLS = ("#EXTM3U\n"
        "#EXT-X-STREAM-INF:RESOLUTION=1280x720\na\n"
        "#EXT-X-STREAM-INF:RESOLUTION=1920x1080\nb\n"
        "#EXT-X-STREAM-INF:RESOLUTION=3840x2160\nc\n")
_HTML = ('<div class="theoplayer-skin"></div>'
         '<button aria-label="Open the video quality settings menu"></button>'
         '<button aria-label="Download Full Movie">Download</button>')


def _client():
    app = flask.Flask(__name__)
    app.register_blueprint(console.bp)
    return app.test_client()


def _seed_capture_with_wacz():
    d = Path(tempfile.mkdtemp(prefix="rcw158_"))
    out = d / "out"
    out.mkdir()
    cap = {
        "url": "https://app.reptyle.com/movies/9",
        "dom_log": [{"type": "full_snapshot", "label": "m", "html": _HTML}],
        "network_log": [
            {"url": "https://api2.reptyle.com/api/v1/movie/9/download-resolution/1080?token=S&sig=A",
             "response_status": 200},
            {"url": "https://cdn.reptyle.com/hls/9/master.m3u8", "response_status": 200,
             "response_body": _HLS},
        ],
    }
    (out / "reptyle_dom.wacz").write_bytes(b"")  # placeholder, replaced below
    with zipfile.ZipFile(out / "reptyle_dom.wacz", "w") as z:
        z.writestr("capture.json", json.dumps(cap))
    tid = "t_" + d.name[-10:]
    with cc._REG_LOCK:
        cc._REGISTRY[tid] = {
            "task_id": tid, "category": "capture", "name": "capture_session",
            "label": "reptyle", "status": "succeeded", "out_dir": str(out),
            "log_path": str(d / "l.log"), "started": 1.0, "finished": 2.0,
            "returncode": 0, "output_files": [],
        }
    return tid, d


def _redirect_root():
    cc._ROOT = Path(tempfile.mkdtemp(prefix="root158_"))
    return cc._ROOT


def test_normalize_from_task_writes_review_ready_candidate() -> None:
    c = _client()
    tid, _d = _seed_capture_with_wacz()
    root = _redirect_root()
    try:
        r = c.post("/cockpit/api/captures/normalize", json={"task_id": tid})
        assert r.status_code == 200, r.get_json()
        j = r.get_json()
        assert j["host"] == "app.reptyle.com"
        assert j["status"] == "review_ready"
        assert j["has_download_trigger"] is True
        assert 2160 in j["resolutions"] and 1080 in j["resolutions"]
        assert j["observed_api_hosts"] == ["api2.reptyle.com"]
        assert (root / "templates" / "review_candidates" / "app.reptyle.com.candidate.json").exists()
    finally:
        cc._ROOT = _ORIG_ROOT


def test_normalize_requires_task_or_wacz() -> None:
    c = _client()
    root = _redirect_root()
    try:
        r = c.post("/cockpit/api/captures/normalize", json={})
        assert r.status_code == 400
    finally:
        cc._ROOT = _ORIG_ROOT


def test_normalize_unknown_task_rejected() -> None:
    c = _client()
    root = _redirect_root()
    try:
        r = c.post("/cockpit/api/captures/normalize", json={"task_id": "nope"})
        assert r.status_code == 400
        assert "no such task" in r.get_json()["error"]
    finally:
        cc._ROOT = _ORIG_ROOT


def test_normalize_task_without_wacz_rejected() -> None:
    c = _client()
    d = Path(tempfile.mkdtemp(prefix="nowacz158_"))
    out = d / "out"
    out.mkdir()  # empty — no .wacz
    tid = "t_nowacz"
    with cc._REG_LOCK:
        cc._REGISTRY[tid] = {
            "task_id": tid, "category": "capture", "name": "capture_session",
            "label": "x", "status": "succeeded", "out_dir": str(out),
            "log_path": str(d / "l.log"), "started": 1.0, "finished": 2.0,
            "returncode": 0, "output_files": [],
        }
    root = _redirect_root()
    try:
        r = c.post("/cockpit/api/captures/normalize", json={"task_id": tid})
        assert r.status_code == 400
        assert "no .wacz" in r.get_json()["error"]
    finally:
        cc._ROOT = _ORIG_ROOT


def test_review_candidates_lists_written_candidate() -> None:
    c = _client()
    tid, _d = _seed_capture_with_wacz()
    root = _redirect_root()
    try:
        c.post("/cockpit/api/captures/normalize", json={"task_id": tid})
        r = c.get("/cockpit/api/review-candidates")
        assert r.status_code == 200
        cands = r.get_json()["candidates"]
        rep = [x for x in cands if x["host"] == "app.reptyle.com"]
        assert rep, cands
        x = rep[0]
        assert x["status"] == "review_ready"
        assert x["has_download_trigger"] is True
        assert "promote_template.py" in x["promote_cmd"]
        assert "review_candidates" in x["path"]
    finally:
        cc._ROOT = _ORIG_ROOT


def test_review_candidates_empty_when_none() -> None:
    c = _client()
    root = _redirect_root()  # fresh empty root
    try:
        r = c.get("/cockpit/api/review-candidates")
        assert r.status_code == 200
        assert r.get_json()["candidates"] == []
    finally:
        cc._ROOT = _ORIG_ROOT
