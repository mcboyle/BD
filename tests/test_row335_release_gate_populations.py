"""Row 335: absent release-gate populations are UNKNOWN, not PASS.

RED was recorded against the untouched row worktree before either production
file was changed.  The real ZIP CLI, pointed at an empty directory, produced
exactly::

    returncode = 0
    stdout = ""
    stderr = "no zips found\n"

The real release-test classifier was then given one real file selection and an
injected successful runner process with no stdout or stderr.  It produced::

    {"failed": 0, "files": 1, "harness_failures": [], "passed": 0,
     "real_failures": [],
     "results": [{"failed": 0, "file": "tests/test_empty_success.py",
                  "harness": false, "passed": 0, "rc": 0, "skipped": 0,
                  "timeout": false, "total": 0}],
     "skipped": 0, "timeouts": []}
    gate = True

The controls are deliberately empty-but-measured cases.  A valid empty ZIP is
one scanned artifact with zero findings, and a recognized ``Total: 0`` line is
one measured runner result with zero tests.  Both stay healthy.  They must not
be merged with zero ZIP artifacts or an unrecognized runner response.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import zipfile

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parents[1]
_ZIP_AUDIT = _REPO / "audit_release_zips.py"


def _load_verify_release():
    spec = importlib.util.spec_from_file_location(
        "row335_verify_release", _REPO / "tools" / "verify_release.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VR = _load_verify_release()


def _run_zip_audit(target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_ZIP_AUDIT), str(target)],
        cwd=_REPO,
        text=True,
        capture_output=True,
        timeout=30,
    )


def _release_fixture(root: Path) -> Path:
    package = root / "bulk_downloader"
    package.mkdir()
    (package / "__init__.py").write_text(
        '__version__ = "3.66.1313"\n', encoding="ascii")
    tests = root / "tests"
    tests.mkdir()
    target = tests / "test_empty_success.py"
    target.write_text("# selected runner target\n", encoding="ascii")
    return target


def _run_release_gate(
    root: Path,
    monkeypatch,
    capsys,
    *,
    stdout: str,
    stderr: str = "",
) -> tuple[int, dict, list[tuple[list[str], dict]]]:
    target = _release_fixture(root)
    calls = []

    def runner(args, **kwargs):
        calls.append((list(args), kwargs))
        return subprocess.CompletedProcess(args, 0, stdout, stderr)

    siblings = (SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    monkeypatch.setattr(VR, "_import_siblings", lambda _root: siblings)
    monkeypatch.setattr(VR, "check_version", lambda *_args: (True, ["measured version"]))
    monkeypatch.setattr(VR, "check_docs", lambda *_args: (True, ["measured docs"]))
    monkeypatch.setattr(VR, "check_templates", lambda *_args: (True, ["measured templates"]))
    monkeypatch.setattr(VR.subprocess, "run", runner)

    rc = VR.main(["--root", str(root), "--tests", "full", "--json"])
    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == [sys.executable, "run_tests.py", "tests/test_empty_success.py"]
    assert kwargs["cwd"] == str(root)
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["timeout"] == 120
    assert Path(kwargs["env"]["BD_HOME"]).is_dir()
    assert payload["tests"]["files"] == 1
    assert payload["tests"]["results"][0]["file"] == target.relative_to(root).as_posix()
    return rc, payload, calls


def _assert_unrecognized_runner_is_unknown(rc: int, payload: dict) -> None:
    tests = payload["tests"]
    row = tests["results"][0]
    assert rc == 1
    assert payload["gates"]["tests:full"] is False
    assert tests["measured_files"] == 0
    assert tests["real_failures"] == []
    assert tests["unmeasured"] == [row]
    assert row["rc"] == 0
    assert row["summary_parsed"] is False
    assert row["total"] is None
    assert row["passed"] is None
    assert row["failed"] is None
    assert row["skipped"] is None


def test_zip_audit_zero_population_is_unknown_not_green(tmp_path):
    assert list(tmp_path.iterdir()) == []

    run = _run_zip_audit(tmp_path)

    assert run.returncode == 2
    assert run.stdout == ""
    assert run.stderr == "UNKNOWN   no zips found\n"


def test_zip_audit_measured_empty_zip_is_still_clean(tmp_path):
    artifact = tmp_path / "measured-empty.zip"
    with zipfile.ZipFile(artifact, "w"):
        pass
    with zipfile.ZipFile(artifact) as archive:
        assert archive.namelist() == []

    run = _run_zip_audit(tmp_path)

    assert run.returncode == 0
    assert run.stdout == f"CLEAN     {artifact}\n"
    assert run.stderr == ""


def test_release_gate_rejects_exit_zero_without_a_summary(
    tmp_path, monkeypatch, capsys
):
    rc, payload, _calls = _run_release_gate(
        tmp_path, monkeypatch, capsys, stdout="", stderr="")

    _assert_unrecognized_runner_is_unknown(rc, payload)


def test_release_gate_rejects_exit_zero_after_summary_format_drift(
    tmp_path, monkeypatch, capsys
):
    rc, payload, _calls = _run_release_gate(
        tmp_path,
        monkeypatch,
        capsys,
        stdout="runner complete: success=0 failure=0 skipped=0\n",
    )

    _assert_unrecognized_runner_is_unknown(rc, payload)


def test_release_gate_accepts_a_recognized_measured_zero_summary(
    tmp_path, monkeypatch, capsys
):
    rc, payload, _calls = _run_release_gate(
        tmp_path,
        monkeypatch,
        capsys,
        stdout="  Total: 0 | Passed: 0 | Failed: 0 | Skipped: 0\n",
    )
    tests = payload["tests"]
    row = tests["results"][0]

    assert rc == 0
    assert payload["gates"]["tests:full"] is True
    assert tests.get("measured_files", 1) == 1
    assert tests.get("unmeasured", []) == []
    assert tests["real_failures"] == []
    assert row.get("summary_parsed", True) is True
    assert (row["total"], row["passed"], row["failed"], row["skipped"]) == (
        0, 0, 0, 0)
