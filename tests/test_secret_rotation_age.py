"""#5 -- secret last-rotated AGE. Records a per-key rotation timestamp on
set/rotate and surfaces AGE ONLY (never a secret value) via /api/secrets/usage.

Invariants under test:
  * set() stamps a rotation timestamp; rotation_ages() reports a non-negative age
  * re-setting a key (a rotation) advances the timestamp
  * delete() drops the rotation entry
  * the rotation metadata + rotation_ages() output NEVER contain a secret value
  * age is computed from the stored timestamp (deterministic with now=)
  * the stamp helpers are best-effort and never raise into a credential op
  * /api/secrets/usage exposes the age, never the value

Crypto is available in this env, so we exercise the real MasterPasswordBackend
(the backend stash actually uses). clean_workdir isolates secrets.json /
secrets_meta.json per test; we save/restore the module backend global where we
mutate it so other suites are not poisoned.
"""
import json
import time
from pathlib import Path

from bulk_downloader import secrets_store as ss
from bulk_downloader import app as bd_app

_SENTINEL = "ROTATION_SENTINEL_VALUE_do_not_echo_7Q2x"


def _master_direct():
    """A directly-instantiated, unlocked master backend (no module-global
    mutation; set/delete still write the shared secrets_meta.json in cwd)."""
    be = ss.MasterPasswordBackend()
    assert be.unlock("test-pw-rotation")
    return be


def test_set_stamps_rotation_age(clean_workdir):
    be = _master_direct()
    be.set("rotkey-1", "a-secret")
    ages = ss.rotation_ages()
    assert "rotkey-1" in ages
    assert ages["rotkey-1"]["age_seconds"] >= 0
    assert ages["rotkey-1"]["age_days"] is not None


def test_reset_updates_rotation_timestamp(clean_workdir):
    be = _master_direct()
    be.set("rotkey-2", "v1")
    t1 = ss.rotation_ages()["rotkey-2"]["rotated_at_epoch"]
    time.sleep(0.02)
    be.set("rotkey-2", "v2")            # re-set == a rotation of the value
    t2 = ss.rotation_ages()["rotkey-2"]["rotated_at_epoch"]
    assert t2 >= t1


def test_delete_removes_rotation(clean_workdir):
    be = _master_direct()
    be.set("rotkey-3", "v")
    assert "rotkey-3" in ss.rotation_ages()
    be.delete("rotkey-3")
    assert "rotkey-3" not in ss.rotation_ages()


def test_rotation_meta_never_stores_secret_value(clean_workdir):
    be = _master_direct()
    be.set("rotkey-4", _SENTINEL)
    meta_txt = (Path("secrets_meta.json").read_text(encoding="utf-8")
                if Path("secrets_meta.json").exists() else "")
    assert _SENTINEL not in meta_txt, "secret value leaked into secrets_meta.json"
    assert _SENTINEL not in json.dumps(ss.rotation_ages())


def test_rotation_ages_empty_without_meta(clean_workdir):
    assert ss.rotation_ages() == {}


def test_age_is_computed_from_timestamp(clean_workdir):
    ss._stamp_rotation("agekey")
    ts = ss.rotation_ages()["agekey"]["rotated_at_epoch"]
    later = ts + 172800  # +2 days
    a = ss.rotation_ages(now=later)["agekey"]
    assert a["age_days"] == 2.0
    assert a["age_seconds"] == 172800


def test_stamp_is_best_effort_never_raises(clean_workdir):
    ss._stamp_rotation("x")
    ss._unstamp_rotation("x")
    ss._unstamp_rotation("never-existed")  # no-op, must not raise


def test_usage_route_surfaces_age_not_value(clean_workdir):
    saved_be, saved_pref = ss._backend, ss._backend_pref
    try:
        assert ss.configure_backend("master_password")
        be = ss.get_backend()
        assert be.unlock("test-pw-rotation")
        be.set("rotkey-route", _SENTINEL)
        c = bd_app.app.test_client()
        r = c.get("/api/secrets/usage")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        data = json.loads(body)
        assert "rotation" in data
        assert "rotkey-route" in data["rotation"]
        assert data["rotation"]["rotkey-route"]["age_days"] is not None
        assert _SENTINEL not in body, "secret value leaked into /api/secrets/usage"
    finally:
        ss._backend, ss._backend_pref = saved_be, saved_pref
