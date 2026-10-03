"""O1698 P2 prep: per-seat tmux sockets (DESIGN-C v3.2 "Per-seat sockets"), DRY-RUN cut -- nothing is installed.

r3 (R1 ruling (a), bd-pm-C): in opted-in mode every call the lib hands to the previous tmux runs with TMUX unset, so a
lib call made INSIDE a per-seat seat still reaches the host's shared server; a seat that routes to another host
(lib_remote_seat.sh bd_route_host prints a host) never takes a local per-seat socket.
Opt in with BD_TEST_O1698_SEAT_SOCKETS=1 (or the candidate dir alone, as in r2).

BD_O1698_SEAT_SOCKETS_CANDIDATE is the candidate scripts DIR (bd-tmux-sockets.lib.sh, the dry-run, and the three
callers). BD_O1698_SEAT_SOCKETS_LIVE (default: the live harness dir) holds today's callers for the argv-identity control.
Real tmux only, on a private server: a short mktemp TMUX_TMPDIR and TMUX unset, so the hub's server is never reached.
"""

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_SEAT_SOCKETS_CANDIDATE", "")
LIVE = os.environ.get("BD_O1698_SEAT_SOCKETS_LIVE", "/home/mboyle/bd-persist/harness")
OPT_IN = os.environ.get("BD_TEST_O1698_SEAT_SOCKETS") == "1"
pytestmark = pytest.mark.skipif(not (CANDIDATE or OPT_IN), reason="candidate opt-in required")
VM, HUB = "10.0.70.183", "10.0.70.164"
TMUX = shutil.which("tmux")


def lib():
    assert CANDIDATE, "BD_TEST_O1698_SEAT_SOCKETS=1 needs BD_O1698_SEAT_SOCKETS_CANDIDATE (the candidate scripts dir)"
    p = Path(CANDIDATE) / "bd-tmux-sockets.lib.sh"
    assert p.is_file(), f"no socket lib in the candidate dir: {p}"
    return p


@pytest.fixture
def box():
    assert TMUX, "tmux is required"
    root = tempfile.mkdtemp(prefix="o1698s-")  # short: a unix socket path must stay under 108 bytes
    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE") and not k.startswith("BD_")}
    env.update(LC_ALL="C", TMUX_TMPDIR=root, BD_HUB_HOST=HUB)
    sockdir = Path(root) / f"tmux-{os.getuid()}"

    def sh(script, self_host=VM, on=True, **extra):
        e = dict(env, BD_SELF_HOSTS=self_host, **extra)
        if on:
            e["BD_SEAT_SOCKETS"] = "1"
        return subprocess.run(["bash", "-c", f'. "{lib()}" || exit 9\n{script}'], capture_output=True, text=True,
                              env=e, timeout=60, check=False)

    def raw(*args):
        return subprocess.run([TMUX, *args], capture_output=True, text=True, env=env, timeout=30, check=False)

    class B:
        pass

    b = B()
    b.sh, b.raw, b.env, b.sockdir, b.root = sh, raw, env, sockdir, root
    yield b
    if sockdir.is_dir():
        for s in sockdir.iterdir():
            subprocess.run([TMUX, "-L", s.name, "kill-server"], env=env, capture_output=True, check=False)
    shutil.rmtree(root, ignore_errors=True)


def sockets(b):
    return sorted(p.name for p in b.sockdir.iterdir() if p.is_socket()) if b.sockdir.is_dir() else []


def test_two_vm_seats_get_two_sockets_and_one_server_death_costs_one_seat(box):
    r = box.sh("tmux new-session -d -s seatA 'sleep 300' && tmux new-session -d -s seatB 'sleep 300'")
    assert r.returncode == 0, r.stderr
    assert sockets(box) == ["seatA", "seatB"], sockets(box)
    assert box.raw("-L", "seatA", "kill-server").returncode == 0
    r = box.sh("tmux has-session -t =seatB && echo B-ALIVE; tmux has-session -t =seatA || echo A-GONE")
    assert "B-ALIVE" in r.stdout and "A-GONE" in r.stdout, r.stdout + r.stderr


