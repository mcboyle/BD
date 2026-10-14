"""O1670 R2: a TEST run never changes the live tmux server or the live role registry (FINDING-BH2-33: a builder's test
launched two PM seats on the fleet's tmux server and their claims landed in ROLE-OCCUPANCY.tsv).

Candidates (harness, installed by the integrator after BOARD) live in one FIX dir:
  <FIX>/test-shims/tmux -> tmux-guard   on a LIVE socket only read-only commands run (list-*, has-session,
                                        capture-pane, display-message, show-*, -V); anything else exits 1, nothing run
  <FIX>/bd-role-claim.sh                claim/release/reap on the LIVE registry exit 7 under a test (shims on PATH);
                                        read verbs read without reaping

  BD_O1670_R2_TESTGUARD_CANDIDATE=<abs path of the FIX dir>. Unset -> skipped; set but absent -> FAIL.

Hermetic: the "live" server and registry are scratch ones DESIGNATED live via BD_TEST_GUARD_LIVE_TMUX /
BD_TEST_GUARD_LIVE_REGISTRY, so a broken guard can only reach scratch. The fleet's real default socket is judged in
BD_TEST_GUARD_EXPLAIN=1 mode only (decision printed, nothing run).

r5 tmux SAFETY (PM ORDER-pm-A3-A-testguard-r5-tmux-safety, lens D110-D): every tmux call these tests make that can reach
a server names an explicit -S socket under the test's own tmp dir and runs with TMUX unset; teardown kills only those
sockets. The "real tmux" the rig hands out (and the shim execs) is _SAFE_TMUX: it refuses (exit 97, logged) any call
without such a -S, with TMUX set, or with -L -- the rig then FAILS the test (O1670_R5_UNSAFE_TMUX_CALL). Two shapes
start no server on another socket and pass: tmux -V, and bd-role-claim.sh's own read-only census (list-sessions, no
-S: product code) under an EXISTING TMUX_TMPDIR inside the tmp dir. -L / TMUX / TMUX_TMPDIR resolution is judged in
explain mode only.
"""

import ast
import os
import re
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1670_R2_TESTGUARD_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")
REFUSAL = "bd-test-tmux-guard (O1670 R2): REFUSED tmux"
UNSAFE = "O1670_R5_UNSAFE_TMUX_CALL"
_SAFE_TMUX = """#!/usr/bin/python3 -I
import os, sys
ROOT, REAL, LOG = @ROOT@, @REAL@, os.environ.get("BD_O1670_SAFE_LOG") or @LOG@
def inside(p):
    p = os.path.realpath(p)
    return p == ROOT or p.startswith(ROOT + "/")
args, flags, vals, i = sys.argv[1:], "", {}, 0
while i < len(args) and args[i].startswith("-") and args[i] not in ("-", "--"):
    a, j = args[i], 1
    while j < len(a):
        if a[j] in "cfLST":
            vals[a[j]] = a[j + 1:] or (args[i + 1] if i + 1 < len(args) else "")
            i += 0 if a[j + 1:] else 1
            break
        flags += a[j]
        j += 1
    i += 1
rest = args[i + 1:] if args[i:i + 1] == ["--"] else args[i:]
cmd = rest[0] if rest else ""
d = os.environ.get("TMUX_TMPDIR", "")
if os.environ.get("TMUX"):
    why = "TMUX set"
elif "S" in vals:
    why = "" if inside(vals["S"]) else "-S outside the test tmp dir"
elif "L" in vals:
    why = "-L without -S"
elif "V" in flags:
    why = ""
elif cmd in ("list-sessions", "ls") and d and os.path.isdir(d) and inside(d):
    why = ""
else:
    why = "no -S"
if why:
    with open(LOG, "a") as f:
        f.write("REFUSED %s: tmux %s\\n" % (why, " ".join(args)))
    print("O1670_R5_UNSAFE_TMUX_CALL refused (%s): tmux %s" % (why, " ".join(args)), file=sys.stderr)
    sys.exit(97)
os.execv(REAL, [REAL] + args)
"""


def _real_tmux():
    for d in os.environ.get("PATH", "").split(os.pathsep):
        c = Path(d or ".") / "tmux"
        if (
            c.is_file()
            and os.access(c, os.X_OK)
            and "test-shims" not in str(c.resolve())
        ):
            return str(c)
    return ""


