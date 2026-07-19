"""BP-CFG (v3.66.285): global-config schema validation.

global_config.get(key, default) is a FLAT lookup. A nested block in
app_config.json (e.g. {"automation": {"auto_refresh": true}}) means the code
reading get("auto_refresh") silently gets the default — the feature is silently
OFF (the 266 footgun). A typo'd flat key has the same silent-OFF effect. This
makes those loud at load and fail-closes safety-bearing flags.

Pure validator (validate_config) + a loader hook (get_config logs + fail-closes).
Zero-arg functions; module globals restored in try/finally (monkeypatch is
unreliable in this runner). Repo root via __file__.
"""
import io
import json
import sys
import tempfile
from pathlib import Path

from bulk_downloader import global_config as GC


# A small explicit schema so the tests aren't coupled to the production seed.
_SCHEMA = {
    "verify_integrity": {"type": bool, "safety": True, "safe_default": True},
    "auto_refresh":     {"type": bool, "safety": False},
    "max_workers":      {"type": int,  "safety": False},
}
_NESTED_OK = ("tunnels", "global_settings")


def _kinds(findings, key=None):
    return [f["kind"] for f in findings if key is None or f.get("key") == key]


def test_nested_block_shadowing_flat_key_is_loud():
    findings = GC.validate_config({"automation": {"auto_refresh": True}},
                                  schema=_SCHEMA, known_nested=_NESTED_OK)
    assert "nested_wont_resolve" in _kinds(findings, "automation")
    # the finding names the lost sub-key so the operator can flatten it
    f = next(x for x in findings if x["key"] == "automation")
    assert "auto_refresh" in f["detail"]


def test_known_nested_block_is_not_flagged():
    # tunnels / global_settings are legitimately dicts — must NOT warn
    findings = GC.validate_config({"tunnels": {"wg0": {"endpoint": "x"}},
                                   "global_settings": {"theme": "dark"}},
                                  schema=_SCHEMA, known_nested=_NESTED_OK)
    assert findings == []


def test_nested_block_with_no_known_subkeys_is_not_a_false_positive():
    # a nested dict whose sub-keys match NO flat schema key is not the footgun
    findings = GC.validate_config({"some_plugin": {"opt_a": 1, "opt_b": 2}},
                                  schema=_SCHEMA, known_nested=())
    assert _kinds(findings, "some_plugin") == []


def test_type_mismatch_on_flat_key():
    findings = GC.validate_config({"max_workers": "eight"},
                                  schema=_SCHEMA, known_nested=())
    assert "type_mismatch" in _kinds(findings, "max_workers")


def test_typo_near_miss_of_a_schema_key():
    findings = GC.validate_config({"verfy_integrity": True},
                                  schema=_SCHEMA, known_nested=())
    f = next(x for x in findings if x["key"] == "verfy_integrity")
    assert f["kind"] == "typo_near_miss"
    assert "verify_integrity" in f["detail"]   # did-you-mean names the target


def test_unrelated_unknown_key_is_not_flagged_as_typo():
    # an unknown key that is NOT close to any schema key is left alone (low noise)
    findings = GC.validate_config({"completely_unrelated_thing": 1},
                                  schema=_SCHEMA, known_nested=())
    assert _kinds(findings, "completely_unrelated_thing") == []


def test_clean_config_yields_no_findings():
    findings = GC.validate_config(
        {"verify_integrity": True, "auto_refresh": False, "max_workers": 8,
         "tunnels": {"wg0": {}}},
        schema=_SCHEMA, known_nested=_NESTED_OK)
    assert findings == []


def test_apply_fail_closed_resets_safety_key_only():
    data = {"verify_integrity": "false", "auto_refresh": "nope"}
    findings = GC.validate_config(data, schema=_SCHEMA, known_nested=())
    safe = GC.apply_fail_closed(data, findings, schema=_SCHEMA)
    # safety-bearing flag with a bad type -> forced to its safe default (True)
    assert safe["verify_integrity"] is True
    # non-safety flag with a bad type is left as-is (warned, not coerced)
    assert safe["auto_refresh"] == "nope"
    # input dict is not mutated
    assert data["verify_integrity"] == "false"


def test_get_config_loads_but_warns_and_fail_closes_on_nested_and_bad_safety():
    d = Path(tempfile.mkdtemp())
    cfgp = d / "app_config.json"
    cfgp.write_text(json.dumps({
        # v3.66.716 deleted the non-dotted auto_refresh decoy (declared, safety-flagged,
        # read by nothing). Its live successor is automation.auto_refresh_enabled, which is
        # the key the runtime actually reads. This test guards the fail-CLOSED behaviour, so
        # it now exercises that key -- the behaviour is unchanged, only the subject moved.
        "automation": {"auto_refresh_enabled": True},   # nested -> shadows a safety key
        "automation.auto_refresh_enabled": "off",       # bad type on the flat safety key
    }))
    saved_file, saved_cache, saved_mtime = GC._CONFIG_FILE, GC._cached, GC._cached_mtime
    saved_err = sys.stderr
    sys.stderr = buf = io.StringIO()
    try:
        GC._CONFIG_FILE = cfgp
        GC._cached = None
        GC._cached_mtime = 0.0
        cfg = GC.get_config()
        warned = buf.getvalue()
    finally:
        sys.stderr = saved_err
        GC._CONFIG_FILE, GC._cached, GC._cached_mtime = saved_file, saved_cache, saved_mtime
    # still LOADS (fail-open into use) ...
    assert isinstance(cfg, dict) and "automation" in cfg
    # ... but the safety flag is fail-CLOSED to its safe default (False) on a bad type ...
    assert cfg.get("automation.auto_refresh_enabled") is False
    # ... and the load was LOUD about both problems, naming the keys
    assert "global_config" in warned
    assert "automation" in warned and "auto_refresh_enabled" in warned
