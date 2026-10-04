"""O1735 R1 addenda 1-5 (o1698-host-prep-addenda): `bd-nfs-share host-prep <host> [--apply]` + bd-auth-sync KEEP-NEWER.

PM hand-applied five gaps on .183/.120/.195/.80/.52; this row makes them host prep:
  A1 the hub ~/ harness links into bd-persist/bd-local-wt/bd-review-wt/bd-cuts + ~/.config/bd minus secrets
  A2 host prep runs bd-auth-sync          A4 /usr/local/bin/claude -> ~/.local/bin/claude + Node >= 20 (NodeSource)
  A5 the fleet ssh key + known_hosts      A3 auth-sync keeps a NEWER VM file only if a seat there ANSWERED since.
R1 addenda 6+7 (r2, same entry point): A6a rsync ~/bd-carry; A6b ~/UniversalSwarmOS .git from the hub, HEAD must equal
  the hub HEAD; A7a fixed order mount -> vm-home-links -> verify (verify/host-prep before the links = REFUSED, named);
  A7b a stale known_hosts key is REPLACED (purge + backup), seat pubkeys appended ONCE to each capacity box.

The harness is deployed from bd-persist, not this repo: BD_O1698_HOST_PREP_CANDIDATE is the absolute path of the
candidate directory (bd-nfs-share + bd-auth-sync). Hermetic: ssh is a fake that runs the "VM" side locally with
HOME=<tmp>/vm-<ip> (hub-home paths in the command are remapped there, stdin is closed on -n exactly like ssh -n);
sudo/apt-get/curl/gpg/node/dpkg/ssh-keyscan are shims first on PATH; every path lives in tmp_path.
"""

from __future__ import annotations

import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_HOST_PREP_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

LIB = os.environ.get("BD_O1698_HOST_PREP_LIB", "/home/mboyle/bd-persist/harness/lib_remote_seat.sh")
SERVER, VM, EXTRA, UNKNOWN = "10.0.0.1", "10.9.9.1", "10.0.0.52", "10.0.0.83"
NODE_FPR = "6F71F525282841EEDAF851B42F59B5F99B1BE0B4"

FAKE_SSH = r"""#!/bin/bash
while [ $# -gt 0 ]; do case "$1" in -n) exec </dev/null; shift ;; -o) shift 2 ;; -*) shift ;; *) break ;; esac; done
host=$1; shift; cmd="$*"
printf '%s\n' "$host" >> "$FAKE_LOG/ssh-hosts.log"
case " ${FAKE_DOWN:-} " in *" $host "*) exit 255 ;; esac
vm="$FAKE_ROOT/vm-$host"; mkdir -p "$vm"
cmd=${cmd//"$FAKE_HUB"/"$vm"}
[ -n "${FAKE_STDIN_LOG:-}" ] || HOME="$vm" PATH="$FAKE_BIN:$PATH" exec bash -c "$cmd"
# an rsync server's stdin is the rsync protocol: a tee in it deadlocks the client's final wait; log its argv instead
case "$cmd" in "rsync --server"*) printf 'rsync-server %s\n' "$cmd" >> "$FAKE_STDIN_LOG"; HOME="$vm" PATH="$FAKE_BIN:$PATH" exec bash -c "$cmd" ;; esac
tee -a "$FAKE_STDIN_LOG" | HOME="$vm" PATH="$FAKE_BIN:$PATH" bash -c "$cmd"
"""
SUDO = """#!/bin/bash
[ "$1" = -n ] && shift
[ -n "${FAKE_NO_SUDO:-}" ] && exit 1
echo "$*" >> "$FAKE_LOG/sudo.log"
exec "$@"
"""
NODE = '#!/bin/sh\n[ -f "$FAKE_ROOT/node-version" ] || exit 127\ncat "$FAKE_ROOT/node-version"\n'
APT = """#!/bin/sh
echo "apt-get $*" >> "$FAKE_LOG/apt.log"
case " $* " in *" install "*nodejs*) echo "${FAKE_NODE_INSTALL:-v20.19.0}" > "$FAKE_ROOT/node-version" ;; esac
"""
CURL = """#!/bin/bash
echo "curl $*" >> "$FAKE_LOG/curl.log"
while [ $# -gt 0 ]; do [ "$1" = -o ] && { echo ARMORED-NODESOURCE-KEY > "$2"; shift; }; shift; done
"""
GPG = """#!/bin/bash
echo "gpg $*" >> "$FAKE_LOG/gpg.log"
out=""; show=0; dearmor=0; last=""
while [ $# -gt 0 ]; do case "$1" in --dearmor) dearmor=1 ;; --show-keys) show=1 ;; -o) out=$2; shift ;; esac; last=$1; shift; done
[ "$dearmor" = 1 ] && { cp "$last" "$out"; exit 0; }
[ "$show" = 1 ] && printf 'pub:-:255:22:2F59B5F99B1BE0B4:\\nfpr:::::::::%s:\\n' "${FAKE_FPR}"
exit 0
"""
DPKG = '#!/bin/sh\necho amd64\n'
KEYSCAN = '#!/bin/sh\necho "ssh-keyscan $*" >> "$FAKE_LOG/keyscan.log"\n'
FINDMNT = '#!/bin/sh\n[ -n "${FAKE_NO_MOUNT:-}" ] && exit 1\necho nfs4\n'
NOOP_RSYNC = '#!/bin/sh\necho "rsync $*" >> "$FAKE_LOG/rsync-noop.log"\nexit 0\n'
LINK_NAMES = ("bd-persist", "bd-local-wt", "bd-review-wt", "bd-cuts", "bin")
TEST_SHIMS = "/home/mboyle/bd-persist/harness/test-shims"


