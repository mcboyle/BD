"""Row 387 — the live version-pin census must have exactly one AST pin.

``bd-versync`` already refuses duplicate pins during a release check.  That is
too late to be the only protection: a same-valued duplicate can otherwise
survive ordinary PR CI and become another hand-maintained bump site.  This
gate asks the production AST index about the current test population directly.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import yaml


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent
_SELF = "tests/test_row387_ast_version_pin_guard.py"
_WORKFLOW = _REPO / ".github" / "workflows" / "ci.yml"


def _indexer():
    import tools.build_pin_index as build_pin_index

    return importlib.reload(build_pin_index)


def _version_pins(indexer):
    unreadable = indexer.unparseable_test_files()
    assert not unreadable, f"AST version-pin census is UNKNOWN: unreadable={unreadable}"
    return [pin for pin in indexer.build_index()["pins"] if pin["form"] == "version"]


def _require_one_version_pin(pins):
    assert len(pins) == 1, f"expected exactly one AST version pin; found {pins}"


def _workflow_suites():
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    matrix = workflow["jobs"]["gate-suites"]["strategy"]["matrix"]["include"]
    return {
        shard["name"]: shard["suites"].split()
        for shard in matrix
        if "suites" in shard
    }


def test_live_tree_has_the_one_authoritative_ast_version_pin():
    """Positive control: current source and its one intended pin agree."""
    pins = _version_pins(_indexer())
    _require_one_version_pin(pins)

    pin = pins[0]
    from bulk_downloader import __version__

    assert pin["file"] == "tests/test_settings_center_slice4.py"
    assert pin["value"] == __version__


def test_same_valued_name_and_attribute_pins_are_two_ast_pins(tmp_path):
    """Negative control: a duplicate cannot hide merely by agreeing in value."""
    root = tmp_path
    path = root / "tests" / "test_duplicate.py"
    path.parent.mkdir()
    path.write_text(
        "from bulk_downloader import __version__\n"
        "import bulk_downloader\n"
        "\n"
        "def test_name_pin():\n"
        "    assert __version__ == '3.66.700'\n"
        "\n"
        "def test_attribute_pin():\n"
        "    assert bulk_downloader.__version__ == '3.66.700'\n",
        encoding="utf-8",
    )

    pins, _ = _indexer()._scan_file(path, root)
    version_pins = [pin for pin in pins if pin["form"] == "version"]

    assert len(version_pins) == 2
    with pytest.raises(AssertionError, match="expected exactly one AST version pin"):
        _require_one_version_pin(version_pins)


def test_this_repo_wide_guard_is_directly_scheduled_in_ci():
    """The guard is ineffective if a green PR never executes it."""
    occurrences = [
        shard for shard, suites in _workflow_suites().items() if _SELF in suites
    ]
    assert occurrences == ["artifacts-pins"], (
        f"{_SELF} must run exactly once in artifacts-pins; found {occurrences}"
    )
