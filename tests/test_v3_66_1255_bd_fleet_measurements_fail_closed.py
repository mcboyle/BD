"""Row 250: bd-fleet measurements fail closed like row 159's fleet runner."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "toolchain" / "bin" / "bd-fleet"
BD_GATE_SCOPE = "module"


def _load():
    loader = importlib.machinery.SourceFileLoader("bd_fleet_1255", str(TOOL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def _repo(home: Path) -> tuple[Path, str]:
    repo = home / "BulkDownloader"
    (repo / "bulk_downloader").mkdir(parents=True)
    (repo / "tools").mkdir()
    (repo / "toolchain" / "bin").mkdir(parents=True)
    (repo / "bulk_downloader" / "__init__.py").write_text(
        '__version__ = "3.66.1255"\n', encoding="ascii"
    )
    (repo / "tools" / "deployed_version.txt").write_text(
        "3.66.1255\n", encoding="ascii"
    )
    jobs = repo / "toolchain" / "bin" / "bd-jobs"
    jobs.write_text(
        "#!/bin/sh\nprintf '0 live, 0 stale on fixture\\n'\n", encoding="ascii"
    )
    jobs.chmod(0o755)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Row 250 Test")
    _git(repo, "config", "user.email", "row250@example.invalid")
    _git(repo, "add", "bulk_downloader/__init__.py", "tools/deployed_version.txt",
         "toolchain/bin/bd-jobs")
    _git(repo, "commit", "-q", "-m", "fixture")
    return repo, _git(repo, "rev-parse", "HEAD")


def _run_probe(home: Path, *, extra_env: dict[str, str] | None = None):
    module = _load()
    env = os.environ.copy()
    env.update({"HOME": str(home), "PATH": "/usr/bin:/bin"})
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        ["bash", "-c", module.PROBE],
        cwd=home,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return module, result, module.parse_probe(result.stdout)


def _failing_commands(tmp_path: Path) -> Path:
    bindir = tmp_path / "failing-bin"
    bindir.mkdir()
    for name in ("cut", "find", "ls", "nproc", "ps", "systemctl"):
        command = bindir / name
        command.write_text("#!/bin/sh\nexit 19\n", encoding="ascii")
        command.chmod(0o755)
    return bindir


def _git_with_failing_status(tmp_path: Path) -> tuple[Path, str]:
    bindir = tmp_path / "git-fails-status"
    bindir.mkdir()
    fake_git = bindir / "git"
    fake_git.write_text(
        "#!/bin/sh\n"
        "case \" $* \" in\n"
        "  *' status --porcelain=v1 '*) exit 19 ;;\n"
        "esac\n"
        "exec \"$BD_FLEET_REAL_GIT\" \"$@\"\n",
        encoding="ascii",
    )
    fake_git.chmod(0o755)
    real_git = subprocess.run(
        ["sh", "-c", "command -v git"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    assert real_git
    return bindir, real_git


def _active_systemctl(tmp_path: Path) -> Path:
    bindir = tmp_path / "active-systemctl"
    bindir.mkdir()
    systemctl = bindir / "systemctl"
    systemctl.write_text("#!/bin/sh\nprintf 'active\\n'\n", encoding="ascii")
    systemctl.chmod(0o755)
    return bindir


def _find_with_vanishing_entry(tmp_path: Path) -> Path:
    """Emulate GNU find losing one readdir result before it can stat it."""
    bindir = tmp_path / "racing-find"
    bindir.mkdir()
    find = bindir / "find"
    find.write_text(
        "#!/bin/sh\n"
        "printf 'M\\n'\n"
        "printf \"find: '/tmp/bd-fleet-vanished': No such file or directory\\n\" >&2\n"
        "exit 1\n",
        encoding="ascii",
    )
    find.chmod(0o755)
    return bindir


def _find_that_really_fails(tmp_path: Path) -> Path:
    bindir = tmp_path / "failed-find"
    bindir.mkdir()
    find = bindir / "find"
    find.write_text(
        "#!/bin/sh\n"
        "printf 'M\\n'\n"
        "printf \"find: '/tmp': Permission denied\\n\" >&2\n"
        "exit 1\n",
        encoding="ascii",
    )
    find.chmod(0o755)
    return bindir


def test_unresolvable_checkout_and_failed_counts_are_unknown_not_clean(tmp_path):
    """The old probe emitted head='', version='', dirty=0 and pytest=0."""
    checkout = tmp_path / "BulkDownloader"
    checkout.mkdir()
    assert checkout.is_dir() and not (checkout / ".git").exists()
    bindir = _failing_commands(tmp_path)

    module, result, fields = _run_probe(
        tmp_path, extra_env={"PATH": f"{bindir}:/usr/bin:/bin"}
    )

    assert result.returncode == 0, result.stderr
    assert fields, "the real probe emitted no key/value evidence"
    expected_unknown = {
        "tree",
        "head",
        "version",
        "serving",
        "branch",
        "dirty",
        "service",
        "load",
        "cores",
        "pytest",
        "tmp_bd",
    }
    assert expected_unknown <= fields.keys(), fields
    assert {fields[key] for key in expected_unknown} == {"unknown"}, fields
    rendered = "\n".join(
        module.render([("fixture", "local", fields, None)])
    )
    assert "unknown" in rendered.lower()
    assert " clean" not in rendered.lower()


def test_clean_dirty_and_unknown_are_three_reachable_distinct_states(tmp_path):
    clean_home = tmp_path / "clean-home"
    dirty_home = tmp_path / "dirty-home"
    unknown_home = tmp_path / "unknown-home"
    clean_home.mkdir()
    dirty_home.mkdir()
    unknown_home.mkdir()
    clean_repo, clean_head = _repo(clean_home)
    dirty_repo, dirty_head = _repo(dirty_home)
    (dirty_repo / "untracked.txt").write_text("dirty\n", encoding="ascii")
    (unknown_home / "BulkDownloader").mkdir()
    bindir = _active_systemctl(tmp_path)
    success_env = {"PATH": f"{bindir}:/usr/bin:/bin"}

    module, clean_result, clean = _run_probe(clean_home, extra_env=success_env)
    _, dirty_result, dirty = _run_probe(dirty_home, extra_env=success_env)
    _, unknown_result, unknown = _run_probe(unknown_home, extra_env=success_env)

    assert clean_result.returncode == dirty_result.returncode == unknown_result.returncode == 0
    assert clean["head"] == clean_head
    assert dirty["head"] == dirty_head
    assert clean["version"] == clean["serving"] == "3.66.1255"
    assert clean["branch"] != "unknown"
    assert clean["service"] == "active"
    assert clean["pytest"].isdigit()
    assert clean["tmp_bd"].isdigit()
    assert clean["jobs"] == "0" and clean["jobs_state"] == "OK"
    assert module.unknown_measurements(clean) == []
    assert module.unknown_measurements(dirty) == []
    assert clean_repo.is_dir() and dirty_repo.is_dir()
    states = (clean["dirty"], dirty["dirty"], unknown["dirty"])
    assert states == ("clean", "dirty", "unknown")
    assert len(set(states)) == 3


def test_tmp_count_survives_an_entry_vanishing_mid_walk(tmp_path):
    """A child ENOENT is a slightly stale count, not a failed measurement."""
    home = tmp_path / "home"
    home.mkdir()
    _repo(home)
    bindir = _find_with_vanishing_entry(tmp_path)

    _, result, fields = _run_probe(
        home, extra_env={"PATH": f"{bindir}:/usr/bin:/bin"}
    )

    assert result.returncode == 0, result.stderr
    assert fields["tmp_bd"] == "1", fields


def test_tmp_count_real_failure_remains_unknown(tmp_path):
    """Negative control: suppressing deletion races must not fail open."""
    home = tmp_path / "home"
    home.mkdir()
    _repo(home)
    bindir = _find_that_really_fails(tmp_path)

    module, result, fields = _run_probe(
        home, extra_env={"PATH": f"{bindir}:/usr/bin:/bin"}
    )

    assert result.returncode == 0, result.stderr
    assert fields["tmp_bd"] == "unknown", fields
    assert "tmp_bd" in module.unknown_measurements(fields)


def test_git_status_failure_reaches_dirty_unknown_not_clean(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    _repo(home)
    bindir, real_git = _git_with_failing_status(tmp_path)

    module, result, fields = _run_probe(
        home,
        extra_env={
            "PATH": f"{bindir}:/usr/bin:/bin",
            "BD_FLEET_REAL_GIT": real_git,
        },
    )

    assert result.returncode == 0, result.stderr
    assert fields["tree"] == "present", fields
    assert fields["head"] != "unknown", fields
    assert fields["dirty"] == "unknown", fields
    assert module.unknown_measurements(fields).count("dirty") == 1
    rendered = "\n".join(module.render([("fixture", "local", fields, None)]))
    assert "unknown" in rendered.lower()
    assert " clean" not in rendered.lower()


def test_fleet_refuses_a_reachable_host_with_unknown_measurements(
    tmp_path, monkeypatch, capsys
):
    module = _load()
    (tmp_path / "BulkDownloader").mkdir()
    hosts = tmp_path / "hosts"
    hostname = subprocess.run(
        ["hostname"], text=True, capture_output=True, check=True
    ).stdout.strip()
    assert hostname
    hosts.write_text(f"{hostname} local\n", encoding="ascii")
    monkeypatch.setenv("HOME", str(tmp_path))
    args = type("Args", (), {"hosts": str(hosts), "timeout": 30})()

    rc = module.run(args)
    captured = capsys.readouterr()

    assert rc == 1
    assert "unknown" in captured.out.lower()
    data_row = next(line for line in captured.out.splitlines()
                    if line.startswith(hostname))
    assert data_row.split()[5] == "unknown", data_row
    assert "measurements are UNKNOWN" in captured.err
    assert "dirty" in captured.err


def test_inherited_git_selectors_cannot_redirect_the_checkout(tmp_path):
    intended_home = tmp_path / "intended-home"
    foreign_home = tmp_path / "foreign-home"
    intended_home.mkdir()
    foreign_home.mkdir()
    intended, intended_head = _repo(intended_home)
    foreign, foreign_head = _repo(foreign_home)
    (foreign / "foreign.txt").write_text("dirty\n", encoding="ascii")
    assert intended_head == _git(intended, "rev-parse", "HEAD")
    assert foreign_head == _git(foreign, "rev-parse", "HEAD")

    _, result, fields = _run_probe(
        intended_home,
        extra_env={"GIT_DIR": str(foreign / ".git"), "GIT_WORK_TREE": str(foreign)},
    )

    assert result.returncode == 0, result.stderr
    assert fields["head"] == intended_head
    assert fields["dirty"] == "clean"


def test_transform_control_runs_the_mutated_probe_without_judging_dirty_state(tmp_path):
    """The mutation control proves collection and shell execution remain valid."""
    home = tmp_path / "home"
    home.mkdir()
    _repo(home)
    bindir, real_git = _git_with_failing_status(tmp_path)
    _, result, fields = _run_probe(
        home,
        extra_env={
            "PATH": f"{bindir}:/usr/bin:/bin",
            "BD_FLEET_REAL_GIT": real_git,
        },
    )
    assert result.returncode == 0, result.stderr
    assert fields["tree"] == "present", fields
    assert len(fields) >= 10, fields
