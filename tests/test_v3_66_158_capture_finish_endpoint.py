"""v3.66.158 #2 — cockpit "Finish capture" endpoint.

capture_session.py (v153) waits for a FINISH/CANCEL sentinel when it has no
terminal (the noVNC subprocess case). This endpoint + cc.finish_capture write
that sentinel so a cockpit operator ends the capture from the task row instead
of a second SSH shell. The browser/noVNC leg is verified on the install; here
we test the endpoint + helper (sentinel written, validation) via test client.
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

cc = console.cc  # the same cockpit_core instance the endpoint uses


def _client():
    app = flask.Flask(__name__)
    app.register_blueprint(console.bp)
    return app.test_client()


def _seed(category: str = "capture", status: str = "running"):
    d = Path(tempfile.mkdtemp(prefix="cap158_"))
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


def test_finish_writes_finish_sentinel() -> None:
    c = _client()
    tid, out = _seed()
    r = c.post("/cockpit/api/captures/finish", json={"task_id": tid})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["action"] == "finish"
    assert (out / "FINISH").exists()
    assert not (out / "CANCEL").exists()


def test_discard_writes_cancel_sentinel() -> None:
    c = _client()
    tid, out = _seed()
    r = c.post("/cockpit/api/captures/finish", json={"task_id": tid, "discard": True})
    assert r.status_code == 200 and r.get_json()["action"] == "discard"
    assert (out / "CANCEL").exists()


def test_unknown_task_rejected() -> None:
    c = _client()
    r = c.post("/cockpit/api/captures/finish", json={"task_id": "does_not_exist"})
    assert r.status_code == 400
    assert "no such task" in r.get_json()["error"]


def test_non_capture_task_rejected() -> None:
    c = _client()
    tid, _out = _seed(category="report")
    r = c.post("/cockpit/api/captures/finish", json={"task_id": tid})
    assert r.status_code == 400
    assert "not a capture task" in r.get_json()["error"]


def test_tasks_feed_exposes_category_and_outdir() -> None:
    # the UI gates the finish/discard buttons on these fields
    c = _client()
    tid, _out = _seed()
    tasks = c.get("/cockpit/api/tasks").get_json()["tasks"]
    row = [x for x in tasks if x["task_id"] == tid][0]
    assert row.get("category") == "capture"
    assert row.get("out_dir")


def test_finish_capture_helper_direct() -> None:
    tid, out = _seed()
    res = cc.finish_capture(tid)
    assert res["action"] == "finish"
    assert (out / "FINISH").exists()
