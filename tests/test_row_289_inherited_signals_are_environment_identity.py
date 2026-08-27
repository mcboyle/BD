"""Row 289: inherited signal dispositions are part of a test lane's identity.

The same tree changed verdict solely because its parent ignored HUP, INT, QUIT
and PIPE.  A run context that records host, workers, distribution and load but
not the process masks therefore describes two materially different lanes as the
same environment.  These tests compare the recorder with an independent read
of the running process and fail closed when procfs cannot supply a field.
"""
from __future__ import annotations

import pathlib
import sys


BD_GATE_SCOPE = "repo-wide"

_TESTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS))
import _run_context as run_context  # noqa: E402

_MASK_FIELDS = ("SigIgn", "SigBlk")


class _Options:
    numprocesses = None
    dist = "no"


class _Config:
    option = _Options()


def _proc_masks() -> dict[str, str]:
    """Independently read the complete two-field population from procfs."""
    found: dict[str, str] = {}
    for line in pathlib.Path("/proc/self/status").read_text(
        encoding="ascii"
    ).splitlines():
        name, separator, value = line.partition(":")
        if separator and name in _MASK_FIELDS:
            found[name.lower()] = "0x%016x" % int(value.strip(), 16)
    assert len(found) == len(_MASK_FIELDS) == 2, (
        "precondition: procfs did not expose the complete nonzero mask-field "
        f"denominator; found {sorted(found)}"
    )
    return found


def test_the_run_context_records_this_process_signal_identity():
    """The exact ambient masks must survive into the persisted context.

    This one test is intentionally disposition-agnostic: it is run once from a
    clean shell and once below ``trap '' HUP INT QUIT PIPE``.  Deriving its
    expectation directly from this process makes both lanes valid while still
    requiring their recorded identities to differ.
    """
    expected = _proc_masks()
    context = run_context.context(_Config())
    recorded = {name: context.get(name) for name in ("sigign", "sigblk")}
    assert recorded == expected, (
        "run context did not record the process's exact SigIgn/SigBlk identity: "
        f"expected {expected}, recorded {recorded}"
    )


def test_signal_mask_collection_is_complete_and_fails_closed(tmp_path):
    """A missing field is UNKNOWN, never a clean-looking zero mask."""
    complete = tmp_path / "complete.status"
    complete.write_text(
        "Name:\tprobe\nSigBlk:\t0000000000004000\n"
        "SigIgn:\t0000000000001007\n",
        encoding="ascii",
    )
    masks = run_context.signal_masks(complete)
    assert masks == {
        "sigign": "0x0000000000001007",
        "sigblk": "0x0000000000004000",
    }, f"the two-field fixture was not parsed exactly: {masks}"

    partial = tmp_path / "partial.status"
    partial.write_text("SigIgn:\t0000000000001007\n", encoding="ascii")
    partial_masks = run_context.signal_masks(partial)
    assert partial_masks["sigign"] == "0x0000000000001007", partial_masks
    assert partial_masks["sigblk"] == "UNKNOWN", (
        "an unavailable SigBlk field was reported as a measured value: "
        f"{partial_masks}"
    )
    missing_masks = run_context.signal_masks(tmp_path / "absent.status")
    assert sum(value == "UNKNOWN" for value in missing_masks.values()) == 2, (
        "the absent source did not produce two explicit UNKNOWN verdicts: "
        f"{missing_masks}"
    )


def test_transform_control_only_imports_the_recorder():
    """Mutation transform control: importing is not a signal assertion."""
    assert run_context.DIR_NAME == "bd-runctx"
