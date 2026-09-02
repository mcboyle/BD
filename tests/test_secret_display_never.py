"""Secret-display-never gate (G0/G12).

A release gate asserting that NO operator-facing endpoint echoes a stored
secret VALUE into a response, and that no shipped template/SPA source
interpolates a secret-named field's value directly. This closes a whole class
of F2 regression (a new panel or status route that helpfully renders a config
value which happens to be a credential) before the Phase-C secret/VPN surfaces
are wired.

Mechanism (deterministic, browser-free, in-process Flask test client — runs in
the custom run_tests.py harness and under real pytest):

  1. Seed a site whose secret-shaped fields carry a UNIQUE sentinel value.
  2. Enumerate every eligible operator-facing GET rule from the live url_map;
     concretize every argument (including multi-argument and non-site rules),
     and assert the sentinel never appears in any body. A stored secret leaking
     through any scanned surface puts the unique sentinel in that response.
  3. Static scan of shipped cockpit templates + built SPA for a secret-named
     field whose VALUE is interpolated unmasked.

The secret-name rule is single-sourced from app_settings_center._is_secret
(password|token|api_key|secret, case-insensitive, plus cookie_file) — the same
classifier the Settings Center uses — so this gate and the editor never drift.

POSTURE: read-only / recognition-only. The gate seeds its own sentinel and
scans for it; it never prints a real secret and never touches the
fixtures/recon_corpus set (no endpoint serves those).
"""
import faulthandler
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

BD_GATE_SCOPE = "repo-wide"

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from bulk_downloader.app_settings_center import _is_secret  # single-sourced rule  # noqa: E402

# Unique, collision-resistant sentinel for the one secret the site schema
# actually persists (the credential `password` column). A token/api_key field
# is NOT a site column — the site model drops it — so seeding one would be
# vacuous (never stored, nothing to leak). The substring detector below is not
# field-specific (the teeth test proves it catches ANY secret-shaped value an
# endpoint echoes), so one genuinely-stored tracer is sufficient for the
# dynamic tier; breadth across secret NAMES is covered by the static tier.
_SENT_PW = "SECRETSENTINEL_PW_9Qk3Zr7x"
_SENTINELS = (_SENT_PW,)

_SURFACE_SUFFIXES = (".html", ".js", ".jinja", ".jinja2", ".htm")
_SECRET_WORDS = ("password", "token", "api_key", "secret", "cookie_file")
_SECRET_INTERPOLATION = re.compile(
    r"(\{\{[^}]*\b(?:" + "|".join(_SECRET_WORDS) + r")\b[^}]*\}\})"
    r"|(\$\{[^}]*\b(?:" + "|".join(_SECRET_WORDS) + r")\b[^}]*\})",
    re.I,
)
_MASK_MARKERS = (
    "•", "****", "masked", "present", "redact", "PLACEHOLDER",
    "has_", "_set", "is_set", "configured",
)

# Endpoints we must NOT drive with a GET in the dynamic scan: streaming/SSE
# (never returns), large/binary downloads/exports, and anything that would
# block the in-process client. We are scanning for value-leaks, so skipping a
# stream is safe — a stream cannot statically render a stored secret anyway.
_SKIP_RULE_SUBSTR = (
    "/api/stream", "/stream", "/export.csv", "/export", "/download",
    "/api/captcha/pending/", "/logs/tail", "/api/activity/v2/export",
)


# ── ROWS 640/641: the shard boundary names what actually failed ────────────
#
# `_scan_all` broke out of its collection loop on the first queue timeout and
# reported only a COUNT, so a DEADLOCKED child, a child SLOWER than the budget,
# and a child that DIED before its put were indistinguishable. That is exactly
# CLAUDE.md A7's named shape, and it is the direct reason the original
# `expected_shards=32 collected_shards=31` could not be diagnosed from the lane
# log: two opposite remedies -- fix a lock, or raise a budget -- looked the
# same, and the wrong one was considered first.
#
# Bounded far below the 60s shard read at `out_q.get` and the 240s pytest bound
# governing this file: asking a child for its stack must never become the reason
# a diagnosis times out. NOT a callee timeout -- it is the deadline of a poll for
# an asynchronous artifact, so it is not a row-338 call site.
_SHARD_STACK_BUDGET_S = 5

#: Set in the CHILD only, so the dump file object outlives `_arm_shard_dumper`.
_ARMED_DUMPER = []


def _arm_shard_dumper(dump_path):
    """Let the parent ask this child for its own Python stack, later.

    MUST be the worker's first statement. SIGUSR1's default disposition is
    Term, so a parent that signals a child which has not yet armed would KILL
    the process it was trying to interview -- the diagnosis destroying its own
    evidence. The parent therefore refuses to signal until this file EXISTS,
    which is why the file is created here rather than at first dump.
    """
    if not dump_path:
        return None
    handle = open(dump_path, "w")
    _ARMED_DUMPER.append(handle)
    faulthandler.register(signal.SIGUSR1, file=handle, all_threads=True,
                          chain=False)
    return handle


def _drop_inherited_db_handle():
    """Forget -- never close -- the sqlite handle this child inherited.

    ROW 641. MEASURED 2026-09-02 on test5: every one of 32 forked children
    inherits the parent's cached `_DB_CONN_LOCAL.idle` handle, and because the
    pool keys its cache on `os.getpid()` the child's first history query calls
    `_close_history_conn` on THAT object -- proven by instrumenting the child
    (`close_calls=[True]`, the True meaning `cx is the parent's handle`). Thirty
    two processes closing one WAL connection the parent still owns is the
    hazard SQLite documents as "do not carry an open connection across fork",
    and it is the credible mechanism for the three `sqlite3.DatabaseError`
    refusals row 641 recorded on /api/history, /api/queue_templates/1 and
    /api/sites/<sid>/queue/search -- the three DB-reading routes in the set.
    Dropping the reference leaves the parent's handle untouched and makes the
    child open its own.
    """
    try:
        from bulk_downloader import db as _db
    except Exception as exc:
        return "unavailable: %s: %s" % (type(exc).__name__, exc)
    inherited = getattr(_db._DB_CONN_LOCAL, "idle", None)
    _db._DB_CONN_LOCAL.idle = None
    return "dropped" if inherited is not None else "none inherited"


