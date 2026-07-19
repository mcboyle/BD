"""v3.66.721 -- restore tasktracker_gen.audit(). A gate was dropped, not deprecated.

PK carried a `audit()` function the work tree does not have; the work tree gained
`status()` in what looks like the same edit. Only `tasktracker_gen.py` lost anything --
the other eight PK/work-tree divergences are the work tree being NEWER (it gained
functions; PK is a stale snapshot). This one dropped a gate.

What was lost, in its own words:

    "--check only proves the md and xlsx agree with each other (render drift). It
     passed IN-SYNC this release while a completed-section row carried the *incomplete*
     schema and rendered blank Version/State/Notes."

That is the shape this whole session kept finding: **a gate that passes while the thing
it guards is broken.** `--check` compares two rendered artifacts to each other, so it is
green whenever they agree -- including when they agree on garbage. `--audit` checks the
DATA against its schema, which is the question actually being asked.

It catches three classes `--check` structurally cannot:
  1. per-section SCHEMA conformance (a completed row carrying the incomplete columns),
  2. DUPLICATE IDs across sections (an item left in two places),
  3. STATE.json prose vs DATA.json counts (the narrative lagging the data).
"""
import importlib.util
import json
import os
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _mod():
    spec = importlib.util.spec_from_file_location(
        "tasktracker_gen", os.path.join(ROOT, "tools", "tasktracker_gen.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_audit_exists():
    m = _mod()
    assert hasattr(m, "audit"), (
        "tasktracker_gen.audit() is gone -- the structural gate was dropped, and "
        "--check cannot cover it (it only proves the md and xlsx agree with EACH OTHER)")


def test_audit_is_wired_to_a_cli_flag():
    src = open(os.path.join(ROOT, "tools", "tasktracker_gen.py"),
               encoding="utf-8").read()
    assert '"--audit"' in src, "--audit is not reachable from the CLI"


def _write(d, data, state=None):
    """Use the tool's own DATA_NAME rather than guessing the filename -- _load_data
    sys.exit()s on a miss, which would look like a test failure instead of a bad fixture."""
    m = _mod()
    os.makedirs(d, exist_ok=True)
    json.dump(data, open(os.path.join(d, m.DATA_NAME), "w"))
    if state is not None:
        json.dump(state, open(os.path.join(d, "STATE.json"), "w"))


def test_audit_catches_the_schema_defect_check_cannot():
    """THE regression it was built for: a COMPLETED row carrying the INCOMPLETE schema.
    The md and xlsx still agree with each other, so --check is green; the row renders
    blank Version/State/Notes."""
    m = _mod()
    with tempfile.TemporaryDirectory() as d:
        good = {c: "x" for c in m.COMPLETED_COLS}
        good["ID"] = "OK-1"
        bad = {c: "x" for c in m.INCOMPLETE_COLS}   # WRONG schema for the completed section
        bad["ID"] = "BAD-1"
        _write(d, {"incomplete": [], "completed": [good, bad]})
        rc = m.audit(d)
        assert rc == 1, "audit() did not flag a completed row carrying the incomplete schema"


def test_audit_catches_duplicate_ids_across_sections():
    m = _mod()
    with tempfile.TemporaryDirectory() as d:
        inc = {c: "x" for c in m.INCOMPLETE_COLS}
        inc["ID"] = "DUP-1"
        comp = {c: "x" for c in m.COMPLETED_COLS}
        comp["ID"] = "DUP-1"          # same item left in two places
        _write(d, {"incomplete": [inc], "completed": [comp]})
        assert m.audit(d) == 1, "audit() did not flag a duplicate ID across sections"


def test_audit_passes_a_clean_tracker():
    """Negative control: a well-formed tracker must NOT be flagged, or the gate is noise."""
    m = _mod()
    with tempfile.TemporaryDirectory() as d:
        inc = {c: "x" for c in m.INCOMPLETE_COLS}
        inc["ID"] = "I-1"
        comp = {c: "x" for c in m.COMPLETED_COLS}
        comp["ID"] = "C-1"
        _write(d, {"incomplete": [inc], "completed": [comp]})
        assert m.audit(d) == 0, "audit() flagged a clean tracker"


def test_status_survived():
    """Guard against re-dropping: the function the work tree GAINED must stay."""
    m = _mod()
    assert hasattr(m, "status"), "status() lost while restoring audit()"
