#!/usr/bin/env python3
"""D2 diagnostic — Windows fresh-`BD_HOME` first-boot `/` asymmetry.

The v3.62.7 CSRF tests fail under `--workers` on Windows: the parallel
runner gives each test file a fresh `BD_HOME` tempdir, and the app's
first-boot init under that fresh `BD_HOME` produces a `/` response
whose CSRF meta tag the three CSRF tests can't match. On Linux the
same swap is invisible. The v3.63.7 fix pinned the CSRF test file so
it runs in-process before parallel dispatch with no forced `BD_HOME`,
which papers over the symptom but leaves the underlying asymmetry
undiagnosed.

The sibling CSRF-bootstrap diagnostic (v3.62.7) booted the app with
`os.environ` as-is and did NOT mimic the parallel-runner env
(cwd=tempdir, BD_HOME=tempdir, fresh process). This diagnostic does.
That sibling was retired at v3.66.824: its whole premise was the
`templates/index.html` contract deleted at v3.66.334, so it could not
fail. Do not restore it -- what it covered that mattered (package
resolution) is measured below.

Run on the Windows VM from ~/BulkDownloader:

    venv\\Scripts\\python.exe tools\\diag_d2_fresh_bd_home.py

Or run the existing CSRF diagnostic side-by-side for comparison —
this script's "control" path matches what that one does, plus a
"parallel-like" path that mimics the worker.

The diagnostic does NOT mutate any persistent state. It writes only
to a TemporaryDirectory that's cleaned up on exit.

Exit codes:
    0  diagnostic completed (regardless of finding)
    2  could not even import bulk_downloader.app
"""

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path


def main() -> int:
    print("=" * 72)
    print("D2 diagnostic: Windows fresh-BD_HOME first-boot / asymmetry")
    print("=" * 72)
    print(f"sys.executable     : {sys.executable}")
    print(f"sys.version        : {sys.version.splitlines()[0]}")
    print(f"platform           : {sys.platform}")
    print(f"cwd at start       : {os.getcwd()}")

    # Resolve repo root from this file's location.
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root))
    print(f"repo_root          : {repo_root}")

    # --- Path A: control. Just hit / with whatever env we inherited.
    print()
    print("-" * 72)
    print("Path A — CONTROL: import + /, no BD_HOME swap, no cwd change")
    print("-" * 72)
    control = _probe_one(repo_root, swap_env=False)
    _print_probe(control)

    # --- Path B: mimic parallel runner. Fresh BD_HOME tempdir + cwd swap.
    print()
    print("-" * 72)
    print("Path B — PARALLEL-LIKE: BD_HOME=<tempdir>, cwd=<tempdir>")
    print("-" * 72)
    with tempfile.TemporaryDirectory(prefix="d2_bd_home_") as td:
        td_path = Path(td)
        parallel = _probe_one(repo_root, swap_env=True, td=td_path)
        _print_probe(parallel)

    # --- Diff
    print()
    print("-" * 72)
    print("DIFF — what's different between control and parallel")
    print("-" * 72)
    _diff_probes(control, parallel)

    print()
    print("=" * 72)
    print("END D2 diagnostic")
    print("=" * 72)
    return 0


def _probe_one(repo_root: Path, *, swap_env: bool, td: Path | None = None) -> dict:
    """Boot the app fresh and hit /. Optionally with cwd+BD_HOME
    swapped to a tempdir to mimic the parallel runner.

    Each call uses a SUBPROCESS to guarantee no module-state leak
    between paths. The subprocess writes its findings as JSON to
    stdout."""
    import subprocess
    payload = _SUBPROCESS_SCRIPT
    env = dict(os.environ)
    env["BD_DISABLE_KEEPALIVE"] = "1"
    env["BD_TEST_MODE"] = "1"
    cwd = None
    if swap_env and td is not None:
        env["BD_HOME"] = str(td)
        cwd = str(td)

    r = subprocess.run(
        [sys.executable, "-c", payload],
        env=env, cwd=cwd,
        capture_output=True, text=True, timeout=60,
    )
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    # The subprocess emits ONE JSON line on stdout, optionally preceded
    # by app boot messages (vpn-api banner, etc.) we need to skip.
    parsed = None
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                parsed = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if parsed is None:
        return {
            "ok": False,
            "error": "could not parse subprocess output",
            "stdout_tail": out[-400:],
            "stderr_tail": err[-400:],
            "returncode": r.returncode,
        }
    parsed["returncode"] = r.returncode
    if err:
        parsed["stderr_tail"] = err[-400:]
    return parsed


