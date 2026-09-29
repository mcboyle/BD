"""IA-02: install_linux.sh must build the SPA with a Node that meets the floor.

Fresh Ubuntu 24.04 (spare8): the system tier installs apt nodejs 18.19.1, but
frontend/package.json says engines.node >=20. npm skips @tailwindcss/oxide's
native binding under 18, `vite build` dies "Cannot find native binding", and /
serves 503 -- the install still exits 0. The installer's own guard checked 18.

The frontend step of install_linux.sh is sliced out (like row 689's probe) and
run against fakes: node, npm, curl and sudo on PATH, HOME in tmp_path. The fake
npm's build only succeeds under a Node that meets the floor, like the real one.
The pinned sha256 is swapped for the fake tarball's in the probe; the negative
control keeps the real pin and must refuse the tarball.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parents[1]
_INSTALL_LINUX = _REPO / "install_linux.sh"
_START = "# D3 U1: Frontend SPA build"
_END = "# ── GUI-parity inventory regen"
_PIN = "v22.23.2"
_ARCH = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(
    platform.machine().lower())

_TOOLS = ("bash", "sh", "sed", "head", "cut", "sha256sum", "mktemp", "rm", "tar",
          "xz", "mkdir", "ln", "uname", "id", "cp", "cat", "dirname", "readlink")

_FAKE_NODE = '#!/bin/sh\necho "{version}"\n'
_FAKE_NPM = r'''#!/bin/sh
v="$(node --version)"
echo "npm $* node=$v" >> "$IA02_LOG"
if [ "$1 $2" = "run build" ]; then
    major="${v#v}"; major="${major%%.*}"
    if [ "$major" -lt 20 ]; then
        echo "Error: Cannot find native binding." >&2
        exit 1
    fi
    mkdir -p dist && echo ok > dist/index.html
fi
exit 0
'''
_FAKE_CURL = r'''#!/bin/sh
echo "curl $*" >> "$IA02_LOG"
out=""
while [ $# -gt 0 ]; do
    case "$1" in -o) out="$2"; shift ;; esac
    shift
done
cp "$IA02_TARBALL" "$out"
'''


def _exe(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _tarball(path: Path) -> str:
    name = f"node-{_PIN}-linux-{_ARCH}"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as tar:
        for member, text in ((f"{name}/bin/node", _FAKE_NODE.format(version=_PIN)),
                             (f"{name}/bin/npm", _FAKE_NPM),
                             (f"{name}/bin/npx", "#!/bin/sh\nexit 0\n")):
            data = text.encode()
            info = tarfile.TarInfo(member)
            info.size = len(data)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(data))
    path.write_bytes(buf.getvalue())
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(tmp_path: Path, *, node: str | None, floor: str = ">=20",
         swap_pin: bool = True) -> tuple[subprocess.CompletedProcess, str, Path]:
    source = _INSTALL_LINUX.read_text(encoding="utf-8")
    assert source.count(_START) == 1 and source.count(_END) == 1, (
        "UNKNOWN: install_linux.sh's frontend-step anchors are not unique")
    body = source[source.index(_START):source.index(_END)]
    tarball = tmp_path / "node.tar.xz"
    digest = _tarball(tarball)
    if swap_pin:
        body = re.sub(r"_nsum=[0-9a-f]{64}", f"_nsum={digest}", body)

    app = tmp_path / "app"
    (app / "frontend").mkdir(parents=True)
    (app / "frontend" / "package.json").write_text(
        json.dumps({"name": "fe", "engines": {"node": floor}}, indent=2) + "\n")
    (app / "frontend" / "package-lock.json").write_text("{}\n")
    probe = app / "probe.sh"
    _exe(probe, "#!/usr/bin/env bash\nset -u\n"
                'INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
                f"{body}\nexit 0\n")

    fake = tmp_path / "fakebin"
    fake.mkdir()
    if node is not None:
        _exe(fake / "node", _FAKE_NODE.format(version=node))
        _exe(fake / "npm", _FAKE_NPM)
    _exe(fake / "curl", _FAKE_CURL)
    _exe(fake / "sudo", "#!/bin/sh\nexit 1\n")  # never touch the real /usr/local
    # Only the utilities the step uses -- a real node/npm in /usr/bin must not
    # stand in for the fakes (the "missing node" case would silently pass).
    sysbin = tmp_path / "sysbin"
    sysbin.mkdir()
    for tool in _TOOLS:
        found = shutil.which(tool)
        assert found, f"UNKNOWN: {tool} not on this host"
        (sysbin / tool).symlink_to(found)
    home = tmp_path / "home"
    home.mkdir()
    log = tmp_path / "ia02.log"
    log.write_text("")
    env = {"PATH": f"{fake}:{sysbin}", "HOME": str(home),
           "IA02_LOG": str(log), "IA02_TARBALL": str(tarball)}
    proc = subprocess.run(["bash", str(probe)], env=env, capture_output=True,
                          text=True, timeout=120)
    return proc, log.read_text(), app


pytestmark = pytest.mark.skipif(_ARCH is None, reason="no pinned Node build for this arch")


def test_node_below_the_floor_gets_the_pinned_node_and_the_build_succeeds(tmp_path):
    proc, log, app = _run(tmp_path, node="v18.19.1")
    out = proc.stdout + proc.stderr
    assert f"https://nodejs.org/dist/{_PIN}/node-{_PIN}-linux-{_ARCH}.tar.xz" in log, out
    assert f"npm run build node={_PIN}" in log, (log, out)
    assert (app / "frontend" / "dist" / "index.html").is_file(), out
    assert "Frontend built" in out, out
    linked = tmp_path / "home" / ".local" / "bin" / "node"
    assert linked.is_symlink() and os.readlink(linked).endswith(
        f"lib/nodejs/node-{_PIN}-linux-{_ARCH}/bin/node"), out


def test_missing_node_gets_the_pinned_node(tmp_path):
    proc, log, app = _run(tmp_path, node=None)
    out = proc.stdout + proc.stderr
    assert f"npm run build node={_PIN}" in log, (log, out)
    assert (app / "frontend" / "dist" / "index.html").is_file(), out


def test_a_tarball_that_misses_the_pinned_sha256_is_refused(tmp_path):
    proc, log, app = _run(tmp_path, node="v18.19.1", swap_pin=False)
    out = proc.stdout + proc.stderr
    assert "does not match the" in out and "not installing it" in out, out
    assert not (tmp_path / "home" / ".local" / "bin" / "node").exists(), out
    assert f"node={_PIN}" not in log, log
    assert not (app / "frontend" / "dist").exists(), out


def test_a_node_meeting_the_floor_is_used_as_is(tmp_path):
    proc, log, app = _run(tmp_path, node="v22.1.0")
    out = proc.stdout + proc.stderr
    assert "curl" not in log, log
    assert "npm run build node=v22.1.0" in log, (log, out)


def test_the_floor_comes_from_package_json(tmp_path):
    # engines >=16 accepts a 17: no download, and the build is attempted --
    # a hard-coded floor (the old 18) would have skipped it.
    proc, log, app = _run(tmp_path, node="v17.9.1", floor=">=16")
    assert "curl" not in log, log
    assert "npm run build node=v17.9.1" in log, (log, proc.stdout)
