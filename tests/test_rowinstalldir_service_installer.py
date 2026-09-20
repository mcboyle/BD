"""The service installer must not turn a worker checkout into production."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
_INSTALLER = _REPO / "install_service.sh"
_MAIN_UNIT = "bulkdownloader.service"
_AI_UNIT = "bulkdownloader-ai-ready.service"


@dataclass(frozen=True)
class _Fixture:
    app_dir: Path
    unit_dir: Path
    env: dict[str, str]
    log: Path

    def run(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.app_dir / "install_service.sh"), *args],
            cwd=cwd or self.app_dir,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
        )


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _fixture(tmp_path: Path, app_dir: Path) -> _Fixture:
    app_dir.mkdir(parents=True)
    unit_dir = tmp_path / "fixture-systemd"
    unit_dir.mkdir()
    source = _INSTALLER.read_text(encoding="utf-8")
    assert source.count("/etc/systemd/system") == 5
    (app_dir / "install_service.sh").write_text(
        source.replace("/etc/systemd/system", str(unit_dir)), encoding="utf-8"
    )
    (app_dir / "downloader_ui.py").write_text("# fixture entry point\n", encoding="utf-8")

    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "commands.log"
    _write_executable(
        bindir / "systemctl",
        """#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_COMMAND_LOG"
case "$1" in
  is-active)
    if [ "${FAKE_SERVICE_ACTIVE:-0}" = 1 ] && \
       [ "${3:-}" = "${FAKE_ACTIVE_SERVICE:-bulkdownloader}" ]; then
      [ "${2:-}" = "--quiet" ] || echo active
      exit 0
    fi
    [ "${2:-}" = "--quiet" ] || echo inactive
    exit 3
    ;;
  show)
    echo "LoadState=loaded"
    if [ "${FAKE_SERVICE_ACTIVE:-0}" = 1 ] && \
       [ "$2" = "${FAKE_ACTIVE_SERVICE:-bulkdownloader}" ]; then
      echo "ActiveState=active"
      echo "WorkingDirectory=${FAKE_WORKING_DIRECTORY:-}"
    else
      echo "ActiveState=inactive"
      echo "WorkingDirectory="
    fi
    exit 0
    ;;
  *) exit 0 ;;
esac
""",
    )
    _write_executable(
        bindir / "sudo",
        """#!/bin/sh
printf 'sudo %s\n' "$*" >> "$FAKE_COMMAND_LOG"
exec "$@"
""",
    )
    _write_executable(bindir / "whoami", "#!/bin/sh\necho fixture-user\n")
    _write_executable(
        bindir / "getent",
        """#!/bin/sh
