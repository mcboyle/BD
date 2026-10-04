"""O1735 R1 (o1698-guard-python-venv): bd-guard's python on a fresh VM.

Outage 13:38-14:44Z: bd-guard ran the say prevalidator under /usr/bin/python3, which lacks mcp on fresh VMs, so every
Bash was refused; and plugins/bd-fleet-mcp/server.py was a link into a hub-only tree, so a VM imported whatever local
copy it happened to hold. The candidate (harness-work/FIX/o1698-guard-python-venv, deployed from bd-persist, not this
repo) makes the venv the manifest default with a named fallback, ships server.py beside cli.py, and makes
`bd-nfs-share verify <host>` refuse a VM whose prevalidator does not exit 0 there.

BD_O1698_GUARD_PYTHON_VENV_CANDIDATE is the candidate root (bd-guard/, bd-fleet-mcp/, harness/ beneath it).
Hermetic: no live VM; ssh is a fake that runs the "VM" side locally; every path the tool reads lives in tmp_path.
The python that has mcp is BD_O1698_MCP_PYTHON, else the hub bd-mcp venv, else this interpreter if it imports mcp.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_GUARD_PYTHON_VENV_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

VENV_PYTHON = "/home/mboyle/bd-persist/harness/bd-mcp/venv/bin/python"
FALLBACK = "/usr/bin/python3"
BENIGN = json.dumps({"tool_name": "Bash", "tool_input": {"command": "true"}})
VM = "10.9.9.1"

FAKE_SSH = r"""#!/bin/bash
while [ $# -gt 0 ]; do case "$1" in -n) shift ;; -o) shift 2 ;; -*) shift ;; *) break ;; esac; done
host=$1; shift
printf '%s\n' "$host" >> "$FAKE_LOG/ssh-hosts.log"
HOME="$FAKE_VMHOME" exec bash -c "$*" < /dev/null
"""


def _exe(p: Path, body: str) -> Path:
    p.write_text(body, encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


def _imports_mcp(python: str) -> bool:
    if not (python and os.access(python, os.X_OK)):
        return False
    return subprocess.run([python, "-c", "import mcp"], capture_output=True, timeout=60, check=False).returncode == 0


@pytest.fixture(scope="module")
def cand() -> Path:
    root = Path(CANDIDATE)
    for rel in ("bd-guard/.claude-plugin/plugin.json", "bd-fleet-mcp/cli.py", "harness/bd-nfs-share"):
        assert (root / rel).exists() or (root / rel).is_symlink(), f"candidate lacks {rel}: {root}"
    return root


@pytest.fixture(scope="module")
def mcp_python() -> str:
    for p in (os.environ.get("BD_O1698_MCP_PYTHON", ""), VENV_PYTHON, sys.executable):
        if _imports_mcp(p):
            return p
    pytest.fail("COULD NOT LOOK: no interpreter here imports mcp (set BD_O1698_MCP_PYTHON)")


@pytest.fixture
def no_mcp_python(tmp_path: Path, mcp_python: str) -> str:
    """A 'fresh VM system python': the same interpreter with site disabled, so mcp is not importable."""
    p = str(_exe(tmp_path / "system-python3", f'#!/bin/sh\nexec {mcp_python} -S "$@"\n'))
    # positive control beside the zero: the probe can say yes with mcp_python and says no here
    assert _imports_mcp(mcp_python) and not _imports_mcp(p)
    return p


def _vm_view(cand: Path, dest: Path) -> Path:
    """plugins/bd-fleet-mcp as a VM sees it over NFS: regular files arrive; a link into a hub-only tree dangles."""
    dest.mkdir(parents=True)
    shutil.copy2(cand / "bd-fleet-mcp" / "cli.py", dest / "cli.py")
    src = cand / "bd-fleet-mcp" / "server.py"
    if src.is_symlink():
        os.symlink(str(dest.parent / "hub-only" / "server.py"), dest / "server.py")
    else:
        shutil.copy2(src, dest / "server.py")
    return dest


def _hook(python: str, cli: Path) -> subprocess.CompletedProcess:
    return subprocess.run([python, str(cli), "hook"], input=BENIGN, capture_output=True, text=True, timeout=60,
                          check=False, cwd=str(cli.parent.parent))


# ---- 1. the manifest: venv default, named fallback, nothing restated ----

def test_manifest_defaults_to_the_venv_with_a_named_fallback(cand: Path) -> None:
    cfg = json.loads((cand / "bd-guard/.claude-plugin/plugin.json").read_text())["userConfig"]
    assert cfg["python"]["default"] == VENV_PYTHON
    assert cfg["pythonFallback"]["default"] == FALLBACK


def test_register_resolves_the_interpreter_and_restates_no_path(cand: Path) -> None:
    reg = (cand / "bd-guard/hooks/register.tsx").read_text()
    assert "interpreter($, python, pythonFallback)" in reg
    assert VENV_PYTHON not in reg and FALLBACK not in reg, "Rule 37: interpreter paths live in plugin.json only"
    assert "[python: ${py.via}]" in reg


# ---- 2. cli.py imports server from its own (NFS-shared) directory ----

def test_server_ships_beside_cli_not_as_a_hub_only_link(cand: Path) -> None:
    srv = cand / "bd-fleet-mcp" / "server.py"
    assert not srv.is_symlink(), f"server.py is a link ({os.readlink(srv)}): it dangles on a VM"
    assert srv.is_file()


def test_prevalidator_runs_on_a_vm_view_of_the_plugin(cand: Path, tmp_path: Path, mcp_python: str) -> None:
    cli = _vm_view(cand, tmp_path / "plugins" / "bd-fleet-mcp") / "cli.py"
    r = _hook(mcp_python, cli)
    assert r.returncode == 0, (r.returncode, r.stderr[-600:])


def test_unimportable_server_is_a_named_refusal_not_a_traceback(cand: Path, tmp_path: Path, mcp_python: str) -> None:
    d = tmp_path / "plugins" / "bd-fleet-mcp"
    d.mkdir(parents=True)
    shutil.copy2(cand / "bd-fleet-mcp" / "cli.py", d / "cli.py")
    os.symlink(str(tmp_path / "hub-only" / "server.py"), d / "server.py")
    r = _hook(mcp_python, d / "cli.py")
    assert r.returncode not in (0, 2), r  # never allowed, never mistaken for a say refusal
    assert "bd-fleet-mcp cli: cannot import server (" in r.stderr, r.stderr
    assert f") from {d / 'server.py'} -> {tmp_path / 'hub-only' / 'server.py'}" in r.stderr, r.stderr
    assert f"under {mcp_python}" in r.stderr


def test_fresh_vm_python_without_mcp_is_refused_by_name(cand: Path, tmp_path: Path, no_mcp_python: str) -> None:
    """Today's outage: the fallback (system python) lacks mcp -> the gate refuses, and says why and under what."""
    cli = _vm_view(cand, tmp_path / "plugins" / "bd-fleet-mcp") / "cli.py"
    r = _hook(no_mcp_python, cli)
    assert r.returncode not in (0, 2), r
    assert "No module named 'mcp'" in r.stderr and "cannot import server" in r.stderr, r.stderr


# ---- 3. bd-nfs-share verify: a host is prepped only if the prevalidator exits 0 there ----

@pytest.fixture
def nfs(cand: Path, tmp_path: Path, mcp_python: str, no_mcp_python: str) -> dict:
    log = tmp_path / "log"
    log.mkdir()
    hosts = tmp_path / "hosts"
    hosts.write_text(f"hub 10.0.0.1 local\nvm1 {VM}\n")
    plugin = _vm_view(cand, tmp_path / "vm" / "plugins" / "bd-fleet-mcp")
    venv = tmp_path / "vm" / "venv" / "bin" / "python"
    manifest = tmp_path / "plugin.json"
    manifest.write_text(json.dumps({"userConfig": {
        "python": {"default": str(venv)}, "pythonFallback": {"default": no_mcp_python},
        "sayValidator": {"default": str(plugin / "cli.py")}}}))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("BD_", "FAKE_"))}
    env.update(
        LC_ALL="C",
        BD_HOSTS_FILE=str(hosts),
        BD_NFS_SERVER="10.0.0.1",
        BD_NFS_SEAT_VMS="vm1",
        BD_NFS_SSH=str(_exe(tmp_path / "fake-ssh", FAKE_SSH)),
        BD_NFS_GUARD_MANIFEST=str(manifest),
        BD_NFS_LOG=str(log / "nfs-share.log"),
        BD_NFS_VERIFY_DIR=str(tmp_path / "verify"),
        BD_SEAT="bd-o1698-guard-python-venv-test",
        FAKE_LOG=str(log),
    )
    # The "VM" has its own home, already linked (vm-home-links), so verify's A7a order gate reaches the probe.
    vmhome = tmp_path / "vmhome"
    vmhome.mkdir()
    root = env.get("BD_NFS_MOUNT_ROOT", "/mnt/bd")
    for name in ("bd-persist", "bd-local-wt", "bd-review-wt", "bd-cuts", "bin"):
        (vmhome / name).symlink_to(f"{root}/{name}")
    env["FAKE_VMHOME"] = str(vmhome)
    return {"tmp": tmp_path, "env": env, "venv": venv, "manifest": manifest, "plugin": plugin, "log": log,
            "mcp_python": mcp_python, "no_mcp_python": no_mcp_python}


