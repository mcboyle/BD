"""bd-precut names missing/errored footguns & ratchet as UNKNOWN, and its
cut-ready verdict's ``ran`` string enumerates only what actually ran
(rows 790 and 794, ONE contract per the brief -- built here as one cut).

THE DEFECT (measured on origin/main 900c08fc). ``toolchain/bin/bd-precut``'s
main() treats the two tree-wide checks asymmetrically and both wrong:
  * bd-footguns absent -> prints "SKIPPED" but never touches ``unknown[]``.
  * bd-ratchet absent -> no branch at all: nothing printed, nothing appended.
  * Present but exiting nonzero-and-not-3 is silently swallowed for BOTH: only
    ``rc == 3`` is blocking, so a crash (rc=1) or a timeout is neither a
    finding nor an UNKNOWN -- it disappears.
  * Line 794 hardcodes ``ran = "footguns clean, metric ratchets clean"``
    regardless of whether either check ran at all, so a tree with both
    checks absent still prints "RESULT: cut-ready for what RAN (footguns
    clean, metric ratchets clean))" -- a literal claim about work that
    never executed.

ACCEPTANCE (row790's own backlog text): "missing/either nonzero-not3 ->
named UNKNOWN, cut-ready-for-what-RAN verdict". Pinned here: absence of
either tool is named UNKNOWN; a present tool exiting nonzero-not-3 is named
UNKNOWN (not silently OK, not blocking); the ``ran`` string names only the
checks that actually completed rc==0; and the negative control -- both
present, both clean, everything else in the fixture clean too -- produces
the plain, unmodified "RESULT: cut-ready" line with no footguns/ratchet
UNKNOWN entries at all.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parents[1]
PRECUT = REPO / "toolchain" / "bin" / "bd-precut"

FLOOR = (
    "tests/test_v3_66_1184_mutation_specs_are_tracked.py",
    "tests/test_row357_mutant_anchors_are_not_fragile.py",
    "tests/test_row473_register_tree_containment.py",
    "tests/test_v3_66_1222_every_budget_is_subordinate_to_its_bound.py",
    "tests/test_v3_66_1197_ambient_locale_into_subprocess.py",
    "tests/test_import_graph_no_new_edges.py",
    "tests/test_v3_66_1034_guards_survive_a_module_wipe.py",
)
NEW_GATE = "tests/test_new_census_gate.py"

# The UNKNOWN BULLET each absent tool must contribute. This is deliberately the
# unknown[] entry's own text and NOT the pre-existing skip line
# ("  [bd-footguns not found -- footgun registry SKIPPED]"), which is printed
# unconditionally at the call site and is therefore satisfied even when the
# append is deleted. Lens A1-A's L1/L2 escaped exactly there: asserting the two
# substrings "footgun registry" and "not found" separately is satisfied by the
# skip line alone, so SEAM 1/2's absent half was never pinned. These constants
# appear in the skip line's output NOWHERE as a contiguous substring.
FOOTGUN_ABSENT_BULLET = "footgun registry (bd-footguns not found)"
RATCHET_ABSENT_BULLET = "metric ratchet (bd-ratchet not found)"
UNKNOWN_HEADER = "NOT RUN, so UNKNOWN, not OK:"


def _bullets(out: str) -> str:
    """The UNKNOWN bullet list -- everything AFTER the header. Callers assert
    the header's presence first, so this never silently returns all of `out`."""
    assert UNKNOWN_HEADER in out, out
    return out.split(UNKNOWN_HEADER, 1)[1]


