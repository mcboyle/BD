"""Verification lanes preserve the identity of every state they consume.

Rows 416, 464, 472, and 527 are one safety contract: a merge-lane tool must
name the state it measured before reducing that state to a verdict.  The tests
drive the real tool entry points and assert every injected seam before reading
the result, so an injection that did not fire is never allowed to manufacture
green evidence.
"""
from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest


BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parents[1]
TOOLS = {
    "band": REPO / "toolchain" / "bin" / "bd-band",
    "ci": REPO / "toolchain" / "bin" / "bd-ci-verdict",
    "precut": REPO / "toolchain" / "bin" / "bd-precut",
    "sweep": REPO / "toolchain" / "bin" / "bd-sweep-run",
}


def _load(name: str):
    path = TOOLS[name]
    loader = SourceFileLoader(f"bd_1423_{name}", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader, f"precondition: {path} must be loadable"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Bandcheck:
    @staticmethod
    def check(_suites, _root):
        return 0, []


def _drive_band(monkeypatch, tmp_path, capsys, *, returncode, stdout="", stderr="",
                expected_timeout=7, pass_timeout=True):
    mod = _load("band")
    root = tmp_path / "tree"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_lane.py").write_text("def test_lane(): pass\n")
    fired = []

    def injected_run(cmd, **_kwargs):
        assert _kwargs.get("timeout") == expected_timeout, (
            "precondition: production passed timeout=%r, expected %r"
            % (_kwargs.get("timeout"), expected_timeout))
        fired.append(list(cmd))
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

    monkeypatch.setattr(mod, "_load_bandcheck", lambda: _Bandcheck())
    monkeypatch.setattr(mod.sec, "resolve_test_interpreter", lambda _root: sys.executable)
    monkeypatch.setattr(mod, "_registered_pytest_argv", lambda *_a, **_kw: ["pytest-stub"])
    monkeypatch.setattr(mod, "band_env", lambda: {})
    monkeypatch.setattr(mod, "run", injected_run)

    argv = ["tests/test_lane.py", "--work", str(root)]
    if pass_timeout:
        argv.extend(["--timeout", str(expected_timeout)])
    rc = mod.main(argv)
    captured = capsys.readouterr()
    assert len(fired) == 1, f"precondition: injected pytest fired {len(fired)} times"
    return rc, captured.out + captured.err


def test_row416_a_suite_timeout_is_not_reported_as_a_bare_failure(
        monkeypatch, tmp_path, capsys):
    rc, output = _drive_band(
        monkeypatch, tmp_path, capsys, returncode=124,
        stderr="TIMEOUT: 'pytest-stub' exceeded 7s")
    plain = re.sub(r"\x1b\[[0-9;]*m", "", output)
    assert rc == 1, f"a timed-out band must remain non-green, got rc={rc}"
    assert "TIMEOUT" in plain and "exceeded 7s" in plain, output
    assert "TIMEOUT  tests/test_lane.py" in plain, output
    assert "FAIL  tests/test_lane.py: (no summary)" not in plain, output


def test_row416_a_genuine_pytest_failure_stays_fail(monkeypatch, tmp_path, capsys):
    rc, output = _drive_band(
        monkeypatch, tmp_path, capsys, returncode=1,
        stdout="1 failed in 0.01s\n")
    assert rc == 1
    assert "FAIL" in output, output
    assert "tests/test_lane.py: 1 failed in 0.01s" in output, output
    assert "TIMEOUT  tests/test_lane.py" not in output, output


def test_row416_timeout_shaped_test_output_cannot_mint_a_timeout(
        monkeypatch, tmp_path, capsys):
    rc, output = _drive_band(
        monkeypatch, tmp_path, capsys, returncode=1,
        stdout="TIMEOUT: test fixture exceeded 7s\n1 failed in 0.01s\n")
    plain = re.sub(r"\x1b\[[0-9;]*m", "", output)
    assert rc == 1
    assert "FAIL  tests/test_lane.py: 1 failed in 0.01s" in plain, output
    assert "TIMEOUT  tests/test_lane.py" not in plain, output


def test_row416_default_timeout_covers_the_remeasured_slowest_suite(
        monkeypatch, tmp_path, capsys):
    rc, output = _drive_band(
        monkeypatch, tmp_path, capsys, returncode=0,
        stdout="1 passed in 0.01s\n", expected_timeout=360, pass_timeout=False)
    assert rc == 0, output


def _precut_root(root: Path):
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
    (root / ".github" / "workflows" / "ci.yml").write_text(
        "name: CI\ntimeout-minutes: 30\n")
    return root


def _drive_precut(monkeypatch, tmp_path, capsys, *, underived_rc: int,
                  predictor_timeout: bool = False,
                  ci_headroom_available: bool = False):
    mod = _load("precut")
    root = _precut_root(tmp_path / "root")
    baseline = tmp_path / "baseline.zip"
    baseline.write_bytes(b"nonempty baseline fixture")
    for rel, _why in (
            # The independent literal is intentional: if production silently
            # drops a gate, a test fixture derived from that smaller list would
            # make the missing denominator invisible.
            ("tests/test_v3_66_1184_mutation_specs_are_tracked.py", ""),
            ("tests/test_row357_mutant_anchors_are_not_fragile.py", ""),
            ("tests/test_v3_66_1222_every_budget_is_subordinate_to_its_bound.py", ""),
            ("tests/test_v3_66_1197_ambient_locale_into_subprocess.py", ""),
            ("tests/test_import_graph_no_new_edges.py", ""),
            ("tests/test_v3_66_1034_guards_survive_a_module_wipe.py", "")):
        (root / rel).write_text("def test_gate(): pass\n")

    calls = []
    predictor = str(root / "tools" / "precut_check.py")

    def fake_run(cmd, **_kwargs):
        argv = [str(x) for x in cmd]
        calls.append(argv)
        if len(argv) > 1 and argv[1] == predictor:
            if predictor_timeout:
                raise subprocess.TimeoutExpired(cmd, 900)
            return subprocess.CompletedProcess(cmd, 9, "predictor advisory\n", "")
        if "pytest" in argv:
            return subprocess.CompletedProcess(cmd, underived_rc, "", "")
        if argv and argv[0] == "gh":
            if ci_headroom_available and argv[1:3] == ["run", "list"]:
                return subprocess.CompletedProcess(cmd, 0, "123\n", "")
            if ci_headroom_available and argv[1:3] == ["run", "view"]:
                return subprocess.CompletedProcess(
                    cmd, 0, "gate-suites (toolchain)\t12\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if any(x.endswith("bd-coretest") for x in argv):
            return subprocess.CompletedProcess(cmd, 0, "CORE TOOLS PASSING: 1/1\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    derived_calls = []

    def derive(root_arg, tmpdir_arg):
        derived_calls.append((root_arg, tmpdir_arg))
        return str(baseline), None

    monkeypatch.setattr(mod, "_auto_baseline", lambda _root: None)
    monkeypatch.setattr(mod, "_derive_baseline", derive)
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    rc = mod.main(["--root", str(root), "--gate"])
    output = capsys.readouterr().out
    predictor_calls = [c for c in calls if len(c) > 1 and c[1] == predictor]
    pytest_calls = [c for c in calls if "pytest" in c]
    assert len(derived_calls) == 1, (
        "precondition: main() must derive its origin/main baseline exactly once; "
        f"got {len(derived_calls)}")
    assert len(predictor_calls) == 1, (
        f"precondition: predictor subprocess fired {len(predictor_calls)} times")
    assert predictor_calls[0].count("--tracked-only") == 1, (
        "precondition: a git-derived baseline and working tree must use the same "
        f"tracked denominator: {predictor_calls[0]}")
    assert len(pytest_calls) == 1, (
        f"precondition: underived-gate subprocess fired {len(pytest_calls)} times")
    gh_calls = [c for c in calls if c and c[0] == "gh"]
    if ci_headroom_available:
        assert len(gh_calls) == 2, (
            f"precondition: CI headroom seam fired {len(gh_calls)} times")
    return rc, output


def test_row464_the_predictor_is_reported_beside_not_inside_the_gate_verdict(
        monkeypatch, tmp_path, capsys):
    rc, output = _drive_precut(
        monkeypatch, tmp_path, capsys, underived_rc=0,
        ci_headroom_available=True)
    assert rc == 0, output
    assert "advisory" in output.lower() and "precut_check" in output, output
    assert "version/pin/surface OK" not in output, output
    assert "NOT CUT-READY" not in output, output


def test_row464_a_real_underived_gate_failure_remains_blocking(
        monkeypatch, tmp_path, capsys):
    rc, output = _drive_precut(
        monkeypatch, tmp_path, capsys, underived_rc=1)
    assert rc == 3
    assert "underived gate(s) FAILED" in output, output
    assert "NOT CUT-READY" in output, output


def test_row464_an_unavailable_predictor_cannot_abort_the_real_gates(
        monkeypatch, tmp_path, capsys):
    rc, output = _drive_precut(
        monkeypatch, tmp_path, capsys, underived_rc=0, predictor_timeout=True)
    assert rc == 0, output
    assert "advisory precut_check" in output, output
    assert "unavailable" in output, output


def _run_ci_fixture(tmp_path: Path, *, names, statuses, gh_rc=0):
    mod = _load("ci")
    ci = tmp_path / "ci.yml"
    ci.write_text(mod._synth_ci_text())
    argv_log = tmp_path / "gh.argv"
    payload = mod._fixture(statuses, names)
    parsed, malformed, bad_status = mod.parse_rows(payload)
    assert len(parsed) == len(statuses), (
        f"precondition: fixture emitted {len(parsed)}/{len(statuses)} rows")
    assert not malformed and not bad_status, (
        f"precondition: fixture rows must be parseable: {malformed} {bad_status}")
    gh = mod._write_fake_gh(
        tmp_path, "gh-fixture", payload, rc=gh_rc, argv_log=argv_log)
    proc = subprocess.run(
        [sys.executable, str(TOOLS["ci"]), "673", "--ci-yml", str(ci),
         "--gh", str(gh)], capture_output=True, text=True)
    logged = argv_log.read_text().splitlines() if argv_log.is_file() else []
    assert logged[:1] == ["pr checks 673"] and len(logged) == 2, (
        f"precondition: fake gh must fire once through the production CLI: {logged}")
    return mod, proc


def test_row472_pending_advisory_is_outside_the_required_denominator(tmp_path):
    mod = _load("ci")
    required = list(mod.SYNTH_NAMES)
    mod, proc = _run_ci_fixture(
        tmp_path, names=required + ["CodeRabbit"],
        statuses=["pass"] * len(required) + ["pending"], gh_rc=8)
    assert len(required) > 0, "precondition: required denominator must be nonzero"
    assert proc.returncode == mod.EXIT_SAFE, proc.stdout + proc.stderr
    assert "MERGE-SAFE" in proc.stdout, proc.stdout
    assert "advisory" in proc.stdout.lower() and "CodeRabbit=pending" in proc.stdout, (
        proc.stdout)


def test_row472_required_pending_and_absent_checks_still_refuse(tmp_path):
    mod = _load("ci")
    required = list(mod.SYNTH_NAMES)
    assert required, "precondition: required denominator must be nonzero"

    pending_dir = tmp_path / "pending"
    pending_dir.mkdir()
    _mod, pending = _run_ci_fixture(
        pending_dir, names=required,
        statuses=["pass"] * (len(required) - 1) + ["pending"], gh_rc=8)
    assert pending.returncode == mod.EXIT_BLOCK, pending.stdout
    assert f"{required[-1]}=pending" in pending.stdout, pending.stdout

    absent_dir = tmp_path / "absent"
    absent_dir.mkdir()
    _mod, absent = _run_ci_fixture(
        absent_dir, names=required[:-1], statuses=["pass"] * (len(required) - 1))
    assert absent.returncode == mod.EXIT_REFUSE, absent.stdout
    assert "VERDICT: REFUSE-" in absent.stdout, absent.stdout
    assert "absent" in absent.stdout.lower(), absent.stdout
    assert required[-1] in absent.stdout, absent.stdout


def test_row472_an_unclassified_foreign_check_is_unknown_not_ignored(tmp_path):
    mod = _load("ci")
    required = list(mod.SYNTH_NAMES)
    _mod, proc = _run_ci_fixture(
        tmp_path, names=required + ["Mystery Gate"],
        statuses=["pass"] * (len(required) + 1))
    assert proc.returncode == mod.EXIT_REFUSE, proc.stdout
    assert "REFUSE-UNKNOWN-CHECK" in proc.stdout, proc.stdout
    assert "Mystery Gate" in proc.stdout, proc.stdout


def test_row472_duplicate_required_identity_is_unknown_not_a_larger_denominator(
        tmp_path):
    mod = _load("ci")
    required = list(mod.SYNTH_NAMES)
    _mod, proc = _run_ci_fixture(
        tmp_path, names=required + [required[0]],
        statuses=["pass"] * (len(required) + 1))
    assert proc.returncode == mod.EXIT_REFUSE, proc.stdout
    assert "REFUSE-DUPLICATE-CHECK" in proc.stdout, proc.stdout
    assert required[0] in proc.stdout, proc.stdout


class _LaunchTransport:
    def __init__(self, mod, home: Path, mode: str):
        self.mod = mod
        self.inner = mod.LocalTransport(home=home)
        self.kind = "local"
        self.mode = mode
        self.fired = 0
        self.launch_rcs = []
        self.rundirs = []

    def run(self, script, timeout=120):
        launch = "BD-SWEEP-LAUNCH-OK" in script
        blocked_target = None
        if launch:
            self.fired += 1
            self.rundirs.append(Path(self.mod._rundir_from_launch(script)))
            if self.mode == "decode":
                script, changed = re.subn(
                    r"(?m)^(printf %s )\S+( \| base64 -d )",
                    r"\1'%%%%'\2", script, count=1)
                assert changed == 1, (
                    f"precondition: decode corruption applied {changed} times")
            elif self.mode == "digest":
                script, changed = re.subn(
                    r"(?m)( != )[0-9a-f]{64}( \]; then)",
                    r"\g<1>0000000000000000000000000000000000000000000000000000000000000000\2",
                    script, count=1)
                assert changed == 1, (
                    f"precondition: digest corruption applied {changed} times")
            elif self.mode == "move":
                rundir = Path(self.mod._rundir_from_launch(script))
                blocked_target = rundir / "runner.sh"
                blocked_target.mkdir(parents=True)
                (blocked_target / "occupied").write_text("block replacement\n")
                blocked_target.chmod(0o500)
            elif self.mode == "stage":
                rundir = Path(self.mod._rundir_from_launch(script))
                blocked_target = rundir
                mkdir_line = re.search(r"(?m)^mkdir -p .*?$", script)
                assert mkdir_line, "precondition: launch mkdir line is absent"
                replacement = mkdir_line.group(0) + "\nchmod 500 " + str(rundir)
                script, changed = re.subn(
                    r"(?m)^mkdir -p .*?$", replacement, script, count=1)
                assert changed == 1, (
                    f"precondition: stage publication block applied {changed} times")
            elif self.mode == "mkdir":
                script, changed = re.subn(
                    r"(?m)^mkdir -p .*?$",
                    "echo 'mkdir injected failure' >&2\nexit 71",
                    script, count=1)
                assert changed == 1, (
                    f"precondition: mkdir failure applied {changed} times")
            elif self.mode != "healthy":
                raise AssertionError(f"unknown launch mode {self.mode!r}")
        try:
            rc, out, err = self.inner.run(script, timeout=timeout)
        finally:
            if blocked_target is not None:
                blocked_target.chmod(0o700)
        if launch:
            self.launch_rcs.append(rc)
            if self.mode == "digest":
                err = "ssh warning: synthetic banner\n" + err
        return rc, out, err

    def fetch(self, remote, local, timeout=120):
        return self.inner.fetch(remote, local, timeout=timeout)


def _sweep_fixture(mod, tmp_path: Path, mode: str):
    home = tmp_path / "home"
    home.mkdir()
    remote, head, record, _order = mod._make_fake_host(
        tmp_path / "host", str(REPO))
    local = tmp_path / "local"
    local.mkdir()
    transport = _LaunchTransport(mod, home, mode)
    args = mod._args_for(str(REPO), remote, local, head)
    return args, transport, record


@pytest.mark.parametrize(
    ("mode", "token", "marker"),
    [
        ("decode", "REFUSE-LAUNCH-SOURCE-DECODE", "BD-SWEEP-SOURCE-DECODE-FAILED"),
        ("stage", "REFUSE-LAUNCH-SOURCE-STAGE", "BD-SWEEP-SOURCE-STAGE-FAILED"),
        ("digest", "REFUSE-LAUNCH-SOURCE-DIGEST", "BD-SWEEP-SOURCE-DIGEST-MISMATCH"),
        ("move", "REFUSE-LAUNCH-SOURCE-PUBLISH", "BD-SWEEP-SOURCE-PUBLISH-FAILED"),
    ],
)
def test_row527_each_source_transport_failure_keeps_its_identity(
        tmp_path, mode, token, marker):
    mod = _load("sweep")
    args, transport, record = _sweep_fixture(mod, tmp_path, mode)
    with pytest.raises(mod.Refuse) as caught:
        mod.run(args, transport_factory=lambda _label, _addr: transport)
    assert transport.fired == 1, (
        f"precondition: {mode} launch seam fired {transport.fired} times")
    assert transport.launch_rcs == [70], (
        f"precondition: {mode} must reach exact rc 70: {transport.launch_rcs}")
    assert caught.value.token == token, str(caught.value)
    assert caught.value.message.count(marker) == 1, caught.value.message
    assert len(transport.rundirs) == 1
    assert not (transport.rundirs[0] / "runner.sh.tmp").exists(), (
        f"{mode} left a temporary runner source behind")
    assert not record.exists(), f"pytest ran despite the {mode} launch refusal"


def test_row527_generic_launch_failure_and_healthy_launch_are_preserved(tmp_path):
    mod = _load("sweep")
    generic_dir = tmp_path / "generic"
    generic_dir.mkdir()
    args, transport, record = _sweep_fixture(mod, generic_dir, "mkdir")
    with pytest.raises(mod.Refuse) as caught:
        mod.run(args, transport_factory=lambda _label, _addr: transport)
    assert transport.fired == 1
    assert transport.launch_rcs == [71]
    assert caught.value.token == "REFUSE-LAUNCH-FAILED", str(caught.value)
    assert "mkdir injected failure" in caught.value.message, caught.value.message
    assert not record.exists(), "pytest ran despite the generic launch refusal"

    healthy_dir = tmp_path / "healthy"
    healthy_dir.mkdir()
    args, transport, record = _sweep_fixture(mod, healthy_dir, "healthy")
    rc, row = mod.run(args, transport_factory=lambda _label, _addr: transport)
    assert transport.fired == 1
    assert transport.launch_rcs == [0]
    assert record.is_file(), "precondition: the healthy launch never ran pytest"
    assert rc == 0 and row.get("verdict") == "SAMPLE-RECORDED", (rc, row)


# ---------------------------------------------------------------------------
# ROW 615 -- a failing underived gate must name ITSELF, not its whole class.
#
# bd-precut ran the six _UNDERIVED_GATES files in one pytest subprocess and kept
# only the return code, so any non-zero rc appended the `why` string for ALL SIX
# gates. MEASURED 2026-09-02: a real failure in exactly one of them
# (tests/test_import_graph_no_new_edges.py::test_baseline_present_and_well_formed)
# printed the whole six-gate class description and cost a rerun by hand to learn
# which one and why. That is CLAUDE.md A7's named shape -- a diagnostic that
# collapses distinct failures costs the investigation, not just the message.
#
# These drive the REAL pytest subprocess (only the tool's other seams are faked)
# so the node id and the assertion text are pytest's own words, not a fixture's.
# ---------------------------------------------------------------------------

_UNDERIVED_FILES = (
    # Independent literal, deliberately NOT read out of bd-precut: a gate that
    # production silently dropped would otherwise shrink this fixture with it.
    "tests/test_v3_66_1184_mutation_specs_are_tracked.py",
    "tests/test_row357_mutant_anchors_are_not_fragile.py",
    "tests/test_v3_66_1222_every_budget_is_subordinate_to_its_bound.py",
    "tests/test_v3_66_1197_ambient_locale_into_subprocess.py",
    "tests/test_import_graph_no_new_edges.py",
    "tests/test_v3_66_1034_guards_survive_a_module_wipe.py",
)

_ALL_SIX_WHYS = (
    "every tracked mutant anchor occurs exactly once",
    "no literal mutation anchor freezes a re-derived value",
    "no inner budget is at or above the bound governing its item",
    "an env-inheriting subprocess pins LC_ALL",
    "no undeclared import edge",
    "no new sys.modules leaker over the ratchet budget",
)


def _drive_precut_with_real_pytest(monkeypatch, tmp_path, capsys, *, bodies):
    """Run bd-precut --gate against a synthetic root whose six underived-gate
    files are `bodies`, with a REAL pytest subprocess for those files only.

    Every other subprocess seam is faked and asserted, so the only thing under
    measurement is the underived-gate block's own diagnostic.
    """
    mod = _load("precut")
    root = _precut_root(tmp_path / "root")
    baseline = tmp_path / "baseline.zip"
    baseline.write_bytes(b"nonempty baseline fixture")
    for rel in _UNDERIVED_FILES:
        (root / rel).write_text(bodies[rel])

    real_run = subprocess.run
    pytest_calls = []
    predictor = str(root / "tools" / "precut_check.py")

    def fake_run(cmd, **kwargs):
        argv = [str(x) for x in cmd]
        if "pytest" in argv:
            pytest_calls.append(argv)
            # BOUNDED BELOW THE 240s BOUND GOVERNING THIS TEST. Six one-line
            # files: measured under 5s. 120 leaves headroom under load and
            # still refuses a hang well inside the governing bound.
            kwargs["timeout"] = 120
            return real_run(cmd, **kwargs)
        if len(argv) > 1 and argv[1] == predictor:
            return subprocess.CompletedProcess(cmd, 9, "predictor advisory\n", "")
        if any(x.endswith("bd-coretest") for x in argv):
            return subprocess.CompletedProcess(cmd, 0, "CORE TOOLS PASSING: 1/1\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(mod, "_auto_baseline", lambda _root: None)
    monkeypatch.setattr(mod, "_derive_baseline",
                        lambda _root, _tmp: (str(baseline), None))
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    rc = mod.main(["--root", str(root), "--gate"])
    output = capsys.readouterr().out
    assert len(pytest_calls) == 1, (
        "precondition: the underived-gate pytest subprocess must fire exactly "
        f"once; it fired {len(pytest_calls)} time(s): {pytest_calls}")
    assert set(_UNDERIVED_FILES).issubset(set(pytest_calls[0])), (
        "precondition: the real pytest call must carry all six gate files: "
        f"{pytest_calls[0]}")
    return rc, output


def _six_bodies(failing=None, body=None):
    out = {rel: "def test_gate():\n    assert True\n" for rel in _UNDERIVED_FILES}
    if failing is not None:
        out[failing] = body
    return out


_FAIL_A = (
    "def test_baseline_present_and_well_formed():\n"
    "    assert False, 'ROW615A the frozen baseline is missing 3 edges'\n"
)
_FAIL_B = (
    "def test_every_tracked_spec_parses_and_declares_schema_band_and_mutants():\n"
    "    assert False, 'ROW615B mutant anchor resolved 0 times'\n"
)


def test_row615_a_failing_underived_gate_names_its_own_node_id(
        monkeypatch, tmp_path, capsys):
    """The failing test is named, in its own words -- not its class of six."""
    rc, output = _drive_precut_with_real_pytest(
        monkeypatch, tmp_path, capsys,
        bodies=_six_bodies("tests/test_import_graph_no_new_edges.py", _FAIL_A))
    assert rc == 3, output
    assert "NOT CUT-READY" in output, output
    assert "underived gate(s) FAILED" in output, output
    assert ("tests/test_import_graph_no_new_edges.py"
            "::test_baseline_present_and_well_formed") in output, output
    assert "ROW615A the frozen baseline is missing 3 edges" in output, output
    # THE COLLAPSE ITSELF: the five gates that passed must not be named as
    # reasons. Naming all six is exactly what row 615 measured.
    silent = [why for why in _ALL_SIX_WHYS
              if why != "no undeclared import edge" and why in output]
    assert not silent, (
        "the RESULT line still describes gates that PASSED, which is the "
        f"collapse this row exists to end: {silent}\n{output}")


def test_row615_a_different_failing_gate_names_that_one_instead(
        monkeypatch, tmp_path, capsys):
    """The message follows the failure; it is not one fixed string."""
    rc, output = _drive_precut_with_real_pytest(
        monkeypatch, tmp_path, capsys,
        bodies=_six_bodies(
            "tests/test_v3_66_1184_mutation_specs_are_tracked.py", _FAIL_B))
    assert rc == 3, output
    assert ("tests/test_v3_66_1184_mutation_specs_are_tracked.py"
            "::test_every_tracked_spec_parses_and_declares_schema_band_and_mutants"
            ) in output, output
    assert "ROW615B mutant anchor resolved 0 times" in output, output
    assert "test_baseline_present_and_well_formed" not in output, output
    assert "no undeclared import edge" not in output, output


def test_row615_all_six_green_still_reaches_the_unchanged_cut_ready_path(
        monkeypatch, tmp_path, capsys):
    """NEGATIVE CONTROL: the fix changes the FAILURE message's granularity and
    nothing else. Six passing gates still print no underived finding."""
    rc, output = _drive_precut_with_real_pytest(
        monkeypatch, tmp_path, capsys, bodies=_six_bodies())
    assert rc == 0, output
    assert "NOT CUT-READY" not in output, output
    assert "underived gate(s) FAILED" not in output, output
    assert "cut-ready" in output, output


_JUNIT_ONE_FAILURE = (
    '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest"'
    ' errors="0" failures="1" skipped="0" tests="1">'
    '<testcase classname="tests.test_import_graph_no_new_edges"'
    ' name="test_baseline_present_and_well_formed" time="0.1">'
    '<failure message="AssertionError: baseline missing 3 edges">tb</failure>'
    "</testcase></testsuite></testsuites>"
)
_JUNIT_ONE_ERROR = (
    '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest"'
    ' errors="1" failures="0" skipped="0" tests="1">'
    '<testcase classname="tests.test_v3_66_1197_ambient_locale_into_subprocess"'
    ' name="test_collect" time="0.1">'
    '<error message="CollectError: cannot import fixtures">tb</error>'
    "</testcase></testsuite></testsuites>"
)
_JUNIT_NO_CASES = (
    '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest"'
    ' errors="0" failures="0" skipped="0" tests="0"></testsuite></testsuites>'
)

_PRESENT_SIX = tuple(
    (rel, why) for rel, why in zip(_UNDERIVED_FILES, _ALL_SIX_WHYS))


def test_row615_every_cause_of_a_nonzero_rc_gets_its_own_words(tmp_path):
    """A7 SELF-AUDIT, asserted rather than asserted-about.

    The defect being fixed is one message serving several causes. The fix is
    only real if the NEW messages cannot do the same, so this drives all five
    ways the underived-gate block can refuse and requires the five strings to
    be pairwise distinct -- not merely non-empty, and not merely different from
    the old one.
    """
    mod = _load("precut")
    argv = ["python", "-m", "pytest", "-q", "tests/test_a.py"]

    named = tmp_path / "named.xml"
    named.write_text(_JUNIT_ONE_FAILURE)
    errored = tmp_path / "errored.xml"
    errored.write_text(_JUNIT_ONE_ERROR)
    empty = tmp_path / "empty.xml"
    empty.write_text(_JUNIT_NO_CASES)
    torn = tmp_path / "torn.xml"
    torn.write_text("<testsuites><testsuite>")            # truncated mid-write
    absent = tmp_path / "never-written.xml"
    assert not absent.exists(), "precondition: the absent record must be absent"

    messages = {
        "named failure": mod._underived_detail(1, str(named), _PRESENT_SIX, argv),
        "collection error": mod._underived_detail(1, str(errored), _PRESENT_SIX, argv),
        "no case recorded": mod._underived_detail(2, str(empty), _PRESENT_SIX, argv),
        "record unparseable": mod._underived_detail(1, str(torn), _PRESENT_SIX, argv),
        "record absent": mod._underived_detail(1, str(absent), _PRESENT_SIX, argv),
        "hang": mod._underived_detail(124, str(absent), _PRESENT_SIX, argv,
                                      timed_out_after=1800),
    }
    assert len(set(messages.values())) == len(messages), (
        "two causes share one message, which is the exact defect row 615 "
        "exists to end:\n" + "\n".join(f"  {k}: {v}" for k, v in messages.items()))

    # And each one says the distinctive thing, so "different" is not merely
    # different noise.
    failure = messages["named failure"]
    assert ("tests/test_import_graph_no_new_edges.py"
            "::test_baseline_present_and_well_formed") in failure, failure
    assert "baseline missing 3 edges" in failure, failure
    assert "[gate: no undeclared import edge]" in failure, failure
    assert "FAILURE" in failure and "ERROR:" not in failure, failure
    for why in _ALL_SIX_WHYS:
        if why != "no undeclared import edge":
            assert why not in failure, (why, failure)

    error = messages["collection error"]
    assert ("tests/test_v3_66_1197_ambient_locale_into_subprocess.py"
            "::test_collect") in error, error
    assert "ERROR" in error and "cannot import fixtures" in error, error

    assert "ZERO failing tests" in messages["no case recorded"], messages
    assert "unparseable" in messages["record unparseable"], messages
    assert "no JUnit record was written" in messages["record absent"], messages
    assert "HANG" in messages["hang"] and "1800s" in messages["hang"], messages
    # The two records that name nothing must still say WHY they name nothing,
    # and must not borrow the hang's words or each other's.
    assert "HANG" not in messages["record absent"], messages
    assert "ZERO failing tests" not in messages["record absent"], messages


def test_row615_a_class_based_gate_failure_is_pasteable_as_a_node_id():
    """A node id that cannot be re-run is barely better than no node id."""
    mod = _load("precut")
    import xml.etree.ElementTree as ET
    case = ET.fromstring(
        '<testcase classname="tests.test_import_graph_no_new_edges.TestBaseline"'
        ' name="test_frozen" />')
    assert mod._junit_nodeid(case, [rel for rel, _ in _PRESENT_SIX]) == (
        "tests/test_import_graph_no_new_edges.py::TestBaseline::test_frozen")
    # NEGATIVE CONTROL: an unmatched classname is reported as-is, never
    # laundered into a plausible-looking path.
    stray = ET.fromstring('<testcase classname="conftest" name="test_x" />')
    assert mod._junit_nodeid(stray, [rel for rel, _ in _PRESENT_SIX]) == (
        "conftest::test_x")
