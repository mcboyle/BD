"""Test environment probes — detect whether the host running the suite
has a systemd bus, the bulkdownloader unit installed, and an Ollama
endpoint listening. Used to make environment-coupled tests assert the
correct branch for the host they are running on instead of failing
when run on a real deployment vs the sandbox.

Plain helper module (NOT a pytest fixture file) because the custom
run_tests.py runner only auto-injects three named conftest fixtures —
clean_workdir, fresh_app, aiassist_module — plus tmp_path/monkeypatch.
Other parameter names are not resolved; helper modules imported
explicitly by each test are the established alternative.

All probes are cached at import time: probing is cheap but not free
(subprocess + socket connect), and the host environment does not
change inside one test process.

v3.63.2 (T54): the systemd probes had a 2-second timeout that
fired under `--workers=4` load on a real deployment, causing
`HAS_BULKDOWNLOADER_UNIT` to cache False even when the unit was
installed. The downstream effect: tests in `test_u41_systemd_live_tests`
asserted the "no-unit" branch and failed against the live-check
results (which used a longer timeout and DID find the unit).
Fix: 8-second timeout + one retry on `TimeoutExpired` before
concluding the probe answer. Returns False only after both
attempts fail — same semantic as before, but with much more
forgiveness for a single transient stall.
"""
from __future__ import annotations

import shutil
import socket
import subprocess


# T54: longer timeout + single retry. The cost of a false negative
# (probe times out → tests assert wrong branch) is much higher than
# the cost of a slow probe (one extra subprocess at import time).
_SYSTEMD_PROBE_TIMEOUT_S = 8.0


def _run_with_retry(cmd):
    """Run `cmd` with the configured timeout. Returns the
    CompletedProcess on success, or None if both attempts time out.
    Any other Exception is treated as 'not available' (returns None).
    """
    for attempt in (1, 2):
        try:
            return subprocess.run(cmd, capture_output=True,
                                  timeout=_SYSTEMD_PROBE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            if attempt == 2:
                return None
            continue
        except Exception:
            return None
    return None


def _has_systemd_bus() -> bool:
    """True iff systemctl is on PATH AND the call returns rc==0. A
    non-zero rc means systemctl is present but cannot reach a bus
    (the common case in a container sandbox without --privileged).

    T54: now uses _run_with_retry; a transient first-call timeout
    no longer flips the answer to False for the rest of the run.
    """
    if shutil.which("systemctl") is None:
        return False
    p = _run_with_retry(
        ["systemctl", "--no-pager", "list-units",
         "--type=service", "--quiet"])
    return p is not None and p.returncode == 0


def _has_bulkdownloader_unit() -> bool:
    """True iff the bulkdownloader systemd unit is installed and
    visible to systemctl. Requires _has_systemd_bus() first.

    T54: uses _run_with_retry. The 2-second timeout previously
    fired on a deployment under `--workers=4` load, flipping the
    answer to False; the 5 test failures that motivated this fix
    all keyed off that single bad cache.
    """
    if not HAS_SYSTEMD_BUS:
        return False
    p = _run_with_retry(
        ["systemctl", "cat", "bulkdownloader",
         "--no-pager", "--quiet"])
    return p is not None and p.returncode == 0


def _ollama_listening(host: str = "localhost",
                      port: int = 11434,
                      timeout: float = 0.5) -> bool:
    """True iff something is accepting TCP connections at host:port.
    Cheap connect-and-close; does not speak HTTP. The sandbox has
    nothing on 11434; a deployment with the ollama systemd unit
    does."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


# Cached at import — host environment is constant for the test run
HAS_SYSTEMD_BUS = _has_systemd_bus()
HAS_BULKDOWNLOADER_UNIT = _has_bulkdownloader_unit()
HAS_OLLAMA_LOCAL = _ollama_listening()
