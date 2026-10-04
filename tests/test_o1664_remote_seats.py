"""O1664 CUT1 remote seats: launch a seat ON another VM, and reach it from the hub's bd-say / role-claim reap / pm-pass.

Before: every tmux call in the launchers, bd-say.sh, bd-role-claim.sh and bd-pm-pass.sh probed THIS host only, so a seat
on a VM was invisible (census), unreachable (say) and reaped as dead (role claim). The candidate adds
lib_remote_seat.sh -- a tmux() wrapper routed by SEAT-HOSTS.tsv -- a --host flag on the four launchers, and bd-auth-sync.

The harness is deployed from bd-persist, not this repo: BD_O1664_REMOTE_SEATS_CANDIDATE is the absolute path of the
candidate DIRECTORY (harness-work/FIX/o1664-remote-seats). Hermetic (O1641): no live host is contacted. ssh and rsync
are the fakes below, passed through BD_REMOTE_SSH / BD_REMOTE_RSYNC (test-shims stays first-but-one on PATH as the
backstop). The fake ssh runs the remote command locally with the host's "filesystem" remapped under tmp_path/vm-<host>
and a fake tmux whose sessions/pane/argv live there; hosts listed in FAKE_DOWN answer 255 like an unreachable box.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1664_REMOTE_SEATS_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

TEST_SHIMS = "/home/mboyle/bd-persist/harness/test-shims"
HOST = "10.9.9.1"
DOWN = "10.9.9.2"
HUB = "10.9.9.100"   # the hub's address in every fixture; BD_SELF_HOSTS says which box a run is "on" (G2)
RS = "\x1e"
TMUX_CALL = re.compile(r"(^|[\s$(;|&{])tmux\s")
US = "\x1f"

FAKE_SSH = r"""#!/bin/bash
while [ $# -gt 0 ]; do case "$1" in -n) shift ;; -o) shift 2 ;; -*) shift ;; *) break ;; esac; done
host=$1; shift; cmd="$*"
printf '%s\t%s\n' "$host" "$cmd" >> "$FAKE_LOG_DIR/ssh.log"
case " $FAKE_DOWN " in *" $host "*) exit 255 ;; esac
root="$FAKE_ROOT/vm-$host"; mkdir -p "$root"
cmd=${cmd//"$FAKE_REMAP"/"$root$FAKE_REMAP"}
P="$FAKE_BIN:$PATH"
case " ${FAKE_NOFS:-} " in *" $host "*) P="$FAKE_NOFS_BIN:$P" ;; esac
FAKE_VM_ROOT=$root PATH=$P exec bash -c "$cmd"
"""

FAKE_RSYNC = r"""#!/bin/bash
printf '%s\n' "$*" >> "$FAKE_LOG_DIR/rsync.log"
srcs=()
while [ $# -gt 0 ]; do case "$1" in -e) shift 2 ;; -*) shift ;; *) srcs+=("$1"); shift ;; esac; done
dest=${srcs[-1]}; unset 'srcs[-1]'; host=${dest%%:*}
case " $FAKE_DOWN " in *" $host "*) exit 255 ;; esac
root="$FAKE_ROOT/vm-$host"
# rsync --relative semantics: a dir merges into the same path (cp -a dir onto an existing dir would nest it)
for s in "${srcs[@]}"; do
  if [ -d "$s" ]; then mkdir -p "$root$s"; cp -a "$s/." "$root$s/"; else mkdir -p "$root$(dirname "$s")"; cp -a "$s" "$root$s"; fi
done
"""

# One tmux for the hub (FAKE_VM_ROOT=<tmp>/hub) and every VM (FAKE_VM_ROOT set by the fake ssh).
FAKE_TMUX = r"""#!/bin/bash
d=$FAKE_VM_ROOT; mkdir -p "$d"; touch "$d/sessions"
{ printf '%s\x1f' "$@"; printf '\x1e'; } >> "$d/tmux.argv"
sub=$1; shift; t=""
while [ $# -gt 0 ]; do
  case "$1" in -t|-s) t=$2; shift 2; continue ;; -pt) t=$2; shift 2; continue ;; esac; shift
