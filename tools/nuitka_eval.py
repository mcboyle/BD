"""nuitka_eval -- size / startup / correctness eval for a Nuitka standalone dist.

MOD-7 cut 2. The binding evaluation is a full --standalone compile measured on a
BUILD HOST (the compile is several GB transient + 30-90 min -- out of a sandbox's
disk and exec budget), exactly the instrument-here / measure-on-the-real-host
split capture.sh uses. This module is the instrument:

  measure_size / human_size   -- roll up the dist size (None on a missing dist,
                                 never a misleading 0)
  startup_command / _env      -- launch the compiled binary on a chosen port
  correctness_probes          -- the SPA + a real API must answer, not just boot
  verdict                     -- adopt vs retain-pyinstaller against a baseline;
                                 correctness is a HARD gate; parity retains
  run_eval                    -- boot + measure + probe a real dist (build host);
                                 with no dist it returns UNKNOWN, never a false PASS
  runbook                     -- the exact build-host procedure + acceptance bar

Reuses tools.build_nuitka / tools.packaging_config so the compile command and the
eval read one source of truth.

Build host:
  python -m tools.build_nuitka --mode standalone --output-dir dist_nuitka --run
  python -m tools.nuitka_eval --dist dist_nuitka/downloader_ui.dist --json
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

# --------------------------------------------------------------------------
# PyInstaller baseline -- the thing Nuitka must BEAT to be worth adopting.
#
# BD ships via PyInstaller today; there is no recorded BD binary size in-tree,
# so these are a DECLARED REFERENCE, not a measurement. The runbook instructs
# the build host to overwrite them with the real `pyinstaller` build's numbers
# BEFORE the verdict is binding. The defaults are conservative typical values
# for a Flask+curl_cffi onefile so that, absent real numbers, the verdict does
# not spuriously say "adopt".
# --------------------------------------------------------------------------
PYINSTALLER_BASELINE = {
    "packager": "pyinstaller",
    "size_bytes": 90 * 1024 * 1024,   # ~90 MB onefile (reference, replace on host)
    "startup_s": 3.0,                 # self-extract cold start (reference)
    "measured": False,                # flip True once the host fills real numbers
}

# A Nuitka build must beat the baseline by at least this margin on a dimension
# to count as a MATERIAL win; a tie or marginal edge retains pyinstaller (the
# roadmap's own read: the value case is weak for BD's I/O-bound use, so parity
# is not a reason to carry a second packager).
_SIZE_WIN = 0.80        # <= 80% of baseline size
_STARTUP_WIN = 0.80     # <= 80% of baseline startup


def _is_onefile_output(dist_dir):
    """True if dist_dir is a Nuitka --onefile OUTPUT dir: the shippable
    <name>.bin at top level PLUS build-leftover trees (<name>.build /
    .onefile-build / <name>.dist) that are NOT part of the artifact. A
    --standalone .dist dir, by contrast, holds the runtime .so files as the
    bin's siblings and no *.build dir, so this returns False for it."""
    if not os.path.isdir(dist_dir):
        return False
    try:
        entries = os.listdir(dist_dir)
    except OSError:
        return False
    has_bin = any(f.endswith(".bin")
                  and os.path.isfile(os.path.join(dist_dir, f)) for f in entries)
    leftover = any(d.endswith((".build", ".onefile-build", ".dist"))
                   and os.path.isdir(os.path.join(dist_dir, d)) for d in entries)
    return has_bin and leftover


def measure_size(dist_dir):
    """Bytes of the SHIPPABLE artifact. None if absent -- a missing dist has
    UNKNOWN size, not zero (zero reads as 'tiny binary, great').

    --onefile: the artifact is the single <name>.bin; Nuitka keeps its
    intermediate .build/.onefile-build/.dist trees in the same output dir, so a
    whole-dir rollup over-reports ~18x (witnessed: a 117.6 MB .bin measured as
    2.2 GB, flipping a real win into a false 'retain'). Size only the .bin.
    --standalone: the whole .dist directory IS the artifact -> roll it up."""
    if not os.path.isdir(dist_dir):
        return None
    if _is_onefile_output(dist_dir):
        b = _dist_binary(dist_dir)
        try:
            return os.path.getsize(b) if b and os.path.isfile(b) else None
        except OSError:
            return None
    total = 0
    for dp, dns, fns in os.walk(dist_dir):
        for fn in fns:
            try:
                total += os.path.getsize(os.path.join(dp, fn))
            except OSError:
                continue
    return total


def human_size(n):
    if n is None:
        return "unknown"
    if n < 1024:
        return "%d B" % n
    for unit in ("KB", "MB", "GB"):
        n /= 1024.0
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit)