@contextmanager
def _nonlocal_network_blocked():
    """Keep the route census local even when a status route normally probes."""
    import errno
    import ipaddress
    import socket

    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def local_host(host):
        if host is None:
            return True
        if isinstance(host, bytes):
            host = host.decode("utf-8", "replace")
        if not isinstance(host, str):
            return False
        if host in {"", "localhost"} or host.endswith(".localhost"):
            return True
        try:
            return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
        except ValueError:
            return False

    def local_address(address):
        if isinstance(address, (str, bytes, os.PathLike)):
            return True
        return isinstance(address, tuple) and bool(address) and local_host(address[0])

    def guarded_getaddrinfo(host, *args, **kwargs):
        if not local_host(host):
            raise OSError(
                f"outbound network disabled during secret route scan: host={host!r}"
            )
        return real_getaddrinfo(host, *args, **kwargs)

    def guarded_connect(sock, address):
        if not local_address(address):
            raise OSError(
                "outbound network disabled during secret route scan: "
                f"address={address!r}"
            )
        return real_connect(sock, address)

    def guarded_connect_ex(sock, address):
        if not local_address(address):
            return errno.ENETUNREACH
        return real_connect_ex(sock, address)

    socket.getaddrinfo = guarded_getaddrinfo
    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    try:
        yield
    finally:
        socket.getaddrinfo = real_getaddrinfo
        socket.socket.connect = real_connect
        socket.socket.connect_ex = real_connect_ex


@contextmanager
def _client_seeded():
    """Boot an isolated app + test client, pair for CSRF, seed a site whose
    password and a secret-named config field carry the sentinels. Yields
    (client, headers, sid)."""
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    from bulk_downloader import secrets_store as ss
    orig_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        Path(td, "screenshots").mkdir(exist_ok=True)
        try:
            with _nonlocal_network_blocked():
                db_init()
                c = A.app.test_client()
                tok = c.get("/api/pair").get_json()["token"]
                csrf = c.post(
                    "/api/pair/redeem", json={"token": tok}
                ).get_json()["csrf_token"]
                H = {"X-CSRF-Token": csrf}
                ss._backend = None
                ss._backend_pref = None
                # Seed a site carrying a plaintext password sentinel + a
                # password sentinel (the one persisted credential column).
                r = c.post("/api/sites", json={
                    "name": "secret-display-gate",
                    "password": _SENT_PW,
                }, headers=H)
                assert r.status_code == 200, f"site seed failed: {r.status_code}"
                sid = r.get_json()["id"]
                yield c, H, sid
        finally:
            os.chdir(orig_cwd)


def _scan_targets(app, sid):
    """Concretize the complete eligible runtime GET-rule denominator."""
    rules = tuple(sorted(
        (
            rule
            for rule in app.url_map.iter_rules()
            if "GET" in rule.methods
            and not str(rule.rule).startswith("/static")
            and not any(part in str(rule.rule) for part in _SKIP_RULE_SUBSTR)
        ),
        key=lambda rule: (str(rule.rule), rule.endpoint),
    ))
    assert rules, "eligible runtime-route denominator is zero: UNKNOWN"

    site_arguments = {"sid", "site_id", "siteId", "site"}
    targets = []
    unavailable = []
    for rule in rules:
        values = dict(rule.defaults or {})
        for argument in sorted(rule.arguments):
            if argument in values:
                continue
            converter = rule._converters.get(argument)
            converter_name = type(converter).__name__
            if argument in site_arguments:
                values[argument] = sid
            elif converter_name == "IntegerConverter":
                values[argument] = 1
            elif converter_name == "FloatConverter":
                values[argument] = 1.0
            elif converter_name in {"UnicodeConverter", "PathConverter"}:
                values[argument] = "probe"
            else:
                unavailable.append(
                    f"{rule.rule}: unsupported {argument} converter "
                    f"{converter_name}"
                )
        if unavailable and unavailable[-1].startswith(f"{rule.rule}:"):
            continue
        built = rule.build(values, append_unknown=False)
        if built is None or built[0]:
            unavailable.append(f"{rule.rule}: could not build a local path")
            continue
        targets.append(built[1])

    assert not unavailable, (
        "runtime route concretization UNKNOWN: " + "; ".join(unavailable)
    )
    assert len(targets) == len(rules) > 0, (
        "eligible runtime-route concretization mismatch: "
        f"eligible={len(rules)} concretized={len(targets)}"
    )
    assert len(set(targets)) == len(targets), (
        "runtime route concretization produced duplicate paths: UNKNOWN"
    )
    return sorted(targets)


def _scan_worker(paths, headers, out_q, shard_id=0, dump_path=None):
    """Scan one shard of endpoint paths in a forked child. The child inherits
    the ALREADY-BOOTED app + seeded DB + cwd from the parent (fork), so there
    is no per-worker boot cost; it opens its own test client and GETs its
    shard. Pushes (shard_id, (scanned_count, leaks, unavailable)) to the queue.
    Module-level so it is picklable for multiprocessing.

    The shard id rides the PAYLOAD because process state cannot name a missing
    shard: a child that died before its put can still exit 0, which is
    indistinguishable from a child that finished (row 640)."""
    _arm_shard_dumper(dump_path)          # FIRST: see _arm_shard_dumper's why
    _drop_inherited_db_handle()           # row 641: never close the parent's
    from bulk_downloader import app as A
    scanned, leaks, unavailable = 0, [], []
    c = A.app.test_client()
    for path in paths:
        try:
            resp = c.get(path, headers=headers)
            body = (resp.get_data(as_text=False) or b"").decode("utf-8", "ignore")
        except Exception as exc:
            # ROW 641: the CLASS NAME ALONE collapsed three different sqlite
            # faults -- a malformed image, a locked database and an unopenable
            # file all read `DatabaseError`, and those need opposite remedies.
            # Carry the library's own words.
            unavailable.append(
                (path, "%s: %s" % (type(exc).__name__, exc)))
            continue
        scanned += 1
        for sent in _SENTINELS:
            if sent in body:
                leaks.append((path, sent[:18]))
    out_q.put((shard_id, (scanned, leaks, unavailable)))


def _shard_receipt(item):
    """(shard_id, payload) from one queue item, refusing in the item's own
    shape rather than letting a malformed receipt read as a valid one."""
    assert (isinstance(item, tuple) and len(item) == 2
            and isinstance(item[0], int)), (
        "runtime route scan UNKNOWN: shard receipt is not (shard_id, result): "
        "%s %.200r" % (type(item).__name__, item)
    )
    return item[0], item[1]


def _split_shard_receipts(items):
    """({shard_id: payload}, [payload, ...]) from the collected queue items."""
    seen = {}
    payloads = []
    for item in items:
        shard_id, payload = _shard_receipt(item)
        seen[shard_id] = payload
        payloads.append(payload)
    return seen, payloads