# Runs in a SUBPROCESS — must be self-contained.
_SUBPROCESS_SCRIPT = r"""
import os, sys, json, hashlib
from pathlib import Path

result = {"ok": True}
result["cwd"] = os.getcwd()
result["bd_home_env"] = os.environ.get("BD_HOME", "")
result["sys_path_head"] = sys.path[:3]

# Locate the repo: this subprocess inherits sys.path but not
# necessarily the test-runner additions. Walk up from cwd until we
# find a bulk_downloader/ sibling, falling back to env.BD_REPO.
def _find_repo():
    # 1. BD_REPO from the parent process always wins if set.
    bd_repo = os.environ.get("BD_REPO")
    if bd_repo and (Path(bd_repo) / "bulk_downloader" / "__init__.py").is_file():
        return Path(bd_repo)
    # 2. Walk up from cwd — works for the "control" path where cwd is
    #    the repo, fails for the "parallel-like" path where cwd is a
    #    tempdir (which is why BD_REPO is the primary mechanism).
    p = Path(os.getcwd())
    for _ in range(6):
        if (p / "bulk_downloader" / "__init__.py").is_file():
            return p
        p = p.parent
    return None

# The parent process exports BD_REPO so the subprocess can find the
# bulk_downloader package even when cwd=tempdir.
repo = _find_repo()
if repo is None:
    # Search PYTHONPATH-style
    for p in sys.path:
        if (Path(p) / "bulk_downloader" / "__init__.py").is_file():
            repo = Path(p); break
result["repo"] = str(repo) if repo else None
if repo:
    sys.path.insert(0, str(repo))

try:
    import bulk_downloader
    result["pkg_file"] = bulk_downloader.__file__
    result["pkg_version"] = bulk_downloader.__version__
    from bulk_downloader import app as bd_app
    result["app_file"] = bd_app.__file__
    # No template probe: bulk_downloader/templates/ went at v3.66.334, so
    # template_exists / template_has_marker were structural constants and the
    # sha/bytes lines they guarded were unreachable.

    # init the DB the same way conftest fresh_app does
    from bulk_downloader.db import db_init
    db_init()
    bd_app.app.config["TESTING"] = True

    # the actual probe
    client = bd_app.app.test_client()
    resp = client.get("/")
    body = resp.get_data(as_text=True)
    result["status"] = resp.status_code
    result["body_len"] = len(body)
    result["body_sha256"] = hashlib.sha256(body.encode("utf-8", "replace")).hexdigest()
    # No meta-tag or token-value probe: / is now the installer 503 or the
    # static frontend/dist/index.html, neither of which can carry the retired
    # csrf meta tag (see tests/test_cut35_csrf_meta_premise_retired_in_tools.py
    # for the literal and the reachability measurement). The token-value field
    # in particular would have written a live CSRF token into a diagnostic that
    # gets shipped. The literal is deliberately not repeated here: this file is
    # inside that gate's denominator, and naming a probe is how it comes back.
    set_cookies = [v for k, v in resp.headers.items()
                   if k.lower() == "set-cookie"]
    result["set_cookies_count"] = len(set_cookies)
    result["set_cookies_have_bd_session"] = any(
        "bd_session=" in c for c in set_cookies)
    # head of response
    head_end = body.find("</head>")
    if head_end > 0:
        result["head_snippet"] = body[:head_end + 7][:1200]
    else:
        result["head_snippet"] = body[:1200]
except Exception as e:
    result["ok"] = False
    result["error"] = f"{type(e).__name__}: {e}"
    import traceback
    result["traceback_tail"] = traceback.format_exc()[-800:]

print(json.dumps(result))
"""


