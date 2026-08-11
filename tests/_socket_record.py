"""STAGE 1 of the socket guard: RECORD non-loopback connects, block nothing.

Operator decision taken at v3.66.980, built at v3.66.1031. The motivating
defect is @977: `_check_ytdlp()` grew a live PyPI call, every existing test
mocked only `status_dict` and silently got live data, and the BOX caught it --
not the band, and not review. The suite acquired a network dependency nobody
asked for and nothing in the tree could see it.

This module is the instrument that settles the size of the problem. It does NOT
enforce: stage 2 turns the measured list into a refusal with an opt-out marker,
and there is no point writing that refusal against an estimate. The estimate on
file is "42 test files reference outbound APIs of which 21 never mention
loopback" -- an upper bound over string matches, not a count of callers.

WHY A HOOK AND NOT A READ (CLAUDE.md section 0). Reading the tree for outbound
calls measures "the code I thought to look at". Measured here at v3.66.1031
against the real subject: with `socket.socket.connect` wrapped,
`ytdlp_updater.latest_version(allow_fetch=True)` records exactly one attempt,
`('151.101.192.223', 443)`, reached through `socket.create_connection` from
`http/client.py`. The subject is inside the denominator, demonstrated rather
than reasoned from the call chain.

WHAT THIS CANNOT SEE -- stated because a recorder that reports zero must not be
readable as "nothing called out" (section 0: unknown is a third state). All
four are measured or structural, not guesses:

  * CHILD PROCESSES. The hook lives in this interpreter. 164 of 1316 tracked
    test files spawn a subprocess (`subprocess.`/`Popen`/`check_output`/
    `os.system`, measured at v3.66.1031); a child's connects are invisible.
    The stage-2 lever for this is a `sitecustomize.py` on the child's
    PYTHONPATH, deliberately NOT built here -- it changes the environment of
    every subprocess in the suite, which is its own cut.
  * C-LEVEL SOCKETS. libpq opens its own; the 8 test files importing psycopg
    are outside the denominator. Same for anything a browser, ffmpeg, yt-dlp
    or streamlink opens.
  * RAW `_socket.socket`. This wraps the Python-level `socket.socket` class.
    Code instantiating the C base directly bypasses it.
  * DNS. Resolution happens in getaddrinfo below the socket layer, so a name
    lookup leaves no record here -- and the address recorded is therefore the
    RESOLVED IP, not the hostname the caller wrote.

`observed` counts every connect the hook saw, local ones included. It exists so
a zero in the report can be told apart from a hook that was never armed -- the
non-empty-denominator assertion this file's own subject demands.
"""
import ipaddress
import json
import os
import pathlib
import socket
import tempfile
import threading
import traceback

# Kept as data rather than prose so the terminal report can print them: a
# summary that says "0 attempts" without naming what it could not look at is
# the gate-reports-OK-while-blind shape this whole module exists to fight.
BLIND_SPOTS = (
    "child processes (164/1316 test files spawn one)",
    "C-level sockets: libpq/psycopg (8 files), browsers, ffmpeg, yt-dlp",
    "raw _socket.socket instantiation",
    "DNS resolution (addresses are recorded post-resolution, as IPs)",
)

_REPO = pathlib.Path(__file__).resolve().parent.parent

_state = threading.local()
_lock = threading.Lock()

observed = 0          # every connect the hook saw
recorded = 0          # the non-local subset

_real_connect = None
_real_connect_ex = None
_sink_path = None
_armed_dir = None
_last_nodeid = None       # main-thread fallback for background-thread connects


