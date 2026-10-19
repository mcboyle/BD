"""sl-lock (sites-lifecycle FINDINGS row 1): per-site lifecycle lock.

On main the per-site lock was one of 64 striped RLocks (hash(sid) % 64), held
by every /api/sites/<sid> request for its whole run.  Unrelated sites on one
stripe blocked each other, and a slow handler (storage_tier/run_now,
hooks/test) blocked every same-site read for as long as it ran.

RED at the stripe base: a stripe-colliding site's readiness and the same
site's watch/status wait on a parked run_now / hook test.
NEG: two transitions on one site still serialize; config import still waits
for an in-flight site route; a DELETE that lands while a slow handler runs
unlocked is reported (SL_LOCK_SITE_CHANGED_DURING_SLOW_HANDLER) as a 409.
"""
from __future__ import annotations

import gc
import logging
import threading

import pytest

BD_GATE_SCOPE = "module"

_WAIT_S = 5.0


def _stripe(sid):
    return hash(str(sid)) % 64


def _colliding_sid(sid, prefix):
    n = 0
    while _stripe(f"{prefix}-{n}") != _stripe(sid):
        n += 1
    return f"{prefix}-{n}"


def _plant(A, client, name, tmp_path):
    r = client.post("/api/sites", json={"name": name})
    assert r.status_code in (200, 201), r.get_data(as_text=True)[:300]
    sid = r.get_json()["id"]
    A.s_cfg[sid]["download_dir"] = str(tmp_path / name)
    return sid


def _call(client, method, url, **kw):
    """Issue a request in a thread; return (thread, result dict)."""
    out = {}

    def run():
        out["r"] = client.open(url, method=method, **kw)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, out


def _done_within(t, out, what):
    t.join(_WAIT_S)
    assert not t.is_alive(), f"SL_LOCK_BLOCKED: {what} waited >{_WAIT_S}s on another request's site lock"
    return out["r"]


@pytest.fixture
def parked_run_now(fresh_app, monkeypatch, tmp_path):
    from bulk_downloader import app as A
    from bulk_downloader import storage_tier

    entered, release = threading.Event(), threading.Event()

    def slow_migration(site_id, cfg, dl_dir, *a, **k):
        entered.set()
        assert release.wait(30), "test never released the parked migration"
        return {"moved": 0}

    monkeypatch.setattr(storage_tier, "run_site_migration", slow_migration)
    sid = _plant(A, fresh_app, "sl-lock-slow", tmp_path)
    t, out = _call(fresh_app, "POST", f"/api/sites/{sid}/storage_tier/run_now")
    assert entered.wait(_WAIT_S), "run_now never reached the migration"
    try:
        yield A, fresh_app, sid, release, t, out
    finally:
        release.set()
        t.join(_WAIT_S)


def test_run_now_does_not_block_same_site_watch_status(parked_run_now):
    A, client, sid, release, t, out = parked_run_now
    r = _done_within(*_call(client, "GET", f"/api/sites/{sid}/watch/status"),
                     "same-site GET watch/status during run_now")
    assert r.status_code == 200
    release.set()
    r = _done_within(t, out, "run_now after release")
    assert r.status_code == 200 and r.get_json()["ok"] is True, r.get_data(as_text=True)[:300]


def test_run_now_does_not_block_stripe_colliding_site_readiness(parked_run_now):
    A, client, sid, _release, _t, _out = parked_run_now
    other = _colliding_sid(sid, "sl-lock-other")
    A.s_cfg[other] = {"name": other}
    try:
        r = _done_within(*_call(client, "GET", f"/api/sites/{other}/readiness"),
                         "another site's GET readiness during run_now")
    finally:
        A.s_cfg.pop(other, None)
    assert r.status_code == 200


def test_held_site_lock_does_not_block_unrelated_stripe_colliding_site(fresh_app):
    from bulk_downloader import app as A
    from bulk_downloader import app_state

    held = "sl-lock-held"
    other = _colliding_sid(held, "sl-lock-unrelated")
    A.s_cfg[other] = {"name": other}
    lock = app_state.site_lifecycle_lock(held)
    lock.acquire()
    try:
        r = _done_within(*_call(fresh_app, "GET", f"/api/sites/{other}/readiness"),
                         "unrelated site's readiness while another site's lock is held")
    finally:
        lock.release()
        A.s_cfg.pop(other, None)
    assert r.status_code == 200


