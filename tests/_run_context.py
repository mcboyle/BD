"""Record what a suite run ran ON, and which file ran where.

WHY. Two full-suite runs of the same tree in the same session reported 1 failure
and 35, and there was no way to tell from either result that the second had four
other suites sharing the box. Every conclusion drawn from a single run that
session had to be retracted, and one prediction was wrong because of it. A
failure count with no record of the machine it came from is not a measurement,
so this attaches the machine to the result -- cores, load at start and end, the
worker count actually used, and the distribution mode.

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


def sink_dir():
    return pathlib.Path(tempfile.gettempdir()) / DIR_NAME


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


def context(config):
    n, source = worker_count(config)
    return {
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "cores": cores(),
        "workers": n,
        "workers_from": source,
        "dist": dist_mode(config),
        "load_at_start": loadavg(),
        "started": time.time(),
    }


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


def prune(keep=20):
    """Bounded retention. Creating a path is a promise to remove it -- 744
    leaked directories, measured, from a recorder that forgot this."""
    d = sink_dir()
    if not d.is_dir():
        return 0
    runs = sorted((p for p in d.iterdir() if p.is_dir()),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for stale in runs[keep:]:
        try:
            for f in stale.iterdir():
                f.unlink()
            stale.rmdir()
            removed += 1
        except OSError:
            pass
    return removed
