"""v3.66.775 -- Plugin-v3 V3-A: grant-UI surfacing (backend half).

The @774 per-capability grant model is enforced at load, but the OPERATOR
cannot drive it from the GUI: `write_config` (the POST /api/plugins/config
write side) silently DROPS `granted_capabilities` because `_CONFIG_KEYS` does
not contain it -- the read side honors the key, the write side cannot set it.
A write path that silently drops a known key is this program's own failure
shape (the denominator excludes the thing being asked about).

This cut:
  * adds "granted_capabilities" to `_CONFIG_KEYS`, so the GUI config POST
    round-trips a grant to plugins.json (list branch str-coerces entries);
  * exposes `gated_capabilities` (the _GATED_CAPS set) and the effective
    `granted_capabilities` in plugins.status(), so the FE renders gated
    badges DERIVED from the backend instead of hand-mirroring the set.

RED on pristine v3.66.774: write_config drops the key; status() lacks both
keys. GREEN after.

run_tests.py conventions: zero-arg test functions.
"""
import json
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader import plugins as P  # noqa: E402


def _with_dir(tmp):
    orig = P._plugin_dir
    P._plugin_dir = lambda: Path(tmp)
    return orig


def test_write_config_round_trips_granted_capabilities():
    """write_config must persist granted_capabilities to plugins.json (the
    GUI write path). RED @774: _CONFIG_KEYS lacks the key, the write silently
    drops it and the on-disk config stays grant-less."""
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        cfg = P.write_config({"granted_capabilities": ["lifecycle"]})
        assert cfg.get("granted_capabilities") == ["lifecycle"], cfg
        on_disk = json.loads((Path(tmp) / "plugins.json").read_text("utf-8"))
        assert on_disk.get("granted_capabilities") == ["lifecycle"], on_disk
        # read_config (env-normalized view) must reflect it too
        assert P.read_config().get("granted_capabilities") == ["lifecycle"]
    finally:
        P._plugin_dir = orig


def test_write_config_coerces_grant_entries_to_str():
    """List entries are str-coerced like every other list config key."""
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        cfg = P.write_config({"granted_capabilities": ["lifecycle", 7]})
        assert cfg.get("granted_capabilities") == ["lifecycle", "7"], cfg
    finally:
        P._plugin_dir = orig


def test_write_config_grant_merge_preserves_other_keys():
    """A grant-only write must not clobber existing config (merge semantics)."""
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        P.write_config({"allow_full_access": True, "node_bin": "/usr/bin/node"})
        cfg = P.write_config({"granted_capabilities": ["page_access"]})
        assert cfg.get("granted_capabilities") == ["page_access"], cfg
        assert cfg.get("allow_full_access") is True, cfg
        assert cfg.get("node_bin") == "/usr/bin/node", cfg
    finally:
        P._plugin_dir = orig


def test_status_exposes_gated_and_granted_capabilities():
    """status() must carry the gated-cap set (so the FE derives badges from
    the backend, never hand-mirrors _GATED_CAPS) plus the effective grants.
    RED @774: neither key exists in status()."""
    P.reset()
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        (Path(tmp) / "plugins.json").write_text(
            '{"granted_capabilities": ["lifecycle"]}', "utf-8")
        P.load_all()
        s = P.status()
        assert s.get("gated_capabilities") == sorted(P._GATED_CAPS), s.get(
            "gated_capabilities")
        assert s.get("granted_capabilities") == ["lifecycle"], s.get(
            "granted_capabilities")
    finally:
        P._plugin_dir = orig
        P.reset()


def test_status_grants_deny_by_default():
    """With no config, status() reports the gated set and ZERO grants --
    deny-by-default is visible, not implied."""
    P.reset()
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        P.load_all()
        s = P.status()
        assert s.get("gated_capabilities") == sorted(P._GATED_CAPS), s
        assert s.get("granted_capabilities") == [], s.get("granted_capabilities")
    finally:
        P._plugin_dir = orig
        P.reset()


def test_gui_write_then_reload_admits_gated_plugin():
    """The full operator loop the grant-UI drives: POST-shaped write_config
    grant -> reset -> load_all admits a gated plugin WITHOUT full-access.
    RED @774: the write drops the grant, so the plugin stays skipped."""
    P.reset()
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        (Path(tmp) / "life.py").write_text(
            "PLUGIN={'name':'life','api_version':2,"
            "'capabilities':['lifecycle']}\n", "utf-8")
        # before the grant: denied
        res0 = P.load_all()
        e0 = {x["filename"]: x for x in res0["plugins"]}["life.py"]
        assert e0["ok"] is False and e0["skipped_reason"], e0
        # the GUI write (route body shape), then the route's reset+reload
        P.write_config({"granted_capabilities": ["lifecycle"]})
        P.reset()
        res1 = P.load_all()
        e1 = {x["filename"]: x for x in res1["plugins"]}["life.py"]
        assert e1["ok"] is True, e1
        assert res1["full_access"] is False, res1
    finally:
        P._plugin_dir = orig
        P.reset()
