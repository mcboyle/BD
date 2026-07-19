"""test_templates_list_identity.py -- band guard for the templates -> site_templates cut.

Wraps tools/decomp/templates_snapshot.py: asserts the live TEMPLATES list matches the
frozen baseline (91 elements, original order, per-element content stable). This is the
binding invariant for DECOMP-LEAF cut 1 (the data-list analogue of a surface-lock).

Runs under the custom run_tests.py harness (zero-arg functions; derives repo root from
__file__). Never prints element bodies -- only hashes are compared.
"""
import importlib.util
import os

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOL = os.path.join(_REPO, "tools", "decomp", "templates_snapshot.py")


def _load_tool():
    spec = importlib.util.spec_from_file_location("templates_snapshot", _TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_templates_snapshot_baseline_present():
    tool = _load_tool()
    assert os.path.isfile(tool._BASELINE), "templates_snapshot_baseline.json missing"


def test_templates_list_identity_holds():
    tool = _load_tool()
    rc = tool.check()
    assert rc == 0, "TEMPLATES list-identity drift (see tool output)"


def test_templates_count_is_91():
    tool = _load_tool()
    manifest = tool.compute_manifest()
    assert manifest["count"] == 91, f"expected 91 elements, got {manifest['count']}"


def test_templates_shim_reexports_surface():
    # the shim must preserve module-attribute access for `from . import templates`
    from bulk_downloader import templates as t
    for name in ("TEMPLATES", "get", "list_templates", "suggest_for_url"):
        assert hasattr(t, name), f"templates shim dropped {name}"
    from bulk_downloader import site_templates as st
    assert t.TEMPLATES is st.TEMPLATES, "shim TEMPLATES is not the package list"
