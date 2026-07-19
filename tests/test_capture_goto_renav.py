"""CAP-ROBUST (B) — /api/captures/goto re-nav endpoint + SPA control.

When a login redirect leaves a held-open capture on a host/landing page, the
operator drops a GOTO sentinel (via this endpoint / the SPA button) and
capture_session.py re-navigates the live page back to the requested --url.
Mirrors the FINISH/CANCEL endpoint harness (test_v3_66_158).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from tools import cockpit_console as console  # noqa: E402
import flask  # noqa: E402

cc = console.cc

SPA_WF = _REPO / "frontend" / "src" / "routes" / "CaptureWorkflow.tsx"


def _client():
    app = flask.Flask(__name__)
    app.register_blueprint(console.bp)
    return app.test_client()


def _seed(category: str = "capture", status: str = "running"):
    d = Path(tempfile.mkdtemp(prefix="goto_"))
    out = d / "out"
    out.mkdir()
    tid = "t_" + d.name[-10:]
    with cc._REG_LOCK:
        cc._REGISTRY[tid] = {
            "task_id": tid, "category": category, "name": "capture_session",
            "label": "reptyle_test", "status": status, "out_dir": str(out),
            "log_path": str(d / "task.log"), "started": 1.0, "finished": None,
            "returncode": None, "output_files": [],
        }
    return tid, out


def test_goto_writes_goto_sentinel():
    c = _client()
    tid, out = _seed()
    r = c.post("/cockpit/api/captures/goto", json={"task_id": tid})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["action"] == "goto"
    assert (out / "GOTO").exists()
    # GOTO must not disturb the finish/cancel pair
    assert not (out / "FINISH").exists()
    assert not (out / "CANCEL").exists()


def test_goto_unknown_task_400():
    c = _client()
    r = c.post("/cockpit/api/captures/goto", json={"task_id": "nope"})
    assert r.status_code == 400


def test_goto_rejects_non_capture_task():
    c = _client()
    tid, _ = _seed(category="report")
    r = c.post("/cockpit/api/captures/goto", json={"task_id": tid})
    assert r.status_code == 400


def test_spa_goto_control_wired():
    src = SPA_WF.read_text(encoding="utf-8")
    # FULL /api/ literal so gui_parity_inventory counts it spa_wired
    assert "/cockpit/api/captures/goto" in src
    assert "Go to my URL" in src