def _verify(cand: Path, fx: dict, **extra: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([str(cand / "harness" / "bd-nfs-share"), "verify", "vm1"], env={**fx["env"], **extra},
                              capture_output=True, text=True, timeout=60, check=False)
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"verify ran the fs probes instead of stopping on the prevalidator: {exc}")


def _venv(fx: dict, body: str) -> None:
    fx["venv"].parent.mkdir(parents=True, exist_ok=True)
    _exe(fx["venv"], body)


def test_verify_fails_naming_the_probe_when_the_fallback_lacks_mcp(cand: Path, nfs: dict) -> None:
    r = _verify(cand, nfs)
    line = [x for x in r.stdout.splitlines() if x.startswith("VERIFY ")]
    assert r.returncode == 4, (r.stdout, r.stderr)
    assert len(line) == 1 and line[0].endswith("RESULT=FAIL"), r.stdout
    assert f"prevalidator=FAIL prevalidator_rc=3 prevalidator_python={nfs['no_mcp_python']} prevalidator_fallback=1" in line[0]
    assert "prevalidator_reason=bd-fleet-mcp_cli:_cannot_import_server_(ModuleNotFoundError:_No_module_named_'mcp')" in line[0]
    assert not (nfs["tmp"] / "verify").exists(), "the fs probes must not start for a host whose guard cannot run"
    assert "RESULT=FAIL prevalidator=FAIL" in (nfs["log"] / "nfs-share.log").read_text()