def _real(tool: str) -> str:
    """The real binary behind the test-shims guard (which refuses any non-loopback destination). The tool's rsync is
    handed it through BD_NFS_RSYNC because its transport here is BD_NFS_SSH = the fake ssh: nothing leaves this box."""
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if d and os.path.realpath(d) != os.path.realpath(TEST_SHIMS) and os.access(os.path.join(d, tool), os.X_OK):
            return os.path.join(d, tool)
    raise AssertionError(f"no real {tool} on PATH")


def _exe(p: Path, body: str) -> Path:
    p.write_text(body, encoding="utf-8")
    p.chmod(0o755)
    return p


def _cand(name: str) -> Path:
    d = Path(CANDIDATE)
    assert d.is_dir(), f"BD_O1698_HOST_PREP_CANDIDATE supplied but not a directory: {d}"
    p = d / name
    assert p.is_file(), f"candidate {p} absent"
    return p


def _put(p: Path, body: str, mode: int) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    p.chmod(mode)
    return p


def _hostkey(tmp: Path, name: str) -> str:
    k = tmp / "hostkeys" / name
    k.parent.mkdir(exist_ok=True)
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(k)], check=True,
                   capture_output=True, timeout=60)
    return (k.parent / f"{name}.pub").read_text().strip()


@pytest.fixture
def fx(tmp_path: Path) -> dict:
    assert Path(LIB).is_file(), f"remote-seat lib absent: {LIB}"
    logs, fbin, hub = tmp_path / "logs", tmp_path / "fakebin", tmp_path / "hubhome"
    for d in (logs, fbin, hub):
        d.mkdir()
    for name, body in (("sudo", SUDO), ("node", NODE), ("apt-get", APT), ("curl", CURL), ("gpg", GPG),
                       ("dpkg", DPKG), ("ssh-keyscan", KEYSCAN), ("findmnt", FINDMNT)):
        _exe(fbin / name, body)
    ssh = _exe(tmp_path / "fake-ssh", FAKE_SSH)
    # A1 links: two in-tree (absolute + relative), two that must NOT be replicated (outside, lexical escape)
    for f in ("a.sh", "b.sh"):
        _put(hub / "bd-persist/harness" / f, "#!/bin/sh\n", 0o755)
    (hub / "a.sh").symlink_to(hub / "bd-persist/harness/a.sh")
    (hub / "b.sh").symlink_to("bd-persist/harness/b.sh")
    (hub / "outside").symlink_to("/etc/hostname")
    (hub / "escape").symlink_to("bd-persist/../x")
    cfg = hub / ".config/bd"
    _put(cfg / "hosts", f"vm1 {VM}\n", 0o664)
    _put(cfg / "roles", "worker A\n", 0o664)
    _put(cfg / "worker-roles", "W\n", 0o600)
    _put(cfg / "operator-cookies/c.json", "{}\n", 0o600)
    for secret in ("gh-pat", "gitea-token", "unifi.env"):
        _put(cfg / secret, f"SECRET-{secret}\n", 0o600)
    _put(hub / ".ssh/bd_agent_ed25519", "FLEET-PRIVATE-KEY\n", 0o600)
    _put(hub / ".ssh/bd_agent_ed25519.pub", "ssh-ed25519 AAAAfleet bd-agent\n", 0o644)
    keys = {ip: _hostkey(tmp_path, ip) for ip in (SERVER, VM, EXTRA)}
    kh = _put(hub / ".ssh/known_hosts", "".join(f"{ip} {k}\n" for ip, k in keys.items()), 0o600)
    subprocess.run(["ssh-keygen", "-H", "-f", str(kh)], check=True, capture_output=True, timeout=60)
    cred = _put(hub / ".claude/.credentials.json", "HUB-TOKEN\n", 0o600)
    vm = tmp_path / f"vm-{VM}"
    _put(vm / ".local/bin/claude", "#!/bin/sh\n", 0o755)
    # A7a: the VM is already mounted and vm-home-linked (the fixture default); tests remove links to probe the order
    mnt = tmp_path / "mnt-bd"
    for n in LINK_NAMES:
        (vm / n).symlink_to(mnt / n)
    # A6: hub ~/bd-carry and a hub ~/UniversalSwarmOS checkout; A7b: the VM has a seat key of its own
    _put(hub / "bd-carry/notes.txt", "carry v1\n", 0o644)
    usos = hub / "UniversalSwarmOS"
    _put(usos / "README", "hub tree\n", 0o644)
    _git(usos, "init", "-q")
    _git(usos, "add", "README")
    _git(usos, "commit", "-q", "-m", "init")
    _put(vm / ".ssh/id_ed25519.pub", "ssh-ed25519 AAAAseat vm1-seat\n", 0o644)
    # A8: a template clone authorises only the template id_ed25519.pub; host prep adds the hub fleet key once
    _put(vm / ".ssh/authorized_keys", "ssh-ed25519 AAAAtemplate template-id\n", 0o600)
    (tmp_path / "usrlocal").mkdir()
    (tmp_path / "sys").mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("BD_", "FAKE_", "TMUX"))}
    env.update(
        LC_ALL="C",
        TMPDIR=str(tmp_path),
        BD_SEAT="bd-o1698-test",
        BD_HOSTS_FILE=str(cfg / "hosts"),
        BD_NFS_SERVER=SERVER, BD_NFS_SEAT_VMS="vm1", BD_NFS_NEVER="none",
        BD_NFS_HUB_UID=str(os.getuid()), BD_NFS_HUB_GID=str(os.getgid()),
        BD_NFS_SOURCE_ROOT=str(hub), BD_NFS_SSH=str(ssh), BD_NFS_LOG=str(logs / "nfs-share.log"),
        BD_NFS_VERIFY_DIR=str(tmp_path / "nfs-verify"),
        BD_NFS_MOUNT_ROOT=str(mnt), BD_HOSTPREP_CAPACITY=EXTRA, BD_NFS_RSYNC=_real("rsync"),
        BD_HOSTPREP_AUTH_SYNC=str(_cand("bd-auth-sync")),
        BD_HOSTPREP_CLAUDE_LINK=str(tmp_path / "usrlocal/claude"),
        BD_HOSTPREP_NODE_KEYRING=str(tmp_path / "sys/keyrings/nodesource.gpg"),
        BD_HOSTPREP_NODE_SOURCES=str(tmp_path / "sys/nodesource.sources"),
        BD_HOSTPREP_KNOWN_EXTRA=EXTRA,
        BD_REMOTE_SEAT_LIB=LIB, BD_REMOTE_SSH=str(ssh), BD_REMOTE_CTL_DIR=str(tmp_path),
        BD_HUB_HOST=SERVER, BD_SELF_HOSTS=SERVER,
        BD_SEAT_HOSTS=str(tmp_path / "SEAT-HOSTS.tsv"),
        BD_AUTH_SYNC_FILES=str(cred), BD_AUTH_SYNC_LOG=str(logs / "auth-sync.log"),
        BD_AUTH_SYNC_SAY_LOG=str(tmp_path / "say.log"),
        FAKE_LOG=str(logs), FAKE_ROOT=str(tmp_path), FAKE_HUB=str(hub), FAKE_BIN=str(fbin), FAKE_FPR=NODE_FPR,
    )
    (tmp_path / "say.log").write_text("")
    return {"tmp": tmp_path, "hub": hub, "vm": vm, "env": env, "logs": logs, "cred": cred, "keys": keys,
            "box": tmp_path / f"vm-{EXTRA}", "mnt": mnt}


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@x", *args], check=True,
                       capture_output=True, text=True, timeout=60)
    return r.stdout.strip()


