"""The sys.modules eviction guard fires on the hazard and not on the idiom.

BACKLOG 101. `patch.dict(sys.modules, ...)` restores the dict to its ENTRY
SNAPSHOT, so a module first imported INSIDE the block is deleted on exit. That
poisons any identity-keyed lazy cache whose owner survived -- v3.66.1085 is the
worked example, where httpx's `HTTPCORE_EXC_MAP` outlived the httpcore classes
it maps and every `isinstance()` against it began failing.

WHY THIS IS A RUNTIME GUARD RATHER THAN A CENSUS. The hazard is
SCHEDULE-DEPENDENT: whether a module is "first imported inside the block"
depends on what an earlier test already imported, which `--dist loadfile`
decides. Measured 2026-08-13 at e8bb3fd, an audit of all 28 existing sites run
serially observed ZERO evictions -- and that is not evidence of safety, it is
one schedule's answer to a question with many. A static gate would have reported
that zero as a pass.

THE TWO DIRECTIONS THIS FILE ASSERTS, because a guard that only fires is as
broken as one that never does:
  - it FIRES when a real module is imported inside the block and evicted;
  - it stays SILENT on the ordinary idiom -- swapping a module for a fake --
    which is 27 of the 28 sites in this repo and must keep working.
"""

from __future__ import annotations

import contextlib
import importlib
import sys
from unittest.mock import patch

import pytest

import _sys_modules_guard

# Its subject is one guard's behaviour, not an invariant over the tree.
BD_GATE_SCOPE = "module"


