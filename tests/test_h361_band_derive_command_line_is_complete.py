"""H361 -- the `bd-band ...` line bd-band-derive prints is a COMMAND, not a label.

WHAT WENT WRONG (bd-review-correctness-B2-B, 2026-09-14T07:1xZ, HIGH). The
human-readable report ended with

    print("  %s" % emit_band(band)[:160])

so for a band of 90 suites the line that reads as "the command to run" stopped
after 162 characters, mid-path, at `tests/test_ca`. There was no ellipsis, no
marker, and no count anywhere in the line to reconcile against the `band (90)`
header printed four lines above it. A reader who pasted it ran 3 of the 90
band files and got a clean `3 passed`: the partial token is not a file, so
pytest's own "file or directory not found" never fired either. A band is a
denominator (FLEET_RULE 8) and a truncated denominator that still exits 0 is
UNKNOWN reported as permission (FLEET_RULE 6) -- silent, and GREEN.

WHAT IS ASSERTED HERE is the finding's acceptance, A1-A4:

  A1  a band over the display cap prints a runnable line naming EVERY member;
  A2  NEGATIVE CONTROL -- a band under the cap prints exactly its members and
      no truncation marker, so A1 cannot pass by the tool shouting "truncated"
      at every band it ever prints;
  A3  no token on that line is a partial path: EVERY argument the shell would
      receive exists on disk, including one that no longer ends in ".py";
  A4  `--json`'s `band` and the runnable line name the SAME SET, so the two
      published denominators cannot drift apart.

The band is built from a SYNTHETIC work tree whose suites are real files, so
the sizes here are the subject of the assertion rather than a property of
whatever the repository happens to contain today. 90 is the size B2-B measured
when the line was cut to 3.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path

# "module": this gate's subject is ONE file, toolchain/bin/bd-band-derive, not the
# tree. It loads that tool by path and asserts what it prints. "repo-wide" would be
# a false claim here -- that value means the gate holds over whatever the diff
# touched, and it obliges the same cut to add the file to _DECLARED in
# tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py and to a CI gate-suites
# shard. Required by test_v3_66_939's
# test_every_tracked_test_file_is_classified_or_baselined, which admits no third
# option: the baseline list is frozen legacy and may only shrink.
BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parents[1]
_TOOL = _REPO / "toolchain" / "bin" / "bd-band-derive"

# The report caps its INDENTED list at 24 entries; these sit either side of it.
_OVER_CAP = 90
_UNDER_CAP = 3
_CHANGED = "bulk_downloader/aiassist.py"


def _load():
    """Import the extensionless tool as a fresh module object."""
    loader = importlib.machinery.SourceFileLoader("_bd_band_derive_h361", str(_TOOL))
    spec = importlib.util.spec_from_loader("_bd_band_derive_h361", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _work(tmp_path: Path, n: int):
    """A work tree holding `n` real suite files, and the rows that map to them."""
    root = tmp_path / "work"
    (root / "tests").mkdir(parents=True, exist_ok=True)
    names = []
    for i in range(n):
        name = "test_h361_band_member_%03d.py" % i
        (root / "tests" / name).write_text("# present\n", encoding="utf-8")
        names.append(name)
    rows = [(_CHANGED, names)]
    return str(root), rows, sorted("tests/" + n for n in names)


def _report(mod, work, rows, as_json=False):
    return mod.report(work, [_CHANGED], rows, "h361-probe", as_json=as_json)


def _runnable_line(out: str, rc=None) -> str:
    """The one printed line a reader would paste into a shell.

    `rc` is carried into the diagnostic only, never asserted on: report()
    returns 2 whenever the import-contract resolver cannot resolve a changed
    path, and the synthetic work tree these tests build has no bulk_downloader/
    in it at all, so 2 is the correct status FOR THIS INPUT and says nothing
    about the line. The contract under test is the printed command itself.
    """
    lines = [ln for ln in out.splitlines() if ln.strip().startswith("bd-band ")]
    assert len(lines) == 1, (
        "the report must print exactly one runnable bd-band line; it printed "
        "%d: %r (the command exited %r)" % (len(lines), lines, rc))
    return lines[0]


def _suites_on(line: str):
    return [tok for tok in line.strip().split(" ") if tok.endswith(".py")]


def test_the_printed_command_names_every_file_in_the_band(tmp_path, capsys):
    """A1 -- a band of 90 prints 90, not the first 160 characters of 90."""
    mod = _load()
    work, rows, expected = _work(tmp_path, _OVER_CAP)
    _report(mod, work, rows)
    out = capsys.readouterr().out
    assert "band (%d)" % _OVER_CAP in out, (
        "precondition: the synthetic tree must produce a band of %d; the "
        "report said:\n%s" % (_OVER_CAP, out))
    line = _runnable_line(out)
    named = _suites_on(line)
    assert sorted(named) == expected, (
        "the runnable line names %d of the %d suites in the band. A command "
        "is not a display string: whoever pastes this runs the subset and "
        "reads GREEN. Missing: %s"
        % (len(named), _OVER_CAP, sorted(set(expected) - set(named))[:5]))


def test_a_band_under_the_cap_prints_itself_and_claims_no_truncation(tmp_path, capsys):
    """A2, the negative control.

    Without this, a tool that printed "# TRUNCATED" over every band -- or one
    that named some fixed 90 files whatever the band was -- would satisfy A1.
    """
    mod = _load()
    work, rows, expected = _work(tmp_path, _UNDER_CAP)
    _report(mod, work, rows)
    out = capsys.readouterr().out
    line = _runnable_line(out)
    assert sorted(_suites_on(line)) == expected, (
        "a band of %d must print exactly those %d: %r" % (_UNDER_CAP, _UNDER_CAP, line))
    assert "TRUNCATED" not in out, (
        "nothing was elided for a band of %d, so no truncation may be "
        "announced: %r" % (_UNDER_CAP, out))


def test_no_token_on_the_printed_command_is_a_partial_path(tmp_path, capsys):
    """A3 -- the defect's signature was `tests/test_ca`, a path that is not a
    file, which is why pytest never complained about it."""
    mod = _load()
    work, rows, _expected = _work(tmp_path, _OVER_CAP)
    _report(mod, work, rows)
    line = _runnable_line(capsys.readouterr().out)
    # EVERY token the shell would see, not only the ones that still end in
    # ".py": the defect's `tests/test_ca` does not, which is exactly why an
    # extraction filtered on the suffix reported a clean subset and missed it.
    argv = line.strip().split(" ")[1:]
    missing = [tok for tok in argv
               if tok and not os.path.exists(os.path.join(work, tok))]
    assert missing == [], (
        "the runnable line carries token(s) that are not files -- a truncated "
        "path reads as a suite and runs as nothing: %s" % missing)


def test_the_json_band_and_the_printed_command_name_the_same_set(tmp_path, capsys):
    """A4 -- two published denominators that can disagree are the defect."""
    mod = _load()
    work, rows, _expected = _work(tmp_path, _OVER_CAP)
    _report(mod, work, rows)
    line = _runnable_line(capsys.readouterr().out)
    _report(mod, work, rows, as_json=True)
    payload = json.loads(capsys.readouterr().out)
    assert set(_suites_on(line)) == set(payload["band"]), {
        "printed-only": sorted(set(_suites_on(line)) - set(payload["band"]))[:5],
        "json-only": sorted(set(payload["band"]) - set(_suites_on(line)))[:5],
    }
    assert set(_suites_on(payload["band_cmd"])) == set(payload["band"]), (
        "--json's own band_cmd disagrees with its band")


# ---------------------------------------------------------------------------
# A5 -- THE SAME CONTRACT, DRIVEN THROUGH THE COMMAND LINE.
#
# WHY THIS EXISTS (bd-pm-B, O504, 2026-09-14T14:25Z). The four tests above load
# the tool and call `mod.report(...)` directly. That leaves the tool's OWN
# dispatch unexercised: main() reaches report() at exactly three call sites --
# the --file branch, the --files branch and the zip-diff branch -- and an early
# return planted at any of them escapes every assertion above, because report()
# is still perfectly correct and simply never runs. The prep's self-mutation
# gate measured that escape (`early-return report@toolchain/bin/bd-band-derive`
# at :1609, :1612, :1619, 3 escaped) and it is a real hole: the defect this row
# is about is a truncated line a HUMAN PASTES, and a human gets that line by
# running the command, not by importing the module.
#
# So each test below drives argv through main() and then asserts the SAME A1
# contract on what the command actually printed. An early return at that
# branch's call site prints no runnable line at all, and _runnable_line fails
# naming the count it found.
#
# The curated map is the one input a synthetic tree cannot supply (find_map
# looks for a repository file), so map_rows is stubbed to hand back the rows
# _work() already built. That stubs an INPUT SIGNAL, not the subject: the
# dispatch, report() and the printing are all the real ones.


def _cli(mod, monkeypatch, capsys, rows, argv):
    """Run the tool's real command line; return (rc, stdout)."""
    monkeypatch.setattr(mod, "map_rows", lambda _path: rows)
    monkeypatch.setattr(mod.sys, "argv", ["bd-band-derive"] + list(argv))
    rc = mod.main()
    return rc, capsys.readouterr().out


