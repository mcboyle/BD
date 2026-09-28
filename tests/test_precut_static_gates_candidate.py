"""P2 row precut-static-gates (T134): the worker band must carry the fast static repo-wide gates.

CI's gate-suites shards run every repo-wide gate; the worker band ran none of them, so
tests/test_v3_66_1222 went red twice in CI and never on a worker. The candidate
(harness bd-worker-band.sh + static-gates.txt + bd-static-gates-derive.py) appends a
derived static-gate list to every band.

Harness-cut shape (O1045): opt-in via BD_PRECUT_STATIC_GATES_CANDIDATE=<candidate dir>.
"""

import os
import pathlib
import subprocess
import sys

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_PRECUT_STATIC_GATES_CANDIDATE", "")
REPO = pathlib.Path(__file__).resolve().parents[1]
T134_NODE = "tests/test_v3_66_1222_every_budget_is_subordinate_to_its_bound.py::test_no_new_over_bound_budget_site_appears"

pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def cut(tmp_path):
    """A hermetic cut: one tracked test, one staged change."""
    wt = tmp_path / "wt"
    (wt / "tests").mkdir(parents=True)
    (wt / "tests" / "test_static_fx.py").write_text("def test_ok():\n    assert True\n")
    (wt / "mod.py").write_text("x = 1\n")
    _git(wt, "init", "-q")
    _git(wt, "add", "--", "tests/test_static_fx.py", "mod.py")
    _git(wt, "commit", "-qm", "init")
    (wt / "mod.py").write_text("x = 2\n")
    _git(wt, "add", "--", "mod.py")
    lst = tmp_path / "static-gates.txt"
    lst.write_text("# header\ntests/test_static_fx.py\ntests/test_absent.py\n")
    return wt, lst


