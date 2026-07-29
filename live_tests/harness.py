"""Shared harness for the BulkDownloader live-test runner.

A live test is a plain function registered with @live_test. The runner
calls it with a Context; the function logs via ctx.log(...) and returns
a (level, detail) tuple — level is PASS / WARN / FAIL. A raised
exception is recorded as FAIL.

Artifact contract — every test writes, under <results_dir> (default
live_tests/results/):

  <id>.log       full verbose log of the test — ALWAYS written,
                 overwritten each run.
  SUMMARY.txt    one line per test: "<LEVEL>  <id>  <detail>".
                 Appended each run, preceded by a run header.
  <id>.fail.txt  a focused failure report — written ONLY on FAIL,
                 removed if a previously-failing test now passes.

Exit code from run_all(): 0 if nothing FAILED, 1 otherwise — so the
runner doubles as a post-deploy gate.

Per-check hard timeout (T55, v3.63.2): each check runs in a daemon
thread and the harness gives it up to `per_check_timeout_s` (default
60) to return. If it doesn't, the harness records a FAIL with a
TIMEOUT detail and moves on to the next check. The wedged thread is
leaked rather than killed — Python has no safe way to kill a thread
holding native resources (Playwright, subprocess) — but it is a
daemon thread, so it dies with the process at run-end. This makes
a single wedged check (e.g. L3 hanging on a Chromium MV3 service
worker handshake) no longer wedge the whole run.

Nothing here runs at import time.
"""
from __future__ import annotations

import json
import platform
import shutil
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
# A fourth verdict: the check could not observe its subject on this host.
#
# Not a pass -- "nothing to look at" is not evidence the thing works. Not a
# warn -- a permanent, unclearable WARN is a false alarm, and CLAUDE.md
# section 0's inverse rule says over-sensitivity is a soundness bug, because a
# gate that cries wolf gets switched off and then the thing it guarded is
# unguarded. Never gates the deploy: run_all's exit code keys on FAIL alone.
NA = "N/A"
_LEVELS = (PASS, WARN, FAIL, NA)

# T55: default per-check hard timeout. Generous enough that the
# slowest legitimate check (small-download pipeline, ~30s on a busy
# host) has headroom, tight enough that a wedged Playwright handshake
# (L3 on a Chromium build without MV3 service worker support) doesn't
# stall the whole run.
DEFAULT_PER_CHECK_TIMEOUT_S = 60.0

# registry of LiveTest, in registration order
_REGISTRY: list = []


class LiveTest:
    """A registered live test."""

    def __init__(self, test_id, name, fn, disruptive=False):
        self.id = test_id
        self.name = name
        self.fn = fn
        self.disruptive = disruptive


def live_test(test_id, name, *, disruptive=False):
    """Decorator — register a function as a live test. Set
    disruptive=True for tests that reboot, kill processes, etc.; those
    are skipped unless the runner is given --include-disruptive."""
    def deco(fn):
        _REGISTRY.append(LiveTest(test_id, name, fn, disruptive))
        return fn
    return deco


def registry() -> list:
    """The registered live tests, in registration order."""
    return list(_REGISTRY)


class Context:
    """Passed to each live test. Carries the target URL and the
    deployment paths, accumulates a per-test log, and offers a
    read-only HTTP GET and a read-only DB connection."""

    def __init__(self, base_url, bd_home, *, disruptive=False):
        self.base_url = str(base_url).rstrip("/")
        self.bd_home = Path(bd_home)
        self.db_path = self.bd_home / "downloader_history.db"
        self.disruptive = disruptive
        self._log: list = []

    def log(self, msg):
        self._log.append(f"{time.strftime('%H:%M:%S')}  {msg}")

    @property
    def log_text(self) -> str:
        return "\n".join(self._log)

    def get(self, path, timeout=15):
        """Read-only HTTP GET. Returns (ok, status, body, elapsed_ms);
        body is parsed JSON when possible, else text."""
        url = self.base_url + path
        t0 = time.time()
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "replace")
                ms = (time.time() - t0) * 1000
                try:
                    return True, r.status, json.loads(raw), ms
                except ValueError:
                    return True, r.status, raw, ms
        except urllib.error.HTTPError as e:
            return False, e.code, None, (time.time() - t0) * 1000
        except Exception as e:
            return (False, None, f"{type(e).__name__}: {e}",
                    (time.time() - t0) * 1000)

    def ro_db(self):
        """Open the deployment DB strictly read-only — never touches
        the live database's state."""
        return sqlite3.connect(f"file:{self.db_path}?mode=ro",
                               uri=True, timeout=5)


def _results_dir(override=None) -> Path:
    if override is not None:
        return Path(override)
    return Path(__file__).resolve().parent / "results"


def _app_version(ctx) -> str:
    ok, _, body, _ = ctx.get("/api/health", timeout=5)
    if ok and isinstance(body, dict) and body.get("version"):
        return str(body["version"])
    return "?"


def _fail_report(test, ctx, detail, version) -> str:
    try:
        free = f"{shutil.disk_usage(ctx.bd_home).free / 1024 ** 3:.1f} GB"
    except Exception:
        free = "?"
    tail = "\n".join(ctx._log[-25:])
    return (
        f"FAIL  {test.id}  {test.name}\n"
        + "=" * 60 + "\n"
        f"what happened : {detail}\n"
        f"app version   : {version}\n"
        f"target        : {ctx.base_url}\n"
        f"bd_home       : {ctx.bd_home}\n"
        f"db_path       : {ctx.db_path}\n"
        f"os            : {platform.platform()}\n"
        f"disk free     : {free}\n"
        + "=" * 60 + "\n"
        f"log tail (last 25 lines):\n{tail}\n")


