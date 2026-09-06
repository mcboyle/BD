"""Rows 715/718: execute installer regions with ordinary files and stub services.

Removing either preflight must expose writes/restarts, not merely change an
exit code. The independent population is the app and capture installers.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "repo-wide"
ROOT = Path(__file__).resolve().parents[1]
INSTALLERS = ("install_service.sh", "scripts/install_capture_service.sh")


class InstallRun:
    def __init__(self, root, installer="install_service.sh", canonical=True):
        self.root = root
        self.installer = installer
        self.home = root / "home"
        self.home.mkdir()
        self.app = self.home / "BulkDownloader" if canonical else root / "scratch"
        self.app.mkdir()
        (self.app / "downloader_ui.py").touch()
        (self.app / "tools").mkdir()
        self.helper = self.app / "tools/write_deployed_version.sh"
        self.helper.write_text('#!/bin/sh\nprintf "helper\\n" >> "$EFFECT_LOG"\nexit "${HELPER_RC:-0}"\n')
        self.helper.chmod(0o755)
        self.bin = root / "bin"
        self.bin.mkdir()
        self.log = root / "systemctl.log"
        self.log.touch()
        self.effects = root / "effects.log"
        self.effects.touch()
        self.writes = root / "writes.log"
        self.writes.touch()
        self.unit = root / "unit.service"
        self.ai_unit = root / "ai.service"
        self.runtime = root / "runtime"
        self.env_path = self.runtime / "fixture.env"
        self.services = ("bulkdownloader", "bulkdownloader-ai-ready") if installer == INSTALLERS[0] else ("bulkdownloader-capture@fixture.service",)
        self.env = {**os.environ, "HOME": str(self.home), "PATH": f"{self.bin}:{os.environ['PATH']}",
                    "SYSTEMCTL_LOG": str(self.log), "EFFECT_LOG": str(self.effects), "WRITE_LOG": str(self.writes),
                    "PROPERTIES": str(root), "CAPTURE_SERVICE_UNIT_PATH": str(self.unit),
                    "CAPTURE_SERVICE_RUNTIME_DIR": str(self.runtime),
                    "CAPTURE_SERVICE_PYTHON": "/usr/bin/python3", "LC_ALL": "C", "LANG": "C"}
        for key in ("BD_DEPLOY_DIR", "BD_INSTALL_DIR", "SUDO_USER"):
            self.env.pop(key, None)
        self.write_stub("sudo", '#!/bin/sh\ncase "$1" in systemctl) exec "$@" ;; sed) SUDO_READING=1 exec "$@" ;; tee|install|chmod|mv|rm) printf "%s\\n" "$*" >> "$WRITE_LOG"; exec "$@" ;; *) exit 99 ;; esac\n')
        self.write_stub("systemctl", '''#!/bin/sh
printf '%s\n' "$*" >> "$SYSTEMCTL_LOG"
case "$1" in
  show) if [ -f "$PROPERTIES/$2.properties" ]; then cat "$PROPERTIES/$2.properties"; exit "${SHOW_RC:-0}"; fi
        printf 'LoadState=not-found\nActiveState=inactive\n'
        case "$*" in *--all*) printf 'WorkingDirectory=\n' ;; esac
        exit "${SHOW_RC:-0}" ;;
  daemon-reload|enable|restart) exit 0 ;;
  *) exit 99 ;;
esac
''')
        self.write_stub("chmod", '#!/bin/sh\nif [ "${CHMOD_FAIL:-0}" = 1 ]; then exit 1; fi\nexec /bin/chmod "$@"\n')

    def write_stub(self, name, source):
        path = self.bin / name
        path.write_text(source)
        path.chmod(0o755)

    def installed(self, service, directory, *, state="active", properties=None):
        text = properties if properties is not None else f"LoadState=loaded\nActiveState={state}\nWorkingDirectory={directory}\n"
        path = self.root / f"{service}.properties"
        path.write_text(text)
        assert path.read_text() == text
        if self.installer == INSTALLERS[1]:
            self.runtime.mkdir(exist_ok=True)
            self.env_path.write_text(f"BD_CAPTURE_APP_DIR={directory}\n")

    def run(self, *args):
        source = (ROOT / self.installer).read_text()
        end = "# Poll up to 15s" if self.installer == INSTALLERS[0] else "ready=0\n"
        assert source.count(end) == 1
        source = source.split(end)[0]
        source, n = re.subn(r'^APP_DIR=.*$', f"APP_DIR={shlex.quote(str(self.app))}", source, count=1, flags=re.M)
        assert n == 1
        if self.installer == INSTALLERS[0]:
            for name, value in (("UNIT_PATH", self.unit), ("AI_UNIT_PATH", self.ai_unit)):
                source, n = re.subn(rf'^{name}=.*$', f"{name}={shlex.quote(str(value))}", source, flags=re.M)
                assert n == 1
            argv = args
        else:
            argv = ("start", "fixture", "42123", str(self.root / "data"), *args)
        assert len(re.findall(r'^(?:if ! )?sudo systemctl restart ', source, re.M)) == len(self.services)
        assert not self.unit.exists() and not self.ai_unit.exists()
        script = self.root / "region.sh"
        script.write_text(source)
        self.result = subprocess.run(["bash", str(script), *argv], cwd=self.root, env=self.env,
                                     capture_output=True, text=True, timeout=10)
        self.out = self.result.stdout + self.result.stderr
        return self

    def refused(self, reason):
        written = [p.read_text() for p in (self.unit, self.ai_unit, self.root / "unit.service.fixture.tmp") if p.exists()]
        restarts = [s for s in self.log.read_text().splitlines() if s.startswith("restart ")]
        assert (written, restarts, self.result.returncode == 0) == ([], [], False), (
            f"refusal leaked effects: units={written!r}, restarts={restarts!r}, rc={self.result.returncode}\n{self.out}")
        assert reason in self.out, self.out
        assert self.out.count("INSTALL_DIR_SOURCE=") == 1, self.out
        assert self.writes.read_text() == "", "refusal performed writes even if cleanup removed the evidence"
        assert self.effects.read_text() == "", "preflight ran the version helper"

    def accepted(self, source):
        assert self.result.returncode == 0, self.out
        assert self.out.count(f"INSTALL_DIR_SOURCE={source}") == 1, self.out
        assert f"WorkingDirectory={self.app.resolve()}" in self.out, self.out
        calls = self.log.read_text().splitlines()
        assert sorted(s.split()[1] for s in calls if s.startswith("show ")) == sorted(self.services)
        assert sorted(s for s in calls if s.startswith("restart ")) == sorted(f"restart {s}" for s in self.services)
        assert self.unit.is_file()
        if self.installer == INSTALLERS[0]:
            assert self.ai_unit.is_file()
            for unit in (self.unit, self.ai_unit):
                assert unit.read_text().splitlines().count(f"WorkingDirectory={self.app.resolve()}") == 1
            assert self.effects.read_text().splitlines() == ["helper"]
        else:
            assert self.env_path.read_text().splitlines().count(f"BD_CAPTURE_APP_DIR={self.app.resolve()}") == 1


@pytest.mark.parametrize("installer", INSTALLERS)
def test_foreign_tree_refuses_before_effects(tmp_path, installer):
    run = InstallRun(tmp_path, installer, canonical=False)
    assert run.app.resolve() != (run.home / "BulkDownloader").resolve()
    run.run().refused("INSTALL-DIR-REFUSED")


@pytest.mark.parametrize("installer", INSTALLERS)
@pytest.mark.parametrize("authorization", ["canonical", "BD_DEPLOY_DIR", "--install-dir", "symlink", "trailing-slash", "cli-over-env"])
def test_authorized_tree_and_source(tmp_path, installer, authorization):
    canonical = authorization in ("canonical", "symlink", "trailing-slash")
    run = InstallRun(tmp_path, installer, canonical=canonical)
    args = ()
    source = authorization
    if authorization == "BD_DEPLOY_DIR":
        run.env["BD_DEPLOY_DIR"] = str(run.app)
    elif authorization in ("--install-dir", "cli-over-env"):
        args = ("--install-dir", str(run.app))
        source = "--install-dir"
        if authorization == "cli-over-env":
            run.env["BD_DEPLOY_DIR"] = str(run.root / "wrong")
    elif authorization == "symlink":
        link = run.root / "alias"
        link.symlink_to(run.app, target_is_directory=True)
        assert link.is_symlink() and link.resolve() == run.app
        run.app = link
        source = "canonical"
    elif authorization == "trailing-slash":
        args = ("--install-dir", str(run.app) + "/")
        source = "--install-dir"
    run.run(*args).accepted(source)


@pytest.mark.parametrize("installer", INSTALLERS)
def test_unrelated_environment_is_not_authorization(tmp_path, installer):
    run = InstallRun(tmp_path, installer, canonical=False)
    run.env["BD_DEPLOY_DIR"] = str(run.home)
    assert run.home.resolve() != run.app.resolve()
    run.run().refused("INSTALL-DIR-REFUSED")


@pytest.mark.parametrize("service", ("bulkdownloader", "bulkdownloader-ai-ready", "bulkdownloader-capture@fixture.service"))
@pytest.mark.parametrize("matching", [False, True])
def test_running_unit_tree(tmp_path, service, matching):
    installer = INSTALLERS[1] if "capture@" in service else INSTALLERS[0]
    run = InstallRun(tmp_path, installer)
    other = tmp_path / "other-tree"
    other.mkdir()
    directory = run.app if matching else other
    assert (directory.resolve() == run.app.resolve()) == matching
    run.installed(service, directory)
    run.run()
    if matching:
        run.accepted("canonical")
    else:
        run.refused("RUNNING-UNIT-DIR-REFUSED")
        assert str(other) in run.out and "stop" in run.out.lower()


@pytest.mark.parametrize("installer", INSTALLERS)
@pytest.mark.parametrize("failure", ["transport", "empty", "missing-workdir", "bad-state"])
def test_unavailable_unit_measurement_refuses(tmp_path, failure, installer):
    run = InstallRun(tmp_path, installer)
    if failure == "transport":
        run.env["SHOW_RC"] = "1"
    elif failure == "empty":
        run.installed(run.services[0], run.app, properties="")
    elif failure == "missing-workdir":
        run.installed(run.services[0], "")
    else:
        run.installed(run.services[0], run.app, state="unknown")
    run.run().refused("UNIT-DIR-UNKNOWN")


@pytest.mark.parametrize("installer", INSTALLERS)
@pytest.mark.parametrize("args", [("--install-dir",), ("--install-dir", ""), ("--install-dir", "/missing/parent/tree")])
def test_invalid_directory_authorization_refuses(tmp_path, installer, args):
    InstallRun(tmp_path, installer).run(*args).refused("INSTALL-DIR-REFUSED")


@pytest.mark.parametrize("installer", INSTALLERS)
def test_inactive_foreign_unit_does_not_prevent_explicit_install(tmp_path, installer):
    run = InstallRun(tmp_path, installer)
    other = tmp_path / "other"
    other.mkdir()
    run.installed(run.services[0], other, state="inactive")
    run.run().accepted("canonical")


@pytest.mark.parametrize("installer", INSTALLERS)
def test_running_unit_resolves_symlink(tmp_path, installer):
    run = InstallRun(tmp_path, installer)
    link = tmp_path / "unit-tree"
    link.symlink_to(run.app, target_is_directory=True)
    assert link.resolve() == run.app
    run.installed(run.services[0], str(link) + "/")
    run.run().accepted("canonical")


def test_capture_reads_root_owned_environment_through_sudo(tmp_path):
    run = InstallRun(tmp_path, INSTALLERS[1])
    run.installed(run.services[0], run.app)
    run.write_stub("sed", '''#!/bin/sh
case "$*" in
  *BD_CAPTURE_APP_DIR*)
    printf '%s\\n' "${SUDO_READING:-0}" >> "$PROPERTIES/privileged-read.log"
    [ "${SUDO_READING:-0}" = 1 ] || exit 1 ;;
esac
exec /bin/sed "$@"
''')
    run.run().accepted("canonical")
    assert (tmp_path / "privileged-read.log").read_text().splitlines() == ["1"]


def test_transform_control_import_only():
    """Mutation transform control: import this harness, assert no installer behavior."""
    __import__(__name__)
