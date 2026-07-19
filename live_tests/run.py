"""Live-test runner — run the BulkDownloader live tests against the
RUNNING app on the deployment host.

    python3 live_tests/run.py --url http://stash:5555
    python3 live_tests/run.py --url http://localhost:5555 --include-disruptive
    python3 live_tests/run.py --only L22,L26

This exercises the live process — it is NOT the unit suite (which
runs against in-memory fixtures via `run_tests.py`). Artifacts land
in live_tests/results/ (per-test .log, SUMMARY.txt, and a .fail.txt
for any failure). Exit code is 0 if nothing FAILED, 1 otherwise — so
it doubles as a post-deploy gate.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# allow `python3 live_tests/run.py` from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from live_tests import checks  # noqa: F401  (import registers the checks)
from live_tests import harness


def _resolve_bd_home(cli_arg, base_url):
    """Pick the deployment directory holding downloader_history.db.

    Order, first hit wins:
      1. --bd-home CLI arg (operator override, always wins)
      2. BD_HOME env var in the runner's environment
      3. Running app's /api/dev/env env.BD_HOME (the canonical source)
      4. Running app's /api/dev/proc cwd (where the process actually runs)
      5. Runner's os.getcwd() (last-resort fallback, warned)

    Pre-v3.64.6 the default was just `BD_HOME or os.getcwd()`, which
    silently broke when the runner was invoked from a tempdir (Windows
    pre-deploy verifier did this — L20/L21 looked for the DB at
    `C:\\...\\Temp\\<tmp>\\downloader_history.db` and WARN'd
    `INV-004 not testable here`). Asking the running app removes the
    coupling to the runner's cwd.

    Steps 3 and 4 are best-effort: dev-mode-disabled deployments
    answer 4xx and we fall through. Each step logs which one matched
    so a future debug pass can tell at a glance.
    """
    # 1. explicit CLI arg
    if cli_arg:
        print(f"  bd-home source: --bd-home arg = {cli_arg}")
        return cli_arg
    # 2. BD_HOME env var
    env_val = os.environ.get("BD_HOME")
    if env_val:
        print(f"  bd-home source: BD_HOME env = {env_val}")
        return env_val
    # 3. running app's /api/dev/env -> env.BD_HOME
    try:
        url = base_url.rstrip("/") + "/api/dev/env"
        with urllib.request.urlopen(url, timeout=5) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
        env_block = body.get("env") if isinstance(body, dict) else None
        app_bd_home = (env_block or {}).get("BD_HOME")
        if app_bd_home:
            print(f"  bd-home source: app /api/dev/env BD_HOME = "
                  f"{app_bd_home}")
            return app_bd_home
    except (urllib.error.HTTPError, urllib.error.URLError,
            ValueError, OSError):
        pass
    # 4. running app's /api/dev/proc -> cwd of the running process
    try:
        url = base_url.rstrip("/") + "/api/dev/proc"
        with urllib.request.urlopen(url, timeout=5) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
        app_cwd = body.get("cwd") if isinstance(body, dict) else None
        if app_cwd:
            print(f"  bd-home source: app /api/dev/proc cwd = {app_cwd}")
            return app_cwd
    except (urllib.error.HTTPError, urllib.error.URLError,
            ValueError, OSError):
        pass
    # 5. last resort — runner's cwd, with a clear warning so a
    # tempdir-cwd misconfiguration is visible instead of silent.
    fallback = os.getcwd()
    print(f"  bd-home source: runner cwd (WARN: could not reach the "
          f"running app at {base_url}; if L20/L21/L22 WARN that the "
          f"DB is not at {os.path.join(fallback, 'downloader_history.db')}, "
          f"pass --bd-home or set BD_HOME) = {fallback}")
    return fallback


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="BulkDownloader live (online) tests")
    ap.add_argument("--url", default="http://localhost:5555",
                    help="base URL of the running app")
    ap.add_argument("--bd-home", default=None,
                    help=("deployment dir holding downloader_history.db. "
                          "When omitted, resolved from (in order): "
                          "BD_HOME env var, the running app's "
                          "/api/dev/env BD_HOME, the running app's "
                          "/api/dev/proc cwd, finally the runner's cwd."))
    ap.add_argument("--include-disruptive", action="store_true",
                    help="also run disruptive tests (reboot, kill, ...)")
    ap.add_argument("--only", default="",
                    help="comma-separated test IDs, e.g. L22,L26")
    ap.add_argument("--per-check-timeout", type=float,
                    default=harness.DEFAULT_PER_CHECK_TIMEOUT_S,
                    help=("hard timeout per check in seconds (T55, "
                          f"default {harness.DEFAULT_PER_CHECK_TIMEOUT_S}). "
                          "A check that doesn't return in time is recorded "
                          "as FAIL with a TIMEOUT detail and the harness "
                          "continues."))
    args = ap.parse_args(argv)

    only = [s.strip() for s in args.only.split(",") if s.strip()]
    registered = harness.registry()

    bd_home = _resolve_bd_home(args.bd_home, args.url)

    print("=" * 64)
    print("BulkDownloader live tests")
    print(f"target : {args.url}")
    print(f"bd_home: {bd_home}")
    print(f"tests  : {len(registered)} registered"
          + (f"; running only {only}" if only else ""))
    print(f"per-check timeout: {args.per_check_timeout:.0f}s")
    print("=" * 64)

    code = harness.run_all(args.url, bd_home,
                           include_disruptive=args.include_disruptive,
                           only=only or None,
                           per_check_timeout_s=args.per_check_timeout)

    rdir = Path(harness.__file__).resolve().parent / "results"
    print("=" * 64)
    print(f"  artifacts: {rdir}")
    print(f"  exit code: {code} "
          + ("(all clear)" if code == 0 else "(something FAILED)"))
    print("=" * 64)
    return code


if __name__ == "__main__":
    sys.exit(main())
