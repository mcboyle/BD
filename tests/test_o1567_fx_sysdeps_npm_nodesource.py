"""o1567 fx-sysdeps-npm-nodesource: one unresolvable group must not cost the rest.

install_linux.sh hands apt ONE call, `apt-get install -y $(bd_system_pkgs all)`.
apt is all-or-nothing, so any single unresolvable name installs NOTHING. On VM bd
(10.0.70.50) nodejs is 22.23.3-1nodesource1, which bundles npm; Ubuntu's `npm`
package then cannot be resolved ("E: Unable to correct problems, you have held
broken packages") -- measured: the full list fails, the same list without
nodejs/npm resolves. So every host with nodesource node silently got none of the
other groups (gtk/xvfb, media/ffmpeg, fonts, ...), and the installer only said
"(system package install failed - continuing)".

The fix keeps the single fast call and, when it fails, retries group by group so
the resolvable groups still install, then names the groups that did not.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tests.test_provision_test_host import (
    _BASH,
    _FAKE_APT_GET,
    _FAKE_SUDO,
    _build_install_linux_system_tier_probe,
    _write_stub,
)

BD_GATE_SCOPE = "module"

# A group-aware stub fragment: each group returns its own names, so the probe
# can see which group's apt call carried which packages.
_GROUPS = {
    "core": "git python3.12",
    "node": "nodejs npm",
    "gtk": "xvfb x11-utils",
    "media": "ffmpeg",
}
_FRAGMENT = (
    "bd_system_pkgs() {\n    case \"$1\" in\n"
    + "".join(f"        {g}) printf '%s\\n' '{names}' ;;\n" for g, names in _GROUPS.items())
    + "        all) printf '%s\\n' '" + " ".join(_GROUPS.values()) + "' ;;\n"
    + "        *) return 2 ;;\n    esac\n}\n"
    + "bd_start_display() { return 0; }\n:\n"
)


def _run(tmp_path: Path, fail_names: str) -> tuple[subprocess.CompletedProcess, list[str]]:
    work = tmp_path / "repo"
    (work / "scripts" / "lib").mkdir(parents=True)
    (work / "scripts" / "lib" / "system_deps.sh").write_text(_FRAGMENT, encoding="utf-8")
    stub_bin = work / "bin"
    stub_bin.mkdir()
    _write_stub(stub_bin / "apt-get", _FAKE_APT_GET)
    _write_stub(stub_bin / "sudo", _FAKE_SUDO)
    probe = work / "install_linux.sh"
    _build_install_linux_system_tier_probe(probe)
    log = work / "apt-calls.log"
    env = dict(os.environ)
    env.update({
        "PATH": f"{stub_bin}{os.pathsep}{env['PATH']}",
        "PROVISION_APT_LOG": str(log),
        "PROVISION_APT_FAIL_NAMES": fail_names,
    })
    env.pop("BD_SKIP_SYSTEM_DEPS", None)
    done = subprocess.run([_BASH, str(probe)], cwd=str(work), env=env,
                          capture_output=True, text=True, timeout=180)
    calls = log.read_text(encoding="utf-8").splitlines() if log.is_file() else []
    return done, [c for c in calls if " install " in f" {c} "]


def test_one_unresolvable_group_does_not_cost_the_other_groups(tmp_path):
    done, installs = _run(tmp_path, fail_names="npm")
    assert done.returncode == 0, done.stderr
    installed = [c for c in installs if "npm" not in c.split()]
    assert any("xvfb" in c.split() for c in installed), (
        "o1567 RED: apt failed on npm and no install call without npm followed -- "
        f"xvfb/ffmpeg/git were never installed. apt install calls: {installs}"
    )
    for g, names in _GROUPS.items():
        if g == "node":
            continue
        assert any(set(names.split()) <= set(c.split()) for c in installed), (g, installs)
    out = done.stdout + done.stderr
    assert "system package group(s) not installed: node" in out, out


def test_all_resolvable_is_still_exactly_one_apt_call(tmp_path):
    done, installs = _run(tmp_path, fail_names="")
    assert done.returncode == 0, done.stderr
    assert len(installs) == 1, installs
    assert set(installs[0].split()) >= set(" ".join(_GROUPS.values()).split())
    assert "not installed" not in done.stdout + done.stderr