def _prep(fx: dict, *args: str, **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(_cand("bd-nfs-share")), "host-prep", "vm1", *args],
                          env={**fx["env"], **extra}, capture_output=True, text=True, timeout=60, check=False)


def _items(out: str, step: str) -> dict[str, str]:
    return {ln.split(" ", 2)[1]: ln.split(" ", 2)[2] for ln in out.splitlines() if ln.startswith(f"{step} ")
            and len(ln.split(" ", 2)) == 3 and ln.split(" ", 2)[1] not in ("summary", "note")}


def _tree(root: Path) -> list[tuple[str, str]]:
    return sorted((str(p.relative_to(root)), os.readlink(p) if p.is_symlink() else p.read_text() if p.is_file()
                   else "<dir>") for p in root.rglob("*"))


# ---- host-prep ---------------------------------------------------------------------------------------------------

def test_dry_run_lists_exactly_each_steps_planned_actions_and_writes_nothing(fx: dict) -> None:
    before = _tree(fx["tmp"] / f"vm-{VM}")
    sent = fx["logs"] / "ssh-stdin.log"
    r = _prep(fx, FAKE_STDIN_LOG=str(sent))
    assert r.returncode == 0, (r.stdout, r.stderr)
    hub = fx["hub"]
    assert _items(r.stdout, "links") == {"a.sh": f"PLAN LINK -> {hub}/bd-persist/harness/a.sh",
                                         "b.sh": "PLAN LINK -> bd-persist/harness/b.sh"}, r.stdout
    assert _items(r.stdout, "config") == {"hosts": "PLAN COPY mode=664", "roles": "PLAN COPY mode=664",
                                          "worker-roles": "PLAN COPY mode=600",
                                          "operator-cookies/c.json": "PLAN COPY mode=600"}, r.stdout
    assert _items(r.stdout, "auth") == {str(fx["cred"]): "PLAN DRY-WOULD-COPY"}, r.stdout
    link = fx["env"]["BD_HOSTPREP_CLAUDE_LINK"]
    assert _items(r.stdout, "claude-path") == {link: f"PLAN sudo ln -s {fx['vm']}/.local/bin/claude {link}"}, r.stdout
    node = _items(r.stdout, "node")
    assert list(node) == ["absent"] and node["absent"].startswith("PLAN curl -fsSL https://deb.nodesource.com/gpgkey/")
    assert f"(fpr {NODE_FPR})" in node["absent"] and "apt-get install -y nodejs" in node["absent"], node
    assert _items(r.stdout, "ssh-key") == {"bd_agent_ed25519": "PLAN COPY mode=600",
                                           "bd_agent_ed25519.pub": "PLAN COPY mode=644"}, r.stdout
    assert _items(r.stdout, "known_hosts") == {f"{ip}/ssh-ed25519": "PLAN APPEND" for ip in (SERVER, EXTRA, VM)}
    assert _items(r.stdout, "carry") == {"notes.txt": "PLAN NEW"}, r.stdout
    assert _items(r.stdout, "usos") == {f"{hub}/UniversalSwarmOS": f"PLAN RSYNC .git (NO-DIR) HEAD={_git(hub / 'UniversalSwarmOS', 'rev-parse', 'HEAD')[:12]}"}
    assert _items(r.stdout, "authorized_keys") == {f"{VM}/bd-agent": "PLAN APPEND", f"{EXTRA}/bd-agent": "PLAN APPEND",
                                                   f"{EXTRA}/vm1-seat": "PLAN APPEND"}
    assert "STEP order FOUND NONE APPLIED 0 REFUSED 0" in r.stdout, r.stdout
    for step, n in (("links", 2), ("config", 4), ("carry", 1), ("usos", 1), ("auth", 1), ("claude-path", 1), ("node", 1),
                    ("ssh-key", 2), ("known_hosts", 3), ("authorized_keys", 3)):
        assert f"STEP {step} FOUND {n} APPLIED 0 REFUSED 0" in r.stdout, (step, r.stdout)
    assert "MODE=dry-run FOUND 19 APPLIED 0 REFUSED 0 RESULT=OK" in r.stdout, r.stdout
    assert _tree(fx["tmp"] / f"vm-{VM}") == before, "dry run wrote on the VM"
    for secret in ("FLEET-PRIVATE-KEY", "HUB-TOKEN", "SECRET-"):
        assert secret not in sent.read_text(), f"dry run sent {secret} bytes to the VM"
    assert {ln for ln in (fx["logs"] / "sudo.log").read_text().splitlines()} == {"true"}, "dry run ran sudo"
    for log in ("apt.log", "curl.log", "keyscan.log"):
        assert not (fx["logs"] / log).exists(), log


