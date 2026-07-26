"""Pytest fixtures shared across the test suite.

Goals:
  - Run tests from a clean working directory so they don't poison the
    real install or each other
  - Keep them fast: no Playwright launches, no real HTTP, no real DB beyond
    SQLite (which is built-in)
  - Mock or skip anything that talks to the outside world

Run with:
    cd /path/to/BulkDownloader && pytest tests/

Or:
    pytest tests/ -v -x          # verbose, stop on first failure
    pytest tests/test_validators.py::test_path_traversal_blocked
"""
import os
import shutil
import sys
from types import ModuleType
from pathlib import Path

import pytest
from capture_lanes import classify_capture_path

# Make sure the package is importable regardless of where pytest is invoked
PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))


def _canonicalize_package_children(package_name, modules=None):
    """Make direct package-child attributes agree with the module table.

    Python publishes ``package.child`` both in ``sys.modules`` and as a
    ``child`` attribute on the parent package. Removing only the table entry
    leaves a stale attribute that ``from package import child`` can return
    without importing or registering a replacement.

    Only attributes whose module ``__name__`` exactly matches the expected
    direct child are touched. Other module-valued package attributes are
    imports or aliases and remain unchanged.
    """
    module_table = sys.modules if modules is None else modules
    prefix = package_name + "."
    package_modules = {
        name: module
        for name, module in tuple(module_table.items())
        if (
            (name == package_name or name.startswith(prefix))
            and isinstance(module, ModuleType)
        )
    }

    for parent_name, parent in package_modules.items():
        for child_attr, current in tuple(vars(parent).items()):
            if not isinstance(current, ModuleType):
                continue
            child_name = f"{parent_name}.{child_attr}"
            if current.__name__ != child_name:
                continue
            canonical = package_modules.get(child_name)
            if canonical is None:
                delattr(parent, child_attr)
            elif canonical is not current:
                setattr(parent, child_attr, canonical)

    # Import machinery normally creates these bindings. Rebuild any missing
    # ones as well so a restored nested package graph is fully canonical.
    for child_name, child in sorted(
        package_modules.items(), key=lambda item: item[0].count(".")
    ):
        if child_name == package_name:
            continue
        parent_name, _, child_attr = child_name.rpartition(".")
        parent = package_modules.get(parent_name)
        if parent is not None and child_attr not in vars(parent):
            setattr(parent, child_attr, child)


# v3.66.13: canonical `isolated_bd_home` fixture. Replaces 45 copy-pasted
# variants that had drifted by env-var set saved/restored.
#
# Two behaviours, gated separately:
#   1. ALWAYS: env-var + cwd isolation, plus the tmp_path-leftover wipe.
#      Cheap and harmless for any test.
#   2. OPT-IN VIA MARKER: also drop every `bulk_downloader.*` from
#      sys.modules on enter and exit so the next import re-reads env
#      vars at module load time. Tests opt in by adding a top-of-file
#      `pytestmark = pytest.mark.bd_module_wipe`.
#
# Why split: the module-wipe is essential for any test that needs
# bulk_downloader to re-read BD_HOME / BD_DISABLE_KEEPALIVE at import,
# which is most tests that exercise deep_detect, the runner, or the
# Flask app. But for tests that already hold module references and
# rely on mutating module state across calls (e.g. `dev_metrics.record(...)`
# then `dev_metrics.snapshot()`), the wipe rebinds the package's
# `dev_metrics` attribute to a *new* module while the test still
# holds the old one — calls go to two different module instances and
# data is silently lost.
#
# Pre-dedup, only the 46 files that defined their own autouse fixture
# got the wipe. Post-dedup, the marker preserves that opt-in.
#
# Design notes carried over from the prior most-defensive variants:
#   * Saves the UNION of env vars any variant touched
#     (BD_HOME, BD_DISABLE_KEEPALIVE, BD_DEV_MODE, BD_DEV_MODE_DISABLE,
#     BD_AUTH_TOKEN).
#   * Wipes tmp_path children at fixture start (from
#     test_v3_50_phase3.py). The Anthropic-sandbox custom test runner
#     reuses the same tmp_path across tests in one file; SQLite DBs
#     from a prior test leak state otherwise. No-op on real pytest
#     where tmp_path is per-test.
#   * Sets BD_HOME=tmp_path, chdir into it. The app resolves several
#     paths relative to cwd.
#   * No monkeypatch dependency — the sandbox stub-runner only defines
#     `_MonkeyPatch` if the *test function* takes monkeypatch as a
#     parameter; autouse fixtures using it raise UnboundLocalError.


def pytest_configure(config):
    """Register the bd_module_wipe marker so pytest doesn't warn."""
    config.addinivalue_line(
        "markers",
        "bd_module_wipe: also drop all bulk_downloader.* modules from "
        "sys.modules around the test (used by tests that need the "
        "package to re-read env vars on import).",
    )
    config.addinivalue_line(
        "markers",
        "capture_serial: run this file in capture.sh's isolated serial lane.",
    )
    config.addinivalue_line(
        "markers",
        "capture_parallel: run this reviewed-safe file in capture.sh's xdist lane.",
    )


