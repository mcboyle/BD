from pathlib import Path

import pytest
from tools.local_wheelhouse import LocalWheelhouse

BD_GATE_SCOPE = "module"


@pytest.mark.parametrize(
    ("versions", "expected"),
    [
        (("1.9.0", "1.10.0"), "1.10.0"),
        (("9.0.0", "10.0.0"), "10.0.0"),
        (("1.10.0rc1", "1.10.0"), "1.10.0"),
    ],
)
def test_latest_uses_parsed_version(tmp_path: Path, versions, expected):
    wheelhouse = LocalWheelhouse(tmp_path)
    for version in versions:
        wheelhouse.create_fixture_wheel("sort-demo", version)
    index = wheelhouse.refresh_index()
    assert len(index["sort-demo"]) == 2
    assert {wheel.version for wheel in index["sort-demo"]} == set(versions)
    latest = wheelhouse.find_wheel("sort-demo")
    assert latest is not None
    assert latest.version == expected, "WHEELHOUSE_VERSION_ORDER: latest must use parsed versions"
    assert latest.filename == f"sort_demo-{expected}-py3-none-any.whl"


def test_latest_after_older_wheel_is_added(tmp_path: Path):
    wheelhouse = LocalWheelhouse(tmp_path / "cache")
    expected = wheelhouse.create_fixture_wheel("sort-demo", "1.10.0")
    older = wheelhouse.create_fixture_wheel("sort-demo", "1.9.0", dest_dir=tmp_path / "incoming")
    wheelhouse.add_wheel(older.path)
    assert len(wheelhouse._index["sort-demo"]) == 2
    assert wheelhouse.find_wheel("sort-demo") == expected, "WHEELHOUSE_VERSION_ORDER: insertion order"


def test_single_wheel_is_byte_identical(tmp_path: Path):
    wheelhouse = LocalWheelhouse(tmp_path)
    expected = wheelhouse.create_fixture_wheel("sort-demo", "1.9.0")
    original_bytes = expected.path.read_bytes()
    original_metadata = expected.as_dict()
    wheelhouse.refresh_index()
    selected = wheelhouse.find_wheel("SORT_demo")
    assert selected is not None
    assert selected.as_dict() == original_metadata
    assert selected.path.read_bytes() == original_bytes


def test_explicit_version_and_missing_package_unchanged(tmp_path: Path):
    wheelhouse = LocalWheelhouse(tmp_path)
    expected = wheelhouse.create_fixture_wheel("sort-demo", "1.9.0")
    wheelhouse.create_fixture_wheel("sort-demo", "1.10.0")
    original_bytes = expected.path.read_bytes()
    wheelhouse.refresh_index()
    assert wheelhouse.find_wheel("sort-demo", "1.9.0") == expected
    assert expected.path.read_bytes() == original_bytes
    assert wheelhouse.find_wheel("sort-demo", "2.0.0") is None
    assert wheelhouse.find_wheel("absent") is None


@pytest.mark.parametrize("invalid", ["1.0_beta_x", "latest_build"])
def test_valid_version_outranks_unparseable_version(tmp_path: Path, invalid):
    wheelhouse = LocalWheelhouse(tmp_path)
    expected = wheelhouse.create_fixture_wheel("sort-demo", "1.2.0")
    wheelhouse.create_fixture_wheel("sort-demo", invalid)
    index = wheelhouse.refresh_index()
    assert len(index["sort-demo"]) == 2
    assert {wheel.version for wheel in index["sort-demo"]} == {"1.2.0", invalid}
    assert wheelhouse.find_wheel("sort-demo") == expected, "WHEELHOUSE_INVALID_VERSION: valid must win"


def test_only_unparseable_versions_return_none(tmp_path: Path):
    wheelhouse = LocalWheelhouse(tmp_path)
    for version in ("1.0_beta_x", "latest_build"):
        wheelhouse.create_fixture_wheel("sort-demo", version)
    index = wheelhouse.refresh_index()
    assert len(index["sort-demo"]) == 2
    assert {wheel.version for wheel in index["sort-demo"]} == {"1.0_beta_x", "latest_build"}
    assert wheelhouse.find_wheel("sort-demo") is None, "WHEELHOUSE_INVALID_VERSION: no valid candidate"