def sink_dir() -> pathlib.Path:
    """Where records land: outside the repo, and behind NO environment variable.

    This module reads `os.environ` nowhere, which `test_the_sink_needs_no_env_var`
    asserts by AST rather than by grep. Two reasons, and the first is the one a
    future editor will be tempted to undo:

    * A config knob here would join the surface `test_gui_parity` grades, where
      an unprefixed key must be ledgered display-only and a `BD_`-prefixed one
      must be ledgered outright. (CLAUDE.md section 4 says that scan matches on
      the `BD_` prefix; it does not, and has not since v3.66.713 -- the test's
      own comment records the prefix-blindness being fixed. Re-derived at
      v3.66.1031.)
    * An env var is inherited by every subprocess the suite spawns, which is a
      lot of processes to hand a knob to for a recorder that needs none.

    The per-run subdirectory is chosen by the caller and reaches xdist workers
    through `workerinput`; see `_socket_record_run_dir` in conftest.
    """
    return pathlib.Path(tempfile.gettempdir()) / "bd-socket-record"


def is_local(address) -> bool:
    """True when this address cannot leave the host.

    Fails toward RECORDING: an address shape we cannot classify, or a name we
    decline to resolve, counts as non-local. For a stage-1 recorder a false
    record costs one line in a report a human reads, while a false "local"
    silently shrinks the measured list -- which is the whole deliverable.
    """
    if isinstance(address, (str, bytes, pathlib.PurePath)):
        return True                       # AF_UNIX path: local by construction
    if not isinstance(address, tuple) or not address:
        return True                       # not a shape we can judge; not our subject
    host = address[0]
    if isinstance(host, bytes):
        host = host.decode("utf-8", "replace")
    if not isinstance(host, str):
        return True
    if host in ("", "localhost") or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        # A NAME reached connect() unresolved. Resolving it here would be a
        # network call inside the instrument that exists to find network calls.
        return False


def _frames():
    """Repo-relative CALLER frames, nearest last; venv, stdlib and self dropped.

    This module's own frames are excluded deliberately: every record would
    otherwise open with the two wrapper frames, pushing the line that actually
    made the call out of a three-frame display. The harvest is read by a human
    deciding which test to fix.
    """
    out = []
    for fr in traceback.extract_stack()[:-2]:
        name = fr.filename
        if "/venv/" in name or "/site-packages/" in name:
            continue
        if name == __file__:
            continue
        try:
            name = str(pathlib.Path(name).resolve().relative_to(_REPO))
        except ValueError:
            continue                      # stdlib and anything outside the tree
        out.append("%s:%d:%s" % (name, fr.lineno, fr.name))
    return out[-6:]


def set_nodeid(nodeid):
    global _last_nodeid
    _state.nodeid = nodeid
    if nodeid is not None:
        _last_nodeid = nodeid


def _current_nodeid():
    """The running test, and HOW confidently -- ("<nodeid>", "test"|"ambient").

    The thread-local is authoritative, but it is set by the per-test fixture on
    the MAIN thread, so a connect from a background thread reads empty. That is
    not an edge case here: on the first harvest 9 of the 16 packet-sending rows
    had no test attached -- more than half the real subject, and three of them
    were to PyPI, the exact @977 shape. An unattributable row cannot be actioned
    by stage 2, so those nine were the least useful rows in the deliverable.

    The fallback is the last nodeid the main thread announced, labelled
    "ambient" rather than "test". It is an approximation and says so: a thread
    outliving the test that spawned it gets blamed on whatever is running now.
    Better than nothing, and honest about which it is.
    """
    own = getattr(_state, "nodeid", None)
    if own is not None:
        return own, "test"
    return _last_nodeid, "ambient"


