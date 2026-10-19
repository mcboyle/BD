"""ss-fix F1-F6 (settings-secrets sweep, O1789): concurrent settings saves and
global_config POST bodies that returned 500 / lost data.

F1 .env editor: two concurrent POSTs of different keys must both land.
F2 store-raw vpn write must not share vpn_config.save()'s tunnels.json.tmp.
F3 a store-raw widgets edit that drops "global" must be live (not the old list).
F4-F6 /api/global_config: non-int cap / watch interval and a list body -> 400.
pc_* tests are positive controls on the same routes.
"""
import copy
import importlib.util
import json
import threading

from flask import Flask

BD_GATE_SCOPE = "module"


def _gc_client():
    from bulk_downloader import app as app_mod
    return app_mod.app.test_client()


# ---- F4-F6: /api/global_config POST ---------------------------------------------------------------------------
def _gc_post_is_400(body):
    import bulk_downloader.app as app_module
    import bulk_downloader.runner as runner
    cfg_before = dict(app_module._app_cfg)
    cap = runner._global_sem_size
    try:
        r = _gc_client().post("/api/global_config", json=body)
        return r, dict(app_module._app_cfg) == cfg_before and runner._global_sem_size == cap
    finally:
        app_module._app_cfg.clear()
        app_module._app_cfg.update(cfg_before)
        runner.set_global_concurrent_cap(cap)


def test_gc_nonint_concurrency_cap_is_400():
    r, unchanged = _gc_post_is_400({"global_max_concurrent": "abc"})
    assert r.status_code == 400, f"SWEEP-GC-1 status={r.status_code} body={r.data[:160]!r}"
    assert "global_max_concurrent" in r.get_json()["error"] and unchanged


def test_gc_nonint_watch_interval_is_400():
    r, unchanged = _gc_post_is_400({"watch_interval_sec": "abc"})
    assert r.status_code == 400, f"SWEEP-GC-2 status={r.status_code} body={r.data[:160]!r}"
    assert "watch_interval_sec" in r.get_json()["error"] and unchanged


def test_gc_list_body_is_400():
    r, unchanged = _gc_post_is_400(["watch_folder"])
    assert r.status_code == 400, f"SWEEP-GC-3 status={r.status_code} body={r.data[:160]!r}"
    assert unchanged


def test_pc_gc_nonint_byte_budget_is_400():
    r, _ = _gc_post_is_400({"global_daily_byte_budget": "abc"})
    assert r.status_code == 400


def test_pc_gc_int_watch_interval_is_applied():
    import bulk_downloader.app as app_module
    before = app_module._app_cfg.get("watch_interval_sec")
    try:
        r = _gc_client().post("/api/global_config", json={"watch_interval_sec": "45"})
        assert r.status_code == 200 and app_module._app_cfg["watch_interval_sec"] == 45
    finally:
        app_module._app_cfg["watch_interval_sec"] = before


# ---- F2/F3: store-raw editor -----------------------------------------------------------------------------------
def _sr_client():
    from bulk_downloader import app_store_raw_editor as SR
    app = Flask(__name__)
    SR.register_routes(app)
    return app.test_client()


def _fresh_widgets_global():
    """What a fresh process would load from the on-disk widgets.json (second module copy)."""
    from bulk_downloader import widgets_config
    spec = importlib.util.spec_from_file_location("_wc_fresh", widgets_config.__file__)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.load()["global"]


def _widgets_scratch(tmp_path, monkeypatch):
    from bulk_downloader import widgets_config as WC
    monkeypatch.setenv("BD_WIDGETS_CONFIG_PATH", str(tmp_path / "widgets.json"))
    saved = copy.deepcopy(WC._state)
    WC._loaded = False
    return WC, saved


def _widgets_restore(WC, saved):
    WC._state.clear()
    WC._state.update(saved)
    WC._loaded = False


def test_store_raw_widgets_dropping_global_takes_effect_live(tmp_path, monkeypatch):
    WC, saved = _widgets_scratch(tmp_path, monkeypatch)
    try:
        c = _sr_client()
        r1 = c.post("/api/settings/store-raw",
                    json={"store": "widgets", "text": json.dumps({"schema_version": 1, "global": []})})
        assert r1.status_code == 200, r1.data[:200]
        r2 = c.post("/api/settings/store-raw", json={"store": "widgets", "text": json.dumps({"schema_version": 1})})
        assert r2.status_code == 200, r2.data[:200]
        live, fresh = WC.load()["global"], _fresh_widgets_global()
        assert live == fresh, \
            f"SWEEP-SR-2 live global={live!r} but a fresh load of the saved file gives {len(fresh)} widgets"
    finally:
        _widgets_restore(WC, saved)


