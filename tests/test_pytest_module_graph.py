"""Regression coverage for pytest's BulkDownloader module-graph cleanup."""

import sys
from types import ModuleType

import pytest


real_pytest_only = pytest.mark.skipif(
    not hasattr(pytest, "__version__"),
    reason="exercises the real-pytest conftest lifecycle",
)


@real_pytest_only
def test_00_canonicalizer_rebinds_and_prunes_direct_children():
    from conftest import _canonicalize_package_children

    package = ModuleType("synthetic_package")
    stale = ModuleType("synthetic_package.child")
    canonical = ModuleType("synthetic_package.child")
    unrelated = ModuleType("logging")
    unrelated_alias = ModuleType("elsewhere")
    scalar_alias = object()
    canonical_alias = ModuleType("synthetic_package.alias")
    canonical_scalar = ModuleType("synthetic_package.scalar")
    missing = ModuleType("synthetic_package.missing")
    package.child = stale
    package.logging = unrelated
    package.alias = unrelated_alias
    package.scalar = scalar_alias
    modules = {
        "synthetic_package": package,
        "synthetic_package.child": canonical,
        "synthetic_package.alias": canonical_alias,
        "synthetic_package.scalar": canonical_scalar,
        "synthetic_package.missing": missing,
    }

    _canonicalize_package_children("synthetic_package", modules)

    assert package.child is canonical
    assert package.logging is unrelated
    assert package.alias is unrelated_alias
    assert package.scalar is scalar_alias
    assert package.missing is missing

    del modules["synthetic_package.child"]
    _canonicalize_package_children("synthetic_package", modules)

    assert not hasattr(package, "child")
    assert package.logging is unrelated


@real_pytest_only
def test_00_nested_package_children_are_rebound_and_pruned():
    from conftest import _canonicalize_package_children

    package = ModuleType("synthetic_package")
    stale_subpackage = ModuleType("synthetic_package.subpackage")
    subpackage = ModuleType("synthetic_package.subpackage")
    stale_leaf = ModuleType("synthetic_package.subpackage.leaf")
    leaf = ModuleType("synthetic_package.subpackage.leaf")
    package.subpackage = stale_subpackage
    subpackage.leaf = stale_leaf
    modules = {
        "synthetic_package": package,
        "synthetic_package.subpackage": subpackage,
        "synthetic_package.subpackage.leaf": leaf,
    }

    _canonicalize_package_children("synthetic_package", modules)

    assert package.subpackage is subpackage
    assert subpackage.leaf is leaf

    del modules["synthetic_package.subpackage.leaf"]
    _canonicalize_package_children("synthetic_package", modules)

    assert not hasattr(subpackage, "leaf")


@real_pytest_only
def test_01_seed_stale_parent_child_binding():
    """Model a test that removes a child from sys.modules but leaves its attr."""
    import bulk_downloader
    from bulk_downloader import provenance

    assert sys.modules["bulk_downloader.provenance"] is provenance
    del sys.modules["bulk_downloader.provenance"]
    assert bulk_downloader.provenance is provenance


@real_pytest_only
def test_02_fixture_removes_stale_parent_child_binding():
    """The next from-import must resolve a canonical, registered child."""
    import bulk_downloader
    from bulk_downloader import provenance

    assert sys.modules["bulk_downloader.provenance"] is provenance
    assert bulk_downloader.provenance is provenance
