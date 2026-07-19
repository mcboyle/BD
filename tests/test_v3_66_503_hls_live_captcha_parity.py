"""v3.66.503 - HLS/Live/Captcha import-time tunables -> full GUI parity (Bucket 1).

RED-first. These 9 env vars were display-only because their readers bound them
into module-level constants at import (e.g. `INPUT_TIMEOUT_US = int(os.environ
.get(...))`). This slice refactors each to a call-time getter (store key > env
seed > default) so a Settings write takes effect live, and promotes them to
gui_exposure=full.

The assertions target the user-visible property (per the perf/dedup-test
lessons): set the global_config store key and the live getter must reflect it,
with store > env > default precedence. On pristine 502 the getters do not exist
and GLOBAL_CONFIG_SCHEMA lacks the keys -> RED.
"""
import json
import os
import sys
import tempfile
import importlib
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

# (module, getter_name, store_key, env_name, default, sentinel, cast)
_CASES = [
    ("hls_downloader", "_input_timeout_us", "hls_input_timeout_us",
     "BD_HLS_INPUT_TIMEOUT_US", 10000000, 7777777, int),
    ("hls_downloader", "_max_runtime_s", "hls_max_runtime_s",
     "BD_HLS_MAX_RUNTIME_S", 3600, 1234, int),
    ("hls_downloader", "_progress_poll_s", "hls_progress_poll_s",
     "BD_HLS_PROGRESS_POLL_S", 1.0, 2.5, float),
    ("live_recorder", "_poll_interval_s", "live_poll_interval_s",
     "BD_LIVE_POLL_INTERVAL_S", 60, 13, int),
    ("live_recorder", "_disconnect_tolerance_s", "live_disconnect_tolerance_s",
     "BD_LIVE_DISCONNECT_TOLERANCE_S", 180, 99, int),
    ("live_recorder", "_max_active_recordings", "live_max_active_recordings",
     "BD_LIVE_MAX_ACTIVE_RECORDINGS", 32, 5, int),
    ("live_recorder", "_launch_timeout_s", "live_launch_timeout_s",
     "BD_LIVE_LAUNCH_TIMEOUT_S", 45, 7, int),
    ("captcha_relay", "_pending_timeout_s", "captcha_pending_timeout_s",
     "BD_CAPTCHA_PENDING_TIMEOUT_S", 3600, 111, int),
    ("captcha_relay", "_push_dedupe_window_s", "captcha_push_dedupe_s",
     "BD_CAPTCHA_PUSH_DEDUPE_S", 300, 22, int),
]

_STORE_KEYS = [c[2] for c in _CASES]
_ENV_NAMES = [c[3] for c in _CASES]


def _fresh_store(d):
    from bulk_downloader import global_config as GC
    Path("app_config.json").write_text(json.dumps(d), encoding="utf-8")
    GC._cached = None
    GC._cached_mtime = 0.0


def _getter(modname, gname):
    m = importlib.import_module("bulk_downloader." + modname)
    return getattr(m, gname)


def test_getters_exist():
    """Each reader exposes a call-time getter (not a frozen module constant)."""
    missing = []
    for mod, g, *_ in _CASES:
        try:
            _getter(mod, g)
        except AttributeError:
            missing.append(mod + "." + g)
    assert missing == [], "missing call-time getters: " + ", ".join(missing)


def test_store_over_env_over_default():
    """store key > env seed > default, live (the user-visible parity property)."""
    for mod, g, key, env, default, sentinel, cast in _CASES:
        cwd = os.getcwd()
        tmp = tempfile.mkdtemp()
        os.chdir(tmp)
        saved = os.environ.get(env)
        try:
            os.environ.pop(env, None)
            _fresh_store({})
            getter = _getter(mod, g)
            assert getter() == cast(default), g + ": default"
            env_val = (sentinel + 1) if cast is int else (sentinel + 1.0)
            os.environ[env] = repr(env_val) if cast is float else str(env_val)
            _fresh_store({})
            assert getter() == cast(env_val), g + ": env seed honored"
            _fresh_store({key: str(sentinel)})
            assert getter() == cast(sentinel), g + ": store wins over env"
        finally:
            if saved is None:
                os.environ.pop(env, None)
            else:
                os.environ[env] = saved
            os.chdir(cwd)


def test_schema_has_all_keys():
    """GLOBAL_CONFIG_SCHEMA gained a store entry for every promoted tunable."""
    from bulk_downloader import global_config as GC
    s = GC.GLOBAL_CONFIG_SCHEMA
    missing = [k for k in _STORE_KEYS if k not in s]
    assert missing == [], "schema missing keys: " + ", ".join(missing)


def test_inventory_classifies_full_not_import_time():
    """The 9 env vars are no longer in _IMPORT_TIME and reach parity_target=full."""
    csi = importlib.import_module("config_surface_inventory")
    still_import_time = [e for e in _ENV_NAMES if e in csi._IMPORT_TIME]
    assert still_import_time == [], \
        "still classified import-time: " + ", ".join(still_import_time)
