"""plugins-fix r1: bughunt plugins-exec-surface F1 F2 F3 F4 F10.

F1  a macro save whose site_id carries "../" wrote outside macros/ (and could
    replace INSTALL_DIR/sites_config.json); delete reached the same paths.
F2  plugin install wrote plugin-dir control files (plugins.json,
    .plugin_state.json, the registry) with force=1, and uninstall then
    deleted them.
F3  install/uninstall read-modify-wrote the registry with no lock, so two
    concurrent installs lost a row.
F4  a plugin skipped by the capability gate kept the hooks and processors it
    registered at import time.
F10 missing bounds: an oversized plugins.json patch was written whole, a
    non-object /api/macros/validate body was a 500 (or ok:true), and the tool
    bridge buffered all child output and left the child's own children
    running past the timeout.

Local Flask test apps (only the blueprint under test), scratch dirs, no network.
"""

from __future__ import annotations

import fcntl
import io
import json
import os
import shutil
import signal
import sys
import threading
import time
import tracemalloc
from pathlib import Path

import pytest
from flask import Flask

from bulk_downloader import macro_recorder as mr
from bulk_downloader import plugins as P
from bulk_downloader import tool_bridge as tb
from bulk_downloader.app_macros import macros_bp

BD_GATE_SCOPE = "module"


def _json_files_under(root: Path) -> set:
    # .json only: the app's own log file may appear under the scratch root.
    return {str(p.relative_to(root)) for p in root.rglob("*.json") if p.is_file()}


@pytest.fixture
def install_dir(tmp_path, monkeypatch):
    root = tmp_path / "install"
    root.mkdir()
    monkeypatch.setenv("BD_INSTALL_DIR", str(root))
    (root / "sites_config.json").write_text('{"sites": {"keep": {}}}', "utf-8")
    (root / "plugins").mkdir()
    return root


@pytest.fixture
def macros_client():
    app = Flask("plugins_fix_r1_macros")
    app.register_blueprint(macros_bp)
    return app.test_client()


@pytest.fixture
def pdir(tmp_path, monkeypatch):
    d = tmp_path / "plugins"
    d.mkdir()
    monkeypatch.setattr(P, "_plugin_dir", lambda: d)
    for var in ("BD_PLUGINS_ENABLE", "BD_PLUGINS_ALLOW_FULL_ACCESS"):
        monkeypatch.delenv(var, raising=False)
    # Put the module table back as found: drop what the test loaded, then
    # restore the entries that were there before it (the 1034 census idiom).
    saved = {
        m: mod for m, mod in sys.modules.items() if m.startswith("bd_plugin_pfr1_")
    }
    P.reset()
    try:
        yield d
    finally:
        P.reset()
        for name in [m for m in sys.modules if m.startswith("bd_plugin_pfr1_")]:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def _candidate(tmp_path: Path, name: str, text: str) -> Path:
    d = tmp_path / ("src-%d" % len(list(tmp_path.glob("src-*"))))
    d.mkdir()
    p = d / name
    p.write_text(text, "utf-8")
    return p


def _man(name: str) -> str:
    return (
        "PLUGIN = "
        + json.dumps(
            {"name": name, "version": "1.0.0", "api_version": P.PLUGIN_API_VERSION}
        )
        + "\n"
    )


# ── F1 ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "site_id,name",
    [
        ("../sites", "config"),
        ("../escape26", "m"),
        ("../../root26", "m"),
        ("../plugins/", "m"),
        ("a/b", "m"),
        (".", "m"),
    ],
)
def test_f1_save_site_id_cannot_leave_macros_dir(
    install_dir, macros_client, site_id, name
):
    before = _json_files_under(install_dir.parent)
    cfg = (install_dir / "sites_config.json").read_text("utf-8")
    r = macros_client.post(
        "/api/macros/save",
        json={"site_id": site_id, "name": name, "actions": [{"kind": "wait", "ms": 1}]},
    )
    body = r.get_json()
    after = _json_files_under(install_dir.parent)
    escaped = sorted(p for p in after - before if not p.startswith("install/macros/"))
    assert escaped == [], f"PFR1-F1-SAVE-ESCAPED {site_id!r}: wrote {escaped}"
    assert (install_dir / "sites_config.json").read_text("utf-8") == cfg, (
        "PFR1-F1-SITES-CONFIG-REPLACED"
    )
    assert body.get("ok") is False, f"PFR1-F1-SAVE-ACCEPTED {body!r}"


