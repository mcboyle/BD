"""Rows 473, 496, and 511 keep register tooling's denominator independent."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys


BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
APPEND = ROOT / "toolchain" / "bin" / "bd-register-append"
RECONCILE = ROOT / "toolchain" / "bin" / "bd-register-reconcile"
PARSER = ROOT / "project-knowledge" / "build_current_overlay.py"
HEADER = re.compile(
    r"<!-- canonical-task-register schema=1 rows=\d+ open=\d+ ids-sha256=[0-9a-f]{64} -->"
)


def _derive(repo: Path, text: str) -> tuple[int, int, str, list[str]]:
    spec = importlib.util.spec_from_file_location("register_tooling_overlay", repo / "project-knowledge" / "build_current_overlay.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.derive_backlog(text)
    assert result is not None
    return result


def _write_register(repo: Path, rows: list[str]) -> Path:
    knowledge = repo / "project-knowledge"
    knowledge.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(PARSER, knowledge / "build_current_overlay.py")
    provisional = (
        "# fixture\n\n"
        "<!-- canonical-task-register schema=1 rows=0 open=0 ids-sha256=" + "0" * 64 + " -->\n\n"
        "| id | status | item |\n| --- | --- | --- |\n"
        + "".join(f"{row}\n" for row in rows)
    )
    count, opened, digest, _ = _derive(repo, provisional)
    register = knowledge / "IMPROVEMENT_BACKLOG.md"
    register.write_text(
        HEADER.sub(
            f"<!-- canonical-task-register schema=1 rows={count} open={opened} ids-sha256={digest} -->",
            provisional,
            count=1,
        ),
        encoding="ascii",
    )
    return register


def _run_reconcile(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RECONCILE), "--repo", str(repo), "--base", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _commit(repo: Path, message: str) -> None:
    _git(
        repo,
        "add",
        "project-knowledge/IMPROVEMENT_BACKLOG.md",
        "project-knowledge/build_current_overlay.py",
        "subject.py",
    )
    _git(repo, "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", message)


def test_511_mutated_append_refuses_one_preexisting_physical_row_rewrite(tmp_path: Path) -> None:
    """The guard must compare candidate bytes with the pre-append original."""
    repo = tmp_path / "repo"
    register = _write_register(
        repo,
        ["| 401 | OPEN | preserved before |", "| 402 | CLOSED @1359 | preserved after |"],
    )
    original = register.read_bytes()
    assert original.count(b"| 401 | OPEN | preserved before |\n") == 1
    assert _derive(repo, original.decode("ascii"))[0] == 2
    source = APPEND.read_text(encoding="utf-8")
    anchor = 'appended = text + "".join(f"{row}\\n" for _, row in proposed)'
    assert source.count(anchor) == 1
    mutant = tmp_path / "bd-register-append-mutant"
    mutant.write_text(
        source.replace(
            anchor,
            'appended = text.replace("| 401 | OPEN | preserved before |", "| 401 | OPEN | rewritten before |", 1) + "".join(f"{row}\\n" for _, row in proposed)',
            1,
        ),
        encoding="utf-8",
    )
    assert mutant.read_text(encoding="utf-8").count("rewritten before") == 1
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "schema": "bd-register-append/v1",
                "expected_ids_sha256": _derive(repo, original.decode("ascii"))[2],
                "rows": ["| 403 | OPEN | proposed row |"],
            }
        ),
        encoding="ascii",
    )

    result = subprocess.run(
        [sys.executable, str(mutant), "--repo", str(repo), "--request", str(request)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0, result.stdout + result.stderr
    assert "append would change a physical row outside the proposed suffix" in result.stderr
    assert register.read_bytes() == original


def test_496_reconciliation_refuses_missing_or_zero_intake_and_accepts_explicit_non_deferral(tmp_path: Path) -> None:
    """A decision denominator is independent of the register it reconciles."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    register = _write_register(repo, ["| 401 | OPEN | represented deferral |", "| 402 | CLOSED @1 | shipped work |"])
    (repo / "subject.py").write_text("value = 'base'\n", encoding="ascii")
    _commit(repo, "base")
    (repo / "subject.py").write_text("value = 'candidate'\n", encoding="ascii")
    _commit(repo, "candidate")
    _git(repo, "tag", "candidate")
    intake = repo / "project-knowledge" / "REGISTER_INTAKE.json"
    intake.write_text(
        json.dumps(
            {
                "schema": 1,
                "decisions": [
                    {"id": "defer-401", "disposition": "defer", "row": 401},
                    {"id": "audit-complete", "disposition": "non-deferral", "evidence": "historical audit closed"},
                ],
            }
        ),
        encoding="ascii",
    )
    shipments = repo / "project-knowledge" / "REGISTER_SHIPMENTS.json"
    shipments.write_text(
        '{"schema": 1, "shipments": [{"row": 402, "candidate": "candidate", "substantive_paths": ["subject.py"]}]}\n',
        encoding="ascii",
    )
    assert len(json.loads(intake.read_text(encoding="ascii"))["decisions"]) == 2
    assert register.read_text(encoding="ascii").count("| 401 | OPEN |") == 1

    control = _run_reconcile(repo)
    assert control.returncode == 0, control.stderr

    intake.write_text('{"schema": 1, "decisions": []}\n', encoding="ascii")
    zero = _run_reconcile(repo)
    assert zero.returncode == 2
    assert "UNKNOWN: decision intake denominator is zero" in zero.stderr

    intake.write_text(
        json.dumps({"schema": 1, "decisions": [{"id": "defer-missing", "disposition": "defer", "row": 403}]}),
        encoding="ascii",
    )
    assert "| 403 |" not in register.read_text(encoding="ascii")
    missing = _run_reconcile(repo)
    assert missing.returncode == 2
    assert "decision defer-missing has no canonical register row 403" in missing.stderr


