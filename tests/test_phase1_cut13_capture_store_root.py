"""Phase 1 Cut 1.3 (v3.66.616): configurable capture store root + migrator.

Today the capture roots are hard-bound to template_registry.PROJECT_ROOT (in-repo).
This cut adds an optional `capture_store_root` config key so the raw capture
artifacts can live off the repo/root, plus a migrator.

TWO-BASE model (required, not single-base):
  * capture-OUTPUT dirs (captures, offline_out, offline_captures) resolve under
    the configured store root (default = PROJECT_ROOT).
  * template dirs (templates/drafts, templates/review_candidates) ALWAYS resolve
    under PROJECT_ROOT -- they are managed by template_manager.DRAFTS_DIR
    (build_draft / drift_repair / app_template_manager write there), independent
    of the capture store. Relocating them would desync the picker from the writers.

Default (unset) is byte-identical: store root == PROJECT_ROOT.

SAFETY: the resolve gate stays FS-authoritative under BOTH bases (symlink/is_file/
is_under) -- store_root just changes WHICH base a capture-output token resolves
against, never the validation.

RED on 615: no capture_store_root config resolution; _project_root is the only
base; the migrator tool does not exist.
"""
import os
import tempfile
import zipfile
from pathlib import Path

import bulk_downloader.dom_analyzer as da


