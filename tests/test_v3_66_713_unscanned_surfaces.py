"""v3.66.713 (A-GUI Cut 4) -- the surfaces the config inventory never scanned.

710 fixed the denominator for the layers the inventory KNEW about. These are the
layers it does not know exist:

  * ENV BEYOND .py -- the env tranche gate scans `.py` only. 12 BD_* knobs live in
    install/deploy shell scripts (BD_DEPLOY_DIR, BD_RESTART_CMD, BD_VENV_PYTHON...)
    and are invisible to it. The gate that is supposed to make BD_* literals
    impossible to miss cannot see them.
  * ENV BEYOND BD_* -- the scan matches the BD_ prefix, so 5 non-prefixed runtime env
    vars are unscored, including CLOAKBROWSER_BINARY_PATH (which browser binary to
    EXECUTE). Note the irony: NETNS_NS / NETNS_BROWSER_BIN were deliberately named
    without the BD_ prefix TO DODGE the env-tranche gate (FG-ENV-TRANCHE-BD-LITERAL).
    The gate's own pressure creates config surface it cannot see.
  * PLUGIN MANIFEST fields -- 8, scored 0.
  * DB-BACKED CONFIG -- capture_schedules, scheduled_exports, api_auth_tokens,
    share_tokens: config that lives in SQLite, not in a file or an env var. Scored 0.
  * PERSISTED STORES -- 11 JSON files BD writes. _scan_other_stores is a hand-list of
    exactly TWO modules.

What this cut must NOT do is inflate the denominator with double-counts. The 39
implicit BD_<KEY> env LOCKS (os.environ.get("BD_" + field.upper()) in app.py) are
OVERRIDES of global_config keys already counted -- they are an ATTRIBUTE of an
existing setting, not new settings. A denominator padded with duplicates is no more
honest than one that truncates.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _inv():
    from tools import config_surface_inventory as csi

    return csi.build(ROOT)


def _keys():
    return {i["key"]: i for i in _inv()["items"]}


def test_shell_env_vars_are_scanned():
    """The env gate is .py-only, so operator-facing deploy knobs are invisible."""
    items = _keys()
    expect = ["BD_DEPLOY_DIR", "BD_RESTART_CMD", "BD_VENV_PYTHON", "BD_CLOAK_PACK"]
    missing = [k for k in expect if k not in items]
    assert not missing, (
        "BD_* env vars in install/deploy shell scripts are unscanned: %s" % missing)


def test_non_bd_runtime_env_is_scanned():
    """CLOAKBROWSER_BINARY_PATH decides WHICH BROWSER BINARY runs. It is unscored
    only because it lacks the BD_ prefix the scan matches on."""
    items = _keys()
    missing = [k for k in ("CLOAKBROWSER_BINARY_PATH", "PLAYWRIGHT_BROWSERS_PATH")
               if k not in items]
    assert not missing, "non-BD_ runtime env vars are unscanned: %s" % missing


def test_db_backed_config_is_scanned():
    items = _keys()
    missing = [k for k in ("capture_schedules", "scheduled_exports", "share_tokens")
               if k not in items]
    assert not missing, (
        "config that lives in SQLite is unscanned: %s" % missing)


def test_plugin_manifest_fields_are_scanned():
    kinds = {i["kind"] for i in _inv()["items"]}
    assert "plugin_manifest" in kinds, "plugin manifest fields are unscanned"


def test_persisted_stores_are_recorded():
    """_scan_other_stores is a hand-list of two modules; BD writes 11 JSON stores."""
    d = _inv()
    stores = d.get("stores") or []
    names = {s["file"] if isinstance(s, dict) else s for s in stores}
    for f in ("app_config.json", "secrets.json", "vault_tokens.json",
              "user_templates.json"):
        assert f in names, "persisted store not recorded: %s" % f


def test_env_locks_are_an_attribute_not_duplicate_rows():
    """os.environ.get("BD_" + field.upper()) makes a BD_<KEY> lock for EVERY
    global_config key. Those are overrides of settings already counted -- recording
    them as rows would double-count. Pin that they are an attribute."""
    d = _inv()
    gc = [i for i in d["items"] if i["kind"] == "global_config"]
    assert gc, "no global_config items"
    assert all("env_lock" in i for i in gc), (
        "global_config items do not carry the env_lock attribute")
    keys = {i["key"] for i in d["items"]}
    dupes = [k for k in keys if k.startswith("BD_AUTOMATION_")]
    assert not dupes, (
        "implicit env locks were added as separate rows -- that double-counts the "
        "same setting: %s" % dupes)


def test_no_fake_debt_was_created():
    """Deploy/bootstrap env is not "parity not yet delivered" -- a GUI write to
    BD_DEPLOY_DIR is meaningless. Scanning these must TRACK them, not manufacture
    open debt."""
    import json

    d = _inv()
    base = json.loads(open(os.path.join(ROOT, "reports", "config_parity_baseline.json"),
                           encoding="utf-8").read())
    assert d["counts"]["open_runtime_tunable"] == base["open_count"], (
        "open=%d vs pinned %d -- re-pin in the same cut"
        % (d["counts"]["open_runtime_tunable"], base["open_count"]))
    assert d["counts"]["open_runtime_tunable"] <= 11, (
        "scanning new surfaces created %d open items; deploy/bootstrap knobs are not "
        "parity debt" % d["counts"]["open_runtime_tunable"])