def test_apply_lands_links_files_keys_modes_then_rerun_finds_none(fx: dict) -> None:
    r = _prep(fx, "--apply")
    assert r.returncode == 0, (r.stdout, r.stderr)
    vm, hub = fx["vm"], fx["hub"]
    assert os.readlink(vm / "a.sh") == f"{hub}/bd-persist/harness/a.sh"
    assert os.readlink(vm / "b.sh") == "bd-persist/harness/b.sh"
    assert not (vm / "outside").exists() and not (vm / "escape").is_symlink()
    cfg = vm / ".config/bd"
    for rel, mode in (("hosts", 0o664), ("roles", 0o664), ("worker-roles", 0o600), ("operator-cookies/c.json", 0o600)):
        assert (cfg / rel).read_text() == (hub / ".config/bd" / rel).read_text(), rel
        assert stat.S_IMODE((cfg / rel).stat().st_mode) == mode, rel
    assert not list(cfg.glob(".host-prep.*")), "temp dir left behind"
    key, pub = vm / ".ssh/bd_agent_ed25519", vm / ".ssh/bd_agent_ed25519.pub"
    assert key.read_text() == "FLEET-PRIVATE-KEY\n" and stat.S_IMODE(key.stat().st_mode) == 0o600
    assert stat.S_IMODE(pub.stat().st_mode) == 0o644
    for ip in (SERVER, EXTRA, VM):
        found = subprocess.run(["ssh-keygen", "-F", ip, "-f", str(vm / ".ssh/known_hosts")], capture_output=True,
                               text=True, timeout=60)
        assert fx["keys"][ip].split()[1] in found.stdout, ip
    assert os.readlink(fx["env"]["BD_HOSTPREP_CLAUDE_LINK"]) == f"{vm}/.local/bin/claude"
    assert (fx["tmp"] / "node-version").read_text().strip() == "v20.19.0"
    assert "install -y -q nodejs" in (fx["logs"] / "apt.log").read_text()
    assert Path(fx["env"]["BD_HOSTPREP_NODE_KEYRING"]).read_text() == "ARMORED-NODESOURCE-KEY\n"
    assert "URIs: https://deb.nodesource.com/node_20.x\n" in Path(fx["env"]["BD_HOSTPREP_NODE_SOURCES"]).read_text()
    cred = vm / ".claude/.credentials.json"
    assert cred.read_text() == "HUB-TOKEN\n" and stat.S_IMODE(cred.stat().st_mode) == 0o600
    assert "MODE=apply FOUND 19 APPLIED 19 REFUSED 0 RESULT=OK" in r.stdout, r.stdout
    r2 = _prep(fx, "--apply")
    assert r2.returncode == 0, (r2.stdout, r2.stderr)
    for step in ("order", "links", "config", "carry", "usos", "auth", "claude-path", "node", "ssh-key", "known_hosts",
                 "authorized_keys"):
        assert f"STEP {step} FOUND NONE APPLIED 0 REFUSED 0" in r2.stdout, (step, r2.stdout)
    assert "MODE=apply FOUND 0 APPLIED 0 REFUSED 0 RESULT=OK" in r2.stdout


def test_secrets_never_reach_the_vm_in_either_mode(fx: dict) -> None:
    r = _prep(fx)
    r2 = _prep(fx, "--apply")
    assert r2.returncode == 0, (r2.stdout, r2.stderr)
    for secret in ("gh-pat", "gitea-token", "unifi.env"):
        assert secret not in r.stdout + r2.stdout, secret
        assert not (fx["vm"] / ".config/bd" / secret).exists(), f"{secret} copied to the VM"
    assert "id_ed25519" not in r2.stdout.replace("bd_agent_ed25519", ""), "a non-fleet key was offered"