printf 'fixture-user:x:1000:1000::%s:/bin/sh\n' "$HOME"
""",
    )
    _write_executable(bindir / "id", "#!/bin/sh\necho 1000\n")
    _write_executable(bindir / "python3", "#!/bin/sh\nexit 0\n")
    _write_executable(bindir / "sleep", "#!/bin/sh\nexit 0\n")
    _write_executable(bindir / "curl", "#!/bin/sh\nprintf 000\nexit 7\n")

    env = dict(os.environ)
    env.pop("BD_INSTALL_DIR", None)
    env.pop("BD_DEPLOY_DIR", None)
    env.pop("SUDO_USER", None)
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "PATH": f"{bindir}:{env['PATH']}",
            "FAKE_COMMAND_LOG": str(log),
            "FAKE_SERVICE_ACTIVE": "0",
            "FAKE_ACTIVE_SERVICE": "bulkdownloader",
            "FAKE_WORKING_DIRECTORY": "",
        }
    )
    return _Fixture(app_dir=app_dir, unit_dir=unit_dir, env=env, log=log)


def _restart_calls(fixture: _Fixture) -> list[str]:
    if not fixture.log.exists():
        return []
    return [line for line in fixture.log.read_text(encoding="utf-8").splitlines()
            if line.startswith("sudo systemctl restart ")]


def _assert_installed(
    fixture: _Fixture,
    result: subprocess.CompletedProcess[str],
    *,
    source: str,
) -> None:
    assert result.returncode == 0, result.stdout + result.stderr
    for name in (_MAIN_UNIT, _AI_UNIT):
        unit = fixture.unit_dir / name
        assert unit.is_file(), f"fixture did not receive {name}"
        body = unit.read_text(encoding="utf-8")
        assert body.splitlines().count(f"WorkingDirectory={fixture.app_dir}") == 1
    assert f"WorkingDirectory: {fixture.app_dir}" in result.stdout
    assert result.stdout.splitlines().count(f"  Install source : {source}") == 1
    assert _restart_calls(fixture) == [
        "sudo systemctl restart bulkdownloader",
        "sudo systemctl restart bulkdownloader-ai-ready",
    ]


def test_relocated_script_refuses_before_rewriting_the_installed_unit(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, tmp_path / "worker-session")
    unit = fixture.unit_dir / _MAIN_UNIT
    original = b"fixture-existing-unit\n"
    unit.write_bytes(original)

    result = fixture.run()

    assert unit.read_bytes() == original, "relocated installer rewrote the existing unit"
    assert result.returncode != 0
    assert "not the canonical install directory" in result.stdout
    assert _restart_calls(fixture) == []


def test_default_canonical_directory_still_installs(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, tmp_path / "home" / "BulkDownloader")

    result = fixture.run()

    _assert_installed(fixture, result, source="canonical ~/BulkDownloader")


def test_explicit_install_directory_still_installs_from_any_cwd(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, tmp_path / "approved-checkout")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    fixture.env["BD_DEPLOY_DIR"] = str(tmp_path / "environment-checkout")
    result = fixture.run("--install-dir", str(fixture.app_dir), cwd=elsewhere)

    _assert_installed(fixture, result, source="--install-dir")


def test_environment_install_directory_still_installs(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, tmp_path / "environment-checkout")
    fixture.env["BD_DEPLOY_DIR"] = str(fixture.app_dir)

    result = fixture.run()

    _assert_installed(fixture, result, source="BD_DEPLOY_DIR")


def test_data_install_directory_does_not_select_the_deploy_tree(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, tmp_path / "home" / "BulkDownloader")
    fixture.env["BD_INSTALL_DIR"] = str(tmp_path / "data-state")

    result = fixture.run()

    _assert_installed(fixture, result, source="canonical ~/BulkDownloader")


def test_running_service_at_another_directory_is_not_rewritten_or_restarted(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, tmp_path / "approved-checkout")
    unit = fixture.unit_dir / _MAIN_UNIT
    original = b"fixture-running-unit\n"
    unit.write_bytes(original)
    fixture.env["FAKE_SERVICE_ACTIVE"] = "1"
    fixture.env["FAKE_WORKING_DIRECTORY"] = str(tmp_path / "other-checkout")

    result = fixture.run("--install-dir", str(fixture.app_dir))

    assert unit.read_bytes() == original, "running service unit was rewritten"
    assert result.returncode != 0
    assert "running bulkdownloader uses WorkingDirectory=" in result.stdout
    assert "sudo systemctl stop bulkdownloader" in result.stdout
    assert _restart_calls(fixture) == []


def test_running_ai_service_at_another_directory_is_not_rewritten_or_restarted(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, tmp_path / "approved-checkout")
    unit = fixture.unit_dir / _AI_UNIT
    original = b"fixture-running-ai-unit\n"
    unit.write_bytes(original)
    fixture.env["FAKE_SERVICE_ACTIVE"] = "1"
    fixture.env["FAKE_ACTIVE_SERVICE"] = "bulkdownloader-ai-ready"
    fixture.env["FAKE_WORKING_DIRECTORY"] = str(tmp_path / "other-checkout")
    assert unit.read_bytes() == original
    assert fixture.env["FAKE_ACTIVE_SERVICE"] == "bulkdownloader-ai-ready"

    result = fixture.run("--install-dir", str(fixture.app_dir))

    assert result.returncode != 0, result.stdout + result.stderr
    assert unit.read_bytes() == original, "running AI service unit was rewritten"
    assert "running bulkdownloader-ai-ready uses WorkingDirectory=" in result.stdout
    assert _restart_calls(fixture) == []


def test_running_service_at_same_directory_can_be_reinstalled(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, tmp_path / "approved-checkout")
    fixture.env["FAKE_SERVICE_ACTIVE"] = "1"
    fixture.env["FAKE_WORKING_DIRECTORY"] = str(fixture.app_dir)

    result = fixture.run("--install-dir", str(fixture.app_dir))

    _assert_installed(fixture, result, source="--install-dir")


def test_transform_control_only_confirms_the_installer_fixture_is_available() -> None:
    assert _INSTALLER.is_file()
