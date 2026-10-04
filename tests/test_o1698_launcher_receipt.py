"""o1698-launcher-receipt: bd-launch-role.sh prints one RESULT line after every LAUNCHED line.

host-placement (bd-dispatch-ops launch) cannot record a placement from the live launcher: it prints no pid, so the
consumer lands rc=in-doubt + PLACEMENT-NO-PID. The candidate prints
    RESULT seat=<NAME> pid=<pane pid> pid_start=<field 22 of /proc/<pid>/stat> rc=<launch rc>
after the codex and the native-claude LAUNCHED lines (the kick=REFUSED / UNCONFIRMED path included). It prints
`pid=- pid_start=-` when either value cannot be read; it never guesses.

The launcher is deployed from bd-persist, not this repo: the test loads the candidate by absolute path and is opt-in
(BD_TEST_O1698_LAUNCHER_RECEIPT=1; BD_O1698_LAUNCHER_RECEIPT_CANDIDATE overrides the path). The receipt function is
cut out of the candidate between its BEGIN/END markers and run against a PRIVATE tmux server (TMUX_TMPDIR in
tmp_path), so no live session, socket or seat file is touched.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = Path(os.environ.get(
    "BD_O1698_LAUNCHER_RECEIPT_CANDIDATE",
    "/home/mboyle/bd-persist/harness-work/FIX/o1698-launcher-receipt/bd-launch-role.sh"))
pytestmark = pytest.mark.skipif(os.environ.get("BD_TEST_O1698_LAUNCHER_RECEIPT") != "1",
                                reason="harness candidate opt-in: BD_TEST_O1698_LAUNCHER_RECEIPT=1")
BEGIN = "# ---- O1698 launcher-receipt BEGIN"
END = "# ---- O1698 launcher-receipt END"
# The consumer's own parse (FIX/o1698-adapter-host-placement operations.py launch()), verbatim in effect.
CONSUMER_FIXTURE = "RESULT seat=fixture-seat pid=42 pid_start=123 rc=0"


def _source() -> str:
    assert CANDIDATE.is_file(), f"candidate {CANDIDATE} is absent (opt-in was supplied)"
    return CANDIDATE.read_text()


def _receipt_fn() -> str:
    src = _source()
    assert BEGIN in src and END in src, f"LAUNCHER-RECEIPT-ABSENT: no {BEGIN!r} block in {CANDIDATE}"
    return src[src.index(BEGIN):src.index(END)]


def _consume(output: str) -> dict:
    return next((dict(re.findall(r"([A-Za-z_]+)=([^\s]+)", line)) for line in output.splitlines()
                 if line.startswith("RESULT ")), {})


def _stat_start(stat: str) -> str:
    return stat.rsplit(")", 1)[1].split()[19]  # field 22; comm (field 2) may hold spaces and parentheses


@pytest.fixture
def tmux_env(tmp_path: Path):
    tm = shutil.which("tmux")
    if not tm:
        pytest.fail("tmux is required: the receipt reads the pane pid from tmux")
    sock = tmp_path / "tmux"
    sock.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("BD_", "TMUX"))}
    env.update(TMUX_TMPDIR=str(sock), LC_ALL="C")
    yield env
    subprocess.run([tm, "kill-server"], env=env, capture_output=True, timeout=30, check=False)


def _run(env: dict, name: str, rc: str, route: str = "", ssh_log: Path | None = None) -> subprocess.CompletedProcess:
    # bd_route_host / bd_ssh stand in for lib_remote_seat.sh: route="" = the seat is on this host; a route makes
    # bd_ssh log the host and run the command here (same kernel), so the remote branch is exercised for real.
    prog = "\n".join([
        "set -u",
        f"NAME={name!r}",
        f"bd_route_host(){{ [ -n {route!r} ] && printf '%s\\n' {route!r}; return 0; }}",
        f"bd_ssh(){{ local h=$1; shift; printf '%s\\n' \"$h $*\" >> {str(ssh_log or '/dev/null')!r}; \"$@\"; }}",
        _receipt_fn(),
        f"launch_receipt {rc}",
    ])
    return subprocess.run(["bash", "-c", prog], env=env, capture_output=True, text=True, timeout=60, check=False)


def _session(env: dict, name: str) -> tuple[str, str]:
    subprocess.run(["tmux", "new-session", "-d", "-s", name, "sleep 300"], env=env, check=True, timeout=30)
    pid = subprocess.run(["tmux", "display-message", "-p", "-t", f"={name}:", "#{pane_pid}"], env=env,
                         capture_output=True, text=True, check=True, timeout=30).stdout.strip()
    return pid, _stat_start(Path(f"/proc/{pid}/stat").read_text())


def test_receipt_names_the_pane_pid_and_its_proc_start_time(tmux_env):
    pid, start = _session(tmux_env, "rcpt-seat")
    r = _run(tmux_env, "rcpt-seat", "0")
    assert r.returncode == 0, r.stderr
    assert r.stdout == f"RESULT seat=rcpt-seat pid={pid} pid_start={start} rc=0\n", (
        f"LAUNCHER-RECEIPT-WRONG: {r.stdout!r} != pid {pid} start {start}")
    got = _consume(r.stdout)
    assert got.get("pid", "").isdecimal() and int(got["pid"]) > 0 and got.get("pid_start") == start and got.get("rc") == "0"


def test_receipt_field_order_matches_the_consumer_fixture(tmux_env):
    _session(tmux_env, "fixture-seat")
    out = _run(tmux_env, "fixture-seat", "0").stdout.strip()
    shape = re.sub(r"pid=\d+", "pid=42", re.sub(r"pid_start=\d+", "pid_start=123", out))
    assert shape == CONSUMER_FIXTURE, f"LAUNCHER-RECEIPT-SHAPE: {out!r} vs consumer fixture {CONSUMER_FIXTURE!r}"


def test_a_remote_seat_reads_proc_on_its_own_host(tmux_env, tmp_path):
    pid, start = _session(tmux_env, "rcpt-remote")
    log = tmp_path / "ssh.log"
    r = _run(tmux_env, "rcpt-remote", "0", route="10.0.70.99", ssh_log=log)
    assert r.stdout.strip() == f"RESULT seat=rcpt-remote pid={pid} pid_start={start} rc=0", r.stdout + r.stderr
    assert log.read_text() == f"10.0.70.99 cat /proc/{pid}/stat\n", log.read_text()


def test_unreadable_values_print_dashes_never_a_guess(tmux_env, tmp_path):
    r = _run(tmux_env, "no-such-seat", "0")  # no session: tmux cannot name a pane pid
    assert r.stdout == "RESULT seat=no-such-seat pid=- pid_start=- rc=0\n", r.stdout + r.stderr
    assert _consume(r.stdout).get("pid") == "-"  # consumer: not decimal -> in-doubt
    _session(tmux_env, "rcpt-gone")
    log = tmp_path / "ssh.log"
    # the seat's host cannot be read (ssh fails): the pid alone is not a receipt
    prog_env = dict(tmux_env)
    r = subprocess.run(["bash", "-c", "\n".join([
        "set -u", "NAME=rcpt-gone",
        "bd_route_host(){ echo 10.0.70.98; }",
        f"bd_ssh(){{ echo \"$*\" >> {str(log)!r}; return 255; }}",
        _receipt_fn(), "launch_receipt 0"])], env=prog_env, capture_output=True, text=True, timeout=60, check=False)
    assert r.stdout == "RESULT seat=rcpt-gone pid=- pid_start=- rc=0\n", r.stdout + r.stderr
    assert log.read_text().startswith("10.0.70.98 cat /proc/"), f"the remote read was attempted: {log.read_text()!r}"


def test_a_comm_with_spaces_and_parens_still_yields_field_22(tmux_env, tmp_path):
    # comm (field 2) is the exec'd basename: a name with ' ' and ')' shifts every naive whitespace split
    odd = tmp_path / "a b) c"
    odd.symlink_to(shutil.which("sleep"))
    subprocess.run(["tmux", "new-session", "-d", "-s", "rcpt-odd", f"exec '{odd}' 300"], env=tmux_env,
                   check=True, timeout=30)
    pid = subprocess.run(["tmux", "display-message", "-p", "-t", "=rcpt-odd:", "#{pane_pid}"], env=tmux_env,
                         capture_output=True, text=True, check=True, timeout=30).stdout.strip()
    stat = Path(f"/proc/{pid}/stat").read_text()
    assert "(a b) c)" in stat, f"control: the pane process must carry the odd comm: {stat[:60]!r}"
    r = _run(tmux_env, "rcpt-odd", "0")
    assert r.stdout.strip() == f"RESULT seat=rcpt-odd pid={pid} pid_start={_stat_start(stat)} rc=0", r.stdout + r.stderr


def test_the_launch_rc_is_reported(tmux_env):
    _session(tmux_env, "rcpt-rc7")
    assert _run(tmux_env, "rcpt-rc7", "7").stdout.rstrip().endswith(" rc=7")


def test_every_launched_line_is_followed_by_exactly_one_receipt():
    lines = _source().splitlines()
    launched = [i for i, ln in enumerate(lines) if re.match(r'\s*echo "LAUNCHED \$NAME ', ln)]
    assert len(launched) == 2, f"expected the codex and native LAUNCHED lines, found {len(launched)}"
    following = [lines[i + 1].strip() for i in launched]
    assert following == ["launch_receipt 0", 'launch_receipt "${KICK_EXIT:-0}"'], (
        f"LAUNCHER-RECEIPT-NOT-WIRED: lines after LAUNCHED: {following}")
    calls = [ln for ln in lines if re.match(r"\s*launch_receipt ", ln)]
    assert len(calls) == 2, calls
    fn_line = next(i for i, ln in enumerate(lines) if ln.startswith("launch_receipt()"))
    assert fn_line < min(launched), "launch_receipt is defined before its first call"