def pytest_collection_modifyitems(items):
    """Apply exactly one capture lane marker to every collected test item.

    Classification is file-level so xdist ``--dist loadfile`` cannot split a
    module across execution lanes. An explicit serial marker is always kept;
    otherwise the repository classifier decides conservatively from the path
    and source.
    """
    for item in items:
        if item.get_closest_marker("capture_serial") is not None:
            item.add_marker(pytest.mark.capture_serial)
            continue
        item_path = getattr(item, "path", None) or item.fspath
        lane = classify_capture_path(str(item_path))
        item.add_marker(getattr(pytest.mark, f"capture_{lane}"))


@pytest.fixture(autouse=True)
def isolated_bd_home(request, tmp_path):
    _ENV_KEYS = (
        "BD_HOME",
        "BD_DISABLE_KEEPALIVE",
        "BD_DEV_MODE",
        "BD_DEV_MODE_DISABLE",
        "BD_AUTH_TOKEN",
    )
    saved_env = {k: os.environ.get(k) for k in _ENV_KEYS}
    saved_cwd = os.getcwd()

    # Repair attr/table splits left by a previous test before this test can
    # resolve a stale child via ``from bulk_downloader import child``.
    _canonicalize_package_children("bulk_downloader")

    # Wipe any leftover state from a prior test sharing this tmp_path
    # (Anthropic-sandbox custom-runner quirk; harmless elsewhere).
    for child in Path(tmp_path).iterdir():
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink()
        except OSError:
            pass

    os.environ["BD_HOME"] = str(tmp_path)
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    os.environ.pop("BD_DEV_MODE_DISABLE", None)
    os.chdir(str(tmp_path))

    # Opt-in: drop bulk_downloader.* from sys.modules so a fresh import
    # in the test re-reads env vars at module load time. Selected via
    # `pytestmark = pytest.mark.bd_module_wipe` at the top of any test
    # file that needs it.
    do_wipe = request.node.get_closest_marker("bd_module_wipe") is not None
    saved_modules: dict = {}
    if do_wipe:
        saved_modules = {k: v for k, v in sys.modules.items()
                         if k.startswith("bulk_downloader")}
        for mod in list(sys.modules):
            if mod.startswith("bulk_downloader"):
                del sys.modules[mod]

    try:
        yield tmp_path
    finally:
        os.chdir(saved_cwd)
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if do_wipe:
            for _bd_mod in [_m for _m in list(sys.modules)
                            if _m.startswith("bulk_downloader")]:
                del sys.modules[_bd_mod]
            sys.modules.update(saved_modules)
        # Keep the two representations of imported children in lockstep.
        # This is required even for unmarked tests: a test can directly pop a
        # child from sys.modules and otherwise poison the next test.
        _canonicalize_package_children("bulk_downloader")


@pytest.fixture
def clean_workdir(tmp_path, monkeypatch):
    """Run a test in a clean temp directory. The runner's various state
    files (sites_config.json, downloader_history.db, profiles/) get
    created here and discarded at test end.

    v3.66.9: also set BD_INSTALL_DIR=tmp_path so db._resolve_db_path()
    sends writes into the tmpdir even if subsequent code chdirs away.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BD_INSTALL_DIR", str(tmp_path))
    monkeypatch.setenv("BD_TEST_MODE", "1")
    yield tmp_path


@pytest.fixture
def fresh_app(clean_workdir, monkeypatch):
    """Yield a fresh Flask test client. The app module is heavyweight
    (imports playwright transitively); we import lazily so non-app tests
    don't pay the cost."""
    # Reset module-level state. The app uses module-level dicts for
    # runners/s_cfg/s_meta which would otherwise leak between tests.
    if "bulk_downloader.app" in sys.modules:
        app_mod = sys.modules["bulk_downloader.app"]
        app_mod.runners.clear()
        app_mod.s_cfg.clear()
        app_mod.s_meta.clear()
        app_mod._app_cfg.clear()
        app_mod._app_cfg["global_max_concurrent"] = 0
        # Phase 26.7: rate-limit buckets are module-level — without
        # this, the second TestRateLimit case sees a still-full bucket
        # from the first and a third of the burst gets 429'd immediately.
        if hasattr(app_mod, "_rate_buckets"):
            app_mod._rate_buckets.clear()
    from bulk_downloader.app import app, _load_app_config
    from bulk_downloader.db import db_init
    # Create the sqlite tables. Runner code unconditionally hits the
    # queue table on construct (via _restore_queue), so without this
    # every site-creation in the test raises OperationalError.
    db_init()
    # Pre-create the screenshots dir; runner does mkdir() not mkdir(parents=True)
    (clean_workdir / "screenshots").mkdir(exist_ok=True)
    app.config["TESTING"] = True
    _load_app_config()
    client = app.test_client()
    yield client
    # Cleanup any threads the app spun up
    if "bulk_downloader.app" in sys.modules:
        app_mod = sys.modules["bulk_downloader.app"]
        for sid in list(app_mod.runners.keys()):
            try:
                app_mod.runners[sid].stop()
                app_mod.runners[sid]._stop_auto_retry()
            except Exception: pass
        app_mod.runners.clear()


@pytest.fixture
def aiassist_module():
    """Import the aiassist module fresh + reset its config state."""
    from bulk_downloader import aiassist
    aiassist.configure(endpoint="http://localhost:11434",
                       model_vision="qwen2.5vl:7b",
                       model_text="qwen2.5:7b",
                       enabled=False)
    # Clear health stats
    aiassist._health["call_count"] = 0
    aiassist._health["fail_count"] = 0
    aiassist._health["recent_latencies"] = []
    yield aiassist
