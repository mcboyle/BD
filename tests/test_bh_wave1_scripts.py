"""BUGHUNT wave 1, BH-SCRIPTS -- four shell defects from FINDING-BH-bd-agy-audit-1.

001 deploy_fleet.sh dropped a final host line with no trailing newline, then
    reported "all N deployed" over the truncated denominator.
002 dev_capabilities.sh used $SUDO without defining it: a set -u caller that
    never set it died with "SUDO: unbound variable" mid-provision.
003 capture_instance.sh probed ports with a cwd-relative venv/bin/python, so
    from any other directory every port looked busy (exit 127 read as "taken").
006 bd-opv-check.sh / bd-opv-run.sh / bd-stash-report.sh wrote and rm -rf'd
    fixed /tmp paths, so concurrent or cross-user runs clobbered each other.

Every probe runs the real script with fakes (ssh, sudo, id, postgres tools) on
PATH; nothing here touches a real host, package manager or database.
"""
from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parents[1]
DEPLOY_FLEET = REPO / "scripts" / "deploy_fleet.sh"
DEV_CAPS = REPO / "scripts" / "lib" / "dev_capabilities.sh"
CAPTURE_INSTANCE = REPO / "scripts" / "lib" / "capture_instance.sh"
OPV_SCRIPTS = ("bd-opv-check.sh", "bd-opv-run.sh", "bd-stash-report.sh")
ALL_SCRIPTS = (DEPLOY_FLEET, DEV_CAPS, CAPTURE_INSTANCE) + tuple(
    REPO / "scripts" / name for name in OPV_SCRIPTS
)


def _fake(bindir: Path, name: str, body: str) -> None:
    path = bindir / name
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _env(bindir: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("SUDO", "CAPTURE_PORT_PROBE_PYTHON")}
    env["PATH"] = f"{bindir}:{env.get('PATH', '/usr/bin:/bin')}"
    env.update(extra)
    return env


@pytest.mark.parametrize("script", ALL_SCRIPTS, ids=lambda p: p.name)
def test_scripts_parse(script: Path) -> None:
    proc = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


# ---- 001 deploy_fleet.sh ------------------------------------------------------