@contextlib.contextmanager
def _a_module_nothing_has_imported(tmp_path, tag):
    """A REAL, importable module whose name cannot already be in sys.modules.

    THE FIRST VERSION OF THIS FILE USED STDLIB NAMES -- wave, colorsys, sunau --
    and that made three tests SCHEDULE-DEPENDENT: they only exercised the
    eviction path when nothing earlier in the worker had imported the module.
    Measured at v3.66.1096: test4 went red on `test_the_guard_records_what_it_saw`
    while test5, test6 and test7 passed the identical commit, because
    `--dist loadfile` had put something importing colorsys on that worker first.

    That is exactly the defect class this session closed in backlog 25, shipped
    by the cut that closes backlog 101. The repair is to stop borrowing a name
    the rest of the suite also owns: the module is written to this test's own
    tmp_path under a unique tag, so "not yet imported" is true BY CONSTRUCTION
    rather than by luck, and the precondition assertion below can actually fail.

    A real file imported through the real machinery, deliberately -- a synthetic
    ModuleType would test the guard against a shape the import system never
    produces.
    """
    name = f"_bd_1097_probe_{tag}"
    (tmp_path / f"{name}.py").write_text("VALUE = 1\n", encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        assert name not in sys.modules, (
            f"{name} was already imported, which is impossible unless this "
            "helper's uniqueness guarantee broke")
        yield name
    finally:
        if str(tmp_path) in sys.path:
            sys.path.remove(str(tmp_path))
        sys.modules.pop(name, None)


def test_the_guard_is_armed_during_this_suite():
    """A guard nothing installed is a guard that never runs.

    Asserted rather than assumed: conftest arms it in pytest_configure, and if
    that wiring is removed every other test in this file still passes by
    exercising a detector that is not actually watching the suite.
    """
    from unittest import mock
    assert mock._patch_dict._unpatch_dict is _sys_modules_guard._guarded_unpatch, (
        "conftest did not arm the eviction guard, so nothing is watching the "
        "28 patch.dict(sys.modules) sites in this repo")


def test_it_fires_when_a_module_is_first_imported_inside_the_block(tmp_path):
    """The hazard, reproduced exactly.

    `wave` is chosen because nothing in this suite imports it, so it is
    genuinely absent from the entry snapshot -- the precondition is asserted
    rather than hoped for, per CLAUDE.md section 6.
    """
    with _a_module_nothing_has_imported(tmp_path, "fires") as name:
        with pytest.raises(_sys_modules_guard.SysModulesEviction) as excinfo:
            with patch.dict(sys.modules, {"httpx": object()}):
                importlib.import_module(name)   # first import happens in here

        assert name in str(excinfo.value), (
            f"the guard fired without naming what it evicted: {excinfo.value}")


def test_it_stays_silent_on_an_ordinary_fake_swap():
    """THE OVER-SENSITIVITY CONTROL, and the one that matters most.

    Replacing a real module with a fake is the idiom at 27 of this repo's 28
    sites. A guard that fired on it would be red across six files on the day it
    landed and would be switched off within the hour -- CLAUDE.md section 0
    counts that as a soundness bug, not a safe default.
    """
    # Establish the precondition rather than assume it. Run alone, this file
    # does not import httpx anywhere -- conftest pre-imports httpCORE, which is
    # a different module -- so without this line the block below would be an
    # INSERTION, and the test would exercise the eviction path while claiming
    # to prove the swap path is quiet. It failed exactly that way on its first
    # run, which is the precondition assertion earning its place.
    import httpx  # noqa: F401
    assert "httpx" in sys.modules, (
        "precondition: httpx must already be imported for this to be a SWAP")

    sentinel = object()
    with patch.dict(sys.modules, {"httpx": sentinel}):
        assert sys.modules["httpx"] is sentinel

    assert sys.modules["httpx"] is not sentinel, "the swap did not unwind"


def test_it_stays_silent_when_the_block_imports_something_already_present():
    """The other half of the idiom: importing inside the block is fine as long
    as the module was already in the snapshot. Only the FIRST import is a
    hazard, and a guard that could not tell those apart would ban a normal
    pattern."""
    import json  # noqa: F401  -- already imported by conftest and everything else
    assert "json" in sys.modules

    with patch.dict(sys.modules, {"httpx": object()}):
        import json  # noqa: F401,F811  -- re-import of a present module
    assert "json" in sys.modules, "a module present before the block was evicted"


def test_a_real_module_the_patch_itself_inserted_is_not_reported():
    """The patch's OWN values are never evictions, even when they are real
    modules under names that did not exist before.

    This is the case the other silence tests cannot reach. Swapping `httpx` for
    a Mock is quiet for two independent reasons -- the key was already in the
    snapshot, and a Mock has no `__spec__` -- so a guard that dropped the
    `patcher.values` exclusion entirely would still pass them. It took a
    mutation escape to find that: inserting a REAL module under a NEW key is
    the only shape where that exclusion is load-bearing.

    Removing the value you inserted is the documented contract of patch.dict,
    not an accident, and reporting it would fire on correct code.
    """
    import json
    alias = "_bd_1095_alias_for_json"
    assert alias not in sys.modules, "precondition: the alias must be absent"

    with patch.dict(sys.modules, {alias: json}):
        assert sys.modules[alias] is json
        assert getattr(sys.modules[alias], "__spec__", None) is not None, (
            "precondition: the inserted value must be a REAL module, or the "
            "__spec__ filter would make this quiet for the wrong reason")

    assert alias not in sys.modules, "the patch did not unwind its own value"


def test_a_nonmodule_stuffed_into_sys_modules_is_not_reported():
    """Tests put bare sentinels in sys.modules deliberately. Those have no
    __spec__ and no identity anything caches, so reporting them would be noise
    that trains the reader to ignore the guard."""
    assert "_bd_1095_not_a_module" not in sys.modules

    with patch.dict(sys.modules, {"httpx": object()}):
        sys.modules["_bd_1095_not_a_module"] = object()

    assert "_bd_1095_not_a_module" not in sys.modules, "the sentinel survived"


def test_the_guard_records_what_it_saw(tmp_path):
    """The eviction must be actionable after the fact, not only at the moment
    it raises -- a run that ends with a raised guard should still be able to
    say which test and which module."""
    before = len(_sys_modules_guard.observed)

    with _a_module_nothing_has_imported(tmp_path, "records") as name:
        with pytest.raises(_sys_modules_guard.SysModulesEviction):
            with patch.dict(sys.modules, {"httpx": object()}):
                importlib.import_module(name)

    assert len(_sys_modules_guard.observed) > before, (
        "the guard raised but recorded nothing, so a summary cannot report it")
    nodeid, mods = _sys_modules_guard.observed[-1]
    assert name in mods
    assert "test_the_guard_records_what_it_saw" in nodeid, (
        f"the record is not attributed to the test that caused it: {nodeid}")


def test_it_does_not_replace_an_in_flight_failure(tmp_path):
    """If the test already failed, THAT is the more informative failure.

    A guard that overwrites a real assertion error with its own turns a
    diagnosable failure into a confusing one two layers away -- which is
    precisely what the un-guarded eviction did at 1085.
    """
    with _a_module_nothing_has_imported(tmp_path, "inflight") as name:
        with pytest.raises(ValueError, match="the real failure"):
            with patch.dict(sys.modules, {"httpx": object()}):
                importlib.import_module(name)
                raise ValueError("the real failure")

    # It still recorded, so nothing is lost -- it simply did not raise.
    assert any(name in mods for _, mods in _sys_modules_guard.observed), (
        "the eviction was neither raised nor recorded, so it is invisible")