def test_f1_delete_and_get_cannot_reach_sites_config(install_dir):
    assert mr.get_macro("../sites", "config") is None, "PFR1-F1-GET-ESCAPED"
    assert mr.delete_macro("../sites", "config") is False, "PFR1-F1-DELETE-ESCAPED"
    assert (install_dir / "sites_config.json").is_file(), "PFR1-F1-SITES-CONFIG-DELETED"


def test_f1_control_legit_save_lands_in_macros(install_dir, macros_client):
    r = macros_client.post(
        "/api/macros/save",
        json={
            "site_id": "Acme.example",
            "name": "Log In",
            "actions": [{"kind": "wait", "ms": 1}],
        },
    )
    assert r.get_json().get("ok") is True, r.get_json()
    assert (install_dir / "macros" / "acme.example_log-in.json").is_file()
    assert mr.get_macro("acme.example", "log-in")["name"] == "log-in"
    assert mr.delete_macro("acme.example", "log-in") is True


# ── F2 ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "name",
    [
        "plugins.json",
        ".plugin_state.json",
        "plugins.registry.json",
        "plugins.json.lock",
    ],
)
def test_f2_install_refuses_plugin_dir_control_files(pdir, tmp_path, name):
    original = '{"allow_full_access": false}'
    (pdir / name).write_text(original, "utf-8")
    src = _candidate(tmp_path, name, _man("ctl"))
    res = P.install_plugin(str(src), ack=True, force=True)
    assert res.get("installed") is False, (
        f"PFR1-F2-CONTROL-FILE-INSTALLED {name}: {res!r}"
    )
    assert (pdir / name).read_text("utf-8") == original, (
        f"PFR1-F2-CONTROL-FILE-REPLACED {name}"
    )


def test_f2_uninstall_refuses_a_control_file_even_with_a_registry_row(pdir):
    (pdir / "plugins.json").write_text("{}", "utf-8")
    P._write_registry({"schema": 1, "plugins": {"plugins.json": {"name": "x"}}})
    res = P.uninstall_plugin("plugins.json", ack=True)
    assert res.get("uninstalled") is False, f"PFR1-F2-CONTROL-FILE-UNINSTALLED {res!r}"
    assert (pdir / "plugins.json").is_file(), "PFR1-F2-CONTROL-FILE-DELETED"


def test_f2_control_plugin_install_and_uninstall_still_work(pdir, tmp_path):
    src = _candidate(tmp_path, "good.py", _man("good"))
    assert P.install_plugin(str(src), ack=True)["installed"] is True
    assert (pdir / "good.py").is_file()
    assert P.uninstall_plugin("good.py", ack=True)["uninstalled"] is True
    assert not (pdir / "good.py").exists()


# ── F3 ────────────────────────────────────────────────────────────────
def test_f3_concurrent_installs_keep_every_registry_row(pdir, tmp_path, monkeypatch):
    """The first install pauses (bounded) right after reading the registry; the
    second install runs meanwhile. Unlocked, both read an empty registry and
    the second write is lost under the first."""
    orig = P._read_registry
    calls = []
    second_read = threading.Event()

    def read_then_pause():
        reg = orig()
        calls.append(threading.current_thread().name)
        if len(calls) == 1:
            second_read.wait(1.0)
        else:
            second_read.set()
        return reg

    monkeypatch.setattr(P, "_read_registry", read_then_pause)
    srcs = {n: _candidate(tmp_path, n, _man(n[:-3])) for n in ("alpha.py", "beta.py")}
    results = {}

    def go(n):
        results[n] = P.install_plugin(str(srcs[n]), ack=True)

    ta = threading.Thread(target=go, args=("alpha.py",), name="alpha")
    ta.start()
    deadline = time.monotonic() + 5
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    tb_ = threading.Thread(target=go, args=("beta.py",), name="beta")
    tb_.start()
    ta.join(10)
    tb_.join(10)
    assert not ta.is_alive() and not tb_.is_alive(), "PFR1-F3-INSTALL-WEDGED"
    assert all(r.get("installed") for r in results.values()), results
    rows = sorted(orig()["plugins"])
    assert rows == ["alpha.py", "beta.py"], f"PFR1-F3-REGISTRY-ROW-LOST rows={rows}"


