"""Row 407: replay output is adoptable only from complete immutable evidence."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
REPLAY = ROOT / "scripts" / "bd_candidate_replay.py"
ADOPT = ROOT / "scripts" / "bd_candidate_adopt.py"
REAL_GIT = shutil.which("git")
assert REAL_GIT is not None
BD_GATE_SCOPE = "module"


def _run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed ({result.returncode}): {argv!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    return _run([REAL_GIT, *args], cwd=cwd, check=check).stdout.strip()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _commit(cwd: Path, message: str) -> str:
    _git(cwd, "add", "--all")
    _git(cwd, "commit", "-m", message)
    return _git(cwd, "rev-parse", "HEAD")


class ReplayedCase:
    def __init__(self, tmp_path: Path) -> None:
        self.repo = tmp_path / "repo"
        self.source = tmp_path / "source"
        self.output = tmp_path / "output"
        self.manifest = tmp_path / ".output.bd-replay.json"
        self.repo.mkdir()
        _git(self.repo, "init", "-b", "main")
        _git(self.repo, "config", "user.name", "Row 407 Test")
        _git(self.repo, "config", "user.email", "row407@example.invalid")
        _write(self.repo / "shared.txt", "base\n")
        self.base = _commit(self.repo, "base")
        _git(
            self.repo,
            "worktree",
            "add",
            "-b",
            "candidate",
            str(self.source),
            self.base,
        )
        _write(self.repo / "main.txt", "new main\n")
        self.main = _commit(self.repo, "advance main")
        _git(self.repo, "update-ref", "refs/remotes/origin/main", self.main)
        _write(self.source / "candidate.txt", "candidate\n")
        self.source_head = _commit(self.source, "candidate")
        replay = _run(
            [
                sys.executable,
                str(REPLAY),
                "--repo",
                str(self.repo),
                "--source",
                str(self.source),
                "--expect-head",
                self.source_head,
                "--main-ref",
                "refs/remotes/origin/main",
                "--output",
                str(self.output),
                "--json",
            ],
            cwd=ROOT,
            check=False,
        )
        assert replay.returncode == 0, (replay.stdout, replay.stderr)
        body = json.loads(replay.stdout)
        assert Path(body["manifest"]) == self.manifest.resolve()
        assert self.manifest.is_file()

    def run_adopt(
        self,
        *,
        manifest: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        result = _run(
            [
                sys.executable,
                str(ADOPT),
                "--manifest",
                str(manifest or self.manifest),
                "--json",
            ],
            cwd=ROOT,
            env=env,
            check=False,
        )
        return result, json.loads(result.stdout)


@pytest.fixture
def replayed_case(tmp_path: Path) -> ReplayedCase:
    return ReplayedCase(tmp_path)


def _visible_state(case: ReplayedCase) -> tuple[bytes, str, str, str]:
    return (
        case.manifest.read_bytes(),
        _git(case.source, "status", "--porcelain=v2", "--untracked-files=all"),
        _git(case.output, "status", "--porcelain=v2", "--untracked-files=all"),
        _git(case.repo, "rev-parse", "refs/remotes/origin/main"),
    )


def test_complete_unchanged_replay_manifest_is_adoptable_and_read_only(
    replayed_case: ReplayedCase,
) -> None:
    """Dropping any evidence predicate would let an incomplete record authorize use."""

    before = _visible_state(replayed_case)

    result, body = replayed_case.run_adopt()

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert body["verdict"] == "ADOPTABLE"
    assert body["manifest"] == str(replayed_case.manifest.resolve())
    assert all(body["evidence"].values())
    assert _visible_state(replayed_case) == before


def test_source_bytes_drift_is_not_adoptable(replayed_case: ReplayedCase) -> None:
    """Checking only source HEAD misses dirty edits made after replay."""

    _write(replayed_case.source / "candidate.txt", "changed after replay\n")

    result, body = replayed_case.run_adopt()

    assert result.returncode == 1
    assert body["verdict"] == "NOT_ADOPTABLE"
    assert body["evidence"]["source_unchanged"] is False


def test_output_bytes_drift_is_not_adoptable(replayed_case: ReplayedCase) -> None:
    """A path that still exists is not proof that replay output is unchanged."""

    _write(replayed_case.output / "candidate.txt", "mutated output\n")

    result, body = replayed_case.run_adopt()

    assert result.returncode == 1
    assert body["verdict"] == "NOT_ADOPTABLE"
    assert body["evidence"]["output_unchanged"] is False


@pytest.mark.parametrize("missing", ("source", "output"))
def test_missing_required_worktree_evidence_is_unknown(
    replayed_case: ReplayedCase,
    missing: str,
) -> None:
    """Unavailable evidence cannot be collapsed into a readable identity mismatch."""

    path = getattr(replayed_case, missing)
    path.rename(path.with_name(path.name + "-retained"))

    result, body = replayed_case.run_adopt()

    assert result.returncode == 2
    assert body["verdict"] == "UNKNOWN"
    assert body["reason_code"] == "EVIDENCE_PATH_UNREADABLE"


def test_main_ref_drift_is_not_adoptable(replayed_case: ReplayedCase) -> None:
    """Adopting after main moves would replay against evidence for a stale base."""

    _write(replayed_case.repo / "later.txt", "later main\n")
    later = _commit(replayed_case.repo, "move main again")
    _git(replayed_case.repo, "update-ref", "refs/remotes/origin/main", later)

    result, body = replayed_case.run_adopt()

    assert result.returncode == 1
    assert body["verdict"] == "NOT_ADOPTABLE"
    assert body["evidence"]["main_ref_unchanged"] is False


def test_partial_claim_record_is_unknown_not_adoptable(
    replayed_case: ReplayedCase,
) -> None:
    """A durable claim proves exclusion, not completed replay."""

    replayed_case.manifest.write_text(
        json.dumps({"schema": 1, "state": "CLAIMED", "token": "still-running"})
    )

    result, body = replayed_case.run_adopt()

    assert result.returncode == 2
    assert body["verdict"] == "UNKNOWN"
    assert body["reason_code"] == "MANIFEST_INCOMPLETE"


def test_unsupported_manifest_schema_is_unknown(
    replayed_case: ReplayedCase,
) -> None:
    """Treating unknown fields as optional lets old validators bless new semantics."""

    manifest = json.loads(replayed_case.manifest.read_text())
    manifest["schema"] = 999
    replayed_case.manifest.write_text(json.dumps(manifest, sort_keys=True))

    result, body = replayed_case.run_adopt()

    assert result.returncode == 2
    assert body["verdict"] == "UNKNOWN"
    assert body["reason_code"] == "MANIFEST_SCHEMA_UNSUPPORTED"


def test_manifest_path_inode_replacement_is_not_adoptable(
    replayed_case: ReplayedCase,
) -> None:
    """Equal bytes at a reused path are not the transaction's held inode."""

    original = replayed_case.manifest.read_bytes()
    replayed_case.manifest.unlink()
    replayed_case.manifest.write_bytes(original)

    result, body = replayed_case.run_adopt()

    assert result.returncode == 1
    assert body["verdict"] == "NOT_ADOPTABLE"
    assert body["evidence"]["manifest_identity_matches"] is False