def _band(wt, **env):
    script = pathlib.Path(CANDIDATE) / "bd-worker-band.sh"
    assert script.is_file(), f"PSG: candidate band script missing: {script}"
    res = subprocess.run(
        ["bash", str(script), str(wt)],
        env={**os.environ, "BD_WORKER_BAND_DRY_RUN": "1", **env},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return res.stdout + res.stderr


def _band_line(out):
    lines = [ln for ln in out.splitlines() if ln.startswith("BAND (")]
    return lines[-1] if lines else ""


def test_band_carries_the_present_static_gates(cut):
    wt, lst = cut
    out = _band(wt, BD_STATIC_GATES_FILE=str(lst))
    assert "tests/test_static_fx.py" in _band_line(out), (
        f"PSG: static gate not in band:\n{out}"
    )
    assert "test_absent.py" not in out.split("STATIC-GATES:", 1)[-1], out
    assert "STATIC-GATES: 1 selector(s)" in out, out


def test_missing_list_is_said_not_skipped(cut, tmp_path):
    wt, _ = cut
    out = _band(wt, BD_STATIC_GATES_FILE=str(tmp_path / "nope.txt"))
    assert "STATIC-GATES: UNKNOWN" in out, out


def test_opt_out_leaves_the_band_alone(cut):
    wt, lst = cut
    out = _band(wt, BD_STATIC_GATES_FILE=str(lst), BD_WORKER_BAND_NO_STATIC="1")
    assert "tests/test_static_fx.py" not in _band_line(out), out


def test_deriver_keeps_fast_repo_wide_non_process_and_pins(tmp_path):
    wt = tmp_path / "wt"
    (wt / "tests").mkdir(parents=True)
    rw = 'BD_GATE_SCOPE = "repo-wide"\n'
    for name, body in {
        "test_fast.py": rw,
        "test_slow.py": rw,
        "test_module.py": 'BD_GATE_SCOPE = "module"\n',
        "test_proc.py": rw,
        "test_untimed.py": rw,
        "test_row331_guarded_imports_are_declared.py": rw,
    }.items():
        (wt / "tests" / name).write_text(body)
    (wt / "tests" / "PROCESS_TESTS.txt").write_text("# comment\ntests/test_proc.py\n")
    cases = "".join(
        f'<testcase classname="tests.{f[:-3]}" name="t" time="{s}"/>'
        for f, s in [
            ("test_fast.py", 0.4),
            ("test_slow.py", 9.0),
            ("test_module.py", 0.1),
            ("test_proc.py", 0.1),
            ("test_row331_guarded_imports_are_declared.py", 30.0),
        ]
    )
    junit = tmp_path / "j.xml"
    junit.write_text(f"<testsuites><testsuite>{cases}</testsuite></testsuites>")
    res = subprocess.run(
        [
            sys.executable,
            str(pathlib.Path(CANDIDATE) / "bd-static-gates-derive.py"),
            str(junit),
            str(wt),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    sel = [ln for ln in res.stdout.splitlines() if ln and not ln.startswith("#")]
    assert res.returncode == 0, res.stderr
    assert sel == [
        "tests/test_row331_guarded_imports_are_declared.py",
        "tests/test_fast.py",
    ], res.stdout
    assert "untimed 1 (left out)" in res.stdout, res.stdout


def test_shipped_list_pins_the_t134_node_and_names_real_files():
    lst = pathlib.Path(CANDIDATE) / "static-gates.txt"
    sel = [ln for ln in lst.read_text().splitlines() if ln and not ln.startswith("#")]
    assert T134_NODE in sel, "PSG: static-gates.txt lost the T134 node"
    missing = [s for s in sel if not (REPO / s.split("::", 1)[0]).is_file()]
    assert not missing, f"PSG: static-gates.txt names absent files: {missing[:5]}"


def test_local_fallback_never_runs_the_static_suite(cut, tmp_path):
    """O381 (lens F1): when every remote host is down (rc 64) the band falls back to a local
    pytest; the static list must not ride along."""
    wt, lst = cut
    (wt / "tests" / "test_dynamic.py").write_text("def test_ok():\n    assert True\n")
    subprocess.run(["git", "add", "--", "tests/test_dynamic.py"], cwd=wt, check=True)
    shim_dir = tmp_path / "h"
    shim_dir.mkdir()
    remote = shim_dir / "bd-band-remote.sh"
    remote.write_text("#!/bin/bash\necho remote down\nexit 64\n")
    argv = tmp_path / "argv.txt"
    py = wt / "venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text(f'#!/bin/bash\necho "$@" > {argv}\necho "1 passed in 0.01s"\n')
    remote.chmod(0o755)
    py.chmod(0o755)
    script = pathlib.Path(CANDIDATE) / "bd-worker-band.sh"
    res = subprocess.run(
        ["bash", str(script), str(wt), "tests/test_dynamic.py"],
        env={
            **os.environ,
            "BD_HARNESS_DIR": str(shim_dir),
            "BD_STATIC_GATES_FILE": str(lst),
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    out = res.stdout + res.stderr
    ran = argv.read_text() if argv.exists() else ""
    assert "tests/test_dynamic.py" in ran, (
        f"PSG: local fallback did not run the band:\n{out}"
    )
    assert "test_static_fx" not in ran, (
        f"PSG: static suite ran locally on fallback: {ran}"
    )
    assert "dropped for the local fallback" in out, out


def test_deriver_times_class_based_gates_by_their_module(tmp_path):
    """Lens F2: classname tests.test_cls.TestGate belongs to tests/test_cls.py."""
    wt = tmp_path / "wt"
    (wt / "tests").mkdir(parents=True)
    (wt / "tests" / "test_cls.py").write_text('BD_GATE_SCOPE = "repo-wide"\n')
    junit = tmp_path / "j.xml"
    junit.write_text(
        '<testsuites><testsuite><testcase classname="tests.test_cls.TestGate" name="t" time="0.2"/>'
        "</testsuite></testsuites>"
    )
    res = subprocess.run(
        [
            sys.executable,
            str(pathlib.Path(CANDIDATE) / "bd-static-gates-derive.py"),
            str(junit),
            str(wt),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    sel = [ln for ln in res.stdout.splitlines() if ln and not ln.startswith("#")]
    assert sel == ["tests/test_cls.py"], res.stdout