def _write(address, family, kind):
    """One record. `kind` is load-bearing, not decoration.

    MEASURED at v3.66.1031 on the first full-suite harvest: 107 of 124 recorded
    attempts were `_lan_ip_guess` doing a SOCK_DGRAM connect to 8.8.8.8:53 --
    the standard trick for asking the routing table which source address it
    would pick. A UDP connect sends NO packet; app.py:4991 says so in as many
    words. Without the socket type in the row, that is indistinguishable from
    the four genuine SOCK_STREAM calls to :443, and whoever writes stage 2 reads
    a list of 124 outbound calls when the tree has four. Blocking the 107 would
    break LAN-IP discovery and buy nothing.
    """
    global recorded
    host = address[0] if isinstance(address, tuple) and address else str(address)
    port = address[1] if isinstance(address, tuple) and len(address) > 1 else None
    nodeid, attribution = _current_nodeid()
    row = {
        "nodeid": nodeid,
        "attribution": attribution,
        "thread": threading.current_thread().name,
        "host": str(host),
        "port": port,
        "family": getattr(family, "name", str(family)),
        "type": getattr(kind, "name", str(kind)),
        "sends_packets": getattr(kind, "name", str(kind)) != "SOCK_DGRAM",
        "pid": os.getpid(),
        "frames": _frames(),
    }
    with _lock:
        recorded += 1
        path = _sink_path
    if path is None:
        return
    # One line, one append. O_APPEND on a sub-PIPE_BUF write is atomic on Linux,
    # and the file is per-PID anyway, so xdist workers never share a handle.
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def is_armed() -> bool:
    return _real_connect is not None


def arm(sink_directory=None):
    """Wrap connect/connect_ex. Idempotent; returns True when it did the work.

    `create_connection` routes through `socket.socket.connect`, so wrapping the
    class method covers urllib, http.client, requests and asyncio's
    `loop.sock_connect` without a second hook -- verified against the @977 path
    rather than assumed.

    `sink_directory` is explicit so a test can isolate its own sink and then
    hand the session-level hook back to the REAL directory. Re-arming through a
    monkeypatched `sink_dir` would silently point the rest of the session at a
    tmp_path that pytest later deletes, and the harvest would come back short
    with nothing to indicate it.
    """
    global _real_connect, _real_connect_ex, _sink_path, _armed_dir
    if _real_connect is not None:
        return False

    d = pathlib.Path(sink_directory) if sink_directory else sink_dir()
    d.mkdir(parents=True, exist_ok=True)
    _sink_path = d / ("%d.jsonl" % os.getpid())
    _armed_dir = d

    _real_connect = socket.socket.connect
    _real_connect_ex = socket.socket.connect_ex

    def _note(sock, address):
        # NEVER raise into the caller. This is a passive recorder wrapped around
        # every connect in the suite; a bug here would present as a network
        # failure in whatever test it fires under, which is the worst possible
        # way for an instrument to fail.
        global observed
        try:
            with _lock:
                observed += 1
            if not is_local(address):
                _write(address, sock.family, sock.type)
        except Exception:
            pass

    def connect(self, address, *a, **k):
        _note(self, address)
        return _real_connect(self, address, *a, **k)

    def connect_ex(self, address, *a, **k):
        _note(self, address)
        return _real_connect_ex(self, address, *a, **k)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    return True


def disarm():
    global _real_connect, _real_connect_ex, _armed_dir
    if _real_connect is None:
        return False
    socket.socket.connect = _real_connect
    socket.socket.connect_ex = _real_connect_ex
    _real_connect = None
    _real_connect_ex = None
    _armed_dir = None
    return True


def reset_counters():
    global observed, recorded
    with _lock:
        observed = 0
        recorded = 0


def summarize(directory=None):
    """Aggregate every worker's JSONL into {nodeid: [rows]}.

    Defaults to the directory currently ARMED, not to `sink_dir()`. Those differ
    whenever a caller armed an explicit sink, and reading the wrong one returns
    `{}` -- a clean verdict over a denominator that never contained the records.
    Found exactly that way at v3.66.1031: this file's own loopback test asserted
    an empty summary and passed while reading a different directory, so it was
    green for the wrong reason and would have stayed green with the classifier
    inverted.

    Reads the whole directory, so a harvest run wants it empty first -- stale
    files from an earlier run are indistinguishable from this one's.
    """
    directory = pathlib.Path(directory or _armed_dir or sink_dir())
    by_test = {}
    if not directory.is_dir():
        return by_test
    for path in sorted(directory.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            by_test.setdefault(row.get("nodeid") or "<no test>", []).append(row)
    return by_test
