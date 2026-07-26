"""Phase 4.2a (CLI->GUI parity): queue-housekeeping tunables promoted into the
global_config store so a Settings write takes effect (env stays the seed default).

RED-first against the 305 tree: getters read os.environ only (a store write has no
effect), the schema lacks the queue_hk_* keys, and Settings.tsx/api-types.ts don't
reference them -> these fail. After the promotion + SPA control -> GREEN.

Sandbox: tools+bulk_downloader; zero-arg fns; canonical package imports;
tempfile not tmp_path; module globals restored in try/finally.
"""
import os
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from tools import autonomy_queue_hk as qh  # noqa: E402
from bulk_downloader import global_config as gc  # noqa: E402

_ENV = ("BD_QUEUE_HK_GC_AGE_DAYS", "BD_QUEUE_HK_ABANDON",
        "BD_QUEUE_HK_MAX_RETRIES", "BD_QUEUE_HK_STALE_HOURS")


def _isolated_store():
    """Point global_config at a fresh temp file + clear its cache. Returns the
    prior (path, cached, mtime) to restore."""
    prior = (gc._CONFIG_FILE, gc._cached, gc._cached_mtime)
    d = Path(tempfile.mkdtemp())
    gc._CONFIG_FILE = d / "app_config.json"
    gc._cached = None
    gc._cached_mtime = 0.0
    return prior


def _restore(prior):
    gc._CONFIG_FILE, gc._cached, gc._cached_mtime = prior


def _clear_env():
    saved = {k: os.environ.pop(k, None) for k in _ENV}
    return saved


def _restore_env(saved):
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_store_value_takes_effect():
    """A global_config write is honored by the getters (the GUI-write path)."""
    prior = _isolated_store()
    saved = _clear_env()
    try:
        assert gc.set_config({"queue_hk_gc_age_days": 3, "queue_hk_max_retries": 2,
                              "queue_hk_stale_hours": 99, "queue_hk_abandon": True})
        assert qh._gc_age_days() == 3
        assert qh._max_retries() == 2
        assert qh._stale_hours() == 99
        assert qh._abandon_enabled() is True
    finally:
        _restore_env(saved)
        _restore(prior)


def test_env_is_the_seed_default_when_store_unset():
    """With no store key, the BD_QUEUE_HK_* env var still applies."""
    prior = _isolated_store()
    saved = _clear_env()
    try:
        os.environ["BD_QUEUE_HK_GC_AGE_DAYS"] = "5"
        os.environ["BD_QUEUE_HK_ABANDON"] = "1"
        assert qh._gc_age_days() == 5
        assert qh._abandon_enabled() is True
    finally:
        _restore_env(saved)
        _restore(prior)


def test_store_overrides_env():
    """Store wins over env when both are set."""
    prior = _isolated_store()
    saved = _clear_env()
    try:
        os.environ["BD_QUEUE_HK_GC_AGE_DAYS"] = "5"
        assert gc.set_config({"queue_hk_gc_age_days": 11})
        assert qh._gc_age_days() == 11
    finally:
        _restore_env(saved)
        _restore(prior)


def test_hard_default_when_store_and_env_absent():
    prior = _isolated_store()
    saved = _clear_env()
    try:
        assert qh._gc_age_days() == 7
        assert qh._max_retries() == 10
        assert qh._stale_hours() == 24
        assert qh._abandon_enabled() is False
    finally:
        _restore_env(saved)
        _restore(prior)


def test_schema_validates_queue_hk_keys():
    """The keys are in GLOBAL_CONFIG_SCHEMA with int/bool types -> a wrong type
    surfaces a type_mismatch finding (loud, not silent)."""
    findings = gc.validate_config({"queue_hk_gc_age_days": "not-an-int"})
    kinds = {(f["key"], f["kind"]) for f in findings}
    assert ("queue_hk_gc_age_days", "type_mismatch") in kinds
    assert "queue_hk_abandon" in gc.GLOBAL_CONFIG_SCHEMA
    assert gc.GLOBAL_CONFIG_SCHEMA["queue_hk_stale_hours"]["type"] is int


def test_spa_control_references_queue_hk_keys():
    """The Settings page + the GlobalConfigSubset type expose the 4 keys, so they
    have a real GUI control (not just the API) — the basis for marking them
    gui_exposure=full in the parity manifest."""
    types_src = (ROOT / "frontend/src/lib/api-types.ts").read_text(encoding="utf-8")
    settings_src = (ROOT / "frontend/src/routes/Settings.tsx").read_text(encoding="utf-8")
    for key in ("queue_hk_gc_age_days", "queue_hk_abandon",
                "queue_hk_max_retries", "queue_hk_stale_hours"):
        assert key in types_src, "GlobalConfigSubset missing %s" % key
        assert key in settings_src, "Settings.tsx has no control for %s" % key
