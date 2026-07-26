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
    package.child = stale
    package.logging = unrelated
    modules = {
        "synthetic_package": package,
        "synthetic_package.child": canonical,
    }

    _canonicalize_package_children("synthetic_package", modules)

    assert package.child is canonical
    assert package.logging is unrelated

    del modules["synthetic_package.child"]
    _canonicalize_package_children("synthetic_package", modules)

    assert not hasattr(package, "child")
    assert package.logging is unrelated


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