def test_ls_unions_every_socket_including_seats_still_on_the_shared_one(box):
    assert box.raw("new-session", "-d", "-s", "legacy", "sleep 300").returncode == 0   # launched before the switch
    box.sh("tmux new-session -d -s seatA 'sleep 300'; tmux new-session -d -s seatB 'sleep 300'")
    r = box.sh("tmux ls -F '#S'")
    assert r.returncode == 0 and sorted(r.stdout.split()) == ["legacy", "seatA", "seatB"], r.stdout + r.stderr
    r = box.sh("tmux list-panes -a -F '#{session_name}'")
    assert sorted(r.stdout.split()) == ["legacy", "seatA", "seatB"], r.stdout
    box.raw("kill-server")


def test_hub_seat_keeps_the_shared_socket(box):
    r = box.sh("tmux new-session -d -s hubseat 'sleep 300'", self_host=HUB)
    assert r.returncode == 0, r.stderr
    assert "hubseat" not in sockets(box) and box.raw("has-session", "-t", "=hubseat").returncode == 0
    box.raw("kill-server")


def test_a_seat_already_on_the_shared_socket_is_reachable_and_never_duplicated(box):
    assert box.raw("new-session", "-d", "-s", "legacy", "sleep 300").returncode == 0
    r = box.sh("tmux has-session -t =legacy && echo FOUND; tmux new-session -d -s legacy 'sleep 300'; echo rc=$?")
    assert "FOUND" in r.stdout and "rc=1" in r.stdout, r.stdout
    assert "already lives on the shared socket" in r.stderr, r.stderr
    assert "legacy" not in sockets(box)
    box.raw("kill-server")


@pytest.mark.parametrize("self_host", [VM, HUB], ids=["vm", "hub"])
def test_opt_in_unset_leaves_every_argv_as_today(box, self_host):
    cmds = ["has-session -t =s1", "new-session -d -s s1 -x 220 -y 50", "send-keys -t =s1: -l x",
            "capture-pane -J -pt s1:", "list-panes -a -F '#S'", "ls -F '#S'"]
    script = "\n".join(f"bd_sock_argv {c}" for c in cmds)
    r = box.sh(script, self_host=self_host, on=False)
    want = box.sh("\n".join(f"set -- {c}; printf 'tmux'; printf ' %q' \"$@\"; echo" for c in cmds), on=False)
    assert r.returncode == 0 and r.stdout == want.stdout and "-L" not in r.stdout, r.stdout


def test_explicit_socket_and_targetless_commands_are_untouched(box):
    r = box.sh("bd_sock_argv -L other ls; bd_sock_argv kill-server; bd_sock_argv has-session -t '=bad name'")
    assert r.stdout.splitlines() == ["tmux -L other ls", "tmux kill-server", r"tmux has-session -t =bad\ name"], r.stdout


def test_a_previous_tmux_function_is_chained_not_replaced(box):
    e = dict(box.env, BD_SELF_HOSTS=VM)
    r = subprocess.run(["bash", "-c", f'tmux() {{ echo "PREV $*"; }}\n. "{lib()}"\ntmux ls -F x'],
                       capture_output=True, text=True, env=e, timeout=30, check=False)
    assert r.stdout.strip() == "PREV ls -F x", r.stdout + r.stderr


def test_dry_run_prints_both_seats_and_starts_no_server(box):
    dry = Path(CANDIDATE) / "bd-tmux-sockets-dryrun.sh"
    r = subprocess.run(["bash", str(dry), str(lib())], capture_output=True, text=True, env=box.env, timeout=60,
                       check=False)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "tmux -L bd-worker-C9-C new-session -d -s bd-worker-C9-C" in r.stdout
    assert "  launch-role    tmux new-session -d -s bd-worker-D4 " in r.stdout   # hub seat, sockets on: no -L
    assert "tmux servers started by this dry-run: 0" in r.stdout
    assert sockets(box) == []