def test_existing_differing_items_are_refused_named_and_left_untouched(fx: dict) -> None:
    vm = fx["vm"]
    (vm / "a.sh").symlink_to("/somewhere/else")
    _put(vm / "b.sh", "LOCAL COPY\n", 0o755)
    _put(vm / ".config/bd/roles", "VM ROLES\n", 0o664)
    _put(vm / ".ssh/bd_agent_ed25519", "OTHER-KEY\n", 0o600)
    _put(Path(fx["env"]["BD_HOSTPREP_NODE_SOURCES"]), "deb https://example.invalid other main\n", 0o644)
    r = _prep(fx, "--apply")
    assert r.returncode == 3, (r.stdout, r.stderr)
    assert "links a.sh REFUSED DIFFERS now->/somewhere/else" in r.stdout
    assert "links b.sh REFUSED EXISTS-NOT-LINK" in r.stdout
    assert "config roles REFUSED DIFFERS" in r.stdout
    assert "ssh-key bd_agent_ed25519 REFUSED DIFFERS (not overwritten)" in r.stdout
    assert "node absent REFUSED SOURCES-DIFFER" in r.stdout
    assert os.readlink(vm / "a.sh") == "/somewhere/else" and (vm / "b.sh").read_text() == "LOCAL COPY\n"
    assert (vm / ".config/bd/roles").read_text() == "VM ROLES\n"
    assert (vm / ".ssh/bd_agent_ed25519").read_text() == "OTHER-KEY\n"
    assert not (fx["logs"] / "apt.log").exists() and not (fx["logs"] / "curl.log").exists()
    assert "RESULT=REFUSED" in r.stdout and "STEP links FOUND 2 APPLIED 0 REFUSED 2" in r.stdout, r.stdout


def test_no_sudo_is_a_refused_line_not_a_silent_skip(fx: dict) -> None:
    r = _prep(fx, "--apply", FAKE_NO_SUDO="1")
    assert r.returncode == 3, (r.stdout, r.stderr)
    assert f"claude-path {fx['env']['BD_HOSTPREP_CLAUDE_LINK']} REFUSED NO-SUDO" in r.stdout
    assert "node absent REFUSED NO-SUDO" in r.stdout
    assert not Path(fx["env"]["BD_HOSTPREP_CLAUDE_LINK"]).is_symlink()


def test_node_key_with_wrong_fingerprint_is_never_installed(fx: dict) -> None:
    r = _prep(fx, "--apply", FAKE_FPR="0000000000000000000000000000000000000000")
    assert "node absent REFUSED KEY-FINGERPRINT got=0000000000000000000000000000000000000000" in r.stdout, r.stdout
    assert not Path(fx["env"]["BD_HOSTPREP_NODE_KEYRING"]).exists()
    assert not Path(fx["env"]["BD_HOSTPREP_NODE_SOURCES"]).exists()
    assert not (fx["logs"] / "apt.log").exists()


def test_node_already_at_or_above_major_is_ok(fx: dict) -> None:
    (fx["tmp"] / "node-version").write_text("v22.23.2\n")
    r = _prep(fx)
    assert "node v22.23.2 OK" in r.stdout and "STEP node FOUND NONE" in r.stdout, r.stdout


def test_known_host_absent_from_hub_is_refused_and_never_scanned(fx: dict) -> None:
    r = _prep(fx, "--apply", BD_HOSTPREP_KNOWN_EXTRA=f"{EXTRA} {UNKNOWN}")
    assert f"known_hosts {UNKNOWN} REFUSED NOT-IN-HUB-KNOWN-HOSTS" in r.stdout and r.returncode == 3, r.stdout
    assert not (fx["logs"] / "keyscan.log").exists()


def test_unreachable_host_is_could_not_look(fx: dict) -> None:
    r = _prep(fx, FAKE_DOWN=VM)
    assert r.returncode == 5 and f"HOST-PREP vm1 {VM} COULD NOT LOOK" in r.stdout, (r.stdout, r.stderr)


def test_not_a_seat_vm_is_refused(fx: dict) -> None:
    r = subprocess.run(["bash", str(_cand("bd-nfs-share")), "host-prep", "10.1.2.3"], env=fx["env"],
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 3 and "not one of the O1666 seat VMs" in r.stderr, (r.stdout, r.stderr)


# ---- bd-auth-sync: KEEP-NEWER needs an answered seat (ADDENDUM 3) ------------------------------------------------

# ---- R1 addenda 6+7 (r2) -------------------------------------------------------------------------------------------

def _verify(fx: dict, **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(_cand("bd-nfs-share")), "verify", "vm1"], env={**fx["env"], **extra},
                          capture_output=True, text=True, timeout=120, check=False)


def _unlink_home_links(fx: dict, *names: str) -> None:
    for n in names or LINK_NAMES:
        (fx["vm"] / n).unlink()


def test_a6a_carry_syncs_new_and_changed_files_and_keeps_the_vm_copy(fx: dict) -> None:
    vm, hub = fx["vm"], fx["hub"]
    r = _prep(fx)
    assert _items(r.stdout, "carry") == {"notes.txt": "PLAN NEW"} and not (vm / "bd-carry").exists(), r.stdout
    r = _prep(fx, "--apply")
    assert r.returncode == 0 and _items(r.stdout, "carry") == {"notes.txt": "APPLIED NEW"}, (r.stdout, r.stderr)
    assert (vm / "bd-carry/notes.txt").read_text() == "carry v1\n"
    (hub / "bd-carry/notes.txt").write_text("carry v2 is longer\n")
    _put(hub / "bd-carry/sub/more.txt", "more\n", 0o644)
    _put(vm / "bd-carry/vm-only.txt", "local\n", 0o644)
    r = _prep(fx, "--apply")
    items = _items(r.stdout, "carry")
    assert items["sub/more.txt"] == "APPLIED NEW" and items["notes.txt"].startswith("APPLIED UPDATE backup=notes.txt.pre-"), items
    assert "STEP carry FOUND 3 APPLIED 3 REFUSED 0" in r.stdout, r.stdout     # sub/ dir + 2 files
    assert (vm / "bd-carry/notes.txt").read_text() == "carry v2 is longer\n"
    backups = list((vm / "bd-carry").glob("notes.txt.pre-*"))
    assert len(backups) == 1 and backups[0].read_text() == "carry v1\n", "the replaced VM copy was not kept"
    assert (vm / "bd-carry/vm-only.txt").exists(), "rsync deleted a VM-only file"
    r = _prep(fx, "--apply")
    assert "STEP carry FOUND NONE APPLIED 0 REFUSED 0" in r.stdout and "carry " + str(hub / "bd-carry") + " OK in-sync" in r.stdout