def test_hook_test_does_not_block_same_site_reads(fresh_app, monkeypatch, tmp_path):
    from bulk_downloader import app as A
    from bulk_downloader import hooks

    entered, release = threading.Event(), threading.Event()

    def slow_webhook(url, payload, *a, **k):
        entered.set()
        assert release.wait(30)
        return True, "sent", None

    monkeypatch.setattr(hooks, "send_webhook", slow_webhook)
    sid = _plant(A, fresh_app, "sl-lock-hook", tmp_path)
    t, out = _call(fresh_app, "POST", f"/api/sites/{sid}/hooks/test",
                   json={"kind": "webhook", "override": "http://127.0.0.1:9/x"})
    assert entered.wait(_WAIT_S), "hooks/test never reached the webhook"
    try:
        r = _done_within(*_call(fresh_app, "GET", f"/api/sites/{sid}/watch/status"),
                         "same-site GET watch/status during hooks/test")
        assert r.status_code == 200
    finally:
        release.set()
    r = _done_within(t, out, "hooks/test after release")
    assert r.status_code == 200 and r.get_json()["ok"] is True, r.get_data(as_text=True)[:300]


def test_neg_transitions_on_one_site_still_serialize(fresh_app, tmp_path):
    from bulk_downloader import app as A
    from bulk_downloader import app_state

    sid = _plant(A, fresh_app, "sl-lock-serial", tmp_path)
    lock = app_state.site_lifecycle_lock(sid)
    lock.acquire()
    try:
        t, out = _call(fresh_app, "DELETE", f"/api/sites/{sid}")
        t.join(0.5)
        assert t.is_alive(), "SL_LOCK_NEG_DEAD: DELETE ran while another transition owned the site lock"
        assert sid in A.s_cfg
    finally:
        lock.release()
    r = _done_within(t, out, "DELETE after the holder released")
    assert r.status_code == 200 and sid not in A.s_cfg


def test_neg_delete_during_unlocked_run_now_is_reported(parked_run_now, caplog):
    A, client, sid, release, t, out = parked_run_now
    caplog.set_level(logging.WARNING)
    r = _done_within(*_call(client, "DELETE", f"/api/sites/{sid}"),
                     "DELETE during run_now")
    assert r.status_code == 200
    release.set()
    r = _done_within(t, out, "run_now after release")
    assert r.status_code == 409, r.get_data(as_text=True)[:300]
    assert "SL_LOCK_SITE_CHANGED_DURING_SLOW_HANDLER" in caplog.text


def test_neg_import_gate_excludes_every_site_including_unknown_sids():
    from bulk_downloader import app_state

    site = app_state.site_lifecycle_lock("sl-lock-gate-a")
    site.acquire()
    importer_in = threading.Event()

    def importer():
        with app_state.all_site_lifecycle_locks():
            importer_in.set()

    t = threading.Thread(target=importer, daemon=True)
    t.start()
    assert not importer_in.wait(0.3), "all-sites gate entered while a site lock was held"
    # the holder re-enters its own (and another) site lock despite the queued importer
    other = app_state.site_lifecycle_lock("sl-lock-gate-b")
    assert other.acquire(timeout=1), "holder could not take a second site lock behind a queued importer"
    other.release()
    site.release()
    assert importer_in.wait(_WAIT_S)
    t.join(_WAIT_S)

    with app_state.all_site_lifecycle_locks():
        got = {}
        w = threading.Thread(target=lambda: got.setdefault(
            "ok", app_state.site_lifecycle_lock("sl-lock-never-seen").acquire(timeout=0.3)))
        w.start()
        w.join(_WAIT_S)
        assert got["ok"] is False, "a never-seen sid took its lock inside the all-sites transaction"


def test_registry_is_bounded_by_live_references():
    from bulk_downloader import app_state

    for i in range(500):
        with app_state.site_lifecycle_lock(f"sl-lock-attacker-{i}"):
            pass
    gc.collect()
    leaked = [k for k in list(app_state._site_lifecycle_locks.keys())
              if k.startswith("sl-lock-attacker-")]
    assert leaked == [], f"SL_LOCK_REGISTRY_LEAK: {len(leaked)} released locks still registered"

    lock = app_state.site_lifecycle_lock("sl-lock-same")
    assert app_state.site_lifecycle_lock("sl-lock-same") is lock
    lock.acquire()
    lock_id = id(lock)
    del lock
    gc.collect()
    again = app_state.site_lifecycle_lock("sl-lock-same")
    assert id(again) == lock_id and again._is_owned(), "a held lock was replaced by a twin"
    again.release()
