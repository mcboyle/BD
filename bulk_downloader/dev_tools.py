"""Dev tools — in-GUI test runner + diagnostics (Phase 200).

DEV-ONLY surface, gated by the `BD_DEV_MODE=1` environment variable.
Without that flag, every endpoint here returns 404. This keeps the
surface from ever leaking into a production / shared install.

Capabilities:
  • List all test files + test classes/functions discovered from
    tests/
  • Run a single test file, class, or function and stream output
  • Run the full preflight check
  • Run the selftest
  • Show recent runs + their pass/fail/duration

The runner shells out to `run_tests.py` rather than importing pytest
or unittest directly, because that's what the project's existing
harness uses and it's the source of truth for skip-vs-fail semantics.
"""
from __future__ import annotations

import ast
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional


# Module-level state for run history. Bounded — never grows past 50.
_runs_lock = threading.RLock()
_runs: list = []  # [{run_id, target, state, output, started, finished, returncode}]
_MAX_HISTORY = 50
_TEST_RUN_CAP_ENV = "TEST_RUN_CAP_SECONDS"
# Match the existing extracted-release-suite ceiling; this is a whole-run bound,
# not a claim about any individual test's expected duration.
_DEFAULT_TEST_RUN_CAP_SECONDS = 60 * 60


def _outer_capped_command(command: list[str]) -> list[str]:
    """Put the test runner behind the same outer cap as shell launchers.

    The cap belongs outside ``run_tests.py``: an inner test timeout cannot fire
    once its owning runner is wedged.  Refuse on platforms without the coreutils
    primitive rather than silently launching the unbounded fallback.
    """
    raw = os.environ.get(_TEST_RUN_CAP_ENV, str(_DEFAULT_TEST_RUN_CAP_SECONDS))
    try:
        seconds = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"{_TEST_RUN_CAP_ENV} must be a positive whole number, got {raw!r}"
        ) from exc
    if seconds <= 0:
        raise RuntimeError(
            f"{_TEST_RUN_CAP_ENV} must be a positive whole number, got {raw!r}"
        )
    timeout = shutil.which("timeout")
    if timeout is None:
        raise RuntimeError(
            "refusing an unbounded test run: coreutils timeout is unavailable"
        )
    return [timeout, "--kill-after=10", str(seconds), *command]


def _public_run(run: dict) -> dict:
    """Return the JSON-safe lifecycle record, excluding held OS handles."""
    return {key: value for key, value in run.items()
            if not key.startswith("_")}


def _close_run_identity(run: dict) -> None:
    """Release a held pidfd exactly once. Caller holds ``_runs_lock``."""
    pidfd = run.pop("_pidfd", None)
    run.pop("_process", None)
    if pidfd is not None:
        try:
            os.close(pidfd)
        except OSError:
            pass


def _settle_completed_run(run: dict, proc: subprocess.Popen) -> bool:
    """Publish a process verdict without overwriting another terminal owner."""
    returncode = proc.poll()
    if returncode is None:
        return False
    run["returncode"] = returncode
    if run["state"] == "running":
        run["state"] = "done" if returncode == 0 else "failed"
        run["finished"] = time.time()
    _close_run_identity(run)
    return True


def is_dev_mode() -> bool:
    """Source of truth. Every endpoint checks this first.

    v3.47.7: dev surface is now part of the normal install — users can
    see logs, run tests, and download diagnostic bundles from the GUI
    without any env-var setup. Was previously gated behind BD_DEV_MODE=1
    (INV-009) on the rationale that /api/dev/run spawns Python
    subprocesses, but in the single-user-LAN threat model the user is
    already the only party that can hit those endpoints.

    SECURITY NOTE: if you ever expose this server to an untrusted
    network, set BD_DEV_MODE_DISABLE=1 to re-gate. The check below
    treats that env var as a kill-switch — the normal default is
    always-on. We do NOT use BD_HOST=0.0.0.0 as a signal because that
    is the documented normal binding for LAN access.

    v3.66.317 (CLI->GUI parity): BD_DEV_MODE is now a live GUI control.
    The BD_DEV_MODE_DISABLE kill-switch STILL WINS (hard off — unchanged
    contract). Otherwise resolve store > env > default-ON: an explicit
    "0"/off (from the global_config store or the env seed) disables the
    in-GUI dev surface; unset preserves the v3.47.7 always-on default. A
    GUI write to `dev_mode` takes effect on the next request (no restart).
    """
    if os.environ.get("BD_DEV_MODE_DISABLE", "0").strip() == "1":
        return False
    pref = ""
    try:
        from . import global_config as _gc
        pref = str(_gc.get("dev_mode", "") or "").strip()
    except Exception:
        pref = ""
    if not pref:
        pref = os.environ.get("BD_DEV_MODE", "").strip()
    if pref.lower() in ("0", "false", "off", "no"):
        return False
    return True


def _tests_dir() -> Path:
    """Locate the tests directory relative to this module. The package
    lives at <root>/bulk_downloader/, tests at <root>/tests/."""
    return Path(__file__).resolve().parent.parent / "tests"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


