"""O1666 (CUT1b of O1664): bd-nfs-share -- hub fleet trees over NFSv4 at identical paths on the seat VMs.

Ruling harness-work/O1664/ANSWER-O1666-shared-fs.md: exports only for the named seat VMs with ssh up (never wrk-188..191,
windows hosts or the hub); VM mount units at the same paths; uid/gid mismatch refused; existing local data at a mount path
refused and left untouched (rule 22); verify = nonce both ways + cross-host flock + latency; rollback per host.

The tool is deployed from bd-persist, not this repo: BD_O1666_NFS_CANDIDATE is the absolute path of the candidate script.
Hermetic (O1641): no live host. ssh is a fake (BD_NFS_SSH) that runs the "VM" side locally with shims for sudo/systemctl/
mountpoint/findmnt/mount.nfs4 first on PATH; hub sudo/exportfs are fakes too; every path lives in tmp_path.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1666_NFS_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

TEST_SHIMS = "/home/mboyle/bd-persist/harness/test-shims"
SERVER = "10.0.0.1"
VM1, VM2 = "10.9.9.1", "10.9.9.2"

FAKE_SSH = r"""#!/bin/bash
while [ $# -gt 0 ]; do case "$1" in -n) shift ;; -o) shift 2 ;; -*) shift ;; *) break ;; esac; done
host=$1; shift; cmd="$*"
printf '%s\n' "$host" >> "$FAKE_LOG/ssh-hosts.log"
case " $FAKE_DOWN " in *" $host "*) exit 255 ;; esac
[ -n "${FAKE_REMAP:-}" ] && { mkdir -p "$FAKE_ROOT/vm-$host$FAKE_REMAP"; cmd=${cmd//"$FAKE_REMAP"/"$FAKE_ROOT/vm-$host$FAKE_REMAP"}; }
PATH="$FAKE_BIN:$PATH" exec bash -c "$cmd"
"""
SUDO = '#!/bin/bash\n[ "$1" = -n ] && shift\necho "$*" >> "$FAKE_LOG/sudo.log"\nexec "$@"\n'
EXPORTFS = '#!/bin/sh\necho "exportfs $*" >> "$FAKE_LOG/exportfs.log"\n'
MOUNT_NFS4 = "#!/bin/sh\nexit 0\n"
# enable --now <unit>: the "mount" is a marker file holding the unit's What=; disable --now removes it.
SYSTEMCTL = r"""#!/bin/bash
echo "systemctl $*" >> "$FAKE_LOG/systemctl.log"
[ "$2" = --now ] || exit 0
p=$(systemd-escape -u -p "${3%.mount}"); m="$FAKE_MOUNTS/$(printf '%s' "$p" | tr / _)"
mkdir -p "$FAKE_MOUNTS"
case "$1" in
  enable) sed -n 's/^What=//p' "$FAKE_UNIT_DIR/$3" > "$m" ;;
  disable) rm -f "$m" ;;
