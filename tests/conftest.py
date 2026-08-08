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
import builtins
import os
import pathlib
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
    # `slow` was applied in tests/test_desandbox_tool_verifiers.py without ever
    # being registered, so pytest emitted PytestUnknownMarkWarning and the mark
    # selected nothing. A marker that reads as a control and controls nothing is
    # the same defect class this suite exists to catch -- register it so
    # `-m "not slow"` is a real, usable deselection.
    config.addinivalue_line(
        "markers",
        "slow: shells out to a whole-tree tool (tens of seconds or more). "
        "Runs by default; deselect with -m 'not slow'.",
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


# ─── The operator's real AI settings are off limits to the whole suite ───────
#
# Caught 2026-08-07 by the box capture at 48707ad (v3.66.932), where
# test_t7_ai_inspection asserted `get_config()["enabled"] is False` and got
# True. The operator had turned AI on in the Global Config UI between two
# captures -- the earlier capture's ollama log carries zero /api/generate
# calls, the later one carries a real inference. Turning a feature ON broke
# the test suite, and the test that broke was not the one that changed.
#
# The mechanism is entirely outside any test's control:
#
#   bulk_downloader/app.py calls _load_app_config() at MODULE SCOPE, and
#   APP_CFG_FILE = Path("app_config.json") is RELATIVE, so it resolves against
#   the CURRENT WORKING DIRECTORY. At pytest COLLECTION time that is the
#   rootdir -- on the box, the operator's install directory. Importing any of
#   the 52 tracked test modules that import bulk_downloader.app* at module
#   scope therefore reads the operator's real config and ends in
#   aiassist.configure(enabled=..., api_key=..., ...).
#
# `isolated_bd_home` cannot help: it chdirs per TEST, and the import already
# happened during COLLECTION. Under xdist every worker collects the whole
# suite, so every worker inherits the setting before a single test runs.
#
# NAME, NOT PLACEMENT. Autouse fixtures declared in one conftest are ordered
# by SCOPE first, then declared dependency, then ALPHABETICALLY BY FIXTURE
# NAME -- never by definition order (pytest 8.4.2; the mechanism is
# `for name in dir(holderobj)` in _pytest/fixtures.py, and dir() sorts). This
# name sorts before `isolated_bd_home` on purpose, and moving this def has no
# effect at all. Renaming it does.
#
# WHY sys.modules RATHER THAN AN IMPORT. `isolated_bd_home` drops
# bulk_downloader.* from sys.modules for the 85 files carrying the
# bd_module_wipe marker. A fixture that imported aiassist in its body would
# RESURRECT bulk_downloader (and with it _envfile's one-shot .env seed) into a
# just-wiped sys.modules -- measured at 221 of 230 setups in a 17-file sample.
# Looking the module up instead means this is a no-op exactly when there is
# nothing to reset, and the test's own fresh import gets the literal defaults.
#
# WHAT IT DOES NOT PROMISE: it restores `_config` before each test. A fixture
# that re-runs _load_app_config() afterwards -- `fresh_app` does -- runs later
# and wins; 4 of the 5 tests using fresh_app see model_vision/model_text as ""
# because _load_app_config reads them off a cleared _app_cfg. `enabled` is
# correctly False in all five, which is the half this exists for. It does not
# touch `_health`, `_warmed` or `_FILENAME_META_CACHE`: those persist across
# tests deliberately and three test files depend on that.
#
# NOT OPT-IN, for the same reason the VPN guard below is not: a protection
# each test opts into has a denominator that excludes every test which forgot,
# and here the module that forgot is every module that imports the app.
#
# The mirror of this lives in run_tests_core.run_test -- `bd-band` does not
# read this file at all. Both call aiassist._reset_config_to_defaults().
@pytest.fixture(autouse=True)
def _aiassist_config_is_never_inherited():
    mod = sys.modules.get("bulk_downloader.aiassist")
    if mod is not None:
        mod._reset_config_to_defaults()
    yield


# ── v3.66.945: BD_INSTALL_DIR must never survive a test with a RELATIVE value
#
# Register item 34 spent three readings being called "four order-dependent
# SSRF/VPN band failures". It was neither. `test_v3_66_940_*` seeds every
# declared editor key from a `.env` file with placeholder values -- and
# BD_INSTALL_DIR is index 3, so its value is the string "v3". `load_envfile()`
# writes with `os.environ[k] = v`, which monkeypatch never RECORDS, so `undo()`
# cannot remove it and the value survives the session. `_resolve_db_path()` then
# joins "v3" onto whatever cwd the next test has, the parent directory does not
# exist, and sqlite3 raises `unable to open database file` four files away.
#
# The rule this inverts: section 0 says a test that VARIES an env var must POP
# it. The mirror is that a test exercising a real env WRITER must CONTAIN the
# write -- popping on entry is necessary and not sufficient.
#
# DELIBERATELY NARROW. Only BD_INSTALL_DIR, only a relative value. A general
# env-diff guard over the whole suite would fire on legitimate fixtures and get
# switched off, which section 0 weighs equally with a false clean. A relative
# BD_INSTALL_DIR has no legitimate use: it is only ever joined with a relative
# DB_PATH and resolved against the cwd. Measured across the 113-suite band
# (1461 tests): exactly ONE test wrote one.
def relative_install_dir_leak(env):
    """The value if BD_INSTALL_DIR is set to a relative path, else None.

    A module-level function, not an inline check, so it can be positive-controlled
    directly -- tests/test_v3_66_945_* does that. @944's battery escaped once
    because a predicate that must return None on a clean tree was never proven
    able to return anything else.
    """
    v = env.get("BD_INSTALL_DIR")
    if not v or os.path.isabs(str(v)):
        return None
    return str(v)


@pytest.fixture(autouse=True)
def _install_dir_never_leaks_relative():
    yield
    leaked = relative_install_dir_leak(os.environ)
    if leaked is None:
        return
    # REPAIR BEFORE FAILING. Without this the leak cascades and every later test
    # fails too, which buries the one test that actually caused it -- the exact
    # misreading that kept item 34 open. Name the leaker, then clean up so the
    # rest of the run still means something.
    os.environ.pop("BD_INSTALL_DIR", None)
    pytest.fail(
        f"this test left BD_INSTALL_DIR={leaked!r} in the environment -- a "
        f"RELATIVE value. _resolve_db_path() joins it with a relative DB_PATH "
        f"and sqlite3 resolves the result against the CWD, so the next test to "
        f"touch the database opens a path whose parent does not exist and fails "
        f"with `unable to open database file`, four files away and under a name "
        f"that describes none of this (register item 34). If the code under "
        f"test writes os.environ directly, monkeypatch cannot undo it: pop the "
        f"keys yourself in the fixture's teardown.")


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


# ─── The operator's real VPN config is off limits to the whole suite ──────────
#
# Caught 2026-07-29 by instrumenting vpn_config.save() and recording the running
# test's nodeid whenever the resolved path was the real user config:
#
#   test : test_v3_66_729_body_contract_fixtures.py::
#          test_no_control_sends_a_body_its_endpoint_refuses
#   path : ~/.config/bulk-downloader/vpn/tunnels.json
#   env  : BD_VPN_CONFIG_PATH=<unset>
#   stack: app_vpn_api.py:391 vpn_settings_update
#            -> vpn_config.update_global_settings(**data) -> save()
#
# The body-contract probe PUTs synthetic bodies at every endpoint to check they
# refuse malformed input. PUT /api/vpn/settings accepted one and saved. On the
# deploy box that replaced the operator's live settings with the probe's payload
# (leak_test_interval_s 1 against a default of 1800, so VPN leak tests ran every
# second instead of every 30 minutes) and wrote a malformed test fixture tunnel
# into their config, where it quarantined on load and blocked --vpn-tunnel
# seeding on every capture.
#
# The VPN tests already set BD_VPN_CONFIG_PATH and restore it in a finally, and
# that is exactly what fails: vpn_config's state is module-global and outlives
# the test, the override is popped on the way out, and any later save in the
# same process resolves to the real path. A protection each test opts into has a
# denominator that excludes every test which forgot -- and the one that forgot
# was not a VPN test at all.
#
# So: two layers, session-wide, neither opt-in.
_REAL_VPN_CONFIG = (
    pathlib.Path(os.path.expanduser("~")) / ".config" / "bulk-downloader"
    / "vpn" / "tunnels.json"
)


def _is_the_real_vpn_config(path) -> bool:
    try:
        return pathlib.Path(path).resolve() == _REAL_VPN_CONFIG.resolve()
    except Exception:
        return False


@pytest.fixture(autouse=True, scope="session")
def _never_write_the_real_vpn_config(tmp_path_factory):
    """Layer 1: point the session somewhere disposable.
    Layer 2: make a save that still resolves to the real config RAISE.

    Layer 2 is the load-bearing half. Layer 1 is an environment variable, which
    is precisely the thing a test pops in a finally -- so it cannot be the only
    protection for the property "no test writes the operator's VPN config".
    """
    sandbox = tmp_path_factory.mktemp("vpn_config") / "tunnels.json"
    os.environ.setdefault("BD_VPN_CONFIG_PATH", str(sandbox))

    try:
        from bulk_downloader import vpn_config as _vc
    except Exception:
        yield
        return

    _real_save = _vc.save

    def _guarded_save(*a, **k):
        target = _vc._config_path()
        if _is_the_real_vpn_config(target):
            raise RuntimeError(
                "refusing to write the operator's real VPN config from the test "
                f"suite: {target}. Set BD_VPN_CONFIG_PATH to a tmp path for this "
                "test. This guard exists because a body-contract probe once "
                "reached PUT /api/vpn/settings with no override in force and "
                "rewrote the deploy box's live VPN settings."
            )
        return _real_save(*a, **k)

    _vc.save = _guarded_save
    try:
        yield
    finally:
        _vc.save = _real_save


# ─── No test may write the operator's real $HOME config, by ANY route ─────────
#
# The VPN guard above wraps the FUNCTION vpn_config.save. That is the wrong
# denominator: the subject of "no test writes the operator's config" is a PATH.
# app_store_raw_editor._atomic_write (:129-133) does Path.write_text into a .tmp
# then os.replace onto the same path and never calls save(), so with the guard
# provably installed a POST to /api/settings/store-raw still returned 200 and
# changed the real tunnels.json. widgets_config had no guard at all, and the
# suite rewrote the operator's real dashboard layout on EVERY capture run --
# confirmed on the box, where a green capture left widgets.json holding the four
# DEFAULT_WIDGETS with "per_site": {}.
#
# So this layer keys on the RESOLVED DESTINATION of the write primitives. A new
# store, or an existing store reached by a route nobody wrapped, is covered
# without anyone remembering to add it.
#
# SCOPE IS DELIBERATELY NARROW, because a guard that fires on correct behaviour
# gets switched off. Only the app's own $HOME config namespace is protected --
# never $HOME at large. On the deploy box the checkout IS ~/BulkDownloader, so
# ~/BulkDownloader/macros is guarded ONLY when it resolves outside the repo;
# inside the repo it is ordinary untracked litter that git already surfaces.
_BD_CONFIG_DIRNAME = "bulk-downloader"
_MACRO_DIRNAME = "macros"
_REPO_ROOT_FOR_GUARD = pathlib.Path(__file__).resolve().parent.parent
_HOME_GUARD = {"on": True}


def _home_roots_for(home):
    """The app's own $HOME config namespace under `home`. Never $HOME at large.

    The macros exclusion is evaluated here so that BOTH halves of the union
    below get it: on the deploy box the checkout IS ~/BulkDownloader, and a
    frozen half that skipped this test would refuse ordinary repo writes for the
    whole session.
    """
    home = pathlib.Path(home)
    roots = [home / ".config" / _BD_CONFIG_DIRNAME]
    macros = home / "BulkDownloader" / _MACRO_DIRNAME
    try:
        macros.relative_to(_REPO_ROOT_FOR_GUARD)
    except ValueError:
        roots.append(macros)          # outside the checkout: operator state
    return tuple(roots)


# Frozen at IMPORT, from the HOME the session started under -- i.e. from the
# operator's real home, because nothing has had a chance to relocate it yet.
_SESSION_START_HOME_ROOTS = _home_roots_for(os.path.expanduser("~"))


def _protected_home_roots():
    """The UNION of the session-start roots and the call-time roots.

    Call-time resolution alone was the whole guard, and it is half right: it is
    what lets a test relocate HOME and exercise the real predicate without going
    anywhere near the operator's files, which is exactly what the os.mkdir REDs
    do. But roots that resolve also MOVE. While a test holds HOME at a tmp path
    the operator's real ~/.config/bulk-downloader belongs to no root at all, so
    the guard stops defending the one path it exists for -- measured, one
    absolute path, True before the relocation and False after it.

    Session-start roots alone are the mirror-image bug: they defend the operator
    and stop following HOME, which turns the relocation-based tests red. A
    candidate that swapped instead of unioning did precisely that, six tests
    over.

    So both. The union protects strictly more paths than either half, which is
    the direction that risks crying wolf, so the extra reach is kept to the same
    two app-owned namespaces under one additional home -- never $HOME at large,
    under either half.
    """
    roots = list(_SESSION_START_HOME_ROOTS)
    for root in _home_roots_for(os.path.expanduser("~")):
        if root not in roots:
            roots.append(root)
    return tuple(roots)


def _home_store_guard_bypass(fn):
    """Run fn with the guard suspended. For fixtures that must seed a store."""
    _HOME_GUARD["on"] = False
    try:
        return fn()
    finally:
        _HOME_GUARD["on"] = True


def _violates_home_store_guard(target) -> bool:
    if not _HOME_GUARD["on"]:
        return False
    try:
        raw = os.fspath(target)
    except TypeError:
        return False
    text = str(raw)
    # Cheap pre-filter: every protected root contains one of these two names, so
    # the expensive normalisation only runs on candidates.
    if _BD_CONFIG_DIRNAME not in text and _MACRO_DIRNAME not in text:
        return False
    candidate = pathlib.Path(text)
    if not candidate.is_absolute():
        candidate = pathlib.Path(os.getcwd()) / candidate
    resolved = os.path.normpath(str(candidate))
    for root in _protected_home_roots():
        root_s = str(root)
        if resolved == root_s or resolved.startswith(root_s + os.sep):
            return True
    return False


def _home_store_refusal(target):
    return RuntimeError(
        "refusing to write the operator's real config from the test suite: "
        f"{target}. Point the store at a tmp path for this test "
        "(BD_VPN_CONFIG_PATH / BD_WIDGETS_CONFIG_PATH / BD_INSTALL_DIR). "
        "This guard keys on the destination PATH, not on any store's save(), "
        "because the suite rewrote the operator's dashboard layout on every "
        "capture run through a route that never called save()."
    )


_WRITE_MODES = frozenset("wax+")


@pytest.fixture(autouse=True, scope="session")
def _never_write_the_real_home_config(tmp_path_factory):
    """Layer 1: redirect the known stores. Layer 2: refuse the path outright.

    Layer 1 is an env var, which is exactly what a test pops in a finally -- so
    it can never be the only protection. Layer 2 is the load-bearing half.
    """
    sandbox = tmp_path_factory.mktemp("home_config")
    os.environ.setdefault("BD_WIDGETS_CONFIG_PATH", str(sandbox / "widgets.json"))
    os.environ.setdefault("BD_VPN_CONFIG_PATH", str(sandbox / "vpn" / "tunnels.json"))

    # macro_recorder has no dedicated env var -- its only lever is
    # BD_INSTALL_DIR, and setting that session-wide would also move the plugin
    # dir, sites_config and everything else keyed off the install dir, which
    # constants.py freezes at IMPORT. So its layer 1 is the resolver itself,
    # the same shape as _never_write_the_repo_plugins_dir patching _plugin_dir.
    #
    # This half is not optional once os.mkdir is intercepted. Without it the
    # /api/macros/* routes 5xx on the guard and
    # test_v3_66_729_body_contract_fixtures.py::
    # test_the_app_never_5xxs_on_a_well_formed_request FAILS -- measured, twice.
    # Layer 2 alone converts a silent write into a red unrelated test, which is
    # how a guard gets switched off.
    #
    # Divert ONLY the unsteered case: a test that sets BD_INSTALL_DIR is
    # deliberately steering this path and must keep steering it. _macro_dir()
    # MKDIRS as a side effect, so the shim must not call the real one merely to
    # inspect its answer.
    _macro_sandbox = sandbox / "macros"
    try:
        from bulk_downloader import macro_recorder as _mr
    except Exception:  # noqa: BLE001
        _mr = None
    if _mr is not None:
        _real_macro_dir = _mr._macro_dir

        def _guarded_macro_dir():
            if os.environ.get("BD_INSTALL_DIR"):
                return _real_macro_dir()
            _macro_sandbox.mkdir(parents=True, exist_ok=True)
            return _macro_sandbox

        _mr._macro_dir = _guarded_macro_dir

    real_write_text = pathlib.Path.write_text
    real_write_bytes = pathlib.Path.write_bytes
    real_path_open = pathlib.Path.open
    real_open = builtins.open
    real_replace = os.replace
    real_rename = os.rename
    real_mkdir = os.mkdir

    def _guard_write_text(self, *a, **k):
        if _violates_home_store_guard(self):
            raise _home_store_refusal(self)
        return real_write_text(self, *a, **k)

    def _guard_write_bytes(self, *a, **k):
        if _violates_home_store_guard(self):
            raise _home_store_refusal(self)
        return real_write_bytes(self, *a, **k)

    def _guard_path_open(self, mode="r", *a, **k):
        if set(mode) & _WRITE_MODES and _violates_home_store_guard(self):
            raise _home_store_refusal(self)
        return real_path_open(self, mode, *a, **k)

    def _guard_open(file, mode="r", *a, **k):
        if set(str(mode)) & _WRITE_MODES and _violates_home_store_guard(file):
            raise _home_store_refusal(file)
        return real_open(file, mode, *a, **k)

    def _guard_mkdir(path, *a, **k):
        # ~/BulkDownloader/macros was a DECLARED protected root that nothing
        # intercepted: macro_recorder._macro_dir() reaches it with
        # Path.mkdir(parents=True), which lands on os.mkdir and touched none of
        # the six primitives above. Measured at 5e5e9c5 -- the guard's own
        # predicate returned True for the path while the directory was created
        # anyway, and test_home_config_stores_are_guarded.py reported OK because
        # it asserts the predicate, not the write. os.mkdir is the one hook that
        # covers Path.mkdir (including parents=True, which recurses through it)
        # AND os.makedirs, measured on CPython 3.12.3.
        if _violates_home_store_guard(path):
            raise _home_store_refusal(path)
        return real_mkdir(path, *a, **k)

    def _guard_replace(src, dst, *a, **k):
        if _violates_home_store_guard(dst):
            raise _home_store_refusal(dst)
        return real_replace(src, dst, *a, **k)

    def _guard_rename(src, dst, *a, **k):
        if _violates_home_store_guard(dst):
            raise _home_store_refusal(dst)
        return real_rename(src, dst, *a, **k)

    pathlib.Path.write_text = _guard_write_text
    pathlib.Path.write_bytes = _guard_write_bytes
    pathlib.Path.open = _guard_path_open
    builtins.open = _guard_open
    os.replace = _guard_replace
    os.rename = _guard_rename
    os.mkdir = _guard_mkdir
    try:
        yield
    finally:
        pathlib.Path.write_text = real_write_text
        pathlib.Path.write_bytes = real_write_bytes
        pathlib.Path.open = real_path_open
        builtins.open = real_open
        os.replace = real_replace
        os.rename = real_rename
        os.mkdir = real_mkdir
        if _mr is not None:
            _mr._macro_dir = _real_macro_dir


# ─── The repository's plugins/ directory is off limits to the whole suite ─────
#
# Reproduced 2026-07-29: running the plugin band on a clean tree leaves
#   ?? plugins/ackgate.py
#   ?? plugins/handdropped.py
#   ?? plugins/plugins.registry.json
# and modifies the TRACKED plugins/plugins.json. install_plugin() stages into
# plugins._plugin_dir(), which is INSTALL_DIR/"plugins".
#
# The trap is that INSTALL_DIR is frozen at IMPORT of constants.py
# (constants.py:15, Path.cwd() when BD_INSTALL_DIR is unset). Whichever happens
# first -- this conftest's chdir, or the first import of constants -- decides it
# for the entire session. Measured both ways in one afternoon: a single-file run
# imported after the chdir and got a tmp dir; the band imported before it and
# froze INSTALL_DIR to /home/user/BD. So the leak is import-ORDER dependent,
# which is why it survives: it does not reproduce when you run the one file you
# suspect.
#
# Setting BD_INSTALL_DIR from a fixture cannot fix it -- by then the value is
# already computed. The lever has to be _plugin_dir itself.
#
# The redirect SEEDS rather than empties: plugins/plugins.json is tracked and is
# read as load configuration, so a bare tmp directory would change what the
# loader sees. Copy the tracked contents across, and reads keep working while
# writes land somewhere disposable.
@pytest.fixture(autouse=True, scope="session")
def _never_write_the_repo_plugins_dir(tmp_path_factory):
    repo_plugins = pathlib.Path(__file__).resolve().parent.parent / "plugins"
    sandbox = tmp_path_factory.mktemp("plugins_root") / "plugins"
    sandbox.mkdir(parents=True, exist_ok=True)
    if repo_plugins.is_dir():
        for entry in repo_plugins.iterdir():
            if entry.is_file():
                try:
                    shutil.copy2(entry, sandbox / entry.name)
                except OSError:
                    pass

    try:
        from bulk_downloader import plugins as _pl
    except Exception:
        yield
        return

    _real_plugin_dir = _pl._plugin_dir

    def _guarded_plugin_dir():
        # Divert ONLY the repository case. A blanket override was the first
        # attempt and the band caught it: test_v3_66_805_plugin_state_bd_home
        # monkeypatches BD_HOME and asserts the quarantine state follows it out
        # of the install tree, which a constant sandbox breaks. Tests that
        # deliberately steer this path must keep steering it; the only thing
        # forbidden is landing on the source tree.
        try:
            target = _real_plugin_dir()
            if pathlib.Path(target).resolve() == repo_plugins.resolve():
                return sandbox
            return target
        except Exception:
            return sandbox

    # _quarantine_state_path relocates the state file under BD_HOME only when
    # _plugin_dir() resolves to the INSTALL-TREE DEFAULT; an explicit override
    # deliberately keeps its state co-located (the 485 isolation contract). Our
    # sandbox looks like an override, so without this shim the v3.66.805
    # invariant -- plugin state lives under BD_HOME, not in the install tree --
    # would stop holding, and its test fails in a band. Caught by the band, not
    # by reading. Patching constants.INSTALL_DIR instead was the obvious
    # alternative and is far worse: 46 call sites across db, _envfile, health
    # and more would move with it.
    _real_state_path = _pl._quarantine_state_path

    def _guarded_state_path():
        home = os.environ.get("BD_HOME")
        if home and pathlib.Path(_pl._plugin_dir()).resolve() == sandbox.resolve():
            return pathlib.Path(home).resolve() / ".plugin_state.json"
        return _real_state_path()

    _pl._plugin_dir = _guarded_plugin_dir
    _pl._quarantine_state_path = _guarded_state_path
    try:
        yield
    finally:
        _pl._plugin_dir = _real_plugin_dir
        _pl._quarantine_state_path = _real_state_path