def test_f3_uninstall_does_not_drop_a_concurrent_install(pdir, tmp_path, monkeypatch):
    assert P.install_plugin(
        str(_candidate(tmp_path, "alpha.py", _man("alpha"))), ack=True
    )["installed"]
    orig = P._read_registry
    calls = []
    second_read = threading.Event()

    def read_then_pause():
        reg = orig()
        calls.append(1)
        if len(calls) == 1:
            second_read.wait(1.0)
        else:
            second_read.set()
        return reg

    monkeypatch.setattr(P, "_read_registry", read_then_pause)
    beta = _candidate(tmp_path, "beta.py", _man("beta"))
    tu = threading.Thread(
        target=P.uninstall_plugin, args=("alpha.py",), kwargs={"ack": True}
    )
    tu.start()
    deadline = time.monotonic() + 5
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    ti = threading.Thread(
        target=P.install_plugin, args=(str(beta),), kwargs={"ack": True}
    )
    ti.start()
    tu.join(10)
    ti.join(10)
    assert not tu.is_alive() and not ti.is_alive(), "PFR1-F3-UNINSTALL-WEDGED"
    rows = sorted(orig()["plugins"])
    assert rows == ["beta.py"], f"PFR1-F3-UNINSTALL-DROPPED-ROW rows={rows}"


def test_f3_install_waits_for_another_holder_of_the_registry_lock(pdir, tmp_path):
    """flock is per open file description: this second description stands in
    for another process (a CLI install) holding the registry lock."""
    src = _candidate(tmp_path, "gamma.py", _man("gamma"))
    done = threading.Event()
    res: dict = {}

    def go():
        res.update(P.install_plugin(str(src), ack=True))
        done.set()

    with open(pdir / "plugins.registry.json.lock", "a+b") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        t = threading.Thread(target=go)
        t.start()
        finished_while_held = done.wait(0.5)
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
    t.join(10)
    assert not finished_while_held, "PFR1-F3-NO-CROSS-PROCESS-LOCK"
    assert res.get("installed") is True, res


# ── F4 ────────────────────────────────────────────────────────────────
_F4_PLUGIN = """
from bulk_downloader import plugins as _P
PLUGIN = {"name": "%(name)s", "api_version": %(api)d, "capabilities": %(caps)s%(extra)s}

@_P.hook("download.done")
def _h(payload):
    payload.setdefault("ran", []).append("hook")

@_P.processor(name="%(name)s-proc")
def _p(payload):
    payload.setdefault("ran", []).append("processor")
    return 1

_P.register_lifecycle("before_launch", lambda *a, **k: None, name="%(name)s-lc")
_P.register_extractor("%(name)s-site", lambda *a, **k: None)
_P.register_config_provider(lambda *a, **k: None, name="%(name)s-cfg")
_P.register_recognizer(lambda *a, **k: None, name="%(name)s-rec")
_P.register_prefilter(lambda *a, **k: None, name="%(name)s-pre")
_P.register_namer(lambda *a, **k: None, name="%(name)s-namer")
_P.register_sink(lambda *a, **k: None, name="%(name)s-sink")
_P.register_source(lambda *a, **k: None, name="%(name)s-src")
_P.register_enricher(lambda *a, **k: None, name="%(name)s-enr")
_P.register_healthcheck(lambda *a, **k: None, name="%(name)s-hc")
"""

_F4_REGISTRIES = (
    "_extractors",
    "_hooks",
    "_lifecycle",
    "_processors",
    "_config_providers",
    "_recognizers",
    "_prefilters",
    "_namers",
    "_sinks",
    "_sources",
    "_enrichers",
    "_healthchecks",
)


def _registry_sizes() -> dict:
    out = {}
    for name in _F4_REGISTRIES:
        reg = getattr(P, name)
        out[name] = (
            sum(len(v) if isinstance(v, list) else 1 for v in reg.values())
            if isinstance(reg, dict)
            else len(reg)
        )
    return out


def _f4_write(pdir: Path, name: str, caps: list, extra: str = "") -> None:
    (pdir / ("%s.py" % name)).write_text(
        _F4_PLUGIN
        % {
            "name": name,
            "api": P.PLUGIN_API_VERSION,
            "caps": json.dumps(caps),
            "extra": extra,
        },
        "utf-8",
    )