done
t=${t#=}; t=${t%%:*}
case "$sub" in
  new-session) echo "$t" >> "$d/sessions" ;;
  has-session) grep -qxF -- "$t" "$d/sessions" ;;
  ls|list-sessions) [ -s "$d/sessions" ] || { echo "no server running on /tmp/fake" >&2; exit 1; }; cat "$d/sessions" ;;
  capture-pane) cat "$d/pane" 2>/dev/null; exit 0 ;;
  list-panes) grep -qxF -- "$t" "$d/sessions" && cat "$d/pane_pid" 2>/dev/null ;;
  *) exit 0 ;;
esac
"""

NOFS_CAT = "#!/bin/sh\necho 'not the hub nonce'\n"


def _exe(p: Path, body: str) -> Path:
    p.write_text(body, encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


def _cand(name: str) -> Path:
    d = Path(CANDIDATE)
    assert d.is_dir(), f"BD_O1664_REMOTE_SEATS_CANDIDATE supplied but not a directory: {d}"
    p = d / name
    assert p.is_file(), f"candidate {p} absent"
    return p


@pytest.fixture
def fx(tmp_path: Path) -> dict:
    logs = tmp_path / "logs"
    logs.mkdir()
    fbin = tmp_path / "fakebin"
    fbin.mkdir()
    _exe(fbin / "tmux", FAKE_TMUX)
    nofs = tmp_path / "nofsbin"
    nofs.mkdir()
    _exe(nofs / "cat", NOFS_CAT)
    ssh = _exe(tmp_path / "fake-ssh", FAKE_SSH)
    rsync = _exe(tmp_path / "fake-rsync", FAKE_RSYNC)
    hubroot = tmp_path / "hubfs"
    hubroot.mkdir()
    shared = tmp_path / "shared"
    (shared / "state").mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("BD_", "FAKE_", "TMUX"))}
    env.update(
        LC_ALL="C",
        PATH=f"{fbin}:{TEST_SHIMS}:{os.environ['PATH']}",
        TMPDIR=str(tmp_path),
        BD_REMOTE_SEAT_LIB=str(_cand("lib_remote_seat.sh")),
        BD_SEAT_HOSTS=str(tmp_path / "SEAT-HOSTS.tsv"),
        BD_REMOTE_SSH=str(ssh),
        BD_REMOTE_RSYNC=str(rsync),
        BD_REMOTE_SHARED_ROOT=str(shared),
        BD_REMOTE_CTL_DIR=str(tmp_path),
        BD_SEAT="bd-o1664-test",
        BD_HUB_HOST=HUB,
        BD_SELF_HOSTS=HUB,
        FAKE_LOG_DIR=str(logs),
        FAKE_ROOT=str(tmp_path),
        FAKE_REMAP=str(hubroot),
        FAKE_BIN=str(fbin),
        FAKE_NOFS_BIN=str(nofs),
        FAKE_DOWN=DOWN,
        FAKE_VM_ROOT=str(tmp_path / "hub"),
    )
    return {"tmp": tmp_path, "env": env, "logs": logs, "hubfs": hubroot, "shared": shared}


def _vm(fx: dict, host: str = HOST) -> Path:
    return fx["tmp"] / f"vm-{host}"


def _sessions(root: Path, *names: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "sessions").write_text("".join(f"{n}\n" for n in names), encoding="utf-8")


def _argv(root: Path) -> list[list[str]]:
    f = root / "tmux.argv"
    if not f.exists():
        return []
    return [rec.split(US)[:-1] for rec in f.read_text(encoding="utf-8").split(RS) if rec]


def _ssh_log(fx: dict) -> str:
    f = fx["logs"] / "ssh.log"
    return f.read_text(encoding="utf-8") if f.exists() else ""


def _register(fx: dict, *rows: tuple[str, str]) -> None:
    Path(fx["env"]["BD_SEAT_HOSTS"]).write_text(
        "# seat\thost\tat\tby\n" + "".join(f"{s}\t{h}\t2026-10-02T00:00:00Z\tfixture\n" for s, h in rows),
        encoding="utf-8",
    )


def _lib(fx: dict, script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. "$BD_REMOTE_SEAT_LIB" || exit 99\n{script}'],
        env=fx["env"], capture_output=True, text=True, timeout=60, check=False,
    )


def _dead_pid() -> int:
    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


# ---- lib: routing ----------------------------------------------------------------------------------------------

def test_no_registry_is_plain_local_tmux(fx: dict) -> None:
    """Hub-only fleet unchanged: no SEAT-HOSTS.tsv -> every call is the local tmux, ssh never runs."""
    _sessions(fx["tmp"] / "hub", "bd-w1")
    r = _lib(fx, 'tmux has-session -t "=bd-w1" && tmux ls -F "#S"')
    assert r.returncode == 0 and r.stdout.strip() == "bd-w1", (r.returncode, r.stdout, r.stderr)
    assert _argv(fx["tmp"] / "hub") == [["has-session", "-t", "=bd-w1"], ["ls", "-F", "#S"]]
    assert _ssh_log(fx) == ""


def test_registered_seat_routes_to_its_host_with_exact_argv(fx: dict) -> None:
    _register(fx, ("bd-w1", HOST))
    _sessions(_vm(fx), "bd-w1")
    _sessions(fx["tmp"] / "hub", "bd-hub")
    text = "it's $(id) `x` \"q\"\nline2 \\ end"
    # pass the hostile text through the environment so this test's own shell quoting cannot help
    r = subprocess.run(
        ["bash", "-c", '. "$BD_REMOTE_SEAT_LIB"; tmux send-keys -t "=bd-w1:" -l "$T"; echo rc=$?; '
                       'tmux capture-pane -J -e -pt "bd-w1:" -S -; tmux has-session -t "=bd-hub"; echo hub=$?'],
        env={**fx["env"], "T": text}, capture_output=True, text=True, timeout=60, check=False,
    )
    assert "rc=0" in r.stdout and "hub=0" in r.stdout, (r.stdout, r.stderr)
    remote = _argv(_vm(fx))
    assert ["send-keys", "-t", "=bd-w1:", "-l", text] in remote, remote
    assert ["capture-pane", "-J", "-e", "-pt", "bd-w1:", "-S", "-"] in remote, remote
    assert ["has-session", "-t", "=bd-hub"] in _argv(fx["tmp"] / "hub")
    log = _ssh_log(fx)
    assert log.count(f"{HOST}\t'tmux'") == 2 and "bd-hub" not in log, log  # the hub seat never went over ssh


def test_ls_is_hub_plus_registered_seats_and_unreachable_is_named(fx: dict) -> None:
    _register(fx, ("bd-w1", HOST), ("bd-w2", DOWN), ("bd-moved", HOST), ("bd-moved", "-"))
    _sessions(fx["tmp"] / "hub", "bd-hub")
    _sessions(_vm(fx), "bd-w1", "bd-stray", "bd-moved")
    r = _lib(fx, 'tmux ls -F "#S"')
    assert r.returncode == 0, r.stderr
    assert sorted(r.stdout.split()) == ["bd-hub", "bd-w1"], r.stdout  # stray and moved-back seats never count
    assert f"BD-REMOTE-UNREACHABLE\t{DOWN}" in r.stderr, r.stderr


def test_hostile_host_values_are_refused(fx: dict) -> None:
    r = _lib(fx, 'bd_seat_host_set bd-w1 "-oProxyCommand=touch /tmp/x"; echo set=$?; '
                 'bd_take_host_arg a --host "-oX" b; echo take=$?; bd_take_host_arg a --host=10.0.70.5 b; '
                 'echo "take2=$? host=$BD_HOST argv=${BD_ARGV[*]}"')
    assert "set=2" in r.stdout and "take=2" in r.stdout, r.stdout
    assert "take2=0 host=10.0.70.5 argv=a b" in r.stdout, r.stdout
    assert not Path(fx["env"]["BD_SEAT_HOSTS"]).exists()


# ---- bd-role-claim: reap / claim -------------------------------------------------------------------------------

def _claim_env(fx: dict) -> dict:
    reg = fx["tmp"] / "ROLE-OCCUPANCY.tsv"
    return {**fx["env"], "BD_ROLE_REGISTRY": str(reg), "BD_ROLE_REAP_LOG": str(fx["tmp"] / "reap.log"),
            "BD_ROLE_CARDINALITY": str(fx["tmp"] / "card.tsv")}


def test_reap_keeps_live_and_unreachable_remote_rows_drops_dead(fx: dict) -> None:
    env = _claim_env(fx)
    alive, dead = os.getpid(), _dead_pid()
    _register(fx, ("bd-r-live", HOST), ("bd-r-gone", HOST), ("bd-r-down", DOWN))
    _sessions(fx["tmp"] / "hub", "bd-hub-live")
    _sessions(_vm(fx), "bd-r-live")
    rows = [("worker", "bd-r-live", alive, HOST), ("worker", "bd-r-gone", dead, HOST),
            ("worker", "bd-r-down", dead, DOWN), ("status", "bd-hub-live", alive, "hub"),
            ("audit", "bd-hub-gone", dead, "hub")]
    Path(env["BD_ROLE_REGISTRY"]).write_text(
        "# role\tseat\tpid\thost\tclaimed_at\n"
        + "".join(f"{r}\t{s}\t{p}\t{h}\t2026-10-02T00:00:0{i}Z\n" for i, (r, s, p, h) in enumerate(rows)),
        encoding="utf-8")
    r = subprocess.run(["bash", str(_cand("bd-role-claim.sh")), "reap"], env=env,
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    kept = {line.split("\t")[1] for line in Path(env["BD_ROLE_REGISTRY"]).read_text().splitlines()[1:]}
    assert kept == {"bd-r-live", "bd-r-down", "bd-hub-live"}, kept
    log = Path(env["BD_ROLE_REAP_LOG"]).read_text(encoding="utf-8")
    assert f"KEEP-UNREACHABLE session=bd-r-down host={DOWN}" in log, log
    assert "DROP role=worker session=bd-r-gone" in log and "DROP role=audit session=bd-hub-gone" in log, log
    # the remote pid was judged ON the host: kill -0 <alive> went through ssh to HOST
    assert f"{HOST}\t'kill' '-0' '{alive}'" in _ssh_log(fx), _ssh_log(fx)


def test_claim_records_the_seats_vm_and_its_remote_pane_pid(fx: dict) -> None:
    env = _claim_env(fx)
    _register(fx, ("bd-r-new", HOST))
    _sessions(_vm(fx), "bd-r-new")
    (_vm(fx) / "pane_pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    r = subprocess.run(["bash", str(_cand("bd-role-claim.sh")), "claim", "worker", "bd-r-new"], env=env,
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    row = [ln for ln in Path(env["BD_ROLE_REGISTRY"]).read_text().splitlines() if "\tbd-r-new\t" in ln]
    assert len(row) == 1 and row[0].split("\t")[2:4] == [str(os.getpid()), HOST], row


# ---- G2 (lens C4-C F1/F2): the same tools run ON A VM -------------------------------------------------------

def _on_vm(fx: dict) -> dict:
    """This run is "on" VM HOST: its local tmux is the VM's; the hub is reached over ssh at HUB."""
    return {**fx["env"], "BD_SELF_HOSTS": HOST, "FAKE_VM_ROOT": str(_vm(fx))}