def test_a6b_usos_git_comes_from_the_hub_and_head_must_equal_hub_head(fx: dict) -> None:
    vm, hub = fx["vm"], fx["hub"]
    hub_head = _git(hub / "UniversalSwarmOS", "rev-parse", "HEAD")
    r = _prep(fx, "--apply")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert _items(r.stdout, "usos") == {f"{hub}/UniversalSwarmOS": f"APPLIED RSYNC .git + checkout HEAD={hub_head[:12]}"}
    assert _git(vm / "UniversalSwarmOS", "rev-parse", "HEAD") == hub_head
    assert (vm / "UniversalSwarmOS/README").read_text() == "hub tree\n", "absent dir: the index was not checked out"
    r = _prep(fx, "--apply")
    assert _items(r.stdout, "usos") == {f"{hub}/UniversalSwarmOS": f"OK HEAD={hub_head[:12]}"}
    # the hub moves on: an existing checkout is never rewritten, the gap is named
    _put(hub / "UniversalSwarmOS/NEW", "x\n", 0o644)
    _git(hub / "UniversalSwarmOS", "add", "NEW")
    _git(hub / "UniversalSwarmOS", "commit", "-q", "-m", "two")
    new_head = _git(hub / "UniversalSwarmOS", "rev-parse", "HEAD")
    r = _prep(fx, "--apply")
    assert r.returncode == 3, (r.stdout, r.stderr)
    assert _items(r.stdout, "usos") == {f"{hub}/UniversalSwarmOS":
                                        f"REFUSED HEAD-MISMATCH host={hub_head[:12]} hub={new_head[:12]} (existing checkout, not rewritten)"}
    assert _git(vm / "UniversalSwarmOS", "rev-parse", "HEAD") == hub_head
    assert "RESULT=REFUSED" in r.stdout


def test_a6b_existing_dir_without_git_gets_git_only_and_its_files_stay(fx: dict) -> None:
    vm, hub = fx["vm"], fx["hub"]
    _put(vm / "UniversalSwarmOS/README", "VM EDIT\n", 0o644)
    r = _prep(fx)
    assert _items(r.stdout, "usos")[f"{hub}/UniversalSwarmOS"].startswith("PLAN RSYNC .git (NO-GIT) HEAD="), r.stdout
    assert not (vm / "UniversalSwarmOS/.git").exists(), "dry run wrote .git"
    r = _prep(fx, "--apply")
    assert _items(r.stdout, "usos")[f"{hub}/UniversalSwarmOS"].startswith("APPLIED RSYNC .git HEAD="), r.stdout
    assert (vm / "UniversalSwarmOS/README").read_text() == "VM EDIT\n", "an existing working tree was overwritten"
    assert _git(vm / "UniversalSwarmOS", "rev-parse", "HEAD") == _git(hub / "UniversalSwarmOS", "rev-parse", "HEAD")


def test_a6b_sync_that_delivers_nothing_is_refused_not_applied(fx: dict) -> None:
    noop = _exe(fx["tmp"] / "noop-rsync", NOOP_RSYNC)
    r = _prep(fx, "--apply", BD_NFS_RSYNC=str(noop))
    assert r.returncode == 3, (r.stdout, r.stderr)
    item = _items(r.stdout, "usos")[f"{fx['hub']}/UniversalSwarmOS"]
    assert item.startswith("REFUSED HEAD-MISMATCH-AFTER-SYNC host=NO-GIT hub="), item
    assert "APPLIED RSYNC" not in r.stdout


def test_a7a_host_prep_before_links_or_mount_is_refused_named_and_runs_nothing_else(fx: dict) -> None:
    vm = fx["vm"]
    _unlink_home_links(fx, "bd-persist", "bin")
    before = _tree(vm)
    r = _prep(fx, "--apply")
    assert r.returncode == 3, (r.stdout, r.stderr)
    assert "order mount OK nfs" in r.stdout
    assert "order links REFUSED LINKS-MISSING bd-persist bin (run: bd-nfs-share vm-home-links vm1, then verify, then host-prep)" in r.stdout
    assert "STEP order FOUND 1 APPLIED 0 REFUSED 1" in r.stdout and "REFUSED ORDER (A7a)" in r.stdout, r.stdout
    assert "STEP links" not in r.stdout and "APPLIED" not in r.stdout.replace("APPLIED 0", ""), r.stdout
    assert _tree(vm) == before, "host-prep wrote on the VM although the order was refused"
    assert not (vm / "bd-carry").exists() and not (vm / ".ssh/bd_agent_ed25519").exists()
    r = _prep(fx, "--apply", FAKE_NO_MOUNT="1")
    assert r.returncode == 3 and "order mount REFUSED NFS-MOUNT-MISSING" in r.stdout and "mount vm1 --apply first" in r.stdout