@pytest.fixture
def rig(tmp_path):
    fix = Path(CANDIDATE)
    shim = fix / "test-shims"
    claim = fix / "bd-role-claim.sh"
    assert (shim / "tmux").is_file() and os.access(shim / "tmux", os.X_OK), (
        "O1670_R2_SHIM_MISSING"
    )
    assert claim.is_file(), "O1670_R2_ROLE_CLAIM_MISSING"
    real_bin = _real_tmux()
    assert real_bin, "COULD NOT LOOK: no real tmux on PATH"
    rbin = tmp_path / "rbin"
    rbin.mkdir()
    unsafe = tmp_path / "unsafe-tmux.log"
    real = str(rbin / "tmux")
    Path(real).write_text(
        _SAFE_TMUX.replace("@ROOT@", repr(os.path.realpath(tmp_path)))
        .replace("@REAL@", repr(real_bin))
        .replace("@LOG@", repr(str(unsafe)))
    )
    os.chmod(real, 0o755)
    base = {
        k: v
        for k, v in os.environ.items()
        if k not in ("TMUX", "TMUX_PANE", "TMUX_TMPDIR", "BD_LIVE_OK")
    }
    live = tmp_path / "live.sock"
    subprocess.run(
        [real, "-S", str(live), "new-session", "-d", "-s", "seat-live", "sleep 600"],
        env=base,
        check=True,
    )
    env = dict(
        base,
        PATH=f"{shim}:{rbin}:{base['PATH']}",
        BD_TEST_GUARD_LIVE_TMUX=str(live),
        BD_TEST_GUARD_LOG=str(tmp_path / "guard.log"),
        LC_ALL="C.UTF-8",
    )
    yield {
        "tmp": tmp_path,
        "shim": shim,
        "claim": claim,
        "real": real,
        "live": live,
        "env": env,
        "base": base,
        "unsafe": unsafe,
    }
    for sock in (live, tmp_path / "rc" / f"tmux-{os.getuid()}" / "default"):
        subprocess.run(
            [real, "-S", str(sock), "kill-server"], env=base, capture_output=True
        )
    refused = unsafe.read_text() if unsafe.exists() else ""
    assert not refused, f"{UNSAFE}: {refused}"


def _tmux(r, *args, **extra):
    return subprocess.run(
        ["tmux", *args],
        env=dict(r["env"], **extra),
        capture_output=True,
        text=True,
        timeout=30,
    )


def _sessions(r):
    out = subprocess.run(
        [r["real"], "-S", str(r["live"]), "ls", "-F", "#S"],
        env=r["base"],
        capture_output=True,
        text=True,
    ).stdout
    return out.split()


