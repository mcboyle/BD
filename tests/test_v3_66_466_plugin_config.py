"""v3.66.466: plugin-config GUI backend -- discovered_plugins / read_config /
write_config (the write side of the Maintenance plugin-settings panel; CLI->GUI
parity for BD_PLUGINS_ENABLE / BD_PLUGINS_ALLOW_FULL_ACCESS).

Runner-safe: zero-arg fns, tempfile.mkdtemp, _plugin_dir overridden + restored.
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


def test_discovered_plugins_lists_non_underscore():
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        for n in ("a.py", "b.py", "_helper.py", "notes.txt"):
            (Path(tmp) / n).write_text("# x\n", "utf-8")
        assert P.discovered_plugins() == ["a.py", "b.py"]
    finally:
        P._plugin_dir = orig


def test_write_config_writes_json_and_rejects_unknown_keys():
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        cfg = P.write_config({"disabled": ["b.py"], "allow_full_access": True, "bogus": 1})
        assert cfg["disabled"] == ["b.py"]
        assert cfg["allow_full_access"] is True
        assert "bogus" not in cfg
        on_disk = json.loads((Path(tmp) / "plugins.json").read_text("utf-8"))
        assert on_disk == cfg
    finally:
        P._plugin_dir = orig


def test_write_config_merges_preserving_existing():
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        P.write_config({"allow_full_access": True})
        # a second write that only sets disabled must preserve allow_full_access
        cfg = P.write_config({"disabled": ["x.py"]})
        assert cfg["allow_full_access"] is True
        assert cfg["disabled"] == ["x.py"]
    finally:
        P._plugin_dir = orig


def test_read_config_roundtrips():
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        P.write_config({"disabled": ["q.py"], "allow_full_access": False})
        rc = P.read_config()
        assert rc["disabled"] == ["q.py"]
        assert rc["allow_full_access"] is False
        # enabled absent -> None (permissive), order -> []
        assert rc["enabled"] is None and rc["order"] == []
    finally:
        P._plugin_dir = orig


def test_write_config_coerces_types():
    tmp = tempfile.mkdtemp()
    orig = _with_dir(tmp)
    try:
        cfg = P.write_config({"allow_full_access": 1, "enabled": ["a.py", 2]})
        assert cfg["allow_full_access"] is True
        assert cfg["enabled"] == ["a.py", "2"]
    finally:
        P._plugin_dir = orig