def test_a7a_verify_before_links_is_refused_named_and_passes_the_gate_after(fx: dict) -> None:
    _unlink_home_links(fx, "bd-persist")
    r = _verify(fx)
    assert r.returncode == 3, (r.stdout, r.stderr)
    assert f"VERIFY vm1 {VM} REFUSED ORDER-LINKS-BEFORE-VERIFY missing_links=bd-persist (run: bd-nfs-share vm-home-links vm1 first) RESULT=REFUSED" in r.stdout
    assert "prevalidator=" not in r.stdout, "verify probed the VM before refusing"
    (fx["vm"] / "bd-persist").symlink_to(fx["mnt"] / "bd-persist")
    r = _verify(fx, BD_NFS_GUARD_MANIFEST=str(fx["tmp"] / "absent-manifest.json"))
    assert "ORDER-LINKS-BEFORE-VERIFY" not in r.stdout and "prevalidator=FAIL" in r.stdout and r.returncode == 4, (r.stdout, r.stderr)


def test_a7b_stale_known_host_key_is_replaced_not_appended_and_backed_up(fx: dict) -> None:
    vm, keys = fx["vm"], fx["keys"]
    stale = _hostkey(fx["tmp"], "stale-" + EXTRA)
    kh = _put(vm / ".ssh/known_hosts", f"{SERVER} {keys[SERVER]}\n{EXTRA} {stale}\n", 0o600)
    r = _prep(fx)
    assert _items(r.stdout, "known_hosts") == {f"{SERVER}/ssh-ed25519": "OK", f"{EXTRA}/ssh-ed25519": "PLAN REPLACE-STALE",
                                               f"{VM}/ssh-ed25519": "PLAN APPEND"}, r.stdout
    assert kh.read_text() == f"{SERVER} {keys[SERVER]}\n{EXTRA} {stale}\n", "dry run touched known_hosts"
    r = _prep(fx, "--apply")
    assert r.returncode == 0, (r.stdout, r.stderr)
    items = _items(r.stdout, "known_hosts")
    assert items[f"{EXTRA}/ssh-ed25519"].startswith("APPLIED REPLACE-STALE backup=known_hosts.pre-") and items[f"{VM}/ssh-ed25519"] == "APPLIED APPEND"
    text = kh.read_text()
    assert stale.split()[1] not in text, "the stale key is still there (appended, not replaced)"
    found = subprocess.run(["ssh-keygen", "-F", EXTRA, "-f", str(kh)], capture_output=True, text=True, timeout=60).stdout
    assert [ln for ln in found.splitlines() if not ln.startswith("#")] and keys[EXTRA].split()[1] in found and found.count("ssh-ed25519") == 1
    assert keys[SERVER].split()[1] in text, "another host's entry was lost in the purge"
    backups = list((vm / ".ssh").glob("known_hosts.pre-*"))
    assert len(backups) == 1 and stale.split()[1] in backups[0].read_text()
    r = _prep(fx, "--apply")
    assert "STEP known_hosts FOUND NONE APPLIED 0 REFUSED 0" in r.stdout, r.stdout


def test_a7b_a8_seat_pubkeys_land_once_on_the_vm_and_on_each_capacity_box(fx: dict) -> None:
    box, vm = fx["box"], fx["vm"]
    _put(box / ".ssh/authorized_keys", 'command="x" ssh-ed25519 AAAAseat vm1-seat-already\n', 0o600)
    template = "ssh-ed25519 AAAAtemplate template-id"
    r = _prep(fx)
    assert _items(r.stdout, "authorized_keys") == {f"{VM}/bd-agent": "PLAN APPEND", f"{EXTRA}/bd-agent": "PLAN APPEND",
                                                   f"{EXTRA}/vm1-seat": "OK"}, r.stdout
    assert (box / ".ssh/authorized_keys").read_text().count("\n") == 1, "dry run appended on the box"
    assert (vm / ".ssh/authorized_keys").read_text() == template + "\n", "dry run appended on the VM"
    r = _prep(fx, "--apply")
    assert r.returncode == 0 and _items(r.stdout, "authorized_keys") == {f"{VM}/bd-agent": "APPLIED APPEND",
                                                                         f"{EXTRA}/bd-agent": "APPLIED APPEND", f"{EXTRA}/vm1-seat": "OK"}
    lines = (box / ".ssh/authorized_keys").read_text().splitlines()
    assert lines == ['command="x" ssh-ed25519 AAAAseat vm1-seat-already', "ssh-ed25519 AAAAfleet bd-agent"], lines
    vm_lines = (vm / ".ssh/authorized_keys").read_text().splitlines()
    assert vm_lines == [template, "ssh-ed25519 AAAAfleet bd-agent"], vm_lines      # A8: template keys kept, fleet key once
    assert stat.S_IMODE((vm / ".ssh/authorized_keys").stat().st_mode) == 0o600
    r = _prep(fx, "--apply")
    assert "STEP authorized_keys FOUND NONE APPLIED 0 REFUSED 0" in r.stdout, r.stdout
    assert (box / ".ssh/authorized_keys").read_text().splitlines() == lines, "a second run appended again on the box"
    assert (vm / ".ssh/authorized_keys").read_text().splitlines() == vm_lines, "a second run appended again on the VM"
    assert "id_ed25519" not in r.stdout, "the key was named by file, not by comment"
    r = _prep(fx, "--apply", FAKE_DOWN=EXTRA)
    assert r.returncode == 5 and f"authorized_keys {EXTRA} COULD NOT LOOK ssh-rc=255" in r.stdout
    assert "STEP authorized_keys COULD NOT LOOK ssh-rc=255" in r.stdout, r.stdout


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _auth(fx: dict, *args: str, **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(_cand("bd-auth-sync")), VM, *args], env={**fx["env"], **extra},
                          capture_output=True, text=True, timeout=60, check=False)