def test_473_reconciliation_compares_candidate_blobs_and_refuses_an_open_landed_row(tmp_path: Path) -> None:
    """A row's landing verdict derives from candidate and base, never the row alone."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _write_register(repo, ["| 401 | OPEN | shipped candidate still open |"])
    (repo / "subject.py").write_text("value = 'old'\n", encoding="ascii")
    _commit(repo, "base")
    _git(repo, "branch", "base")
    (repo / "subject.py").write_text("value = 'landed'\n", encoding="ascii")
    _commit(repo, "candidate")
    _git(repo, "tag", "candidate")
    _git(repo, "checkout", "-q", "base")
    (repo / "subject.py").write_text("value = 'landed'\n", encoding="ascii")
    _commit(repo, "land independently")
    (repo / "project-knowledge" / "REGISTER_INTAKE.json").write_text(
        '{"schema": 1, "decisions": [{"id": "defer-401", "disposition": "defer", "row": 401}]}\n',
        encoding="ascii",
    )
    shipments = repo / "project-knowledge" / "REGISTER_SHIPMENTS.json"
    shipments.write_text(
        '{"schema": 1, "shipments": [{"row": 401, "candidate": "candidate", "substantive_paths": ["subject.py"]}]}\n',
        encoding="ascii",
    )
    assert (repo / "subject.py").read_text(encoding="ascii") == "value = 'landed'\n"
    assert subprocess.run(
        ["git", "-C", str(repo), "show", "candidate:subject.py"], text=True, capture_output=True, check=True
    ).stdout == "value = 'landed'\n"

    landed = _run_reconcile(repo)

    assert landed.returncode == 2
    assert "OPEN row 401 is fully landed from candidate" in landed.stderr


def test_473_checked_in_shipment_denominator_covers_every_named_landed_row() -> None:
    """The checked-in inventory cannot omit one of row 473's four examples."""
    manifest = json.loads((ROOT / "project-knowledge" / "REGISTER_SHIPMENTS.json").read_text(encoding="ascii"))
    assert {entry["row"] for entry in manifest["shipments"]} == {426, 427, 432, 447}

    result = _run_reconcile(ROOT)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "PASS: reconciled 2 decision(s) and 4 candidate shipment(s)"
