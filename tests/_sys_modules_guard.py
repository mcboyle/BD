"""Catch a `patch.dict(sys.modules, ...)` that EVICTS a real module.

BACKLOG 101. `unittest.mock.patch.dict` snapshots the target dict on entry and,
on exit, CLEARS it and restores that snapshot. So any key that appeared while
the block was open is DELETED -- including a module imported as a side effect of
the code under test. Nobody writes that deletion and, until this file, no gate
saw it.

It poisons any identity-keyed lazy cache whose OWNER survives while its SUBJECT
does not. Measured at v3.66.1085: httpx builds `HTTPCORE_EXC_MAP` on first use,
mapping httpcore's exception CLASSES to its own. Evicting `httpcore` while httpx
survived left that map holding classes from a module object that no longer
existed, so the next import created a second one, every `isinstance()` against
the map failed, and httpx re-raised a raw `httpcore.ConnectError` through the
branch it marks `# pragma: no cover`. That surfaced on test6 as a capture
failure -- "submit raised ConnectError instead of QBError" -- three layers away
from anything the test touched.

WHY A RUNTIME DETECTOR AND NOT A GREP GATE. The hazard is SCHEDULE-DEPENDENT.
Whether a module is "first imported inside the block" depends on what some
earlier test already imported, and `--dist loadfile` decides that. Measured
2026-08-13 at e8bb3fd: an audit of all 28 existing sites, run serially, observed
ZERO evictions -- and that is not evidence of safety, because a different
schedule imports different things first. A static census cannot answer a
question whose answer changes with worker assignment; only a check that runs
every time can.

WHAT THIS CANNOT SEE, stated per CLAUDE.md section 1's rule that an instrument
must publish its blind spots:
  - it fires on the EVICTION, not on the harm. Whether an eviction matters
    depends on some other module holding an identity-keyed reference to the
    evicted one, which this does not attempt to judge. It is deliberately the
    stricter question: the eviction is always unintended.
  - a site that does not EXECUTE evicts nothing and is not thereby safe.
  - it sees `sys.modules` only. The same restore-to-snapshot semantics apply to
    every `patch.dict` target, and an identity-keyed cache over any other dict
    would fail the same way unwatched.
  - if an exception is already propagating out of the block, the eviction is
    RECORDED but not raised, because replacing a real failure with this one
    would hide the thing the test actually found.
"""

from __future__ import annotations

import sys
from unittest import mock

BLIND_SPOTS = (
    "fires on the eviction, not on the harm it may or may not cause",
    "an unexecuted site evicts nothing and is not thereby safe",
    "watches sys.modules only, not every patch.dict target",
)

# Names deliberately exempt. EMPTY, and that is a measurement rather than an
# omission: the audit at e8bb3fd found zero evictions across all 28 sites, so
# nothing needed exempting. An entry here must carry why, and a backlog row.
ALLOWED: frozenset[str] = frozenset()

# (nodeid, [module names]) for everything observed, including the cases that
# were recorded rather than raised.
observed: list[tuple[str, list[str]]] = []

_current_nodeid: str | None = None
_original_unpatch = None


class SysModulesEviction(AssertionError):
    """A patch.dict(sys.modules, ...) deleted a module it did not insert."""


def set_nodeid(nodeid: str | None) -> None:
    global _current_nodeid
    _current_nodeid = nodeid


def _evicted_real_modules(patcher) -> list[str]:
    """Modules that appeared inside the block and are about to be deleted.

    The keys the patch ITSELF set are excluded: replacing `httpx` with a fake is
    a deliberate swap, and restoring it is the whole point. What is left is
    anything the code under test imported while the block was open.

    `__spec__ is not None` filters out the bare sentinels and objects tests
    stuff into sys.modules by hand -- those are not modules whose identity
    anything caches.
    """
    original = patcher._original
    if original is None:
        return []
    added = set(patcher.in_dict) - set(original) - set(patcher.values)
    return sorted(
        name for name in added
        if name not in ALLOWED
        and getattr(sys.modules.get(name), "__spec__", None) is not None
    )


def _guarded_unpatch(self):
    evicted: list[str] = []
    try:
        if self.in_dict is sys.modules:
            evicted = _evicted_real_modules(self)
    except Exception:
        # A detector that breaks the run it watches is worse than no detector.
        evicted = []

    result = _original_unpatch(self)

    if evicted:
        observed.append((_current_nodeid or "<unknown test>", evicted))
        # Do NOT replace an in-flight failure with this one: the test found
        # something, and that something is more informative than the eviction.
        if sys.exc_info()[0] is None:
            raise SysModulesEviction(
                "patch.dict(sys.modules, ...) evicted %d module(s) it did not "
                "insert: %s.\n"
                "These were imported INSIDE the block, so they were absent from "
                "the snapshot the restore rewound to, and mock deleted them.\n"
                "Any module that survived while holding an identity-keyed "
                "reference to one of these now holds classes from a module "
                "object that no longer exists -- every isinstance() against it "
                "fails. That is backlog 101, and v3.66.1085 is the worked "
                "example (httpx's HTTPCORE_EXC_MAP over an evicted httpcore).\n"
                "FIX: import the module at tests/conftest.py scope so it is in "
                "every later snapshot and no restore can evict it."
                % (len(evicted), ", ".join(evicted))
            )
    return result


def arm() -> None:
    """Wrap mock's dict restore. Idempotent."""
    global _original_unpatch
    if _original_unpatch is not None:
        return
    _original_unpatch = mock._patch_dict._unpatch_dict
    mock._patch_dict._unpatch_dict = _guarded_unpatch


def disarm() -> None:
    global _original_unpatch
    if _original_unpatch is None:
        return
    mock._patch_dict._unpatch_dict = _original_unpatch
    _original_unpatch = None