def _dist_binary(dist_dir):
    """The launchable binary inside a Nuitka standalone dist. Nuitka names it
    <entry-stem>.bin on Linux (e.g. downloader_ui.bin)."""
    if not os.path.isdir(dist_dir):
        return None
    # prefer the entry-stem binary; fall back to any executable *.bin
    cands = [f for f in os.listdir(dist_dir) if f.endswith(".bin")]
    if not cands:
        # onefile / non-.bin layouts: an executable with no extension
        cands = [f for f in os.listdir(dist_dir)
                 if os.access(os.path.join(dist_dir, f), os.X_OK)
                 and "." not in f]
    if not cands:
        return None
    cands.sort(key=lambda f: (not f.startswith("downloader_ui"), f))
    return os.path.join(dist_dir, cands[0])


def startup_env(port=5599):
    """Env for the boot: BD_PORT/BD_HOST are the app's documented knobs
    (downloader_ui.py). One-shot idle exit disabled so the probe window is
    stable."""
    e = dict(os.environ)
    e["BD_PORT"] = str(port)
    e["BD_HOST"] = "127.0.0.1"
    e["BD_DISABLE_KEEPALIVE"] = "1"
    return e


def startup_command(dist_dir, port=5599):
    """argv to launch the compiled binary. Raises if the binary is absent --
    a launch command for a non-existent binary is a lie, not a command."""
    binp = _dist_binary(dist_dir)
    if not binp:
        raise FileNotFoundError("no launchable binary in dist: %s" % dist_dir)
    return [binp]


def correctness_probes(base_url):
    """What the frozen binary must answer -- not merely 'it started'. Each probe
    exercises something the packaging config had to get right:
      /            -> the bundled frontend/dist SPA (a data_dir)
      /api/health  -> a real blueprint route (the app actually wired up)
    """
    base = base_url.rstrip("/")
    return [
        {"name": "spa_root", "url": base + "/", "expect_status": (200, 302)},
        {"name": "api_health", "url": base + "/api/health",
         "expect_status": (200,)},
    ]


def verdict(size_bytes, startup_s, correctness_pass, baseline=None):
    """The MOD-7 close decision.

    - UNKNOWN if any measurement is missing (never guess).
    - retain-pyinstaller if correctness fails (HARD gate) OR if Nuitka does not
      MATERIALLY beat the baseline on size or startup (parity is not adoption).
    - adopt-candidate otherwise. (Never a bare "adopt": adoption also needs the
      operator's judgement on maintaining a second toolchain -- the harness
      recommends, the operator decides.)
    """
    base = baseline or PYINSTALLER_BASELINE
    if size_bytes is None or startup_s is None or correctness_pass is None:
        return {"decision": "unknown",
                "reason": "missing measurement(s)",
                "size_bytes": size_bytes, "startup_s": startup_s,
                "correctness_pass": correctness_pass}
    if not correctness_pass:
        return {"decision": "retain-pyinstaller",
                "reason": "correctness gate failed -- a smaller/faster binary "
                          "that does not serve correctly is not adoptable",
                "size_bytes": size_bytes, "startup_s": startup_s,
                "correctness_pass": False}
    size_win = size_bytes <= _SIZE_WIN * base["size_bytes"]
    startup_win = startup_s <= _STARTUP_WIN * base["startup_s"]
    if size_win or startup_win:
        wins = []
        if size_win:
            wins.append("size %s vs %s" % (human_size(size_bytes),
                                           human_size(base["size_bytes"])))
        if startup_win:
            wins.append("startup %.2fs vs %.2fs" % (startup_s, base["startup_s"]))
        return {"decision": "adopt-candidate",
                "reason": "material win: " + "; ".join(wins) +
                          (" (BASELINE UNMEASURED -- confirm on host)"
                           if not base.get("measured") else ""),
                "size_bytes": size_bytes, "startup_s": startup_s,
                "correctness_pass": True}
    return {"decision": "retain-pyinstaller",
            "reason": "no material win over pyinstaller (%s / %.2fs vs %s / %.2fs)"
                      % (human_size(size_bytes), startup_s,
                         human_size(base["size_bytes"]), base["startup_s"]),
            "size_bytes": size_bytes, "startup_s": startup_s,
            "correctness_pass": True}