def _vm_newer(fx: dict, body: str = "VM-REWRITTEN\n") -> tuple[Path, int]:
    f = fx["vm"] / ".claude/.credentials.json"
    _put(f, body, 0o600)
    mt = int(time.time()) - 600
    os.utime(fx["cred"], (mt - 3600, mt - 3600))
    os.utime(f, (mt, mt))
    return f, mt


def _seat_hosts(fx: dict, *rows: tuple[str, str, float]) -> None:
    (fx["tmp"] / "SEAT-HOSTS.tsv").write_text(
        "# seat\thost\tat\tby\n" + "".join(f"{s}\t{h}\t{_iso(t)}\tbd-dispatch\n" for s, h, t in rows))


def _say(path: Path, *rows: tuple[float, str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{_iso(t)}\t{s}\tbd-dispatch-L3-C\t{rc}\t40\tabcd\t[from {s}] x\tmode=type\n"
                            for t, s, rc in rows))


def test_vm_newer_and_a_seat_there_answered_since_is_kept(fx: dict) -> None:
    f, mt = _vm_newer(fx)
    _seat_hosts(fx, ("bd-worker-X", VM, mt - 7200))
    _say(fx["tmp"] / "say.log", (mt + 60, "bd-worker-X", "0"))
    r = _auth(fx)
    assert r.returncode == 0 and "kept_newer=1" in r.stdout and "hub_wins=0" in r.stdout, r.stdout
    assert f.read_text() == "VM-REWRITTEN\n" and not list(f.parent.glob("*.pre-*"))
    assert f"answered_at={_iso(mt + 60)}" in r.stdout


def test_vm_newer_without_an_answer_since_hub_wins_with_backup(fx: dict) -> None:
    f, mt = _vm_newer(fx)
    # every near-miss: answered BEFORE the rewrite; answered after but rc!=0; another host's seat; a target column
    _seat_hosts(fx, ("bd-worker-X", VM, mt - 7200), ("bd-worker-Y", "10.9.9.2", mt - 7200))
    _say(fx["tmp"] / "say.log", (mt - 30, "bd-worker-X", "0"), (mt + 60, "bd-worker-X", "3"),
         (mt + 90, "bd-worker-Y", "0"))
    r = _auth(fx)
    assert r.returncode == 0 and "kept_newer=0" in r.stdout and "hub_wins=1" in r.stdout, r.stdout
    assert f.read_text() == "HUB-TOKEN\n" and stat.S_IMODE(f.stat().st_mode) == 0o600
    baks = list(f.parent.glob(".credentials.json.pre-*"))
    assert len(baks) == 1 and baks[0].read_text() == "VM-REWRITTEN\n", baks
    log = (fx["logs"] / "auth-sync.log").read_text()
    assert "HUB-WINS+BACKUP" in log and "VM-REWRITTEN" not in log and "HUB-TOKEN" not in log, log


def test_answer_from_a_seat_after_it_left_the_host_does_not_count(fx: dict) -> None:
    f, mt = _vm_newer(fx)
    _seat_hosts(fx, ("bd-worker-X", VM, mt - 7200), ("bd-worker-X", "-", mt + 10))
    _say(fx["tmp"] / "say.log", (mt + 60, "bd-worker-X", "0"))
    r = _auth(fx)
    assert "hub_wins=1" in r.stdout and f.read_text() == "HUB-TOKEN\n", r.stdout


def test_answer_found_in_recent_archive_counts_old_archive_does_not(fx: dict) -> None:
    f, mt = _vm_newer(fx)
    _seat_hosts(fx, ("bd-worker-X", VM, mt - 7200))
    arch = fx["tmp"] / "archives"
    _say(arch / f"say.log.{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.bak", (mt + 60, "bd-worker-X", "0"))
    r = _auth(fx, "--dry-run")
    assert "kept_newer=1" in r.stdout, r.stdout
    r2 = _auth(fx, "--dry-run", BD_AUTH_SYNC_ANSWER_WINDOW_H="0")
    assert "hub_wins=1" in r2.stdout and "DRY-WOULD-HUB-WIN" in r2.stdout, r2.stdout
    assert f.read_text() == "VM-REWRITTEN\n" and not list(f.parent.glob("*.pre-*")), "dry run wrote"


def test_identical_content_is_same_whatever_the_mtimes(fx: dict) -> None:
    f, _ = _vm_newer(fx, body="HUB-TOKEN\n")
    r = _auth(fx)
    assert "same=1" in r.stdout and "hub_wins=0" in r.stdout and not list(f.parent.glob("*.pre-*")), r.stdout


def test_unreadable_say_ledger_is_named_and_counts_as_no_answer(fx: dict) -> None:
    f, mt = _vm_newer(fx)
    _seat_hosts(fx, ("bd-worker-X", VM, mt - 7200))
    r = _auth(fx, BD_AUTH_SYNC_SAY_LOG=str(fx["tmp"] / "no-such-say.log"))
    assert "answered_at=COULD-NOT-LOOK" in r.stdout and "hub_wins=1" in r.stdout, r.stdout