def _print_probe(p: dict) -> None:
    if not p.get("ok"):
        print(f"  FAIL: {p.get('error', '?')}")
        if "stdout_tail" in p:
            print(f"  stdout tail: {p['stdout_tail']!r}")
        if "stderr_tail" in p:
            print(f"  stderr tail: {p['stderr_tail']!r}")
        if "traceback_tail" in p:
            print(f"  trace tail: {p['traceback_tail']}")
        return
    print(f"  cwd                 : {p.get('cwd')}")
    print(f"  BD_HOME env         : {p.get('bd_home_env') or '(unset)'}")
    print(f"  repo found          : {p.get('repo')}")
    print(f"  pkg version         : {p.get('pkg_version')}")
    print(f"  pkg file            : {p.get('pkg_file')}")
    print(f"  GET / status        : {p.get('status')}")
    print(f"  body length         : {p.get('body_len')}")
    print(f"  body sha256         : {(p.get('body_sha256') or '')[:16]}...")
    print(f"  Set-Cookie count    : {p.get('set_cookies_count')}")
    print(f"  has bd_session cookie: {p.get('set_cookies_have_bd_session')}")


def _diff_probes(a: dict, b: dict) -> None:
    if not a.get("ok") or not b.get("ok"):
        print("  one or both probes failed; see traces above")
        return
    # body_sha256 IS structural now. It used to be excluded as "expected to
    # differ (random CSRF token in the body)" -- but the Jinja shell that
    # injected that token went at v3.66.334, and / is now the installer 503 or
    # the static frontend/dist/index.html, both byte-identical between the two
    # paths. Keeping the exclusion meant a genuine body difference -- exactly
    # the D2 asymmetry this tool exists to find -- was filed as cosmetic.
    structural = [
        "status", "body_len", "body_sha256",
        "set_cookies_count",
        "set_cookies_have_bd_session",
        "pkg_file", "app_file",
    ]
    structural_diffs = []
    for k in structural:
        av, bv = a.get(k), b.get(k)
        if av != bv:
            structural_diffs.append((k, av, bv))

    # Nothing is expected-to-differ any more, so this stays empty unless a
    # future field is deliberately added to it.
    cosmetic_diffs: list[str] = []

    if structural_diffs:
        for k, av, bv in structural_diffs:
            print(f"  {k:24s}  control={av!r}  parallel={bv!r}")
        print()
        print("  ^ STRUCTURAL DIFF detected. This is the D2 asymmetry.")
        print("  Compare the rendered <head> from both paths:")
        print()
        print("  --- CONTROL head_snippet (first 800 chars) ---")
        print((a.get("head_snippet") or "")[:800])
        print()
        print("  --- PARALLEL head_snippet (first 800 chars) ---")
        print((b.get("head_snippet") or "")[:800])
    else:
        print("  No structural difference between control and parallel.")
        if cosmetic_diffs:
            print(f"  (Cosmetic: {cosmetic_diffs} differ — expected, "
                  "they're per-session random.)")
        print()
        print("  If the CSRF tests still fail under --workers on this")
        print("  host, the cause is NOT in the / response itself — look")
        print("  at test-code session handling or fixture order. The")
        print("  papered-over symptom may have been a different issue.")


if __name__ == "__main__":
    try:
        # Export the repo path so the subprocess can find bulk_downloader/
        # even when cwd=tempdir.
        repo_root = Path(__file__).resolve().parent.parent
        os.environ["BD_REPO"] = str(repo_root)
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        sys.exit(130)
