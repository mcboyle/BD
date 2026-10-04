import importlib.util
import math
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
KILL = getattr(signal, "SIGKILL", 9)


@pytest.fixture
def batch(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "tools/capture_batch.py"
    spec = importlib.util.spec_from_file_location("capture_batch_timeout_subject", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    return module


class Capture:
    def __init__(self, pid, mode="ok", ignore_term=False, saves_on_int=False, interrupts=1):
        self.pid = pid
        self.mode = mode
        self.ignore_term = ignore_term
        self.saves_on_int = saves_on_int
        self.interrupts = interrupts
        self.alive = True
        self.signals = []
        self.waits = []

    def poll(self):
        return None if self.alive else 0

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.alive and self.mode == "interrupt":
            self.interrupts -= 1
            if self.interrupts <= 0:
                self.mode = "hung"
            raise KeyboardInterrupt
        if self.alive and self.mode == "hung":
            assert timeout is not None and math.isfinite(timeout), "BATCH-WAIT-UNBOUNDED: hung capture has no deadline"
            raise subprocess.TimeoutExpired("fixture-capture", timeout)
        self.alive = False
        return 0

    def terminate(self):
        self.send(signal.SIGTERM)

    def kill(self):
        self.send(KILL)

    def send(self, sig):
        self.signals.append(sig)
        if sig == signal.SIGINT:
            self.alive = self.alive and not self.saves_on_int
        elif sig == KILL or not self.ignore_term:
            self.alive = False


def launch(batch, monkeypatch, captures):
    options = []
    pending = iter(captures)

    def popen(cmd, **kwargs):
        options.append(kwargs)
        return next(pending)

    monkeypatch.setattr(batch.subprocess, "Popen", popen)
    if hasattr(batch, "os"):
        by_pid = {p.pid: p for p in captures}
        monkeypatch.setattr(batch.os, "killpg", lambda pid, sig: by_pid[pid].send(sig), raising=False)
    return options


def arguments(tmp_path, count=2):
    args = ["--out-dir", str(tmp_path / "captures"), "--stagger-secs", "0"]
    for index in range(count):
        args += ["--job", f"capture{index}", f"https://example.com/{index}"]
    return args


def test_success_reports_all_jobs(batch, monkeypatch, tmp_path, capsys):
    captures = [Capture(10001), Capture(10002)]
    launch(batch, monkeypatch, captures)
    assert batch.run(arguments(tmp_path)) == 0
    assert "2/2 succeeded" in capsys.readouterr().out
    assert all(not p.alive and not p.signals for p in captures)


@pytest.mark.parametrize("ignore_term", [False, True])
def test_hung_capture_is_stopped_and_reported(batch, monkeypatch, tmp_path, capsys, ignore_term):
    hung = Capture(10001, "hung", ignore_term)
    healthy = Capture(10002)
    options = launch(batch, monkeypatch, [hung, healthy])
    assert batch.run(arguments(tmp_path)) == 1
    output = capsys.readouterr()
    assert "capture0: TIMEOUT" in output.out, "BATCH-TIMEOUT-NOT-RECORDED"
    assert "capture1: OK" in output.out and "1/2 succeeded" in output.out
    assert not hung.alive and not healthy.alive
    assert hung.signals[0] == signal.SIGTERM
    if ignore_term:
        assert KILL in hung.signals
    assert all(wait is not None and 0 < wait < math.inf for wait in hung.waits)
    if batch.os.name == "posix":
        assert all(item["start_new_session"] for item in options)


@pytest.mark.parametrize("phase", ["wait", "launch"])
def test_interrupt_cleans_all_launched_captures(batch, monkeypatch, tmp_path, phase):
    captures = [Capture(10001, "interrupt" if phase == "wait" else "hung", True), Capture(10002, "hung", True)]
    launch(batch, monkeypatch, captures)
    if phase == "launch":
        def interrupt(seconds):
            raise KeyboardInterrupt
        monkeypatch.setattr(batch.time, "sleep", interrupt)
    with pytest.raises(KeyboardInterrupt):
        batch.run(arguments(tmp_path))
    launched = captures if phase == "wait" else captures[:1]
    assert all(not p.alive for p in launched), "BATCH-INTERRUPT-ORPHANS: interrupted launcher left live captures"
    assert all(signal.SIGTERM in p.signals and KILL in p.signals for p in launched)
    if batch.os.name == "posix":
        assert all(p.signals[0] == signal.SIGINT for p in launched), "BATCH-INTERRUPT-NO-SAVE: Ctrl-C not forwarded first"


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
def test_interrupt_lets_captures_save_before_cleanup(batch, monkeypatch, tmp_path):
    captures = [Capture(10001, "interrupt", True, saves_on_int=True), Capture(10002, "hung", True, saves_on_int=True)]
    launch(batch, monkeypatch, captures)
    with pytest.raises(KeyboardInterrupt):
        batch.run(arguments(tmp_path))
    assert all(p.signals == [signal.SIGINT] for p in captures), "BATCH-INTERRUPT-NO-SAVE: saving capture was terminated"


def test_second_interrupt_skips_save_grace(batch, monkeypatch, tmp_path):
    captures = [Capture(10001, "interrupt", True, interrupts=2), Capture(10002, "hung", True)]
    launch(batch, monkeypatch, captures)
    with pytest.raises(KeyboardInterrupt):
        batch.run(arguments(tmp_path))
    assert all(not p.alive and signal.SIGTERM in p.signals and KILL in p.signals for p in captures)
    assert captures[1].waits == [5, 5], "BATCH-INTERRUPT-GRACE: second Ctrl-C still waited for saves"


def test_timeout_flag_is_used(batch, monkeypatch, tmp_path):
    capture = Capture(10001)
    launch(batch, monkeypatch, [capture])
    assert batch.run(arguments(tmp_path, 1) + ["--wait-timeout", "7.5"]) == 0
    assert capture.waits == [7.5]


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_timeout_must_be_finite_and_positive(batch, tmp_path, value, capsys):
    with pytest.raises(SystemExit) as raised:
        batch.run(arguments(tmp_path, 1) + ["--wait-timeout", value])
    assert raised.value.code == 2
    assert "finite positive" in capsys.readouterr().err


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
@pytest.mark.parametrize("parent_ignores_term", [False, True])
def test_stop_capture_kills_real_browser_descendant(batch, monkeypatch, parent_ignores_term):
    child_code = "import signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('browser-ready', flush=True); signal.pause()"
    parent_code = (
        "import signal,subprocess,sys; "
        f"signal.signal(signal.SIGTERM, signal.{'SIG_IGN' if parent_ignores_term else 'SIG_DFL'}); "
        f"p=subprocess.Popen([sys.executable, '-u', '-c', {child_code!r}]); "
        "print('parent-ready:'+str(p.pid), flush=True); signal.pause()"
    )
    proc = subprocess.Popen([sys.executable, "-u", "-c", parent_code],
                            stdout=subprocess.PIPE, bufsize=0, start_new_session=True)
    original_wait = proc.wait
    monkeypatch.setattr(proc, "wait", lambda timeout=None: original_wait(timeout=min(timeout, 0.1)))
    try:
        lines = []
        for _ in range(2):
            ready, _, _ = select.select([proc.stdout], [], [], 5)
            assert ready, "BATCH-PROCESS-CONTROL: fixture did not become ready"
            lines.append(proc.stdout.readline().decode().strip())
        parent_line = next(line for line in lines if line.startswith("parent-ready:"))
        child_pid = int(parent_line.split(":")[1])
        assert "browser-ready" in lines and os.getpgid(child_pid) == proc.pid
        assert os.getpgid(proc.pid) == proc.pid and proc.poll() is None
        batch._stop_capture(proc)
        assert proc.poll() is not None
        ready, _, _ = select.select([proc.stdout], [], [], 5)
        assert ready and proc.stdout.read() == b"", "BATCH-BROWSER-ORPHAN: descendant still owns stdout"
    finally:
        try:
            os.killpg(proc.pid, KILL)
        except ProcessLookupError:
            pass
        original_wait(timeout=5)
        proc.stdout.close()


FAKE_TTY_CAPTURE = """
import os, select, sys, time
out = sys.argv[sys.argv.index("--out") + 1]
try:
    open(out + ".started", "w").close()
    while not select.select([sys.stdin], [], [], 1.0)[0]:
        pass
    sys.stdin.readline()
except (EOFError, KeyboardInterrupt):
    pass
time.sleep(0.3)
open(out, "w").write("saved")
"""

TERMINAL_JOB = """
import importlib.util, os, sys
from pathlib import Path
batch_path, fake, out = sys.argv[1:4]
spec = importlib.util.spec_from_file_location("capture_batch_terminal_job", batch_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module._CAPTURE_CLI = Path(fake)
os.chdir(out)
try:
    module.run(["--job", "a", "http://a.invalid", "--job", "b", "http://b.invalid",
                "--out-dir", out, "--stagger-secs", "0"])
except KeyboardInterrupt:
    os._exit(130)
os._exit(3)
"""


@pytest.mark.skipif(os.name != "posix", reason="POSIX terminal job control")
def test_terminal_ctrl_c_keeps_interactive_saves(tmp_path):
    import pty

    fake = tmp_path / "fake_capture.py"
    fake.write_text(FAKE_TTY_CAPTURE)
    out = tmp_path / "captures"
    out.mkdir()
    batch_path = Path(__file__).resolve().parents[1] / "tools/capture_batch.py"
    pid, master = pty.fork()
    if pid == 0:
        try:
            os.execv(sys.executable, [sys.executable, "-c", TERMINAL_JOB, str(batch_path), str(fake), str(out)])
        finally:
            os._exit(127)

    def drain(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if select.select([master], [], [], 0.05)[0]:
                try:
                    os.read(master, 4096)
                except OSError:
                    return

    status = None
    try:
        deadline = time.monotonic() + 20
        while len(list(out.glob("*.started"))) < 2 and time.monotonic() < deadline:
            drain(0.1)
        assert len(list(out.glob("*.started"))) == 2, "BATCH-PROCESS-CONTROL: fake captures did not start"
        drain(0.3)
        os.write(master, b"\x03")
        deadline = time.monotonic() + 20
        while status is None and time.monotonic() < deadline:
            drain(0.1)
            done, raw = os.waitpid(pid, os.WNOHANG)
            if done:
                status = os.waitstatus_to_exitcode(raw)
    finally:
        if status is None:
            os.kill(pid, KILL)
            os.waitpid(pid, 0)
        os.close(master)
    assert status == 130
    saved = sorted(path.name for path in out.glob("*.wacz"))
    assert saved == ["a.wacz", "b.wacz"], f"BATCH-CTRLC-LOST-SAVES: terminal Ctrl-C saved {saved}"
