"""Row 407: candidate replay must never rewrite the source worker."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bd_candidate_replay.py"
REAL_GIT = shutil.which("git")
assert REAL_GIT is not None
BD_GATE_SCOPE = "module"


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
        commit_candidate: bool = True,
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
        self.source_head = (
            _commit(self.source, "candidate")
            if commit_candidate
            else self.base_head
        )

    @property
    def manifest(self) -> Path:
        return self.output.parent / f".{self.output.name}.bd-replay.json"

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
    assert Path(body["manifest"]) == repo_case.manifest.resolve()
    assert json.loads(repo_case.manifest.read_text())["state"] == "REPLAYED"
    assert Path(body["output"]) == repo_case.output.resolve()
    assert _source_snapshot(repo_case.source) == source_before
    assert (repo_case.output / "candidate.txt").read_text() == "candidate\n"
    assert (repo_case.output / "main.txt").read_text() == "new main\n"
    assert _git(repo_case.output, "rev-parse", "HEAD").stdout.strip() == body["replayed_head"]


def test_late_same_output_loser_refuses_on_winners_atomic_claim(
    repo_case: RepoCase,
) -> None:
    """A completed winner remains the claim authority before output inspection."""

    first = repo_case.run_replay()
    assert first.returncode == 0, (first.stdout, first.stderr)
    manifest_before = repo_case.manifest.read_bytes()
    output_head = _git(repo_case.output, "rev-parse", "HEAD").stdout.strip()

    second = repo_case.run_replay()

    assert second.returncode == 2
    body = json.loads(second.stdout)
    assert body["reason_code"] == "OUTPUT_CLAIMED"
    assert repo_case.manifest.read_bytes() == manifest_before
    assert _git(repo_case.output, "rev-parse", "HEAD").stdout.strip() == output_head


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


def test_partial_registration_without_output_is_identity_cleaned_or_claimed(
    repo_case: RepoCase,
    tmp_path: Path,
) -> None:
    """A missing directory does not prove Git left no registered worktree."""

    bin_dir = tmp_path / "registration-only-bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "git"
    wrapper.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, shutil, subprocess, sys\n"
        "args = sys.argv[1:]\n"
        "result = subprocess.run([os.environ['BD_REAL_GIT'], *args], check=False)\n"
        "if 'worktree' in args and 'add' in args and result.returncode == 0:\n"
        "    shutil.rmtree(pathlib.Path(args[-2]))\n"
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

    try:
        result = repo_case.run_replay(env=env)

        assert result.returncode == 2
        assert json.loads(result.stdout)["reason_code"] == "OUTPUT_CREATE_FAILED"
        assert not repo_case.output.exists()
        common = Path(
            _git(repo_case.repo, "rev-parse", "--git-common-dir").stdout.strip()
        )
        if not common.is_absolute():
            common = repo_case.repo / common
        registrations = common / "worktrees"
        registration_retained = any(
            str(repo_case.output) in receipt.read_text()
            for receipt in registrations.glob("*/gitdir")
        )
        claim_retained = repo_case.manifest.is_file()
        assert registration_retained is claim_retained
        assert not registration_retained, (
            "a fully identified partial registration should be cleaned; "
            "otherwise its claim must remain"
        )
        assert _source_snapshot(repo_case.source) == source_before
    finally:
        _git(
            repo_case.repo,
            "worktree",
            "remove",
            "--force",
            str(repo_case.output),
            check=False,
        )
        _git(repo_case.repo, "worktree", "prune", "--expire", "now", check=False)
        repo_case.manifest.unlink(missing_ok=True)


def test_preexisting_missing_worktree_registration_is_refused_and_preserved(
    repo_case: RepoCase,
) -> None:
    """A failed add cannot transfer ownership of an older Git registration."""

    _git(
        repo_case.repo,
        "worktree",
        "add",
        "--detach",
        str(repo_case.output),
        repo_case.main_head,
    )
    registration = Path(
        _git(
            repo_case.output,
            "rev-parse",
            "--path-format=absolute",
            "--absolute-git-dir",
        ).stdout.strip()
    )
    registration_identity = registration.lstat()
    receipt_before = (registration / "gitdir").read_bytes()
    retained = repo_case.output.with_name("preexisting-missing-output")
    repo_case.output.rename(retained)

    try:
        result = repo_case.run_replay()

        assert result.returncode == 2
        body = json.loads(result.stdout)
        assert body["reason_code"] == "OUTPUT_REGISTRATION_EXISTS"
        current = registration.lstat()
        assert (current.st_dev, current.st_ino) == (
            registration_identity.st_dev,
            registration_identity.st_ino,
        )
        assert (registration / "gitdir").read_bytes() == receipt_before
        assert retained.is_dir()
        assert not repo_case.manifest.exists()
    finally:
        if retained.exists() and not repo_case.output.exists():
            retained.rename(repo_case.output)
        _git(
            repo_case.repo,
            "worktree",
            "remove",
            "--force",
            str(repo_case.output),
            check=False,
        )
        repo_case.manifest.unlink(missing_ok=True)


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


def test_poisoned_git_environment_cannot_retarget_replay(
    repo_case: RepoCase,
    tmp_path: Path,
) -> None:
    """Dropping any scrubbed GIT_* key would retarget a real Git read."""

    poison = tmp_path / "poison"
    poison.mkdir()
    _git(poison, "init", "-b", "main")
    _git(poison, "config", "user.name", "Poison")
    _git(poison, "config", "user.email", "poison@example.invalid")
    _write(poison / "poison.txt", "not the candidate\n")
    _commit(poison, "poison")
    env = dict(os.environ)
    env.update(
        GIT_DIR=str(poison / ".git"),
        GIT_WORK_TREE=str(poison),
        GIT_INDEX_FILE=str(tmp_path / "poison.index"),
        GIT_OBJECT_DIRECTORY=str(poison / ".git" / "objects"),
    )

    result = repo_case.run_replay(env=env)

    assert result.returncode == 0, (result.stdout, result.stderr)
    body = json.loads(result.stdout)
    assert body["source_head"] == repo_case.source_head
    assert body["main_sha"] == repo_case.main_head
    assert (repo_case.output / "candidate.txt").read_text() == "candidate\n"
    assert not (repo_case.output / "poison.txt").exists()


def test_entirely_uncommitted_candidate_replays_onto_main_without_touching_source(
    tmp_path: Path,
) -> None:
    """An empty commit list must not drop the incident's uncommitted half."""

    case = RepoCase(tmp_path, commit_candidate=False)
    _write(case.source / "staged.txt", "staged\n")
    _git(case.source, "add", "staged.txt")
    _write(case.source / "staged.txt", "staged\nunstaged\n")
    source_before = _source_snapshot(case.source)

    result = case.run_replay(expect_head=case.base_head)

    assert result.returncode == 0, (result.stdout, result.stderr)
    body = json.loads(result.stdout)
    assert body["candidate_commits"] == []
    assert _source_snapshot(case.source) == source_before
    assert (case.output / "candidate.txt").read_text() == "candidate\n"
    assert (case.output / "staged.txt").read_text() == "staged\nunstaged\n"


