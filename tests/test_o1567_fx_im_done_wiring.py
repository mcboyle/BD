"""fx-im-done-wiring (O1567, operator ruling 05:01Z): a pending manual login must be finishable from the UI.

bd4 blacked 2026-09-30 04:39Z: the operator finished the takeover and "clicked I'm Done"; no login_manual_done request
reached the app. The app tells the operator to "click I'm Done" (browser banner, login status), but no UI rendered
an I'm Done: the React app never read ``awaiting_manual_login``, the Home "Needs attention" list had no entry for
it, and the dashboard Resolve only STARTS a manual login. The one path was Site actions -> "Manual login done".

Contract pinned here (backend half; the buttons are pinned in AttentionBanner/ManualLoginPending vitests):
  - ``_m2_attention_for_site`` returns a ``manual_login_pending`` entry, ahead of every other kind, whose label
    names the "I'm Done" button;
  - ``/api/dashboard/v2`` sorts it first;
  - ``/api/sites/v2`` rows carry ``awaiting_manual_login`` so the Site page can offer I'm Done / Cancel;
  - the runner's pending-login status text names where that button is.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import importlib
import threading

import pytest
from flask import Flask

from tests.frontend_vitest import run_vitest

pytestmark = pytest.mark.bd_module_wipe


class _Runner:
    """Just enough runner surface for the attention / sites-v2 paths."""

    def __init__(self, awaiting: bool, captcha: bool = False):
        self._awaiting = awaiting
        self._captcha_pending = captcha
        self._state = "idle"
        self._consec_no_btn = 0
        self.config = {}
        self.cookies = []
        self._event_log = []
        self._lock = threading.Lock()
        self.jobs = {}

    def is_awaiting_manual_login(self):
        return self._awaiting

    def state(self):
        return self._state

    def is_rate_limited(self):
        return False

    def get_status(self, light=False):
        return {"counts": {}, "active": 0}


def test_pending_manual_login_is_an_attention_entry_naming_im_done():
    app = importlib.import_module("bulk_downloader.app")
    entry = app._m2_attention_for_site("bk", _Runner(awaiting=True), {"name": "blacked"})
    assert entry is not None and entry.get("kind") == "manual_login_pending", (
        "fx-im-done-wiring: a site awaiting a manual login produced no manual_login_pending attention entry -- "
        f"Home offers no I'm Done ({entry})")
    assert entry["name"] == "blacked"
    assert "I'm Done" in entry["label"], entry


def test_pending_manual_login_outranks_a_captcha_flag():
    app = importlib.import_module("bulk_downloader.app")
    entry = app._m2_attention_for_site("bk", _Runner(awaiting=True, captcha=True), {"name": "blacked"})
    assert entry["kind"] == "manual_login_pending", entry


def test_no_pending_login_no_entry():
    """Negative control: the entry is keyed on the pending takeover, nothing else."""
    app = importlib.import_module("bulk_downloader.app")
    assert app._m2_attention_for_site("bk", _Runner(awaiting=False), {"name": "blacked"}) is None


def test_sites_v2_row_carries_awaiting_manual_login(monkeypatch):
    core = importlib.import_module("bulk_downloader.app_sites_id_core")
    monkeypatch.setattr(core, "_app_runners", lambda: {"bk": _Runner(awaiting=True), "ok": _Runner(awaiting=False)})
    monkeypatch.setattr(core, "_app_s_cfg", lambda: {"bk": {"name": "blacked"}, "ok": {"name": "ok"}})
    flask_app = Flask(__name__)
    flask_app.register_blueprint(core.sites_bp)
    rows = {s["site_id"]: s for s in flask_app.test_client().get("/api/sites/v2").get_json()["sites"]}
    assert rows["bk"].get("awaiting_manual_login") is True, (
        "fx-im-done-wiring: /api/sites/v2 row has no awaiting_manual_login -- the Site page cannot offer I'm Done")
    assert rows["ok"].get("awaiting_manual_login") is False


def test_pending_status_text_names_where_im_done_is():
    src = importlib.import_module("bulk_downloader.runner_auth").__file__
    text = open(src, encoding="utf-8").read()
    assert "click I'm Done in the takeover panel" not in text, (
        "fx-im-done-wiring: the status still points at a 'takeover panel' that no UI renders")
    assert "Needs attention" in text, "the pending-login status must name where I'm Done is"


@pytest.mark.parametrize("spec", [
    "src/components/AttentionBanner.manualLogin.test.tsx",
    "src/routes/SiteDetail.manualLogin.test.tsx",
])
def test_im_done_and_cancel_are_rendered_and_post_the_takeover_endpoints(spec):
    """Home banner (3 cases) and Site page (2 cases), each with a no-pending-login control."""
    run_vitest(spec, expected_tests={"src/components/AttentionBanner.manualLogin.test.tsx": 3,
                                     "src/routes/SiteDetail.manualLogin.test.tsx": 2}[spec])
