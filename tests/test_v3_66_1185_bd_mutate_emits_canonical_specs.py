"""v3.66.1185 -- bd-mutate itself writes canonical tracked-spec candidates."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-mutate"
_NAME = "v3_66_9999_emitter_contract.json"


def _tree(tmp_path: Path) -> tuple[Path, str]:
    (tmp_path / "tests").mkdir()
    (tmp_path / "m.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    band = "tests/test_m.py"
    (tmp_path / band).write_text(
        "import importlib\n"
        "import m\n"
        "def test_value():\n"
        "    importlib.reload(m)\n"
        "    assert m.value() == 1\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "--", "m.py", band], cwd=tmp_path, check=True)
    return tmp_path, band


def _mutant(*, escaped: bool = False) -> dict:
    if escaped:
        old = "def value():"
        new = "UNOBSERVED = 1\ndef value():"
        label = "unobserved extra binding"
    else:
        old = "return 1"
        new = "return 2"
        label = "return 1 becomes 2"
    return {
        "label": label,
        "file": "m.py",
        "old": old,
        "new": new,
        "catcher": "tests/test_m.py::test_value",
    }


def _run_emit(
    work: Path,
    band: str,
    mutant: dict,
    *,
    name: str = _NAME,
    subject: str = "the emitter's synthetic contract",
) -> subprocess.CompletedProcess[str]:
    source = work / "scratch.json"
    source.write_text(json.dumps([mutant]), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(_TOOL),
            "--spec",
            str(source),
            "--band",
            band,
            "--work",
            str(work),
            "--subject",
            subject,
            "--emit-spec",
            name,
            "--json",
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_emit_spec_writes_the_canonical_object_before_running(tmp_path):
    work, band = _tree(tmp_path)
    run = _run_emit(work, band, _mutant())
    assert run.returncode == 0, run.stdout + run.stderr
    destination = work / "tests" / "mutants" / _NAME
    assert destination.is_file(), "the canonical spec was not written"
    document = json.loads(destination.read_text(encoding="utf-8"))
    assert document == {
        "schema": "bd-mutate-spec/1",
        "_comment": f"Re-run: toolchain/bin/bd-mutate --spec tests/mutants/{_NAME}",
        "subject": "the emitter's synthetic contract",
        "band": [band],
        "mutants": [{**_mutant(), "direction": "regression"}],
    }


def test_an_escape_still_leaves_its_rerunnable_spec(tmp_path):
    work, band = _tree(tmp_path)
    run = _run_emit(work, band, _mutant(escaped=True))
    assert run.returncode == 1, run.stdout + run.stderr
    destination = work / "tests" / "mutants" / _NAME
    assert destination.is_file(), (
        "an escaped battery lost its spec; only green evidence would survive"
    )


def test_emit_spec_refuses_to_clobber_an_existing_battery(tmp_path):
    work, band = _tree(tmp_path)
    destination = work / "tests" / "mutants" / _NAME
    destination.parent.mkdir()
    sentinel = b'{"owned":"somebody else"}\n'
    destination.write_bytes(sentinel)
    run = _run_emit(work, band, _mutant())
    assert run.returncode == 2, run.stdout + run.stderr
    assert "already exists" in run.stderr, run.stderr
    assert destination.read_bytes() == sentinel, "the existing spec was overwritten"


def test_emit_spec_rejects_path_traversal_as_a_name(tmp_path):
    work, band = _tree(tmp_path)
    run = _run_emit(work, band, _mutant(), name="../outside.json")
    assert run.returncode == 2, run.stdout + run.stderr
    assert "canonical basename" in run.stderr, run.stderr
    assert not (work / "tests" / "outside.json").exists()


def test_emit_spec_requires_a_nonempty_subject(tmp_path):
    work, band = _tree(tmp_path)
    run = _run_emit(work, band, _mutant(), subject="")
    assert run.returncode == 2, run.stdout + run.stderr
    assert "--subject is required with --emit-spec" in run.stderr, run.stderr
    assert not (work / "tests" / "mutants" / _NAME).exists()
