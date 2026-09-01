"""Row 497: adoption proves replay output carries independently derived work."""

from __future__ import annotations

import hashlib
import json
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
TRACKED_CANDIDATE = "candidate.txt"
STAGED_CANDIDATE = "staged-candidate.txt"
UNTRACKED_CANDIDATE = "candidate-note.txt"
BD_GATE_SCOPE = "module"

scripts_path = str(ROOT / "scripts")
path_added = scripts_path not in sys.path
if path_added:
    sys.path.insert(0, scripts_path)
try:
    from bd_candidate_replay import _fingerprint
finally:
    if path_added:
        sys.path.remove(scripts_path)


def _run(
    argv: list[str],
    *,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=cwd,
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


def _git(cwd: Path, *args: str) -> str:
    return _run([REAL_GIT, *args], cwd=cwd).stdout.strip()


def _write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


def _commit(cwd: Path, message: str) -> str:
    _git(cwd, "add", "--all")
    _git(cwd, "commit", "-m", message)
    return _git(cwd, "rev-parse", "HEAD")


def _commits(cwd: Path, start: str, end: str = "HEAD") -> list[str]:
    output = _git(cwd, "rev-list", "--reverse", f"{start}..{end}")
    return output.splitlines() if output else []


def _untracked(cwd: Path) -> list[str]:
    output = _git(cwd, "ls-files", "--others", "--exclude-standard")
    return output.splitlines() if output else []


class AdoptionCase:
    def __init__(self, tmp_path: Path) -> None:
        self.repo = tmp_path / "repo"
        self.source = tmp_path / "source"
        self.output = tmp_path / "output"
        self.manifest = tmp_path / ".output.bd-replay.json"
        self.repo.mkdir()
        _git(self.repo, "init", "-b", "main")
        _git(self.repo, "config", "user.name", "Row 497 Test")
        _git(self.repo, "config", "user.email", "row497@example.invalid")
        _write(
            self.repo / "shared.txt",
            "base line 1\nbase line 2\nbase line 3\nbase line 4\nbase line 5\n"
            "base line 6\nbase line 7\nbase line 8\nbase line 9\nbase line 10\n",
        )
        _write(self.repo / "dirty.txt", "base dirty file\n")
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
        shared_lines = (self.repo / "shared.txt").read_text(
            encoding="utf-8"
        ).splitlines()
        assert len(shared_lines) == 10
        shared_lines[0] = "main line 1"
        _write(self.repo / "shared.txt", "\n".join(shared_lines) + "\n")
        _write(self.repo / "main.txt", "main advance\n")
        self.main = _commit(self.repo, "advance main")
        _git(self.repo, "update-ref", "refs/remotes/origin/main", self.main)
        shared_lines = (self.source / "shared.txt").read_text(
            encoding="utf-8"
        ).splitlines()
        assert len(shared_lines) == 10
        shared_lines[-1] = "candidate line 10"
        _write(self.source / "shared.txt", "\n".join(shared_lines) + "\n")
        _write(self.source / TRACKED_CANDIDATE, "candidate commit\n")
        self.source_head = _commit(self.source, "candidate commit")
        _write(self.source / STAGED_CANDIDATE, "candidate staged\n")
        _git(self.source, "add", STAGED_CANDIDATE)
        _write(self.source / "dirty.txt", "candidate unstaged\n")
        _write(self.source / UNTRACKED_CANDIDATE, "candidate untracked\n")

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
        replay_body = json.loads(replay.stdout)
        assert replay_body["status"] == "REPLAYED"
        assert Path(replay_body["manifest"]) == self.manifest.resolve()
        assert self.manifest.is_file()
        self.adopt_calls = 0

    def assert_untampered_preconditions(self) -> None:
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        source_commits = _commits(self.source, self.base, self.source_head)
        output_commits = _commits(self.output, self.main)
        assert source_commits == [self.source_head], (
            "precondition: the source must carry exactly one candidate commit"
        )
        assert len(output_commits) == 1, (
            "precondition: replay output must carry exactly one commit above main"
        )
        assert manifest["candidate_commits"] == source_commits
        assert _untracked(self.source) == [UNTRACKED_CANDIDATE]
        assert _untracked(self.output) == [UNTRACKED_CANDIDATE]
        assert _git(self.source, "diff", "--cached", "--name-only") == STAGED_CANDIDATE
        assert _git(self.output, "diff", "--cached", "--name-only") == STAGED_CANDIDATE
        assert _git(self.source, "diff", "--name-only") == "dirty.txt"
        assert _git(self.output, "diff", "--name-only") == "dirty.txt"
        source_shared = (self.source / "shared.txt").read_text(encoding="utf-8")
        output_shared = (self.output / "shared.txt").read_text(encoding="utf-8")
        assert source_shared.startswith("base line 1\n")
        assert output_shared.startswith("main line 1\n")
        assert source_shared.endswith("candidate line 10\n")
        assert output_shared.endswith("candidate line 10\n")
        assert manifest["output"]["head"] == _git(self.output, "rev-parse", "HEAD")
        assert manifest["output"]["state_sha256"] == _fingerprint(self.output)

    def rewrite_manifest_output_receipt(self) -> None:
        before = self.manifest.stat()
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["output"]["head"] = _git(self.output, "rev-parse", "HEAD")
        manifest["output"]["state_sha256"] = _fingerprint(self.output)
        self.manifest.write_text(
            json.dumps(manifest, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        after = self.manifest.stat()
        assert (after.st_dev, after.st_ino, after.st_mode) == (
            before.st_dev,
            before.st_ino,
            before.st_mode,
        ), "precondition: schema-valid tampering must preserve manifest identity"

    def run_adopt(self) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        self.adopt_calls += 1
        result = _run(
            [
                sys.executable,
                str(ADOPT),
                "--manifest",
                str(self.manifest),
                "--json",
            ],
            cwd=ROOT,
            check=False,
        )
        return result, json.loads(result.stdout)


@pytest.fixture
def adoption_case(tmp_path: Path) -> AdoptionCase:
    return AdoptionCase(tmp_path)


def test_manifest_rewrite_cannot_hide_a_missing_untracked_candidate_entry(
    adoption_case: AdoptionCase,
) -> None:
    adoption_case.assert_untampered_preconditions()
    source_digest = hashlib.sha256(
        (adoption_case.source / UNTRACKED_CANDIDATE).read_bytes()
    ).hexdigest()

    (adoption_case.output / UNTRACKED_CANDIDATE).unlink()
    adoption_case.rewrite_manifest_output_receipt()

    manifest = json.loads(adoption_case.manifest.read_text(encoding="utf-8"))
    assert _commits(adoption_case.source, adoption_case.base) == [
        adoption_case.source_head
    ]
    assert len(_commits(adoption_case.output, adoption_case.main)) == 1
    assert _untracked(adoption_case.source) == [UNTRACKED_CANDIDATE]
    assert _untracked(adoption_case.output) == []
    assert hashlib.sha256(
        (adoption_case.source / UNTRACKED_CANDIDATE).read_bytes()
    ).hexdigest() == source_digest
    assert manifest["output"]["state_sha256"] == _fingerprint(adoption_case.output)

    result, body = adoption_case.run_adopt()

    assert adoption_case.adopt_calls == 1
    assert (
        result.returncode,
        body["verdict"],
        body["evidence"].get("output_reconciles_with_source"),
        sum(value is True for value in body["evidence"].values()),
    ) == (1, "NOT_ADOPTABLE", False, 9)


def test_manifest_rewrite_cannot_hide_output_detached_at_main(
    adoption_case: AdoptionCase,
) -> None:
    adoption_case.assert_untampered_preconditions()

    (adoption_case.output / UNTRACKED_CANDIDATE).unlink()
    _git(adoption_case.output, "switch", "--detach", adoption_case.main)
    adoption_case.rewrite_manifest_output_receipt()

    manifest = json.loads(adoption_case.manifest.read_text(encoding="utf-8"))
    assert manifest["candidate_commits"] == [adoption_case.source_head]
    assert len(_commits(adoption_case.source, adoption_case.base)) == 1
    assert len(_commits(adoption_case.output, adoption_case.main)) == 0
    assert sum(
        (adoption_case.output / relative).exists()
        for relative in (TRACKED_CANDIDATE, UNTRACKED_CANDIDATE)
    ) == 0
    assert (adoption_case.source / TRACKED_CANDIDATE).is_file()
    assert (adoption_case.source / UNTRACKED_CANDIDATE).is_file()
    assert manifest["output"]["head"] == adoption_case.main
    assert manifest["output"]["state_sha256"] == _fingerprint(adoption_case.output)

    result, body = adoption_case.run_adopt()

    assert adoption_case.adopt_calls == 1
    assert (
        result.returncode,
        body["verdict"],
        body["evidence"].get("output_reconciles_with_source"),
        sum(value is True for value in body["evidence"].values()),
    ) == (1, "NOT_ADOPTABLE", False, 9)


def test_manifest_rewrite_cannot_hide_tracked_candidate_content_drift(
    adoption_case: AdoptionCase,
) -> None:
    adoption_case.assert_untampered_preconditions()

    _write(adoption_case.output / TRACKED_CANDIDATE, "tampered candidate output\n")
    adoption_case.rewrite_manifest_output_receipt()

    manifest = json.loads(adoption_case.manifest.read_text(encoding="utf-8"))
    assert len(_commits(adoption_case.source, adoption_case.base)) == 1
    assert len(_commits(adoption_case.output, adoption_case.main)) == 1
    assert _untracked(adoption_case.source) == [UNTRACKED_CANDIDATE]
    assert _untracked(adoption_case.output) == [UNTRACKED_CANDIDATE]
    assert (adoption_case.source / TRACKED_CANDIDATE).read_text(
        encoding="utf-8"
    ) == "candidate commit\n"
    assert (adoption_case.output / TRACKED_CANDIDATE).read_text(
        encoding="utf-8"
    ) == "tampered candidate output\n"
    assert manifest["output"]["state_sha256"] == _fingerprint(adoption_case.output)

    result, body = adoption_case.run_adopt()

    assert adoption_case.adopt_calls == 1
    assert (
        result.returncode,
        body["verdict"],
        body["evidence"].get("output_reconciles_with_source"),
        sum(value is True for value in body["evidence"].values()),
    ) == (1, "NOT_ADOPTABLE", False, 9)


def test_untampered_replay_still_has_ten_true_adoption_keys(
    adoption_case: AdoptionCase,
) -> None:
    adoption_case.assert_untampered_preconditions()

    result, body = adoption_case.run_adopt()

    assert adoption_case.adopt_calls == 1
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert body["verdict"] == "ADOPTABLE"
    assert len(body["evidence"]) == 10
    assert sum(value is True for value in body["evidence"].values()) == 10


@pytest.mark.parametrize(
    ("field", "replacement", "failed_key"),
    (
        ("merge_base", "0" * 40, "merge_base_matches"),
        ("candidate_commits", [], "candidate_commits_match"),
    ),
)
def test_existing_manifest_derivation_tampers_keep_their_own_failure_keys(
    adoption_case: AdoptionCase,
    field: str,
    replacement: object,
    failed_key: str,
) -> None:
    adoption_case.assert_untampered_preconditions()
    manifest = json.loads(adoption_case.manifest.read_text(encoding="utf-8"))
    assert manifest[field] != replacement
    before = adoption_case.manifest.stat()
    manifest[field] = replacement
    adoption_case.manifest.write_text(
        json.dumps(manifest, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    after = adoption_case.manifest.stat()
    assert (after.st_dev, after.st_ino, after.st_mode) == (
        before.st_dev,
        before.st_ino,
        before.st_mode,
    )

    result, body = adoption_case.run_adopt()

    assert adoption_case.adopt_calls == 1
    assert result.returncode == 1
    assert body["verdict"] == "NOT_ADOPTABLE"
    assert body["evidence"][failed_key] is False
    assert sum(value is False for value in body["evidence"].values()) == 1
