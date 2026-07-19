"""v3.66.774 -- Plugin-v3 V3-A (W3): per-capability grant model.

Closes the all-or-nothing trust gap for GATED capabilities. Today a plugin
declaring a gated cap (lifecycle / page_access) loads ONLY if the operator flips
the single allow_full_access switch -- all-or-nothing. This adds a per-capability
grant: plugins.json `granted_capabilities: [...]` lets the operator consent to a
SPECIFIC gated cap without opening full-access. Deny-by-default is preserved: a
gated cap that is neither granted nor covered by full-access is still skipped.

The gate logic (identical `(caps & gated_caps) and not full_access` in FOUR
places: plugins._validate_manifest, plugin_node, plugin_exec, plugin_py_bridge) is
consolidated into ONE SoT, plugins.capability_gate(), so the four runtimes can
never drift and the grant is enforced universally.

RED on pristine v3.66.773: no capability_gate SoT exists; a granted gated cap is
still skipped (only full-access opens it). GREEN after.

run_tests.py conventions: zero-arg test functions.
"""
import tempfile
from pathlib import Path

from bulk_downloader import plugins as P


def _with_plugin_dir(tmp):
    orig = P._plugin_dir
    P._plugin_dir = lambda: Path(tmp)
    return orig


def _write(pdir, name, body):
    (Path(pdir) / name).write_text(body, "utf-8")


def test_gated_cap_loads_when_individually_granted():
    """A gated cap named in granted_capabilities loads WITHOUT full-access."""
    P.reset()
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    try:
        (Path(tmp) / "plugins.json").write_text(
            '{"granted_capabilities": ["lifecycle"]}', "utf-8")
        _write(tmp, "life.py",
               "PLUGIN={'name':'life','api_version':2,'capabilities':['lifecycle']}\n")
        res = P.load_all()
        e = {x["filename"]: x for x in res["plugins"]}["life.py"]
        assert e["ok"] is True, e
        # per-capability grant does NOT imply full-access
        assert res["full_access"] is False, res
    finally:
        P._plugin_dir = orig
        P.reset()


def test_ungranted_gated_cap_still_denied():
    """Deny-by-default: a gated cap NOT granted (and no full-access) is skipped."""
    P.reset()
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    try:
        (Path(tmp) / "plugins.json").write_text(
            '{"granted_capabilities": ["lifecycle"]}', "utf-8")
        # declares page_access, which is gated and NOT granted
        _write(tmp, "pa.py",
               "PLUGIN={'name':'pa','api_version':2,'capabilities':['page_access']}\n")
        res = P.load_all()
        e = {x["filename"]: x for x in res["plugins"]}["pa.py"]
        assert e["skipped_reason"], e
        assert "page_access" in e["skipped_reason"], e["skipped_reason"]
    finally:
        P._plugin_dir = orig
        P.reset()


def test_full_access_still_grants_all_gated_caps():
    """Backward compat: allow_full_access still opens every gated cap."""
    P.reset()
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    try:
        (Path(tmp) / "plugins.json").write_text('{"allow_full_access": true}', "utf-8")
        _write(tmp, "life.py",
               "PLUGIN={'name':'life','api_version':2,"
               "'capabilities':['lifecycle','page_access']}\n")
        res = P.load_all()
        e = {x["filename"]: x for x in res["plugins"]}["life.py"]
        assert e["ok"] is True, e
    finally:
        P._plugin_dir = orig
        P.reset()


def test_capability_gate_is_the_single_sot():
    """The four runtime paths must route through plugins.capability_gate, not each
    reimplement the inline gate -- so the grant is enforced identically everywhere."""
    assert hasattr(P, "capability_gate"), "no capability_gate SoT in plugins"
    # direct unit check of the SoT's semantics
    gated = {"lifecycle", "page_access"}
    ok, why = P.capability_gate({"lifecycle"}, gated, full_access=False,
                                granted={"lifecycle"})
    assert ok is True and not why, (ok, why)
    ok, why = P.capability_gate({"page_access"}, gated, full_access=False,
                                granted={"lifecycle"})
    assert ok is False and "page_access" in why, (ok, why)
    ok, why = P.capability_gate({"lifecycle"}, gated, full_access=True, granted=set())
    assert ok is True, (ok, why)
    ok, why = P.capability_gate({"hook"}, gated, full_access=False, granted=set())
    assert ok is True, "non-gated caps are never blocked"

    # the runtimes must not carry the old inline gate anymore
    import bulk_downloader.plugin_node as _n
    import bulk_downloader.plugin_exec as _x
    import bulk_downloader.plugin_py_bridge as _b
    for mod in (_n, _x, _b):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "and not full_access" not in src, (
            "%s still carries the inline gate; route it through capability_gate"
            % Path(mod.__file__).name)
