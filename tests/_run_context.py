"""Record what a suite run ran ON, and which file ran where.

WHY. Two full-suite runs of the same tree in the same session reported 1 failure
and 35, and there was no way to tell from either result that the second had four
other suites sharing the box. Every conclusion drawn from a single run that
session had to be retracted, and one prediction was wrong because of it. A
failure count with no record of the machine it came from is not a measurement,
so this attaches the machine to the result -- cores, load at start and end, the
worker count actually used, the distribution mode, and the process's blocked
and ignored signal masks.

AND WHICH FILE RAN WHERE. Cross-file state leaks are found by replaying one
worker's real chain, and that chain had to be reconstructed BY HAND from `-v`
output twice. Each worker now appends its executed files, in order, to its own
file; the master prints the directory. `bd-ladder --chain <that file>` replays it.

THE PART THAT IS NOT SOLVED, stated here because a deferral in prose is a
deferral that gets dropped: this makes a run REPRODUCIBLE, not DETERMINISTIC.
`--dist loadfile` hands files to whichever worker is free, so the assignment
changes run to run and nothing here pins it. What it gives you is the assignment
that actually happened, in a form you can re-run exactly -- which is what every
investigation has actually needed.
"""
import json
import os
import pathlib
import platform
import socket
import tempfile
import time

DIR_NAME = "bd-runctx"

# ANCHORED AT IMPORT, ON PURPOSE. conftest points `tempfile.tempdir` at a
# per-process root it removes when the session ends cleanly, so that every
# `mkdtemp` in the suite is reclaimed. Resolving this path at CALL time would
# put the run context inside that root and delete it with the rest -- and this
# data exists precisely to outlive the run that produced it. conftest imports
# this module before it redirects, so the value captured here is the real
# system temp directory.
_TMP_AT_IMPORT = pathlib.Path(tempfile.gettempdir())   # see tests/_tmproot.py


def sink_dir():
    return _TMP_AT_IMPORT / DIR_NAME


def loadavg():
    try:
        return [round(x, 2) for x in os.getloadavg()]
    except (OSError, AttributeError):
        return None


def cores():
    return os.cpu_count() or 0


def worker_count(config):
    """The -n actually in force, and whether anyone chose it.

    Returns (n, source). xdist stores the resolved number on the master; a
    serial run has none. `auto` resolves before this runs, so the number here is
    the real one -- which is the point, since the value that got recorded wrong
    last time was a 4 nobody meant to type on an 86-core box.
    """
    n = getattr(config.option, "numprocesses", None)
    if n in (None, 0):
        return 1, "serial"
    return int(n), "-n"


def dist_mode(config):
    return getattr(config.option, "dist", None) or "no"


def signal_masks(status_path="/proc/self/status"):
    """Return this process's ignored and blocked masks, failing closed.

    The kernel exposes both as hexadecimal bit sets. They are environment
    identity rather than diagnostics: an inherited ignore changed six test
    verdicts while every previously recorded context field stayed identical.
    Missing procfs, a missing field, or a malformed value is UNKNOWN for that
    field, never a clean-looking zero mask.
    """
    wanted = {"SigIgn": "sigign", "SigBlk": "sigblk"}
    masks = {key: "UNKNOWN" for key in wanted.values()}
    try:
        lines = pathlib.Path(status_path).read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError):
        return masks
    for line in lines:
        name, separator, value = line.partition(":")
        key = wanted.get(name)
        if not separator or key is None:
            continue
        try:
            masks[key] = "0x%016x" % int(value.strip(), 16)
        except ValueError:
            masks[key] = "UNKNOWN"
    return masks


def context(config):
    n, source = worker_count(config)
    ctx = {
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "cores": cores(),
        "workers": n,
        "workers_from": source,
        "dist": dist_mode(config),
        "load_at_start": loadavg(),
        "started": time.time(),
    }
    ctx.update(signal_masks())
    return ctx