def test_a_staged_gitlink_is_refused_instead_of_approximated(
    tmp_path: Path,
) -> None:
    """Removing the gitlink refusal would approximate a submodule as a patch."""

    case = RepoCase(tmp_path, commit_candidate=False)
    _git(
        case.source,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{case.base_head},nested-module",
    )
    source_before = _source_snapshot(case.source)

    result = case.run_replay(expect_head=case.base_head)

    assert result.returncode == 2
    body = json.loads(result.stdout)
    assert body["status"] == "REFUSED"
    assert body["reason_code"] == "SOURCE_HAS_SUBMODULE"
    assert not case.output.exists()
    assert not case.manifest.exists()
    assert _source_snapshot(case.source) == source_before


def test_intent_to_add_is_refused_instead_of_reclassified_as_unstaged(
    tmp_path: Path,
) -> None:
    """An intent-to-add index entry has state that a plain worktree patch loses."""

    case = RepoCase(tmp_path, commit_candidate=False)
    _write(case.source / "intent.txt", "intent content\n")
    _git(case.source, "add", "--intent-to-add", "intent.txt")
    source_before = _source_snapshot(case.source)

    result = case.run_replay(expect_head=case.base_head)

    assert result.returncode == 2
    body = json.loads(result.stdout)
    assert body["reason_code"] == "SOURCE_HAS_INTENT_TO_ADD"
    assert not case.output.exists()
    assert not case.manifest.exists()
    assert _source_snapshot(case.source) == source_before


