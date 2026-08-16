"""Row 159: fleet provenance measures the intended checkout and fails closed."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import pathlib
import shlex
import subprocess


REPO = pathlib.Path(__file__).resolve().parents[1]
TOOL = REPO / "toolchain" / "bin" / "bd-fleet-run"
BD_GATE_SCOPE = "module"


def _load():
    loader = importlib.machinery.SourceFileLoader("bd_fleet_run_1158", str(TOOL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _git(repo: pathlib.Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def _repo(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str]:
    repo = tmp_path / "checkout"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Row 159 Test")
    _git(repo, "config", "user.email", "row159@example.invalid")
    (repo / "tracked.txt").write_text("original\n")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-q", "-m", "fixture")
    return repo, _git(repo, "rev-parse", "HEAD")


def _run_wrapped(mod, repo: pathlib.Path, command: str = "printf 'COMMAND_RAN\\n'"):
    wrapped = mod.wrap_command(command, True, str(repo))
    return subprocess.run(
        ["bash", "-c", wrapped], text=True, capture_output=True, check=False
    )


def test_default_path_measures_the_checkout_not_login_pwd(tmp_path):
    """Direct legacy reproduction: old code reports unknown/0 from cwd."""
    mod = _load()
    wrapped = mod.wrap_command("printf 'COMMAND_RAN\\n'", True)
    result = subprocess.run(
        ["bash", "-c", wrapped],
        cwd=tmp_path,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        text=True,
        capture_output=True,
        check=False,
    )
    expected_repo = pathlib.Path(mod.DEFAULT_REPO_DIR)
    expected_head = _git(expected_repo, "rev-parse", "HEAD")
    expected_dirty = "dirty" if _git(expected_repo, "status", "--porcelain") else "clean"
    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        f"bd-fleet-run: commit={expected_head} dirty={expected_dirty} repo={expected_repo}",
        "COMMAND_RAN",
    ]


def test_clean_provenance_is_bound_to_the_explicit_checkout(tmp_path):
    mod = _load()
    repo, head = _repo(tmp_path)
    result = _run_wrapped(mod, repo)
    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        f"bd-fleet-run: commit={head} dirty=clean repo={repo}",
        "COMMAND_RAN",
    ]


def test_dirty_checkout_is_distinct_from_clean(tmp_path):
    mod = _load()
    repo, head = _repo(tmp_path)
    (repo / "untracked.txt").write_text("dirty\n")
    result = _run_wrapped(mod, repo)
    assert result.returncode == 0
    assert result.stdout.splitlines()[0] == (
        f"bd-fleet-run: commit={head} dirty=dirty repo={repo}"
    )
    assert result.stdout.splitlines()[-1] == "COMMAND_RAN"


def test_inherited_git_repository_overrides_cannot_select_another_subject(tmp_path):
    mod = _load()
    intended_root = tmp_path / "intended-fixture"
    foreign_root = tmp_path / "foreign-fixture"
    intended_root.mkdir()
    foreign_root.mkdir()
    intended, intended_head = _repo(intended_root)
    foreign, _ = _repo(foreign_root)
    (foreign / "tracked.txt").write_text("foreign\n")
    _git(foreign, "commit", "-qam", "make foreign identity distinct")
    foreign_head = _git(foreign, "rev-parse", "HEAD")
    assert foreign_head != intended_head
    env = os.environ.copy()
    env.update({"GIT_DIR": str(foreign / ".git"), "GIT_WORK_TREE": str(intended)})
    wrapped = mod.wrap_command("printf 'COMMAND_RAN\\n'", True, str(intended))
    result = subprocess.run(
        ["bash", "-c", wrapped],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        f"bd-fleet-run: commit={intended_head} dirty=clean repo={intended}",
        "COMMAND_RAN",
    ]


def test_unpublishable_provenance_does_not_authorize_the_payload(tmp_path):
    mod = _load()
    repo, _ = _repo(tmp_path)
    marker = tmp_path / "payload-ran"
    probe = subprocess.run(
        ["bash", "-c", "printf probe >/dev/full"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert probe.returncode != 0
    wrapped = mod.wrap_command(
        f"printf ran > {shlex.quote(str(marker))}", True, str(repo)
    )
    with open("/dev/full", "wb", buffering=0) as full:
        result = subprocess.run(
            ["bash", "-c", wrapped],
            stdout=full,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    assert result.returncode != 0
    assert not marker.exists()
    assert "provenance publication failed" in result.stderr


def test_missing_checkout_reports_unknown_and_does_not_run_command(tmp_path):
    mod = _load()
    missing = tmp_path / "missing-checkout"
    result = _run_wrapped(mod, missing)
    assert result.returncode != 0
    assert result.stdout.splitlines() == [
        f"bd-fleet-run: commit=unknown dirty=unknown repo={missing}"
    ]
    assert "COMMAND_RAN" not in result.stdout


def test_nonrepository_reports_unknown_and_does_not_run_command(tmp_path):
    mod = _load()
    not_repo = tmp_path / "not-a-repository"
    not_repo.mkdir()
    result = _run_wrapped(mod, not_repo)
    assert result.returncode != 0
    assert result.stdout.splitlines() == [
        f"bd-fleet-run: commit=unknown dirty=unknown repo={not_repo}"
    ]
    assert "COMMAND_RAN" not in result.stdout


def test_symlinked_checkout_path_is_unknown_and_does_not_run_command(tmp_path):
    mod = _load()
    repo, _ = _repo(tmp_path)
    link = tmp_path / "checkout-link"
    link.symlink_to(repo, target_is_directory=True)
    result = _run_wrapped(mod, link)
    assert result.returncode != 0
    assert result.stdout.splitlines() == [
        f"bd-fleet-run: commit=unknown dirty=unknown repo={link}"
    ]
    assert "COMMAND_RAN" not in result.stdout


def test_git_status_failure_reports_unknown_and_does_not_run_command(tmp_path):
    mod = _load()
    repo = tmp_path / "checkout"
    repo.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "git-calls"
    fake_git = bindir / "git"
    fake_git.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> {calls}\n"
        "case \"$*\" in\n"
        f"  *--show-toplevel*) printf '%s\\n' {repo};;\n"
        "  *'rev-parse --verify HEAD'*) printf '%040d\\n' 0;;\n"
        "  *'status --porcelain=v1 --untracked-files=normal'*) exit 19;;\n"
        "  *) exit 20;;\n"
        "esac\n"
    )
    fake_git.chmod(0o755)
    wrapped = mod.wrap_command("printf 'COMMAND_RAN\\n'", True, str(repo))
    result = subprocess.run(
        ["bash", "-c", wrapped],
        env={"HOME": str(tmp_path), "PATH": f"{bindir}:/usr/bin:/bin"},
        text=True,
        capture_output=True,
        check=False,
    )
    fired = calls.read_text().splitlines()
    assert sum("status --porcelain=v1" in call for call in fired) == 1
    assert result.returncode != 0
    assert result.stdout.splitlines() == [
        f"bd-fleet-run: commit=unknown dirty=unknown repo={repo}"
    ]
    assert "COMMAND_RAN" not in result.stdout


def test_malformed_commit_measurement_does_not_run_command(tmp_path):
    mod = _load()
    repo = tmp_path / "checkout"
    repo.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake_git = bindir / "git"
    fake_git.write_text(
        "#!/bin/sh\n"
        "case \"$*\" in\n"
        f"  *--show-toplevel*) printf '%s\\n' {repo};;\n"
        "  *'rev-parse --verify HEAD'*) printf 'not-a-commit\\n';;\n"
        "  *'status --porcelain=v1 --untracked-files=normal'*) exit 0;;\n"
        "  *) exit 20;;\n"
        "esac\n"
    )
    fake_git.chmod(0o755)
    wrapped = mod.wrap_command("printf 'COMMAND_RAN\\n'", True, str(repo))
    result = subprocess.run(
        ["bash", "-c", wrapped],
        env={"HOME": str(tmp_path), "PATH": f"{bindir}:/usr/bin:/bin"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert result.stdout.splitlines() == [
        f"bd-fleet-run: commit=unknown dirty=unknown repo={repo}"
    ]
    assert "COMMAND_RAN" not in result.stdout


class _LogRunner:
    name = "row159-log-runner"

    def __init__(self, text: str, rc: int = 0):
        self.text = text
        self.rc = rc
        self.calls = 0

    def run(self, argv, log_path, timeout):
        self.calls += 1
        pathlib.Path(log_path).write_text(self.text)
        return self.rc, None


class _Probe:
    def local_head(self):
        return "local-head"


def _execute(mod, tmp_path: pathlib.Path, log_text: str, runner_rc: int = 0):
    hosts = tmp_path / "hosts"
    hosts.write_text("alpha 192.0.2.10\n")
    root = tmp_path / "runs"
    root.mkdir()
    runner = _LogRunner(log_text, rc=runner_rc)
    rc = mod.main(
        [
            "--hosts", str(hosts),
            "--root", str(root),
            "--repo-dir", "/srv/BulkDownloader",
            "--execute", "--", "true",
        ],
        runner=runner,
        probe=_Probe(),
    )
    run = next(p for p in root.iterdir() if p.is_dir())
    summary = json.loads((run / "summary.json").read_text())
    manifest = json.loads((run / "manifest.json").read_text())
    return rc, runner, summary[0], manifest


def test_missing_provenance_cannot_be_host_success(tmp_path):
    mod = _load()
    rc, runner, row, manifest = _execute(mod, tmp_path, "command output only\n")
    assert runner.calls == 1
    assert rc != 0
    assert row["status"] == "PROVENANCE_UNKNOWN"
    assert row["provenance"] == {
        "commit": "unknown", "dirty": "unknown", "repo": "/srv/BulkDownloader"
    }
    assert manifest["repo_dir"] == "/srv/BulkDownloader"


def test_malformed_or_unknown_provenance_cannot_be_host_success(tmp_path):
    mod = _load()
    for index, line in enumerate((
        "bd-fleet-run: commit=abc dirty=clean repo=/srv/BulkDownloader\n",
        "bd-fleet-run: commit=unknown dirty=unknown repo=/srv/BulkDownloader\n",
        "bd-fleet-run: commit=" + "a" * 40 + " dirty=clean repo=/wrong\n",
    )):
        case = tmp_path / str(index)
        case.mkdir()
        rc, runner, row, _ = _execute(mod, case, line)
        assert runner.calls == 1
        assert rc != 0
        assert row["status"] == "PROVENANCE_UNKNOWN"
        assert row["provenance"]["dirty"] == "unknown"


def test_valid_provenance_is_persisted_as_measured_facts(tmp_path):
    mod = _load()
    head = "b" * 40
    line = (
        f"bd-fleet-run: commit={head} dirty=dirty "
        "repo=/srv/BulkDownloader\npayload\n"
    )
    rc, runner, row, manifest = _execute(mod, tmp_path, line)
    assert runner.calls == 1
    assert rc == 0
    assert row["status"] == "ok"
    assert row["provenance"] == {
        "commit": head, "dirty": "dirty", "repo": "/srv/BulkDownloader"
    }
    assert manifest["record_provenance"] is True


def test_failed_payload_retains_valid_measured_provenance(tmp_path):
    mod = _load()
    head = "c" * 40
    line = (
        f"bd-fleet-run: commit={head} dirty=clean "
        "repo=/srv/BulkDownloader\npayload failed\n"
    )
    rc, runner, row, _ = _execute(mod, tmp_path, line, runner_rc=7)
    assert runner.calls == 1
    assert rc != 0
    assert row["status"] == "FAIL"
    assert row["exit"] == 7
    assert row["provenance"] == {
        "commit": head, "dirty": "clean", "repo": "/srv/BulkDownloader"
    }
