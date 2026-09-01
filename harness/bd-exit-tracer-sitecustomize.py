"""Catch the exit(1) that wedges a pytest worker -- backlog row 102.

WHAT IS ALREADY KNOWN, measured 2026-08-14 across two independent wedges. The
worker does NOT die: zero fatal signals before it goes, no core, no OOM, no
faulthandler traceback, and every one of its 13 threads calls do_exit with raw
code 256 -- (1<<8)|0, i.e. EXIT STATUS 1, NO SIGNAL. It exits deliberately.

That explains every symptom (xdist's "Not properly terminated" is its text for a
channel closed with NO ERROR attached, which is exactly what a clean exit
produces) and leaves exactly one question: WHAT CALLS exit(1)?

This file answers it by wrapping the two ways a process can leave with a status
and dumping the CALLER'S STACK before letting the exit proceed. It is installed
as sitecustomize.py in the venv's site-packages, so it loads at interpreter
startup for every process using that venv -- including execnet's workers, which
are spawned as `python -c` with no -S and therefore DO run site initialisation.
Verified on the fleet before trusting it.

WHY NOT A CONFTEST PLUGIN: conftest loads after interpreter startup and after
execnet's bootstrap, so an exit during worker teardown or bootstrap would be
outside its window. The failure happens in the DRAIN phase, which is exactly
when a conftest-scoped hook is least likely to still be installed.

COST: two function-object rebinds at startup and nothing at all on the hot path.
It writes only when a process actually exits non-zero, which in a healthy run is
never.

THIS PERTURBS THE EXPERIMENT AS LITTLE AS ANYTHING CAN, but it is not nothing:
it is host state, it is untracked, and it applies to every python process using
that venv. Remove it by deleting this file. See ~/bd-OVERNIGHT-HANDOFF.md.
"""
import os
import sys

_LOG = os.environ.get("BD_EXIT_TRACE_LOG", "/tmp/bd-exit-trace.log")


def _dump(kind, code):
    """Write the caller's stack. Deliberately defensive: a tracer that raises
    inside an exit path would turn a diagnosable exit into a crash and destroy
    the very evidence it exists to collect."""
    try:
        if not code:                      # exit(0) is the healthy path
            return
        import datetime
        import traceback
        with open(_LOG, "a") as fh:
            fh.write("=== %s  %s(%r)  pid=%d ppid=%d ===\n" % (
                datetime.datetime.utcnow().isoformat(), kind, code,
                os.getpid(), os.getppid()))
            fh.write("argv: %r\n" % (sys.argv[:4],))
            # The worker id is the single most useful correlator: it is what
            # the master names in "[gwNN] node down".
            for var in ("PYTEST_XDIST_WORKER", "PYTEST_XDIST_TESTRUNUID"):
                if var in os.environ:
                    fh.write("%s=%s\n" % (var, os.environ[var]))
            traceback.print_stack(file=fh)
            exc = sys.exc_info()[1]
            if exc is not None:
                fh.write("--- in-flight exception ---\n")
                traceback.print_exc(file=fh)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())         # os._exit skips buffers; fsync or lose it
    except Exception:
        pass


_real_os_exit = os._exit
_real_sys_exit = sys.exit


def _traced_os_exit(code=0):
    _dump("os._exit", code)
    _real_os_exit(code)


def _traced_sys_exit(code=0):
    _dump("sys.exit", code)
    _real_sys_exit(code)


os._exit = _traced_os_exit
sys.exit = _traced_sys_exit


def _hook(exc_type, exc, tb, _prev=sys.excepthook):
    # `raise SystemExit(1)` bypasses sys.exit entirely, so the wrapper above
    # cannot see it. This is the third door.
    if exc_type is SystemExit:
        _dump("SystemExit-raised", getattr(exc, "code", None))
    return _prev(exc_type, exc, tb)


sys.excepthook = _hook
