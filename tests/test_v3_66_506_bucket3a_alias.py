"""v3.66.506 — Bucket 3a: promote the cloakbrowser alias to a first-class full key.

`session_keeper_use_cloakbrowser` (env `BD_SESSION_KEEPER_USE_CLOAKBROWSER`) is the
third leg of cloak.resolve_backend()'s backend-resolution triple. The read path
(cloak._CFG_KEYS) and the write path (app_global_config.py) already honor it; it was
display-only ONLY because config_surface_inventory._ALIAS_OF_FULL marked it redundant
with the canonical `browser_backend` control, and it was never a declared key in
GLOBAL_CONFIG_SCHEMA.

Bucket 3a makes it first-class:
  1. declared in GLOBAL_CONFIG_SCHEMA (type bool),
  2. dropped from _ALIAS_OF_FULL (so the inventory marks it runtime-tunable -> full),
  3. manifest entry flipped display-only -> full.

RED-first: every assertion below fails on pristine v3.66.505. Custom-runner safe:
zero-arg tests; env restored in try/finally; tempfile.mkdtemp (no tmp_path).
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

_ENV = "BD_SESSION_KEEPER_USE_CLOAKBROWSER"
_KEY = "session_keeper_use_cloakbrowser"


def _fresh_store(d: dict) -> None:
    from bulk_downloader import global_config as GC
    Path("app_config.json").write_text(json.dumps(d), encoding="utf-8")
    GC._cached = None
    GC._cached_mtime = 0.0


def test_alias_declared_in_schema():
    from bulk_downloader import global_config as GC
    s = GC.GLOBAL_CONFIG_SCHEMA
    assert _KEY in s, f"{_KEY} not declared in GLOBAL_CONFIG_SCHEMA"
    assert s[_KEY]["type"] is bool, s[_KEY]


def test_resolve_backend_honors_alias_store_over_env():
    """Store value honored when env unset; env still wins as a deploy override.

    cloakbrowser isn't importable in the sandbox, so resolve_backend() would
    downgrade a cloakbrowser request to playwright. Stub is_available -> True
    (restored in finally) so the test measures the store/env precedence the cut
    relies on, not the package-availability downgrade.
    """
    from bulk_downloader import cloak
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    saved_env = {k: os.environ.get(k) for k in cloak._ENV_KEYS}
    saved_avail = cloak.is_available
    try:
        cloak.is_available = lambda: True
        for k in cloak._ENV_KEYS:
            os.environ.pop(k, None)
        # store asks for cloakbrowser via the alias leg only (no browser_backend/use_cloak)
        _fresh_store({_KEY: True})
        assert cloak.resolve_backend() == "cloakbrowser"   # store alias honored (env unset)
        # env override still wins over the store
        os.environ["BD_BROWSER_BACKEND"] = "playwright"
        _fresh_store({_KEY: True})
        assert cloak.resolve_backend() == "playwright"      # env deploy override preserved
    finally:
        cloak.is_available = saved_avail
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        os.chdir(cwd)


def test_alias_dropped_from_alias_of_full():
    import config_surface_inventory as P2
    assert _ENV not in P2._ALIAS_OF_FULL, "alias must be promoted out of _ALIAS_OF_FULL"


def test_alias_full_in_inventory_and_manifest():
    import config_surface_inventory as P2
    d = P2.build(str(_REPO))
    items = {it["key"]: it for it in d["items"] if it["kind"] == "env_var"}
    assert items[_ENV]["runtime_tunable"] is True, items[_ENV]
    assert items[_ENV]["gui_exposure"] == "full", (items[_ENV]["gui_exposure"],)
    m = json.loads((_REPO / "reports/config_gui_manifest.json").read_text()).get("exposed", {})
    assert m.get(_ENV) == "full", m.get(_ENV)
