#!/usr/bin/env python3
"""capture_batch.py — launch several independent capture_session.py instances at once.

Each site is captured by its OWN Chromium instance, its OWN profile dir, and writes
its OWN WACZ — fully independent processes, nothing shared. This is the parallel
front-end to ``tools/capture_session.py``; it adds no capture or browser logic of its
own, it only orchestrates N copies of that tool. So everything the single tool
guarantees holds per instance: capture-time redaction is on, signing values are
masked, nothing is replayed or reconstructed. Posture is inherited unchanged.

Why separate profile dirs: Chromium locks a persistent profile to one process at a
time, so parallel instances cannot share one ``--profile-dir``. This mirrors the
project's own parallel-browser convention (``session_keeper`` uses a separate profile
dir per instance, "SEPARATE from worker profiles to avoid contention"). Each captured
site therefore gets ``<profile-root>/<name>`` as its own persistent profile — you log
in / set up autofill once per profile, and it persists for that site across runs.

Each instance still needs you to drive it (log in if prompted, press play, download,
press ENTER in that instance's window to save). The launcher opens them and then
reports which succeeded; it does not and cannot press ENTER for you — that is the
human-in-the-loop boundary the capture tool keeps.

Usage:
    # a small jobs file, one capture per line: name|url
    python3 tools/capture_batch.py --jobs jobs.txt \
        --out-dir ./captures --profile-root ~/.bd_profiles --autofill

    # or inline:
    python3 tools/capture_batch.py \
        --job alice https://site-a.com/v/alice \
        --job bob   https://site-b.com/v/bob \
        --out-dir ./captures --profile-root ~/.bd_profiles --autofill

A jobs-file line is ``name|url`` (``|`` separates; blank lines and ``#`` comments
ignored). ``name`` is used for the profile dir, the output WACZ name, AND as the
``--title`` for per-title URL memory, so a second batch run of the same names lands
each instance back on the same page it captured before.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Tuple

_ROOT = Path(__file__).resolve().parent.parent
_CAPTURE_CLI = Path(__file__).resolve().parent / "capture_session.py"


def _parse_jobs(args) -> List[Tuple[str, str]]:
    """Collect (name, url) jobs from --job pairs and/or a --jobs file."""
    jobs: List[Tuple[str, str]] = []
    for name, url in (args.job or []):
        jobs.append((name, url))
    if args.jobs:
        for raw in Path(args.jobs).read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "|" not in line:
                print(f"  skipping malformed jobs line (need name|url): {line!r}",
                      file=sys.stderr)
                continue
            name, _, url = line.partition("|")
            jobs.append((name.strip(), url.strip()))
    return jobs


def _expand_pairs(jobs: List[Tuple[str, str]], pairs: bool) -> List[Tuple[str, str]]:
    """With --pairs, capture each site TWICE for a diff pair: a site named
    ``bros`` becomes ``bros_run1`` then ``bros_run2`` (separate captures,
    separate WACZs, separate URL-memory titles). Two genuine human-driven
    sessions of the same site is exactly the right input for the C-T1 diff /
    temporal harness — the natural session-to-session variation is the signal.
    Without --pairs, jobs pass through unchanged."""
    if not pairs:
        return jobs
    expanded: List[Tuple[str, str]] = []
    for name, url in jobs:
        expanded.append((f"{name}_run1", url))
        expanded.append((f"{name}_run2", url))
    return expanded


def _safe_name(name: str) -> str:
    """Filesystem-safe profile/output stem."""
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in name)[:60] or "job"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Launch several independent capture_session.py instances in parallel")
    p.add_argument("--job", nargs=2, action="append", metavar=("NAME", "URL"),
                   help="A capture job: a logical NAME and the starting URL. "
                        "Repeatable.")
    p.add_argument("--jobs", default=None,
                   help="A jobs file, one 'name|url' per line (# comments ok).")
    p.add_argument("--out-dir", default="./captures",
                   help="Directory for the per-job .wacz files (default ./captures).")
    p.add_argument("--profile-root", default=None,
                   help="Parent dir for per-job persistent profiles. Each job gets "
                        "<profile-root>/<name> as its own profile (Chromium locks a "
                        "profile to one process, so parallel jobs need separate "
                        "profiles). Omit for throwaway browsers (no persistence).")
    p.add_argument("--seed-profile", default=None,
                   help="A profile dir you set up ONCE (install Proton Pass, import "
                        "your passwords, sign in). When a job's profile doesn't exist "
                        "yet, it's CLONED from this seed — so the vault, its imported "
                        "logins, and its signed-in session propagate to every job and "
                        "every new site. Set up once, clone everywhere. Requires "
                        "--profile-root. Existing job profiles are left as-is (not "
                        "re-cloned), so your per-site sessions persist between runs.")
    p.add_argument("--autofill", action="store_true",
                   help="Pass --autofill to each instance (native password autofill "
                        "in that job's profile). Only meaningful with --profile-root.")
    p.add_argument("--system-chrome", action="store_true",
                   help="Pass --system-chrome to each instance.")
    p.add_argument("--stagger-secs", type=float, default=1.5,
                   help="Seconds between launching each instance, so N browser "
                        "windows don't all spawn in the same instant (default 1.5).")
    p.add_argument("--url-memory-file", default="capture_url_memory.json",
                   help="Shared title->URL memory file passed to every instance "
                        "(default ./capture_url_memory.json). Page URLs only, "
                        "query-stripped.")
    p.add_argument("--sequential", action="store_true",
                   help="Run jobs one after another (each waits for the previous to "
                        "finish) instead of all at once. Use if your machine can't "
                        "drive many browser windows simultaneously.")
    p.add_argument("--pairs", action="store_true",
                   help="Capture each site TWICE (name_run1 then name_run2) to produce "
                        "the diff pair the C-T1/temporal pipeline wants. Combine with "
                        "--sequential for a hands-off chain: run1 opens, you drive + "
                        "press ENTER, run2 opens automatically, ENTER, next site's "
                        "run1, and so on.")
    return p


def _cmd_for(job_name: str, url: str, args, out_dir: Path) -> List[str]:
    safe = _safe_name(job_name)
    cmd = [sys.executable, str(_CAPTURE_CLI),
           "--url", url,
           "--out", str(out_dir / f"{safe}.wacz"),
           "--title", job_name,
           "--url-memory-file", args.url_memory_file]
    if args.profile_root:
        cmd += ["--profile-dir", str(Path(args.profile_root) / safe)]
    if args.autofill:
        cmd += ["--autofill"]
    if args.system_chrome:
        cmd += ["--system-chrome"]
    return cmd


def _seed_profile_if_new(job_name: str, args) -> None:
    """If --seed-profile is set and this job's profile doesn't exist yet, clone
    the seed into it. The seed is a profile you set up once (Proton Pass + your
    imported logins + signed in); cloning propagates the vault and its session to
    every job. Existing job profiles are left untouched, so per-site sessions
    persist between runs. Posture-neutral: this copies a browser profile dir; it
    does not read credentials or touch the capture/redaction path."""
    if not (args.seed_profile and args.profile_root):
        return
    import shutil
    dest = Path(args.profile_root) / _safe_name(job_name)
    if dest.exists():
        return  # already provisioned; keep its persisted session
    seed = Path(args.seed_profile)
    if not seed.is_dir():
        print(f"  WARNING: --seed-profile {seed} not found; "
              f"{job_name} will start with an empty profile", file=sys.stderr)
        return
    try:
        # copy2 metadata; dirs_exist_ok False since we checked dest is absent.
        shutil.copytree(seed, dest, symlinks=True)
        print(f"  seeded {job_name} profile from {seed.name}")
    except Exception as e:
        print(f"  WARNING: could not seed {job_name} profile "
              f"({str(e)[:80]}); it will start empty", file=sys.stderr)


def run(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    jobs = _expand_pairs(_parse_jobs(args), args.pairs)
    if not jobs:
        print("error: no jobs (use --job NAME URL or --jobs file.txt)",
              file=sys.stderr)
        return 2
    if not _CAPTURE_CLI.exists():
        print(f"error: capture CLI not found at {_CAPTURE_CLI}", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.profile_root:
        Path(args.profile_root).mkdir(parents=True, exist_ok=True)

    print(f"Launching {len(jobs)} capture instance(s) "
          f"({'sequential' if args.sequential else 'parallel'}). Each opens its own "
          f"browser — log in if prompted, play + download, then press ENTER in that "
          f"window to save.\n")

    # Sequential: start one, wait for it (operator drives + presses ENTER), then next.
    if args.sequential:
        results = []
        for name, url in jobs:
            print(f"--- {name}: {url}")
            _seed_profile_if_new(name, args)
            rc = subprocess.call(_cmd_for(name, url, args, out_dir))
            results.append((name, rc))
        return _report(results)

    # Parallel: start them all (staggered), then wait for the operator to finish each.
    procs = []
    for name, url in jobs:
        print(f"--- starting {name}: {url}")
        _seed_profile_if_new(name, args)
        p = subprocess.Popen(_cmd_for(name, url, args, out_dir))
        procs.append((name, p))
        time.sleep(max(0.0, args.stagger_secs))
    print(f"\nAll {len(procs)} instances launched. Drive each browser window and "
          f"press ENTER in it to save. Waiting for all to finish...\n")
    results = [(name, p.wait()) for name, p in procs]
    return _report(results)


def _report(results: List[Tuple[str, int]]) -> int:
    print("\n=== capture batch results ===")
    ok = 0
    for name, rc in results:
        # capture_session.py returns 0 ok, 1 digest error, 2 setup failure
        label = {0: "OK", 1: "DIGEST ERROR", 2: "SETUP FAILED"}.get(rc, f"rc={rc}")
        print(f"  {name}: {label}")
        ok += (rc == 0)
    print(f"  {ok}/{len(results)} succeeded")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(run())
