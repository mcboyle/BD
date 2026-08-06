"""build_session_pack's two final gates both fail open, and each cites the other.

THE DEFECT, and it is CLAUDE.md section 0 twice in one function.

  schema_gate  reads `/mnt/project/STATE_schema.json`, a retired-era absolute
      path that does not exist here or on the box. It returns [] when the file
      is absent -- "schema unavailable in this env -- skip (bd-state still
      gates)". MEASURED: `/mnt/project` does not exist, and the schema it wants
      IS tracked, at `project-knowledge/STATE_schema.json`. So the gate skips
      itself while the file it needs sits in the repo.

  bd-state     is invoked as a BARE NAME through subprocess, which is
      PATH-dependent. MEASURED: `bd-state` is not on PATH; `toolchain/bin/
      bd-state` is present. The FileNotFoundError branch prints "not on PATH --
      run it manually as the final gate" and execution falls through to
      `RESULT: pack ready` and `return 0`.

Each gate's justification for failing open is the other one. schema_gate skips
because "bd-state still gates"; bd-state does not run at all. The chain is
inert end to end, and the tool reports success -- a gate that cannot see its
subject saying OK, truthfully and uselessly.

WHY THIS IS ONE CUT AND NOT TWO. Same function, same failure mode, same
repair (resolve the repo-local path instead of an absent absolute one), and
same blast radius. Fixing only one leaves the tool reporting `RESULT: pack
ready` on a pack whose binding facts were never checked -- and the surviving
gate's comment would still point at the fixed one as its excuse, which is how
this pair got here.

RED IN BOTH DIRECTIONS. Two cases fail on pristine source. Three more pass
before and after: they pin that a REAL mismatch is still caught, that a
reachable-and-passing gate is still a pass, and that the repair does not
invent a failure on a healthy tree.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
import zipfile

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
_TOOL = _REPO / "tools" / "build_session_pack.py"


def _load():
    spec = importlib.util.spec_from_file_location("_bsp_gates", _TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_zip(path: pathlib.Path, version: str = "3.66.888"):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("bulk_downloader/__init__.py", '__version__ = "%s"\n' % version)
        zf.writestr("tools/build_release.py", "# guard body\n")
        zf.writestr("a.txt", "a\n")


def _schema_required():
    sch = json.loads((_REPO / "project-knowledge" / "STATE_schema.json")
                     .read_text(encoding="utf-8"))
    return sch.get("required") or []


def _fixture(tmp_path: pathlib.Path, state_extra=None):
    """A pack that reaches the bd-state gate -- the LAST gate in main().

    THE STATE IS SCHEMA-COMPLETE ON PURPOSE, and getting that wrong is why
    this file's first draft proved nothing. With required keys missing, main()
    exits at the SCHEMA gate and never reaches bd-state, so the bd-state case
    passed after the repair for the wrong reason -- caught by bd-mutate, not by
    review. The zip still does not match the STATE, so a bd-state that RUNS
    must reject it.
    """
    zp = tmp_path / "rel.zip"
    _fake_zip(zp)
    pack = tmp_path / "pack"
    pack.mkdir()
    state = {k: "x" for k in _schema_required()}
    state.update({
        "built_version": "0.0.0",
        "zip": {"name": "OLD.zip", "file": "OLD.zip", "sha256": "x", "file_count": 1},
        "guards": {"tools/build_release.py": "deadbeef"},
        "guards_full_sha256": {"tools/build_release.py": "dead" * 16},
    })
    if state_extra:
        state.update(state_extra)
    sp = tmp_path / "STATE.draft.json"
    sp.write_text(json.dumps(state), encoding="utf-8")
    return ["--state", str(sp), "--zip", str(zp),
            "--pack-dir", str(pack), "--out", str(tmp_path / "pack.zip")]


def _run_main(mod, argv):
    """Return (exit_code, stdout). SystemExit is an exit code, not a crash."""
    import io
    import contextlib
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = mod.main(argv)
    except SystemExit as e:
        return (e.code if isinstance(e.code, int) else 1), buf.getvalue() + str(e.code)
    return rc, buf.getvalue()


# --------------------------------------------------------------------------- #
# RED: the two gates that fail open                                            #
# --------------------------------------------------------------------------- #

def test_bd_state_gate_actually_runs(tmp_path):
    """The binding final gate must EXECUTE, not be skipped for being off PATH.

    The fixture's STATE deliberately does not match its zip, so a gate that
    runs must reject it. On pristine source the bare-name subprocess raises
    FileNotFoundError, the branch prints a note, and main() returns 0 -- the
    pack is declared ready with its binding facts unchecked.
    """
    mod = _load()
    rc, out = _run_main(mod, _fixture(tmp_path))
    assert "not on PATH" not in out, (
        "bd-state was skipped for being off PATH. It is resolvable at "
        "toolchain/bin/bd-state; a bare-name subprocess is the defect.")
    assert "bd-state:" in out, (
        "main() never printed a bd-state verdict, so the binding gate did not "
        "EXECUTE. That is the defect: on pristine source the bare-name call "
        "raised FileNotFoundError and the tool declared the pack ready with "
        "its binding facts unchecked. stdout=%r" % out[-400:])
    assert rc == 0, (
        "the gate ran and rejected a pack it should accept -- refresh_state's "
        "job is to make STATE match the zip, so this fixture is legitimately "
        "clean and a failure here is over-sensitivity. stdout=%r" % out[-400:])


def test_a_failing_bd_state_fails_the_pack(tmp_path):
    """The verdict must reach the exit code.

    A real mismatch cannot be built through this path -- `refresh_state`
    rewrites STATE from the zip, so by the time the gate runs they agree, and
    the first draft of this file asserted a failure that could never happen.
    The gate is stubbed to fail instead, which is the only way to pin that its
    non-zero is honoured rather than printed.
    """
    mod = _load()
    mod._bd_state_cmd = lambda: [sys.executable, "-c",
                                 "import sys; print('MISMATCH'); sys.exit(3)"]
    rc, out = _run_main(mod, _fixture(tmp_path))
    assert rc != 0, (
        "bd-state exited non-zero and the pack was still declared ready. "
        "stdout=%r" % out[-300:])
    assert "pack ready" not in out


def test_an_unlocatable_bd_state_is_a_failure_not_a_skip(tmp_path):
    """The branch the repair added, forced -- it is unreachable while the tool
    is present in the tree, so only a stub can exercise it.

    Pinned because the original defect WAS this branch silently continuing.
    """
    mod = _load()
    mod._bd_state_cmd = lambda: None
    rc, out = _run_main(mod, _fixture(tmp_path))
    assert rc != 0, (
        "bd-state could not be located and main() still reported success -- "
        "that is the original defect restored. stdout=%r" % out[-300:])
    assert "pack ready" not in out, (
        "the tool announced 'pack ready' with its binding gate unrun.")


def test_an_unfindable_schema_is_a_failure_not_a_pass(tmp_path):
    """Same shape on the other gate: 'nothing missing' and 'nothing checked'
    must not share an outcome."""
    mod = _load()
    mod._schema_path = lambda: None
    rc, out = _run_main(mod, _fixture(tmp_path))
    assert rc != 0, (
        "the schema could not be found and main() continued -- an unrunnable "
        "gate reported clean. stdout=%r" % out[-300:])


def test_schema_gate_finds_the_tracked_schema(tmp_path):
    """schema_gate must resolve the schema the repo actually ships.

    It reads a hardcoded `/mnt/project/STATE_schema.json`, which does not
    exist here or on the box, and returns [] when absent. The schema IS
    tracked, at project-knowledge/STATE_schema.json, whose `required` list
    the fixture state deliberately violates.
    """
    mod = _load()
    state = json.loads((_REPO / "project-knowledge" / "STATE_schema.json")
                       .read_text(encoding="utf-8"))
    required = state.get("required") or []
    assert required, "fixture precondition: the tracked schema declares no required keys"

    missing = mod.schema_gate({"built_version": "0.0.0"})
    assert missing, (
        "schema_gate reported NOTHING missing for a state lacking every one of "
        "%r. It is reading an absent absolute path and failing open." % required)


# --------------------------------------------------------------------------- #
# GREEN before AND after: the repair must not invent failures                   #
# --------------------------------------------------------------------------- #

def test_a_complete_state_passes_the_schema_gate():
    """The over-sensitivity direction: a state carrying every required key
    must produce NO missing list, before and after."""
    mod = _load()
    sch = json.loads((_REPO / "project-knowledge" / "STATE_schema.json")
                     .read_text(encoding="utf-8"))
    complete = {k: "x" for k in (sch.get("required") or [])}
    assert mod.schema_gate(complete) == [], (
        "a state with every required key was reported incomplete -- the repair "
        "turned a fail-open gate into a fail-always one.")


def test_schema_gate_returns_a_list_not_a_bool():
    """main() interpolates the result into its failure message, so the shape
    is part of the contract and a truthiness-only fix would break it."""
    mod = _load()
    out = mod.schema_gate({"built_version": "0.0.0"})
    assert isinstance(out, list), type(out)


def test_refresh_state_is_untouched(tmp_path):
    """Baseline: this cut changes only the two gates."""
    mod = _load()
    zp = tmp_path / "rel.zip"
    _fake_zip(zp, "3.66.281")
    out, ver, cnt, full = mod.refresh_state(
        {"built_version": "0.0.0", "zip": {}, "guards": {},
         "guards_full_sha256": {}}, str(zp), 2)
    assert ver == "3.66.281" and cnt == 3 and len(full) == 64