def _wacz(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("archive/capture.json", "{}")


def test_default_store_root_is_project_root_byte_identical(monkeypatch):
    """With no capture_store_root configured, capture-output AND template captures
    both enumerate under PROJECT_ROOT exactly as before."""
    root = Path(tempfile.mkdtemp())
    _wacz(root / "captures" / "app.example.com_x_20250101_aa.wacz")
    _wacz(root / "templates" / "drafts" / "draft.host_y_20250101_bb.wacz")
    monkeypatch.setattr(da, "_project_root", lambda: root)
    # no config -> _capture_store_root() must fall back to PROJECT_ROOT
    monkeypatch.setattr(da, "_capture_store_root", lambda: root)
    rows = da.scan_captures(root=None)
    rels = {r["rel_path"] for r in rows}
    assert any(x.startswith("captures/") for x in rels), rels
    assert any(x.startswith("templates/drafts/") for x in rels), rels


def test_capture_store_root_relocates_output_dirs_only(monkeypatch):
    """When a store root is set, capture-OUTPUT captures live under it, while
    template captures stay under PROJECT_ROOT."""
    project = Path(tempfile.mkdtemp())
    store = Path(tempfile.mkdtemp())
    # a capture-output capture under the STORE root
    _wacz(store / "captures" / "app.example.com_x_20250101_aa.wacz")
    # a template-review capture under the PROJECT root
    _wacz(project / "templates" / "review_candidates" / "cand.host_z_20250101_cc.wacz")
    monkeypatch.setattr(da, "_project_root", lambda: project)
    monkeypatch.setattr(da, "_capture_store_root", lambda: store)
    rows = da.scan_captures(root=None)
    rels = {r["rel_path"] for r in rows}
    # capture-output capture found under the store root
    assert "captures/app.example.com_x_20250101_aa.wacz" in rels, rels
    # template capture still found under PROJECT_ROOT
    assert "templates/review_candidates/cand.host_z_20250101_cc.wacz" in rels, rels


def test_resolve_token_uses_store_root_for_output_dirs(monkeypatch):
    """A capture-output token resolves against the store root; a template token
    resolves against PROJECT_ROOT."""
    project = Path(tempfile.mkdtemp())
    store = Path(tempfile.mkdtemp())
    _wacz(store / "captures" / "c.wacz")
    _wacz(project / "templates" / "drafts" / "d.wacz")
    monkeypatch.setattr(da, "_project_root", lambda: project)
    monkeypatch.setattr(da, "_capture_store_root", lambda: store)
    r1 = da.resolve_capture_token("captures/c.wacz", root=None)
    assert r1 is not None and r1.exists(), "output token did not resolve under store root"
    r2 = da.resolve_capture_token("templates/drafts/d.wacz", root=None)
    assert r2 is not None and r2.exists(), "template token did not resolve under PROJECT_ROOT"


def test_resolve_gate_stays_fs_authoritative_under_store_root(monkeypatch):
    """SAFETY: a symlinked capture-output token is refused even under a store root."""
    project = Path(tempfile.mkdtemp())
    store = Path(tempfile.mkdtemp())
    capdir = store / "captures"
    capdir.mkdir(parents=True, exist_ok=True)
    _wacz(capdir / "real.wacz")
    secret = store / "secret_outside.txt"
    secret.write_text("secret")
    os.symlink(secret, capdir / "sneaky.wacz")
    monkeypatch.setattr(da, "_project_root", lambda: project)
    monkeypatch.setattr(da, "_capture_store_root", lambda: store)
    assert da.resolve_capture_token("captures/sneaky.wacz", root=None) is None, \
        "symlinked capture-output token resolved under store root (gate weakened)"
    assert da.resolve_capture_token("captures/real.wacz", root=None) is not None, \
        "legit capture-output token refused under store root"


def test_capture_store_root_reads_config_key(monkeypatch):
    """_capture_store_root() resolves the `capture_store_root` app-config key when
    it is a valid absolute existing dir, else falls back to PROJECT_ROOT."""
    project = Path(tempfile.mkdtemp())
    store = Path(tempfile.mkdtemp())
    monkeypatch.setattr(da, "_project_root", lambda: project)
    import bulk_downloader.global_config as gc
    # config points at a valid absolute dir -> used
    monkeypatch.setattr(gc, "get", lambda k, d=None: str(store) if k == "capture_store_root" else d)
    assert da._capture_store_root() == store
    # config unset -> PROJECT_ROOT fallback
    monkeypatch.setattr(gc, "get", lambda k, d=None: d)
    assert da._capture_store_root() == project
    # config set to a non-existent path -> PROJECT_ROOT fallback (never a bad base)
    monkeypatch.setattr(gc, "get", lambda k, d=None: "/no/such/store/dir" if k == "capture_store_root" else d)
    assert da._capture_store_root() == project


def test_migrator_relocates_and_reindexes(monkeypatch):
    """The migrator moves capture-output dirs from PROJECT_ROOT to the new store
    root and the post-move scan finds them there (relocate + rescan round-trip)."""
    import importlib.util
    project = Path(tempfile.mkdtemp())
    store = Path(tempfile.mkdtemp())
    _wacz(project / "captures" / "app.example.com_x_20250101_aa.wacz")
    # load the migrator tool
    tool = Path(__file__).resolve().parent.parent / "tools" / "relocate_capture_store.py"
    assert tool.exists(), "tools/relocate_capture_store.py missing"
    spec = importlib.util.spec_from_file_location("relocate_capture_store", tool)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "relocate"), "relocate_capture_store.relocate missing"
    res = mod.relocate(str(project), str(store), dry_run=False)
    # the capture file now lives under the store root
    assert (store / "captures" / "app.example.com_x_20250101_aa.wacz").exists(), \
        f"capture not moved to store root: {res}"
    # and NOT under the old project root
    assert not (project / "captures" / "app.example.com_x_20250101_aa.wacz").exists(), \
        "capture left behind under the old root"
    # rescan under the new store root enumerates it
    monkeypatch.setattr(da, "_project_root", lambda: project)
    monkeypatch.setattr(da, "_capture_store_root", lambda: store)
    rels = {r["rel_path"] for r in da.scan_captures(root=None)}
    assert "captures/app.example.com_x_20250101_aa.wacz" in rels, rels


def test_migrator_dry_run_moves_nothing(monkeypatch):
    """dry_run reports the plan but moves no files."""
    import importlib.util
    project = Path(tempfile.mkdtemp())
    store = Path(tempfile.mkdtemp())
    _wacz(project / "captures" / "c.wacz")
    tool = Path(__file__).resolve().parent.parent / "tools" / "relocate_capture_store.py"
    spec = importlib.util.spec_from_file_location("relocate_capture_store", tool)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    res = mod.relocate(str(project), str(store), dry_run=True)
    assert (project / "captures" / "c.wacz").exists(), "dry_run moved a file"
    assert not (store / "captures" / "c.wacz").exists(), "dry_run created files at target"