def _reg_two(fx: dict, env: dict, pm_pid: int) -> None:
    Path(env["BD_ROLE_REGISTRY"]).write_text(
        "# role\tseat\tpid\thost\tclaimed_at\n"
        f"o1664probe\tbd-pm-X\t{pm_pid}\thub-mesh01\t2026-10-02T10:00:00Z\n"
        f"worker\tbd-remote-1\t{os.getpid()}\t{HOST}\t2026-10-02T17:00:00Z\n", encoding="utf-8")


def test_vm_side_claim_tool_never_reaps_hub_rows(fx: dict) -> None:
    """Lens F1: on a VM the hub seat is invisible to local tmux/kill -0; r1 dropped its row on the first reap."""
    env = {**_claim_env(fx), **_on_vm(fx)}
    _register(fx, ("bd-remote-1", HOST))
    _sessions(_vm(fx), "bd-remote-1")
    _sessions(_vm(fx, HUB), "bd-pm-X")
    _reg_two(fx, env, _dead_pid())          # a pid that does not exist on THIS box, like a hub pid seen from a VM
    r = subprocess.run(["bash", str(_cand("bd-role-claim.sh")), "incumbent", "o1664probe"], env=env,
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0 and r.stdout.strip() == "bd-pm-X", (r.returncode, r.stdout, r.stderr)
    kept = {ln.split("\t")[1] for ln in Path(env["BD_ROLE_REGISTRY"]).read_text().splitlines()[1:]}
    assert kept == {"bd-pm-X", "bd-remote-1"}, kept
    assert "REAP-SKIP off-hub" in Path(env["BD_ROLE_REAP_LOG"]).read_text()


def test_hub_side_reap_of_the_same_registry_still_drops_a_dead_hub_seat(fx: dict) -> None:
    """Control for the VM test: the probe CAN drop -- on the hub, a hub seat with no session and a dead pid goes."""
    env = _claim_env(fx)
    _register(fx, ("bd-remote-1", HOST))
    _sessions(_vm(fx), "bd-remote-1")
    _sessions(fx["tmp"] / "hub", "bd-other")
    _reg_two(fx, env, _dead_pid())
    r = subprocess.run(["bash", str(_cand("bd-role-claim.sh")), "incumbent", "o1664probe"], env=env,
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 2, (r.stdout, r.stderr)
    kept = {ln.split("\t")[1] for ln in Path(env["BD_ROLE_REGISTRY"]).read_text().splitlines()[1:]}
    assert kept == {"bd-remote-1"}, kept


def test_vm_side_claim_of_a_hub_seat_records_the_hub_and_its_hub_pid(fx: dict) -> None:
    env = {**_claim_env(fx), **_on_vm(fx)}
    _sessions(_vm(fx, HUB), "bd-hub-seat")
    (_vm(fx, HUB) / "pane_pid").write_text("31337\n", encoding="utf-8")
    r = subprocess.run(["bash", str(_cand("bd-role-claim.sh")), "claim", "o1664probe", "bd-hub-seat"], env=env,
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    row = [ln for ln in Path(env["BD_ROLE_REGISTRY"]).read_text().splitlines() if "\tbd-hub-seat\t" in ln]
    assert len(row) == 1 and row[0].split("\t")[2:4] == ["31337", HUB], row


def test_vm_routes_hub_seats_to_the_hub_and_its_own_seat_locally(fx: dict) -> None:
    """Lens F2: from a VM, a hub seat is reached over ssh (live delivery remote->hub); the VM's own seat stays local."""
    env = _on_vm(fx)
    _register(fx, ("bd-remote-1", HOST), ("bd-elsewhere", DOWN))
    _sessions(_vm(fx), "bd-remote-1", "bd-vm-scratch")
    _sessions(_vm(fx, HUB), "bd-pm-X", "bd-remote-1", "bd-elsewhere")   # stale same-name copies on the hub never count
    r = subprocess.run(
        ["bash", "-c", '. "$BD_REMOTE_SEAT_LIB"; tmux has-session -t "=bd-pm-X"; echo hub=$?; '
                       'tmux send-keys -t "=bd-remote-1:" -l hi; echo own=$?; tmux ls -F "#S" | sort | tr "\\n" " "'],
        env=env, capture_output=True, text=True, timeout=60, check=False)
    assert "hub=0" in r.stdout and "own=0" in r.stdout, (r.stdout, r.stderr)
    assert r.stdout.split("own=0")[1].split() == ["bd-pm-X", "bd-remote-1", "bd-vm-scratch"], r.stdout
    assert ["has-session", "-t", "=bd-pm-X"] in _argv(_vm(fx, HUB))
    assert ["send-keys", "-t", "=bd-remote-1:", "-l", "hi"] in _argv(_vm(fx)), "own seat went over ssh"
    log = _ssh_log(fx)
    assert f"{HUB}\t'tmux' 'has-session'" in log and "send-keys" not in log, log
    assert f"BD-REMOTE-UNREACHABLE\t{DOWN}" in r.stderr


def test_unreadable_self_address_means_hub(fx: dict) -> None:
    """No address readable -> the lib assumes the hub (pre-O1664 behaviour), never "a VM that ssh's to itself"."""
    env = {**fx["env"], "BD_SELF_HOSTS": " "}
    r = subprocess.run(["bash", "-c", '. "$BD_REMOTE_SEAT_LIB"; bd_on_hub && echo HUB; bd_route_host bd-any; echo end'],
                       env=env, capture_output=True, text=True, timeout=30, check=False)
    assert r.stdout == "HUB\nend\n", (r.stdout, r.stderr)   # hub seat from the hub: no route, local


# ---- bd-pm-pass census ------------------------------------------------------------------------------------------

def test_pm_pass_census_lists_remote_seats_and_names_unreachable_host(fx: dict) -> None:
    root = fx["tmp"] / "pmroot"
    (root / "state").mkdir(parents=True)
    _register(fx, ("bd-worker-R1", HOST), ("bd-worker-R2", DOWN))
    _sessions(fx["tmp"] / "hub", "bd-worker-H1")
    _sessions(_vm(fx), "bd-worker-R1")
    (_vm(fx) / "pane").write_text("x\nesc to interrupt\n", encoding="utf-8")
    r = subprocess.run(["bash", str(_cand("bd-pm-pass.sh"))], env={**fx["env"], "BD_PM_PASS_ROOT": str(root)},
                       capture_output=True, text=True, timeout=120, check=False)
    out = r.stdout
    assert "BUSY=1 IDLE: bd-worker-H1" in out, out  # the remote seat was read through ITS host's pane
    assert f"COULD NOT LOOK (remote host unreachable): {DOWN}" in out, out
    assert "remote seats registered: 2 (2 hosts)" in out, out


# ---- launchers --------------------------------------------------------------------------------------------------

def _kimi_env(fx: dict) -> dict:
    claim = _exe(fx["tmp"] / "claim-stub", '#!/bin/sh\necho "$@" >> "$FAKE_LOG_DIR/claim.log"\n')
    return {**fx["env"], "BD_KIMI_SEATS_ROOT": str(fx["hubfs"] / "seats"), "BD_KIMI_ROLE_CLAIM": str(claim),
            "BD_KIMI_START_TRIES": "3", "BD_KIMI_CONSUME_TRIES": "3"}


def _kimi(fx: dict, env: dict, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(_cand("bd-launch-kimi.sh")), "worker", "kimi", "bd-kimi-o1664t", *extra],
                          env=env, capture_output=True, text=True, timeout=120, check=False)


def test_kimi_host_launch_creates_session_on_the_vm(fx: dict) -> None:
    env = _kimi_env(fx)
    vm = _vm(fx)
    vm.mkdir()
    (vm / "pane").write_text("Welcome to Kimi Code\n ✨ BEGIN. SEAT: bd-kimi-o1664t. ROLE: worker.\n", encoding="utf-8")
    r = _kimi(fx, env, "--host", HOST)
    assert r.returncode == 0 and "LAUNCHED bd-kimi-o1664t on kimi" in r.stdout, (r.stdout, r.stderr)
    assert "shared-fs ok" in r.stdout, r.stdout
    remote = _argv(vm)
    assert any(a[:4] == ["new-session", "-d", "-s", "bd-kimi-o1664t"] for a in remote), remote
    assert ["send-keys", "-t", "=bd-kimi-o1664t:", "BEGIN. SEAT: bd-kimi-o1664t. ROLE: worker.", "Enter"] in remote
    assert not any(a and a[0] == "new-session" for a in _argv(fx["tmp"] / "hub")), "session created on the hub"
    seatdir = fx["hubfs"] / "seats" / "bd-kimi-o1664t"
    assert (vm / str(seatdir).lstrip("/") / "ROLE-AGENT.md").is_file(), "per-seat WORKDIR not staged on the VM"
    rows = [ln.split("\t")[:2] for ln in Path(env["BD_SEAT_HOSTS"]).read_text().splitlines() if not ln.startswith("#")]
    assert rows == [["bd-kimi-o1664t", HOST]], rows
    assert "claim worker bd-kimi-o1664t" in (fx["logs"] / "claim.log").read_text()


def test_kimi_host_without_hub_fs_is_refused_before_any_session(fx: dict) -> None:
    env = {**_kimi_env(fx), "FAKE_NOFS": HOST}
    r = _kimi(fx, env, "--host", HOST)
    assert r.returncode == 6 and "SHARED-FS-ABSENT" in r.stdout, (r.returncode, r.stdout, r.stderr)
    assert not any(a and a[0] == "new-session" for a in _argv(_vm(fx)) + _argv(fx["tmp"] / "hub"))
    assert not Path(env["BD_SEAT_HOSTS"]).exists(), "a refused launch registered the seat"
    assert not list(fx["shared"].glob("state/remote-fs-probe.*")), "nonce file left behind"


def test_kimi_unreachable_host_is_refused(fx: dict) -> None:
    r = _kimi(fx, _kimi_env(fx), "--host", DOWN)
    assert r.returncode == 6 and f"host {DOWN} unreachable" in r.stdout, (r.stdout, r.stderr)
    assert not Path(fx["env"]["BD_SEAT_HOSTS"]).exists()


def test_kimi_without_host_stays_on_the_hub(fx: dict) -> None:
    """Negative control: no --host -> local tmux only, no ssh, no registry row."""
    env = _kimi_env(fx)
    hub = fx["tmp"] / "hub"
    hub.mkdir()
    (hub / "pane").write_text("Welcome to Kimi Code\n ✨ BEGIN. SEAT: bd-kimi-o1664t. ROLE: worker.\n", encoding="utf-8")
    r = _kimi(fx, env)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert any(a[:4] == ["new-session", "-d", "-s", "bd-kimi-o1664t"] for a in _argv(hub))
    assert _ssh_log(fx) == "" and not Path(env["BD_SEAT_HOSTS"]).exists()


def test_codex_dry_run_names_host_and_writes_no_registry(fx: dict) -> None:
    home = fx["tmp"] / "codex-home"
    (home / "agents").mkdir(parents=True)
    (home / "agents" / "bd-worker.toml").write_text('developer_instructions = "YOU ARE bd-<role>. t."\n')
    (home / "bd-worker.config.toml").write_text('model = "m"\nmodel_reasoning_effort = "medium"\n')
    (fx["tmp"] / "card.tsv").write_text("worker\tMULTI\n")
    stub = _exe(fx["tmp"] / "ok-stub", "#!/bin/sh\nexit 0\n")
    (fx["tmp"] / "seat-cwd").mkdir()
    env = {**fx["env"], "CODEX_HOME": str(home), "BD_ROLE_CARDINALITY": str(fx["tmp"] / "card.tsv"),
           "BD_LAUNCH_ROLE_CLAIM": str(stub), "BD_CX_SAY": str(stub), "BD_CX_WORKDIR": str(fx["tmp"] / "seat-cwd"),
           "BD_CX_PREFIX_GATE": "off", "BD_CODEX_APPSERVER_SOCK": str(fx["tmp"] / "no.sock"),
           "BD_CODEX": str(fx["tmp"] / "no-codex")}
    r = subprocess.run(["bash", str(_cand("bd-launch-codex-role.sh")), "worker", "bd-cx-o1664t", "--host", HOST,
                        "--dry-run"], env=env, capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0 and f"DRY host: {HOST} standalone" in r.stdout, (r.stdout, r.stderr)
    assert not Path(env["BD_SEAT_HOSTS"]).exists() and _ssh_log(fx) == ""


def test_agy_rc_with_host_is_refused(fx: dict) -> None:
    r = subprocess.run(["bash", str(_cand("bd-launch-agy.sh")), "worker", "bd-agy-o1664t", "flash", "--rc",
                        "--host", HOST, "--dry-run"], env=fx["env"], capture_output=True, text=True, timeout=60,
                       check=False)
    assert r.returncode == 2 and "--rc with --host is refused" in r.stderr, (r.returncode, r.stdout, r.stderr)


def test_every_patched_script_sources_the_lib_before_its_first_tmux_call() -> None:
    for name in ("bd-launch-role.sh", "bd-launch-codex-role.sh", "bd-launch-kimi.sh", "bd-launch-agy.sh",
                 "bd-say.sh", "bd-role-claim.sh", "bd-pm-pass.sh"):
        lines = _cand(name).read_text(encoding="utf-8").splitlines()
        src = next(i for i, ln in enumerate(lines) if ln.lstrip().startswith('. "$BD_REMOTE_SEAT_LIB"'))
        first = next(i for i, ln in enumerate(lines)
                     if not ln.lstrip().startswith("#") and TMUX_CALL.search(ln) and "tmux()" not in ln)
        assert src < first, f"{name}: lib sourced at line {src + 1}, first tmux call at {first + 1}"


# ---- bd-auth-sync -----------------------------------------------------------------------------------------------

def _auth(fx: dict, *args: str, files: list[Path]) -> subprocess.CompletedProcess:
    env = {**fx["env"], "BD_AUTH_SYNC_FILES": "\n".join(map(str, files)),
           "BD_AUTH_SYNC_LOG": str(fx["tmp"] / "auth-sync.log"), "BD_AUTH_SYNC_SAY_LOG": str(fx["tmp"] / "say.log")}
    return subprocess.run(["bash", str(_cand("bd-auth-sync")), *args], env=env, capture_output=True, text=True,
                          timeout=60, check=False)


def test_auth_sync_copies_missing_backs_up_older_keeps_newer(fx: dict) -> None:
    hub = fx["hubfs"]
    new_f, old_f, newer_f, nosrc = (hub / ".claude-b/.credentials.json", hub / ".codex/auth.json",
                                    hub / ".claude/.credentials.json", hub / ".claude-c/.credentials.json")
    for f, body in ((new_f, "HUB-B"), (old_f, "HUB-CODEX"), (newer_f, "HUB-A")):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
        f.chmod(0o600)
    now = int(time.time())
    vm = _vm(fx)
    v_old, v_newer = vm / str(old_f).lstrip("/"), vm / str(newer_f).lstrip("/")
    for f, body, mt in ((v_old, "VM-OLD", now - 3600), (v_newer, "VM-REFRESHED", now + 3600)):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
        os.utime(f, (mt, mt))
    # O1735 R1 addendum 3: a newer VM file is kept only when a seat on that host answered (rc-0 bd-say row) since
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 3660))
    (fx["tmp"] / "SEAT-HOSTS.tsv").write_text(f"# seat\thost\tat\tby\nbd-w9\t{HOST}\t2026-01-01T00:00:00Z\tt\n")
    (fx["tmp"] / "say.log").write_text(f"{iso}\tbd-w9\tbd-pm\t0\t9\tabcd\t[from bd-w9] ok\tmode=type\n")
    r = _auth(fx, HOST, files=[new_f, old_f, newer_f, nosrc])
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "copied=1 backed_up=1 kept_newer=1 nosrc=1 failed=0" in r.stdout, r.stdout
    v_new = vm / str(new_f).lstrip("/")
    assert v_new.read_text() == "HUB-B" and stat.S_IMODE(v_new.stat().st_mode) == 0o600
    assert int(v_new.stat().st_mtime) == int(new_f.stat().st_mtime), "hub mtime not kept"
    assert v_old.read_text() == "HUB-CODEX" and stat.S_IMODE(v_old.stat().st_mode) == 0o600
    baks = list(v_old.parent.glob("auth.json.pre-*"))
    assert len(baks) == 1 and baks[0].read_text() == "VM-OLD", baks
    assert v_newer.read_text() == "VM-REFRESHED", "a newer VM token was overwritten"
    log = (fx["tmp"] / "auth-sync.log").read_text()
    assert log.count(f"\t{HOST}\t") == 4 and "HUB-" not in log and "KEEP-NEWER" in log, log
    # second run: the two synced files are byte-identical (SAME), the answered-for newer one is kept -> nothing copied
    r2 = _auth(fx, HOST, files=[new_f, old_f, newer_f])
    assert "copied=0 backed_up=0 kept_newer=1 nosrc=0 failed=0 same=2 hub_wins=0" in r2.stdout, r2.stdout


def test_auth_sync_dry_run_writes_nothing_and_down_host_is_could_not_look(fx: dict) -> None:
    f = fx["hubfs"] / ".codex/auth.json"
    f.parent.mkdir(parents=True)
    f.write_text("HUB")
    r = _auth(fx, HOST, "--dry-run", files=[f])
    assert r.returncode == 0 and "DRY copied=1" in r.stdout, r.stdout
    assert not (_vm(fx) / str(f).lstrip("/")).exists()
    r2 = _auth(fx, DOWN, files=[f])
    assert r2.returncode == 3 and f"AUTH-SYNC {DOWN} COULD NOT LOOK" in r2.stdout, r2.stdout
