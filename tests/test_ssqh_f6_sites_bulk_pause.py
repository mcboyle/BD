"""ssqh-f6 -- Sites bulk Pause on a site that is not running is a no-op.

FINDINGS spa-sites-queue-history F6: POST /api/sites/v2/bulk {"action":
"pause"} on an idle site returned applied_to 1 while the site stayed idle, so
the SPA toasted "Paused 1 site". runner.pause() returns early unless the site
is running; the bulk route counted every call that did not raise.

Fix: a pause of a site that was not running is not counted in applied_to and
is reported in a separate ``skipped`` list (not ``errors``: nothing failed).

Real SiteRunner objects over the isolated per-test DB; direct handler call
(CSRF and the rate limiter stubbed) as in test_bug1_idle_site_delete.
"""

# H622 slice A. An ordinary module test: its subject is the module under
# test, not the tree, so it is not a repo-wide CI gate.
BD_GATE_SCOPE = "module"

import importlib

import pytest

pytestmark = pytest.mark.bd_module_wipe

TAG = "SSQH-F6"

st = None
app = None
site_core = None
SiteRunner = None


@pytest.fixture(autouse=True)
def _f6_subjects(isolated_bd_home, monkeypatch):
    """Import the app only after canonical cwd/env/module isolation."""
    global st, app, site_core, SiteRunner
    st = importlib.import_module("bulk_downloader.app_state")
    app = importlib.import_module("bulk_downloader.app").app
    site_core = importlib.import_module("bulk_downloader.app_sites_id_core")
    SiteRunner = importlib.import_module("bulk_downloader.runner").SiteRunner
    importlib.import_module("bulk_downloader.db").db_init()
    monkeypatch.setattr(site_core, "_check_csrf", lambda: None)
    monkeypatch.setattr(site_core, "_rate_check", lambda _key: True)
    monkeypatch.setattr(SiteRunner, "_worker_loop", lambda self, *a, **k: None)
    made = []
    yield made
    for sid, r in made:
        st.runners.pop(sid, None)
        r._sched_stop.set()
    st = app = site_core = SiteRunner = None


def _runner(made, sid, state):
    r = SiteRunner(sid, {"name": sid, "auto_teach_first_run": False})
    if state == "running":
        r._state = "running"
        r._pause.set()
    else:
        r._state = state
    st.runners[sid] = r
    made.append((sid, r))
    return r


def _bulk_pause(site_ids):
    with app.test_request_context(
        "/api/sites/v2/bulk",
        method="POST",
        json={"action": "pause", "site_ids": site_ids},
    ):
        return site_core.api_sites_v2_bulk().get_json()


@pytest.mark.parametrize("state", ["idle", "stopped", "done", "paused",
                                   "window_paused"])
def test_bulk_pause_of_not_running_site_is_not_applied(_f6_subjects, state):
    sid = f"f6_{state}"
    r = _runner(_f6_subjects, sid, state)
    payload = _bulk_pause([sid])
    assert payload["applied_to"] == 0, (
        f"{TAG} {state} pause counted as applied: {payload}")
    assert payload["errors"] == [], payload
    assert payload["total"] == 1, payload
    assert payload.get("skipped") == [{"site_id": sid, "state": state}], (
        f"{TAG} {state} no-op pause not reported in skipped: {payload}")
    assert r.state() == state


def test_bulk_pause_counts_only_the_running_site(_f6_subjects):
    """Positive control: a running site is paused and counted; the idle
    site in the same request is skipped, not failed."""
    run = _runner(_f6_subjects, "f6_run", "running")
    _runner(_f6_subjects, "f6_idle", "idle")
    payload = _bulk_pause(["f6_run", "f6_idle"])
    assert run.state() == "paused", run.state()
    assert payload["applied_to"] == 1, (
        f"{TAG} mixed pause applied_to wrong: {payload}")
    assert payload["total"] == 2, payload
    assert payload["errors"] == [], payload
    assert payload.get("skipped") == [
        {"site_id": "f6_idle", "state": "idle"}], payload


def test_bulk_resume_still_counts_as_before(_f6_subjects):
    """Scope guard: only pause gains the no-op accounting."""
    _runner(_f6_subjects, "f6_res", "paused")
    with app.test_request_context(
        "/api/sites/v2/bulk",
        method="POST",
        json={"action": "resume", "site_ids": ["f6_res"]},
    ):
        payload = site_core.api_sites_v2_bulk().get_json()
    assert payload["applied_to"] == 1, payload
    assert payload.get("skipped", []) == [], payload
