"""v3.66.1188 -- review controls for durable, specific mutation evidence."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-mutate"
_NAME = "v3_66_9998_review_control.json"
_BAND = "tests/test_m.py"
_CATCHER = f"{_BAND}::test_value"


def _tree(tmp_path: Path, test_source: str | None = None) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "m.py").write_text(
        "VALUE = 1\n"
        "SESSION_FAILURE = False\n"
        "ZERO_COLLECTION = False\n",
        encoding="utf-8",
    )
    (tmp_path / _BAND).write_text(
        test_source
        or (
            "import m\n"
            "def test_value():\n"
            "    assert m.VALUE == 1\n"
        ),
        encoding="utf-8",
    )
    return tmp_path


def _track(work: Path, *paths: str) -> None:
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "add", "--", *paths], cwd=work, check=True)


def _mutant(**updates) -> dict:
    mutant = {
        "label": "change the measured value",
        "file": "m.py",
        "old": "VALUE = 1",
        "new": "VALUE = 2",
        "catcher": _CATCHER,
    }
    mutant.update(updates)
    return mutant


def _run(
    work: Path,
    mutant: dict,
    *,
    band: str = _BAND,
    emit: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    spec = work / "scratch.json"
    spec.write_text(json.dumps([mutant]), encoding="utf-8")
    command = [
        sys.executable,
        str(_TOOL),
        "--spec",
        str(spec),
        "--band",
        band,
        "--work",
        str(work),
        "--json",
    ]
    if emit:
        command.extend([
            "--emit-spec",
            _NAME,
            "--subject",
            "review control",
        ])
    return subprocess.run(
        command,
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )


def _row(run: subprocess.CompletedProcess[str]) -> dict:
    start = run.stdout.find("{")
    assert start >= 0, run.stdout + run.stderr
    return json.loads(run.stdout[start:])["rows"][0]


def _destination(work: Path) -> Path:
    return work / "tests" / "mutants" / _NAME


def test_junit_xml_is_never_created_inside_the_worktree_even_if_TMPDIR_points_there(tmp_path):
    work = _tree(
        tmp_path,
        "from pathlib import Path\n"
        "import m\n"
        "def test_value(request):\n"
        "    xml = Path(request.config.option.xmlpath).resolve()\n"
        "    assert not xml.is_relative_to(Path.cwd().resolve()), xml\n"
        "    assert xml.name == 'band.xml'\n"
        "    assert m.VALUE == 1\n",
    )
    child = dict(os.environ)
    child["TMPDIR"] = str(work)
    run = _run(work, _mutant(), env=child)
    assert run.returncode == 0, run.stdout + run.stderr
    assert _row(run)["verdict"] == "CAUGHT"
    assert list(work.rglob("band.xml")) == []


def test_an_exit_2_battery_publishes_neither_canonical_spec_nor_staging_file(tmp_path):
    work = _tree(
        tmp_path,
        "def test_value():\n"
        "    assert False, 'baseline deliberately has no verdict'\n",
    )
    _track(work, "m.py", _BAND)
    run = _run(work, _mutant(), emit=True)
    assert run.returncode == 2, run.stdout + run.stderr
    assert not _destination(work).exists()
    staged = list((work / "tests" / "mutants").glob(".*.tmp"))
    assert staged == [], staged


def test_emitter_refuses_an_ambiguous_anchor_before_publication(tmp_path):
    work = _tree(tmp_path)
    (work / "m.py").write_text("VALUE = 1\nVALUE = 1\n", encoding="utf-8")
    _track(work, "m.py", _BAND)
    run = _run(work, _mutant(), emit=True)
    assert run.returncode == 2, run.stdout + run.stderr
    assert "anchor occurs 2 times" in run.stderr, run.stderr
    assert not _destination(work).exists()


def test_emitter_refuses_an_undefined_named_catcher_before_publication(tmp_path):
    work = _tree(tmp_path)
    _track(work, "m.py", _BAND)
    missing = f"{_BAND}::test_missing"
    run = _run(work, _mutant(catcher=missing), emit=True)
    assert run.returncode == 2, run.stdout + run.stderr
    assert f"nodeid is not a defined test: {missing}" in run.stderr, run.stderr
    assert not _destination(work).exists()


def test_emitter_refuses_an_untracked_subject_before_publication(tmp_path):
    work = _tree(tmp_path)
    (work / "untracked.py").write_text("VALUE = 1\n", encoding="utf-8")
    _track(work, _BAND)
    run = _run(work, _mutant(file="untracked.py"), emit=True)
    assert run.returncode == 2, run.stdout + run.stderr
    assert "untracked subject: untracked.py" in run.stderr, run.stderr
    assert not _destination(work).exists()


def test_emitter_refuses_an_untracked_band_before_publication(tmp_path):
    work = _tree(tmp_path)
    _track(work, "m.py")
    run = _run(work, _mutant(), emit=True)
    assert run.returncode == 2, run.stdout + run.stderr
    assert f"untracked band target: {_BAND}" in run.stderr, run.stderr
    assert not _destination(work).exists()


def test_nonzero_collection_after_naming_tests_is_UNKNOWN_not_a_collection_error(tmp_path):
    work = _tree(tmp_path)
    (work / "conftest.py").write_text(
        "import m\n"
        "def pytest_sessionfinish(session, exitstatus):\n"
        "    if m.SESSION_FAILURE:\n"
        "        session.exitstatus = 1\n",
        encoding="utf-8",
    )
    mutant = _mutant(
        old="SESSION_FAILURE = False",
        new="SESSION_FAILURE = True",
    )
    run = _run(work, mutant)
    assert run.returncode == 2, run.stdout + run.stderr
    row = _row(run)
    assert row["verdict"] == "UNKNOWN", row
    assert "exited 1 after collecting 1 test" in row["why"], row
    assert "collection error" not in row["why"].lower(), row


def test_zero_collected_tests_is_reachable_UNKNOWN_not_INVALID(tmp_path):
    work = _tree(
        tmp_path,
        "import m\n"
        "import pytest\n"
        "if m.ZERO_COLLECTION:\n"
        "    pytest.skip('mutant removes the denominator', allow_module_level=True)\n"
        "def test_value():\n"
        "    assert m.VALUE == 1\n",
    )
    mutant = _mutant(
        old="ZERO_COLLECTION = False",
        new="ZERO_COLLECTION = True",
    )
    run = _run(work, mutant)
    assert run.returncode == 2, run.stdout + run.stderr
    row = _row(run)
    assert row["verdict"] == "UNKNOWN", row
    assert "collected zero tests" in row["why"], row
