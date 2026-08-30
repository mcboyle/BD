"""Row 407: candidate replay must never rewrite the source worker."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bd_candidate_replay.py"
REAL_GIT = shutil.which("git")
assert REAL_GIT is not None


def _run(
    argv: list[str],
    *,
    cwd: Path,
    check: bool = True,
    env: dict[str, str] | None = None,
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


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run([REAL_GIT, *args], cwd=cwd, check=check)


def _write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)


def _commit(cwd: Path, message: str) -> str:
    _git(cwd, "add", "--all")
    _git(cwd, "commit", "-m", message)
    return _git(cwd, "rev-parse", "HEAD").stdout.strip()


def _git_bytes(cwd: Path, *args: str) -> bytes:
    result = subprocess.run(
        [REAL_GIT, *args],
        cwd=cwd,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"git bytes command failed ({result.returncode}): {args!r}\n"
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
        )
    return result.stdout


def _source_snapshot(source: Path) -> tuple[bytes, ...]:
    """Exact Git-visible source state, including untracked bytes and modes."""

    untracked_raw = _git_bytes(
        source, "ls-files", "--others", "--exclude-standard", "-z"
    )
    untracked_state: list[bytes] = []
    for raw in filter(None, untracked_raw.split(b"\0")):
        rel = os.fsdecode(raw)
        path = source / rel
        meta = path.lstat()
        if stat.S_ISLNK(meta.st_mode):
            payload = b"link\0" + os.fsencode(os.readlink(path))
        else:
            payload = b"file\0" + path.read_bytes()
        untracked_state.append(
            raw
            + b"\0"
            + oct(stat.S_IMODE(meta.st_mode)).encode("ascii")
            + b"\0"
            + payload
        )
    return (
        _git_bytes(source, "rev-parse", "HEAD"),
        _git_bytes(source, "status", "--porcelain=v2", "-z", "--untracked-files=all"),
        _git_bytes(source, "ls-files", "--stage", "-z"),
        _git_bytes(source, "diff", "--cached", "--binary", "--full-index", "HEAD", "--"),
        _git_bytes(source, "diff", "--binary", "--full-index", "--"),
        b"\0".join(sorted(untracked_state)),
    )


class RepoCase:
    def __init__(
        self,
        tmp_path: Path,
        *,
        main_shared: str = "base\n",
        candidate_shared: str | None = None,
    ) -> None:
        self.repo = tmp_path / "repo"
        self.source = tmp_path / "source"
        self.output = tmp_path / "replayed"
        self.repo.mkdir()
        _git(self.repo, "init", "-b", "main")
        _git(self.repo, "config", "user.name", "Row 407 Test")
        _git(self.repo, "config", "user.email", "row407@example.invalid")

        _write(self.repo / "shared.txt", "base\n")
        _write(self.repo / "main.txt", "old main\n")
        self.base_head = _commit(self.repo, "base")
        _git(self.repo, "worktree", "add", "-b", "worker", str(self.source), self.base_head)

        _write(self.repo / "main.txt", "new main\n")
        _write(self.repo / "shared.txt", main_shared)
        self.main_head = _commit(self.repo, "advance main")
        _git(self.repo, "update-ref", "refs/remotes/origin/main", self.main_head)

        _write(self.source / "candidate.txt", "candidate\n")
        if candidate_shared is not None:
            _write(self.source / "shared.txt", candidate_shared)
        self.source_head = _commit(self.source, "candidate")

    def run_replay(
        self,
        *,
        expect_head: str | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(self.repo),
            "--source",
            str(self.source),
            "--expect-head",
            expect_head or self.source_head,
            "--main-ref",
            "refs/remotes/origin/main",
            "--output",
            str(self.output),
            "--json",
        ]
        return _run(command, cwd=ROOT, check=False, env=env)


@pytest.fixture
def repo_case(tmp_path: Path) -> RepoCase:
    return RepoCase(tmp_path)


def test_committed_candidate_replays_onto_new_main_without_touching_source(
    repo_case: RepoCase,
) -> None:
    source_before = _source_snapshot(repo_case.source)

    result = repo_case.run_replay()

    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["source_head"] == repo_case.source_head
    assert body["main_sha"] == repo_case.main_head
    assert body["candidate_commits"] == [repo_case.source_head]
    assert Path(body["output"]) == repo_case.output.resolve()
    assert _source_snapshot(repo_case.source) == source_before
    assert (repo_case.output / "candidate.txt").read_text() == "candidate\n"
    assert (repo_case.output / "main.txt").read_text() == "new main\n"
    assert _git(repo_case.output, "rev-parse", "HEAD").stdout.strip() == body["replayed_head"]


def test_dirty_candidate_preserves_index_worktree_untracked_binary_exec_and_symlink(
    repo_case: RepoCase,
) -> None:
    mixed = repo_case.source / "mixed.txt"
    _write(mixed, "staged\n")
    _git(repo_case.source, "add", "mixed.txt")
    _write(mixed, "staged\nunstaged\n")

    _write(repo_case.source / "untracked.bin", b"\x00\xffrow407\n")
    executable = repo_case.source / "run-row407"
    _write(executable, "#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    os.symlink("missing-target", repo_case.source / "untracked-link")
    source_before = _source_snapshot(repo_case.source)

    result = repo_case.run_replay()

    assert result.returncode == 0, result.stderr
    assert _source_snapshot(repo_case.source) == source_before
    assert _git_bytes(
        repo_case.output,
        "diff",
        "--cached",
        "--binary",
        "--full-index",
        "HEAD",
        "--",
    ) == _git_bytes(
        repo_case.source,
        "diff",
        "--cached",
        "--binary",
        "--full-index",
        "HEAD",
        "--",
    )
    assert _git_bytes(
        repo_case.output, "diff", "--binary", "--full-index", "--"
    ) == _git_bytes(repo_case.source, "diff", "--binary", "--full-index", "--")
    assert (repo_case.output / "untracked.bin").read_bytes() == b"\x00\xffrow407\n"
    assert stat.S_IMODE((repo_case.output / "run-row407").stat().st_mode) == 0o755
    assert os.readlink(repo_case.output / "untracked-link") == "missing-target"


def test_cherry_pick_conflict_removes_output_and_preserves_exact_source_state(
    tmp_path: Path,
) -> None:
    case = RepoCase(tmp_path, main_shared="main\n", candidate_shared="candidate\n")
    source_before = _source_snapshot(case.source)

    result = case.run_replay()

    assert result.returncode == 3, (result.stdout, result.stderr)
    assert not case.output.exists()
    assert _source_snapshot(case.source) == source_before


def test_dirty_apply_conflict_removes_output_and_preserves_exact_source_state(
    tmp_path: Path,
) -> None:
    case = RepoCase(tmp_path, main_shared="main\n")
    _write(case.source / "shared.txt", "dirty candidate\n")
    source_before = _source_snapshot(case.source)

    result = case.run_replay()

    assert result.returncode == 3, (result.stdout, result.stderr)
    assert not case.output.exists()
    assert _source_snapshot(case.source) == source_before


def test_expected_head_mismatch_refuses_before_output_is_created(
    repo_case: RepoCase,
) -> None:
    source_before = _source_snapshot(repo_case.source)

    result = repo_case.run_replay(expect_head=repo_case.base_head)

    assert result.returncode == 2
    body = json.loads(result.stdout)
    assert body["status"] == "REFUSED"
    assert body["reason_code"] == "SOURCE_HEAD_MISMATCH"
    assert not repo_case.output.exists()
    assert _source_snapshot(repo_case.source) == source_before


def test_failed_worktree_creation_reaps_partial_output_and_preserves_source(
    repo_case: RepoCase,
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "failing-bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "git"
    wrapper.write_text(
        "#!/usr/bin/env python3\n"
        "import os, subprocess, sys\n"
        "args = sys.argv[1:]\n"
        "result = subprocess.run([os.environ['BD_REAL_GIT'], *args], check=False)\n"
        "if 'worktree' in args and 'add' in args and result.returncode == 0:\n"
        "    raise SystemExit(77)\n"
        "raise SystemExit(result.returncode)\n"
    )
    wrapper.chmod(0o755)
    env = dict(os.environ)
    env.update(
        BD_REAL_GIT=REAL_GIT,
        PATH=str(bin_dir) + os.pathsep + env.get("PATH", ""),
    )
    source_before = _source_snapshot(repo_case.source)

    result = repo_case.run_replay(env=env)

    assert result.returncode == 2
    body = json.loads(result.stdout)
    assert body["reason_code"] == "OUTPUT_CREATE_FAILED"
    assert not repo_case.output.exists()
    assert _source_snapshot(repo_case.source) == source_before


def test_source_worker_never_receives_a_destructive_git_command(
    repo_case: RepoCase,
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    git_log = tmp_path / "git-argv.jsonl"
    wrapper = bin_dir / "git"
    wrapper.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "with open(os.environ['BD_GIT_ARGV_LOG'], 'a', encoding='utf-8') as out:\n"
        "    out.write(json.dumps({\n"
        "        'argv': sys.argv[1:],\n"
        "        'optional_locks': os.environ.get('GIT_OPTIONAL_LOCKS'),\n"
        "    }) + '\\n')\n"
        "os.execv(os.environ['BD_REAL_GIT'], [os.environ['BD_REAL_GIT'], *sys.argv[1:]])\n"
    )
    wrapper.chmod(0o755)
    env = dict(os.environ)
    env.update(
        BD_GIT_ARGV_LOG=str(git_log),
        BD_REAL_GIT=REAL_GIT,
        PATH=str(bin_dir) + os.pathsep + env.get("PATH", ""),
    )

    result = repo_case.run_replay(env=env)

    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in git_log.read_text().splitlines()]
    source = str(repo_case.source.resolve())
    source_calls = [
        call
        for call in calls
        if len(call["argv"]) >= 3 and call["argv"][:2] == ["-C", source]
    ]
    assert source_calls, "the forwarding Git wrapper did not observe source reads"
    assert {call["optional_locks"] for call in source_calls} == {"0"}
    forbidden = {
        "add",
        "apply",
        "checkout",
        "cherry-pick",
        "rebase",
        "reset",
        "rm",
        "stash",
    }
    assert not [call for call in source_calls if call["argv"][2] in forbidden]
    output = str(repo_case.output.resolve())
    assert any(
        len(call["argv"]) >= 3
        and call["argv"][:2] == ["-C", output]
        and call["argv"][2] == "cherry-pick"
        for call in calls
    )