@pytest.mark.parametrize("caller", ["bd-working.sh", "bd-tmux-dispatch.sh"])
def test_callers_run_the_same_tmux_argv_as_today_when_opt_in_is_unset(box, caller, tmp_path):
    log = tmp_path / "argv.log"
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "tmux").write_text(f'#!/bin/bash\nprintf "%s\\n" "$*" >> "{log}"\nexit 1\n')
    (fake / "tmux").chmod(0o755)
    e = dict(box.env, PATH=f"{fake}:{box.env['PATH']}", BD_PERSIST=str(tmp_path),
             BD_DISPATCH_QUEUE=str(tmp_path / "queue.txt"), BD_TMUX_SOCK_LIB=str(lib()))
    (tmp_path / "logs").mkdir()
    (tmp_path / "harness").mkdir()
    (tmp_path / "harness" / "bd-load-gate.sh").write_text("#!/bin/bash\nexit 0\n")  # past the load hold, to tmux
    e["BD_DISPATCH_DRY_RUN"] = "1"
    (tmp_path / "queue.txt").write_text("o1698-fixture-row\n")  # a non-empty queue, so the tick reaches `tmux ls`
    runs = {}
    for label, d in (("live", LIVE), ("candidate", CANDIDATE)):
        log.write_text("")
        subprocess.run(["bash", str(Path(d) / caller)], capture_output=True, text=True, env=e, timeout=60, check=False)
        runs[label] = log.read_text()
    assert runs["candidate"], "the fake tmux saw no call (control: the caller must reach tmux)"
    assert runs["candidate"] == runs["live"], runs


@pytest.mark.parametrize("caller", ["bd-working.sh", "bd-tmux-dispatch.sh"])
def test_callers_refuse_when_opted_in_without_the_lib(box, caller, tmp_path):
    e = dict(box.env, BD_SEAT_SOCKETS="1", BD_TMUX_SOCK_LIB=str(tmp_path / "absent.lib.sh"), BD_PERSIST=str(tmp_path))
    r = subprocess.run(["bash", str(Path(CANDIDATE) / caller)], capture_output=True, text=True, env=e, timeout=60,
                       check=False)
    assert r.returncode == 4 and "BD_SEAT_SOCKETS=1 but" in (r.stdout + r.stderr), (r.returncode, r.stdout, r.stderr)


def test_launch_role_sources_the_lib_after_the_remote_seat_lib():
    text = (Path(CANDIDATE) / "bd-launch-role.sh").read_text()
    remote = text.index('. "$BD_REMOTE_SEAT_LIB"')
    sock = text.index('. "$BD_TMUX_SOCK_LIB"')
    assert remote < sock < text.index("tmux has-session -t \"=$NAME\""), "socket lib must chain lib_remote_seat's tmux()"


# ---- r3 -------------------------------------------------------------------------------------------------------------
def inside(b, seat, script):
    """Run <script> (lib sourced) in a new window of per-seat seat <seat>: tmux sets $TMUX to that seat's own server."""
    body = Path(b.root) / f"in-{seat}.sh"
    out = Path(b.root) / f"in-{seat}.out"
    body.write_text(f'echo "TMUX=$TMUX"\n. "{lib()}" || exit 9\n{script}\n')
    r = b.raw("-L", seat, "new-window", "-t", f"={seat}", f'bash {body} > {out}.tmp 2>&1; mv {out}.tmp {out}')
    assert r.returncode == 0, r.stderr
    deadline = time.monotonic() + 30
    while not out.exists():  # the window runs async; wait for its output file, bounded
        assert time.monotonic() < deadline, f"no output from inside {seat}"
        time.sleep(0.1)
    text = out.read_text()
    assert f"/{seat}," in text.splitlines()[0], text  # control: the script really ran inside <seat>'s own server
    return text


def seat_a_and_legacy(b):
    assert b.raw("new-session", "-d", "-s", "legacy", "sleep 300").returncode == 0   # on the shared socket
    r = b.sh("tmux new-session -d -s seatA 'sleep 300'")
    assert r.returncode == 0 and "seatA" in sockets(b), r.stderr + str(sockets(b))


def test_r3_a_inside_a_per_seat_seat_the_lib_reaches_the_shared_socket(box):
    seat_a_and_legacy(box)
    out = inside(box, "seatA", "tmux has-session -t =legacy; echo rc=$?\necho \"ls=$(tmux ls -F '#S' | sort | xargs)\"")
    assert "rc=0" in out, out                  # r2: rc=1, the pass path asked seatA's own server
    assert "ls=legacy seatA" in out, out       # the union's shared leg is the shared server, not seatA again
    box.raw("kill-server")


