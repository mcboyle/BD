"""DECOMP-R1 — shim-over-rm: re-export completeness.

`unzip -o` overlay cannot delete (H-01): a `<target>.py` -> `<package>/` split
deployed by overlay leaves the old file shadowing the package on stash, so
`dependency_graph` re-counts the monolith -> in-sync FAIL. The robust default is
to keep `<target>.py` as an ADD-only re-export shim over the package (no `rm`, no
shadow, preflight `isfile` stays green).

A shim is only safe if it re-exports EVERY name external importers pull from the
old module — including privates (H-04: a guard or other module doing
`from pkg.<target> import _private` must keep resolving). This test pins
`tools/decomp/make_shim.py`:

  * generate() must produce a shim that re-exports the full importer-set
    (public AND private names);
  * check_shim_complete() must FLAG a shim that drops any required name —
    so an incomplete shim is a red test, not a stash-only breakage.

Synthetic temp fixture (no real LEAF target is split yet @446). Custom-runner
conventions: zero-arg tests, `tempfile.mkdtemp` (no `tmp_path`), load the tool by
absolute path, `assert ..., msg`.
"""
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import pytest  # noqa: F401

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TOOL = _REPO_ROOT / "tools" / "decomp" / "make_shim.py"


def _load_tool():
    assert _TOOL.exists(), (
        f"{_TOOL.relative_to(_REPO_ROOT)} is missing — the DECOMP-R1 shim "
        f"generator was not landed."
    )
    spec = importlib.util.spec_from_file_location("_r1_make_shim", _TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _fixture_tree():
    """A temp tree: a package `widget/` exposing one public + one PRIVATE name,
    and an external importer pulling the private (the H-04 case)."""
    root = Path(tempfile.mkdtemp(prefix="r1_shim_"))
    pkg = root / "pkgsrc" / "widget"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        "from .core import public_fn, _private_helper\n"
        "__all__ = ['public_fn']\n",
        encoding="utf-8",
    )
    (pkg / "core.py").write_text(
        "def public_fn():\n    return 1\n\n"
        "def _private_helper():\n    return 2\n",
        encoding="utf-8",
    )
    importer = root / "pkgsrc" / "consumer.py"
    importer.write_text(
        "from pkgsrc.widget import public_fn\n"
        "from pkgsrc.widget import _private_helper  # H-04: private importer\n",
        encoding="utf-8",
    )
    return root


def test_required_reexports_includes_private():
    tool = _load_tool()
    root = _fixture_tree()
    try:
        required = tool.compute_required_reexports(
            "widget", search_dirs=[root / "pkgsrc"], pkg_qualifier="pkgsrc")
        assert "public_fn" in required and "_private_helper" in required, (
            f"importer-set scan missed a name (incl. the private): {sorted(required)}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_generated_shim_is_complete():
    tool = _load_tool()
    root = _fixture_tree()
    try:
        required = tool.compute_required_reexports(
            "widget", search_dirs=[root / "pkgsrc"], pkg_qualifier="pkgsrc")
        shim = tool.generate_shim("widget", "widget", required, pkg_qualifier="pkgsrc")
        missing = tool.check_shim_complete(shim, required)
        assert not missing, (
            f"generate() produced a shim that does not re-export: {sorted(missing)}")
        # both names must appear in the explicit re-export block
        assert "_private_helper" in shim and "public_fn" in shim, (
            "shim text is missing an explicit re-export")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_check_flags_an_incomplete_shim():
    """Teeth: a shim dropping a required private must be reported missing."""
    tool = _load_tool()
    incomplete = (
        '"""shim."""\n'
        "from pkgsrc.widget import *  # noqa\n"
        "from pkgsrc.widget import public_fn  # noqa  (dropped _private_helper)\n"
    )
    missing = tool.check_shim_complete(incomplete, {"public_fn", "_private_helper"})
    assert "_private_helper" in missing, (
        "check_shim_complete failed to flag a shim that drops a required private — "
        "the completeness gate would pass vacuously.")
