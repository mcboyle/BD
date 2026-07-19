"""Pin test for tools/precut_check.py — the pre-cut gate predictor.

Zero-arg functions; repo root via __file__; stdlib only.

HERMETICITY (v3.66.730): precut's _tree_files() calls build_release._load_exclusions(),
which does `sys.path.insert(0, <root>); from bulk_downloader.dev_suite import ...` and
NEVER pops the path nor evicts the cached module (build_release.py is a SHA-pinned guard,
so that leak cannot be fixed there without an operator re-SHA). Run against a TEMP tree,
it caches `bulk_downloader` -> the temp tree's stub __init__.py for the rest of the process.
Under a single-boot band that poisons every later suite ("cannot import app_settings_center /
dev_suite"). These tests therefore snapshot and RESTORE sys.path + the bulk_downloader modules
around every predict() call, so no ordering can leak. A verdict that depends on what ran first
is not a verdict.
"""
import contextlib
import importlib.util
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


@contextlib.contextmanager
def _no_import_leak():
    """Restore sys.path and evict any bulk_downloader.* modules a callee cached."""
    path0 = list(sys.path)
    mods0 = {k: v for k, v in sys.modules.items() if k == "bulk_downloader"
             or k.startswith("bulk_downloader.")}
    try:
        yield
    finally:
        sys.path[:] = path0
        for k in [k for k in sys.modules
                  if k == "bulk_downloader" or k.startswith("bulk_downloader.")]:
            if k not in mods0:
                del sys.modules[k]
        for k, v in mods0.items():
            sys.modules[k] = v


def _load():
    spec = importlib.util.spec_from_file_location(
        "precut_check", REPO / "tools" / "precut_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _baseline_zip(root, members):
    zp = Path(tempfile.mkdtemp(prefix="bd_precut_b_")) / "base.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        for name, body in members.items():
            zf.writestr(name, body)
    return zp


def _tree(members):
    root = Path(tempfile.mkdtemp(prefix="bd_precut_t_"))
    for name, body in members.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return root


def test_detects_changed_guard_and_tool_regen():
    m = _load()
    base = {
        "bulk_downloader/__init__.py": '__version__ = "3.66.100"\n',
        "tools/build_release.py": "# guard v1\n",
        "tools/existing.py": "# x\n",
    }
    tree = {
        "bulk_downloader/__init__.py": '__version__ = "3.66.100"\n',
        "tools/build_release.py": "# guard v2 CHANGED\n",   # guard moved
        "tools/existing.py": "# x\n",
        "tools/brand_new.py": "# added tool\n",              # tools set changed
    }
    with _no_import_leak():
        r = m.predict(str(_tree(tree)), str(_baseline_zip(None, base)))
    assert "tools/build_release.py" in r["moved_guards"], r["moved_guards"]
    assert any("DEPENDENCY_GRAPH" in g for g in r["predicted_regens"]), r["predicted_regens"]


def test_version_inconsistency_flagged():
    m = _load()
    base = {"bulk_downloader/__init__.py": '__version__ = "3.66.100"\n'}
    # build the stale assertion-pin from parts so this source file does not itself
    # contain a verbatim `__version__ == "<literal>"` that the build's tests-only
    # version-pin scanner would read as a real (stale) pin and fail the build on.
    stale = "3.66.100"
    stale_pin = 'assert __version__ == "' + stale + '"\n'
    tree = {
        "bulk_downloader/__init__.py": '__version__ = "3.66.101"\n',
        "CHANGELOG.md": "# Changelog\n\n## v3.66.100 -- old\n",   # no 101 entry
        "tests/test_settings_center_slice4.py": stale_pin,         # stale pin
    }
    with _no_import_leak():
        r = m.predict(str(_tree(tree)), str(_baseline_zip(None, base)))
    assert r["version"]["consistent"] is False
    assert r["version"]["init"] == "3.66.101"
    # the temp tree's slice4 file carries a real stale assertion pin -> the
    # authoritative scanner (run inside precut) must surface it
    assert r["version"]["stale_test_pins"], r["version"]