@pytest.mark.parametrize(
    "cmd",
    [
        "new-session -d -s intruder sleep 9",
        "new -d -s i2 sleep 9",
        "send-keys -t seat-live hello Enter",
        "kill-session -t seat-live",
        "kill-server",
        "split-window -t seat-live sleep 9",
        "set-option -g status off",
        "rename-session -t seat-live renamed",
        "run-shell true",
        "frobnicate-unknown",
        "ls ; new-session -d -s chained sleep 9",
    ],
)
def test_a_non_read_only_command_on_the_live_server_is_refused_and_nothing_runs(
    rig, cmd
):
    r = _tmux(rig, "-S", str(rig["live"]), *cmd.split())
    assert r.returncode == 1 and REFUSAL in r.stderr, (cmd, r.returncode, r.stderr)
    assert _sessions(rig) == ["seat-live"]
    panes = subprocess.run(
        [rig["real"], "-S", str(rig["live"]), "list-panes", "-t", "seat-live"],
        env=rig["base"],
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert len(panes) == 1
    assert " REFUSED tmux " in (rig["tmp"] / "guard.log").read_text()


def test_a_test_inside_a_seat_pane_targets_that_server_and_is_refused(rig):
    """r5: explain mode -- a TMUX-set call never runs from these tests (it would name the caller's server)."""
    out = _explain(rig, "new-session", "-d", TMUX=f"{rig['live']},1,0")
    assert out == f"socket={rig['live']} live=yes changing=new-session verdict=REFUSE"
    assert _sessions(rig) == ["seat-live"]


@pytest.mark.parametrize(
    "cmd,expect",
    [
        ("ls -F #S", "seat-live"),
        ("has-session -t seat-live", ""),
        ("display-message -p -t seat-live #S", "seat-live"),
    ],
)
def test_read_only_commands_on_the_live_server_pass_through(rig, cmd, expect):
    r = _tmux(rig, "-S", str(rig["live"]), *cmd.split())
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == expect


def test_tmux_version_passes(rig):
    r = _tmux(rig, "-V")
    assert r.returncode == 0 and r.stdout.startswith("tmux "), r.stdout + r.stderr


def test_a_private_server_runs_any_command(rig):
    sock = str(rig["tmp"] / "priv.sock")
    r = _tmux(rig, "-S", sock, "new-session", "-d", "-s", "p1", "sleep 60")
    assert r.returncode == 0, r.stderr
    assert _tmux(rig, "-S", sock, "kill-server").returncode == 0
    assert _sessions(rig) == ["seat-live"]


def test_bd_live_ok_overrides_and_is_logged(rig):
    r = _tmux(
        rig,
        "-S",
        str(rig["live"]),
        "new-session",
        "-d",
        "-s",
        "sanctioned",
        "sleep 60",
        BD_LIVE_OK="r2 fixture",
    )
    assert r.returncode == 0, r.stderr
    assert "sanctioned" in _sessions(rig)
    assert "OVERRIDE tmux" in (rig["tmp"] / "guard.log").read_text()


@pytest.mark.parametrize(
    "args,verdict",
    [
        (["new-session", "-d", "-s", "x"], "changing=new-session verdict=REFUSE"),
        ([], "changing=new-session verdict=REFUSE"),
        (["list-sessions"], "changing=- verdict=PASS"),
    ],
)
def test_the_fleet_default_socket_is_live_by_default_explain_only(rig, args, verdict):
    env = {k: v for k, v in rig["base"].items() if k != "BD_TEST_GUARD_LIVE_TMUX"}
    out = subprocess.run(
        [str(rig["shim"] / "tmux"), *args],
        env=dict(env, BD_TEST_GUARD_EXPLAIN="1"),
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert out == f"socket=/tmp/tmux-{os.getuid()}/default live=yes {verdict}"


def _registry(path, dead_pid=None):
    rows = "# role\tseat\tpid\thost\tclaimed_at\n"
    if dead_pid:
        rows += f"worker\tr2-dead\t{dead_pid}\thub\t2026-10-13T00:00:00Z\n"
    path.write_text(rows)


def _claim(r, reg, *verb, shims=True, **extra):
    rc = r["tmp"] / "rc"
    rc.mkdir(exist_ok=True)
    env = dict(
        r["env"] if shims else r["base"],
        BD_TEST_SHIM_DIR=str(r["shim"]),
        BD_ROLE_REGISTRY=str(reg),
        BD_TEST_GUARD_LIVE_REGISTRY=str(r["tmp"] / "ROLE-OCCUPANCY.tsv"),
        BD_TEST_GUARD_LOG=str(r["tmp"] / "guard.log"),
        BD_ROLE_REAP_LOG=str(r["tmp"] / "reap.log"),
        BD_ROLE_CARDINALITY="/dev/null",
        TMUX_TMPDIR=str(rc),
    )
    for k, v in extra.items():  # None = UNSET (r2: as in every real test run)
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    return subprocess.run(
        ["bash", str(r["claim"]), *verb],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize(
    "verb",
    [["claim", "worker", "r2-seat", "1"], ["release", "worker", "r2-seat"], ["reap"]],
)
def test_a_registry_write_on_the_live_registry_under_a_test_is_refused(rig, verb):
    reg = rig["tmp"] / "ROLE-OCCUPANCY.tsv"
    _registry(reg)
    before = reg.read_text()
    r = _claim(rig, reg, *verb)
    assert r.returncode == 7, r.stdout + r.stderr
    assert (
        f"bd-role-claim (O1670 R2): REFUSED {verb[0]} on the LIVE registry" in r.stderr
    )
    assert reg.read_text() == before
    assert f"REFUSED bd-role-claim {verb[0]}" in (rig["tmp"] / "guard.log").read_text()


def test_controls_a_claim_is_written_outside_a_test_on_a_fixture_registry_and_with_bd_live_ok(
    rig,
):
    reg = rig["tmp"] / "ROLE-OCCUPANCY.tsv"
    _registry(reg)
    _claim(rig, reg, "claim", "worker", "r2-a", "1", shims=False)
    assert "worker\tr2-a\t" in reg.read_text()
    fixture = rig["tmp"] / "fixture.tsv"
    _claim(rig, fixture, "claim", "worker", "r2-b", "1")
    assert "worker\tr2-b\t" in fixture.read_text()
    _claim(rig, reg, "claim", "worker", "r2-c", "1", BD_LIVE_OK="r2 override")
    assert "worker\tr2-c\t" in reg.read_text()
    assert "OVERRIDE bd-role-claim claim" in (rig["tmp"] / "guard.log").read_text()


@pytest.fixture
def reap_rig(rig):
    """A census that ANSWERS (private server, one unrelated session), a stub remote-seat lib that reaps as on the hub,
    and a registry row whose pid has exited."""
    lib = rig["tmp"] / "lib_remote_seat.sh"
    lib.write_text(
        "bd_seat_host(){ :; }\nbd_seat_where(){ :; }\nbd_on_hub(){ return 0; }\n"
        'bd_pid_alive(){ kill -0 "${2:-}" 2>/dev/null; }\n'
    )
    sockdir = rig["tmp"] / "rc" / f"tmux-{os.getuid()}"
    sockdir.mkdir(parents=True, exist_ok=True)
    sockdir.chmod(0o700)
    subprocess.run(  # the socket the census resolves under TMUX_TMPDIR=rc, named by -S
        [
            rig["real"],
            "-S",
            str(sockdir / "default"),
            "new-session",
            "-d",
            "-s",
            "r2-other",
            "sleep 600",
        ],
        env=rig["base"],
        check=True,
    )
    p = subprocess.Popen(["true"], env=rig["base"])
    p.wait()
    return dict(rig, lib=lib, dead=p.pid)


@pytest.mark.parametrize(
    "verb", [["who", "worker"], ["who-all"], ["incumbent", "worker"]]
)
def test_a_read_verb_on_the_live_registry_under_a_test_does_not_reap(reap_rig, verb):
    reg = reap_rig["tmp"] / "ROLE-OCCUPANCY.tsv"
    _registry(reg, reap_rig["dead"])
    _claim(
        reap_rig,
        reg,
        *verb,
        BD_REMOTE_SEAT_LIB=str(reap_rig["lib"]),
        BD_ROLE_REAP_STAMP=str(reap_rig["tmp"] / "stamp"),
        BD_ROLE_REAP_TTL="0",
    )
    assert "worker\tr2-dead\t" in reg.read_text()


@pytest.mark.parametrize(
    "shims,extra", [(False, {}), (True, {"BD_LIVE_OK": "r2 reap control"})]
)
def test_control_the_same_read_outside_a_test_or_with_bd_live_ok_does_reap(
    reap_rig, shims, extra
):
    reg = reap_rig["tmp"] / "ROLE-OCCUPANCY.tsv"
    _registry(reg, reap_rig["dead"])
    _claim(
        reap_rig,
        reg,
        "who",
        "worker",
        shims=shims,
        BD_REMOTE_SEAT_LIB=str(reap_rig["lib"]),
        BD_ROLE_REAP_STAMP=str(reap_rig["tmp"] / "stamp"),
        BD_ROLE_REAP_TTL="0",
        **extra,
    )
    assert "worker\tr2-dead\t" not in reg.read_text()


def _wrapper(r, name):
    b = r["tmp"] / name
    b.mkdir()
    w = b / "tmux"
    w.write_text(
        f'#!/bin/sh\nexec "{r["shim"] / "tmux"}" -S "{r["tmp"] / name}.sock" "$@"\n'
    )
    w.chmod(0o755)
    return b


def test_a_wrapper_tmux_earlier_on_path_that_calls_the_shim_does_not_loop(rig):
    """test_o1673's shape: a fake-bin `tmux` placed BEFORE the shims calls the shim by absolute path with a private
    socket (r5: -S under the test tmp dir). The shim must hand off to the real tmux after its own PATH entry, not back to the wrapper (an endless loop
    that timed the test out)."""
    b = _wrapper(rig, "wbin")
    priv = rig["tmp"] / "w"
    priv.mkdir()
    env = dict(rig["env"], PATH=f"{b}:{rig['env']['PATH']}", TMUX_TMPDIR=str(priv))
    r = subprocess.run(
        ["tmux", "new-session", "-d", "-s", "w1", "sleep 30"],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert r.returncode == 0, r.stderr
    assert (
        subprocess.run(
            ["tmux", "kill-server"], env=env, capture_output=True, timeout=20
        ).returncode
        == 0
    )


def test_a_caller_that_replaced_path_and_names_the_shim_by_path_reaches_the_real_tmux(
    rig,
):
    """test_o1673's kimi shape: PATH = fake-bin wrapper + system dirs, NO shim dir; the wrapper execs the shim by
    absolute path. The shim must pick the compiled tmux, not the script wrapper ahead of it."""
    b = _wrapper(rig, "kbin")
    priv = rig["tmp"] / "k"
    priv.mkdir()
    real_dir = str(Path(rig["real"]).parent)
    env = dict(rig["base"], PATH=f"{b}:{real_dir}:/usr/bin:/bin", TMUX_TMPDIR=str(priv))
    r = subprocess.run(
        ["tmux", "new-session", "-d", "-s", "k1", "sleep 30"],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert r.returncode == 0, r.stderr
    assert (
        subprocess.run(
            ["tmux", "kill-server"], env=env, capture_output=True, timeout=20
        ).returncode
        == 0
    )


def test_a_wrapper_loop_with_no_real_tmux_fails_fast_instead_of_spinning(rig):
    b = _wrapper(rig, "lbin")
    env = dict(rig["base"], PATH=str(b), TMUX_TMPDIR=str(rig["tmp"] / "l"))
    r = subprocess.run(
        [str(b / "tmux"), "ls"], env=env, capture_output=True, text=True, timeout=20
    )
    assert r.returncode == 127 and "exec loop through a tmux wrapper" in r.stderr, (
        r.returncode,
        r.stderr,
    )


@pytest.mark.parametrize(
    "verb",
    [
        ["claim", "worker", "r2-u", "1"],
        ["who", "worker"],
        ["release", "worker", "r2-u"],
    ],
)
def test_r2_with_shims_on_path_and_the_live_list_var_unset_a_fixture_registry_still_works(
    rig, verb
):
    """r2 (lens D87-D F1): under `set -u` an unset BD_TEST_GUARD_LIVE_REGISTRY killed EVERY verb (rc 1, 'unbound
    variable') once the shims were on PATH -- every real test's shape."""
    fixture = rig["tmp"] / "fixture-unset.tsv"
    _registry(fixture)
    _claim(
        rig,
        fixture,
        "claim",
        "worker",
        "r2-u",
        "1",
        BD_TEST_GUARD_LIVE_REGISTRY=None,
        BD_SEAT="r2-u",
    )
    r = _claim(rig, fixture, *verb, BD_TEST_GUARD_LIVE_REGISTRY=None, BD_SEAT="r2-u")
    assert r.returncode == 0, (verb, r.returncode, r.stderr)
    assert "unbound variable" not in r.stderr


@pytest.mark.parametrize(
    "order", [["-S", "{live}", "-L", "r2x"], ["-L", "r2x", "-S", "{live}"]]
)
def test_r2_dash_S_beats_dash_L_in_either_order(rig, order):
    """r2 (lens D87-D F2): tmux uses -S whenever given; keeping the LAST of -L/-S let `-S live -L x kill-server` run."""
    argv = [a.format(live=rig["live"]) for a in order] + ["kill-server"]
    r = _tmux(rig, *argv)
    assert r.returncode == 1 and REFUSAL in r.stderr, (argv, r.returncode, r.stderr)
    assert _sessions(rig) == ["seat-live"]


def test_r2_a_tmux_env_naming_no_socket_falls_back_to_the_default_explain_only(rig):
    """r2 (lens D87-D F3): TMUX=",1,0" names no socket; tmux then uses the default (live) server."""
    env = {k: v for k, v in rig["base"].items() if k != "BD_TEST_GUARD_LIVE_TMUX"}
    out = subprocess.run(
        [str(rig["shim"] / "tmux"), "kill-server"],
        env=dict(env, TMUX=",1,0", BD_TEST_GUARD_EXPLAIN="1"),
        capture_output=True,
        text=True,
    ).stdout
    assert (
        out.strip()
        == f"socket=/tmp/tmux-{os.getuid()}/default live=yes changing=kill-server verdict=REFUSE"
    )


def test_r2_a_format_job_on_an_allow_listed_command_is_refused_on_the_live_server(rig):
    marker = rig["tmp"] / "job-ran"
    r = _tmux(rig, "-S", str(rig["live"]), "list-sessions", "-F", f"#(touch {marker})")
    assert r.returncode == 1 and REFUSAL in r.stderr, r.stderr
    assert not marker.exists()


def test_r2_a_hard_link_to_the_live_socket_is_live(rig):
    hl = rig["tmp"] / "hardlink.sock"
    os.link(rig["live"], hl)
    r = _tmux(rig, "-S", str(hl), "kill-server")
    assert r.returncode == 1 and REFUSAL in r.stderr, r.stderr
    assert _sessions(rig) == ["seat-live"]


def _buffers(r):
    return subprocess.run(
        [r["real"], "-S", str(r["live"]), "list-buffers"],
        env=r["base"],
        capture_output=True,
        text=True,
    ).stdout.count("\n")


def test_r2_capture_pane_without_p_is_refused_on_the_live_server_with_p_it_reads(rig):
    """r2 (lens D88-D): capture-pane without -p writes a paste buffer into the server."""
    before = _buffers(rig)
    r = _tmux(rig, "-S", str(rig["live"]), "capture-pane", "-t", "seat-live")
    assert r.returncode == 1 and "capture-pane-without-p" in r.stderr, r.stderr
    assert _buffers(rig) == before
    r = _tmux(rig, "-S", str(rig["live"]), "capture-pane", "-pt", "seat-live")
    assert r.returncode == 0, r.stderr


def test_r2_version_flag_with_trailing_words_prints_the_version(rig):
    r = _tmux(rig, "-S", str(rig["live"]), "-V", "new-session", "-d", "-s", "v1")
    assert r.returncode == 0 and r.stdout.startswith("tmux "), (r.stdout, r.stderr)
    assert _sessions(rig) == ["seat-live"]


def _explain(r, *args, **extra):
    return _tmux(r, *args, BD_TEST_GUARD_EXPLAIN="1", **extra).stdout.strip()


def test_r2_a_tmux_env_naming_the_live_socket_is_live(rig):
    priv = rig["tmp"] / "p2"
    priv.mkdir()
    out = _explain(rig, "new-session", TMUX=f"{rig['live']},1,0", TMUX_TMPDIR=str(priv))
    assert out == f"socket={rig['live']} live=yes changing=new-session verdict=REFUSE"


@pytest.mark.parametrize("hops,rc", [("2", 0), ("3", 127)])
def test_r2_the_hop_limit_is_three(rig, hops, rc):
    priv = rig["tmp"] / "p2"
    priv.mkdir()
    r = _tmux(rig, "-V", TMUX_TMPDIR=str(priv), BD_TEST_TMUX_GUARD_HOPS=hops)
    assert r.returncode == rc, (r.stdout, r.stderr)
    assert (rc == 127) == ("exec loop" in r.stderr)


@pytest.mark.parametrize(
    "cmd_dir,live_dir", [("lnkdir", "realdir"), ("realdir", "lnkdir")]
)
def test_r2_a_symlinked_path_to_a_not_yet_running_live_socket_is_live(
    rig, cmd_dir, live_dir
):
    """No server yet, so no inode to compare: both sides of the path compare must be resolved."""
    (rig["tmp"] / "realdir").mkdir()
    (rig["tmp"] / "lnkdir").symlink_to(rig["tmp"] / "realdir")
    out = _explain(
        rig,
        "-S",
        str(rig["tmp"] / cmd_dir / "none.sock"),
        "new-session",
        BD_TEST_GUARD_LIVE_TMUX=str(rig["tmp"] / live_dir / "none.sock"),
    )
    assert out.endswith("live=yes changing=new-session verdict=REFUSE"), out


def test_r2_dash_L_naming_a_live_socket_is_live(rig):
    priv = rig["tmp"] / "p2"
    priv.mkdir()
    sock = priv / f"tmux-{os.getuid()}" / "r2live"
    out = _explain(
        rig,
        "-L",
        "r2live",
        "new-session",
        TMUX_TMPDIR=str(priv),
        BD_TEST_GUARD_LIVE_TMUX=str(sock),
    )
    assert out == f"socket={sock} live=yes changing=new-session verdict=REFUSE"


@pytest.mark.parametrize(
    "args,verdict",
    [
        (["capture-pane", "-t", "-p"], "REFUSE"),
        (["capture-pane", "-b", "p"], "REFUSE"),
        (["capturep", "-ep"], "PASS"),
        (["list-sessions", ";", "capture-pane"], "REFUSE"),
        (["list-sessions", ";", "capture-pane", "-p"], "PASS"),
    ],
)
def test_r2_capture_pane_p_is_read_the_way_getopt_reads_it(rig, args, verdict):
    out = _explain(rig, "-S", str(rig["live"]), *args)
    assert out.endswith(f"verdict={verdict}"), (args, out)


def _explain_tmpdir(r, tmpdir, *args, **extra):
    env = {k: v for k, v in r["base"].items() if k != "BD_TEST_GUARD_LIVE_TMUX"}
    return subprocess.run(
        [str(r["shim"] / "tmux"), *args],
        env=dict(env, TMUX_TMPDIR=tmpdir, BD_TEST_GUARD_EXPLAIN="1", **extra),
        cwd=str(r["tmp"]),
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.mark.parametrize("tmpdir", ["{tmp}/never-made", "rel-never-made", ""])
def test_r3_a_tmux_tmpdir_that_does_not_exist_falls_back_to_the_live_default(
    rig, tmpdir
):
    """r3 (lens D102-D F1): tmux skips a TMUX_TMPDIR whose realpath fails and uses /tmp -- the forgotten-mkdir shape
    reached the live default server through a guard that took TMUX_TMPDIR verbatim. Explain mode: nothing runs."""
    out = _explain_tmpdir(rig, tmpdir.format(tmp=rig["tmp"]), "new-session", "-d")
    assert (
        out
        == f"socket=/tmp/tmux-{os.getuid()}/default live=yes changing=new-session verdict=REFUSE"
    )


def test_r3_dash_L_under_a_missing_tmux_tmpdir_names_a_socket_under_tmp(rig):
    sock = f"/tmp/tmux-{os.getuid()}/r3live"
    out = _explain_tmpdir(
        rig,
        str(rig["tmp"] / "never-made"),
        "-L",
        "r3live",
        "kill-server",
        BD_TEST_GUARD_LIVE_TMUX=sock,
    )
    assert out == f"socket={sock} live=yes changing=kill-server verdict=REFUSE"


def test_r3_controls_an_existing_tmux_tmpdir_is_used_and_a_file_is_not_a_fallback(
    rig,
):
    """tmux uses an existing TMUX_TMPDIR (resolved) and, for a regular file, fails rather than falling back."""
    d = rig["tmp"] / "made"
    d.mkdir()
    (rig["tmp"] / "lnk").symlink_to(d)
    uid = os.getuid()
    out = _explain_tmpdir(rig, str(rig["tmp"] / "lnk"), "new-session")
    assert (
        out
        == f"socket={d}/tmux-{uid}/default live=no changing=new-session verdict=PASS"
    )
    f = rig["tmp"] / "afile"
    f.write_text("")
    out = _explain_tmpdir(rig, str(f), "new-session")
    assert (
        out
        == f"socket={f}/tmux-{uid}/default live=no changing=new-session verdict=PASS"
    )


@pytest.mark.parametrize(
    "label,verdict",
    [
        ("/default", "live=yes changing=kill-server verdict=REFUSE"),
        ("//default", "live=yes changing=kill-server verdict=REFUSE"),
        ("/./default", "live=yes changing=kill-server verdict=REFUSE"),
        ("default", "live=yes changing=kill-server verdict=REFUSE"),
        ("/r4-other", "live=no changing=kill-server verdict=PASS"),
    ],
)
def test_r4_an_absolute_dash_L_label_is_joined_under_the_base_explain_only(
    rig, label, verdict
):
    """r4 (lens D107-D F1): tmux 3.4 make_label builds "%s/%s" (base, label), so -L /default IS the live default
    socket; os.path.join dropped the base and the guard judged socket=/default live=no -> PASS (kill-server ran)."""
    out = _explain_tmpdir(rig, "", "-L", label, "kill-server")
    assert out == f"socket=/tmp/tmux-{os.getuid()}/{label} {verdict}", (label, out)


def test_r4_a_dangling_symlink_tmux_tmpdir_falls_back_to_the_live_default(rig):
    """D106-D advisory: tmux realpath()s TMUX_TMPDIR; a dangling symlink fails there and tmux uses /tmp (live)."""
    (rig["tmp"] / "dangle").symlink_to(rig["tmp"] / "nowhere")
    out = _explain_tmpdir(rig, str(rig["tmp"] / "dangle"), "new-session", "-d")
    assert (
        out
        == f"socket=/tmp/tmux-{os.getuid()}/default live=yes changing=new-session verdict=REFUSE"
    )


def test_r5_the_safe_real_tmux_refuses_every_call_without_a_private_socket(rig):
    """Positive control for the rig's net: these shapes are refused before any server is reached, and logged. Each is
    harmless even if the net were broken: mutating shapes resolve under a private TMUX_TMPDIR, the rest only read."""
    log = rig["tmp"] / "control.log"
    ctl = rig["tmp"] / "ctl"
    ctl.mkdir()
    for args, extra in [
        (["kill-server"], {"TMUX_TMPDIR": str(ctl)}),
        (["-L", "x", "kill-server"], {"TMUX_TMPDIR": str(ctl)}),
        (["-S", "/tmp/tmux-0/elsewhere", "ls"], {}),
        (["-S", str(rig["live"]), "ls"], {"TMUX": f"{rig['live']},1,0"}),
        (["ls"], {"TMUX_TMPDIR": "/"}),
    ]:
        r = subprocess.run(
            [rig["real"], *args],
            env=dict(rig["base"], BD_O1670_SAFE_LOG=str(log), **extra),
            capture_output=True,
            text=True,
        )
        assert r.returncode == 97 and UNSAFE in r.stderr, (args, r.returncode, r.stderr)
    assert log.read_text().count("REFUSED ") == 5
    ok = subprocess.run(
        [rig["real"], "-S", str(rig["live"]), "ls", "-F", "#S"],
        env=rig["base"],
        capture_output=True,
        text=True,
    )
    assert ok.returncode == 0 and ok.stdout.split() == ["seat-live"], ok.stderr


def _tmux_argv_lacking_dash_s(src):
    """Direct real-tmux calls (argv[0] = the rig's real) without '-S', and subprocess calls without env=. A call whose
    env names BD_O1670_SAFE_LOG is a designed refusal of the net's control (harmless shapes only) and is exempt."""
    bad = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        fn = ast.unparse(node.func)
        if fn not in ("subprocess.run", "subprocess.Popen"):
            continue
        if not any(k.arg == "env" for k in node.keywords):
            bad.append(f"line {node.lineno}: {fn} without env=")
        argv = node.args[0] if node.args else None
        if isinstance(argv, ast.List) and argv.elts:
            head = ast.unparse(argv.elts[0])
            is_real = head.endswith("real']") or head == "real"
            exempt = "BD_O1670_SAFE_LOG" in ast.unparse(node)
            if is_real and not exempt and "'-S'" not in ast.unparse(argv):
                bad.append(f"line {node.lineno}: tmux without -S")
    return bad


_SH_REAL_CALL = re.compile(
    r"(?:^|\$\(|;|&&|\|\||\bif\b|\bthen\b|!)\s*(?:\w+=\S*\s+)*(?:env(?:\s+-u\s+\S+|\s+\w+=\S*)*\s+)?"
    r'"?\$\{?REAL(?:_BIN)?\}?"?(\s+\S*)'
)


def _sh_tmux_lines_lacking_dash_s(text):
    """In the harness .sh: every command that runs the real tmux ($REAL / $REAL_BIN in command position, quoted or
    not, after env assignments) names -S. Designed refusals of the net's control (BD_O1670_SAFE_LOG=) are exempt."""
    bad = []
    for n, line in enumerate(text.splitlines(), 1):
        code = line.split(" #", 1)[0]
        if code.lstrip().startswith("#") or "BD_O1670_SAFE_LOG=" in code:
            continue
        for m in _SH_REAL_CALL.finditer(code):
            if m.group(1).split()[:1] != ["-S"]:
                bad.append(f"line {n}: {code.strip()[:120]}")
                break
    return bad


def test_r5_every_tmux_call_in_these_tests_names_a_private_socket(rig):
    """PM r5 rule: a test that FAILS if any tmux call lacks -S. Static half (the runtime half is the rig's net):
    this file's direct real-tmux calls and the candidate harness .sh. Controls: planted bad calls are found and
    non-calls (a -x test, chmod) are not."""
    assert _tmux_argv_lacking_dash_s(Path(__file__).read_text()) == []
    sh = Path(CANDIDATE) / "tests" / "test_o1670_r2_testguard.sh"
    assert sh.is_file(), "O1670_R5_SH_MISSING"
    assert _sh_tmux_lines_lacking_dash_s(sh.read_text()) == []
    planted = 'import subprocess\nsubprocess.run([real, "kill-server"], env={})\nsubprocess.run(["x"])\n'
    assert len(_tmux_argv_lacking_dash_s(planted)) == 2
    planted_sh = (
        "TMUX_TMPDIR=$T/rc $REAL kill-server 2>/dev/null\n"
        'x=$("$REAL_BIN" ls -F x)\n'
        '[ -x "$REAL_BIN" ] || exit 2; chmod 755 "$REAL"\n'
        '$REAL -S "$LIVE" ls && "$REAL" -L y kill-server\n'
    )
    assert len(_sh_tmux_lines_lacking_dash_s(planted_sh)) == 3