def _child_stack(pid, alive, dump_path):
    """This child's own Python stack, or the NAMED reason there is not one.

    The three shortfall causes are told apart HERE. A deadlocked child and a
    slow child both read alive=True with no exit code; only the stack says
    whether it is parked on a lock or still doing work, so a diagnosis that
    omitted it would have collapsed two of the three causes it exists to
    separate.
    """
    if pid is None:
        return "stack unavailable: the shard has no pid, so no child was forked"
    if alive is False:
        return ("stack unavailable: the child is GONE -- there is nothing left "
                "to interview, and its exit code above is the evidence")
    if alive is not True:
        return "stack unavailable: this shard's liveness could not be read"
    if not dump_path:
        return "stack unavailable: no dump destination was assigned to this shard"
    if not os.path.exists(dump_path):
        return ("stack unavailable: the child never armed its dumper, so it "
                "stopped BEFORE the first line of the worker; signalling it "
                "now would kill it rather than interview it")
    before = os.path.getsize(dump_path)
    try:
        os.kill(pid, signal.SIGUSR1)
    except Exception as exc:
        return ("stack unavailable: could not ask the child (%s: %s)"
                % (type(exc).__name__, exc))
    # A bounded poll for an ASYNCHRONOUS artifact, not a fixed wait: the loop
    # exits the moment the dump lands, and the deadline is itself a finding.
    deadline = time.monotonic() + _SHARD_STACK_BUDGET_S
    while time.monotonic() < deadline:
        if os.path.getsize(dump_path) > before:
            time.sleep(0.05)                       # let the dump finish writing
            with open(dump_path, "r", errors="replace") as handle:
                handle.seek(before)
                text = handle.read()
            frames = [line.strip() for line in text.splitlines() if line.strip()]
            return "stack: " + " <- ".join(frames[:12])
        time.sleep(0.02)
    return ("stack unavailable: no dump arrived within %ss of asking, which "
            "itself says the child is not running interruptible Python"
            % _SHARD_STACK_BUDGET_S)


def _diagnose_missing_shards(procs, shards, seen, dumps):
    """Name every shard that never reported, one shard at a time."""
    lines = []
    for index, proc in enumerate(procs):
        if index in seen:
            continue
        # FACTS FIRST, THEN THE STACK. Asking for a stack signals the child, so
        # reading liveness afterwards would report the state the QUESTION
        # produced instead of the state that caused the shortfall.
        pid = getattr(proc, "pid", None)
        try:
            alive = bool(proc.is_alive())
        except Exception as exc:
            alive = "unreadable (%s)" % type(exc).__name__
        exitcode = getattr(proc, "exitcode", "unreadable")
        routes = shards[index] if index < len(shards) else ()
        lines.append(
            "shard %d never reported: %d route(s) starting %s; pid=%s "
            "alive=%s exitcode=%s; %s"
            % (index, len(routes), routes[0] if routes else "(none)", pid,
               alive, exitcode, _child_stack(pid, alive, dumps.get(index))))
    return " || ".join(lines)


def _reconcile_scan_results(targets, results, expected_shards):
    """Require exact, nonzero shard collection and route execution counts.

    The four-line count assertion below is byte-identical to its parent on
    purpose: it is row310's M3 mutation anchor, and the statement that reads
    the queue above it is row338's M04 anchor bridged to a measured 60s. The
    shard identity and the shortfall diagnosis are therefore split off in
    `_split_shard_receipts` and raised in `_scan_all`, not folded in here.
    """
    assert targets, "runtime route scan has a zero denominator: UNKNOWN"
    assert expected_shards > 0, "runtime route scan expected zero shards: UNKNOWN"
    assert len(results) == expected_shards, (
        "runtime route scan UNKNOWN: "
        f"expected_shards={expected_shards} collected_shards={len(results)}"
    )
    malformed = [
        index
        for index, result in enumerate(results)
        if not isinstance(result, tuple)
        or len(result) not in {2, 3}
        or not isinstance(result[0], int)
        or result[0] < 0
        or not isinstance(result[1], list)
        or (len(result) == 3 and not isinstance(result[2], list))
    ]
    assert not malformed, (
        f"runtime route scan UNKNOWN: malformed shard results={malformed}"
    )
    unavailable = [
        item
        for result in results
        for item in (result[2] if len(result) == 3 else [])
    ]
    assert not unavailable, (
        "runtime route scan UNKNOWN: endpoint request(s) unavailable: "
        + "; ".join(f"{path} ({error})" for path, error in unavailable[:10])
    )
    scanned = sum(result[0] for result in results)
    assert scanned == len(targets) > 0, (
        "runtime route execution denominator mismatch: "
        f"collected={len(targets)} executed={scanned}; verdict is UNKNOWN"
    )
    leaks = [leak for result in results for leak in result[1]]
    return scanned, leaks


def _scan_sequential(targets, headers):
    q = _SeqQueue()
    try:
        _scan_worker(targets, headers, q, shard_id=0, dump_path=None)
    except Exception as exc:
        raise AssertionError(
            "runtime route scan UNKNOWN: the in-process worker failed: %s: %s"
            % (type(exc).__name__, exc)) from exc
    payloads = [_shard_receipt(item)[1] for item in q.items]
    return _reconcile_scan_results(targets, payloads, expected_shards=1)