def _deploy(tmp_path: Path, hosts: str, *args: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    tmp_path.mkdir(exist_ok=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "ssh.log"
    # deploy_fleet.sh calls: ssh -o BatchMode=yes -o ConnectTimeout=15 <addr> <cmd>
    _fake(bindir, "ssh", f'printf "%s\\n" "$5" >> "{log}"\necho "DEPLOY OK"\n')
    _fake(bindir, "hostname", "echo bh-wave1-not-a-fleet-host\n")
    hosts_file = tmp_path / "hosts"
    hosts_file.write_text(hosts, encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(DEPLOY_FLEET), "--hosts", str(hosts_file), *args],
        capture_output=True, text=True, env=_env(bindir), timeout=60, check=False,
    )
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return proc, calls


def test_deploy_fleet_deploys_final_host_without_trailing_newline(tmp_path: Path) -> None:
    proc, calls = _deploy(tmp_path, "host1 10.0.0.1\nhost2 10.0.0.2")
    assert proc.returncode == 0, proc.stderr
    assert "2 host(s)" in proc.stdout, proc.stdout
    assert calls == ["10.0.0.1", "10.0.0.2"], calls
    assert "all 2 host(s) deployed and verified" in proc.stdout


def test_deploy_fleet_dry_run_lists_final_host_without_trailing_newline(tmp_path: Path) -> None:
    proc, calls = _deploy(tmp_path, "host1 10.0.0.1\nhost2 10.0.0.2", "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert "host2" in proc.stdout and "2 host(s) listed" in proc.stdout, proc.stdout
    assert calls == []


def test_deploy_fleet_trailing_newline_count_unchanged(tmp_path: Path) -> None:
    # Negative control: the well-formed file parsed correctly before and after.
    proc, calls = _deploy(tmp_path, "host1 10.0.0.1\nhost2 10.0.0.2\n")
    assert proc.returncode == 0, proc.stderr
    assert len(calls) == 2


def test_deploy_fleet_unterminated_final_line_still_validated(tmp_path: Path) -> None:
    # The recovered last line must pass the same validation as every other:
    # a label with no address refuses, a comment is skipped.
    proc, calls = _deploy(tmp_path / "a", "host1 10.0.0.1\nhost2")
    assert proc.returncode == 2
    assert 'line for "host2" has no address' in proc.stderr
    assert calls == []
    proc, calls = _deploy(tmp_path / "b", "host1 10.0.0.1\n# host2 10.0.0.2")
    assert proc.returncode == 0, proc.stderr
    assert "1 host(s)" in proc.stdout


# ---- 002 dev_capabilities.sh ----------------------------------------------------

def _pg_provision(tmp_path: Path, uid: str, preset: str | None) -> tuple[subprocess.CompletedProcess[str], str]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    rec = f'printf "%s\\n" "$(basename "$0") $*" >> "{log}"\n'
    _fake(bindir, "id", f'[ "${{1:-}}" = "-u" ] && echo {uid} || command /usr/bin/id "$@"\n')
    _fake(bindir, "sudo", rec + 'exec "$@"\n')
    for tool in ("pg_ctlcluster", "su", "apt-get"):
        _fake(bindir, tool, rec + "exit 0\n")
    _fake(bindir, "pg_lsclusters", 'echo "16 main 5432 down postgres"\n')
    _fake(bindir, "pg_isready", "exit 1\n")
    _fake(bindir, "psql", "exit 1\n")
    home = tmp_path / "home"
    home.mkdir()
    setter = "" if preset is None else f"SUDO={preset!r}; "
    script = f'set -u; {setter}. "{DEV_CAPS}"; bd_mod3_pg_provision; echo "rc=$?"'
    proc = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True,
        env=_env(bindir, HOME=str(home)), timeout=60, check=False,
    )
    return proc, log.read_text(encoding="utf-8") if log.exists() else ""


def test_dev_capabilities_unset_sudo_non_root_uses_sudo(tmp_path: Path) -> None:
    proc, calls = _pg_provision(tmp_path, "1000", None)
    assert "unbound variable" not in proc.stderr, proc.stderr
    assert "sudo pg_ctlcluster 16 main start" in calls, calls
    # Reached the end of the function (the fake DSN never answers).
    assert "provisioned DSN did not answer" in proc.stdout and "rc=1" in proc.stdout


def test_dev_capabilities_unset_sudo_root_runs_direct(tmp_path: Path) -> None:
    proc, calls = _pg_provision(tmp_path, "0", None)
    assert "unbound variable" not in proc.stderr, proc.stderr
    assert "pg_ctlcluster 16 main start" in calls and "sudo" not in calls, calls


def test_dev_capabilities_caller_sudo_is_respected(tmp_path: Path) -> None:
    # Negative control: callers (cloud-setup.sh, provision_test_host.sh) set
    # SUDO themselves; an explicit empty value must not be replaced by "sudo".
    _proc, calls = _pg_provision(tmp_path, "1000", "")
    assert "pg_ctlcluster 16 main start" in calls and "sudo" not in calls, calls


# ---- 003 capture_instance.sh ------------------------------------------------------

def _port_is_free(cwd: Path, port: int) -> subprocess.CompletedProcess[str]:
    script = f'. "{CAPTURE_INSTANCE}"; bd_capture_port_is_free {port}; echo "rc=$?"'
    env = {k: v for k, v in os.environ.items() if k != "CAPTURE_PORT_PROBE_PYTHON"}
    return subprocess.run(["bash", "-c", script], cwd=cwd, capture_output=True,
                          text=True, env=env, timeout=30, check=False)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_capture_port_probe_works_outside_repo_root(tmp_path: Path) -> None:
    proc = _port_is_free(tmp_path, _free_port())
    assert "rc=0" in proc.stdout, (proc.stdout, proc.stderr)


def test_capture_port_probe_still_reports_busy_outside_repo_root(tmp_path: Path) -> None:
    # Negative control: the probe must still be able to say "taken".
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        proc = _port_is_free(tmp_path, int(s.getsockname()[1]))
    assert "rc=1" in proc.stdout, (proc.stdout, proc.stderr)


# ---- 006 opv/stash diagnostics output paths ----------------------------------------

def _run_opv(script: str, checkout: Path, tmpdir: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["TMPDIR"] = str(tmpdir)
    # A closed port: every probe fails fast and the scripts run to completion
    # (bd-opv-run.sh stops at its liveness check, after creating its output).
    return subprocess.run(
        ["bash", str(REPO / "scripts" / script), str(checkout), f"http://127.0.0.1:{_free_port()}"],
        capture_output=True, text=True, env=env, timeout=180, check=False,
    )


@pytest.mark.parametrize("script", OPV_SCRIPTS)
def test_opv_scripts_use_private_output_dirs(script: str, tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    (checkout / "bulk_downloader").mkdir(parents=True)
    (checkout / "bulk_downloader" / "__init__.py").write_text("", encoding="utf-8")
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    stem = script[:-3].replace("-", "_")

    first = _run_opv(script, checkout, tmpdir)
    dirs = sorted(p for p in tmpdir.iterdir() if p.is_dir())
    assert len(dirs) == 1 and dirs[0].name.startswith(stem), (
        [p.name for p in tmpdir.iterdir()], first.stdout[-2000:], first.stderr[-2000:])
    sentinel = dirs[0] / "SENTINEL-from-first-run"
    sentinel.write_text("peer data", encoding="utf-8")

    second = _run_opv(script, checkout, tmpdir)
    dirs2 = sorted(p for p in tmpdir.iterdir() if p.is_dir())
    assert len(dirs2) == 2, ([p.name for p in tmpdir.iterdir()], second.stderr[-2000:])
    assert sentinel.read_text(encoding="utf-8") == "peer data"
    # The path an operator is told to open/upload is the one the run wrote.
    new_dir = next(p for p in dirs2 if p != dirs[0])
    assert str(new_dir) in second.stdout, second.stdout[-2000:]