def _run_with_timeout(fn, ctx, timeout_s):
    """T55 — run `fn(ctx)` in a daemon thread, wait up to
    `timeout_s` for it to finish.

    Returns one of:
      ("done", (level, detail))      — fn returned normally
      ("exc", "<repr>")              — fn raised; repr is `type: msg`
      ("timeout", "<elapsed>s")      — fn did not finish in time

    On timeout the thread is LEAKED — Python has no safe way to kill
    a thread holding native resources (Playwright pages, ffmpeg
    subprocesses, socket reads). The thread is a daemon so it dies
    with the process at run-end; the harness can continue with the
    next check. This is strictly better than the pre-T55 behaviour
    where a wedged check (L3 on a Chromium-without-MV3 host) stalled
    the entire run forever.
    """
    box = {}

    def _runner():
        try:
            box["ret"] = fn(ctx)
        except BaseException as e:  # noqa: BLE001 — relay everything
            box["exc"] = f"{type(e).__name__}: {e}"

    th = threading.Thread(target=_runner, daemon=True,
                          name=f"live-test-{getattr(fn, '__name__', '?')}")
    t0 = time.time()
    th.start()
    th.join(timeout_s)
    elapsed = time.time() - t0
    if th.is_alive():
        return ("timeout", f"{elapsed:.1f}s")
    if "exc" in box:
        return ("exc", box["exc"])
    return ("done", box.get("ret"))


def run_all(base_url, bd_home, *, include_disruptive=False,
            only=None, results_dir=None,
            per_check_timeout_s=DEFAULT_PER_CHECK_TIMEOUT_S) -> int:
    """Run the registered live tests and write artifacts per the
    contract. `only` is an iterable of test IDs — when given, exactly
    those run (regardless of the disruptive flag, since naming an ID
    is explicit intent). Returns the process exit code: 0 if nothing
    FAILED, 1 otherwise."""
    rdir = _results_dir(results_dir)
    rdir.mkdir(parents=True, exist_ok=True)

    tests = registry()
    if only:
        wanted = set(only)
        tests = [t for t in tests if t.id in wanted]
    elif not include_disruptive:
        tests = [t for t in tests if not t.disruptive]

    version = _app_version(Context(base_url, bd_home))
    summary = rdir / "SUMMARY.txt"
    with summary.open("a", encoding="utf-8") as fh:
        fh.write(f"\n=== live-test run "
                 f"{time.strftime('%Y-%m-%d %H:%M:%S')} | "
                 f"host={platform.node()} | app={version} | "
                 f"target={str(base_url).rstrip('/')} ===\n")

    counts = {PASS: 0, WARN: 0, FAIL: 0, NA: 0}
    for t in tests:
        ctx = Context(base_url, bd_home, disruptive=include_disruptive)
        ctx.log(f"[{t.id}] {t.name} — start")
        # T55: hard-time-out each check. A wedged check (e.g. L3 on
        # a Chromium build without MV3 service worker support) used
        # to stall the entire run forever; now it gets recorded as
        # FAIL with a TIMEOUT detail and the harness moves on.
        outcome, payload = _run_with_timeout(t.fn, ctx,
                                              per_check_timeout_s)
        if outcome == "timeout":
            level, detail = FAIL, (
                f"TIMEOUT after {payload} (limit "
                f"{per_check_timeout_s:.1f}s) — the check did not "
                "return in time; thread leaked")
            ctx.log(f"TIMEOUT: {detail}")
        elif outcome == "exc":
            level, detail = FAIL, payload
            ctx.log(f"EXCEPTION: {detail}")
        else:
            res = payload
            if isinstance(res, tuple) and len(res) == 2:
                level, detail = res
            else:
                level, detail = FAIL, f"test returned {res!r}"
        if level not in _LEVELS:
            level, detail = FAIL, f"invalid level {level!r} from test"
        ctx.log(f"[{t.id}] result: {level} — {detail}")
        counts[level] += 1
        print(f"  [{level}]  {t.id}  {t.name}  —  {detail}")

        (rdir / f"{t.id}.log").write_text(ctx.log_text + "\n",
                                          encoding="utf-8")
        with summary.open("a", encoding="utf-8") as fh:
            fh.write(f"{level}  {t.id}  {detail}\n")
        fail_file = rdir / f"{t.id}.fail.txt"
        if level == FAIL:
            fail_file.write_text(
                _fail_report(t, ctx, detail, version), encoding="utf-8")
        elif fail_file.exists():
            fail_file.unlink()  # clear a stale failure from a prior run

    # The n/a bucket is printed only when non-zero, so a run without any
    # unobservable checks emits the historical summary shape verbatim and every
    # archived bundle keeps parsing.
    _na = f"| {counts[NA]} n/a " if counts[NA] else ""
    print(f"  {counts[PASS]} pass | {counts[WARN]} warn | "
          f"{counts[FAIL]} fail {_na} ({len(tests)} run)")
    return 1 if counts[FAIL] else 0