# ─── Test discovery ────────────────────────────────────────────────────


def discover_tests() -> dict:
    """Walk tests/ directory, parse each file's AST to extract test
    classes + functions without executing them. Returns:

        {
          files: [
            {path, name, classes: [...], functions: [...], total_tests}
          ],
          summary: {file_count, test_count}
        }
    """
    tests_dir = _tests_dir()
    if not tests_dir.is_dir():
        return {"files": [], "summary": {"file_count": 0,
                                          "test_count": 0},
                "error": f"tests/ not found at {tests_dir}"}

    files = []
    total_tests = 0
    for path in sorted(tests_dir.glob("test_*.py")):
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src, filename=str(path))
        except (OSError, SyntaxError):
            continue

        classes = []
        functions = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                methods = []
                for sub in ast.iter_child_nodes(node):
                    if isinstance(sub, (ast.FunctionDef,
                                         ast.AsyncFunctionDef)):
                        if sub.name.startswith("test_"):
                            methods.append({
                                "name": sub.name,
                                "lineno": sub.lineno,
                            })
                if methods:
                    classes.append({
                        "name": node.name,
                        "lineno": node.lineno,
                        "tests": methods,
                    })
                    total_tests += len(methods)
            elif isinstance(node, (ast.FunctionDef,
                                    ast.AsyncFunctionDef)):
                if node.name.startswith("test_"):
                    functions.append({
                        "name": node.name,
                        "lineno": node.lineno,
                    })
                    total_tests += 1

        file_test_count = sum(len(c["tests"]) for c in classes) + \
                          len(functions)
        files.append({
            "path": str(path.relative_to(_repo_root())),
            "name": path.name,
            "classes": classes,
            "functions": functions,
            "total_tests": file_test_count,
        })

    return {
        "files": files,
        "summary": {
            "file_count": len(files),
            "test_count": total_tests,
        },
    }


# ─── Test execution ────────────────────────────────────────────────────


def _trim_history():
    """Keep history bounded — drop oldest when over limit."""
    global _runs
    if len(_runs) > _MAX_HISTORY:
        _runs = _runs[-_MAX_HISTORY:]


