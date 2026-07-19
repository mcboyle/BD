"""Subprocess helpers — cross-platform process-group isolation.

Ported pattern from Unrud/video-downloader (GPLv3 reference, this is an
adaptation). Their explanation in `src/downloader/__init__.py`:

    # Start child process in its own process group to shield it from
    # signals by terminals (e.g. SIGINT) and to identify remaining
    # children. yt-dlp doesn't kill ffmpeg and other subprocesses on
    # error.

Same problem exists for BulkDownloader's spawns:
  - openvpn (vpn_openvpn.py) — daemon-style, often forks helpers
  - ffmpeg (hls_downloader.py, live_recorder.py) — spawns its own
    decoder/encoder processes, plus probe subprocesses
  - dev_tools subprocess — usually a Python script that may spawn more

Without process-group isolation, calling .terminate() on the parent
leaves the children running. They hold file handles open (preventing
clean rename of partial downloads), keep CPU busy, and occasionally
sit on the network port we need for the next attempt.

With isolation:
  - POSIX: os.setpgrp() makes the child the leader of its own process
    group. os.killpg(pid, sig) then signals every descendant in one
    call.
  - Windows: CREATE_NEW_PROCESS_GROUP makes the child eligible to
    receive Ctrl+Break (the only "kill whole tree" signal that works
    on Windows without taskkill /T).

Usage:
    import subprocess
    from .subprocess_helpers import isolated_popen_kwargs, kill_process_tree

    proc = subprocess.Popen(cmd, **isolated_popen_kwargs())
    # ... later ...
    kill_process_tree(proc)  # kills proc and its descendants
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


def isolated_popen_kwargs() -> dict:
    """Return Popen kwargs that put the child in its own process group.

    Merge into your existing kwargs:
        proc = subprocess.Popen(cmd, **isolated_popen_kwargs())
    or
        kwargs = {**isolated_popen_kwargs(), "stdout": subprocess.PIPE}
        proc = subprocess.Popen(cmd, **kwargs)
    """
    if sys.platform == "win32":
        # Windows: create a new console process group so Ctrl+Break can
        # be sent to the whole tree via GenerateConsoleCtrlEvent.
        # CREATE_NO_WINDOW additionally hides the console window for
        # GUI launches; we keep stdout/stderr redirection working.
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            | subprocess.CREATE_NO_WINDOW       # type: ignore[attr-defined]
        )
        return {"creationflags": creationflags}
    # POSIX: set process group leader. Using start_new_session=True is
    # equivalent for our purposes and also detaches from the controlling
    # terminal so SIGINT (Ctrl+C) on the parent shell doesn't propagate
    # to the child by terminal-line discipline. Slightly stronger
    # isolation than bare setpgrp.
    return {"start_new_session": True}


def kill_process_tree(proc: subprocess.Popen, timeout: float = 5.0) -> bool:
    """Politely terminate proc and every descendant; SIGKILL on timeout.

    Returns True if the tree exited cleanly within `timeout`, False if
    we had to force-kill. Safe to call even if proc has already exited
    (no-op in that case).

    Use this in cancel paths instead of `proc.terminate()`, which only
    signals proc itself and orphans every child.
    """
    if proc.poll() is not None:
        return True  # already exited

    if sys.platform == "win32":
        # Windows can't signal a whole tree from Python without taskkill.
        # Use the OS tool — present on every supported Windows since XP.
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass
        # Wait for the process record to clear.
        try:
            proc.wait(timeout=max(0.5, timeout / 2))
            return True
        except subprocess.TimeoutExpired:
            return False

    # POSIX: signal the whole process group, then escalate.
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        # Process already gone — clean up the zombie and return.
        try: proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired: pass
        return True

    # SIGTERM the group, then poll for exit.
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.05)
    # SIGKILL the group — survivors only.
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass
    try:
        proc.wait(timeout=1.0)
        return False  # we had to escalate
    except subprocess.TimeoutExpired:
        return False