def _scan_all(targets, headers):
    """GET every target and return (scanned_count, leaks).

    MULTI-CORE: endpoint work runs in-process (Flask test client), so threads
    would serialize on the GIL for CPU-heavy report endpoints (e.g. the
    capture-diagnostics collector). Instead we FORK worker processes after the
    one seeded boot -- children inherit the app + DB -- and shard the targets,
    so wall time is ~the slowest single endpoint instead of the sum of all of
    them (the sequential scan exceeded run_tests' 900s file timeout on a large
    operator capture store). Falls back to a sequential in-process scan if
    fork isn't available or the pool fails (e.g. macOS spawn-only)."""
    import multiprocessing as mp
    nworkers = min(32, (os.cpu_count() or 2), max(1, len(targets) // 8))
    if nworkers <= 1:
        return _scan_sequential(targets, headers)
    # Created BEFORE the first fork: an action whose evidence record must exist
    # cannot prove it afterwards (CLAUDE.md A7).
    dumpdir = tempfile.mkdtemp(prefix="bd-secretscan-shard-")
    try:
        ctx = mp.get_context("fork")
        out_q = ctx.Queue()
        shards = [targets[i::nworkers] for i in range(nworkers)]
        live = [(index, sh) for index, sh in enumerate(shards) if sh]
        dumps = {position: os.path.join(dumpdir, "shard-%d.stack" % position)
                 for position, _ in enumerate(live)}
        procs = [ctx.Process(
                    target=_scan_worker,
                    args=(sh, headers, out_q, position, dumps[position]),
                    daemon=True)
                 for position, (_index, sh) in enumerate(live)]
        shards = [sh for _index, sh in live]
    except Exception:
        shutil.rmtree(dumpdir, ignore_errors=True)
        return _scan_sequential(targets, headers)

    started = []
    try:
        for p in procs:
            p.start()
            started.append(p)
    except Exception:
        for p in started:
            p.terminate()
            p.join(timeout=5)
        shutil.rmtree(dumpdir, ignore_errors=True)
        return _scan_sequential(targets, headers)

    import queue as _qmod
    results = []
    seen = {}
    payloads = []
    missing_detail = ""
    try:
        for _ in procs:
            try:
                # 32 real shard reads measured at most 2.334737s in row 338;
                # max(60, ceil(2 * 2.334737)) = 60s.
                results.append(out_q.get(timeout=60))
            except _qmod.Empty:
                break
        seen, payloads = _split_shard_receipts(results)
        if len(seen) < len(procs):
            # DIAGNOSE BEFORE TERMINATING. p.terminate() destroys the very
            # state -- liveness, stack, exit code -- that tells a deadlock from
            # a slow shard from a child that died before its put (row 640).
            missing_detail = _diagnose_missing_shards(procs, shards, seen, dumps)
    except AssertionError:
        raise            # a refusal that already names itself is not "read failed"
    except Exception as exc:
        raise AssertionError(
            "runtime route scan UNKNOWN: shard read failed: %s: %s"
            % (type(exc).__name__, exc)) from exc
    finally:
        for p in procs:
            p.terminate()
            p.join(timeout=5)
        shutil.rmtree(dumpdir, ignore_errors=True)
    # ROW 640, raised HERE rather than inside the reconciler: that keeps the
    # reconciler's count assertion byte-identical (it is a tracked mutation
    # anchor), and its catcher drives it directly so this earlier refusal
    # cannot launder a mutant that weakened it (CLAUDE.md A5).
    assert not missing_detail, (
        "runtime route scan UNKNOWN: "
        f"expected_shards={len(procs)} collected_shards={len(seen)}"
        f" -- {missing_detail}")
    return _reconcile_scan_results(targets, payloads, expected_shards=len(procs))


class _SeqQueue:
    def __init__(self):
        self.items = []

    def put(self, x):
        self.items.append(x)


def _copy_frontend_for_secret_build(source: Path, destination: Path) -> Path:
    """Copy exact current SPA build inputs while sharing installed tools."""
    assert source.is_dir(), f"frontend source unavailable: {source}"
    node_modules = source / "node_modules"
    assert node_modules.is_dir(), (
        f"frontend build dependencies unavailable at {node_modules}; "
        "shipped-surface verdict is UNKNOWN"
    )
    input_files = tuple(sorted(
        path.relative_to(source)
        for path in source.rglob("*")
        if path.is_file()
        and not ({"dist", "node_modules"} & set(path.relative_to(source).parts))
    ))
    assert input_files, "frontend build-input denominator is zero: verdict is UNKNOWN"
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("dist", "node_modules"),
    )
    os.symlink(node_modules, destination / "node_modules", target_is_directory=True)
    copied_files = tuple(sorted(
        path.relative_to(destination)
        for path in destination.rglob("*")
        if path.is_file()
        and not ({"dist", "node_modules"} & set(path.relative_to(destination).parts))
    ))
    assert copied_files == input_files, (
        "fresh frontend copy did not reconcile to its exact input denominator: "
        f"expected={len(input_files)} copied={len(copied_files)}"
    )
    return destination


def _build_secret_spa_fresh(frontend: Path, output: Path) -> Path:
    """Build the secret scan's shipped surface into attempt-owned output."""
    assert not output.exists(), f"fresh-build output already exists: {output}"
    npm = shutil.which("npm")
    assert npm is not None, "npm unavailable; shipped-surface verdict is UNKNOWN"
    for tool in ("tsc", "vite"):
        candidate = frontend / "node_modules" / ".bin" / tool
        assert candidate.is_file(), (
            f"frontend build tool unavailable: {candidate}; "
            "shipped-surface verdict is UNKNOWN"
        )
    try:
        build = subprocess.run(
            [
                npm,
                "run",
                "build",
                "--",
                "--outDir",
                str(output),
                "--emptyOutDir",
            ],
            cwd=frontend,
            env=dict(os.environ),
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            "fresh SPA build is UNKNOWN: npm run build exceeded 180 seconds",
            pytrace=False,
        )
    assert build.returncode == 0, (
        f"fresh SPA build failed ({build.returncode})\n"
        f"--- stdout ---\n{build.stdout}\n--- stderr ---\n{build.stderr}"
    )
    indexes = list(output.glob("index.html"))
    assert indexes == [output / "index.html"], (
        f"fresh build emitted {len(indexes)} root index files, expected exactly 1"
    )
    return output


def _fresh_secret_surface_dist(work: Path) -> Path:
    frontend = _copy_frontend_for_secret_build(
        _ROOT / "frontend", work / "frontend"
    )
    return _build_secret_spa_fresh(frontend, work / "fresh-dist")


def _eligible_surface_files(roots: tuple[Path, ...]) -> tuple[Path, ...]:
    return tuple(sorted(
        path
        for root in roots
        if root.exists()
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in _SURFACE_SUFFIXES
    ))


def _assert_secret_surface_is_value_free(roots: tuple[Path, ...]) -> int:
    """Scan every eligible file and reconcile collection to execution."""
    subjects = _eligible_surface_files(roots)
    assert subjects, "shipped-surface denominator is zero: verdict is UNKNOWN"
    hits = []
    scanned = 0
    for path in subjects:
        try:
            text = path.read_text(errors="ignore")
        except OSError as exc:
            raise AssertionError(
                f"shipped surface unreadable: {path}: verdict is UNKNOWN"
            ) from exc
        scanned += 1
        for line in text.splitlines():
            if (
                _SECRET_INTERPOLATION.search(line)
                and not any(marker in line for marker in _MASK_MARKERS)
            ):
                try:
                    label = path.relative_to(_ROOT)
                except ValueError:
                    label = path
                hits.append(f"{label}: {line.strip()[:90]}")
    assert scanned == len(subjects) > 0, (
        "shipped-surface execution denominator mismatch: "
        f"expected={len(subjects)} scanned={scanned}"
    )
    assert not hits, (
        "secret-named value appears interpolated unmasked in a shipped surface:\n"
        + "\n".join(hits[:15])
    )
    return scanned


def test_no_endpoint_echoes_a_stored_secret_value():
    """The core gate: with a secret-bearing site seeded, no scanned
    operator-facing GET endpoint returns the sentinel value in its body."""
    from bulk_downloader import app as A
    with _client_seeded() as (c, H, sid):
        targets = _scan_targets(A.app, sid)
        assert targets, "eligible runtime-route denominator is zero: UNKNOWN"
        scanned, leaks = _scan_all(targets, H)
        assert scanned == len(targets) > 0, (
            "runtime route execution denominator mismatch: "
            f"collected={len(targets)} executed={scanned}"
        )
        assert not leaks, (
            "secret VALUE leaked into operator-facing response(s): "
            + "; ".join(f"{p} -> {s}…" for p, s in leaks[:10])
        )


def test_secrets_status_is_value_free():
    """Anchor on the known secrets surface: status reports a count/backend but
    never the seeded value."""
    with _client_seeded() as (c, H, sid):
        body = c.get("/api/secrets/status", headers=H).get_data(as_text=True) or ""
        assert _SENT_PW not in body
        # And the effective per-site config presents secrets as presence-only.
        eff = c.get(f"/api/settings/site/{sid}/effective", headers=H)
        if eff.status_code == 200:
            assert _SENT_PW not in (eff.get_data(as_text=True) or "")


def test_no_template_or_spa_interpolates_a_secret_value(tmp_path):
    """Static scan: no shipped cockpit template or built SPA bundle directly
    interpolates a secret-NAMED field's value (e.g. `{{ password }}`,
    `site.api_token`, `value={secret}`). The SPA bundle is built by this
    attempt, never assumed."""
    dist = _fresh_secret_surface_dist(tmp_path)
    roots = (
        _ROOT / "bulk_downloader" / "templates",
        _ROOT / "templates",
        dist,
    )
    # Secret-named tokens to look for in an interpolation context. Driven off
    # the same classifier vocabulary so the two stay in lockstep.
    assert all(_is_secret(word) for word in _SECRET_WORDS)
    # Detector controls are additional to, not substitutes for, the nonzero
    # fresh artifact denominator reconciled by _assert_secret_surface_is_value_free.
    assert _SECRET_INTERPOLATION.search('value="{{ site.password }}"'), (
        "pattern fails to flag {{ secret }}"
    )
    assert _SECRET_INTERPOLATION.search("x=${api_key}"), (
        "pattern fails to flag ${secret}"
    )
    _bad = "value={{ site.password }}"
    _masked = "value={{ password_is_set }}"
    assert (
        _SECRET_INTERPOLATION.search(_bad)
        and not any(marker in _bad for marker in _MASK_MARKERS)
    ), "unmasked control mis-suppressed"
    assert any(marker in _masked for marker in _MASK_MARKERS), (
        "mask allowlist control broken"
    )
    expected = len(_eligible_surface_files(roots))
    scanned = _assert_secret_surface_is_value_free(roots)
    assert scanned == expected
    assert expected > 0


def test_secret_surface_build_invokes_exactly_one_fresh_build(
    tmp_path, monkeypatch
):
    """The static gate cannot silently return to the checkout's absent dist."""
    fired = {"copy": 0, "build": 0}
    copied = tmp_path / "copied-frontend"
    emitted = tmp_path / "emitted-dist"

    def fake_copy(source, destination):
        fired["copy"] += 1
        assert source == _ROOT / "frontend"
        assert destination == tmp_path / "frontend"
        return copied

    def fake_build(frontend, output):
        fired["build"] += 1
        assert frontend == copied
        assert output == tmp_path / "fresh-dist"
        return emitted

    monkeypatch.setitem(
        _fresh_secret_surface_dist.__globals__,
        "_copy_frontend_for_secret_build",
        fake_copy,
    )
    monkeypatch.setitem(
        _fresh_secret_surface_dist.__globals__,
        "_build_secret_spa_fresh",
        fake_build,
    )

    assert _fresh_secret_surface_dist(tmp_path) == emitted
    assert fired == {"copy": 1, "build": 1}


def test_secret_surface_zero_denominator_is_unknown(tmp_path):
    """Negative control: absent/JSON-only roots reach the UNKNOWN refusal."""
    json_only = tmp_path / "templates"
    json_only.mkdir()
    (json_only / "only.json").write_text("{}\n", encoding="ascii")
    roots = (tmp_path / "absent", json_only)
    assert _eligible_surface_files(roots) == ()
    with pytest.raises(AssertionError, match="denominator is zero.*UNKNOWN"):
        _assert_secret_surface_is_value_free(roots)


def test_secret_surface_leak_failure_path_is_reachable(tmp_path):
    """Negative control: one eligible unmasked interpolation is rejected."""
    surface = tmp_path / "dist"
    surface.mkdir()
    bad = surface / "bundle.js"
    bad.write_text("const shown = `${api_key}`;\n", encoding="ascii")
    roots = (surface,)
    assert _eligible_surface_files(roots) == (bad,)
    with pytest.raises(
        AssertionError,
        match="secret-named value appears interpolated unmasked",
    ):
        _assert_secret_surface_is_value_free(roots)


def test_transform_control_imports_secret_gate_without_building_spa():
    """Transform control: importing the gate does not measure a surface."""
    imported = __import__(__name__, fromlist=["*"])
    assert imported.__file__ == __file__


def _eligible_runtime_rules_for_test(app):
    """Independent route-map denominator for the runtime secret census."""
    return tuple(sorted(
        (
            rule
            for rule in app.url_map.iter_rules()
            if "GET" in rule.methods
            and not str(rule.rule).startswith("/static")
            and not any(part in str(rule.rule) for part in _SKIP_RULE_SUBSTR)
        ),
        key=lambda rule: (str(rule.rule), rule.endpoint),
    ))


def test_runtime_route_census_covers_non_sid_and_multi_argument_rules():
    """The concretizer covers every controlled eligible route shape exactly."""
    from flask import Flask

    fixture = Flask("secret-route-census-fixture")
    fixture.add_url_rule("/plain", "plain", lambda: "", methods=["GET"])
    fixture.add_url_rule(
        "/item/<int:item_id>", "item", lambda item_id: "", methods=["GET"]
    )
    fixture.add_url_rule(
        "/site/<sid>/thing/<path:name>",
        "site_thing",
        lambda sid, name: "",
        methods=["GET"],
    )
    fixture.add_url_rule(
        "/api/stream/<token>", "stream", lambda token: "", methods=["GET"]
    )
    fixture.add_url_rule("/post-only", "post_only", lambda: "", methods=["POST"])

    eligible = _eligible_runtime_rules_for_test(fixture)
    templates = tuple(str(rule.rule) for rule in eligible)
    assert templates == (
        "/item/<int:item_id>",
        "/plain",
        "/site/<sid>/thing/<path:name>",
    ), "controlled eligible-route denominator did not have exactly three rules"
    assert sum(len(rule.arguments) > 1 for rule in eligible) == 1
    assert sum(
        len(rule.arguments) == 1
        and not (rule.arguments & {"sid", "site_id", "siteId"})
        for rule in eligible
    ) == 1

    targets = _scan_targets(fixture, "seed-site")
    assert targets == [
        "/item/1",
        "/plain",
        "/site/seed-site/thing/probe",
    ]


def test_runtime_route_census_matches_the_complete_live_route_map():
    """Every independently eligible live rule has one concrete scan target."""
    from bulk_downloader import app as A

    with _client_seeded() as (_client, _headers, sid):
        eligible = _eligible_runtime_rules_for_test(A.app)
        multi_argument = tuple(rule for rule in eligible if len(rule.arguments) > 1)
        non_sid = tuple(
            rule
            for rule in eligible
            if rule.arguments
            and not (
                len(rule.arguments) == 1
                and rule.arguments & {"sid", "site_id", "siteId"}
            )
        )
        assert eligible, "eligible live runtime-route denominator is zero: UNKNOWN"
        assert multi_argument, "live map did not exercise a multi-argument route"
        assert non_sid, "live map did not exercise a non-sid route"

        targets = _scan_targets(A.app, sid)
        assert len(set(targets)) == len(targets), (
            "runtime route census produced duplicate concrete targets"
        )
        assert len(targets) == len(eligible) > 0, (
            "eligible runtime-route census mismatch: "
            f"eligible={len(eligible)} concretized={len(targets)}"
        )


def _fake_fork_scan(monkeypatch, results):
    """Run the real shard coordinator against a controlled fork boundary."""
    import multiprocessing as mp
    import queue

    fired = {
        "context": 0,
        "queue": 0,
        "process": 0,
        "start": 0,
        "get": 0,
        "terminate": 0,
        "join": 0,
    }
    targets = tuple(f"/probe/{index}" for index in range(16))
    headers = {"X-Route-Census": "fixture"}
    shards = []

    class FakeQueue:
        def __init__(self):
            self._results = list(results)

        def get(self, timeout):
            fired["get"] += 1
            assert timeout == 60
            if self._results:
                return self._results.pop(0)
            raise queue.Empty

    out_q = FakeQueue()

    class FakeProcess:
        """Deliberately carries NO pid/is_alive/exitcode: the diagnosis has to
        degrade by NAME against a process handle that cannot answer, not
        crash and not invent."""

        def start(self):
            fired["start"] += 1

        def terminate(self):
            fired["terminate"] += 1

        def join(self, timeout):
            fired["join"] += 1
            assert timeout == 5

    class FakeContext:
        def Queue(self):
            fired["queue"] += 1
            return out_q

        def Process(self, *, target, args, daemon):
            fired["process"] += 1
            assert target is _scan_worker
            paths, got_headers, got_q, shard_id, dump_path = args
            assert got_headers == headers
            assert got_q is out_q
            assert daemon is True
            assert shard_id == len(shards), (shard_id, len(shards))
            assert dump_path and dump_path.endswith("shard-%d.stack" % shard_id)
            shards.append(tuple(paths))
            return FakeProcess()

    context = FakeContext()

    def fake_get_context(method):
        fired["context"] += 1
        assert method == "fork"
        return context

    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    monkeypatch.setattr(mp, "get_context", fake_get_context)
    return targets, headers, shards, fired


def test_runtime_route_scan_reconciles_two_complete_shards(monkeypatch):
    """Positive control: two complete eight-route receipts reconcile to 16."""
    targets, headers, shards, fired = _fake_fork_scan(
        monkeypatch, [(0, (8, [])), (1, (8, []))]
    )
    assert len(targets) == 16

    assert _scan_all(targets, headers) == (16, [])

    assert shards == [targets[0::2], targets[1::2]]
    assert fired == {
        "context": 1,
        "queue": 1,
        "process": 2,
        "start": 2,
        "get": 2,
        "terminate": 2,
        "join": 2,
    }


def test_runtime_route_scan_rejects_one_missing_shard_as_unknown(monkeypatch):
    """Negative control: 1/2 receipts is UNKNOWN, never partial success."""
    targets, headers, shards, fired = _fake_fork_scan(
        monkeypatch, [(0, (8, []))])
    assert len(targets) == 16

    with pytest.raises(
        AssertionError,
        match=r"runtime route scan UNKNOWN: expected_shards=2 collected_shards=1",
    ) as caught:
        _scan_all(targets, headers)

    # ROW 640: the count is no longer the whole answer. The refusal names WHICH
    # shard, how many routes it owned, and -- against a handle that cannot
    # answer -- says so by name instead of inventing a pid.
    message = str(caught.value)
    assert "shard 1 never reported: 8 route(s) starting /probe/1" in message, message
    assert "pid=None" in message and "exitcode=unreadable" in message, message
    assert "alive=unreadable (AttributeError)" in message, message
    assert "no child was forked" in message, message
    assert "shard 0 never reported" not in message, message

    # The coordinator refuses BEFORE the reconciler now, so the reconciler's
    # own count assertion is driven directly here; otherwise the shortfall
    # refusal would launder a mutant that weakened it (CLAUDE.md A5).
    with pytest.raises(
        AssertionError,
        match=r"runtime route scan UNKNOWN: expected_shards=2 collected_shards=1",
    ):
        _reconcile_scan_results(targets, [(8, [])], expected_shards=2)

    assert shards == [targets[0::2], targets[1::2]]
    assert fired == {
        "context": 1,
        "queue": 1,
        "process": 2,
        "start": 2,
        "get": 2,
        "terminate": 2,
        "join": 2,
    }


def test_runtime_route_scan_rejects_one_unexecuted_route_as_unknown(monkeypatch):
    """Negative control: complete shard receipts cannot hide a skipped route."""
    targets, headers, shards, fired = _fake_fork_scan(
        monkeypatch, [(0, (8, [])), (1, (7, []))]
    )
    assert len(targets) == 16

    with pytest.raises(
        AssertionError,
        match=(
            r"runtime route execution denominator mismatch: "
            r"collected=16 executed=15; verdict is UNKNOWN"
        ),
    ):
        _scan_all(targets, headers)

    assert shards == [targets[0::2], targets[1::2]]
    assert fired == {
        "context": 1,
        "queue": 1,
        "process": 2,
        "start": 2,
        "get": 2,
        "terminate": 2,
        "join": 2,
    }


def test_runtime_route_census_blocks_nonlocal_network():
    """The complete route scan cannot turn a status route into live egress."""
    import errno
    import socket

    original = (socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with _nonlocal_network_blocked():
            with pytest.raises(
                OSError, match="outbound network disabled during secret route scan"
            ):
                socket.getaddrinfo("row310.invalid", 443)
            with pytest.raises(
                OSError, match="outbound network disabled during secret route scan"
            ):
                sock.connect(("203.0.113.10", 443))
            assert sock.connect_ex(("203.0.113.10", 443)) == errno.ENETUNREACH
    finally:
        sock.close()
    assert (
        socket.getaddrinfo,
        socket.socket.connect,
        socket.socket.connect_ex,
    ) == original


# ═══════════════════════════════════════════════════════════════════════════
# ROW 640 -- one message per cause, proven by driving all three causes.
#
# RED, replayed explicitly against the defective parent (HEAD 9e7031fb) on
# test5: the identical 1-of-2 shortfall produced exactly
#   "runtime route scan UNKNOWN: expected_shards=2 collected_shards=1"
# and NONE of the five facts below -- no shard identity, no pid, no liveness,
# no exit code, no stack. A deadlocked child, a child slower than the budget
# and a child that died before its put were therefore the same message.
# ═══════════════════════════════════════════════════════════════════════════

#: Set in the PARENT before the fork; the child inherits it.
_ROW640_CAUSE = []


def _row640_deadlocked_child():
    """Parked on a lock nobody will ever release."""
    import threading
    threading.Event().wait()


def _row640_slow_child():
    """Still working, just past the wait."""
    time.sleep(1800)


def _row640_child_that_died_before_its_put():
    """Gone, with an exit code, having produced nothing."""
    os._exit(3)


def _row640_control_worker(paths, headers, out_q, shard_id=0, dump_path=None):
    """Shard 0 reports normally; shard 1 fails the way the test selected.

    Arms through the PRODUCTION helper, so the control exercises the real
    dumper rather than a look-alike.
    """
    _arm_shard_dumper(dump_path)
    if shard_id == 0:
        out_q.put((0, (len(paths), [], [])))
        return
    _ROW640_CAUSE[0]()
    out_q.put((shard_id, (len(paths), [], [])))     # never reached


#: Seconds a control waits for a shard receipt. The PRODUCTION 60s literal is
#: untouched -- row 338 bridges its measured table to that exact call site, so
#: the control shortens its own WAIT instead of moving the tool's bound. Well
#: under the 240s pytest bound governing this file.
_ROW640_CONTROL_WAIT_S = 4


def _row640_shortfall(monkeypatch, cause):
    """Drive the REAL fork boundary with one misbehaving shard; return the
    refusal's message.

    The children, their pids, their stacks and their exit codes are real. Only
    the parent's WAIT is shortened, by a queue wrapper -- so the production
    budget stays a literal where its census can see it.
    """
    import multiprocessing as mp
    import queue as _qmod
    module = sys.modules[__name__]
    _ROW640_CAUSE[:] = [cause]
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    monkeypatch.setattr(module, "_scan_worker", _row640_control_worker)

    real_ctx = mp.get_context("fork")
    assert real_ctx, "precondition: this platform must fork"
    waits = []

    class _ImpatientQueue:
        def __init__(self, inner):
            self._inner = inner

        def get(self, timeout):
            waits.append(timeout)
            return self._inner.get(timeout=min(timeout, _ROW640_CONTROL_WAIT_S))

        def __getattr__(self, name):
            return getattr(self._inner, name)

    class _Ctx:
        def Queue(self):
            return _ImpatientQueue(real_ctx.Queue())

        def Process(self, **kwargs):
            return real_ctx.Process(**kwargs)

    monkeypatch.setattr(mp, "get_context", lambda method: _Ctx())
    targets = tuple(f"/probe/{index}" for index in range(16))
    with pytest.raises(AssertionError) as caught:
        _scan_all(targets, {"X-Route-Census": "row640"})
    message = str(caught.value)
    assert waits == [60, 60], (
        "precondition: the production 60s shard read must be what the "
        f"coordinator asked for; it asked for {waits}")
    assert "expected_shards=2 collected_shards=1" in message, message
    assert "shard 0 never reported" not in message, message
    return message


def test_row640_the_worker_arms_its_dumper_before_anything_else():
    """PRECONDITION for every stack below, asserted structurally.

    If arming were not the FIRST statement, a parent asking a not-yet-armed
    child for its stack would kill it with SIGUSR1's default disposition --
    the diagnosis destroying the evidence it came for.
    """
    import ast
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    worker = next(node for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name == "_scan_worker")
    body = [node for node in worker.body
            if not (isinstance(node, ast.Expr)
                    and isinstance(node.value, ast.Constant))]     # drop docstring
    first = body[0]
    assert (isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)
            and getattr(first.value.func, "id", None) == "_arm_shard_dumper"), (
        "the worker's first statement is %r, not the dumper arming"
        % ast.dump(first)[:200])


def test_row640_a_deadlocked_shard_is_named_as_parked_on_a_lock(monkeypatch):
    message = _row640_shortfall(monkeypatch, _row640_deadlocked_child)
    assert "shard 1 never reported: 8 route(s) starting /probe/1" in message, message
    assert "alive=True" in message and "exitcode=None" in message, message
    assert "stack: " in message, message
    assert "_row640_deadlocked_child" in message, message
    assert "_row640_slow_child" not in message, message


def test_row640_a_shard_slower_than_the_budget_is_named_as_still_working(
        monkeypatch):
    message = _row640_shortfall(monkeypatch, _row640_slow_child)
    assert "shard 1 never reported: 8 route(s) starting /probe/1" in message, message
    assert "alive=True" in message and "exitcode=None" in message, message
    assert "stack: " in message, message
    assert "_row640_slow_child" in message, message
    assert "_row640_deadlocked_child" not in message, message


def test_row640_a_shard_that_died_before_its_put_is_named_as_gone(monkeypatch):
    message = _row640_shortfall(
        monkeypatch, _row640_child_that_died_before_its_put)
    assert "shard 1 never reported: 8 route(s) starting /probe/1" in message, message
    assert "alive=False" in message and "exitcode=3" in message, message
    assert "the child is GONE" in message, message
    assert "stack: " not in message, message
    assert "_row640" not in message.split("stack unavailable")[-1], message


def test_row640_the_three_causes_do_not_share_one_message(monkeypatch):
    """THE A7 SELF-AUDIT. The defect is one message serving several causes, so
    the fix is only real if the three new messages cannot do the same. Driven
    through the same real fork boundary, back to back, in one process."""
    seen = {}
    for label, cause in (
            ("deadlocked", _row640_deadlocked_child),
            ("slower than the budget", _row640_slow_child),
            ("died before its put", _row640_child_that_died_before_its_put)):
        with monkeypatch.context() as patched:
            seen[label] = _row640_shortfall(patched, cause)
    assert len(set(seen.values())) == 3, (
        "two of the three shard failures share one message, which is the "
        "exact defect row 640 exists to end:\n"
        + "\n".join(f"  {k}: {v}" for k, v in seen.items()))
    # And "different" is not merely different noise: the discriminating fact
    # for each cause is present in that one and absent from the others.
    assert "_row640_deadlocked_child" in seen["deadlocked"]
    assert "_row640_deadlocked_child" not in seen["slower than the budget"]
    assert "_row640_slow_child" in seen["slower than the budget"]
    assert "_row640_slow_child" not in seen["deadlocked"]
    assert "exitcode=3" in seen["died before its put"]
    assert "exitcode=3" not in seen["deadlocked"]
    assert "exitcode=3" not in seen["slower than the budget"]


# ═══════════════════════════════════════════════════════════════════════════
# ROW 641 -- the third abstention mode: three endpoints that could not be
# requested at all.
#
#   runtime route scan UNKNOWN: endpoint request(s) unavailable:
#     /api/history (DatabaseError); /api/queue_templates/1 (DatabaseError);
#     /api/sites/fb3b0acf/queue/search (DatabaseError)
#
# Two findings, MEASURED on test5 2026-09-02 at HEAD 9e7031fb:
#
#   1. The refusal named the exception CLASS and nothing else, so a malformed
#      image, a locked database and an unopenable file were the same word --
#      three faults needing three different remedies.
#   2. Every one of 32 forked children inherits the parent's cached
#      `_DB_CONN_LOCAL.idle` handle, and because the pool keys its cache on
#      `os.getpid()` the child's first history query CLOSES that object --
#      instrumented in the child, `cx is <the parent's handle>` was True.
#      Thirty two processes closing one WAL connection the parent still owns
#      is the hazard SQLite documents for fork, and the three routes it
#      reached for are the three DB-reading routes in the eligible set.
# ═══════════════════════════════════════════════════════════════════════════

_ROW641_DB_ROUTES = ("/api/history", "/api/queue_templates/1")


class _FailingClient:
    """A test client whose GETs raise the sqlite faults row 641 collapsed."""

    def __init__(self, faults):
        self._faults = dict(faults)

    def get(self, path, headers=None):
        import sqlite3
        if path in self._faults:
            raise sqlite3.DatabaseError(self._faults[path])
        return _EmptyResponse()


class _EmptyResponse:
    def get_data(self, as_text=False):
        return "" if as_text else b""


def test_row641_an_unavailable_endpoint_carries_the_librarys_own_words(
        monkeypatch):
    """RED at HEAD 9e7031fb: `_scan_worker` recorded `type(exc).__name__`, so
    both faults below were the single word `DatabaseError` and the refusal
    could not tell a corrupt image from a locked file."""
    import sqlite3
    from bulk_downloader import app as A
    faults = {
        "/api/history": "database disk image is malformed",
        "/api/queue_templates/1": "database is locked",
    }
    client = _FailingClient(faults)
    monkeypatch.setattr(A.app, "test_client", lambda: client)
    monkeypatch.setattr(sys.modules[__name__], "_drop_inherited_db_handle",
                        lambda: "not exercised in this control")
    q = _SeqQueue()
    _scan_worker(tuple(faults) + ("/api/quiet",), {}, q, shard_id=0)

    assert len(q.items) == 1, q.items
    shard_id, (scanned, leaks, unavailable) = q.items[0]
    assert shard_id == 0 and scanned == 1 and leaks == [], q.items
    assert len(unavailable) == 2, unavailable
    recorded = dict(unavailable)
    assert recorded["/api/history"] == (
        "DatabaseError: database disk image is malformed"), recorded
    assert recorded["/api/queue_templates/1"] == (
        "DatabaseError: database is locked"), recorded
    assert len(set(recorded.values())) == 2, (
        "two different sqlite faults still share one word, which is the "
        f"collapse row 641 exists to end: {recorded}")

    # And the reconciler carries those words all the way into the refusal.
    with pytest.raises(AssertionError) as caught:
        _reconcile_scan_results(("/api/history",), [(scanned, leaks, unavailable)],
                                expected_shards=1)
    message = str(caught.value)
    assert "database disk image is malformed" in message, message
    assert "database is locked" in message, message
    assert isinstance(sqlite3.DatabaseError("x"), Exception)      # class anchor


def _row641_fork_probe(paths, headers, out_q, shard_id=0, dump_path=None):
    """Report what THIS child did with the connection handle it inherited."""
    from bulk_downloader import db as _db
    inherited = getattr(_db._DB_CONN_LOCAL, "idle", None)
    handle = inherited[1] if inherited else None
    closed_parents = []
    real_close = _db._close_history_conn
    _db._close_history_conn = lambda cx: (
        closed_parents.append(cx is handle), real_close(cx))[1]
    dropped = _drop_inherited_db_handle()
    from bulk_downloader import app as A
    client = A.app.test_client()
    errors = []
    for path in paths:
        try:
            client.get(path, headers=headers).get_data()
        except Exception as exc:
            errors.append((path, "%s: %s" % (type(exc).__name__, exc)))
    out_q.put((shard_id, (handle is not None, dropped,
                          any(closed_parents), errors)))


def test_row641_a_forked_child_never_closes_the_parents_db_handle():
    """THE MECHANISM, measured at the real fork boundary.

    Precondition asserted, not assumed: the child must actually INHERIT a
    cached handle, or this test would pass over an empty seam.
    """
    import multiprocessing as mp
    from bulk_downloader import app as A
    from bulk_downloader import db as _db
    with _client_seeded() as (client, headers, sid):
        routes = _ROW641_DB_ROUTES + (f"/api/sites/{sid}/queue/search",)
        for path in routes:
            client.get(path, headers=headers).get_data()   # cache a live handle
        assert getattr(_db._DB_CONN_LOCAL, "idle", None) is not None, (
            "precondition: the parent must hold a cached handle for the child "
            "to inherit, or this test proves nothing")
        assert set(routes).issubset(set(_scan_targets(A.app, sid))), (
            "precondition: the three routes row 641 named must be inside the "
            "scan's own denominator")

        ctx = mp.get_context("fork")
        out_q = ctx.Queue()
        proc = ctx.Process(target=_row641_fork_probe,
                           args=(routes, headers, out_q, 0, None), daemon=True)
        proc.start()
        try:
            shard_id, (inherited, dropped, closed_parent, errors) = out_q.get(
                timeout=60)
        finally:
            proc.terminate()
            proc.join(timeout=5)

    assert shard_id == 0
    assert inherited is True, (
        "precondition: the forked child did not inherit a handle at all")
    assert dropped == "dropped", dropped
    assert closed_parent is False, (
        "the child still closed the connection object its parent holds -- the "
        "SQLite-across-fork hazard row 641's DatabaseErrors came from")
    assert errors == [], (
        "the three routes row 641 named are still unreachable in a forked "
        f"child, and now they say why: {errors}")


def test_row641_the_named_routes_are_reachable_and_the_gate_still_bites():
    """NEGATIVE CONTROL for the reachability fix: once the three routes are
    reachable, an echoed secret on THOSE routes is still caught.

    Without this, "reachable" could have been bought by skipping them.
    """
    from bulk_downloader import app as A
    with _client_seeded() as (client, headers, sid):
        routes = _ROW641_DB_ROUTES + (f"/api/sites/{sid}/queue/search",)
        targets = _scan_targets(A.app, sid)
        assert set(routes).issubset(set(targets)), routes

        adapter = A.app.url_map.bind("localhost")
        endpoints = {}
        for path in routes:
            endpoint, _args = adapter.match(path)
            endpoints[path] = endpoint
        assert len(set(endpoints.values())) == 3, endpoints

        original = dict(A.app.view_functions)
        try:
            for endpoint in endpoints.values():
                A.app.view_functions[endpoint] = (
                    lambda *a, **k: (_SENT_PW, 200))
            scanned, leaks = _scan_all(targets, headers)
        finally:
            A.app.view_functions.clear()
            A.app.view_functions.update(original)

    assert scanned == len(targets) > 0, (scanned, len(targets))
    leaked = sorted({path for path, _sentinel in leaks})
    assert leaked == sorted(routes), (
        "the gate did not catch the seeded echo on exactly the three routes "
        f"row 641 named: caught {leaked}, expected {sorted(routes)}")