def start_run(target: str, *, kind: str = "file") -> dict:
    """Kick off a test run in a background thread. Returns the run_id
    immediately. Caller polls /api/dev/runs/<run_id> for status.

    target can be:
      • a file path relative to repo root (e.g. "tests/test_phases_195_199.py")
      • "all" — runs the entire suite via run_tests.py
      • "preflight" — runs preflight.py
      • "selftest" — runs the in-app selftest module

    The threading model keeps the test running across HTTP request
    boundaries. Output is captured + appended to the run record so
    polling sees incremental progress."""

    run_id = uuid.uuid4().hex[:12]
    run = {
        "run_id": run_id,
        "target": target,
        "kind": kind,
        "state": "running",
        "output": "",
        "started": time.time(),
        "finished": None,
        "returncode": None,
        "pid": None,
        "_process": None,
        "_pidfd": None,
    }
    with _runs_lock:
        _runs.append(run)
        _trim_history()

    def _worker():
        try:
            cmd = _build_cmd(target, kind)
            if cmd is None:
                with _runs_lock:
                    run["state"] = "error"
                    run["output"] = (
                        f"unknown target {target!r} for kind {kind!r}")
                    run["finished"] = time.time()
                    run["returncode"] = -1
                return

            env = os.environ.copy()
            env.pop("BD_INSTALL_DIR", None)
            env["BD_DISABLE_KEEPALIVE"] = "1"
            env["PYTHONUNBUFFERED"] = "1"

            # v3.47.8 (port from unrud/video-downloader): isolate the test
            # runner process group so a cancel from the UI cleans up any
            # pytest sub-processes, not just the top-level python invocation.
            # Particularly relevant when tests themselves spawn Playwright
            # workers — without process-group isolation, cancelling a run
            # leaves orphan Chromium processes consuming RAM until reboot.
            _pgrp = ({"start_new_session": True}
                     if os.name != "nt" else
                     {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP})
            proc = subprocess.Popen(
                _outer_capped_command(cmd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(_repo_root()),
                env=env,
                text=True,
                bufsize=1,
                **_pgrp,
            )
            pidfd = None
            if os.name != "nt":
                opener = getattr(os, "pidfd_open", None)
                if opener is not None:
                    try:
                        pidfd = opener(proc.pid, 0)
                    except OSError:
                        pidfd = None
            with _runs_lock:
                run["pid"] = proc.pid
                run["_process"] = proc
                run["_pidfd"] = pidfd

            # Stream output to the run record line-by-line so the UI's
            # polling sees progress without waiting for the whole run.
            buf = []
            for line in proc.stdout:
                buf.append(line)
                # Trim the buffer if it gets crazy (some tests dump
                # massive traces). Cap at ~1MB.
                if sum(len(b) for b in buf) > 1_000_000:
                    with _runs_lock:
                        run["output"] = (
                            "[output truncated to 1MB] ...\n" +
                            "".join(buf[-500:]))
                    continue
                with _runs_lock:
                    run["output"] = "".join(buf)
            proc.wait()
            with _runs_lock:
                _settle_completed_run(run, proc)
        except Exception as e:
            with _runs_lock:
                if run["state"] == "running":
                    run["state"] = "error"
                    run["finished"] = time.time()
                run["output"] = (run.get("output") or "") + \
                                 f"\n[worker error] {type(e).__name__}: {e}"
                if run["returncode"] is None:
                    run["returncode"] = -1
                _close_run_identity(run)

    t = threading.Thread(target=_worker, daemon=True,
                          name=f"dev-run-{run_id}")
    t.start()
    return {"ok": True, "run_id": run_id, "target": target}


def _build_cmd(target: str, kind: str) -> Optional[list]:
    """Build the subprocess command for the given target."""
    py = sys.executable  # use the same python interpreter the app runs on
    root = _repo_root()
    if kind == "preflight":
        return [py, str(root / "preflight.py")]
    if kind == "selftest":
        return [py, "-c",
                "import json; "
                "from bulk_downloader.db import DB_PATH; "
                "from bulk_downloader import selftest as st; "
                "r = st.run_all(db_path=str(DB_PATH)); "
                "print(json.dumps(r, indent=2, default=str))"]
    if kind == "all":
        return [py, str(root / "run_tests.py")]
    if kind == "file":
        # Whitelist: target must be under tests/, and must end in .py
        p = (root / target).resolve()
        try:
            p.relative_to(root / "tests")
        except ValueError:
            return None
        if not p.is_file() or p.suffix != ".py":
            return None
        return [py, str(root / "run_tests.py"),
                str(p.relative_to(root))]
    if kind == "class" or kind == "function":
        # Format: "tests/test_foo.py::TestBar" or "tests/test_foo.py::TestBar::test_baz"
        # We pass to run_tests.py as-is; it understands pytest-style ids.
        if "::" not in target:
            return None
        file_part = target.split("::")[0]
        p = (root / file_part).resolve()
        try:
            p.relative_to(root / "tests")
        except ValueError:
            return None
        if not p.is_file():
            return None
        return [py, str(root / "run_tests.py"), target]
    return None


def run_status(run_id: str) -> Optional[dict]:
    """Get current state of a run. Returns None if unknown."""
    with _runs_lock:
        for r in _runs:
            if r["run_id"] == run_id:
                return _public_run(r)
    return None


def cancel_run(run_id: str) -> dict:
    """Cancel only the exact process identity retained from launch."""
    with _runs_lock:
        run = next((r for r in _runs if r["run_id"] == run_id), None)
        if not run:
            return {"ok": False, "error": "unknown run_id"}
        if run["state"] != "running":
            return {"ok": False, "error": f"not running (state={run['state']})"}
        proc = run.get("_process")
        if proc is None:
            return {"ok": False,
                    "error": "no process identity yet — try again in a moment"}
        if _settle_completed_run(run, proc):
            return {"ok": False,
                    "error": f"not running (state={run['state']})"}
        try:
            if os.name == "nt":
                # Popen retains the non-reusable Windows process handle.
                proc.terminate()
            else:
                pidfd = run.get("_pidfd")
                sender = getattr(signal, "pidfd_send_signal", None)
                if pidfd is None or sender is None:
                    return {"ok": False,
                            "error": "process identity unavailable; refusing "
                                     "numeric-PID signal"}
                sender(pidfd, signal.SIGTERM)
        except ProcessLookupError:
            _settle_completed_run(run, proc)
            return {"ok": False, "error": "process exited before cancellation"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}
        # The signal seam may synchronously publish a worker verdict. Never let
        # cancellation replace a terminal state it did not win.
        if run["state"] != "running":
            _close_run_identity(run)
            return {"ok": False,
                    "error": f"not running (state={run['state']})"}
        run["state"] = "cancelled"
        run["finished"] = time.time()
        _close_run_identity(run)
        return {"ok": True}


def recent_runs(*, limit: int = 20) -> list:
    """Most-recent runs first."""
    with _runs_lock:
        return [_public_run(run) for run in reversed(_runs[:])][:limit]


def parse_summary(output: str) -> dict:
    """Extract pass/fail/skip counts from run_tests.py output. The
    runner emits a footer like:
        Total: 3016 | Passed: 3013 | Failed: 0 | Skipped: 3
    Returns the numbers or {} if no match."""
    import re
    m = re.search(
        r"Total:\s*(\d+)\s*\|\s*Passed:\s*(\d+)\s*\|"
        r"\s*Failed:\s*(\d+)\s*\|\s*Skipped:\s*(\d+)",
        output or "")
    if not m:
        return {}
    return {
        "total": int(m.group(1)),
        "passed": int(m.group(2)),
        "failed": int(m.group(3)),
        "skipped": int(m.group(4)),
    }