def test_pc_store_raw_widgets_set_global_takes_effect_live(tmp_path, monkeypatch):
    WC, saved = _widgets_scratch(tmp_path, monkeypatch)
    try:
        r = _sr_client().post("/api/settings/store-raw",
                              json={"store": "widgets", "text": json.dumps({"schema_version": 1, "global": []})})
        assert r.status_code == 200 and WC.load()["global"] == _fresh_widgets_global() == []
    finally:
        _widgets_restore(WC, saved)


def test_store_raw_and_vpn_save_do_not_collide(tmp_path, monkeypatch):
    """store-raw _atomic_write and vpn_config.save() both staged <tunnels>.json.tmp, under no shared lock."""
    from bulk_downloader import vpn_config
    monkeypatch.setenv("BD_VPN_CONFIG_PATH", str(tmp_path / "tunnels.json"))
    saved = copy.deepcopy(vpn_config._state)
    vpn_config._loaded = False
    text = json.dumps({"schema_version": 1, "tunnels": []})
    errors, stop = [], threading.Event()

    def saver():
        while not stop.is_set():
            vpn_config.save()

    t = threading.Thread(target=saver, daemon=True)
    t.start()
    c = _sr_client()
    try:
        for _ in range(300):
            r = c.post("/api/settings/store-raw", json={"store": "vpn", "text": text})
            if r.status_code != 200:
                errors.append((r.status_code, (r.get_json() or {}).get("error", "")[:100]))
    finally:
        stop.set()
        t.join(timeout=10)
        vpn_config._state.clear()
        vpn_config._state.update(saved)
        vpn_config._loaded = False
    assert not errors, f"SWEEP-SR-3 {len(errors)}/300 store-raw saves failed: {errors[:3]}"
    leftovers = sorted(p.name for p in tmp_path.iterdir() if p.name != "tunnels.json" and "tunnels" in p.name)
    assert leftovers in ([], ["tunnels.json.tmp"]), f"store-raw left temp files: {leftovers}"


# ---- F1: .env editor -------------------------------------------------------------------------------------------
def _env_client():
    from bulk_downloader import app_envfile_editor
    app = Flask(__name__)
    app_envfile_editor.register_routes(app)
    return app.test_client()


def _two_bool_keys():
    from bulk_downloader import _envfile
    ks = [e["name"] for e in _envfile.EDITOR_KEYS if e["kind"] == "bool" and not e["danger"]]
    assert len(ks) >= 2, ks
    return ks[0], ks[1]


def _saved_env(p):
    from bulk_downloader import _envfile
    return _envfile.parse_envfile(p.read_text(encoding="utf-8")) if p.exists() else {}


def test_envfile_concurrent_posts_keep_both_keys(tmp_path, monkeypatch):
    p = tmp_path / ".env"
    monkeypatch.setenv("BD_ENVFILE", str(p))
    k1, k2 = _two_bool_keys()
    lost, codes = 0, []
    for _ in range(60):
        p.write_text("# scratch\n", encoding="utf-8")
        bar = threading.Barrier(2)

        def post(k):
            c = _env_client()
            bar.wait()
            codes.append(c.post("/api/settings/envfile", json={k: "1"}).status_code)

        ts = [threading.Thread(target=post, args=(k,)) for k in (k1, k2)]
        [t.start() for t in ts]
        [t.join(timeout=30) for t in ts]
        s = _saved_env(p)
        lost += int(s.get(k1) != "1" or s.get(k2) != "1")
    ok = sum(1 for x in codes if x == 200)
    assert ok == len(codes) == 120, f"POST statuses: {ok}/{len(codes)} were 200"
    assert lost == 0, f"SWEEP-ENV-1 {lost}/60 rounds lost a key; POST statuses: {ok}/{len(codes)} were 200 ({k1},{k2})"


def test_pc_envfile_sequential_posts_keep_both_keys(tmp_path, monkeypatch):
    p = tmp_path / ".env"
    monkeypatch.setenv("BD_ENVFILE", str(p))
    k1, k2 = _two_bool_keys()
    c = _env_client()
    assert c.post("/api/settings/envfile", json={k1: "1"}).status_code == 200
    assert c.post("/api/settings/envfile", json={k2: "1"}).status_code == 200
    s = _saved_env(p)
    assert s.get(k1) == "1" and s.get(k2) == "1"
