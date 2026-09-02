"""A child a test launches must not outlive the test that launched it.

MEASURED 2026-09-02: a register-tool child parked at an injected test barrier
was found with ppid 1, 3.3 hours after the pytest that started it had been
killed. Its release could only ever come from the dead test. Nothing in the
launch bound the child's lifetime to the launcher's, so the kill that ended the
test left the child exactly where it was.

`guarded_popen` is `subprocess.Popen` with one addition: the child asks the
kernel, before exec, to deliver SIGTERM when the thread that forked it dies
(PR_SET_PDEATHSIG). The signal is bound to the forking THREAD, which is why the
guard is applied at the launch site and not by a fixture: a fixture's teardown
never runs for a SIGKILLed worker, and the observed orphan came from exactly
that. The race in which the parent dies between fork and prctl is closed by
checking the parent afterwards and delivering the signal by hand.

Linux only. On another platform the guard is a plain Popen and SAYS so through
`GUARD_ACTIVE`, so a test that relies on it can refuse rather than pass vacuously.
"""
from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
from typing import Any

PR_SET_PDEATHSIG = 1
GUARD_ACTIVE = sys.platform.startswith("linux")


def bind_to_parent(sig: int = signal.SIGTERM) -> None:
    """Runs in the child between fork and exec; see the module docstring."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_PDEATHSIG, int(sig), 0, 0, 0) != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, f"prctl(PR_SET_PDEATHSIG) failed: {os.strerror(errno)}")
    if os.getppid() == 1:
        # The launcher died before the guard was armed: honour it now.
        os.kill(os.getpid(), sig)


def guarded_popen(args: Any, **kwargs: Any) -> subprocess.Popen:  # type: ignore[type-arg]
    if GUARD_ACTIVE:
        if "preexec_fn" in kwargs:
            raise ValueError("guarded_popen owns preexec_fn; compose inside bind_to_parent instead")
        kwargs["preexec_fn"] = bind_to_parent
    return subprocess.Popen(args, **kwargs)