def test_verify_uses_the_venv_when_it_resolves_on_the_vm(cand: Path, nfs: dict) -> None:
    _venv(nfs, '#!/bin/sh\necho "venv ran $*" >&2\nexit 5\n')
    r = _verify(cand, nfs)
    assert r.returncode == 4, (r.stdout, r.stderr)
    assert f"prevalidator=FAIL prevalidator_rc=5 prevalidator_python={nfs['venv']} prevalidator_fallback=0" in r.stdout
    assert "prevalidator_reason=venv_ran_" in r.stdout


def test_verify_passes_the_probe_with_a_working_venv(cand: Path, nfs: dict) -> None:
    _venv(nfs, f'#!/bin/sh\nexec {nfs["mcp_python"]} "$@"\n')
    blocker = nfs["tmp"] / "not-a-dir"
    blocker.write_text("x")
    # the first step after the probe is mkdir of the verify dir; a file in its way stops verify THERE, proving the
    # probe passed without running the minutes-long fs probes
    r = _verify(cand, nfs, BD_NFS_VERIFY_DIR=str(blocker / "verify"))
    assert "prevalidator=FAIL" not in r.stdout, r.stdout
    assert r.returncode == 3 and "cannot create" in r.stderr, (r.returncode, r.stdout, r.stderr)


def test_verify_unreadable_manifest_is_could_not_look_never_ok(cand: Path, nfs: dict) -> None:
    r = _verify(cand, nfs, BD_NFS_GUARD_MANIFEST=str(nfs["tmp"] / "absent.json"))
    assert r.returncode == 4, (r.stdout, r.stderr)
    assert "prevalidator=FAIL prevalidator_reason=COULD-NOT-LOOK:guard-manifest-lacks-python/pythonFallback/sayValidator:" in r.stdout


def test_verify_unreachable_host_keeps_exit_5(cand: Path, nfs: dict) -> None:
    _exe(Path(nfs["env"]["BD_NFS_SSH"]), "#!/bin/sh\nexit 255\n")
    r = _verify(cand, nfs)
    assert r.returncode == 5 and "unreachable" in r.stderr, (r.returncode, r.stderr)
