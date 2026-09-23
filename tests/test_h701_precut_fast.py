"""H712 (O1281; first cut H701, O1264c): ``bd-precut --fast`` is EXACTLY the fast gates.

POLICY-0010 s5 / RULING-0044 name them: guard pins (bd-guardcheck), env tranche
(bd-envscan), secret scan (bd-secrets), the defect ratchet (bd-ratchet) and register/trio
(bd-shipped's register containment, then the release-trio read). No pytest, no
regeneration; the exit status is the rc of the FIRST gate that failed, and a gate that
could not run is named UNKNOWN -- never counted as a pass, never a block (the full gate's
rule for a check that did not run).

The tests drive the real ``main()`` with ``subprocess.run`` replaced by a recorder and
every other way to start a process made to fail loudly, so each assertion is about the
processes the gate starts (their exact argv), what it writes and what it returns -- not
about the tool's source text. The last tests pin the other half: without --fast (or with
--full over BD_PRECUT_FAST=1) the full gate is the one a bd-precut without --fast runs --
the same processes in the same order and the same RAN line (T66h: a secret scan H701 had
added to the full gate reddened test_row790's ran-string and test_row794's fakes).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

BD_GATE_SCOPE = "repo-wide"
REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "toolchain" / "bin" / "bd-precut"
BIN = os.path.realpath(TOOL.parent)
VERSION = "3.66.1634"
FAST_GATES = ["bd-guardcheck", "bd-envscan", "bd-secrets", "bd-ratchet", "bd-shipped"]
# The regenerators, and bd-footguns, whose in-sync detectors run bd-regen-order --check.
REGENERATORS = {"bd-regen-order", "bd-regen", "bd-footguns", "bd-kb-sync", "bd-imports"}
RATCHET_ROSE = "  * defect_DP_total: rose 1399 -> 1620\n"


def _load(tool: Path = TOOL):
    loader = SourceFileLoader("bd_" + tool.name.replace("-", "_") + "_h712", str(tool))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tree(root: Path, changelog_version: str = VERSION) -> Path:
    """A source tree whose release trio agrees, beside a stale generated index: a gate
    that regenerated anything would change a byte of it."""
    (root / "bulk_downloader").mkdir(parents=True)
    (root / "bulk_downloader" / "__init__.py").write_text(f'__version__ = "{VERSION}"\n')
    (root / "tests").mkdir()
    (root / "tests" / "test_settings_center_slice4.py").write_text(f'assert __version__ == "{VERSION}"\n')
    (root / "CHANGELOG.md").write_text(f"## v{changelog_version}\n\nrelease\n")
    (root / "PIN_INDEX.json").write_text(f'{{"version": "{VERSION}"}}\n')
    (root / "FUNCTION_INDEX.md").write_text("# stale on purpose\n")
    for gate in ("test_row473_register_tree_containment.py", "test_v3_66_1184_mutation_specs_are_tracked.py"):
        (root / "tests" / gate).write_text("def test_x():\n    pass\n")
    return root


def _snapshot(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _tool(argv: list[str]) -> str:
    if argv[1:2] == ["-m"]:
        return argv[2]                     # `python -m pytest ...` -> "pytest"
    if os.path.basename(argv[0]).startswith("python") and len(argv) > 1:
        return os.path.basename(argv[1])   # `python <toolchain>/bd-x ...` -> "bd-x"
    return os.path.basename(argv[0])


def _runs_tests(argv: list[str]) -> bool:
    names = [os.path.basename(a) for a in argv]
    return (bool({"pytest", "py.test", "run_tests.py"} & set(names))
            or any(n.startswith("test_") and n.endswith(".py") for n in names))


class _Recorder:
    """subprocess.run stand-in: records every argv; each tool answers with its rc."""

    def __init__(self, rcs, raises, ratchet_out):
        self.calls: list[list[str]] = []
        self.rcs, self.raises, self.ratchet_out = rcs, raises, ratchet_out

    def __call__(self, cmd, *args, **kwargs):
        argv = [str(c) for c in cmd]
        self.calls.append(argv)
        tool = _tool(argv)
        if tool in self.raises:
            raise self.raises[tool]
        out = self.ratchet_out if tool == "bd-ratchet" else ""
        return subprocess.CompletedProcess(cmd, self.rcs.get(tool, 0), out, "")

    def tools(self) -> list[str]:
        return [_tool(argv) for argv in self.calls]


def _result(out: str) -> tuple[str, list[str]]:
    """(what RESULT counts as RAN, the NOT RUN items) of a --fast run that failed no gate.
    The count it prints must equal the items it names: nothing UNKNOWN goes unnamed."""
    m = re.search(r"^RESULT: cut-ready for what RAN \((.*)\) -- (\d+) check\(s\) NOT RUN, "
                  r"so UNKNOWN, not OK:\n((?:  - .*\n)*)", out, re.M)
    assert m, out
    items = [line[len("  - "):] for line in m.group(3).splitlines()]
    assert int(m.group(2)) == len(items), out
    return m.group(1), items


def _drive(monkeypatch, capsys, root: Path, argv: list[str], *, env_fast=None, block=None,
           rcs=None, raises=None, ratchet_out="", absent=()):
    module = _load()
    rec = _Recorder(rcs or {}, raises or {}, ratchet_out)
    monkeypatch.setattr(module.subprocess, "run", rec)
    forbidden: list[str] = []

    def _forbidden(name):
        def _refuse(*a, **k):
            forbidden.append(f"{name}{a[:1]!r}")
            raise AssertionError(f"bd-precut started a process through {name}: {a[:1]!r}")
        return _refuse

    for name in ("Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(module.subprocess, name, _forbidden("subprocess." + name))
    monkeypatch.setattr(module.os, "system", _forbidden("os.system"))
    monkeypatch.setattr(pytest, "main", _forbidden("pytest.main"))
    if absent:
        real_isfile = os.path.isfile
        monkeypatch.setattr(module.os.path, "isfile",
                            lambda p: False if os.path.basename(str(p)) in absent else real_isfile(p))
    # The full gate's slow or tree-reading helpers, for the half of the file that drives it
    # (and for a tree whose bd-precut has no --fast and so falls into it).
    monkeypatch.setattr(module, "_auto_baseline", lambda _r: None, raising=False)
    monkeypatch.setattr(module, "_derive_baseline", lambda _r, _t: (None, "test baseline"), raising=False)
    monkeypatch.setattr(module, "_repo_wide_marker_census", lambda _r: {"error": "test census"}, raising=False)
    for helper in ("_print_underived_failures", "_print_inflight", "_preserve_underived_evidence"):
        monkeypatch.setattr(module, helper, lambda *a, **k: None, raising=False)
    for var, val in (("BD_PRECUT_FAST", env_fast), ("BD_RATCHET_BLOCK", block)):
        if val is None:
            monkeypatch.delenv(var, raising=False)
        else:
            monkeypatch.setenv(var, val)
    try:
        rc = module.main(["--root", str(root), *argv])
    except SystemExit as exc:   # the parser's REFUSED path: a bd-precut without --fast
        rc = exc.code
    out = capsys.readouterr().out
    assert forbidden == [], forbidden
    return rec, rc, out


def test_fast_flag_is_accepted(tmp_path, monkeypatch, capsys):
    _, rc, out = _drive(monkeypatch, capsys, _tree(tmp_path / "wt"), ["--fast"])
    assert "REFUSED: unknown argument" not in out, out
    assert rc == 0, out


@pytest.mark.parametrize("argv,env_fast", [(["--fast"], None), (["--gate"], "1"), (["--fast", "--gate"], "0")],
                         ids=["flag", "harness-env", "flag-over-env-0"])
def test_fast_runs_exactly_the_fast_gates_in_order(tmp_path, monkeypatch, capsys, argv, env_fast):
    root = _tree(tmp_path / "wt")
    rec, rc, out = _drive(monkeypatch, capsys, root, argv, env_fast=env_fast)
    py, r = sys.executable, str(root)
    assert rec.calls == [
        [py, os.path.join(BIN, "bd-guardcheck"), "--tree", r],
        [py, os.path.join(BIN, "bd-envscan"), "--tree", r],
        [py, os.path.join(BIN, "bd-secrets"), "--work", r],
        [py, os.path.join(BIN, "bd-ratchet"), "--check", "--tree", r, "--metric", "defect_DP_total"],
        [py, os.path.join(BIN, "bd-shipped"), "--repo", r, "--against", "origin/main",
         "--register", os.path.join(r, "project-knowledge", "IMPROVEMENT_BACKLOG.md"),
         "--candidate-blobs", os.path.join(r, "tests", "fixtures", "register_candidate_blobs_row473.json")],
    ], rec.calls
    assert out.count("  [release trio] ok") == 1, out
    assert out.count("] ok") == 6, out   # the five tools and the trio read, each once
    assert f"== bd-precut --fast --gate :: root={r}" in out, out
    # what --fast leaves to CI is reported UNKNOWN by name, never absorbed into "cut-ready"
    assert ("\nRESULT: cut-ready for what RAN (guard pins, env tranche, secret scan, metric ratchet, "
            "register containment, release trio) -- 7 check(s) NOT RUN, so UNKNOWN, not OK:\n"
            "  - test suite and test bands (--fast: CI owns it)\n") in out, out
    assert len(_result(out)[1]) == 7, out
    assert rc == 0, out


def test_fast_regenerates_nothing(tmp_path, monkeypatch, capsys):
    root = _tree(tmp_path / "wt")
    before = _snapshot(root)
    rec, rc, out = _drive(monkeypatch, capsys, root, ["--fast"])
    assert "== bd-precut --fast --gate" in out, out
    regenerating = [argv for argv in rec.calls if REGENERATORS & {os.path.basename(a) for a in argv}]
    assert regenerating == [], regenerating
    assert _snapshot(root) == before   # not a byte rewritten, not a file added
    assert rc == 0, out


def test_fast_runs_no_test_suite(tmp_path, monkeypatch, capsys):
    rec, rc, out = _drive(monkeypatch, capsys, _tree(tmp_path / "wt"), ["--fast"])
    assert "== bd-precut --fast --gate" in out, out
    suites = [argv for argv in rec.calls if _runs_tests(argv)]
    assert suites == [], suites
    assert rc == 0, out


@pytest.mark.parametrize("tool,code,label", [
    ("bd-guardcheck", 1, "guard pins"),             # a drifted pin
    ("bd-guardcheck", 2, "guard pins"),             # an unknown pin
    ("bd-envscan", 3, "env tranche"),               # open env vars
    ("bd-secrets", 1, "secret scan"),               # a committed secret
    ("bd-shipped", 3, "register containment"),      # a register row NOT SHIPPED
    ("bd-shipped", 4, "register containment"),      # register UNKNOWN
])
def test_fast_exits_with_the_failing_gates_own_rc(tmp_path, monkeypatch, capsys, tool, code, label):
    rec, rc, out = _drive(monkeypatch, capsys, _tree(tmp_path / "wt"), ["--fast"], rcs={tool: code})
    assert f"RESULT: NOT CUT-READY -- {label}: {tool} rc={code}\n" in out, out
    assert rc == code, out
    assert rec.tools() == FAST_GATES, rec.tools()   # one red does not hide the others


def test_the_first_failing_gate_is_the_exit_and_every_red_is_named(tmp_path, monkeypatch, capsys):
    rec, rc, out = _drive(monkeypatch, capsys, _tree(tmp_path / "wt"), ["--fast"],
                          rcs={"bd-guardcheck": 1, "bd-shipped": 4})
    assert ("RESULT: NOT CUT-READY -- guard pins: bd-guardcheck rc=1; "
            "register containment: bd-shipped rc=4\n") in out, out
    assert rc == 1, out
    assert rec.tools() == FAST_GATES, rec.tools()


def test_a_release_trio_that_disagrees_fails_the_register_trio_gate(tmp_path, monkeypatch, capsys):
    root = _tree(tmp_path / "wt", changelog_version="3.66.1633")
    rec, rc, out = _drive(monkeypatch, capsys, root, ["--fast"])
    assert "  [release trio] FAILED -- CHANGELOG.md's top entry is v3.66.1633, not v3.66.1634" in out, out
    assert rc == 3, out
    assert rec.tools() == FAST_GATES, rec.tools()


@pytest.mark.parametrize("block,want_rc", [(None, 0), ("1", 3)], ids=["advisory", "BD_RATCHET_BLOCK=1"])
def test_a_ratchet_regression_is_advisory_unless_blocked(tmp_path, monkeypatch, capsys, block, want_rc):
    root = _tree(tmp_path / "wt")
    before = _snapshot(root)
    rec, rc, out = _drive(monkeypatch, capsys, root, ["--fast"], block=block,
                          rcs={"bd-ratchet": 3}, ratchet_out=RATCHET_ROSE)
    assert rc == want_rc, out
    if block is None:
        assert "  [metric ratchet] ok -- metric ratchet regressed -- ADVISORY" in out, out
        assert "\nRESULT: cut-ready for what RAN (" in out, out
    else:
        assert "RESULT: NOT CUT-READY -- metric ratchet: bd-ratchet rc=3 (metric ratchet regressed" in out, out
    after = _snapshot(root)   # O733's advisory note is the one file the gate writes
    assert {k: v for k, v in after.items() if k != ".review/RATCHET-ADVISORY.md"} == before
    assert "delta: +221" in (root / ".review" / "RATCHET-ADVISORY.md").read_text()


@pytest.mark.parametrize("code", [2, 1], ids=["rc2-no-baseline-on-this-host", "rc1"])
def test_a_ratchet_that_can_not_compare_is_unknown_by_name_not_a_block(tmp_path, monkeypatch, capsys, code):
    # rc 2 is what a capacity host without ~/.bd_metrics_baseline.json answers. The full
    # gate reports a ratchet rc that is neither 0 nor 3 as UNKNOWN, and so does --fast.
    rec, rc, out = _drive(monkeypatch, capsys, _tree(tmp_path / "wt"), ["--fast"], rcs={"bd-ratchet": code},
                          ratchet_out="no baseline -- run: bd-ratchet --baseline\n")
    note = f"bd-ratchet exited rc={code} (neither clean nor a regression)"
    assert f"  [metric ratchet] UNKNOWN -- {note}\n" in out, out
    ran, not_run = _result(out)
    assert ran == "guard pins, env tranche, secret scan, register containment, release trio", out
    assert not_run[0] == f"metric ratchet: {note}" and len(not_run) == 8, out
    assert rc == 0, out
    assert rec.tools() == FAST_GATES, rec.tools()


@pytest.mark.parametrize("tool,label", [("bd-envscan", "env tranche"), ("bd-ratchet", "metric ratchet")])
def test_an_absent_fast_gate_tool_is_unknown_by_name_not_a_pass(tmp_path, monkeypatch, capsys, tool, label):
    rec, rc, out = _drive(monkeypatch, capsys, _tree(tmp_path / "wt"), ["--fast"], absent=(tool,))
    assert f"  [{label}] UNKNOWN -- {tool} not found\n" in out, out
    ran, not_run = _result(out)
    assert label not in ran, out
    assert not_run[0] == f"{label}: {tool} not found" and len(not_run) == 8, out
    assert rc == 0, out
    assert rec.tools() == [t for t in FAST_GATES if t != tool], rec.tools()


@pytest.mark.parametrize("tool,label", [("bd-secrets", "secret scan"), ("bd-ratchet", "metric ratchet")])
def test_a_fast_gate_that_times_out_is_unknown_by_name_not_a_pass(tmp_path, monkeypatch, capsys, tool, label):
    rec, rc, out = _drive(monkeypatch, capsys, _tree(tmp_path / "wt"), ["--fast"],
                          raises={tool: subprocess.TimeoutExpired([tool], 900)})
    assert f"  [{label}] UNKNOWN -- {tool} TimeoutExpired: " in out, out
    ran, not_run = _result(out)
    assert label not in ran, out
    assert not_run[0].startswith(f"{label}: {tool} TimeoutExpired: ") and len(not_run) == 8, out
    assert rc == 0, out
    assert rec.tools() == FAST_GATES, rec.tools()


def _ratchet_with(monkeypatch, tmp_path, values: dict, pinned: dict):
    """bd-ratchet with stand-in collectors (each records that it ran) and a private baseline."""
    module = _load(TOOL.parent / "bd-ratchet")
    ran: list[str] = []

    def collector(name):
        def measure(_tree):
            ran.append(name)
            return values[name]
        return measure

    monkeypatch.setattr(module, "METRICS", {name: (collector(name), "ceiling") for name in values})
    baseline = tmp_path / "metrics_baseline.json"
    baseline.write_text(json.dumps(pinned))
    monkeypatch.setattr(module, "BASELINE", str(baseline))
    return module, ran


def test_ratchet_metric_measures_and_compares_only_the_named_metric(tmp_path, monkeypatch, capsys):
    module, ran = _ratchet_with(monkeypatch, tmp_path, {"defect_DP_total": 5, "other_ceiling": 99},
                                {"defect_DP_total": 7, "other_ceiling": 1, "_meta": {"why": "test"}})
    rc = module.cmd_check(argparse.Namespace(tree=str(tmp_path), metric=["defect_DP_total"]))
    out = capsys.readouterr().out
    assert ran == ["defect_DP_total"], ran
    assert "other_ceiling" not in out, out
    assert rc == 0, out
    ran.clear()   # control: the same baseline over every metric regresses on the other one
    assert module.cmd_check(argparse.Namespace(tree=str(tmp_path), metric=None)) == 3
    assert sorted(ran) == ["defect_DP_total", "other_ceiling"], ran


def test_ratchet_metric_naming_no_pinned_metric_is_cannot_evaluate(tmp_path, monkeypatch, capsys):
    module, ran = _ratchet_with(monkeypatch, tmp_path, {"defect_DP_total": 5}, {"defect_DP_total": 7})
    rc = module.cmd_check(argparse.Namespace(tree=str(tmp_path), metric=["no_such_metric"]))
    err = capsys.readouterr().err
    assert "CANNOT-EVALUATE: --metric names no pinned metric: no_such_metric (pinned: defect_DP_total)" in err, err
    assert rc == 2, err
    assert ran == [], ran


@pytest.mark.parametrize("argv,env_fast", [(["--gate"], None), (["--gate", "--full"], "1"), (["--gate"], "0")],
                         ids=["no-env", "full-over-env-1", "env-0"])
def test_without_fast_the_full_gate_still_runs_its_battery(tmp_path, monkeypatch, capsys, argv, env_fast):
    rec, rc, out = _drive(monkeypatch, capsys, _tree(tmp_path / "wt"), argv, env_fast=env_fast)
    # exactly the processes, in order, that a bd-precut without --fast starts under this
    # harness (measured at origin/main 2a0e64c1): --fast adds nothing to the full gate
    assert rec.tools() == ["bd-footguns", "bd-ratchet", "bd-coretest", "pytest"], rec.calls
    battery = [a for argv_ in rec.calls if _tool(argv_) == "pytest" for a in argv_]
    assert "tests/test_v3_66_1184_mutation_specs_are_tracked.py" in battery, rec.calls
    ratchet = [argv_ for argv_ in rec.calls if _tool(argv_) == "bd-ratchet"]
    assert len(ratchet) == 1 and "--metric" not in ratchet[0], ratchet
    assert "== bd-precut --fast" not in out, out
    assert "\nRESULT: cut-ready for what RAN (footguns clean, metric ratchets clean) -- " in out, out
    assert rc == 0, out


# bd-worker-precut.sh and bd-train.sh EXPORT BD_PRECUT_FAST=1 for their precut, and then run
# pytest bands in that same environment. A test that drives bd-precut's full gate must not be
# handed --fast by the band it happens to run in: conftest's isolated_bd_home pops the switch
# before every test body, as it pops the other operator/service values.
def test_a_test_body_never_sees_the_harness_mode_switch():
    assert os.environ.get("BD_PRECUT_FAST") is None, os.environ.get("BD_PRECUT_FAST")


def test_a_band_that_exports_the_harness_mode_switch_does_not_hand_it_to_a_test():
    node = "tests/test_h701_precut_fast.py::test_a_test_body_never_sees_the_harness_mode_switch"
    cp = subprocess.run([sys.executable, "-m", "pytest", node, "-q", "-p", "no:cacheprovider"],
                        cwd=REPO, env=dict(os.environ, BD_PRECUT_FAST="1"),
                        capture_output=True, text=True, timeout=600)
    assert cp.returncode == 0, cp.stdout[-3000:] + cp.stderr[-1000:]
    assert "1 passed" in cp.stdout, cp.stdout[-3000:]
