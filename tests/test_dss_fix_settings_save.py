"""dss-fix (O1789 bughunt deep-settings-save, finder bd-worker-S18-D): M2 M3 M4 M5 M6 L3 L5.

Settings writers that lost keys under concurrency, coerced "false" to True, reopened the B19 tunnel-secret collision,
persisted a rejected restore, wrote secrets umask-wide, or answered a bad value with a 500. Every test is a FINDINGS
repro (RED at BASE) plus the legitimate case it must keep. Real Flask test client on the isolated BD_HOME/cwd the
conftest autouse fixture provides; plugin dir and secrets backend are test-local; no network.
"""

import json
import os
import stat
import sys
import threading
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe


@pytest.fixture(scope="module", autouse=True)
def _restore_bd_modules_after_file():
    """Put the bulk_downloader module table back when this file finishes (1034 leaker census)."""
    saved = {
        m: mod
        for m, mod in sys.modules.items()
        if m == "bulk_downloader" or m.startswith("bulk_downloader.")
    }
    try:
        yield
    finally:
        for m in [
            m
            for m in sys.modules
            if m == "bulk_downloader" or m.startswith("bulk_downloader.")
        ]:
            del sys.modules[m]
        sys.modules.update(saved)


def _setup():
    for mod in list(sys.modules):
        if mod.startswith("bulk_downloader"):
            del sys.modules[mod]
    from bulk_downloader import app as a

    return a, a.app.test_client()


@pytest.fixture
def plugin_dir(tmp_path, monkeypatch):
    _a, c = _setup()
    from bulk_downloader import plugins as pl

    d = tmp_path / "plugins-under-test"
    monkeypatch.setattr(pl, "_plugin_dir", lambda: d)
    return c, pl, d / "plugins.json"


# ── M2: plugins.json writes ──────────────────────────────────────────────────
def test_m2_concurrent_plugin_saves_keep_both_keys(plugin_dir):
    c, _pl, fp = plugin_dir
    lost = []
    for i in range(20):
        ts = [
            threading.Thread(
                target=lambda i=i: c.post(
                    "/api/plugins/config", json={"enabled": [f"p{i}"]}
                )
            ),
            threading.Thread(
                target=lambda i=i: c.post(
                    "/api/plugins/config", json={"node_bin": f"/n{i}"}
                )
            ),
        ]
        [t.start() for t in ts]
        [t.join() for t in ts]
        try:
            cfg = json.loads(fp.read_text())
        except ValueError as e:
            lost.append((i, f"unreadable: {e}"))
            continue
        if (cfg.get("enabled"), cfg.get("node_bin")) != ([f"p{i}"], f"/n{i}"):
            lost.append((i, cfg.get("enabled"), cfg.get("node_bin")))
    assert not lost, f"M2: {len(lost)}/20 rounds lost a key: {lost[:3]}"


def test_m2_unreadable_plugins_json_is_refused_and_kept(plugin_dir):
    c, _pl, fp = plugin_dir
    assert (
        c.post("/api/plugins/config", json={"enabled": ["keep_me"]}).status_code == 200
    )  # control
    assert (
        c.post("/api/plugins/config", json={"node_bin": "/usr/bin/node"}).status_code
        == 200
    )
    assert json.loads(fp.read_text()) == {
        "enabled": ["keep_me"],
        "node_bin": "/usr/bin/node",
    }
    torn = '{"enabled": ["keep_me"], "ord'
    fp.write_text(torn)
    resp = c.post("/api/plugins/config", json={"node_bin": "/opt/node"})
    assert resp.status_code == 409, (resp.status_code, resp.get_json())
    assert fp.read_text() == torn, "M2: the unreadable plugins.json was replaced"


# ── M3 / L5: plugin config types ─────────────────────────────────────────────
def test_m3_string_false_never_opens_plugin_full_access(plugin_dir):
    c, pl, fp = plugin_dir
    resp = c.post(
        "/api/plugins/config",
        json={"allow_full_access": "false", "risk_acknowledged": "false"},
    )
    assert resp.status_code == 400, (resp.status_code, resp.get_json())
    assert not fp.exists() and pl.full_access_enabled() is False
    for on in (True, False):  # control: real booleans still save
        ok = c.post(
            "/api/plugins/config",
            json={"allow_full_access": on, "risk_acknowledged": on},
        )
        assert (
            ok.status_code == 200
            and json.loads(fp.read_text())["allow_full_access"] is on
        ), ok.get_json()


def test_l5_plugin_config_list_body_is_a_400(plugin_dir):
    c, _pl, fp = plugin_dir
    resp = c.post("/api/plugins/config", json=["enabled"])
    assert resp.status_code == 400, (resp.status_code, resp.get_json())
    assert not fp.exists()


# ── M3 / L3: Telegram settings types ─────────────────────────────────────────
@pytest.mark.parametrize(
    "body",
    [
        {"tg_bot_enabled": "false"},
        {"tg_bot_allowlist": ["111", "222"]},
        {"tg_bot_token": ["t"]},
    ],
)
def test_m3_l3_tg_settings_refuse_wrong_types(body):
    _a, c = _setup()
    store = Path("notify_apprise.json")
    before = store.read_text() if store.exists() else None
    resp = c.post("/api/tg/settings", json=body)
    assert resp.status_code == 400, (body, resp.status_code, resp.get_json())
    assert (store.read_text() if store.exists() else None) == before, (
        "a refused save still wrote the store"
    )
    ok = c.post(
        "/api/tg/settings", json={"tg_bot_enabled": False, "tg_bot_allowlist": "111"}
    )  # control
    assert (
        ok.status_code == 200 and ok.get_json()["settings"]["tg_bot_allowlist"] == "111"
    ), ok.get_json()
    assert ok.get_json()["settings"]["tg_bot_enabled"] is False


