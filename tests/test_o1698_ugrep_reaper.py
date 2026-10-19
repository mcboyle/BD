"""O1698 recursive-search-report (ugrep-reaper r4, REPORT-ONLY, PM ruling 15): recursive grep/rg/ugrep searches
older than 120 s whose search root is under a fleet root are listed; nothing else is; nothing is ever signalled.

The candidate (harness-work/FIX/o1698-ugrep-reaper/bd-ugrep-reaper.sh, or BD_O1698_UGREP_REAPER_CANDIDATE)
runs only with BD_TEST_O1698_UGREP_REAPER=1. Fixture "searches" are bash processes that only sleep: nothing
is ever searched. Most are started through symlinks named ugrep / grep / rg as `<tool> -c 'sleep 180; :'
<extra...>`; the extra words are bash's $0.. but the reporter reads them as the tool's options and operands.
Real-shaped fixtures run bash under the exact argv of a runaway (`grep -r PAT -n DIR`): bash takes -r as its
restricted flag and runs PAT, a sleeper script on PATH. BD_REAPER_ONLY_PIDS confines every run to the
fixture pids, so a real search elsewhere on the host is never looked at.

No-signal proof (ruling 15 test a): every run gets BASH_ENV defining a `kill` function (it shadows the builtin)
and a PATH of fake kill/pkill/killall/timeout binaries; all append to one record that must stay empty, and
every fixture must still be alive afterwards. BD_REAPER_SAY points the bd-status line at a recorder.
"""
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.skipif(os.environ.get("BD_TEST_O1698_UGREP_REAPER") != "1",
                                reason="opt-in BD_TEST_O1698_UGREP_REAPER=1 required")
CANDIDATE = os.environ.get("BD_O1698_UGREP_REAPER_CANDIDATE",
                           "/home/mboyle/bd-persist/harness-work/FIX/o1698-ugrep-reaper/bd-ugrep-reaper.sh")
FLEET = "/home/mboyle/bd-persist"
SLEEPER = "o1698sleeper"


