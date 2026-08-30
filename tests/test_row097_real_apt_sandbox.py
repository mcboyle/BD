"""Exercise row 97's apt transaction without changing the worker host.

The original row-97 gate deliberately replaces ``apt-get`` with a shell stub.
It proves the decision to request ``postgresql``, but not whether apt can
resolve a package from an empty package database and hand its payload back to
``bd_mod3_pg_provision``.  This test creates a one-package file repository and
uses the host's real apt and dpkg binaries only against temporary directories.
No package is installed into the worker's dpkg database or filesystem.

The tiny package is named ``postgresql`` because that is the package the
provisioner requests.  Its ``pg_ctlcluster`` payload is the contract the
provisioner checks after apt reports success.  The negative control installs a
package with the same name but without that payload: apt succeeds, while the
provisioner must still refuse rather than treating an exit status as evidence.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


BD_GATE_SCOPE = "module"

_ROOT = Path(__file__).resolve().parents[1]
_LIBRARY = _ROOT / "scripts" / "lib" / "dev_capabilities.sh"
_BASH = shutil.which("bash") or "/bin/bash"
_APT_GET = shutil.which("apt-get")
_DPKG_DEB = shutil.which("dpkg-deb")
_DPKG_SCANPACKAGES = shutil.which("dpkg-scanpackages")


@dataclass(frozen=True)
class AptSandbox:
    """Every mutable apt/dpkg path is beneath ``root``."""

    root: Path
    bin_dir: Path
    apt_log: Path


def _require_tools() -> tuple[str, str, str]:
    """Fail honestly if this Debian-specific executable path cannot run."""
    assert _APT_GET, "apt-get is required for the real apt sandbox"
    assert _DPKG_DEB, "dpkg-deb is required for the real apt sandbox"
    assert _DPKG_SCANPACKAGES, "dpkg-scanpackages is required for the real apt sandbox"
    return _APT_GET, _DPKG_DEB, _DPKG_SCANPACKAGES


def _write_executable(path: Path, text: str) -> None:
    path.write_text(f"#!{_BASH}\n{text}", encoding="utf-8")
    path.chmod(0o755)


def _build_repository(base: Path, *, provides_pg_ctlcluster: bool) -> Path:
    """Build a real Debian archive and index it with dpkg-scanpackages."""
    _, dpkg_deb, scanpackages = _require_tools()
    package = base / "package"
    control = package / "DEBIAN"
    control.mkdir(parents=True)
    (control / "control").write_text(
        "Package: postgresql\n"
        "Version: 1.0\n"
        "Architecture: all\n"
        "Maintainer: BulkDownloader test <test@example.invalid>\n"
        "Description: hermetic row-97 apt fixture\n",
        encoding="utf-8",
    )
    if provides_pg_ctlcluster:
        command = package / "usr" / "bin" / "pg_ctlcluster"
        command.parent.mkdir(parents=True)
        _write_executable(command, "exit 0\n")

    repository = base / "repository"
    pool = repository / "pool"
    pool.mkdir(parents=True)
    archive = pool / "postgresql_1.0_all.deb"
    built = subprocess.run(
        [dpkg_deb, "--build", str(package), str(archive)],
        capture_output=True, text=True,
    )
    assert built.returncode == 0, built.stderr
    index = subprocess.run(
        [scanpackages, "--multiversion", "pool", "/dev/null"],
        cwd=repository, capture_output=True, text=True,
    )
    assert index.returncode == 0, index.stderr
    (repository / "Packages").write_text(index.stdout, encoding="utf-8")
    return repository


def _sandbox(tmp_path: Path, *, provides_pg_ctlcluster: bool) -> AptSandbox:
    """Create a bare apt state and an apt executable that confines every write."""
    apt_get, _, _ = _require_tools()
    root = tmp_path / "apt-root"
    for relative in (
        "etc/apt",
        "var/lib/apt/lists/partial",
        "var/cache/apt/archives/partial",
        "var/lib/dpkg",
        "var/log/apt",
        "usr/bin",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)
    # An empty status file makes this a bare host from apt's point of view.
    (root / "var/lib/dpkg/status").touch()
    repository = _build_repository(
        tmp_path, provides_pg_ctlcluster=provides_pg_ctlcluster)
    (root / "etc/apt/sources.list").write_text(
        f"deb [trusted=yes] file:{repository} ./\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    apt_log = tmp_path / "apt.log"
    _write_executable(
        bin_dir / "apt-get",
        f'''printf '%s\\n' "$*" >> "{apt_log}"
exec "{apt_get}" \\
  -o Dir="{root}" \\
  -o Dir::Etc::sourcelist="sources.list" \\
  -o Dir::Etc::sourceparts="-" \\
  -o Dir::Etc::main="-" \\
  -o Dir::Etc::parts="-" \\
  -o APT::Get::List-Cleanup="0" \\
  -o APT::Sandbox::User="root" \\
  -o Dpkg::Options::=--root="{root}" \\
  -o Dpkg::Options::=--force-not-root \\
  -o Dpkg::Options::=--log="{root}/var/log/dpkg.log" \\
  "$@"
''',
    )

    # The provisioner must be unable to discover a worker-host postgres.  It
    # gets only a few real filesystem utilities plus the package payload path.
    for name in ("id", "locale", "mkdir", "mktemp", "mv", "rm", "touch"):
        executable = shutil.which(name)
        assert executable, f"fixture prerequisite unavailable: {name}"
        (bin_dir / name).symlink_to(executable)
    _write_executable(bin_dir / "psql", "exit 1\n")
    return AptSandbox(root=root, bin_dir=bin_dir, apt_log=apt_log)


def _run(sandbox: AptSandbox, library: Path = _LIBRARY) -> subprocess.CompletedProcess[str]:
    home = sandbox.root.parent / "home"
    home.mkdir()
    environment = dict(os.environ)
    environment.update({
        # The payload directory is empty at entry and appears only when apt/dpkg
        # unpack the synthetic package.  System PATH is intentionally absent.
        "PATH": f"{sandbox.bin_dir}:{sandbox.root / 'usr/bin'}",
        "HOME": str(home),
        "SUDO": "",
    })
    return subprocess.run(
        [_BASH, "-c", f'. "{library}"\nSUDO=""\nbd_mod3_pg_provision\n'],
        capture_output=True, text=True, env=environment,
    )


def test_fixture_starts_as_a_bare_host(tmp_path):
    sandbox = _sandbox(tmp_path, provides_pg_ctlcluster=True)

    probe = subprocess.run(
        [_BASH, "-c", "command -v pg_ctlcluster"],
        capture_output=True, text=True,
        env={"PATH": f"{sandbox.bin_dir}:{sandbox.root / 'usr/bin'}"},
    )

    assert probe.returncode != 0, probe.stdout
    assert not (sandbox.root / "usr/bin/pg_ctlcluster").exists()


def test_real_apt_installs_postgresql_payload_into_only_the_temporary_root(tmp_path):
    sandbox = _sandbox(tmp_path, provides_pg_ctlcluster=True)

    result = _run(sandbox)

    assert sandbox.apt_log.exists(), result.stderr
    calls = sandbox.apt_log.read_text(encoding="utf-8").splitlines()
    assert calls == ["update -qq", "install -y -qq postgresql"]
    installed = sandbox.root / "usr/bin/pg_ctlcluster"
    assert installed.is_file() and os.access(installed, os.X_OK), (
        f"apt did not install the package payload; stdout={result.stdout!r} "
        f"stderr={result.stderr!r}")
    # No cluster is part of this deliberately tiny package.  Reaching this
    # diagnostic proves the production post-apt binary check passed.
    assert result.returncode != 0
    assert "no postgres cluster initialized in this image" in result.stdout
    assert "still absent" not in result.stdout


def test_real_apt_success_without_the_required_payload_is_rejected(tmp_path):
    sandbox = _sandbox(tmp_path, provides_pg_ctlcluster=False)

    result = _run(sandbox)

    assert sandbox.apt_log.exists(), result.stderr
    status = (sandbox.root / "var/lib/dpkg/status").read_text(encoding="utf-8")
    assert status.count("Package: postgresql") == 1, (
        f"apt did not record the synthetic package; stdout={result.stdout!r} "
        f"stderr={result.stderr!r}")
    assert not (sandbox.root / "usr/bin/pg_ctlcluster").exists()
    assert result.returncode != 0
    assert "install reported success but pg_ctlcluster is still absent" in result.stdout
