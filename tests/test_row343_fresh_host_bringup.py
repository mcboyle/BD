"""Row 343: a fresh host must complete the documented bring-up in one run.

The three regressions here are state- and host-boundary defects.  The cloud
tests therefore execute the shipped setup script with instrumented host
commands, while the documentation tests execute the fenced runbook commands
against separate old/new host roots.  Text mentions are not accepted as
evidence that a package was installed, an archive crossed hosts, or a configured
directory was created.
"""
from __future__ import annotations

import json
import os
import sys
import re
import shutil
import subprocess
from pathlib import Path


BD_GATE_SCOPE = "repo-wide"

REPO_ROOT = Path(__file__).resolve().parents[1]
CLOUD_SETUP = REPO_ROOT / "scripts" / "cloud-setup.sh"
RUNBOOK = REPO_ROOT / "docs" / "repo" / "FRESH_HOST_BRINGUP.md"


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _cloud_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Build the minimum checkout/host needed by the real cloud setup script."""
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    state = tmp_path / "python312-venv.installed"
    events = tmp_path / "events.log"
    for directory in (
        repo / "bulk_downloader",
        repo / "scripts" / "lib",
        repo / "frontend",
        repo / "tools",
        repo / "toolchain" / "bin",
        repo / "reports",
        home,
        bindir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    (repo / "bulk_downloader" / "__init__.py").write_text(
        '__version__ = "0.0.0"\n', encoding="utf-8"
    )
    for manifest in (
        "requirements.txt",
        "requirements-test.txt",
        "requirements-cloak.txt",
        "requirements-optional.txt",
    ):
        (repo / manifest).write_text("", encoding="utf-8")
    shutil.copy2(
        REPO_ROOT / "scripts" / "lib" / "system_deps.sh",
        repo / "scripts" / "lib" / "system_deps.sh",
    )

    _write_executable(
        bindir / "python3.12",
        r'''#!/bin/bash
set -u
name="${0##*/}"
if [ "$name" = pip ]; then
  exit 0
fi
if [ "${1:-}" = "--version" ]; then
  echo "Python 3.12.0"
  exit 0
fi
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "venv" ]; then
  printf 'VENV_ATTEMPT\n' >> "$BD_TEST_EVENTS"
  if [ ! -f "$BD_TEST_VENV_STATE" ]; then
    echo "SIMULATED: ensurepip unavailable until python3.12-venv is installed" >&2
    exit 71
  fi
  target="${3:?venv target missing}"
  mkdir -p "$target/bin" "$target/lib/python3.12/site-packages"
  ln -sf "$0" "$target/bin/python"
  ln -sf "$0" "$target/bin/pip"
  exit 0
fi
if [ "${1:-}" = "-c" ]; then
  case "${2:-}" in
    *bulk_downloader*__version__*) echo "0.0.0" ;;
    *site.getsitepackages*)
      mkdir -p "$PWD/venv/lib/python3.12/site-packages"
      echo "$PWD/venv/lib/python3.12/site-packages"
      ;;
    *gui_parity_inventory*|*route_source*)
      "$BD_TEST_REAL_PYTHON" -c "$2"
      exit $?
      ;;
  esac
  exit 0
fi
exit 0
''',
    )
    _write_executable(
        bindir / "apt-get",
        r'''#!/bin/bash
set -u
printf 'APT %s\n' "$*" >> "$BD_TEST_EVENTS"
case " $* " in
  *" python3.12-venv "*)
    printf 'APT_CORE\n' >> "$BD_TEST_EVENTS"
    if [ "${BD_TEST_APT_FAIL:-0}" = 1 ]; then
      echo "SIMULATED: core package install unavailable" >&2
      exit 72
    fi
    : > "$BD_TEST_VENV_STATE"
    ;;
esac
exit 0
''',
    )
    _write_executable(
        bindir / "sudo",
        '#!/bin/bash\nexec "$@"\n',
    )
    _write_executable(
        bindir / "git",
        r'''#!/bin/bash
case "${1:-}" in
  rev-parse) echo 0000000000000000000000000000000000000000 ;;
esac
exit 0
''',
    )
    _write_executable(
        bindir / "npm",
        r'''#!/bin/bash
if [ "${1:-}" = run ] && [ "${2:-}" = build ]; then
  mkdir -p dist
  printf '<div id="root"></div>\n' > dist/index.html
fi
exit 0
''',
    )
    for command, status in (
        ("curl", 1),
        ("ip", 1),
        ("psql", 1),
        ("xdpyinfo", 0),
    ):
        _write_executable(bindir / command, f"#!/bin/bash\nexit {status}\n")
    return repo, home, state, events


def _run_cloud_setup(
    tmp_path: Path, *, package_preinstalled: bool = False, apt_fails: bool = False
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    repo, home, state, events = _cloud_fixture(tmp_path)
    if package_preinstalled:
        state.touch()
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "BD_HOME": str(tmp_path / "bd-home"),
            "BD_REPO": str(repo),
            "BD_TEST_VENV_STATE": str(state),
            "BD_TEST_EVENTS": str(events),
            "BD_TEST_APT_FAIL": "1" if apt_fails else "0",
            "BD_TEST_REAL_PYTHON": sys.executable,
            "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
            "BD_SKIP_BROWSERS": "1",
            "BD_SKIP_CLOAK": "1",
            "BD_SKIP_AUDIT": "1",
            "BD_SKIP_NET": "1",
            "BD_SKIP_SECTOOLS": "1",
            # EXTRAS stays enabled.  On the defective source that is the only
            # place the core group is requested, after the venv attempt.
            "BD_SKIP_EXTRAS": "0",
            "BD_SKIP_ARCHB": "1",
        }
    )
    env.pop("NODE_ENV", None)
    reports = repo / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "gui_parity_inventory.json").write_text(
        json.dumps({"route_source": "live url_map"}), encoding="utf-8"
    )
    proc = subprocess.run(
        ["bash", str(CLOUD_SETUP)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    event_lines = events.read_text(encoding="utf-8").splitlines()
    return proc, event_lines


def test_fresh_cloud_setup_installs_python_venv_support_before_building_the_venv(
    tmp_path: Path,
) -> None:
    proc, events = _run_cloud_setup(tmp_path)
    assert events.count("APT_CORE") == 1, (
        "the fixture never observed the declared core group install; event log:\n"
        + "\n".join(events)
    )
    assert events.count("VENV_ATTEMPT") == 1, (
        "the fixture never reached the production venv command; event log:\n"
        + "\n".join(events)
    )
    assert events.index("APT_CORE") < events.index("VENV_ATTEMPT"), (
        "cloud-setup.sh attempted the venv before apt requested "
        "python3.12-venv; a fresh host fails once and only a second run works.\n"
        f"events={events}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[ ok ] python venv" in proc.stdout
    assert "=== READY" in proc.stdout


def test_cloud_setup_still_accepts_a_host_where_venv_support_is_already_present(
    tmp_path: Path,
) -> None:
    proc, events = _run_cloud_setup(tmp_path, package_preinstalled=True)
    assert events.count("VENV_ATTEMPT") == 1
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[ ok ] python venv" in proc.stdout
    assert "=== READY" in proc.stdout


def test_unavailable_core_packages_cannot_be_reported_ready(tmp_path: Path) -> None:
    proc, events = _run_cloud_setup(tmp_path, apt_fails=True)
    assert events.count("APT_CORE") == 1
    assert events.count("VENV_ATTEMPT") == 1
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "[FAIL] system packages (core) (exit 72)" in proc.stdout
    assert "=== READY" not in proc.stdout


def _bringup_code() -> str:
    source = RUNBOOK.read_text(encoding="utf-8")
    heading = source.find("## The ordered bring-up")
    assert heading >= 0, "ordered bring-up heading is absent"
    match = re.search(r"```bash\n(.*?)\n```", source[heading:], re.S)
    assert match, "ordered bring-up bash fence is absent or unterminated"
    return match.group(1)


def _stage(start_label: str, end_label: str) -> str:
    code = _bringup_code()
    start = code.find(f"# ── {start_label}")
    end = code.find(f"# ── {end_label}", start + 1)
    assert start >= 0 and end > start, (
        f"could not bound runbook stage {start_label!r} through {end_label!r}"
    )
    return code[start:end]


def _host_command_bin(root: Path) -> Path:
    bindir = root / "bin"
    bindir.mkdir()
    _write_executable(bindir / "sudo", '#!/bin/bash\nexec "$@"\n')
    _write_executable(bindir / "systemctl", "#!/bin/bash\nexit 0\n")
    return bindir


def _export_old_host(tmp_path: Path) -> tuple[Path, Path]:
    old_root = tmp_path / "old"
    old_home = old_root / "home"
    old_tmp = old_root / "tmp"
    repo = old_home / "BulkDownloader"
    old_tmp.mkdir(parents=True)
    repo.mkdir(parents=True)
    bindir = _host_command_bin(old_root)

    files = (
        "downloader_history.db",
        "downloader_history.db-wal",
        "downloader_history.db-shm",
        "app_config.json",
        "secrets.json",
        "secrets_meta.json",
        "vault_tokens.json",
        "user_templates.json",
        "vapid_keys.json",
        ".env",
    )
    for rel in files:
        (repo / rel).write_text(f"old-host:{rel}\n", encoding="utf-8")
    configured = tmp_path / "configured-downloads" / "primary"
    (repo / "sites_config.json").write_text(
        json.dumps({"site-a": {"download_dir": str(configured)}}),
        encoding="utf-8",
    )
    for rel in ("cookies", "learned", "state", "macros", "profiles"):
        (repo / rel).mkdir()
        (repo / rel / "marker").write_text(rel, encoding="utf-8")
    (repo / "plugins").mkdir()
    (repo / "plugins" / "plugins.registry.json").write_text(
        '{"plugins": []}\n', encoding="utf-8"
    )
    (old_home / ".config" / "bulk-downloader").mkdir(parents=True)
    (old_home / ".config" / "bulk-downloader" / "marker").write_text(
        "user config", encoding="utf-8"
    )

    stage = _stage("0. OLD box", "1. NEW box").replace(
        "/tmp/", f"{old_tmp}/"
    )
    env = os.environ.copy()
    env.update({"HOME": str(old_home), "PATH": f"{bindir}:/usr/bin:/bin"})
    proc = subprocess.run(
        ["bash", "-c", stage], capture_output=True, text=True, env=env, timeout=30
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    archives = (old_tmp / "bd_state.tar.gz", old_tmp / "bd_userconfig.tar.gz")
    assert all(path.stat().st_size > 0 for path in archives), (
        "the old-host precondition did not create both nonempty archives"
    )
    return old_tmp, configured


def _restore_bin(root: Path) -> Path:
    bindir = root / "bin"
    bindir.mkdir()
    _write_executable(
        bindir / "scp",
        r'''#!/bin/bash
if [ "${BD_TEST_SCP_FAIL:-0}" = 1 ]; then
  echo "SIMULATED: archive transfer unavailable" >&2
  exit 67
fi
source_path="${1:?source missing}"
destination="${2:?destination missing}"
cp "$BD_TEST_OLD_TMP/${source_path##*/}" "$destination"
''',
    )
    _write_executable(
        bindir / "rsync",
        r'''#!/bin/bash
if [ "${1:-}" = -a ]; then shift; fi
source_path="${1:?source missing}"; destination="${2:?destination missing}"
mkdir -p "$destination"
printf '%s\n' "$source_path" > "$destination/.rsync-source"
''',
    )
    return bindir


def _run_restore_stage(
    tmp_path: Path, old_tmp: Path, *, scp_fails: bool = False
) -> tuple[subprocess.CompletedProcess[str], Path]:
    new_root = tmp_path / "new"
    new_home = new_root / "home"
    new_tmp = new_root / "tmp"
    repo = new_home / "BulkDownloader"
    repo.mkdir(parents=True)
    new_tmp.mkdir(parents=True)
    bindir = _restore_bin(new_root)
    stage = (
        _stage("3. restore state BEFORE the first capture", "3b. the AI backend")
        .replace("<old-box-ip>", "old-box")
        .replace("/tmp/", f"{new_tmp}/")
    )
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(new_home),
            "PATH": f"{bindir}:/usr/bin:/bin",
            "BD_TEST_OLD_TMP": str(old_tmp),
            "BD_TEST_SCP_FAIL": "1" if scp_fails else "0",
        }
    )
    proc = subprocess.run(
        ["bash", "-c", stage],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc, repo


def test_runbook_transfers_both_archives_before_restoring_them(tmp_path: Path) -> None:
    old_tmp, configured = _export_old_host(tmp_path)
    proc, repo = _run_restore_stage(tmp_path, old_tmp)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    restored = repo / "secrets.json"
    assert restored.read_text(encoding="utf-8") == "old-host:secrets.json\n", (
        "the old-host archive was nonempty but its state never reached the new "
        f"host. stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    user_marker = tmp_path / "new" / "home" / ".config" / "bulk-downloader" / "marker"
    assert user_marker.read_text(encoding="utf-8") == "user config"
    assert configured.is_dir(), "the restored flat config's download_dir was not created"


def test_an_unavailable_archive_transfer_stops_before_restore(tmp_path: Path) -> None:
    old_tmp, _configured = _export_old_host(tmp_path)
    proc, repo = _run_restore_stage(tmp_path, old_tmp, scp_fails=True)
    assert proc.returncode == 67, proc.stdout + proc.stderr
    assert "SIMULATED: archive transfer unavailable" in proc.stderr
    assert not (repo / "secrets.json").exists()


def _download_directory_command() -> str:
    stage = _stage("3. restore state BEFORE the first capture", "3b. the AI backend")
    heredoc = "python3 - <<'PY'"
    if heredoc in stage:
        start = stage.index(heredoc)
        end = stage.find("\nPY", start)
        assert end >= 0, "download-directory Python heredoc is unterminated"
        return stage[start : end + len("\nPY")]
    line = next(
        (line for line in stage.splitlines() if line.startswith("mkdir -p ")),
        None,
    )
    assert line, "no executable download-directory command found in stage 3"
    return line


def _run_download_command(
    tmp_path: Path, payload: object
) -> tuple[subprocess.CompletedProcess[str], Path]:
    work = tmp_path / "download-command"
    home = tmp_path / "download-home"
    work.mkdir()
    home.mkdir()
    (work / "sites_config.json").write_text(json.dumps(payload), encoding="utf-8")
    env = os.environ.copy()
    env["HOME"] = str(home)
    proc = subprocess.run(
        ["bash", "-c", _download_directory_command()],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc, home


def test_app_written_flat_config_creates_every_configured_download_dir(
    tmp_path: Path,
) -> None:
    first = tmp_path / "downloads" / "first"
    second = tmp_path / "downloads" / "second"
    payload = {
        "site-a": {"download_dir": str(first)},
        "site-b": {"download_dir": ""},
        "site-c": {"download_dir": str(second)},
    }
    assert "sites" not in payload and len(payload) == 3, (
        "fixture is not the application's top-level site-ID mapping"
    )
    proc, home = _run_download_command(tmp_path, payload)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert first.is_dir() and second.is_dir(), (
        "the documented command exited successfully without creating every "
        f"configured directory. stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert not (home / "d").exists(), "the unrelated fallback directory was created"


def test_no_configured_download_directory_fails_instead_of_creating_a_fallback(
    tmp_path: Path,
) -> None:
    proc, home = _run_download_command(
        tmp_path, {"site-a": {"download_dir": ""}}
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "no configured download_dir" in proc.stderr
    assert not (home / "d").exists()


def test_legacy_nested_sites_shape_is_rejected(tmp_path: Path) -> None:
    wrong_dir = tmp_path / "wrong-shape-download"
    proc, home = _run_download_command(
        tmp_path, {"sites": [{"download_dir": str(wrong_dir)}]}
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert not wrong_dir.exists()
    assert not (home / "d").exists()


def test_transform_control_loads_subjects_without_asserting_bringup() -> None:
    """Import/parse-only control for the matching durable mutation spec."""
    assert CLOUD_SETUP.is_file() and RUNBOOK.is_file()
    parsed = subprocess.run(
        ["bash", "-n", str(CLOUD_SETUP)], capture_output=True, text=True, timeout=30
    )
    assert parsed.returncode == 0, parsed.stderr
    assert _bringup_code().strip()
