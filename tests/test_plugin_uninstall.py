"""Plugin uninstall (v3.66.513): managed-plugin removal — backend + route.

RED-first. The managed plugin lifecycle had install (509/510) but NO uninstall,
so the registry grew write-only. Adds:

  - `plugins.uninstall_plugin(file, *, ack=False)` — atomic remove of the staged
    file + its registry record. Refuses: anything outside the managed plugins
    dir (path-escape), an un-acked call (destructive), and a non-registry-managed
    (hand-dropped) file. Value-free result `{uninstalled: bool, file, reason?}`.
  - `POST /api/plugins/uninstall` on plugins_bp (CSRF-checked, ack-gated).
"""
import tempfile
from pathlib import Path

from bulk_downloader import plugins as pl


def _install_fake(name="myplugin.py"):
    src = Path(tempfile.mkdtemp()) / name
    src.write_text(
        'PLUGIN = {"name": "myplugin", "version": "1.0.0"}\n', "utf-8")
    ins = pl.install_plugin(str(src), ack=True)
    assert ins.get("installed") is True, ins
    return ins["file"]


def test_uninstall_removes_file_and_registry_record():
    fn = _install_fake()
    assert any(r["file"] == fn for r in pl.installed_registry())
    assert (pl._plugin_dir() / fn).exists()
    res = pl.uninstall_plugin(fn, ack=True)
    assert res.get("uninstalled") is True, res
    assert res.get("file") == fn
    assert not any(r["file"] == fn for r in pl.installed_registry())
    assert not (pl._plugin_dir() / fn).exists()


def test_uninstall_refuses_path_escape():
    for bad in ("../evil.py", "sub/evil.py", "/etc/passwd", "..\\evil.py", ""):
        res = pl.uninstall_plugin(bad, ack=True)
        assert res.get("uninstalled") is False, (bad, res)


def test_uninstall_is_ack_gated():
    fn = _install_fake("ackgate.py")
    res = pl.uninstall_plugin(fn, ack=False)
    assert res.get("uninstalled") is False, res
    assert (pl._plugin_dir() / fn).exists()  # destructive op must NOT proceed


def test_uninstall_refuses_unmanaged_handdropped_file():
    pdir = pl._plugin_dir()
    pdir.mkdir(parents=True, exist_ok=True)
    f = pdir / "handdropped.py"
    f.write_text("x = 1\n", "utf-8")
    res = pl.uninstall_plugin("handdropped.py", ack=True)
    assert res.get("uninstalled") is False, res
    assert f.exists()  # only registry-managed files are removable


def test_uninstall_route_destructive_ack():
    from bulk_downloader import app as bd_app
    app = bd_app.app
    fn = _install_fake("routed.py")
    c = app.test_client()
    # ack-gated at the route too
    r0 = c.post("/api/plugins/uninstall", json={"file": fn})
    assert (r0.get_json() or {}).get("uninstalled") is not True, r0.get_json()
    r = c.post("/api/plugins/uninstall", json={"file": fn, "ack": True})
    assert (r.get_json() or {}).get("uninstalled") is True, r.get_json()
    assert not (pl._plugin_dir() / fn).exists()
