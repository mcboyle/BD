"""bd-precut selects repo-wide gates BY MARKER, not by its hand list.

THE DEFECT. Rows 46/99/133 shipped ``BD_GATE_SCOPE = "repo-wide"`` and made
CI's shards consume it (test_v3_66_939 forces every marked file into a shard).
Nothing BEFORE assembly consumed it: ``toolchain/bin/bd-precut --gate`` ran only
its hand-maintained ``_UNDERIVED_GATES`` (seven entries, grown one incident at
a time -- row 315 is the one its own comment records), and bd-collect-gate.sh
runs own-tests plus precut, so a repo-wide census gate outside that hand list
was invisible to the worker's floor, to collect and to prep, and first failed
at assembly. Twice: row713's bounce and cx-hostsafety (law 23:40:46Z).

MEASURED 2026-09-07 on origin/main a8267e5d, denominator
``git ls-files 'tests/*.py' | xargs grep -l 'BD_GATE_SCOPE = "repo-wide"'``:
208 marked repo-wide, 178 marked module, 7 hand-listed, 2 of those 7 unmarked,
0 occurrences of the marker in bd-precut.

WHAT IS PINNED HERE (the PM-B row's acceptance, clause by clause): a repo-wide
gate newly added in a fixture tree is selected with NO edit to
``_UNDERIVED_GATES``; a ``"module"`` gate is NOT selected, in the same test;
the selection reconciles to the marker census by count; a file the census
cannot classify is UNKNOWN by name, never a silent drop; the denominator is
the tracked tree, so an untracked marked file and a marker that only appears
in prose or inside a function select nothing; a census that cannot look is
COULD NOT LOOK, with the floor still running. The live-tree test at the end
reconciles the tool's selection against the row's own denominator command.
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parents[1]
PRECUT = REPO / "toolchain" / "bin" / "bd-precut"

#: Pinned HERE, independently of bd-precut (the same reason
#: test_v3_66_1239 pins it): the acceptance says the hand list is NOT edited,
#: and a test that read the list out of the tool could not tell.
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
MODULE_GATE = "tests/test_module_scoped_gate.py"
PROSE_ONLY = "tests/test_marker_in_prose_only.py"
NESTED_ONLY = "tests/test_marker_inside_a_function.py"
UNTRACKED = "tests/test_untracked_repo_wide.py"
UNPARSABLE = "tests/test_unparsable_marked.py"
ODD_SCOPE = "tests/test_odd_scope_value.py"
DANGLING = "tests/test_dangling_link.py"

_CENSUS = re.compile(
    r"\[repo-wide marker census\] tracked=(\d+) expected=(\d+) \(marked\) selected=(\d+) "
    r"\(repo-wide\) excluded=(\d+) \(module\) mentions-only=(\d+) unclassified=(\d+)")


def _load():
    loader = SourceFileLoader("bd_precut_marker_census", str(PRECUT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader, PRECUT
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(root), check=True,
                          capture_output=True, text=True).stdout


def _fixture_root(tmp_path: Path, *, git: bool = True, new_gate: bool = True,
                  unclassifiable: bool = False, mark_floor: bool = False) -> Path:
    root = tmp_path / "root"
    (root / "bulk_downloader").mkdir(parents=True)
    (root / "bulk_downloader" / "__init__.py").write_text('__version__ = "1.2.3"\n')
    (root / "tests").mkdir()
    # precut's release-trio check reads this file; it declares no scope marker.
    (root / "tests" / "test_settings_center_slice4.py").write_text(
        'assert __version__ == "1.2.3"\n')
    (root / "CHANGELOG.md").write_text("## v1.2.3\n\nrelease\n")
    (root / "PIN_INDEX.json").write_text('{"version": "1.2.3"}\n')
    (root / "tools").mkdir()
    (root / "tools" / "precut_check.py").write_text("raise SystemExit(9)\n")
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text("name: CI\ntimeout-minutes: 30\n")
    for rel in FLOOR:
        # mark_floor: the first hand-listed gate ALSO carries the marker, as five
        # of the seven do in the live tree -- the union must not run it twice.
        marker = 'BD_GATE_SCOPE = "repo-wide"\n\n\n' if mark_floor and rel == FLOOR[0] else ""
        (root / rel).write_text(marker + "def test_gate(): pass\n")
    if new_gate:
        (root / NEW_GATE).write_text(
            'BD_GATE_SCOPE = "repo-wide"\n\n\ndef test_gate(): pass\n')
    (root / MODULE_GATE).write_text(
        'BD_GATE_SCOPE = "module"\n\n\ndef test_gate(): pass\n')
    (root / PROSE_ONLY).write_text(
        '"""This docstring says BD_GATE_SCOPE = "repo-wide" and declares nothing."""\n'
        "\n\ndef test_gate(): pass\n")
    (root / NESTED_ONLY).write_text(
        'def test_gate():\n    BD_GATE_SCOPE = "repo-wide"\n    assert BD_GATE_SCOPE\n')
    if unclassifiable:
        (root / UNPARSABLE).write_text('BD_GATE_SCOPE = "repo-wide"\ndef broken(:\n')
        (root / ODD_SCOPE).write_text('BD_GATE_SCOPE = "tree"\n\n\ndef test_gate(): pass\n')
        os.symlink("no-such-target.py", root / DANGLING)
    if git:
        _git(root, "init", "-q")
        _git(root, "add", "-A")
    (root / UNTRACKED).write_text(
        'BD_GATE_SCOPE = "repo-wide"\n\n\ndef test_gate(): pass\n')
    return root


def _drive(monkeypatch, tmp_path: Path, capsys, root: Path, *, underived_rc: int = 0):
    """Drive bd-precut's own main() on root. git runs for real (the census's
    denominator is the checkout); every other subprocess is faked, so nothing
    here reaches a network or launches the fixture's tests."""
    mod = _load()
    baseline = tmp_path / "baseline.zip"
    baseline.write_bytes(b"nonempty baseline fixture")
    real_run = subprocess.run
    calls: list[list[str]] = []

    def selective_run(cmd, **kwargs):
        argv = [str(x) for x in cmd]
        if argv[:1] == ["git"]:
            return real_run(cmd, **kwargs)
        calls.append(argv)
        if "pytest" in argv:
            return subprocess.CompletedProcess(cmd, underived_rc, "", "")
        if any(x.endswith("bd-coretest") for x in argv):
            return subprocess.CompletedProcess(cmd, 0, "CORE TOOLS PASSING: 1/1\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(mod, "_auto_baseline", lambda _root: None)
    monkeypatch.setattr(mod, "_derive_baseline", lambda _r, _t: (str(baseline), None))
    monkeypatch.setattr(mod.subprocess, "run", selective_run)
    rc = mod.main(["--root", str(root), "--gate"])
    out = capsys.readouterr().out
    pytest_calls = [c for c in calls if "pytest" in c]
    assert len(pytest_calls) == 1, (
        "precondition: the underived gates run in exactly ONE pytest subprocess "
        "(test_verify_lane_state_naming pins the same count); got %d" % len(pytest_calls))
    assert not [c for c in calls if c[:1] == ["gh"] and "network" in c], calls
    return rc, out, pytest_calls[0]


def _hand_list_in_the_tool() -> set[str]:
    import ast

    tree = ast.parse(PRECUT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_UNDERIVED_GATES" for t in node.targets):
            return {elt.elts[0].value for elt in node.value.elts}
    raise AssertionError("bd-precut no longer declares _UNDERIVED_GATES")


def _counts(out: str) -> dict[str, int]:
    match = _CENSUS.search(out)
    assert match, "no census line in precut's output:\n" + out
    keys = ("tracked", "marked", "selected", "excluded", "mentions", "unclassified")
    return dict(zip(keys, (int(g) for g in match.groups())))


def test_a_new_repo_wide_gate_is_selected_and_a_module_gate_is_not(
        monkeypatch, tmp_path, capsys):
    """The acceptance's first two clauses, in one test as the row asks."""
    root = _fixture_root(tmp_path)
    rc, out, argv = _drive(monkeypatch, tmp_path, capsys, root)
    assert rc == 0, out
    assert _hand_list_in_the_tool() == set(FLOOR), (
        "the hand list changed; the acceptance is selection with NO edit to it")
    assert NEW_GATE in argv, (
        "a tracked file declaring BD_GATE_SCOPE = \"repo-wide\" was not handed to "
        "the underived run: %r" % argv)
    for rel in FLOOR:
        assert rel in argv, ("the pinned floor no longer runs: %s" % rel, argv)
    assert MODULE_GATE not in argv, ("a module-scoped gate was selected", argv)
    assert PROSE_ONLY not in argv, ("a marker in prose selected a gate", argv)
    assert NESTED_ONLY not in argv, ("a marker inside a function selected a gate", argv)
    assert UNTRACKED not in argv, ("an UNTRACKED file is outside the denominator", argv)
    assert "NOT CUT-READY" not in out, out


def test_a_floor_gate_that_also_carries_the_marker_runs_once(monkeypatch, tmp_path, capsys):
    """Five of the seven hand-listed gates carry the marker in the live tree. The
    union hands each file to pytest ONCE: a duplicate would inflate the "N gate
    file(s)" denominator in the failure detail and count one file as two."""
    root = _fixture_root(tmp_path, mark_floor=True)
    rc, out, argv = _drive(monkeypatch, tmp_path, capsys, root)
    assert rc == 0, out
    assert argv.count(FLOOR[0]) == 1, (
        "a floor gate that also carries the marker was handed to pytest %d times"
        % argv.count(FLOOR[0]), argv)
    assert NEW_GATE in argv, argv
    files = [a for a in argv if a.startswith("tests/")]
    assert len(files) == len(set(files)), ("a gate file appears twice", files)
    counts = _counts(out)
    assert counts["selected"] == 2, counts   # the census still COUNTS the marked floor file


def test_the_selection_reconciles_to_the_census_by_count(monkeypatch, tmp_path, capsys):
    """expected / selected / excluded, and the identity a reader can check."""
    root = _fixture_root(tmp_path)
    rc, out, argv = _drive(monkeypatch, tmp_path, capsys, root)
    counts = _counts(out)
    tracked = set(_git(root, "ls-files", "--", "tests/test*.py").split())
    assert counts["tracked"] == len(tracked) == len(FLOOR) + 5
    assert counts == {
        "tracked": len(FLOOR) + 5,   # floor, slice4, and the four marked files
        "marked": 4,        # NEW_GATE, MODULE_GATE, PROSE_ONLY, NESTED_ONLY
        "selected": 1,
        "excluded": 1,
        "mentions": 2,
        "unclassified": 0,
    }, counts
    assert counts["marked"] == (counts["selected"] + counts["excluded"]
                                + counts["mentions"] + counts["unclassified"])
    selected_in_run = [rel for rel in argv if rel.startswith("tests/") and rel not in FLOOR]
    assert selected_in_run == [NEW_GATE], selected_in_run
    assert len([rel for rel in argv if rel.startswith("tests/")]) == len(FLOOR) + counts["selected"]


def test_a_file_the_census_cannot_classify_is_UNKNOWN_by_name_not_dropped(
        monkeypatch, tmp_path, capsys):
    root = _fixture_root(tmp_path, unclassifiable=True)
    rc, out, argv = _drive(monkeypatch, tmp_path, capsys, root)
    assert rc == 0, out            # UNKNOWN is reported, not blocking (A5)
    counts = _counts(out)
    assert counts["unclassified"] == 3, counts
    assert counts["selected"] == 1 and NEW_GATE in argv, (counts, argv)
    assert "NOT RUN, so UNKNOWN, not OK" in out, out
    line = next((ln for ln in out.splitlines()
                 if "could not be classified" in ln), None)
    assert line, out
    for rel, why in ((UNPARSABLE, "unparsable"), (ODD_SCOPE, "neither"),
                     (DANGLING, "unreadable")):
        assert rel in line and why in line, (rel, why, line)
        assert rel not in argv, (rel, argv)


def test_a_tree_with_no_repo_wide_marker_is_UNKNOWN_not_clean(monkeypatch, tmp_path, capsys):
    root = _fixture_root(tmp_path, new_gate=False)
    rc, out, argv = _drive(monkeypatch, tmp_path, capsys, root)
    assert rc == 0, out
    assert _counts(out)["selected"] == 0
    assert "selected NO gate" in out and "UNKNOWN, not clean" in out, out
    assert [rel for rel in argv if rel.startswith("tests/")] == list(FLOOR), argv


def test_a_census_that_cannot_look_says_so_and_the_floor_still_runs(
        monkeypatch, tmp_path, capsys):
    root = _fixture_root(tmp_path, git=False)
    rc, out, argv = _drive(monkeypatch, tmp_path, capsys, root)
    assert rc == 0, out
    assert "repo-wide marker census COULD NOT LOOK" in out, out
    assert "[repo-wide marker census]" not in out, (
        "a census that could not look must not print counts", out)
    assert NEW_GATE not in argv, argv
    assert [rel for rel in argv if rel.startswith("tests/")] == list(FLOOR), argv


def test_a_failing_marker_selected_gate_is_BLOCKING_like_the_floor(
        monkeypatch, tmp_path, capsys):
    root = _fixture_root(tmp_path)
    rc, out, argv = _drive(monkeypatch, tmp_path, capsys, root, underived_rc=1)
    assert rc == 3, out
    assert "underived gate(s) FAILED" in out and "NOT CUT-READY" in out, out
    assert NEW_GATE in argv, argv


def test_the_underived_run_keeps_every_file_whole_in_one_worker(monkeypatch, tmp_path, capsys):
    """xdist is used for wall time only; loadfile is the shard shape CI runs."""
    root = _fixture_root(tmp_path)
    mod = _load()
    assert importlib.util.find_spec("xdist") is not None, (
        "precondition: this interpreter carries pytest-xdist")
    rc, out, argv = _drive(monkeypatch, tmp_path, capsys, root)
    assert "-n" in argv and "--dist" in argv, argv
    assert argv[argv.index("--dist") + 1] == "loadfile", argv
    workers = int(argv[argv.index("-n") + 1])
    assert 1 <= workers <= mod._UNDERIVED_WORKERS_CAP, workers
    assert "--timeout=240" in argv and "-p" in argv, argv


def test_the_live_tree_selection_matches_the_rows_own_denominator():
    """The reconciliation on THIS tree, against the denominator the row names.

    The row's command is a grep; the tool reads the AST at module scope. They
    agree on every tracked file today, and this test names the first file on
    which they stop agreeing rather than letting the count drift silently.
    """
    mod = _load()
    census = mod._repo_wide_marker_census(str(REPO))
    assert census["error"] == "", census["error"]
    assert census["unclassified"] == [], census["unclassified"]
    tracked = subprocess.run(["git", "ls-files", "--", "tests/test*.py"], cwd=str(REPO),
                             capture_output=True, text=True, check=True).stdout.split()
    line = re.compile(r'^BD_GATE_SCOPE\s*=\s*"repo-wide"\s*(#.*)?$', re.M)
    expected = {rel for rel in tracked
                if line.search((REPO / rel).read_text(encoding="utf-8"))}
    selected = set(census["selected"])
    assert census["tracked"] == len(tracked) and len(tracked) > 100
    assert "tests/test_v3_66_1239_precut_runs_the_underived_gates.py" in selected, (
        "positive control: a known repo-wide gate must be selected")
    assert Path(__file__).relative_to(REPO).as_posix() in selected, (
        "this file declares repo-wide and must select itself")
    assert selected == expected, {
        "grep-only": sorted(expected - selected),
        "ast-only": sorted(selected - expected),
    }
    assert census["marked"] == (len(selected) + census["excluded"]
                                + census["mentions"] + len(census["unclassified"]))