def advise(ctx):
    """One sentence about whether this run's shape is worth trusting.

    Deliberately blunt and deliberately conservative. The rule it encodes is
    the one that cost the most: a suite sharing a box with other work produces
    failure counts that cannot be compared with an idle box's, and the number
    everybody reads is the failure count.
    """
    notes = []
    load = (ctx.get("load_at_start") or [0])[0]
    c = ctx.get("cores") or 1
    w = ctx.get("workers") or 1
    if load > c * 0.5:
        notes.append("the box was ALREADY at load %.1f on %d cores when this "
                     "started, so this run's failure count is not comparable "
                     "with an idle one" % (load, c))
    if w == 1 and c >= 16:
        notes.append("serial on a %d-core box -- -n %d would have been the "
                     "shape of the machine" % (c, max(2, c // 3)))
    if w > c:
        notes.append("-n %d on %d cores oversubscribes the box" % (w, c))
    return notes


def chain_path(directory, worker_id):
    return pathlib.Path(directory) / ("%s.chain" % worker_id)


def note_file(directory, worker_id, path, _seen={}):
    """Append a test FILE to this worker's chain, once, in first-seen order.

    Called per test; a file already in this chain is never appended again, so a
    2000-test run writes a couple of hundred lines rather than 2000. DEDUPED
    rather than transition-only, and that is not a detail: the xdist MASTER
    re-emits every worker's events interleaved, so a transition rule turned
    three test files into a 32-line chain on its first xdist run. What the
    chain is FOR is replaying a process's file sequence, and a file appears in
    that sequence once.

    The file is reopened per write rather than held: a worker killed mid-run
    must still leave a readable chain, and that is the run an investigation
    cares most about.
    """
    key = (str(directory), worker_id)
    seen = _seen.setdefault(key, set())
    if path in seen:
        return False
    seen.add(path)
    d = pathlib.Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    with open(chain_path(d, worker_id), "a", encoding="utf-8") as fh:
        fh.write(path + "\n")
    return True


def read_chains(directory):
    d = pathlib.Path(directory)
    if not d.is_dir():
        return {}
    out = {}
    for p in sorted(d.glob("*.chain")):
        out[p.stem] = [ln.strip() for ln in
                       p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return out


def write_assignment(directory, chains, ctx):
    path = pathlib.Path(directory) / "assignment.json"
    path.write_text(json.dumps(
        {"context": ctx,
         "assignment": {f: w for w, files in chains.items() for f in files},
         "chains": chains}, indent=1), encoding="utf-8")
    return path


def _newest_touch(run_dir):
    """The most recent mtime of a run directory OR anything inside it.

    Ranking runs by the DIRECTORY's own mtime is wrong for this recorder: a
    chain is append-only, and appending to a file updates the FILE's mtime, not
    the containing directory's, so an actively-written run's directory mtime
    freezes when its chain files are first created and then looks stale. A nested
    pytest's prune then evicted the live outer run mid-suite, losing most of its
    chain (row 179). Keying on the newest content instead keeps a run that is
    still being appended ranked as recent.

    CONCURRENT REMOVAL IS EXPECTED, NOT EXCEPTIONAL. Every pytest process on
    this host shares this one directory and prunes it from its own
    `pytest_unconfigure`, and `bd-gc` sweeps individual run directories too
    (never the shared parent -- see its own NEVER list). So a directory
    listed a moment ago by `prune()`'s scan can be gone by the time this
    function stats it: that TOCTOU race made `run_dir.stat()` raise
    FileNotFoundError here, which escaped `prune()` and crashed
    `pytest_unconfigure` for an entire suite whose own tests had all already
    passed (captured 2026-09-01). A run directory that has vanished by the
    time anyone gets around to ranking it is definitionally not worth
    keeping, so it ranks as the oldest possible run rather than raising --
    there is nothing left under that identity to keep OR remove, and the
    removal loop in `prune()` tolerates its absence too. A DIFFERENT error
    (PermissionError, say) is not a disappearance and must still surface.
    """
    try:
        newest = run_dir.stat().st_mtime
    except (FileNotFoundError, NotADirectoryError):
        return float("-inf")
    try:
        for f in run_dir.iterdir():
            try:
                newest = max(newest, f.stat().st_mtime)
            except (FileNotFoundError, NotADirectoryError):
                pass
    except (FileNotFoundError, NotADirectoryError):
        pass
    return newest


def prune(keep=20):
    """Bounded retention. Creating a path is a promise to remove it -- 744
    leaked directories, measured, from a recorder that forgot this. Runs are
    ranked by newest CONTENT mtime (see `_newest_touch`) so a live run being
    appended is never in the eviction set even when a nested pytest calls this.

    A candidate directory can also vanish AFTER ranking, between being chosen
    as stale and actually being removed -- another concurrent prune() or a
    `bd-gc` sweep can win the same race. That is tolerated here too, on the
    same terms as `_newest_touch`: only a proven disappearance
    (FileNotFoundError/NotADirectoryError) is swallowed, so a directory
    genuinely gone by the time we get to it is not "removed BY this call" and
    is not counted, but anything else (a permission failure, say) still
    surfaces instead of being silently absorbed into a clean-looking count.
    """
    d = sink_dir()
    if not d.is_dir():
        return 0
    runs = sorted((p for p in d.iterdir() if p.is_dir()),
                  key=_newest_touch, reverse=True)
    removed = 0
    for stale in runs[keep:]:
        try:
            for f in stale.iterdir():
                f.unlink()
            stale.rmdir()
            removed += 1
        except (FileNotFoundError, NotADirectoryError):
            pass
    return removed
# ---- appended to tests/_run_context.py by cut 1221 (row 234) ----

def current_path(directory, worker_id):
    return pathlib.Path(directory) / ("%s.current" % worker_id)


def note_current(directory, worker_id, nodeid):
    """Record the nodeid this worker is ABOUT to run, atomically.

    WHY THIS EXISTS. When a worker dies mid-test its identity is destroyed three
    ways at once: pytest-timeout writes its diagnostic to a stdout xdist points
    at /dev/null, xdist's synthetic crash report names the nodeid but renders
    only in a final summary a livelocked session never reaches, and `-q`
    suppresses the recovery narration entirely. On 2026-08-24 the only surviving
    evidence was the sibling `.chain` file, which names the FILE -- and that file
    held 51 candidate items.

    WRITTEN BEFORE THE TEST RUNS, and atomically. A reader that catches a
    half-written marker learns a truncated nodeid, which is worse than none:
    backlog row 222 is an entire row about a pid file read between create and
    write. Temp plus os.replace, the same discipline that closed it.
    """
    d = pathlib.Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    target = current_path(d, worker_id)
    tmp = target.with_suffix(".current.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(str(nodeid) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, target)
    return target


def clear_current(directory, worker_id):
    """Drop the marker once the test finishes, however it finished.

    A marker that outlives its test would accuse an innocent one: after a clean
    run every worker would still be pointing at whatever it happened to run
    last, and the ONE fact this instrument exists to provide -- "this worker died
    here" -- would be indistinguishable from "this worker finished here". So the
    presence of a marker is itself the signal, and it must be cleared on the
    normal path for that to mean anything.
    """
    try:
        current_path(directory, worker_id).unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def read_current(directory):
    """Every worker that left a marker behind, i.e. did not finish its test.

    Returns {worker_id: nodeid}. Empty on a clean run, which is the point.
    """
    d = pathlib.Path(directory)
    if not d.is_dir():
        return {}
    out = {}
    for p in sorted(d.glob("*.current")):
        try:
            text = p.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            out[p.stem] = text
    return out