def test_the_file_branch_of_the_command_line_prints_the_whole_band(tmp_path, monkeypatch, capsys):
    """A5a -- `bd-band-derive --work W --file F` (the dispatch at :1609)."""
    mod = _load()
    work, rows, expected = _work(tmp_path, _OVER_CAP)
    rc, out = _cli(mod, monkeypatch, capsys, rows, ["--work", work, "--file", _CHANGED])
    named = sorted(_suites_on(_runnable_line(out, rc)))
    assert named == expected, (
        "run as a COMMAND, the --file branch named %d of the %d suites in the "
        "band. Missing: %s"
        % (len(named), _OVER_CAP, sorted(set(expected) - set(named))[:5]))


def test_the_files_branch_of_the_command_line_prints_the_whole_band(tmp_path, monkeypatch, capsys):
    """A5b -- `bd-band-derive --work W --files F ...` (the dispatch at :1612)."""
    mod = _load()
    work, rows, expected = _work(tmp_path, _OVER_CAP)
    rc, out = _cli(mod, monkeypatch, capsys, rows, ["--work", work, "--files", _CHANGED])
    named = sorted(_suites_on(_runnable_line(out, rc)))
    assert named == expected, (
        "run as a COMMAND, the --files branch named %d of the %d suites in the "
        "band. Missing: %s"
        % (len(named), _OVER_CAP, sorted(set(expected) - set(named))[:5]))


def test_the_zip_diff_branch_of_the_command_line_prints_the_whole_band(tmp_path, monkeypatch, capsys):
    """A5c -- `bd-band-derive --work W` with no selector (the dispatch at :1619).

    This is the branch a reader hits by typing the bare command, so it is the
    one most likely to produce the line that gets pasted. The baseline zip and
    the tree diff are the two inputs a synthetic tree cannot supply; both are
    stubbed, and everything after them is real.
    """
    mod = _load()
    work, rows, expected = _work(tmp_path, _OVER_CAP)
    zip_path = tmp_path / "baseline-h361.zip"
    zip_path.write_bytes(b"")
    monkeypatch.setattr(mod, "auto_zip", lambda: str(zip_path))
    monkeypatch.setattr(mod, "diff_tree", lambda _w, _z: ([_CHANGED], None))
    rc, out = _cli(mod, monkeypatch, capsys, rows, ["--work", work])
    named = sorted(_suites_on(_runnable_line(out, rc)))
    assert named == expected, (
        "run as a COMMAND with no selector, the zip-diff branch named %d of "
        "the %d suites in the band. Missing: %s"
        % (len(named), _OVER_CAP, sorted(set(expected) - set(named))[:5]))