esac
"""
MOUNTPOINT = r"""#!/bin/bash
[ "$1" = -q ] && shift
[ -e "$FAKE_MOUNTS/$(printf '%s' "$1" | tr / _)" ]
"""
FINDMNT = r"""#!/bin/bash
p=${!#}; cat "$FAKE_MOUNTS/$(printf '%s' "$p" | tr / _)"
"""
LYING_FLOCK = '#!/bin/sh\n[ -n "${FAKE_FLOCK_LIES:-}" ] && exit 0\nexec /usr/bin/flock "$@"\n'


def _exe(p: Path, body: str) -> Path:
    p.write_text(body, encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


@pytest.fixture
def fx(tmp_path: Path) -> dict:
    cand = Path(CANDIDATE)
    assert cand.is_file() and os.access(cand, os.X_OK), f"BD_O1666_NFS_CANDIDATE supplied but not executable: {cand}"
    log = tmp_path / "log"
    log.mkdir()
    fbin = tmp_path / "fakebin"
    fbin.mkdir()
    for name, body in (("sudo", SUDO), ("systemctl", SYSTEMCTL), ("mountpoint", MOUNTPOINT), ("findmnt", FINDMNT),
                       ("mount.nfs4", MOUNT_NFS4), ("flock", LYING_FLOCK)):
        _exe(fbin / name, body)
    hosts = tmp_path / "hosts"
    hosts.write_text(f"hub {SERVER} local\nvm1 {VM1}\nvm2 {VM2}\nwin 10.9.9.3 windows vm oracle\nwrk-188 10.9.9.4\n")
    paths = [tmp_path / "vmfs" / "bd-persist", tmp_path / "vmfs" / "bin"]
    hubdirs = [tmp_path / "hubfs" / "bd-persist", tmp_path / "hubfs" / "bin"]
    for d in hubdirs:
        d.mkdir(parents=True)
    units = tmp_path / "units"
    units.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("BD_", "FAKE_"))}
    env.update(
        LC_ALL="C",
        PATH=f"{TEST_SHIMS}:{os.environ['PATH']}",
        BD_HOSTS_FILE=str(hosts),
        BD_NFS_SERVER=SERVER,
        BD_NFS_SEAT_VMS="vm1 vm2 win wrk-188 ghost",
        BD_NFS_SSH=str(_exe(tmp_path / "fake-ssh", FAKE_SSH)),
        BD_NFS_SUDO=str(fbin / "sudo"),
        BD_NFS_EXPORTFS=str(_exe(tmp_path / "fake-exportfs", EXPORTFS)),
        BD_NFS_EXPORTS_FILE=str(tmp_path / "etc-exports.d" / "bd-fleet.exports"),
        BD_NFS_UNIT_DIR=str(units),
        BD_NFS_VERIFY_DIR=str(tmp_path / "hubfs" / "bd-persist" / "state" / "nfs-verify"),
        BD_NFS_LOG=str(log / "nfs-share.log"),
        BD_NFS_HOLD="2",
        BD_SEAT="bd-o1666-test",
        FAKE_LOG=str(log),
        FAKE_BIN=str(fbin),
        FAKE_ROOT=str(tmp_path),
        FAKE_DOWN=VM2,
        FAKE_MOUNTS=str(tmp_path / "mounts"),
        FAKE_UNIT_DIR=str(units),
    )
    (tmp_path / "etc-exports.d").mkdir()
    return {"tmp": tmp_path, "env": env, "log": log, "paths": paths, "hubdirs": hubdirs, "units": units}


def _run(fx: dict, *args: str, paths: list[Path] | None = None, **extra: str) -> subprocess.CompletedProcess:
    env = {**fx["env"], "BD_NFS_PATHS": " ".join(map(str, paths if paths is not None else fx["paths"])), **extra}
    return subprocess.run([CANDIDATE, *args], env=env, capture_output=True, text=True, timeout=120, check=False)


def _logged(fx: dict, name: str) -> str:
    f = fx["log"] / name
    return f.read_text() if f.exists() else ""


def test_hosts_named_seat_vms_only_and_up_filter(fx: dict) -> None:
    r = _run(fx, "hosts", "--all")
    assert r.stdout.split("\n")[:-1] == [f"vm1 {VM1}", f"vm2 {VM2}"], (r.stdout, r.stderr)
    assert "ABSENT win" in r.stderr and "SKIP wrk-188 (never: O1666)" in r.stderr and "ABSENT ghost" in r.stderr
    up = _run(fx, "hosts")
    assert up.stdout == f"vm1 {VM1}\n" and f"DOWN vm2 {VM2}" in up.stderr, (up.stdout, up.stderr)
    assert "10.9.9.3" not in _logged(fx, "ssh-hosts.log") and "10.9.9.4" not in _logged(fx, "ssh-hosts.log")


def test_exports_dry_run_prints_only_up_vms_and_writes_nothing(fx: dict) -> None:
    r = _run(fx, "exports", paths=fx["hubdirs"])
    assert r.returncode == 0, r.stderr
    opts = "rw,sync,root_squash,no_subtree_check,sec=sys"
    for d in fx["hubdirs"]:
        assert f"{d} {VM1}({opts})\n" in r.stdout, r.stdout
    assert VM2 not in r.stdout and "10.9.9.4" not in r.stdout
    assert not Path(fx["env"]["BD_NFS_EXPORTS_FILE"]).exists() and _logged(fx, "exportfs.log") == ""


def test_exports_apply_backs_up_and_reloads(fx: dict) -> None:
    ex = Path(fx["env"]["BD_NFS_EXPORTS_FILE"])
    ex.write_text("# previous\n")
    r = _run(fx, "exports", "--apply", paths=fx["hubdirs"])
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert f"{fx['hubdirs'][0]} {VM1}(" in ex.read_text()
    baks = list(ex.parent.glob("bd-fleet.exports.pre-*"))
    assert len(baks) == 1 and baks[0].read_text() == "# previous\n"
    assert "exportfs -ra" in _logged(fx, "exportfs.log")


def test_mount_apply_writes_units_at_identical_paths_and_is_idempotent(fx: dict) -> None:
    r = _run(fx, "mount", "vm1", "--apply")
    assert r.returncode == 0, (r.stdout, r.stderr)
    for p in fx["paths"]:
        unit = subprocess.run(["systemd-escape", "-p", "--suffix=mount", str(p)], capture_output=True, text=True,
                              check=True).stdout.strip()
        body = (fx["units"] / unit).read_text()
        assert f"What={SERVER}:{p}\n" in body and f"Where={p}\n" in body and "Type=nfs4\n" in body, body
        assert f"MOUNTED {p}" in r.stdout
        assert f"enable --now {unit}" in _logged(fx, "systemctl.log")
    again = _run(fx, "mount", "vm1", "--apply")
    assert again.returncode == 0 and again.stdout.count("OK ALREADY-MOUNTED") == 2, again.stdout


def test_mount_refuses_existing_local_data_and_touches_nothing(fx: dict) -> None:
    keep = fx["paths"][1] / "local-tool"
    keep.parent.mkdir(parents=True)
    keep.write_text("vm-local")
    r = _run(fx, "mount", "vm1", "--apply")
    assert r.returncode == 3 and f"REFUSED EXISTING-LOCAL-DATA {fx['paths'][1]} (1 entries)" in r.stdout, r.stdout
    assert keep.read_text() == "vm-local"
    assert list(fx["units"].iterdir()) == [], "a refused host got units (phase 2 ran)"
    assert _logged(fx, "systemctl.log") == ""


def test_mount_refuses_uid_mismatch(fx: dict) -> None:
    r = _run(fx, "mount", "vm1", "--apply", BD_NFS_HUB_UID="4242")
    assert r.returncode == 3 and "REFUSED UID-MISMATCH" in r.stdout and "hub is 4242:" in r.stdout, r.stdout
    assert list(fx["units"].iterdir()) == []


def test_mount_refuses_non_seat_and_down_hosts(fx: dict) -> None:
    r = _run(fx, "mount", "wrk-188", "--apply")
    assert r.returncode == 3 and "not one of the O1666 seat VMs" in r.stderr, r.stderr
    assert _logged(fx, "ssh-hosts.log") == ""
    d = _run(fx, "mount", "vm2", "--apply")
    assert d.returncode == 5 and "unreachable" in d.stderr, d.stderr


def test_mount_dry_run_checks_but_writes_nothing(fx: dict) -> None:
    r = _run(fx, "mount", VM1)
    assert r.returncode == 0 and "DRY all paths pass" in r.stdout, r.stdout
    assert list(fx["units"].iterdir()) == [] and _logged(fx, "sudo.log") == ""


def test_verify_passes_on_a_shared_fs(fx: dict) -> None:
    r = _run(fx, "verify", "vm1")
    assert r.returncode == 0, (r.stdout, r.stderr)
    for k in ("nonce_hub_to_vm=OK", "nonce_vm_to_hub=OK", "vm_blocked_while_hub_holds=OK", "vm_locks_after_release=OK",
              "hub_blocked_while_vm_holds=OK", "RESULT=PASS"):
        assert k in r.stdout, r.stdout
    assert "latency_ms_per_op=UNKNOWN" not in r.stdout


def test_verify_fails_when_the_vm_does_not_see_the_hub_tree(fx: dict) -> None:
    r = _run(fx, "verify", "vm1", FAKE_REMAP=fx["env"]["BD_NFS_VERIFY_DIR"])
    assert r.returncode == 4 and "nonce_hub_to_vm=FAIL" in r.stdout and "RESULT=FAIL" in r.stdout, r.stdout


def test_verify_fails_when_locks_do_not_cross_hosts(fx: dict) -> None:
    r = _run(fx, "verify", "vm1", FAKE_FLOCK_LIES="1")
    assert r.returncode == 4 and "vm_blocked_while_hub_holds=FAIL" in r.stdout, r.stdout
    assert "nonce_hub_to_vm=OK" in r.stdout


def test_rollback_keeps_unit_files_and_hub_exports(fx: dict) -> None:
    assert _run(fx, "mount", "vm1", "--apply").returncode == 0
    dry = _run(fx, "rollback", "vm1")
    assert dry.returncode == 0 and dry.stdout.count("DRY would disable") == 2
    r = _run(fx, "rollback", "vm1", "--apply")
    assert r.returncode == 0 and r.stdout.count("ROLLED-BACK") == 2, r.stdout
    names = sorted(p.name for p in fx["units"].iterdir())
    assert len(names) == 2 and all(".mount.pre-" in n for n in names), names
    assert "disable --now" in _logged(fx, "systemctl.log")
    ex = Path(fx["env"]["BD_NFS_EXPORTS_FILE"])
    ex.write_text("x\n")
    h = _run(fx, "rollback", "--hub", "--apply")
    assert h.returncode == 0 and not ex.exists() and len(list(ex.parent.glob("*.pre-*"))) == 1, h.stdout


# ---- r2 (O1673): bd1..bd4 join the default seat list; an unlistable dir is never FREE (lens F1) -----------------------
SEAT_22 = ("test1 test3 test4 test6 test7 fresh149 test3big spare1 spare2 spare4 spare5 spare6 spare7 spare8 spare9 "
           "spare10 spare11 spare12 bd1 bd2 bd3 bd4").split()


def test_default_seat_list_is_the_22_vms_incl_bd1_to_bd4(fx: dict) -> None:
    hosts = Path(fx["env"]["BD_HOSTS_FILE"])
    rows = [f"test5 {SERVER} local", "win-oracle 10.9.8.250 windows vm oracle"]
    rows += [f"wrk-{n} 10.9.8.{n - 100}" for n in (188, 189, 190, 191)]
    rows += [f"{n} 10.9.7.{i + 1}" for i, n in enumerate(SEAT_22)]
    hosts.write_text("\n".join(rows) + "\n")
    env = {k: v for k, v in fx["env"].items() if k != "BD_NFS_SEAT_VMS"}   # the script's own default list
    r = subprocess.run([CANDIDATE, "hosts", "--all"], env=env, capture_output=True, text=True, timeout=60, check=False)
    got = [ln.split()[0] for ln in r.stdout.splitlines()]
    assert got == SEAT_22, (r.stdout, r.stderr)
    assert {"bd1", "bd2", "bd3", "bd4"} <= set(got) and len(got) == 22
    for out in ("test5", "win-oracle", "wrk-188", "wrk-189", "wrk-190", "wrk-191", SERVER):
        assert out not in r.stdout, f"{out} resolved as a seat VM:\n{r.stdout}"
    assert "ABSENT" not in r.stderr, r.stderr


def test_seat_name_resolving_to_the_hub_ip_is_skipped(fx: dict) -> None:
    with Path(fx["env"]["BD_HOSTS_FILE"]).open("a") as f:
        f.write(f"hubalias {SERVER}\n")
    r = _run(fx, "hosts", "--all", BD_NFS_SEAT_VMS="vm1 hubalias")
    assert r.stdout == f"vm1 {VM1}\n" and "SKIP hubalias (is the hub)" in r.stderr, (r.stdout, r.stderr)


def test_unlistable_dir_with_local_data_is_refused_not_free(fx: dict) -> None:
    d = fx["paths"][1]
    d.mkdir(parents=True)
    (d / "local-tool").write_text("vm-local")
    d.chmod(0o300)   # write+search, no read: ls -A fails (a root-made 0700 dir looks the same to this user)
    try:
        try:
            os.listdir(d)
        except PermissionError:
            pass
        else:
            pytest.skip("precondition unmet: this uid can list a 0300 dir (root?) -- the unreadable case cannot be built")
        r = _run(fx, "mount", "vm1", "--apply")
        assert r.returncode == 3 and f"REFUSED UNREADABLE {d}" in r.stdout, r.stdout
        assert f"OK FREE {d}" not in r.stdout
        assert list(fx["units"].iterdir()) == [] and _logged(fx, "systemctl.log") == "", "units written over it"
    finally:
        d.chmod(0o755)
    assert (d / "local-tool").read_text() == "vm-local"
