"""v3.66.677 -- PLG-5: `bd plugin new <kind>` scaffolder.

Proves tools/plugin_scaffold.py emits, for every host extension kind, a plugin
module that (a) is syntactically valid Python, (b) carries a PLUGIN manifest the
host accepts via plugins.api_compatible, (c) declares the right capability, and
(d) the CLI writes a file + refuses to clobber. Zero-arg tests.
"""
from __future__ import annotations

import ast
import importlib.util
import tempfile
from pathlib import Path

from bulk_downloader import plugins

_SCAFFOLD = Path(__file__).resolve().parent.parent / "tools" / "plugin_scaffold.py"


def _load():
    spec = importlib.util.spec_from_file_location("plugin_scaffold", _SCAFFOLD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_kind_scaffolds_valid_accepted_plugin():
    ps = _load()
    for kind in ps.KNOWN_KINDS:
        src = ps.scaffold(kind, name=f"demo_{kind}")
        # (a) valid Python
        tree = ast.parse(src)
        # (b) has a PLUGIN manifest dict
        manifest = None
        g = {}
        # extract the PLUGIN literal without importing bulk_downloader inside exec
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                    getattr(t, "id", "") == "PLUGIN" for t in node.targets):
                manifest = ast.literal_eval(node.value)
        assert isinstance(manifest, dict), f"{kind}: no PLUGIN manifest"
        # (c) capability matches the kind
        assert kind in manifest["capabilities"], (kind, manifest["capabilities"])
        # (b') host accepts the manifest's api range
        ok, reason = plugins.api_compatible(manifest)
        assert ok, f"{kind}: host rejected scaffold manifest: {reason}"


def test_unknown_kind_raises():
    ps = _load()
    try:
        ps.scaffold("not_a_kind")
        assert False, "expected ValueError for unknown kind"
    except ValueError:
        pass


def test_cli_writes_file_and_refuses_overwrite():
    ps = _load()
    d = tempfile.mkdtemp(prefix="plg5_")
    rc = ps.main(["processor", "plex_refresh", "--out", d])
    assert rc == 0
    fp = Path(d) / "plex_refresh.py"
    assert fp.is_file(), "scaffold CLI must write the plugin file"
    assert "plugins.processor" in fp.read_text(encoding="utf-8")
    # second run without --force refuses
    rc2 = ps.main(["processor", "plex_refresh", "--out", d])
    assert rc2 == 1, "must refuse to overwrite without --force"
    # --force overwrites
    rc3 = ps.main(["processor", "plex_refresh", "--out", d, "--force"])
    assert rc3 == 0
