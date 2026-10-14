"""O1670 R5 flaky isolation: the configured-shell failure line must be in the
job log before the gate signals EOF on its exec-status pipe.

THE FLAKE: test_v3_66_1040_remote_job_registry.py::
test_public_run_missing_configured_shell_is_exec_failed_with_exact_id failed
once on 10.0.70.120 (farm baseline e626d248, under suite load) with
``assert 'configured shell could not be executed' in ''``: rc and the
EXEC_FAILED verdict were right, the log was empty.

THE CAUSE: after a failed execvp the gate wrote its failure frame, CLOSED the
exec-status descriptor and only then printed the failure line into the log.
The parent's await_exec_handoff reads to EOF, so it returned EXEC_FAILED --
"record ... is durable and RETAINED" -- the moment the descriptor closed, and
an operator (or the test) reading the log at that point raced the gate's
print. Load stretches the gap between the close and the print.

The test widens that gap on purpose: a sitecustomize, active only in the gate
child, sleeps right after the gate closes its exec-status descriptor. It does
not change what the gate writes or in which order; it only makes the existing
order observable every time.
"""
import os
import pathlib
import sys
import time

import pytest

BD_GATE_SCOPE = "module"

_REPO = pathlib.Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-jobs"
_REGISTRY_LITERAL = 'JOBS_DIR = pathlib.Path("/tmp/bd-jobs")'
_LINE = "configured shell could not be executed"
_DELAY_ENV = "O1670_R5_DELAY_AFTER_EXEC_CLOSE"
_MARK_ENV = "O1670_R5_SHIM_MARK"

# Gate argv (bd-jobs _gate_argv): python, tool, --internal-gate-exec,
# release_fd, ready_fd, exec_fd, "--", user argv.  Read from /proc because
# sys.argv is not guaranteed to be set while sitecustomize runs.
_SHIM = '''
import os, time
def _o1670_r5_install():
    delay = os.environ.get(%(delay)r)
    if not delay:
        return
    try:
        with open("/proc/self/cmdline", "rb") as fh:
            argv = fh.read().split(b"\\0")
    except OSError:
        return
    if len(argv) < 7 or argv[2] != b"--internal-gate-exec":
        return
    exec_fd = int(argv[5])
    real_close = os.close
    def close(fd):
        if fd == exec_fd:
            with open(os.environ[%(mark)r], "a") as fh:
                fh.write("closing exec fd %%d\\n" %% fd)
            real_close(fd)
            time.sleep(float(delay))
            return None
        return real_close(fd)
    os.close = close
_o1670_r5_install()
''' % {"delay": _DELAY_ENV, "mark": _MARK_ENV}


def _private_jobs(tmp_path):
    """A private copy of bd-jobs whose registry is under tmp_path (the same
    anchored rewrite the 1040 suite uses), imported as a module."""
    import importlib.machinery
    import importlib.util
    registry = tmp_path / "bd-jobs"
    source = _TOOL.read_text(encoding="utf-8")
    assert source.count(_REGISTRY_LITERAL) == 1, (
        "registry literal no longer occurs exactly once in %s" % _TOOL)
    copy = tmp_path / "bd-jobs-private"
    copy.write_text(source.replace(
        _REGISTRY_LITERAL, "JOBS_DIR = pathlib.Path(%r)" % str(registry)),
        encoding="utf-8")
    copy.chmod(0o755)
    loader = importlib.machinery.SourceFileLoader("bd_jobs_o1670_r5", str(copy))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    assert mod.JOBS_DIR == registry, (mod.JOBS_DIR, registry)
    return mod


def _run_args(purpose, command):
    return type("A", (), {"host": "local", "purpose": purpose,
                          "command": command})()


def _wait_for_line(log_path, deadline_s):
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if _LINE in log_path.read_text(encoding="utf-8"):
            return True
        time.sleep(0.05)
    return False


@pytest.mark.parametrize("delay", ["0", "1.5"])
def test_exec_failure_line_is_in_the_log_when_exec_failed_returns(
        tmp_path, monkeypatch, capsys, delay):
    """delay=0 is the shim-off control; delay=1.5 widens the close->print gap."""
    jobs = _private_jobs(tmp_path)
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    (shim_dir / "sitecustomize.py").write_text(_SHIM, encoding="utf-8")
    mark = tmp_path / "shim.mark"
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(
        p for p in (str(shim_dir), os.environ.get("PYTHONPATH", "")) if p))
    monkeypatch.setenv(_MARK_ENV, str(mark))
    if delay == "0":
        monkeypatch.delenv(_DELAY_ENV, raising=False)
    else:
        monkeypatch.setenv(_DELAY_ENV, delay)
    missing = "/definitely/missing/configured-shell"
    monkeypatch.setattr(jobs, "_user_argv", lambda cmd: [missing, "-c", cmd])
    entry = None
    try:
        rc = jobs.cmd_run(_run_args(
            "o1670 r5 missing configured shell", ["--", "printf", "never-ran"]))
        log_now = None
        entries = jobs.load_all()
        assert len(entries) == 1, entries
        entry = entries[0]
        log_path = pathlib.Path(entry["log"])
        log_now = log_path.read_text(encoding="utf-8")
        out, err = capsys.readouterr()
        assert rc == jobs._EXIT_EXEC_FAILED, (rc, err)
        assert "EXEC_FAILED" in err
        # Let a late gate finish its print before the verdict, so no gate
        # child outlives the test either way.
        late = _wait_for_line(log_path, 15.0)
        if delay != "0":
            assert mark.exists() and "closing exec fd" in mark.read_text(), (
                "O1670_R5_SHIM_INACTIVE: the gate child never closed its "
                "exec-status descriptor through the shim; the widened window "
                "was not exercised (python=%s)" % sys.executable)
        assert _LINE in log_now, (
            "O1670_R5_EXEC_FAILURE_LOG_AFTER_EOF: cmd_run returned EXEC_FAILED "
            "(record durable and RETAINED) while the job log did not yet hold "
            "%r; the gate closed its exec-status descriptor before writing the "
            "line (line arrived later: %s). log at return: %r"
            % (_LINE, late, log_now))
    finally:
        if entry is not None:
            jobs.forget(entry)