def test_r3_b_inside_a_per_seat_seat_the_duplicate_guard_sees_the_shared_seat(box):
    seat_a_and_legacy(box)
    out = inside(box, "seatA", "tmux new-session -d -s legacy 'sleep 300'; echo rc=$?")
    assert "rc=1" in out and "already lives on the shared socket" in out, out
    assert "legacy" not in sockets(box), sockets(box)   # r2: a second copy of legacy on -L legacy
    box.raw("kill-server")


def test_r3_c_a_remote_routed_seat_never_takes_a_local_socket(box, tmp_path):
    log = tmp_path / "argv.log"
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "tmux").write_text(f'#!/bin/bash\nprintf "LOCAL %s\\n" "$*" >> "{log}"\n')
    (fake / "tmux").chmod(0o755)
    box.sockdir.mkdir(mode=0o700)
    for seat in ("bd-worker-R1", "bd-worker-L1"):   # both "already up" by file test: -t would take sock mode
        subprocess.run(["python3", "-c", "import socket,sys; socket.socket(socket.AF_UNIX).bind(sys.argv[1])",
                        str(box.sockdir / seat)], check=True)
    pre = (f'tmux() {{ printf "STUB %s\\n" "$*" >> "{log}"; [ "$1" != has-session ]; }}\n'
           'bd_route_host() { case "$1" in bd-worker-R1) echo vm1 ;; esac; }\n')
    script = ("tmux new-session -d -s bd-worker-R1 -x 220 -y 50\ntmux has-session -t =bd-worker-R1\n"
              "tmux new-session -d -s bd-worker-L1\n")
    e = dict(box.env, PATH=f"{fake}:{box.env['PATH']}", BD_SELF_HOSTS=HUB, BD_SEAT_SOCKETS="1",
             BD_SEAT_SOCKETS_HUB="1")
    r = subprocess.run(["bash", "-c", f'{pre}. "{lib()}" || exit 9\n{script}'], capture_output=True, text=True, env=e,
                       timeout=30, check=False)
    assert log.read_text().splitlines() == [
        "STUB new-session -d -s bd-worker-R1 -x 220 -y 50",   # remote seat: the routing tmux() got the argv unchanged
        "STUB has-session -t =bd-worker-R1",
        "STUB has-session -t =bd-worker-L1",                  # local seat: duplicate guard, then its own socket --
        "LOCAL -L bd-worker-L1 new-session -d -s bd-worker-L1",  # control: this probe does see a local binary call
    ], log.read_text() + r.stderr


def test_r3_d_off_path_is_the_previous_tmux_in_the_same_process_and_on_path_drops_tmux(box):
    stub = 'tmux() { printf "%s|%s|%s\\n" "$BASH_SUBSHELL" "${TMUX:-}" "$*"; }\n'
    cmds = "tmux has-session -t =s1\ntmux ls -F x\ntmux -L other ls\ntmux kill-server\n"
    e = dict(box.env, BD_SELF_HOSTS=VM, TMUX="/x/tmux-1/seatA,1,0")
    run = lambda src, **x: subprocess.run(["bash", "-c", f"{stub}{src}{cmds}"], capture_output=True, text=True,
                                          env=dict(e, **x), timeout=30, check=False).stdout
    without = run("")
    assert without.splitlines()[0] == "0|/x/tmux-1/seatA,1,0|has-session -t =s1", without
    assert run(f'. "{lib()}"\n') == without                                   # OFF: byte-identical, no subshell
    on = run(f'. "{lib()}"\n', BD_SEAT_SOCKETS="1").splitlines()
    assert on == ["1||has-session -t =s1", "1||ls -F x", "1||-L other ls", "1||kill-server"], on


def test_r3_e_open4_plain_tmux_inside_a_per_seat_seat_misses_the_shared_seat(box):
    seat_a_and_legacy(box)
    out = inside(box, "seatA", "command tmux has-session -t =legacy; echo plain=$?\ntmux has-session -t =legacy; "
                               "echo lib=$?")
    assert "plain=1" in out, out   # OPEN 4 (enable gate, not fixed here): bd-say etc. reach only seatA's server
    assert "lib=0" in out, out     # control: the same pane does see legacy through the lib
    box.raw("kill-server")