def test_f4_gate_skipped_plugin_registers_nothing(pdir):
    _f4_write(pdir, "pfr1_gated", ["lifecycle"])
    summary = P.load_all()
    assert summary["skipped"] == 1 and summary["loaded"] == 0, summary
    payload: dict = {}
    P.fire_hook("download.done", payload)
    ran = P.run_processors(payload)
    assert payload.get("ran") is None, (
        f"PFR1-F4-SKIPPED-PLUGIN-RAN {payload.get('ran')}"
    )
    assert ran == [], f"PFR1-F4-SKIPPED-PROCESSOR-LISTED {ran}"
    assert P._lifecycle.get("before_launch", []) == [], "PFR1-F4-SKIPPED-LIFECYCLE-LIVE"
    left = {k: v for k, v in _registry_sizes().items() if v}
    assert left == {}, f"PFR1-F4-SKIPPED-REGISTRATIONS-LEFT {left}"


def test_f4_enable_hook_skip_registers_nothing(pdir):
    """The second skip branch: a manifest naming on_enable the module lacks."""
    _f4_write(pdir, "pfr1_nohook", [], extra=', "on_enable": True')
    summary = P.load_all()
    assert summary["skipped"] == 1 and summary["loaded"] == 0, summary
    left = {k: v for k, v in _registry_sizes().items() if v}
    assert left == {}, f"PFR1-F4-HOOKERR-REGISTRATIONS-LEFT {left}"


def test_f4_skip_keeps_an_earlier_plugins_registrations(pdir):
    _f4_write(pdir, "pfr1_a_open", [])
    _f4_write(pdir, "pfr1_b_gated", ["lifecycle"])
    summary = P.load_all()
    assert (summary["loaded"], summary["skipped"]) == (1, 1), summary
    payload: dict = {}
    P.fire_hook("download.done", payload)
    assert payload.get("ran") == ["hook"], (
        f"PFR1-F4-ROLLBACK-TOO-WIDE {payload.get('ran')}"
    )
    assert [r["name"] for r in P.run_processors({})] == ["pfr1_a_open-proc"]


def test_f4_control_ungated_plugin_runs(pdir):
    _f4_write(pdir, "pfr1_open", [])
    summary = P.load_all()
    assert summary["loaded"] == 1, summary
    payload: dict = {}
    P.fire_hook("download.done", payload)
    assert P.run_processors(payload)[0]["ok"] is True
    assert payload["ran"] == ["hook", "processor"]
    empty = [k for k, v in _registry_sizes().items() if not v]
    assert empty == [], f"the F4 plugin must register into every registry: {empty}"


# ── F5 (AMEND 0755Z, PM ANSWER-pm-B-F5-0756Z) ─────────────────────────
@pytest.fixture
def app_client(pdir):
    from bulk_downloader import app as A

    return A.app.test_client()


def _marker_plugin(tmp_path: Path, stem: str) -> tuple:
    marker = tmp_path / ("%s.ran" % stem)
    return _man(stem) + "open(%r, 'w').write('x')\n" % str(marker), marker


def _upload(client, name: str, text: str):
    data = {"file": (io.BytesIO(text.encode("utf-8")), name), "ack": "1"}
    return client.post(
        "/api/plugins/install", data=data, content_type="multipart/form-data"
    )


def test_f5_install_then_reload_does_not_run_it(app_client, pdir, tmp_path):
    text, marker = _marker_plugin(tmp_path, "pfr1_f5a")
    r = _upload(app_client, "pfr1_f5a.py", text)
    assert r.status_code == 200, r.get_json()
    app_client.post("/api/plugins/reload")
    assert not marker.exists(), "PFR1-F5-INSTALLED-PLUGIN-RAN"
    body = r.get_json()
    assert body.get("enabled") is False and '"disabled"' in body.get("enable", ""), body
    assert "pfr1_f5a.py" in P.read_config()["disabled"]


def test_f5_enable_via_config_route_then_it_loads(app_client, pdir, tmp_path):
    text, marker = _marker_plugin(tmp_path, "pfr1_f5b")
    assert _upload(app_client, "pfr1_f5b.py", text).status_code == 200
    r = app_client.post("/api/plugins/config", json={"disabled": []})
    assert r.status_code == 200, r.get_json()
    assert marker.exists(), "enabling via the config route + reload must load it"