@pytest.fixture
def lab():
    assert Path(CANDIDATE).is_file(), f"UGREP_REAPER_CANDIDATE_MISSING: {CANDIDATE}"
    root = Path(tempfile.mkdtemp(prefix="o1698-reaper-"))
    for d in ("bin", "fake", "scratch"):
        (root / d).mkdir()
    bash = shutil.which("bash")
    for name in ("ugrep", "grep", "rg"):
        (root / "bin" / name).symlink_to(bash)
    (root / "bin" / SLEEPER).write_text("sleep 180; :\n")
    (root / "bin" / SLEEPER).chmod(0o755)
    rec = root / "signals.rec"
    for name in ("kill", "pkill", "killall", "timeout"):
        (root / "fake" / name).write_text(f'#!/bin/sh\necho "{name} $*" >> "{rec}"\n')
        (root / "fake" / name).chmod(0o755)
    (root / "fake-env.sh").write_text(f'kill() {{ echo "builtin-kill $*" >> "{rec}"; }}\n')
    said = root / "say.rec"
    (root / "say").write_text(f'#!/bin/sh\nprintf "%s|%s|%s\\n" "$BD_SEAT" "$1" "$2" >> "{said}"\n')
    (root / "say").chmod(0o755)
    procs = []

    def start(tool, cwd, *extra, stdin=subprocess.DEVNULL, argv0=None, pre=(), shaped=None):
        # argv0="ugrep": bash itself runs under that argv[0] (comm stays bash), like a seat's `exec -a ugrep`.
        # pre=("-B",): a bash flag the search tool reads as value-taking, so the script is that option's value
        # and the first word of `extra` becomes the tool's pattern.  shaped=[...]: the exact runaway argv.
        env = None
        if shaped:
            cmd, exe = shaped, bash
            env = dict(os.environ, PATH=f"{root / 'bin'}:{os.environ['PATH']}")
        elif argv0:
            cmd, exe = [argv0, "-c", *pre, "sleep 180; :", *extra], bash
        else:
            cmd, exe = [str(root / "bin" / tool), "-c", *pre, "sleep 180; :", *extra], None
        p = subprocess.Popen(cmd, executable=exe, cwd=cwd, stdin=stdin, env=env, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(p)
        return p

    def reap(pids, *args, min_age="1", ps_body=None):
        # ps_body: a fake `ps` (shell body) first on PATH -- the could-not-look probes (B3-r4 F1).
        if ps_body is not None:
            (root / "fake" / "ps").write_text(f"#!/bin/sh\n{ps_body}\n")
            (root / "fake" / "ps").chmod(0o755)
        env = dict(os.environ, BD_REAPER_LOG=str(root / "report.log"), BD_REAPER_MIN_AGE=min_age,
                   BD_REAPER_ONLY_PIDS=" ".join(str(p.pid) for p in pids), BD_REAPER_SAY=str(root / "say"),
                   BASH_ENV=str(root / "fake-env.sh"), PATH=f"{root / 'fake'}:{os.environ['PATH']}")
        r = subprocess.run(["bash", CANDIDATE, *args], env=env, capture_output=True,
                           text=True, timeout=60, check=False)
        time.sleep(0.5)
        sent = rec.read_text() if rec.exists() else ""
        assert sent == "", f"SIGNAL_SENT: {sent}"
        dead = [p.pid for p in pids if p.poll() is not None]
        assert dead == [], f"FIXTURE_DIED: {dead} {r.stdout}{r.stderr}"
        return r

    def log():
        f = root / "report.log"
        return f.read_text() if f.exists() else ""

    def says():
        return said.read_text().splitlines() if said.exists() else []

    try:
        yield root, start, reap, log, says
    finally:
        for p in procs:
            if p.stdin:
                p.stdin.close()
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        shutil.rmtree(root, ignore_errors=True)


def _line(log, kind, p):
    hits = [ln for ln in log.splitlines() if f" {kind} pid={p.pid} " in ln]
    return hits[0] if hits else ""


def listed(log, p):
    return _line(log, "WOULD-REAP", p)


# --- positive controls (ruling 15 test c; lens control: a sleep-wrapped recursive grep over bd-persist) ------
@pytest.mark.parametrize("argv", [
    ["grep", "-r", SLEEPER, "-n", FLEET],          # the 03:39Z runaway's shape (`grep -R spare2 -n /home/mboyle`)
    ["ugrep", "-r", SLEEPER, FLEET],
], ids=["grep-r-PAT-n-DIR", "ugrep-r-PAT-DIR"])
def test_real_shaped_runaway_is_listed_and_status_sent(lab, argv):
    root, start, reap, log, says = lab
    p = start(None, root / "scratch", shaped=argv)
    time.sleep(2)
    assert Path(f"/proc/{p.pid}/cmdline").read_text().split("\0")[:len(argv)] == argv
    r = reap([p])
    line = listed(log(), p)
    assert line, f"RUNAWAY_NOT_LISTED: {r.stdout}{log()}"
    assert f"root={FLEET} " in line and "reason=recursive-operand-is-fleet-root" in line
    assert f"tool={argv[0]} " in line and " age=" in line and " seat=" in line
    assert "argv=" + " ".join(argv) in line
    assert line.split(" ", 1)[1] in r.stdout
    assert says() == [f"bd-search-report|bd-status|search-report: FOUND 1: {root / 'report.log'}"], says()
    assert "SUMMARY FOUND 1 (" in log() and "STATUS-SENT rc=0 target=bd-status" in log()
    assert r.returncode == 0 and "FOUND NONE" not in r.stdout


def test_exec_a_ugrep_with_foreign_comm_is_listed(lab):
    # r1 F1: argv[0] ugrep, comm bash -- a seat's grep function is `exec -a ugrep <claude binary>`.
    _root, start, reap, log, _says = lab
    p = start(None, FLEET, "-r", argv0="ugrep")
    time.sleep(2)
    assert Path(f"/proc/{p.pid}/comm").read_text().strip() != "ugrep"
    r = reap([p])
    line = listed(log(), p)
    assert line and "argv=ugrep -c " in line, f"ARGV0_UGREP_NOT_LISTED: {r.stdout}{log()}"
    assert "reason=recursive-cwd-is-fleet-root" in line and f"root={FLEET} " in line


@pytest.mark.parametrize("tool,cwd,extra", [
    ("ugrep", FLEET, ("-r",)),
    ("grep", FLEET, ("-Rn",)),
    ("grep", "/tmp", ("-d", "recurse", FLEET)),     # r1 P5: -d recurse is -r
    ("grep", "/tmp", ("-drecurse", FLEET)),
    ("grep", "/tmp", ("--directories=recurse", FLEET)),
    ("rg", FLEET, ()),                              # rg recurses by default; /dev/null stdin is not readable
    ("rg", FLEET, ("-d", "3")),                     # r2 F2: -d/--max-depth takes 3
    ("ugrep", "/tmp", ("-r", "--depth=2", FLEET)),  # r3 F1: --depth=VALUE is known
    ("ugrep", "/tmp", ("-r", FLEET + "/harness")),  # ruling 15: UNDER a fleet root is listed
    ("grep", "/tmp", ("-r", "/")),                  # an ancestor of every fleet root
], ids=["ugrep-r-cwd", "grep-Rn", "d-recurse", "drecurse", "directories-recurse", "rg-default", "rg-max-depth",
        "ugrep-depth-eq", "under-root", "ancestor-root"])
def test_recursive_search_over_fleet_is_listed(lab, tool, cwd, extra):
    _root, start, reap, log, _says = lab
    p = start(tool, cwd, *extra)
    time.sleep(2)
    r = reap([p])
    assert listed(log(), p), f"NOT_LISTED: {r.stdout}{log()}"


def test_ugrep_without_file_on_a_terminal_recurses_the_cwd(lab):
    # ugrep --help: no FILE and a terminal on stdin -> recursive as if -r.
    _root, start, reap, log, _says = lab
    master, slave = os.openpty()
    try:
        p = start("ugrep", FLEET, stdin=slave)
        time.sleep(2)
        reap([p])
        assert listed(log(), p), f"TTY_UGREP_NOT_LISTED: {log()}"
    finally:
        os.close(slave)
        os.close(master)


# --- not listed ----------------------------------------------------------------------------------------
def test_search_under_a_scratch_root_is_not_listed_and_no_status(lab):
    root, start, reap, log, says = lab
    p = start("ugrep", root / "scratch", "-r")
    time.sleep(2)
    r = reap([p])
    assert log() == "" and "FOUND NONE" in r.stdout, r.stdout
    assert says() == [], says()


def test_grep_without_recursive_flag_is_not_listed(lab):
    _root, start, reap, log, _says = lab
    p = start("grep", FLEET)
    time.sleep(2)
    reap([p])
    assert log() == ""


def test_young_search_is_not_listed(lab):
    # MUTANT spec: drop the age check -> this goes RED.
    _root, start, reap, log, _says = lab
    p = start("ugrep", FLEET, "-r")
    reap([p], min_age="60")
    assert log() == "", f"UGREP_LISTED_UNDER_AGE: {log()}"


def test_search_root_argument_decides_not_cwd(lab):
    root, start, reap, log, _says = lab
    away = start("ugrep", "/home/mboyle", "-r", str(root / "scratch"))   # cwd fleet, root scratch -> no
    over = start("ugrep", root / "scratch", "-r", FLEET)                  # cwd scratch, root fleet -> yes
    time.sleep(2)
    reap([away, over])
    assert not listed(log(), away), log()
    assert listed(log(), over), log()


# r2 (REFUTE B1-r1 F3) + PM F2 ruling A + lens control: a stdin-pipe grep is not listed.
@pytest.mark.parametrize("tool,extra", [
    ("grep", ()),                            # lens control: `... | grep PAT`
    ("grep", ("-eERROR",)),                  # P1: the r in -eERROR is the pattern, not -r
    ("grep", ("-frules.txt", "somefile")),   # P4: -f value + a file operand
    ("ugrep", ("--line-buffered", "-E")),    # P6: Claude-style stdin monitor
    ("rg", ("-r", "X")),                     # rg -r is --replace; rg on a stdin pipe reads stdin
], ids=["stdin-grep", "P1-eERROR", "P4-frules", "P6-ugrep-monitor", "rg-replace-stdin"])
def test_stdin_pipe_search_is_not_listed(lab, tool, extra):
    _root, start, reap, log, _says = lab
    p = start(tool, FLEET, *extra, stdin=subprocess.PIPE)
    time.sleep(2)
    r = reap([p])
    assert log() == "", f"MONITOR_LISTED: {log()}{r.stdout}"


def test_rg_text_flag_does_not_swallow_the_operand(lab):
    # r2 REFUTE C1 F1: rg -a/--text takes no value. cwd fleet, operand scratch -> the root is scratch.
    root, start, reap, log, _says = lab
    p = start("rg", FLEET, "-a", str(root / "scratch"))
    time.sleep(2)
    reap([p])
    assert log() == "", f"RG_TEXT_FALSE_LISTING: {log()}"


def test_ugrep_depth_value_does_not_shift_the_pattern(lab):
    # r3 REFUTE B2 F1. argv: ugrep -c -B 'sleep..' -r --depth 2 <FLEET> .   (cwd scratch)
    # -> pattern = the FLEET string, root = "." = scratch -> not listed.
    root, start, reap, log, _says = lab
    p = start("ugrep", root / "scratch", "-r", "--depth", "2", FLEET, ".", pre=("-B",))
    time.sleep(2)
    reap([p])
    assert log() == "", f"UGREP_DEPTH_FALSE_LISTING: {log()}"


@pytest.mark.parametrize("tool,extra,opt", [
    ("ugrep", ("-r", "--future-opt", "VAL", FLEET, "."), "--future-opt"),
    ("rg", ("--future-opt", "VAL", FLEET, "."), "--future-opt"),
    ("grep", ("-rj", FLEET), "-j"),                  # GNU grep has no -j
    ("grep", ("-r", "--bogus-opt", "x", FLEET), "--bogus-opt"),   # B3-r4 F2 probe
], ids=["ugrep-long", "rg-long", "grep-short", "B3-bogus-opt"])
def test_unknown_option_is_reported_as_spared(lab, tool, extra, opt):
    # r3 REFUTE B2 F2: an option this tool version does not list -> UNPARSED (spared), never WOULD-REAP.
    _root, start, reap, log, says = lab
    p = start(tool, FLEET + "/harness", *extra)
    time.sleep(2)
    r = reap([p])
    assert not listed(log(), p), f"UNKNOWN_OPTION_LISTED_AS_REAP: {log()}{r.stdout}"
    line = _line(log(), "UNPARSED", p)
    assert line and f"opt={opt} " in line and "reason=unknown-option-spared" in line, log()
    # B3-r4 F2: a run that logged uncertainty never ends in FOUND NONE.
    assert "FOUND NONE" not in r.stdout and "SUMMARY FOUND 0; UNPARSED 1 (" in r.stdout, r.stdout
    assert len(says()) == 1 and "search-report: FOUND 0; UNPARSED 1:" in says()[0], says()


# --- no kill path (ruling 15 test a) -------------------------------------------------------------------
def test_apply_flag_is_refused_and_nothing_is_signalled(lab):
    _root, start, reap, log, says = lab
    p = start("ugrep", FLEET, "-r")
    time.sleep(2)
    r = reap([p], "--apply")
    assert r.returncode == 2 and "REPORT-ONLY" in r.stderr, (r.returncode, r.stderr)
    assert log() == "" and says() == []


def test_candidate_has_no_signalling_command():
    src = Path(CANDIDATE).read_text()
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    hits = re.findall(r"\b(?:kill|pkill|killall|skill|timeout)\b", code)
    assert hits == [], f"SIGNAL_PATH_PRESENT: {hits}"


# B3-r4 F1: a process table that cannot be read is COULD NOT LOOK (exit 3 + bd-status), never FOUND NONE.
@pytest.mark.parametrize("ps_body", [None, "exit 1", "exit 0"], ids=["control-real-ps", "ps-fails", "ps-empty"])
def test_unreadable_process_table_is_could_not_look(lab, ps_body):
    _root, start, reap, log, says = lab
    p = start("ugrep", FLEET, "-r")
    time.sleep(2)
    r = reap([p], ps_body=ps_body)
    if ps_body is None:   # control: the same fixture with the real ps is found
        assert listed(log(), p) and r.returncode == 0 and "COULD" not in r.stdout, r.stdout
        return
    assert "FOUND NONE" not in r.stdout, f"COULD_NOT_LOOK_REPORTED_AS_NONE: {r.stdout}"
    assert r.returncode == 3, f"COULD_NOT_LOOK_EXIT_NOT_3: rc={r.returncode} {r.stdout}"
    assert "COULD-NOT-LOOK scope=host reason=process-table-unreadable" in log(), log()
    assert "SUMMARY FOUND 0; COULD NOT LOOK 1 (" in r.stdout, r.stdout
    assert len(says()) == 1 and "search-report: FOUND 0; COULD NOT LOOK 1:" in says()[0], says()
