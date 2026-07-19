"""v3.66.736 — build_release._load_exclusions must not leak sys.path / sys.modules.

THE BUG (found @730 while banding, and left open because build_release.py is a
SHA-pinned release guard -- fixing it needs an operator-declared new SHA):

    def _load_exclusions(root: Path):
        sys.path.insert(0, str(root))          # <-- never removed
        from bulk_downloader.dev_suite import (...)   # <-- never evicted
        return _manifest_excluded, zip_manifest_check

`root` is whatever tree is being built. Run against a TEMP tree -- exactly what
`tools/precut_check.py::_tree_files` does -- and this permanently prepends the
temp tree to sys.path AND caches `bulk_downloader` -> the temp tree's stub
`__init__.py` in sys.modules FOR THE REST OF THE PROCESS. Every later import of
`bulk_downloader` in that interpreter then resolves to the stub.

That is why version-pin / settings suites FAILED IN THE BAND but PASSED
STANDALONE -- the band shares one interpreter, and whoever ran precut_check
first poisoned it. A test that passes alone and fails in company is the tell.

`test_precut_check.py` currently works around this by snapshotting and restoring
sys.path + sys.modules['bulk_downloader'] around every predict() call. That is a
CALLER defending itself against a CALLEE that corrupts global state -- every
future caller has to remember. Fix the leak at the source; the workaround can
stay as a belt-and-braces.

WHY THE EVICTION IS SAFE: the two returned callables survive it.
`_manifest_excluded` is pure (module-level constants); `zip_manifest_check` only
imports `zipfile` (stdlib) at call time. Removing a module from sys.modules does
not destroy the module object while a function's __globals__ still references it.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load_build_release():
    """Import tools/build_release.py by path (tools/ is not a package)."""
    spec = importlib.util.spec_from_file_location(
        "_br_under_test", REPO / "tools" / "build_release.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_tree(tmp_path: Path) -> Path:
    """A tree that LOOKS like the repo to an importer but is a decoy: its
    bulk_downloader is a stub. If _load_exclusions leaks, the stub wins."""
    root = tmp_path / "decoy"
    (root / "bulk_downloader" / "dev_suite").mkdir(parents=True)
    (root / "bulk_downloader" / "__init__.py").write_text(
        '__version__ = "0.0.0-DECOY"\n')
    (root / "bulk_downloader" / "dev_suite" / "__init__.py").write_text(
        "def _manifest_excluded(relpath):\n"
        "    return False\n"
        "def zip_manifest_check(zip_path):\n"
        "    return {'decoy': True}\n"
    )
    return root


def _bd_modules():
    return {k: v for k, v in sys.modules.items()
            if k == "bulk_downloader" or k.startswith("bulk_downloader.")}


def test_load_exclusions_does_not_leak_sys_path(tmp_path):
    br = _load_build_release()
    root = _fake_tree(tmp_path)

    before = list(sys.path)
    saved_mods = _bd_modules()
    try:
        br._load_exclusions(root)
    finally:
        after = list(sys.path)
        # Restore no matter what, so a failure here does not poison the rest of
        # the session. (Leaving this out the first time made the LAST test in
        # this file fail too, on the decoy's stub filter -- the bug reproducing
        # itself across tests in one interpreter, which is exactly the shape.)
        sys.path[:] = before
        for k in list(_bd_modules()):
            if k not in saved_mods:
                del sys.modules[k]
        sys.modules.update(saved_mods)

    assert after == before, (
        f"_load_exclusions left {set(after) - set(before)} on sys.path — "
        "every later import in this interpreter now searches the built tree "
        "first. This is the @730 band-vs-standalone divergence."
    )


def test_load_exclusions_does_not_leak_sys_modules(tmp_path):
    br = _load_build_release()
    root = _fake_tree(tmp_path)

    saved_path = list(sys.path)
    saved_mods = _bd_modules()
    try:
        br._load_exclusions(root)
        leaked = _bd_modules()
        # Any bulk_downloader module that was NOT cached before must not be
        # cached now — and one that WAS cached must be the SAME object.
        new_keys = set(leaked) - set(saved_mods)
        rebound = [k for k in saved_mods if leaked.get(k) is not saved_mods[k]]
    finally:
        sys.path[:] = saved_path
        for k in list(_bd_modules()):
            if k not in saved_mods:
                del sys.modules[k]
        sys.modules.update(saved_mods)

    assert not new_keys, (
        f"_load_exclusions cached {sorted(new_keys)} in sys.modules from the "
        "built tree. Against a temp tree that binds `bulk_downloader` to a "
        "STUB for the whole interpreter."
    )
    assert not rebound, (
        f"_load_exclusions REBOUND already-imported modules {rebound} to the "
        "built tree — worse than caching a new one: it swaps the real module "
        "out from under code that already imported it."
    )


def test_load_exclusions_still_returns_working_callables(tmp_path):
    """The fix must not break what it returns. Evicting the module from
    sys.modules does not invalidate the function objects."""
    br = _load_build_release()
    saved_path = list(sys.path)
    saved_mods = _bd_modules()
    try:
        excluded, zipcheck = br._load_exclusions(REPO)
        assert callable(excluded) and callable(zipcheck)
        # the real filter: caches are excluded, source is not
        assert excluded("bulk_downloader/__pycache__/app.cpython-312.pyc") is True
        assert excluded("bulk_downloader/app.py") is False
    finally:
        sys.path[:] = saved_path
        for k in list(_bd_modules()):
            if k not in saved_mods:
                del sys.modules[k]
        sys.modules.update(saved_mods)
