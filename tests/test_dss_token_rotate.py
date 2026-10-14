"""dss-token-rotate (deep-settings-secrets F1, F3): a rotated API token must stay rotated.

F1: tools/rotate_token.py writes the new ``auth_token`` to app_config.json and the
running app enforces it at once (``_expected_token`` reads the file). The next
settings save (``app._save_app_config``) overlaid app.py's import-time snapshot
``_app_cfg`` -- still holding the OLD token -- over the disk copy, so the leaked
token worked again and the new one was refused.

F3: rotate_token.py read and rewrote app_config.json outside
``global_config.app_config_transaction``, so a settings write landing between its
read and its replace was lost.

r2 (T281b eject): _load_app_config reset the last-seen snapshot only when
app_config.json existed, so a load with no file kept the previous file's snapshot
and an explicit write equal to that stale value (path_allowlist []) yielded to the
seeded disk copy (tests/test_security.py test_seed_does_not_overwrite_existing_config).
"""

from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_ROTATE = _REPO / "tools" / "rotate_token.py"
_OLD = "OLD-TOKEN-dss-token-rotate"

_PROBE = f"""
import json, importlib.util, pathlib
import bulk_downloader.app as a
from bulk_downloader import global_config as gc
spec = importlib.util.spec_from_file_location("rt", {str(_ROTATE)!r})
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)
p = pathlib.Path(a.APP_CFG_FILE)
c = a.app.test_client()
auth = lambda t: c.get("/api/global_config",
                       headers={{"Authorization": "Bearer " + t}}).status_code
rc1 = rt.main(["--config", str(p)])
new1 = json.loads(p.read_text())["auth_token"]
# another writer changes a key the app then edits itself: the app's edit must win
gc.set_config({{"log_level": "WARNING"}})
post = c.post("/api/global_config", json={{"log_level": "DEBUG"}},
              headers={{"Authorization": "Bearer " + new1}}).status_code
a._save_app_config()
d1 = json.loads(p.read_text())
# a second rotation before any restart must survive the next save too
rc2 = rt.main(["--config", str(p)])
new2 = json.loads(p.read_text())["auth_token"]
a._save_app_config()
d2 = json.loads(p.read_text())
print(json.dumps({{"rc": [rc1, rc2], "rotated": len({{{_OLD!r}, new1, new2}}) == 3,
    "post": post, "disk1": {{new1: "new1", {_OLD!r}: "old"}}.get(d1.get("auth_token"), "other"),
    "disk2": {{new2: "new2", new1: "new1", {_OLD!r}: "old"}}.get(d2.get("auth_token"), "other"),
    "log_level": d1.get("log_level"), "log_level2": d2.get("log_level"),
    "old": auth({_OLD!r}), "new1": auth(new1), "new2": auth(new2)}}))
"""


def _run_app_probe(probe: str = _PROBE, start: dict | None = None) -> dict:
    """A fresh app process whose app_config.json already holds the OLD token at
    import, as on a running install: _load_app_config snapshots it into _app_cfg."""
    work = tempfile.mkdtemp(prefix="dss_token_rotate_")
    (Path(work) / "app_config.json").write_text(
        json.dumps(
            start if start is not None else {"auth_token": _OLD, "log_level": "INFO"}
        )
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("BD_INSTALL_DIR", "BD_AUTH_TOKEN", "BD_TOKEN")
    }
    env.update(
        LC_ALL="C",
        PYTHONPATH=str(_REPO),
        BD_DISABLE_KEEPALIVE="1",
        BD_ENVFILE=str(Path(work) / "no.env"),
    )
    r = subprocess.run(
        [sys.executable, "-c", probe],
        check=False,
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        # measured ~4 s per probe (band -n 12); stays under the 240 s item bound
        timeout=120,
    )
    assert r.returncode == 0, f"probe failed: {r.stderr[-800:]}"
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_rotated_token_survives_the_next_settings_save():
    out = _run_app_probe()
    assert out["rc"] == [0, 0] and out["rotated"], out
    assert out["post"] == 200, f"settings save with the NEW token was refused: {out}"
    assert out["disk1"] == "new1", (
        f"DSS_ROTATED_TOKEN_REVERTED: after rotate_token and two settings saves, "
        f"app_config.json holds the {out['disk1']} token; _save_app_config put "
        f"app.py's stale _app_cfg snapshot back over it. {out}"
    )
    assert out["disk2"] == "new2", (
        f"DSS_SECOND_ROTATION_REVERTED: a second rotation before restart was undone "
        f"by the next save (disk holds the {out['disk2']} token). {out}"
    )
    assert out["old"] == 401 and out["new1"] == 401, (
        f"DSS_OLD_TOKEN_ACCEPTED: a rotated-out token still authenticates: {out}"
    )
    assert out["new2"] == 200, f"the rotated-in token is refused: {out}"