def _load_replay_module():
    spec = importlib.util.spec_from_file_location("row407_candidate_replay", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_source_quiescence_requires_two_equal_complete_snapshots_before_claim(
    repo_case: RepoCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single preflight read can claim output while the source is changing."""

    subject = _load_replay_module()
    snapshots = iter(("snapshot-a", "snapshot-b"))
    monkeypatch.setattr(subject, "_fingerprint", lambda _source: next(snapshots))

    with pytest.raises(subject.ReplayFailure) as caught:
        subject.replay(
            repo=repo_case.repo,
            source=repo_case.source,
            expect_head=repo_case.source_head,
            main_ref="refs/remotes/origin/main",
            output=repo_case.output,
        )

    assert caught.value.reason_code == "SOURCE_NOT_QUIESCENT"
    assert not repo_case.output.exists()
    assert not repo_case.manifest.exists()


def test_claim_construction_cancellation_preserves_primary_and_records_retention(
    repo_case: RepoCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An incomplete token record is retained safely, but never silently."""

    class ClaimCancelled(BaseException):
        pass

    subject = _load_replay_module()
    cancellation = ClaimCancelled("cancel while constructing claim")

    def cancel_payload(*_args, **_kwargs):
        raise cancellation

    monkeypatch.setattr(subject, "_claim_payload", cancel_payload)

    with pytest.raises(ClaimCancelled) as caught:
        subject.replay(
            repo=repo_case.repo,
            source=repo_case.source,
            expect_head=repo_case.source_head,
            main_ref="refs/remotes/origin/main",
            output=repo_case.output,
        )

    assert caught.value is cancellation
    assert not repo_case.output.exists()
    assert repo_case.manifest.is_file()
    assert any("retained replay claim" in note for note in cancellation.__notes__)


@pytest.mark.parametrize(
    "fault",
    (OSError("injected replay I/O failure"), UnicodeError("injected Unicode failure")),
    ids=("ordinary-io", "unicode"),
)
def test_ordinary_fault_after_claim_rolls_back_owned_output_and_reraises_original(
    repo_case: RepoCase,
    monkeypatch: pytest.MonkeyPatch,
    fault: BaseException,
) -> None:
    """Catching only ReplayFailure strands output after ordinary local faults."""

    subject = _load_replay_module()
    source_before = _source_snapshot(repo_case.source)

    def fail_copy(*_args, **_kwargs):
        raise fault

    monkeypatch.setattr(subject, "_copy_untracked", fail_copy)

    with pytest.raises(type(fault), match="injected") as caught:
        subject.replay(
            repo=repo_case.repo,
            source=repo_case.source,
            expect_head=repo_case.source_head,
            main_ref="refs/remotes/origin/main",
            output=repo_case.output,
        )

    assert caught.value is fault
    assert not repo_case.output.exists()
    assert not repo_case.manifest.exists()
    assert _source_snapshot(repo_case.source) == source_before


def test_cancellation_after_claim_rolls_back_then_preserves_the_same_baseexception(
    repo_case: RepoCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Narrow Exception cleanup strands output when its owner is cancelled."""

    class ReplayCancelled(BaseException):
        pass

    subject = _load_replay_module()
    cancellation = ReplayCancelled("injected cancellation")
    source_before = _source_snapshot(repo_case.source)

    def cancel_copy(*_args, **_kwargs):
        raise cancellation

    monkeypatch.setattr(subject, "_copy_untracked", cancel_copy)

    with pytest.raises(ReplayCancelled) as caught:
        subject.replay(
            repo=repo_case.repo,
            source=repo_case.source,
            expect_head=repo_case.source_head,
            main_ref="refs/remotes/origin/main",
            output=repo_case.output,
        )

    assert caught.value is cancellation
    assert not repo_case.output.exists()
    assert not repo_case.manifest.exists()
    assert _source_snapshot(repo_case.source) == source_before


def test_rollback_retains_output_when_claim_path_inode_is_replaced(
    repo_case: RepoCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A matching path or token cannot authorize unlinking a replacement inode."""

    subject = _load_replay_module()
    original_claim = repo_case.manifest.with_name("owned-claim.json")
    replacement_bytes = b'{"state":"unowned-replacement"}\n'
    injected = OSError("injected failure after claim replacement")

    def replace_claim_then_fail(*_args, **_kwargs):
        repo_case.manifest.rename(original_claim)
        repo_case.manifest.write_bytes(replacement_bytes)
        raise injected

    monkeypatch.setattr(subject, "_copy_untracked", replace_claim_then_fail)

    try:
        with pytest.raises(OSError) as caught:
            subject.replay(
                repo=repo_case.repo,
                source=repo_case.source,
                expect_head=repo_case.source_head,
                main_ref="refs/remotes/origin/main",
                output=repo_case.output,
            )

        assert caught.value is injected
        assert repo_case.output.is_dir(), "unproved output ownership must be retained"
        assert repo_case.manifest.read_bytes() == replacement_bytes
        assert original_claim.is_file()
    finally:
        _git(repo_case.repo, "worktree", "remove", "--force", str(repo_case.output), check=False)
        for path in (repo_case.manifest, original_claim):
            path.unlink(missing_ok=True)


def test_rollback_retains_output_when_claim_token_is_replaced_in_place(
    repo_case: RepoCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The held manifest inode alone does not authorize deleting an output."""

    subject = _load_replay_module()
    injected = OSError("injected failure after claim token replacement")

    def replace_token_then_fail(*_args, **_kwargs):
        payload = json.loads(repo_case.manifest.read_text())
        payload["token"] = "0" * 64
        repo_case.manifest.write_text(json.dumps(payload, sort_keys=True))
        raise injected

    monkeypatch.setattr(subject, "_copy_untracked", replace_token_then_fail)

    try:
        with pytest.raises(OSError) as caught:
            subject.replay(
                repo=repo_case.repo,
                source=repo_case.source,
                expect_head=repo_case.source_head,
                main_ref="refs/remotes/origin/main",
                output=repo_case.output,
            )

        assert caught.value is injected
        assert repo_case.output.is_dir(), "token drift must retain replay output"
        assert repo_case.manifest.is_file(), "token drift must retain the claim"
        assert any("claim token changed" in note for note in injected.__notes__)
    finally:
        _git(repo_case.repo, "worktree", "remove", "--force", str(repo_case.output), check=False)
        repo_case.manifest.unlink(missing_ok=True)


def test_rollback_retains_replacement_output_inode(
    repo_case: RepoCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A loser must never remove a directory that replaced its exact output inode."""

    subject = _load_replay_module()
    retained = repo_case.output.with_name("owned-output")
    injected = OSError("injected failure after output replacement")

    def replace_output_then_fail(*_args, **_kwargs):
        repo_case.output.rename(retained)
        repo_case.output.mkdir()
        (repo_case.output / "replacement.txt").write_text("winner\n")
        raise injected

    monkeypatch.setattr(subject, "_copy_untracked", replace_output_then_fail)

    try:
        with pytest.raises(OSError) as caught:
            subject.replay(
                repo=repo_case.repo,
                source=repo_case.source,
                expect_head=repo_case.source_head,
                main_ref="refs/remotes/origin/main",
                output=repo_case.output,
            )

        assert caught.value is injected
        assert (repo_case.output / "replacement.txt").read_text() == "winner\n"
        assert retained.is_dir(), "the transaction-owned output must be retained"
        assert repo_case.manifest.is_file(), "uncertain output ownership retains claim"
        assert any("output final-path" in note for note in injected.__notes__)
    finally:
        shutil.rmtree(repo_case.output, ignore_errors=True)
        if retained.exists():
            retained.rename(repo_case.output)
        _git(repo_case.repo, "worktree", "remove", "--force", str(repo_case.output), check=False)
        repo_case.manifest.unlink(missing_ok=True)


def _wait_until(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_concurrent_same_output_has_one_owner_and_loser_never_removes_winner(
    repo_case: RepoCase,
    tmp_path: Path,
) -> None:
    """output.exists() is evidence of presence, never transaction ownership."""

    bin_dir = tmp_path / "barrier-bin"
    markers = tmp_path / "markers"
    bin_dir.mkdir()
    markers.mkdir()
    git_log = tmp_path / "git-argv.jsonl"
    wrapper = bin_dir / "git"
    wrapper.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, time, sys\n"
        "args = sys.argv[1:]\n"
        "root = pathlib.Path(os.environ['BD_BARRIER_ROOT'])\n"
        "with open(os.environ['BD_GIT_ARGV_LOG'], 'a', encoding='utf-8') as out:\n"
        "    out.write(json.dumps({'pid': os.getpid(), 'argv': args}) + '\\n')\n"
        "if 'worktree' in args and 'add' in args:\n"
        "    (root / ('add-' + str(os.getpid()))).write_text('ready')\n"
        "    while not (root / 'release').exists():\n"
        "        time.sleep(0.01)\n"
        "os.execv(os.environ['BD_REAL_GIT'], "
        "[os.environ['BD_REAL_GIT'], *args])\n"
    )
    wrapper.chmod(0o755)
    env = dict(os.environ)
    env.update(
        BD_BARRIER_ROOT=str(markers),
        BD_GIT_ARGV_LOG=str(git_log),
        BD_REAL_GIT=REAL_GIT,
        PATH=str(bin_dir) + os.pathsep + env.get("PATH", ""),
    )
    command = [
        sys.executable,
        str(SCRIPT),
        "--repo",
        str(repo_case.repo),
        "--source",
        str(repo_case.source),
        "--expect-head",
        repo_case.source_head,
        "--main-ref",
        "refs/remotes/origin/main",
        "--output",
        str(repo_case.output),
        "--json",
    ]
    first = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert _wait_until(lambda: bool(list(markers.glob("add-*"))))
    second = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert _wait_until(
        lambda: second.poll() is not None
        or len(list(markers.glob("add-*"))) == 2
    )
    (markers / "release").write_text("go\n")
    first_stdout, first_stderr = first.communicate(timeout=20)
    second_stdout, second_stderr = second.communicate(timeout=20)
    results = [
        (first.returncode, json.loads(first_stdout), first_stderr),
        (second.returncode, json.loads(second_stdout), second_stderr),
    ]

    assert sorted(result[0] for result in results) == [0, 2], results
    assert sum(result[1]["status"] == "REPLAYED" for result in results) == 1
    refusal = next(result[1] for result in results if result[0] == 2)
    assert refusal["reason_code"] == "OUTPUT_CLAIMED"
    assert repo_case.output.is_dir()
    assert repo_case.manifest.is_file()
    calls = [json.loads(line) for line in git_log.read_text().splitlines()]
    assert not [
        call
        for call in calls
        if "worktree" in call["argv"] and "remove" in call["argv"]
    ]


# ── Row 480: ownership is created-by-this-transaction ───────────────────────
#
# The module docstring states the contract -- a failed replay removes only that
# new output -- and it did not hold. replay() captured ownership BEFORE it
# tested the worktree add's return code, so an add that failed because a
# FOREIGN creator had registered a linked worktree of the same repo at --output
# during the window recorded THAT directory's inode as this transaction's
# output. The identity guard then compared the live inode against the identity
# it had just captured from the foreign tree, passed, and rollback ran
# `git worktree remove --force` on another worker's tree.
#
# A second bd_candidate_replay cannot be the victim: the O_EXCL claim makes the
# loser refuse OUTPUT_CLAIMED before the add. The reachable adversary is any
# creator that does not take that claim -- a bare `git worktree add`, another
# harness, or the operator.
#
# The window was not narrow. Between the pre-checks and the add the tool runs
# three complete fingerprint passes, each reading every untracked file's bytes.
# It is narrow now: the path is re-probed one stat before the add, and that
# probe is the discriminator.

def test_a_foreign_worktree_created_inside_the_window_is_never_removed(
    repo_case: RepoCase,
    tmp_path: Path,
) -> None:
    """RED on the defective parent.

    The foreign tree must appear INSIDE the window -- between the pre-checks
    that prove the output path is free and the `worktree add` itself -- or the
    pre-checks catch it and nothing is measured. A PATH wrapper does exactly
    that: it passes every other git invocation through, and on the single
    `worktree add` it first has a DISTINCT process create a genuine linked
    worktree of the same repo at --output, then returns real git's own refusal.
    """
    bin_dir = tmp_path / "racing-bin"
    bin_dir.mkdir()
    marker = tmp_path / "race-fired"
    wrapper = bin_dir / "git"
    wrapper.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, subprocess, sys\n"
        "args = sys.argv[1:]\n"
        "real = os.environ['BD_REAL_GIT']\n"
        "marker = pathlib.Path(os.environ['BD_RACE_MARKER'])\n"
        "out = os.environ['BD_RACE_OUTPUT']\n"
        "repo = os.environ['BD_RACE_REPO']\n"
        "if 'worktree' in args and 'add' in args and not marker.exists():\n"
        "    marker.write_text('1')\n"
        # A DISTINCT PROCESS, so nothing about this is the tool's own doing.
        "    subprocess.run([real, '-C', repo, 'worktree', 'add', '--detach',\n"
        "                    out, 'HEAD'], check=True,\n"
        "                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "    pathlib.Path(out, 'main.txt').write_text('foreign worker edit\\n')\n"
        "    pathlib.Path(out, 'foreign-untracked.txt').write_text(\n"
        "        'foreign untracked bytes\\n')\n"
        "result = subprocess.run([real, *args], check=False)\n"
        "raise SystemExit(result.returncode)\n"
    )
    wrapper.chmod(0o755)
    env = dict(os.environ)
    env.update(
        BD_REAL_GIT=REAL_GIT,
        BD_RACE_MARKER=str(marker),
        BD_RACE_OUTPUT=str(repo_case.output),
        BD_RACE_REPO=str(repo_case.repo),
        PATH=str(bin_dir) + os.pathsep + env.get("PATH", ""),
    )

    # Preconditions: the path is free and this transaction owns nothing yet.
    assert not repo_case.output.exists()

    result = repo_case.run_replay(env=env)

    assert marker.exists(), (
        "the race never fired, so the foreign tree was created outside the "
        "window and the pre-checks -- not the fix -- would explain any refusal")
    assert repo_case.output.is_dir(), (
        "the foreign worktree directory was REMOVED by a rollback that had "
        "captured its inode as this transaction's own output")
    tracked = repo_case.output / "main.txt"
    untracked = repo_case.output / "foreign-untracked.txt"
    assert tracked.is_file() and untracked.is_file(), (
        "another worker's tracked and untracked files were destroyed")
    assert tracked.read_text(encoding="utf-8") == "foreign worker edit\n"
    assert untracked.read_text(encoding="utf-8") == "foreign untracked bytes\n"

    assert result.returncode == 2, result.stdout + result.stderr
    body = json.loads(result.stdout)
    assert body["reason_code"] == "OUTPUT_FOREIGN_AT_PATH", (
        f"the foreign case reports {body.get('reason_code')!r}; a code distinct "
        "from the created-then-failed case is the point, because the two "
        "diagnoses -- reap my own partial output, versus somebody else's tree "
        "is in the way -- lead to opposite actions")


# ── Row 500: --expect-head must be a value the CALLER supplied ──────────────

def test_expect_head_may_not_be_a_revision_resolved_in_the_source(
    repo_case: RepoCase,
) -> None:
    """Both sides used to be resolved inside the one worktree the argument
    exists to certify, so the comparison was tautological for anything derived
    from the source."""
    for tautology in ("HEAD", "@", "HEAD^{commit}"):
        result = repo_case.run_replay(expect_head=tautology)
        assert result.returncode != 0, (
            f"--expect-head {tautology!r} certified the source against itself")
        body = json.loads(result.stdout)
        assert body["reason_code"] == "EXPECTED_HEAD_NOT_LITERAL", body


def test_expect_head_still_accepts_the_literal_object_name(
    repo_case: RepoCase,
) -> None:
    """POSITIVE CONTROL: the real certification path must keep working, or the
    refusal above has traded a tautology for a lockout."""
    result = repo_case.run_replay(expect_head=repo_case.source_head)
    assert result.returncode == 0, result.stdout + result.stderr


def test_expect_head_refuses_a_wrong_literal(repo_case: RepoCase) -> None:
    """NEGATIVE CONTROL: a full object name that is not the source HEAD is still
    a mismatch, so the shape check has not replaced the comparison."""
    wrong = "0" * 40
    result = repo_case.run_replay(expect_head=wrong)
    assert result.returncode != 0
    body = json.loads(result.stdout)
    assert body["reason_code"] == "SOURCE_HEAD_MISMATCH", body