def test_f5_reinstall_of_an_enabled_plugin_disables_it_again(
    app_client, pdir, tmp_path
):
    text, marker = _marker_plugin(tmp_path, "pfr1_f5c")
    assert _upload(app_client, "pfr1_f5c.py", text).status_code == 200
    assert (
        app_client.post("/api/plugins/config", json={"disabled": []}).status_code == 200
    )
    assert marker.exists()
    marker.unlink()
    assert _upload(app_client, "pfr1_f5c.py", text).status_code == 200
    app_client.post("/api/plugins/reload")
    assert not marker.exists(), "PFR1-F5-REINSTALL-STAYED-ENABLED"
    assert P.read_config()["disabled"] == ["pfr1_f5c.py"]


def test_f5_preexisting_plugin_still_loads(app_client, pdir, tmp_path):
    pre_text, pre_marker = _marker_plugin(tmp_path, "pfr1_f5pre")
    (pdir / "pfr1_f5pre.py").write_text(pre_text, "utf-8")
    text, marker = _marker_plugin(tmp_path, "pfr1_f5d")
    assert _upload(app_client, "pfr1_f5d.py", text).status_code == 200
    app_client.post("/api/plugins/reload")
    assert pre_marker.exists(), "a plugin not installed by this call must keep loading"
    assert not marker.exists(), "PFR1-F5-INSTALLED-PLUGIN-RAN"


def test_f5_allowlist_mode_leaves_config_alone(app_client, pdir, tmp_path):
    P.write_config({"enabled": ["other.py"]})
    before = (pdir / "plugins.json").read_bytes()
    text, marker = _marker_plugin(tmp_path, "pfr1_f5e")
    r = _upload(app_client, "pfr1_f5e.py", text)
    assert r.status_code == 200, r.get_json()
    assert (pdir / "plugins.json").read_bytes() == before, (
        "PFR1-F5-ALLOWLIST-CONFIG-CHANGED"
    )
    app_client.post("/api/plugins/reload")
    assert not marker.exists()
    assert '"enabled"' in r.get_json().get("enable", ""), (
        f"PFR1-F5-NO-ENABLE-STEP {r.get_json()!r}"
    )


@pytest.mark.parametrize("prior", ["fresh", "reinstall"])
def test_f5_allowlist_naming_the_file_still_disables_it(
    app_client, pdir, tmp_path, prior
):
    """r2 (R183-A): an allowlist that already names the file is no shield."""
    name = "pfr2_f5h.py"
    text, marker = _marker_plugin(tmp_path, "pfr2_f5h")
    if prior == "fresh":
        P.write_config({"enabled": [name]})
    else:
        assert _upload(app_client, name, text).status_code == 200
        r = app_client.post(
            "/api/plugins/config", json={"enabled": [name], "disabled": []}
        )
        assert r.status_code == 200, r.get_json()
        assert marker.exists(), "control: the allowlist loads the enabled plugin"
        marker.unlink()
    r = _upload(app_client, name, text)
    assert r.status_code == 200, r.get_json()
    app_client.post("/api/plugins/reload")
    body = r.get_json()
    assert not marker.exists(), f"PFR2-F5-ALLOWLISTED-INSTALL-RAN {body!r}"
    assert body.get("enabled") is False and '"disabled"' in body.get("enable", ""), (
        f"PFR2-F5-WRONG-ENABLE-STEP {body!r}"
    )
    cfg = P.read_config()
    assert cfg["enabled"] == [name] and cfg["disabled"] == [name], cfg


def test_f5_unreadable_config_refuses_the_install(pdir, tmp_path):
    """If "disabled" cannot be written the file must not land enabled."""
    (pdir / "plugins.json").write_text("[1]", "utf-8")
    text, _marker = _marker_plugin(tmp_path, "pfr1_f5f")
    res = P.install_plugin(str(_candidate(tmp_path, "pfr1_f5f.py", text)), ack=True)
    assert res.get("installed") is False, f"PFR1-F5-UNREADABLE-CONFIG-INSTALLED {res!r}"
    assert not (pdir / "pfr1_f5f.py").exists()
    assert (pdir / "plugins.json").read_text("utf-8") == "[1]"