def test_symlink_manifest_is_unknown_and_never_followed(
    replayed_case: ReplayedCase,
) -> None:
    """Following a replaced manifest symlink would transfer adoption authority."""

    retained = replayed_case.manifest.with_name("retained-manifest.json")
    replayed_case.manifest.rename(retained)
    replayed_case.manifest.symlink_to(retained)

    result, body = replayed_case.run_adopt()

    assert result.returncode == 2
    assert body["verdict"] == "UNKNOWN"
    assert body["reason_code"] == "MANIFEST_NOT_REGULAR"


def test_poisoned_git_environment_cannot_retarget_manifest_adoption(
    replayed_case: ReplayedCase,
    tmp_path: Path,
) -> None:
    """Adoption must independently honor manifest repositories under GIT_* poison."""

    poison = tmp_path / "poison"
    poison.mkdir()
    _git(poison, "init", "-b", "main")
    env = dict(os.environ)
    env.update(
        GIT_DIR=str(poison / ".git"),
        GIT_WORK_TREE=str(poison),
        GIT_INDEX_FILE=str(tmp_path / "poison.index"),
        GIT_OBJECT_DIRECTORY=str(poison / ".git" / "objects"),
    )

    result, body = replayed_case.run_adopt(env=env)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert body["verdict"] == "ADOPTABLE"
