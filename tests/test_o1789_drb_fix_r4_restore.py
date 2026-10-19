"""drb-fix r4 (O1789; security lens DR3S-C REFUTE F-SEC-1b on r3 + ride F4).

F-SEC-1b  api_backup_restore checked the restored sites_config.json, then
          _load_sites_config re-read it from disk. A second restore that wrote
          the file inside that window put a site the check refuses ("../../x")
          live. The probe below holds one restore inside the window
          deterministically (the lens raced real threads for 90 s).
F4        The app-wide 4 MB body cap refused the app's own backups past 4 MB
          (413 "request body exceeds 4 MB limit").
"""
from __future__ import annotations

import base64
import io
import json
import os
import threading

from bulk_downloader import app_backup
from tests.test_o1789_drb_fix_r2_backup_api import _restore, _sites_file

BD_GATE_SCOPE = "module"

BENIGN = {"s1": "not-a-dict"}  # passes the check, loads no site
EVIL = {"../../RACE_SID": {"name": "r", "start_url": "https://r.test/"}}
MB = 1024 * 1024


def test_fsec1b_restore_cannot_land_between_check_and_load(fresh_app, clean_workdir, monkeypatch):
    from bulk_downloader import app as A
    sites_file = _sites_file(clean_workdir, monkeypatch)
    in_window, evil_done = threading.Event(), threading.Event()
    real_check = app_backup._restored_sites_refusal

    def check_then_hold(app_mod, restored):
        refused = real_check(app_mod, restored)
        if json.loads(restored.read_text()) == BENIGN:
            in_window.set()
            evil_done.wait(timeout=3)  # the window between check and load
        return refused

    monkeypatch.setattr(app_backup, "_restored_sites_refusal", check_then_hold)
    out = {}

    def run(name, body):
        out[name] = _restore(fresh_app, body)
        if name == "evil":
            evil_done.set()

    t1 = threading.Thread(target=run, args=("benign", BENIGN))
    t1.start()
    assert in_window.wait(timeout=30), "benign restore never reached the refusal check"
    t2 = threading.Thread(target=run, args=("evil", EVIL))
    t2.start()
    t1.join(timeout=60); t2.join(timeout=60)
    assert not t1.is_alive() and not t2.is_alive(), "restores did not finish"
    assert out["benign"].status_code == 200 and out["evil"].status_code == 200
    assert not A.runners and not A.s_cfg, (
        f"RESTORE_RACE_LOADED_REFUSED_SITE: runners={sorted(A.runners)} "
        f"benign={out['benign'].get_json()}")
    evil = out["evil"].get_json()
    assert "is not a plain token" in evil.get("reload_refused", ""), evil
    assert json.loads(sites_file.read_text()) == EVIL


def _learned_backup(fresh_app, n_bytes):
    """A real backup (api_backup_create) carrying an incompressible learned/ file."""
    learned = app_backup._data_dir() / "learned"
    learned.mkdir(parents=True, exist_ok=True)
    pad = learned / "pad.json"
    pad.write_text(json.dumps({"pad": base64.b64encode(os.urandom(n_bytes)).decode()}))
    r = fresh_app.post("/api/backup/create", json={"include_db": False})
    assert r.status_code == 200, (r.status_code, r.data[:200])
    return pad, r.data


def _post_zip(client, data):
    return client.post("/api/backup/restore", data={"file": (io.BytesIO(data), "b.zip")},
                       content_type="multipart/form-data")


def test_f4_backup_over_4mb_restores(fresh_app, clean_workdir, monkeypatch):
    _sites_file(clean_workdir, monkeypatch)
    pad, data = _learned_backup(fresh_app, 6 * MB)
    assert len(data) > 4 * MB, len(data)
    want = pad.read_bytes()
    pad.unlink()
    r = _post_zip(fresh_app, data)
    assert r.status_code == 200, f"RESTORE_BODY_CAP_413: {r.status_code} {r.get_json()}"
    assert r.get_json()["ok"] is True, r.get_json()
    assert pad.read_bytes() == want


def test_f4_control_other_api_routes_keep_the_4mb_cap(fresh_app, clean_workdir, monkeypatch):
    _sites_file(clean_workdir, monkeypatch)
    # a route that reads its body (get_json): the app-wide cap still refuses it
    r = fresh_app.post("/api/backup/create", json={"include_db": False, "pad": "x" * (5 * MB)})
    assert r.status_code == 413, r.status_code
    js = r.get_json()
    assert js["limit_bytes"] == 4 * MB and "4 MB" in js["error"], js


def test_f4_restore_over_its_own_cap_is_413_naming_that_cap(fresh_app, clean_workdir, monkeypatch):
    _sites_file(clean_workdir, monkeypatch)
    monkeypatch.setattr(app_backup, "_RESTORE_UPLOAD_MAX", 2 * MB)
    r = _post_zip(fresh_app, os.urandom(3 * MB))
    assert r.status_code == 413, r.status_code
    js = r.get_json()
    assert js["limit_bytes"] == 2 * MB and "2 MB" in js["error"], js