# ── M4: rekey must keep the B19 ':' rule ─────────────────────────────────────
class _FakeBackend:
    name = "dss-fake"

    def __init__(self):
        self.d = {}

    def list_keys(self):
        return list(self.d)

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v):
        self.d[k] = v

    def delete(self, k):
        self.d.pop(k, None)


def test_m4_rekey_to_an_id_with_a_colon_is_refused(monkeypatch):
    _a, c = _setup()
    from bulk_downloader import secrets_store, vpn_config

    fb = _FakeBackend()
    monkeypatch.setattr(secrets_store, "get_backend", lambda: fb)
    for tid in ("a", "x"):
        vpn_config.add_tunnel_config(
            {
                "tunnel_id": tid,
                "name": tid,
                "provider": "custom",
                "backend": "wireguard",
            }
        )
        fb.set(f"{tid}:password", f"pw-{tid}")
    resp = c.post(
        "/api/settings/store-raw/rekey", json={"old_id": "x", "new_id": "a:pw"}
    )
    assert resp.status_code == 400, (resp.status_code, resp.get_json(), fb.d)
    assert fb.d == {"a:password": "pw-a", "x:password": "pw-x"}
    ok = c.post(
        "/api/settings/store-raw/rekey", json={"old_id": "x", "new_id": "y"}
    )  # control
    assert ok.status_code == 200 and fb.d == {
        "a:password": "pw-a",
        "y:password": "pw-x",
    }, (ok.get_json(), fb.d)


# ── M5 M6: dev-suite config snapshot / restore ───────────────────────────────
def test_m5_a_rejected_restore_is_rolled_back_on_disk_and_live():
    a, _c = _setup()
    from bulk_downloader.dev_suite import config_tools as ct

    a._save_app_config()
    before = Path("app_config.json").read_text()
    live = a._app_cfg.get("global_max_concurrent")
    a._app_cfg["dss_mem_only"] = (
        7  # live but not yet on disk: only the memory rollback brings it back
    )
    d = Path("config_snapshots")
    d.mkdir(exist_ok=True)
    (d / "dss_bad.json").write_text(
        json.dumps(
            {"global_max_concurrent": "abc", "dss_new_key": 1, "dss_mem_only": "x"}
        )
    )
    out = ct.config_restore("dss_bad")
    assert out.get("ok") is False, out
    assert Path("app_config.json").read_text() == before, (
        "M5: the rejected snapshot stayed on disk"
    )
    assert (
        a._app_cfg.get("global_max_concurrent") == live
        and "dss_new_key" not in a._app_cfg
        and a._app_cfg.get("dss_mem_only") == 7
    )
    assert out.get("rolled_back") is True, out
    (d / "dss_good.json").write_text(
        json.dumps({"global_max_concurrent": 3})
    )  # control
    good = ct.config_restore("dss_good")
    assert (
        good.get("ok") is True
        and json.loads(Path("app_config.json").read_text())["global_max_concurrent"]
        == 3
    )


def test_m6_snapshot_is_owner_only_like_app_config():
    a, _c = _setup()
    from bulk_downloader.dev_suite import config_tools as ct

    a._save_app_config()
    old = os.umask(0o002)
    try:
        out = ct.config_snapshot("dss_mode")
    finally:
        os.umask(old)
    assert out.get("ok") is True and out["key_count"] > 0, out
    mode = stat.S_IMODE(
        os.stat(os.path.join("config_snapshots", out["snapshot"])).st_mode
    )
    assert mode == 0o600, f"M6: snapshot mode {oct(mode)}; app_config.json is 0600"


# ── L5: 400, not 500 ─────────────────────────────────────────────────────────
def test_l5_widget_entry_with_a_dict_id_is_a_400():
    _a, c = _setup()
    from bulk_downloader import widgets_config as wc

    wid = min(wc.VALID_WIDGET_IDS)
    resp = c.put(
        "/api/widgets/site1", json={"widgets": [{"id": {"x": 1}, "size": "m"}]}
    )
    assert resp.status_code == 400, (
        resp.status_code,
        resp.get_data(as_text=True)[:200],
    )
    ok = c.put(
        "/api/widgets/site1", json={"widgets": [{"id": wid, "size": "sm"}, 1]}
    )  # control: junk dropped
    assert ok.status_code == 200 and ok.get_json()["widgets"] == [
        {"id": wid, "size": "sm"}
    ], ok.get_json()


@pytest.mark.parametrize("body", [{"global_bps": "abc"}, {"per_site_bps": {"s": "x"}}])
def test_l5_supervisor_bad_rates_are_a_400(body):
    a, c = _setup()
    assert a._SUPERVISOR_AVAILABLE, (
        "COULD NOT LOOK: supervisor unavailable in this build"
    )
    resp = c.post("/api/supervisor/configure", json={"enabled": False, **body})
    assert resp.status_code == 400, (
        body,
        resp.status_code,
        resp.get_data(as_text=True)[:200],
    )
    ok = c.post(
        "/api/supervisor/configure",
        json={"enabled": False, "global_bps": "0", "per_site_bps": {"s": 1000}},
    )
    assert ok.status_code == 200 and ok.get_json()["ok"] is True, (
        ok.get_json()
    )  # control