def _load():
    loader = SourceFileLoader("bd_precut_row790", str(PRECUT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader, PRECUT
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(root), check=True,
                          capture_output=True, text=True).stdout


def _fixture_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    (root / "bulk_downloader").mkdir(parents=True)
    (root / "bulk_downloader" / "__init__.py").write_text('__version__ = "1.2.3"\n')
    (root / "tests").mkdir()
    (root / "tests" / "test_settings_center_slice4.py").write_text(
        'assert __version__ == "1.2.3"\n')
    (root / "CHANGELOG.md").write_text("## v1.2.3\n\nrelease\n")
    (root / "PIN_INDEX.json").write_text('{"version": "1.2.3"}\n')
    (root / "tools").mkdir()
    (root / "tools" / "precut_check.py").write_text("raise SystemExit(9)\n")
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text("name: CI\ntimeout-minutes: 30\n")
    for rel in FLOOR:
        (root / rel).write_text("def test_gate(): pass\n")
    (root / NEW_GATE).write_text(
        'BD_GATE_SCOPE = "repo-wide"\n\n\ndef test_gate(): pass\n')
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    return root


def _drive(monkeypatch, tmp_path, capsys, root, *, footguns_present=True,
           footguns_rc=0, ratchet_present=True, ratchet_rc=0,
           underived_rc=0):
    """Drive bd-precut's own main() with footguns/ratchet presence and exit
    code faked (they live beside the REAL bd-precut, not the fixture root,
    so a name-scoped isfile() wrapper is the only way to control them), and
    every other subprocess call stubbed except git."""
    mod = _load()
    baseline = tmp_path / "baseline.zip"
    baseline.write_bytes(b"nonempty baseline fixture")
    real_run = subprocess.run
    real_isfile = mod.os.path.isfile
    calls: list[list[str]] = []

    def fake_isfile(path):
        base = mod.os.path.basename(str(path))
        if base == "bd-footguns":
            return footguns_present
        if base == "bd-ratchet":
            return ratchet_present
        return real_isfile(path)

    def selective_run(cmd, **kwargs):
        argv = [str(x) for x in cmd]
        if argv[:1] == ["git"]:
            return real_run(cmd, **kwargs)
        calls.append(argv)
        if any(x.endswith("bd-footguns") for x in argv):
            return subprocess.CompletedProcess(cmd, footguns_rc, "", "")
        if any(x.endswith("bd-ratchet") for x in argv):
            return subprocess.CompletedProcess(cmd, ratchet_rc, "", "")
        if "pytest" in argv:
            return subprocess.CompletedProcess(cmd, underived_rc, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(mod, "_auto_baseline", lambda _root: None)
    monkeypatch.setattr(mod, "_derive_baseline", lambda _r, _t: (str(baseline), None))
    monkeypatch.setattr(mod.subprocess, "run", selective_run)
    monkeypatch.setattr(mod.os.path, "isfile", fake_isfile)
    rc = mod.main(["--root", str(root), "--gate", "--no-coretest"])
    out = capsys.readouterr().out
    return rc, out, calls


def test_missing_footguns_is_named_unknown_not_silently_dropped(monkeypatch, tmp_path, capsys):
    root = _fixture_root(tmp_path)
    rc, out, _ = _drive(monkeypatch, tmp_path, capsys, root, footguns_present=False)
    assert rc == 0, out   # an absent check is UNKNOWN, not a blocking finding
    assert "footgun registry" in out and "not found" in out, out
    assert "NOT RUN, so UNKNOWN, not OK" in out, out
    assert "footguns clean" not in out.split("RESULT:")[-1].split("\n")[0], (
        "the ran string claims footguns ran when the tool was absent", out)
    # SEAM 1 absent half, pinned on the unknown[] ENTRY rather than on substrings
    # the unconditional skip line already satisfies (lens A1-A escape L1).
    assert out.count(FOOTGUN_ABSENT_BULLET) == 1, (
        "the absent footgun registry must contribute exactly one UNKNOWN bullet; "
        "the skip line alone is not the acceptance", out)
    assert FOOTGUN_ABSENT_BULLET in _bullets(out), (
        "the bullet must be listed AFTER the UNKNOWN header, i.e. come from "
        "unknown[], not from the skip line printed at the call site", out)


def test_missing_ratchet_is_named_unknown_not_silently_dropped(monkeypatch, tmp_path, capsys):
    """Ratchet had NO missing-branch at all before this cut: nothing printed,
    nothing appended. This is the sharper half of the defect."""
    root = _fixture_root(tmp_path)
    rc, out, _ = _drive(monkeypatch, tmp_path, capsys, root, ratchet_present=False)
    assert rc == 0, out
    assert "metric ratchet" in out and "not found" in out, out
    assert "NOT RUN, so UNKNOWN, not OK" in out, out
    assert "metric ratchets clean" not in out.split("RESULT:")[-1].split("\n")[0], (
        "the ran string claims the ratchet ran when the tool was absent", out)
    # SEAM 2 absent half (lens A1-A escape L2).
    assert out.count(RATCHET_ABSENT_BULLET) == 1, (
        "the absent metric ratchet must contribute exactly one UNKNOWN bullet; "
        "the skip line alone is not the acceptance", out)
    assert RATCHET_ABSENT_BULLET in _bullets(out), (
        "the bullet must be listed AFTER the UNKNOWN header", out)


def test_footguns_nonzero_not_3_is_unknown_not_swallowed(monkeypatch, tmp_path, capsys):
    """rc=1 (a crash) is neither a violation (rc==3) nor clean (rc==0); the
    defective tool treated it as clean by omission."""
    root = _fixture_root(tmp_path)
    rc, out, _ = _drive(monkeypatch, tmp_path, capsys, root, footguns_rc=1)
    assert rc == 0, out
    assert "NOT CUT-READY" not in out, out   # rc=1 is not a violation
    assert "footgun registry exited rc=1" in out, out
    assert "footguns clean" not in out.split("RESULT:")[-1].split("\n")[0], out


def test_ratchet_nonzero_not_3_is_unknown_not_swallowed(monkeypatch, tmp_path, capsys):
    root = _fixture_root(tmp_path)
    rc, out, _ = _drive(monkeypatch, tmp_path, capsys, root, ratchet_rc=2)
    assert rc == 0, out
    assert "NOT CUT-READY" not in out, out
    assert "metric ratchet exited rc=2" in out, out
    assert "metric ratchets clean" not in out.split("RESULT:")[-1].split("\n")[0], out


def test_footgun_violation_rc3_still_blocks(monkeypatch, tmp_path, capsys):
    """Unchanged behaviour: rc==3 is still a blocking violation, never UNKNOWN."""
    root = _fixture_root(tmp_path)
    rc, out, _ = _drive(monkeypatch, tmp_path, capsys, root, footguns_rc=3)
    assert rc == 3, out
    assert "NOT CUT-READY" in out and "footgun violation" in out, out
    assert "footgun registry exited" not in out, out


def test_both_present_and_clean_names_both_in_the_ran_string(monkeypatch, tmp_path, capsys):
    """NEGATIVE CONTROL: both tools present and rc==0 -> the ran string names
    both as clean, and NEITHER contributes an UNKNOWN bullet -- the two
    UNKNOWN bullets that remain (--no-coretest, CI shard headroom) are
    unrelated fixture facts this row does not touch."""
    root = _fixture_root(tmp_path)
    rc, out, calls = _drive(monkeypatch, tmp_path, capsys, root)
    assert rc == 0, out
    pytest_calls = [c for c in calls if "pytest" in c]
    assert len(pytest_calls) == 1, calls   # precondition: the floor+census ran once
    assert "footgun registry" not in out, out
    assert "metric ratchet exited" not in out, out
    assert "not found" not in out, out
    assert "for what RAN (footguns clean, metric ratchets clean)" in out, out
    bullets = out.split("not OK:", 1)[-1]
    assert "footgun" not in bullets and "ratchet" not in bullets, bullets


def test_both_tools_absent_makes_the_ran_string_read_nothing(monkeypatch, tmp_path, capsys):
    """SEAM 3's terminal case, and lens A1-A's escape L8.

    The ``ran`` string is ``", ".join(ran_parts) if ran_parts else "nothing"``.
    Every other test in this file leaves at least ONE detector running, so
    ``ran_parts`` is never empty and the ``else`` arm is never evaluated -- which
    is why replacing ``"nothing"`` with the old hardcoded claim
    ``"footguns clean, metric ratchets clean"`` left the whole band green. This
    test is the only one that reaches that arm: with BOTH tools absent, nothing
    ran, and the verdict must say so in those words.
    """
    root = _fixture_root(tmp_path)
    rc, out, _ = _drive(monkeypatch, tmp_path, capsys, root,
                        footguns_present=False, ratchet_present=False)
    assert rc == 0, out   # two absent checks are UNKNOWN, not a blocking finding
    verdict = out.split("RESULT:")[-1].split("\n")[0]
    assert "for what RAN (nothing)" in verdict, (
        "with both detectors absent the verdict must report that NOTHING ran", verdict)
    # The specific false claim row 794 exists to kill must not reappear, in whole
    # or in half, on a tree where neither detector executed.
    assert "footguns clean" not in verdict, verdict
    assert "metric ratchets clean" not in verdict, verdict
    # ...and both absences are named, once each, as UNKNOWN bullets.
    bullets = _bullets(out)
    assert FOOTGUN_ABSENT_BULLET in bullets, bullets
    assert RATCHET_ABSENT_BULLET in bullets, bullets
    assert out.count(FOOTGUN_ABSENT_BULLET) == 1, out
    assert out.count(RATCHET_ABSENT_BULLET) == 1, out


def test_both_tools_errored_also_reads_nothing_and_names_both(monkeypatch, tmp_path, capsys):
    """The second route to an empty ``ran_parts``: both tools PRESENT but exiting
    nonzero-and-not-3. Present-but-crashed is not "clean", so it must not appear
    in the ran string either -- a tool that ran and failed to produce a verdict
    did not verify anything."""
    root = _fixture_root(tmp_path)
    rc, out, _ = _drive(monkeypatch, tmp_path, capsys, root,
                        footguns_rc=1, ratchet_rc=2)
    assert rc == 0, out
    verdict = out.split("RESULT:")[-1].split("\n")[0]
    assert "for what RAN (nothing)" in verdict, verdict
    assert "clean" not in verdict, verdict
    bullets = _bullets(out)
    assert "footgun registry exited rc=1" in bullets, bullets
    assert "metric ratchet exited rc=2" in bullets, bullets


def test_one_tool_absent_names_only_the_other__nothing_control(monkeypatch, tmp_path, capsys):
    """NEGATIVE CONTROL for the two tests above: "nothing" must be the reading of
    an EMPTY ran_parts, not a constant the verdict prints whenever anything is
    UNKNOWN. With footguns absent but the ratchet present and clean, exactly one
    detector ran, so the verdict must name that one and must NOT read "nothing".

    Without this control, ``else "nothing"`` mutated into an unconditional
    ``"nothing"`` would satisfy both tests above -- the assertions could not say
    no, which is the failure mode that produced escape L8 in the first place.
    """
    root = _fixture_root(tmp_path)
    rc, out, _ = _drive(monkeypatch, tmp_path, capsys, root, footguns_present=False)
    assert rc == 0, out
    verdict = out.split("RESULT:")[-1].split("\n")[0]
    assert "for what RAN (metric ratchets clean)" in verdict, verdict
    assert "nothing" not in verdict, (
        "one detector DID run; 'nothing' here would make the both-absent "
        "assertion vacuous", verdict)
    assert "footguns clean" not in verdict, verdict
