"""Row 473: register candidate containment is decided by authored blobs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys


BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "toolchain" / "bin" / "bd-shipped"
REGISTER = ROOT / "project-knowledge" / "IMPROVEMENT_BACKLOG.md"
EVIDENCE = ROOT / "tests" / "fixtures" / "register_candidate_blobs_row473.json"


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        text=True,
        capture_output=True,
    )


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.name=fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        message,
    )
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _run(*args: str, repo: Path = ROOT) -> subprocess.CompletedProcess[str]:
    assert TOOL.is_file(), "bd-shipped containment gate is absent"
    return subprocess.run(
        [sys.executable, str(TOOL), "--repo", str(repo), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )


def _register_text(*rows: tuple[int, str, str]) -> str:
    assert rows
    identities = [str(identity) for identity, _, _ in rows]
    assert len(identities) == len(set(identities))
    opened = sum(status == "OPEN" for _, status, _ in rows)
    digest = hashlib.sha256(",".join(identities).encode("ascii")).hexdigest()
    marker = (
        f"<!-- canonical-task-register schema=1 rows={len(rows)} open={opened} "
        f"ids-sha256={digest} -->"
    )
    body = "\n".join(f"| {identity} | {status} | {item} |" for identity, status, item in rows)
    return marker + "\n" + body + "\n"


def _candidate_repo(tmp_path: Path, *, landed: bool) -> tuple[Path, str, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    for rel, text in {
        "subject.py": "state = 'base'\n",
        "DERIVED.txt": "base generated\n",
        "bulk_downloader/__init__.py": '__version__ = "1.0.0"\n',
        "tests/test_settings_center_slice4.py": 'assert __version__ == "1.0.0"\n',
        "CHANGELOG.md": "## v1.0.0\n\nbase\n",
        "toolchain/bin/bd-regen-order": (
            "REQUIRED_CHAIN_LABELS = ('indexes',)\n"
            "TRACKED_OUTPUTS_BY_LABEL = {'indexes': ('DERIVED.txt',)}\n"
        ),
    }.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="ascii")
    base = _commit(repo, "base")
    _git(repo, "branch", "mainline")

    (repo / "subject.py").write_text("state = 'candidate'\n", encoding="ascii")
    (repo / "DERIVED.txt").write_text("candidate generated\n", encoding="ascii")
    (repo / "bulk_downloader/__init__.py").write_text(
        '__version__ = "1.0.1"\n', encoding="ascii"
    )
    (repo / "tests/test_settings_center_slice4.py").write_text(
        'assert __version__ == "1.0.1"\n', encoding="ascii"
    )
    (repo / "CHANGELOG.md").write_text(
        "## v1.0.1\n\ncandidate\n\n## v1.0.0\n\nbase\n", encoding="ascii"
    )
    candidate = _commit(repo, "candidate")

    _git(repo, "checkout", "-q", "mainline")
    if landed:
        (repo / "subject.py").write_text("state = 'candidate'\n", encoding="ascii")
    else:
        (repo / "subject.py").write_text("state = 'different'\n", encoding="ascii")
    (repo / "DERIVED.txt").write_text("main generated\n", encoding="ascii")
    (repo / "bulk_downloader/__init__.py").write_text(
        '__version__ = "1.0.9"\n', encoding="ascii"
    )
    (repo / "tests/test_settings_center_slice4.py").write_text(
        'assert __version__ == "1.0.9"\n', encoding="ascii"
    )
    (repo / "CHANGELOG.md").write_text(
        "## v1.0.9\n\nmain\n\n## v1.0.0\n\nbase\n", encoding="ascii"
    )
    against = _commit(repo, "independent mainline")
    return repo, base, candidate, against


def test_bd_shipped_exists_as_the_mechanical_containment_gate() -> None:
    assert TOOL.is_file(), "bd-shipped containment gate is absent"


def test_rebased_candidate_is_shipped_when_every_authored_blob_matches(
    tmp_path: Path,
) -> None:
    repo, base, candidate, against = _candidate_repo(tmp_path, landed=True)
    changed = _git(
        repo, "diff", "--no-renames", "--name-only", base, candidate
    ).stdout.splitlines()
    assert len(changed) == 5
    assert _git(
        repo, "merge-base", "--is-ancestor", candidate, against, check=False
    ).returncode == 1
    assert _git(repo, "rev-parse", f"{candidate}:subject.py").stdout == _git(
        repo, "rev-parse", f"{against}:subject.py"
    ).stdout
    assert _git(repo, "rev-parse", f"{candidate}:DERIVED.txt").stdout != _git(
        repo, "rev-parse", f"{against}:DERIVED.txt"
    ).stdout

    result = _run(
        "--base",
        base,
        "--against",
        against,
        "--authored",
        candidate,
        repo=repo,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "SHIPPED: 1 of 1 substantive candidate blobs equal" in result.stdout
    assert result.stdout.count("EXCLUDED release-trio ") == 3
    assert result.stdout.count("EXCLUDED generated DERIVED.txt") == 1


def test_a_genuinely_unmerged_candidate_is_not_reported_as_shipped(
    tmp_path: Path,
) -> None:
    repo, base, candidate, against = _candidate_repo(tmp_path, landed=False)
    assert _git(repo, "rev-parse", f"{candidate}:subject.py").stdout != _git(
        repo, "rev-parse", f"{against}:subject.py"
    ).stdout

    result = _run(
        "--base",
        base,
        "--against",
        against,
        "--authored",
        candidate,
        repo=repo,
    )

    assert result.returncode == 3
    assert "NOT SHIPPED: 0 of 1 substantive candidate blobs equal" in result.stdout
    assert "DIFF subject.py" in result.stdout


def test_an_unavailable_candidate_is_unknown_not_not_shipped(tmp_path: Path) -> None:
    repo, base, _, against = _candidate_repo(tmp_path, landed=True)

    result = _run(
        "--base",
        base,
        "--against",
        against,
        "--authored",
        "f" * 40,
        repo=repo,
    )

    assert result.returncode == 4
    assert result.stderr.startswith("UNKNOWN: candidate commit cannot be resolved:")
    assert "NOT SHIPPED" not in result.stdout + result.stderr


def test_register_gate_refuses_an_open_row_whose_candidate_blobs_all_landed(
    tmp_path: Path,
) -> None:
    repo, _, candidate, against = _candidate_repo(tmp_path, landed=True)
    candidate_id = "a" * 40
    register = tmp_path / "register.md"
    register.write_text(
        _register_text((401, "OPEN", f"queued work; candidate {candidate_id}")),
        encoding="ascii",
    )
    evidence = tmp_path / "candidate-blobs.json"
    evidence.write_text(
        json.dumps(
            {
                "schema": "bd-register-candidate-blobs/v1",
                "candidates": [
                    {
                        "candidate": candidate_id,
                        "changed_paths": [
                            {
                                "path": "subject.py",
                                "blob": _git(
                                    repo, "rev-parse", f"{candidate}:subject.py"
                                ).stdout.strip(),
                                "disposition": "authored",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="ascii",
    )
    assert register.read_text(encoding="ascii").count("| 401 | OPEN |") == 1
    assert len(json.loads(evidence.read_text(encoding="ascii"))["candidates"]) == 1

    result = _run(
        "--against",
        against,
        "--register",
        str(register),
        "--candidate-blobs",
        str(evidence),
        repo=repo,
    )

    assert result.returncode == 3
    assert "OPEN row 401 is fully landed" in result.stderr
    assert "1 of 1 substantive candidate blobs equal" in result.stderr


def test_register_gate_fails_unknown_when_candidate_evidence_is_missing(
    tmp_path: Path,
) -> None:
    repo, _, _, against = _candidate_repo(tmp_path, landed=True)
    candidate_id = "b" * 40
    register = tmp_path / "register.md"
    register.write_text(
        _register_text((402, "OPEN", f"queued work; candidate {candidate_id}")),
        encoding="ascii",
    )
    evidence = tmp_path / "candidate-blobs.json"
    evidence.write_text(
        '{"schema":"bd-register-candidate-blobs/v1","candidates":[]}\n',
        encoding="ascii",
    )

    result = _run(
        "--against",
        against,
        "--register",
        str(register),
        "--candidate-blobs",
        str(evidence),
        repo=repo,
    )

    assert result.returncode == 4
    assert "UNKNOWN: candidate evidence denominator disagrees with register" in result.stderr


def test_a_pipe_inside_an_open_row_cannot_hide_a_landed_candidate(
    tmp_path: Path,
) -> None:
    repo, _, candidate, against = _candidate_repo(tmp_path, landed=True)
    candidate_id = "c" * 40
    register = tmp_path / "register.md"
    register.write_text(
        _register_text(
            (403, "OPEN", f"queued work | detail; candidate {candidate_id}")
        ),
        encoding="ascii",
    )
    assert register.read_text(encoding="ascii").count("|") == 5
    assert "queued work | detail" in register.read_text(encoding="ascii")
    evidence = tmp_path / "candidate-blobs.json"
    evidence.write_text(
        json.dumps(
            {
                "schema": "bd-register-candidate-blobs/v1",
                "candidates": [
                    {
                        "candidate": candidate_id,
                        "changed_paths": [
                            {
                                "path": "subject.py",
                                "blob": _git(
                                    repo, "rev-parse", f"{candidate}:subject.py"
                                ).stdout.strip(),
                                "disposition": "authored",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="ascii",
    )
    assert len(json.loads(evidence.read_text(encoding="ascii"))["candidates"]) == 1

    result = _run(
        "--against",
        against,
        "--register",
        str(register),
        "--candidate-blobs",
        str(evidence),
        repo=repo,
    )

    assert result.returncode == 3, result.stdout + result.stderr
    assert "OPEN row 403 is fully landed" in result.stderr


def test_register_row_count_mismatch_is_unknown_not_a_verdict(tmp_path: Path) -> None:
    repo, _, candidate, against = _candidate_repo(tmp_path, landed=True)
    candidate_id = "d" * 40
    register = tmp_path / "register.md"
    valid = _register_text((404, "OPEN", f"queued work; candidate {candidate_id}"))
    malformed = valid.replace("rows=1", "rows=2")
    assert malformed != valid and malformed.count("rows=2") == 1
    register.write_text(malformed, encoding="ascii")
    evidence = tmp_path / "candidate-blobs.json"
    evidence.write_text(
        json.dumps(
            {
                "schema": "bd-register-candidate-blobs/v1",
                "candidates": [
                    {
                        "candidate": candidate_id,
                        "changed_paths": [
                            {
                                "path": "subject.py",
                                "blob": _git(
                                    repo, "rev-parse", f"{candidate}:subject.py"
                                ).stdout.strip(),
                                "disposition": "authored",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="ascii",
    )

    result = _run(
        "--against",
        against,
        "--register",
        str(register),
        "--candidate-blobs",
        str(evidence),
        repo=repo,
    )

    assert result.returncode == 4
    assert "UNKNOWN: register row denominator" in result.stderr


def test_current_register_candidate_denominator_is_nonzero_and_not_landed() -> None:
    text = REGISTER.read_text(encoding="ascii")
    manifest = json.loads(EVIDENCE.read_text(encoding="ascii"))
    assert len(manifest["candidates"]) == 1
    assert text.count(
        "candidate 56a8768ba0a7aaa7b5947c7ee132ae7e81f055c7"
    ) == 1

    result = _run(
        "--against",
        "origin/main",
        "--register",
        str(REGISTER),
        "--candidate-blobs",
        str(EVIDENCE),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == (
        "PASS: 1 candidate row(s), 3 substantive blob(s), 0 fully landed"
    )
