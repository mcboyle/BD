"""dl95-file-examples-2: a site auto-paused as ``paused_no_button`` must say so.

Measured on test2 (harness-work/UIUX-20260928/download-95/RERUN-file-examples-B6-B.md,
shots/fe95-home.png): after N consecutive pages with no download button the runner
sets ``_state = "paused_no_button"`` and every pending job waits with 0 workers,
while Home shows "All clear - Nothing needs attention" and the site row reads
"Idle". Nothing names the hold or offers the resume it needs.

Contract pinned here:
  - ``_m2_attention_for_site`` returns a ``paused_no_button`` entry whose label
    names the cause (Home's attention card is fed from it);
  - ``/api/sites/v2`` carries that reason on the site row (``hold_reason``);
  - ``/api/queue/v2`` per-site entries carry ``state`` + ``hold_reason`` so the
    Queue offers Resume and names why the waiting jobs do not move;
  - ``/api/dashboard/v2/resolve`` accepts the kind and resumes the runner, so the
    banner's Resolve button does not 400 on the entry it is shown.
"""
from __future__ import annotations

# An ordinary module test: its subject is the module under test, not the tree.
BD_GATE_SCOPE = "module"

import importlib
import threading

import pytest
from flask import Flask

pytestmark = pytest.mark.bd_module_wipe


class _HeldRunner:
    """Just enough runner surface for the attention/sites/resolve paths."""

    def __init__(self, state: str = "paused_no_button", streak: int = 5):
        self._state = state
        self._consec_no_btn = streak
        self.config = {"no_button_threshold": 5}
        self.cookies = []
        self.resumed = 0
        self._event_log = []
        self._lock = threading.Lock()
        self.jobs = {"https://fe.example/a.mp4": {"status": "pending"}}

    def state(self):
        return self._state

    def is_rate_limited(self):
        return False

    def resume(self):
        self.resumed += 1
        self._state = "running"

    def get_status(self, light=False):
        return {"counts": {"pending": 10, "done": 0}, "active": 0}


def test_paused_no_button_is_an_attention_entry_naming_the_cause():
    app = importlib.import_module("bulk_downloader.app")
    entry = app._m2_attention_for_site("fe", _HeldRunner(), {"name": "fe95"})
    assert entry is not None, (
        "DL95-FE2: paused_no_button site produced no attention entry -- "
        "Home shows 'All clear' while every job is held")
    assert entry["kind"] == "paused_no_button"
    assert entry["name"] == "fe95"
    assert "no download button" in entry["label"].lower()
    assert "5" in entry["label"]


def test_running_site_is_not_flagged_as_held():
    """Negative control: the entry is keyed on the hold state, not on the streak."""
    app = importlib.import_module("bulk_downloader.app")
    assert app._m2_attention_for_site(
        "fe", _HeldRunner(state="running", streak=3), {"name": "fe95"}) is None


def test_sites_v2_row_carries_the_hold_reason(monkeypatch):
    core = importlib.import_module("bulk_downloader.app_sites_id_core")
    held, idle = _HeldRunner(), _HeldRunner(state="idle", streak=0)
    monkeypatch.setattr(core, "_app_runners", lambda: {"fe": held, "ok": idle})
    monkeypatch.setattr(core, "_app_s_cfg",
                        lambda: {"fe": {"name": "fe95"}, "ok": {"name": "ok"}})
    flask_app = Flask(__name__)
    flask_app.register_blueprint(core.sites_bp)
    body = flask_app.test_client().get("/api/sites/v2").get_json()
    rows = {s["site_id"]: s for s in body["sites"]}
    assert "no download button" in (rows["fe"].get("hold_reason") or "").lower(), (
        "DL95-FE2: /api/sites/v2 row has no hold_reason for paused_no_button")
    assert rows["ok"].get("hold_reason") == ""


def test_resolve_paused_no_button_resumes_the_runner(monkeypatch):
    dash = importlib.import_module("bulk_downloader.app_dashboard")
    runner = _HeldRunner()
    monkeypatch.setattr(dash, "_app_runners", lambda: {"fe": runner})
    flask_app = Flask(__name__)
    flask_app.register_blueprint(dash.dashboard_bp)
    r = flask_app.test_client().post(
        "/api/dashboard/v2/resolve",
        json={"site_id": "fe", "kind": "paused_no_button"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["ok"] is True
    assert runner.resumed == 1


def test_queue_v2_per_site_carries_state_and_hold_reason(monkeypatch):
    q = importlib.import_module("bulk_downloader.app_queue")
    held = _HeldRunner()
    monkeypatch.setattr(q, "_app_runners", lambda: {"fe": held})
    monkeypatch.setattr(q, "_app_s_cfg", lambda: {"fe": {"name": "fe95"}})
    flask_app = Flask(__name__)
    flask_app.register_blueprint(q.queue_bp)
    body = flask_app.test_client().get("/api/queue/v2").get_json()
    (site,) = [p for p in body["per_site"] if p["site_id"] == "fe"]
    assert site.get("state") == "paused_no_button", (
        "DL95-FE2: /api/queue/v2 per_site has no state -- Queue cannot offer Resume")
    assert "no download button" in (site.get("hold_reason") or "").lower()