def test_f5_full_disabled_list_refuses_the_install(pdir, tmp_path):
    """One more "disabled" entry past the F10 bound would lock the config route."""
    P.write_config({"disabled": ["x%d.py" % i for i in range(P._CONFIG_MAX_LIST)]})
    before = (pdir / "plugins.json").read_bytes()
    text, _marker = _marker_plugin(tmp_path, "pfr1_f5g")
    res = P.install_plugin(str(_candidate(tmp_path, "pfr1_f5g.py", text)), ack=True)
    assert res.get("installed") is False, f"PFR1-F5-DISABLED-OVER-BOUND {res!r}"
    assert not (pdir / "pfr1_f5g.py").exists()
    assert (pdir / "plugins.json").read_bytes() == before


# ── F10 ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("body", [5, "xactionsx", [1], True])
def test_f10_validate_non_object_body_is_400(macros_client, body):
    r = macros_client.post(
        "/api/macros/validate", data=json.dumps(body), content_type="application/json"
    )
    assert r.status_code == 400, f"PFR1-F10-VALIDATE-{r.status_code} {r.get_json()!r}"
    assert r.get_json().get("ok") is False


def test_f10_validate_control_object_body(macros_client):
    r = macros_client.post(
        "/api/macros/validate", json={"actions": [{"kind": "wait", "for": "#ready"}]}
    )
    assert r.status_code == 200 and r.get_json()["ok"] is True, r.get_json()


@pytest.mark.parametrize(
    "patch",
    [
        {"disabled": ["x.py"] * 100_000},
        {"enabled": ["e" * 100_000]},
        {"node_bin": "n" * 1_000_000},
    ],
    ids=["list-100k", "entry-100k", "node_bin-1M"],
)
def test_f10_config_patch_is_bounded(pdir, patch):
    with pytest.raises(P.PluginConfigError):
        P.write_config(patch)
    assert not (pdir / "plugins.json").exists(), "PFR1-F10-CONFIG-UNBOUNDED-WRITTEN"


def test_f10_config_control_normal_patch(pdir):
    cfg = P.write_config({"disabled": ["a.py", "b.py"], "node_bin": "/usr/bin/node"})
    assert cfg["disabled"] == ["a.py", "b.py"] and cfg["node_bin"] == "/usr/bin/node"


def _sh() -> str:
    path = shutil.which("sh")
    if not path:
        pytest.skip("sh not on PATH -- bridge child NOT measured")
    return path


def _proc_alive(pid: int) -> bool:
    try:
        with open("/proc/%d/stat" % pid, encoding="ascii") as fh:
            state = fh.read().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state != "Z"


def test_f10_bridge_timeout_kills_the_childs_children(tmp_path, monkeypatch):
    monkeypatch.setattr(tb, "TIMEOUT_S", 2)
    pidfile = tmp_path / "grandchild.pid"
    argv = [_sh(), "-c", 'sleep 300 & echo $! > "$1"; wait', "sh", str(pidfile)]
    gc_pid = None
    try:
        r = tb._run_in(argv, str(tmp_path))
        assert r["timed_out"] is True, r
        gc_pid = int(pidfile.read_text().strip())
        deadline = time.monotonic() + 3
        while _proc_alive(gc_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _proc_alive(gc_pid), (
            f"PFR1-F10-BRIDGE-GRANDCHILD-SURVIVED pid={gc_pid}"
        )
    finally:
        if gc_pid is None and pidfile.exists():
            gc_pid = int(pidfile.read_text().strip() or 0) or None
        if gc_pid and _proc_alive(gc_pid):
            os.kill(gc_pid, signal.SIGKILL)


def test_f10_bridge_does_not_buffer_past_the_cap(tmp_path):
    size = 64 * 1024 * 1024
    argv = [_sh(), "-c", 'head -c "$1" /dev/zero', "sh", str(size)]
    tracemalloc.start()
    try:
        r = tb._run_in(argv, str(tmp_path))
        _cur, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert r["returncode"] == 0 and r["timed_out"] is False, {
        k: r[k] for k in ("returncode", "timed_out")
    }
    assert len(r["stdout"]) == tb.MAX_OUTPUT_BYTES
    assert peak < 16 * 1024 * 1024, f"PFR1-F10-BRIDGE-BUFFERED peak={peak}"


def test_f10_bridge_control_small_output(tmp_path):
    r = tb._run_in([_sh(), "-c", "echo out; echo err >&2; exit 3"], str(tmp_path))
    assert (r["returncode"], r["stdout"], r["stderr"], r["timed_out"]) == (
        3,
        "out\n",
        "err\n",
        False,
    )