def test_settings_save_still_persists_its_own_edit_after_rotation():
    """Over-correction control: a key the app itself changed still wins, even when
    another writer changed it on disk first (the v3.66.964 merge direction)."""
    out = _run_app_probe()
    assert out["log_level"] == "DEBUG" and out["log_level2"] == "DEBUG", (
        f"DSS_OWN_EDIT_LOST: the app's own log_level edit did not persist: {out}"
    )


_RELOAD_PROBE = """
import json, os, pathlib
import bulk_downloader.app as a
from bulk_downloader import global_config as gc
# import loaded ./app_config.json (path_allowlist []); now a second load in a
# workdir with no file, as the fresh_app fixture does for every test
b = pathlib.Path(os.getcwd()) / "second"
b.mkdir()
os.chdir(b)
a._app_cfg.clear()
a._load_app_config()
seeded = list(a._APP_CFG_SEED_PENDING or [])
gc.set_config({"path_allowlist": seeded})  # boot_once persists the seed this way
on_disk = json.loads((b / "app_config.json").read_text()).get("path_allowlist")
c = a.app.test_client()
post = c.post("/api/global_config", json={"path_allowlist": []}).status_code
disk = json.loads((b / "app_config.json").read_text()).get("path_allowlist")
got = (c.get("/api/global_config").get_json() or {}).get("path_allowlist")
print(json.dumps({"seeded": len(seeded), "seed_on_disk": on_disk == seeded,
    "post": post, "disk": disk, "get": got}))
"""


def test_explicit_clear_after_a_reload_without_a_file_is_kept():
    out = _run_app_probe(_RELOAD_PROBE, {"path_allowlist": []})
    assert out["seeded"] >= 1 and out["seed_on_disk"] and out["post"] == 200, out
    assert out["disk"] == [] and out["get"] == [], (
        f"DSS_STALE_SNAPSHOT_CLEAR_LOST: path_allowlist [] after a reload with no "
        f"app_config.json came back as the seeded list: {out}"
    )


def _load_rotate():
    spec = importlib.util.spec_from_file_location("rotate_token_dss", _ROTATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _lock_is_held(cfg: Path) -> bool:
    """True when another open file description holds app_config's sibling lock."""
    with open(f"{cfg}.lock", "a+b") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        return False


def test_lock_probe_can_say_held(tmp_path):
    """Positive control for _lock_is_held: inside the shared transaction it is True."""
    from bulk_downloader.global_config import app_config_transaction

    cfg = tmp_path / "app_config.json"
    assert _lock_is_held(cfg) is False
    with app_config_transaction(cfg):
        assert _lock_is_held(cfg) is True


def test_rotate_token_does_not_lose_a_concurrent_settings_write(tmp_path, monkeypatch):
    """A set_config() that arrives while rotate_token is between its read and its
    replace must survive. Under the shared lock it waits for the rotation; without
    it, it is joined inside the read so the lost update is deterministic."""
    from bulk_downloader import global_config as gc

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BD_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(gc, "_CONFIG_FILE", Path("app_config.json"))
    monkeypatch.setattr(gc, "_cached", None)
    cfg = tmp_path / "app_config.json"
    cfg.write_text(json.dumps({"auth_token": _OLD, "keep": 1}))
    rt = _load_rotate()
    monkeypatch.setattr(rt, "print", lambda *a, **k: None, raising=False)

    seen = []
    writer = threading.Thread(target=lambda: gc.set_config({"dss_concurrent": 7}))
    real_load = json.load

    def observed_load(f, *a, **k):
        data = real_load(f, *a, **k)
        if Path(getattr(f, "name", "")).resolve() == cfg.resolve() and not seen:
            held = _lock_is_held(cfg)
            seen.append(held)
            writer.start()
            if not held:
                writer.join(30)
        return data

    monkeypatch.setattr(json, "load", observed_load)
    rc = rt.main(["--config", str(cfg)])
    writer.join(30)
    assert not writer.is_alive(), "UNKNOWN: set_config did not finish"
    data = json.loads(cfg.read_text())
    assert rc == 0 and data["auth_token"] != _OLD and data["keep"] == 1, data
    assert data.get("dss_concurrent") == 7, (
        f"DSS_ROTATE_LOST_UPDATE: a settings write made during the rotation was lost: {data}"
    )
    assert seen == [True], (
        f"DSS_ROTATE_OUTSIDE_LOCK: rotate_token read app_config.json without "
        f"app_config_transaction held (lock held at read: {seen})"
    )