def run_eval(dist_dir, port=5599, boot_timeout=30.0, baseline=None):
    """Boot the compiled binary, measure cold start, run the correctness probes,
    and return the verdict. BUILD-HOST path (needs a real dist).

    With no dist present it returns UNKNOWN with a reason -- it never fabricates
    a measurement or reports a false PASS (the gate-degrades-to-skip footgun:
    a harness with no artifact must say so)."""
    size = measure_size(dist_dir)
    if size is None:
        return {"decision": "unknown",
                "reason": "no dist at %s -- run build_nuitka --run on a build "
                          "host first" % dist_dir,
                "size_bytes": None, "startup_s": None, "correctness_pass": None}
    try:
        cmd = startup_command(dist_dir, port=port)
    except FileNotFoundError as e:
        return {"decision": "unknown", "reason": str(e),
                "size_bytes": size, "startup_s": None, "correctness_pass": None}

    base_url = "http://127.0.0.1:%d" % port
    probes = correctness_probes(base_url)
    t0 = time.time()
    proc = subprocess.Popen(cmd, env=startup_env(port),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    startup_s = None
    correctness = None
    try:
        # cold start: poll the health probe until it answers or we time out
        deadline = t0 + boot_timeout
        health = base_url + "/api/health"
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(health, timeout=1.0) as r:
                    if r.status == 200:
                        startup_s = time.time() - t0
                        break
            except Exception:
                time.sleep(0.25)
        if startup_s is None:
            return {"decision": "unknown",
                    "reason": "binary did not become ready within %.0fs"
                              % boot_timeout,
                    "size_bytes": size, "startup_s": None,
                    "correctness_pass": None}
        # correctness: every probe must hit an accepted status
        correctness = True
        failed = []
        for p in probes:
            try:
                with urllib.request.urlopen(p["url"], timeout=3.0) as r:
                    if r.status not in p["expect_status"]:
                        correctness = False
                        failed.append("%s=%d" % (p["name"], r.status))
            except Exception as e:
                correctness = False
                failed.append("%s=err(%s)" % (p["name"], type(e).__name__))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    v = verdict(size, startup_s, correctness, baseline)
    v["dist"] = dist_dir
    v["size_human"] = human_size(size)
    if correctness is False:
        v["failed_probes"] = failed
    return v


def runbook():
    """The build-host procedure + acceptance bar. Printed with --runbook; kept
    here (not a separate doc) so the instrument is self-documenting and there is
    one place to change when the command changes."""
    return """NUITKA BUILD + EVAL RUNBOOK (MOD-7 cut 2) -- run on a BUILD HOST

Why a build host: a full --standalone compile of BulkDownloader follows the
whole import closure (~70 MB of Python: flask, jinja, werkzeug, curl_cffi,
cloakbrowser, aiohttp, cryptography, playwright client, ...). Nuitka transpiles
each to C and compiles to .so, with several GB of transient build space and
30-90 min wall clock. That exceeds a sandbox's disk and exec budget, so the
compile + measurement run here, on a machine with >= 10 GB free and gcc +
patchelf (bd_nuitka_pack provides both offline).

STEP 1 -- baseline the INCUMBENT (pyinstaller), so the comparison is real:
    # build BD the current way and record size + cold start
    #   -> overwrite tools/nuitka_eval.PYINSTALLER_BASELINE with these numbers
    #      and set measured=True. Until then the verdict flags BASELINE UNMEASURED.

STEP 2 -- compile with Nuitka from the shared config (one source of truth):
    python -m tools.build_nuitka --mode standalone --output-dir dist_nuitka --print   # review
    python -m tools.build_nuitka --mode standalone --output-dir dist_nuitka --run     # compile
    # BD modules use relative imports (from . import ...), so they MUST compile as
    # a package -- build_nuitka already emits --include-package=bulk_downloader.
    # A bare --module compile of a package-internal module cannot be loaded
    # standalone (witnessed at cut 2: compiles clean, fails to import in isolation).

STEP 3 -- evaluate (size / startup / correctness) against the baseline:
    python -m tools.nuitka_eval --dist dist_nuitka/downloader_ui.dist --json

ACCEPTANCE BAR (encoded in verdict()):
  * correctness is a HARD gate: / (bundled SPA) and /api/health must answer;
    a smaller/faster binary that does not serve correctly is NOT adoptable.
  * adopt-candidate only on a MATERIAL win: <= 80% of baseline size OR startup.
  * parity or a marginal edge -> retain-pyinstaller (the value case is weak for
    BD's I/O-bound use; a second packager + toolchain is not worth a tie).

MOD-7 may legitimately close as "evaluated, pyinstaller retained" on these
numbers. The harness recommends; the operator makes the adopt/retain call.
"""


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nuitka_eval")
    ap.add_argument("--dist", help="path to a Nuitka standalone .dist dir")
    ap.add_argument("--port", type=int, default=5599)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--runbook", action="store_true")
    a = ap.parse_args(argv)
    if a.runbook or not a.dist:
        print(runbook())
        return 0
    res = run_eval(a.dist, port=a.port)
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        print("decision : %s" % res["decision"])
        print("reason   : %s" % res.get("reason", ""))
        print("size     : %s" % res.get("size_human", human_size(res.get("size_bytes"))))
        print("startup  : %s" % (("%.2fs" % res["startup_s"])
                                  if res.get("startup_s") is not None else "unknown"))
    return 0 if res["decision"] != "unknown" else 3


if __name__ == "__main__":
    sys.exit(main())
