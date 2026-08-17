"""test_templates_list_identity.py -- band guard for the templates -> site_templates cut.

Wraps tools/decomp/templates_snapshot.py: asserts the live TEMPLATES list matches the
frozen baseline (91 elements, original order, per-element content stable). This is the
binding invariant for DECOMP-LEAF cut 1 (the data-list analogue of a surface-lock).

Runs under the custom run_tests.py harness (zero-arg functions; derives repo root from
__file__). Never prints element bodies -- only hashes are compared.
"""
import importlib.util
import ast
import copy
import json
import os
import subprocess
import sys

BD_GATE_SCOPE = "repo-wide"

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


def test_a_template_content_mutation_is_detected():
    tool = _load_tool()
    from bulk_downloader import templates as t
    original = copy.deepcopy(t.TEMPLATES[0])
    try:
        t.TEMPLATES[0]["__snapshot_mutation_control__"] = True
        assert tool.check() == 1
    finally:
        t.TEMPLATES[0].clear()
        t.TEMPLATES[0].update(original)
    assert tool.check() == 0


def test_every_template_identity_producer_bands_this_gate():
    expected = {
        "bulk_downloader/templates.py",
        "bulk_downloader/site_templates/__init__.py",
        "bulk_downloader/site_templates/accessors.py",
        "bulk_downloader/site_templates/_data_cms.py",
        "bulk_downloader/site_templates/_data_heuristics.py",
        "bulk_downloader/site_templates/_data_mainstream.py",
        "bulk_downloader/site_templates/_data_players.py",
        "bulk_downloader/site_templates/_data_studios_a.py",
        "bulk_downloader/site_templates/_data_studios_b.py",
        "bulk_downloader/site_templates/_data_tubes.py",
    }
    package = os.path.join(_REPO, "bulk_downloader", "site_templates")
    init_path = os.path.join(package, "__init__.py")
    tree = ast.parse(open(init_path, encoding="utf-8").read())
    imported = {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module
    }
    data_modules = {name for name in imported if name.startswith("_data_")}
    tracked_data = {
        os.path.splitext(name)[0]
        for name in os.listdir(package)
        if name.startswith("_data_") and name.endswith(".py")
    }
    assert data_modules == tracked_data
    assert imported == data_modules | {"accessors"}
    data_aliases = {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.level == 1
        and node.module in data_modules
        for alias in node.names
    }
    template_assignments = [
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "TEMPLATES"
                for target in node.targets)
    ]
    assert len(template_assignments) == 1
    consumed_aliases = {
        node.id for node in ast.walk(template_assignments[0].value)
        if isinstance(node, ast.Name)
    }
    assert consumed_aliases == data_aliases

    shim = ast.parse(open(os.path.join(_REPO, "bulk_downloader", "templates.py"),
                          encoding="utf-8").read())
    assert any(isinstance(node, ast.ImportFrom) and node.level == 1
               and node.module == "site_templates" for node in shim.body)
    derived = {
        "bulk_downloader/templates.py",
        "bulk_downloader/site_templates/__init__.py",
        "bulk_downloader/site_templates/accessors.py",
        *{"bulk_downloader/site_templates/%s.py" % name for name in data_modules},
    }
    assert derived == expected
    assert all(os.path.isfile(os.path.join(_REPO, path)) for path in derived)

    band = os.path.join(_REPO, "toolchain", "bin", "bd-band-derive")
    for producer in sorted(derived):
        cp = subprocess.run(
            [sys.executable, band, "--file", producer, "--json"],
            cwd=_REPO, text=True, capture_output=True, timeout=60,
        )
        assert cp.returncode == 0, (producer, cp.stdout, cp.stderr)
        payload = json.loads(cp.stdout[cp.stdout.find("{"):])
        assert "tests/test_templates_list_identity.py" in payload["band"], producer


def test_regen_order_checks_but_never_freezes_template_identity():
    regen = os.path.join(_REPO, "toolchain", "bin", "bd-regen-order")
    text = open(regen, encoding="utf-8").read()
    chain = text[text.index("CHAIN = ["):text.index("VERIFY = [")]
    verify = text[text.index("VERIFY = ["):text.index("_BASELINE_SHA_RE")]
    assert "templates_snapshot.py" not in chain
    assert '["tools/decomp/templates_snapshot.py", "--check"]' in verify
    assert "--freeze" not in verify
    assert 'failed.append(label)' in text
    assert 'failed.append("route counts")' not in text
