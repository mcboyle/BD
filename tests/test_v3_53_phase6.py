"""v3.53 (Phase 6) — regression tests for the new bdctl power-user
commands.

The bdctl command functions are thin: they call _request() (an HTTP
client) and format the response. We test them by driving bdctl as a
subprocess against a live in-process Flask server, asserting on exit
codes + stdout. This exercises the full path: argparse → command
function → _request → endpoint.

The custom test runner doesn't support module-scoped fixtures or
tmp_path_factory, so the server is a plain lazy singleton: started
once on first use by _get_server(), cached for the rest of the run.

v3.66.7: previously the server was launched via
`threading.Thread(target=lambda: app.app.run(...))` with the thread
marked daemon so it would die with the process. That's fine when this
file runs in isolation, but it leaks across the full suite: the
daemon thread holds a live reference to the imported
`bulk_downloader.app` module *and* the env vars set during boot
(BD_HOME, BD_URL, BD_DISABLE_KEEPALIVE). Downstream files that do
their own `sys.modules` purge then end up with two app instances in
memory and contradictory env, producing flakes only on serial runs
(nproc=1 sandbox) or `--workers=1`.

The v3.66.6 `test_extension_live.py` fix established the right
pattern: use `werkzeug.serving.make_server` with `port=0` (kernel-
picks-port at bind, no TOCTOU window), keep a reference to the server
object, and `.shutdown()` it from a module-scoped teardown. We do the
same here, snapshotting the complete execution-time module graph immediately
before server boot and restoring that exact graph after shutdown so modules
collected later cannot be orphaned.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

import pytest


_BDCTL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bdctl.py")
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Lazy singleton — populated on first _get_server() call.
_SERVER_BASE = None
_SERVER_HOME = None
_SERVER_OBJ = None   # v3.66.7: werkzeug BaseWSGIServer instance, so the
                     # session-scoped teardown can call .shutdown() and
                     # let the daemon thread exit cleanly before any
                     # sys.modules restoration.
_SERVER_THREAD = None
_SERVER_MODULES = None
_SERVER_ERROR = None   # row 414: the boot diagnosis, cached. See _get_server.


# Row 414 (v3.66.1363): the fixture's own first-use master password.
# Zero-entropy and published on purpose, per the CLAUDE.md A4 security-fixture
# convention — it unlocks nothing but the empty throwaway vault this file
# creates inside its own tempfile.mkdtemp() home, and _isolated_vault_path()
# refuses to send it anywhere else. /api/secrets/unlock enforces
# minimum_initial_length=8 on a first unlock, so it must stay >= 8 characters.
_VAULT_FIXTURE_PASSWORD = "phase6-fixture-vault-not-a-secret"


class ServerPreconditionError(RuntimeError):
    """A phase 6 precondition could not be established.

    Row 414: the message this replaces was the bare string "Flask server did
    not come up", raised for FOUR different conditions that a reader cannot
    tell apart — nothing listening, the app unreachable, the vault not
    initialized, and the app answering but degraded. Every raise below names
    which precondition failed and carries the measurement that decided it, so
    an unavailable server reports what is UNKNOWN about it rather than a
    generic failure.
    """


def _free_port():
    """Bind port 0, read the kernel's choice, close. The port is then free.

    v3.66.7 retired this from the boot path — make_server(port=0) is atomic and
    has no bind->close->re-bind TOCTOU window. Row 414 gives it a second, honest
    use: the negative control for _wait_for_listener needs a port that nothing
    is listening on, and that test asserts the refusal before relying on it.
    """
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _probe_http(url, method="GET", body=None, timeout=5):
    """One HTTP request. Returns (status, raw_body, transport_error).

    THIS SEPARATION IS THE ROW 414 FIX. urllib raises HTTPError for a 503 and
    URLError for nothing-listening, and the readiness loop this replaces called
    urlopen inside a bare ``except Exception`` — so a server that answered
    "503 credential_vault_uninitialized" eighty times running was reported as a
    server that never came up. A 4xx/5xx is a STATUS: the request reached a WSGI
    app and that app decided. ``status`` is None only when nothing answered.
    """
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), None
    except urllib.error.HTTPError as e:
        # An HTTP verdict, not a transport failure.
        try:
            raw = e.read()
        except Exception:
            raw = b""
        return e.code, raw, None
    except Exception as e:
        # Refused / reset / timed out / never resolved: no app was reached.
        return None, b"", e


def _wait_for_listener(host, port, thread=None, attempts=50, delay=0.1):
    """Block until the port ACCEPTS A TCP CONNECTION. Liveness only.

    Deliberately does not speak HTTP. Socket readiness and application health
    are two different questions, and row 414 is what happens when one probe is
    asked to answer both. This is the same shape every sibling live-server file
    already uses (test_extension_live.py, test_e2e_smoke.py,
    test_v3_60_phase12.py) — this file was the only one that did not.

    Returns the attempt number that succeeded (1-based), so a caller can assert
    a nonzero, bounded wait rather than trusting that the loop ran at all.
    """
    last = None
    started = time.time()
    for attempt in range(attempts):
        try:
            socket.create_connection((host, port), timeout=0.2).close()
            return attempt + 1
        except OSError as e:
            last = e
            # Poll interval, not a settle sleep: the loop exits on the
            # condition the moment the kernel accepts.
            time.sleep(delay)
    alive = None if thread is None else thread.is_alive()
    raise ServerPreconditionError(
        f"phase6 precondition NOT_LISTENING: {host}:{port} never accepted a "
        f"TCP connection in {attempts} attempts "
        f"({time.time() - started:.1f}s). serve_forever thread alive={alive}; "
        f"last error {type(last).__name__}: {last}")


def _isolated_vault_path(home):
    """Absolute path the running app will use for its credential vault.

    ``secrets_store.SECRETS_FILE`` is a RELATIVE Path bound at import and
    resolved against the process cwd at every use, and this fixture chdirs into
    its own temp home. That is fine — until it is not: if the path ever
    resolved outside the temp home, the first-unlock below would aim a
    throwaway password at the OPERATOR'S vault (a real secrets.json exists at
    the repository root on the deployed host), which is a wrong-password
    attempt against live credentials plus an escalating auth-throttle lockout.
    So this is a hard refusal with a named cause, never a warning.
    """
    from bulk_downloader import secrets_store as ss
    rel = str(ss.SECRETS_FILE)
    home_real = os.path.realpath(home)
    if os.path.isabs(rel):
        raise ServerPreconditionError(
            f"phase6 precondition VAULT_NOT_ISOLATED: "
            f"secrets_store.SECRETS_FILE is absolute ({rel!r}); the fixture "
            f"will not initialize a vault it cannot prove lives inside its own "
            f"temp home {home_real!r}")
    cwd_real = os.path.realpath(os.getcwd())
    if cwd_real != home_real:
        raise ServerPreconditionError(
            f"phase6 precondition VAULT_NOT_ISOLATED: cwd is {cwd_real!r}, not "
            f"the fixture home {home_real!r}; a relative vault path would "
            f"resolve outside the isolated home")
    target = os.path.realpath(os.path.join(home_real, rel))
    if os.path.commonpath([home_real, target]) != home_real:
        raise ServerPreconditionError(
            f"phase6 precondition VAULT_NOT_ISOLATED: {rel!r} resolves to "
            f"{target!r}, which escapes the fixture home {home_real!r}")
    return target


def _initialize_vault(base, home):
    """Commit a master password to the fixture's OWN empty vault.

    WHY THE FIXTURE MUST DO THIS AT ALL (row 414's root cause). v3.66.1359
    (87bb9c9e, row 402) changed credential_health: an uninitialized vault used
    to be ``ok=True`` and is now ``ok=False`` with
    ``degraded=credential_vault_uninitialized``, because a vault with no
    durable password commitment would accept a new first password after a
    restart. /api/health therefore answers 503 on a fresh BD_HOME and
    ``bdctl health`` exits 1. The tests below assert a HEALTHY server, so the
    fixture builds one through the product's own first-use path rather than
    relaxing the assertion.

    ONE attempt, never a retry loop: /api/secrets/unlock shares an escalating
    auth-throttle with change_password, so retrying would convert a single
    clear failure into a 429 that names nothing.

    ``initialized_now`` is the load-bearing assertion. It is True only when
    this call CREATED the vault; opening an already-initialized one returns
    False. That single field is what proves the fixture built its own subject
    instead of finding someone else's.
    """
    vault = _isolated_vault_path(home)
    if os.path.exists(vault):
        raise ServerPreconditionError(
            f"phase6 precondition VAULT_NOT_FRESH: {vault!r} already exists; "
            f"the fixture only ever initializes a vault it creates itself")
    status, raw, err = _probe_http(
        base + "/api/secrets/unlock", method="POST",
        body={"password": _VAULT_FIXTURE_PASSWORD})
    if err is not None:
        raise ServerPreconditionError(
            f"phase6 precondition VAULT_UNREACHABLE: POST /api/secrets/unlock "
            f"never reached the app although the port accepted a connection: "
            f"{type(err).__name__}: {err}")
    try:
        payload = json.loads(raw)
    except Exception:
        raise ServerPreconditionError(
            f"phase6 precondition VAULT_UNKNOWN: /api/secrets/unlock returned "
            f"HTTP {status} with a non-JSON body {raw[:200]!r}")
    if status != 200 or payload.get("initialized_now") is not True:
        raise ServerPreconditionError(
            f"phase6 precondition VAULT_NOT_INITIALIZED: "
            f"/api/secrets/unlock returned HTTP {status} "
            f"state={payload.get('state')!r} "
            f"initialized_now={payload.get('initialized_now')!r} "
            f"is_initialized={payload.get('is_initialized')!r} "
            f"is_unlocked={payload.get('is_unlocked')!r} "
            f"error={payload.get('error')!r}")
    if not os.path.exists(vault):
        raise ServerPreconditionError(
            f"phase6 precondition VAULT_NOT_WRITTEN: /api/secrets/unlock "
            f"reported initialized_now=True but {vault!r} does not exist, so "
            f"nothing durable commits the password")
    return vault


def _require_healthy(base):
    """GET /api/health ONCE and demand 200 + ok. Never re-polls.

    By the time this runs the socket has already accepted a connection and the
    vault has been initialized, so a degraded answer is a deterministic verdict
    about the application rather than a startup race. Polling it would spend
    eight seconds restating one fact — which is exactly what row 414 measured:
    fifteen nodes, 149 seconds, and not one of them said why.

    The message names the status, the ``degraded`` marker and the credential
    state. That is the information the old generic string destroyed.
    """
    status, raw, err = _probe_http(base + "/api/health", timeout=5)
    if err is not None:
        raise ServerPreconditionError(
            f"phase6 precondition HEALTH_UNREACHABLE: GET /api/health never "
            f"reached the app although the port accepted a connection: "
            f"{type(err).__name__}: {err}")
    try:
        payload = json.loads(raw)
    except Exception:
        payload = {}
    if status != 200 or payload.get("ok") is not True:
        creds = payload.get("credentials") or {}
        raise ServerPreconditionError(
            f"phase6 precondition UNHEALTHY: GET /api/health returned HTTP "
            f"{status} ok={payload.get('ok')!r} "
            f"degraded={payload.get('degraded')!r} "
            f"credentials.state={creds.get('state')!r} "
            f"db_ok={payload.get('db_ok')!r}; body={raw[:400]!r}")
    return payload


def _get_server():
    """Boot the Flask server once; return its base URL. Idempotent.

    Three preconditions, each measured separately and each with its own named
    failure: the port ACCEPTS a connection, the vault is INITIALIZED by this
    fixture, and /api/health answers 200/ok. Row 414: the previous boot asked
    one HTTP probe to stand for all three and could report none of them.
    """
    global _SERVER_BASE, _SERVER_HOME, _SERVER_OBJ, _SERVER_THREAD
    global _SERVER_MODULES, _SERVER_ERROR
    if _SERVER_BASE is not None:
        _pin_cwd()
        return _SERVER_BASE
    if _SERVER_ERROR is not None:
        # Boot is a module singleton: it failed once and would fail the same
        # way for every remaining node. Re-raising the ORIGINAL diagnosis keeps
        # each node naming the real cause; re-attempting it is what turned one
        # fact into 149 seconds of futile retries.
        raise ServerPreconditionError(
            f"phase6 server boot already failed: {_SERVER_ERROR}")
    tmp = tempfile.mkdtemp(prefix="bd_phase6_")
    _SERVER_HOME = tmp
    os.environ["BD_HOME"] = tmp
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    os.chdir(tmp)
    if _SERVER_MODULES is None:
        _SERVER_MODULES = {k: v for k, v in sys.modules.items()
                           if k.startswith("bulk_downloader")}
    for mod in list(sys.modules):
        if mod.startswith("bulk_downloader"):
            del sys.modules[mod]
    import bulk_downloader.app as a
    from werkzeug.serving import make_server
    # v3.66.7: port=0 → kernel assigns at bind; no TOCTOU window
    # between port selection and server start.
    _SERVER_OBJ = make_server("127.0.0.1", 0, a.app, threaded=True)
    port = _SERVER_OBJ.server_port
    _SERVER_THREAD = threading.Thread(
        target=_SERVER_OBJ.serve_forever, daemon=True)
    _SERVER_THREAD.start()
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_for_listener("127.0.0.1", port, thread=_SERVER_THREAD)
        _initialize_vault(base, tmp)
        _require_healthy(base)
    except ServerPreconditionError as e:
        _SERVER_ERROR = e
        raise
    os.environ["BD_URL"] = base
    _SERVER_BASE = base
    return base


# v3.66.7: capture the keys the boot pollutes so the session teardown
# can restore them. Keeping this at module load (rather than inside
# the fixture) means we capture pre-boot state even if some earlier
# test already mutated the env — we restore to what it was at *this*
# file's import time, which is what the rest of the suite expects.
_ENV_KEYS_TO_ISOLATE = ("BD_HOME", "BD_URL", "BD_DISABLE_KEEPALIVE",
                        "BD_INSTALL_DIR")
_ENV_SNAPSHOT_AT_IMPORT = {
    k: os.environ.get(k) for k in _ENV_KEYS_TO_ISOLATE
}
_CWD_AT_IMPORT = os.getcwd()


@pytest.fixture(scope="module", autouse=True)
def _cleanup_singleton_server():
    """Module-scoped teardown: shutdown the in-process server, join
    the thread, then restore env + sys.modules to pre-boot state so
    downstream test files don't inherit BD_HOME/BD_URL/etc.

    v3.66.21: scope changed from "session" to "module". Session scope
    only ran the teardown at the very end of the entire pytest run,
    leaving `sys.modules['bulk_downloader.app']` pointing at this
    file's freshly-reimported copy for the duration of every test that
    ran AFTER this file. That broke tests like
    `test_v3_62_2_login_fallback.py` which import bulk_downloader
    modules at file-load time and rely on them sharing identity with
    what their patches and mocks target. Module scope runs the
    teardown after the last test in THIS file completes, which is
    the contract the downstream tests need.

    The order matters: shutdown FIRST so the daemon thread releases
    its reference to bulk_downloader.app, THEN restore sys.modules.
    If we restored modules with the thread still running, the thread
    would hold a stale module instance.
    """
    yield
    global _SERVER_OBJ, _SERVER_THREAD, _SERVER_BASE, _SERVER_HOME, _SERVER_MODULES
    global _SERVER_ERROR
    _SERVER_ERROR = None   # row 414: the cached diagnosis is per-module too
    if _SERVER_OBJ is not None:
        try:
            _SERVER_OBJ.shutdown()
        except Exception:
            pass
    if _SERVER_THREAD is not None:
        _SERVER_THREAD.join(timeout=5)
    _SERVER_OBJ = None
    _SERVER_THREAD = None
    _SERVER_BASE = None
    _SERVER_HOME = None
    # Restore env
    for k, v in _ENV_SNAPSHOT_AT_IMPORT.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    # Restore sys.modules: drop anything new under bulk_downloader,
    # put back the complete graph captured immediately before server boot.
    if _SERVER_MODULES is not None:
        for m in [m for m in list(sys.modules) if m.startswith("bulk_downloader")]:
            del sys.modules[m]
        sys.modules.update(_SERVER_MODULES)
        _SERVER_MODULES = None
    # Restore cwd
    try:
        os.chdir(_CWD_AT_IMPORT)
    except OSError:
        pass


def _pin_cwd():
    """The test runner chdir's to a fresh temp dir per test. The live
    server resolves its DB path relative to cwd, so before every request
    we restore cwd (and BD_HOME) to the server's home — otherwise the
    request hits an empty DB and 500s with 'no such table'."""
    if _SERVER_HOME:
        os.environ["BD_HOME"] = _SERVER_HOME
        try:
            os.chdir(_SERVER_HOME)
        except OSError:
            pass


def _bdctl(*args, stdin=None):
    """Run bdctl as a subprocess, return (rc, stdout, stderr)."""
    _get_server()  # ensure server up + BD_URL set
    _pin_cwd()
    r = subprocess.run([sys.executable, _BDCTL, *args],
                       capture_output=True, text=True,
                       env=dict(os.environ), timeout=30, cwd=_REPO,
                       input=stdin)
    return r.returncode, r.stdout, r.stderr


def _api_post(path, body):
    base = _get_server()
    _pin_cwd()
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(),
        method="POST", headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


# ── health ──────────────────────────────────────────────────────────────

def test_health_exits_zero_when_ok():
    rc, out, err = _bdctl("health")
    assert rc == 0, f"health rc={rc} stderr={err}"
    assert "OK" in out
    assert "version" in out


def test_health_shows_version():
    rc, out, err = _bdctl("health")
    assert any(ch.isdigit() for ch in out)


# ── library ─────────────────────────────────────────────────────────────

def test_library_stats_runs():
    rc, out, err = _bdctl("library", "stats")
    assert rc == 0, f"stderr={err}"
    assert "total files" in out


def test_library_scan_status_when_never_run():
    rc, out, err = _bdctl("library", "scan-status")
    assert rc == 0
    assert "scan" in out.lower() or "running" in out.lower()


def test_library_subcommand_requires_subsub():
    """`bdctl library` with no subcommand should print help, exit 1."""
    rc, out, err = _bdctl("library")
    assert rc == 1


# ── audit ───────────────────────────────────────────────────────────────

def test_audit_runs():
    rc, out, err = _bdctl("audit", "--limit", "5")
    assert rc == 0, f"stderr={err}"


def test_audit_shows_created_site():
    """Create a site → it should appear in the audit log."""
    _api_post("/api/sites", {"name": "AuditProbe"})
    rc, out, err = _bdctl("audit", "--limit", "20")
    assert rc == 0
    assert "create" in out and "sites_config" in out


# ── site export / import / validate ─────────────────────────────────────

def test_site_export_to_stdout():
    r = _api_post("/api/sites", {"name": "ExportCLI"})
    sid = r["id"]
    rc, out, err = _bdctl("site", "export", sid)
    assert rc == 0, f"stderr={err}"
    env = json.loads(out)
    assert env["schema"]
    assert "config" in env


def test_site_export_strips_secrets_by_default():
    r = _api_post("/api/sites", {"name": "SecretCLI", "password": "hunter2"})
    sid = r["id"]
    rc, out, err = _bdctl("site", "export", sid)
    assert rc == 0
    env = json.loads(out)
    assert "password" not in env["config"]


def test_site_validate_via_stdin_valid():
    cfg = json.dumps({"name": "ValidCLI",
                      "login_url": "https://x.com/login"})
    rc, out, err = _bdctl("site", "validate", "-", stdin=cfg)
    assert rc == 0, f"stderr={err}"
    assert "VALID" in out


def test_site_validate_via_stdin_invalid():
    cfg = json.dumps({"name": "", "wait": 9999})
    rc, out, err = _bdctl("site", "validate", "-", stdin=cfg)
    assert rc == 1
    assert "INVALID" in out or "ERROR" in out


def test_site_import_via_stdin():
    """Export a site, then import it back through bdctl."""
    r = _api_post("/api/sites", {"name": "ImportSource"})
    sid = r["id"]
    rc, exported, err = _bdctl("site", "export", sid)
    assert rc == 0
    rc2, out2, err2 = _bdctl("site", "import", "-", stdin=exported)
    assert rc2 == 0, f"stderr={err2}"
    assert "imported as new site" in out2


def test_site_subcommand_requires_subsub():
    rc, out, err = _bdctl("site")
    assert rc == 1


# ── pause-all / resume-all ──────────────────────────────────────────────

def test_pause_all_and_resume_all():
    _api_post("/api/sites", {"name": "PauseTarget"})
    rc, out, err = _bdctl("pause-all")
    assert rc == 0, f"stderr={err}"
    assert "paused" in out.lower()
    rc, out, err = _bdctl("resume-all")
    assert rc == 0, f"stderr={err}"
    assert "resumed" in out.lower()


# ── parser registration ─────────────────────────────────────────────────

def test_all_phase6_commands_registered():
    """`bdctl --help` should list every new command."""
    rc, out, err = _bdctl("--help")
    for cmd in ("health", "pause-all", "resume-all", "audit",
                "library", "site"):
        assert cmd in out, f"{cmd} missing from bdctl --help"


# ── command-palette entries ─────────────────────────────────────────────


# ── row 414: controls for the boot preconditions themselves ─────────────
#
# These four nodes never touch the singleton. They drive the extracted
# helpers against purpose-built stub servers so that every outcome of the
# boot sequence is proven REACHABLE and proven DISTINCT — the two refusals
# that row 414 collapsed into one string must not be able to launder into
# each other again.


class _FixedStatusApp:
    """Minimal WSGI app answering one fixed status + body, counting calls."""

    def __init__(self, status, payload):
        self.status = status
        self.raw = json.dumps(payload).encode()
        self.hits = 0

    def __call__(self, environ, start_response):
        self.hits += 1
        start_response(self.status,
                       [("Content-Type", "application/json"),
                        ("Content-Length", str(len(self.raw)))])
        return [self.raw]


def _serve_stub(app):
    """Run ``app`` on a kernel-chosen loopback port. Returns (base, server,
    thread); the caller shuts it down."""
    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return f"http://127.0.0.1:{srv.server_port}", srv, th


def test_listener_wait_names_a_port_that_never_accepts():
    """Negative control for the transport refusal.

    NOT_LISTENING must be reachable, must name the endpoint it measured, and
    must not borrow the health verdict's vocabulary.
    """
    port = _free_port()   # bound, read, closed — nothing listens on it now
    # Precondition, asserted rather than assumed: the port really refuses.
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
        pytest.fail(f"precondition failed: 127.0.0.1:{port} accepted a "
                    f"connection, so this test cannot measure a refusal")
    except OSError:
        pass
    with pytest.raises(ServerPreconditionError) as excinfo:
        _wait_for_listener("127.0.0.1", port, attempts=3, delay=0.01)
    msg = str(excinfo.value)
    assert "NOT_LISTENING" in msg, msg
    assert str(port) in msg, msg
    assert "3 attempts" in msg, msg
    assert "UNHEALTHY" not in msg, msg
    assert "Flask server did not come up" not in msg, msg


def test_readiness_and_health_are_separate_verdicts():
    """Row 414's exact defect shape, replayed against a stub.

    A server that ANSWERS 503 IS UP. The probe this replaces called urlopen
    inside ``except Exception``, so HTTPError(503) was indistinguishable from
    connection-refused and the file reported "Flask server did not come up"
    about a server that had answered eighty times. Here the liveness wait must
    SUCCEED on that same server and the health check must FAIL by name.
    """
    stub = _FixedStatusApp("503 SERVICE UNAVAILABLE", {
        "ok": False,
        "degraded": "credential_vault_uninitialized",
        "credentials": {"state": "uninitialized", "ok": False},
        "db_ok": True,
        "version": "0.0.0-stub",
    })
    base, srv, th = _serve_stub(stub)
    try:
        # LIVENESS: accepted, so the wait succeeds on its first attempt and
        # makes ZERO application requests — proof it does not speak HTTP.
        assert _wait_for_listener("127.0.0.1", srv.server_port,
                                  thread=th) == 1
        assert stub.hits == 0, f"liveness probe issued {stub.hits} requests"
        # HEALTH: the same live server is a deterministic, named failure.
        with pytest.raises(ServerPreconditionError) as excinfo:
            _require_healthy(base)
        assert stub.hits == 1, (
            f"health check issued {stub.hits} requests; it must ask once and "
            f"report, never poll a deterministic verdict")
        msg = str(excinfo.value)
        assert "UNHEALTHY" in msg, msg
        assert "503" in msg, msg
        assert "credential_vault_uninitialized" in msg, msg
        assert "uninitialized" in msg, msg
        assert "NOT_LISTENING" not in msg, msg
    finally:
        srv.shutdown()
        th.join(timeout=5)


def test_health_precondition_accepts_a_genuinely_ok_server():
    """Positive control: _require_healthy is not simply always-refuse.

    Without this, the negative control above would pass just as well against a
    helper that raised unconditionally.
    """
    stub = _FixedStatusApp("200 OK", {"ok": True, "version": "0.0.0-stub",
                                      "db_ok": True})
    base, srv, th = _serve_stub(stub)
    try:
        payload = _require_healthy(base)
        assert payload["ok"] is True
        assert payload["version"] == "0.0.0-stub"
        assert stub.hits == 1
    finally:
        srv.shutdown()
        th.join(timeout=5)


def test_vault_isolation_refuses_a_path_outside_the_fixture_home():
    """The fixture must never aim its throwaway password at a vault it does
    not own. A real secrets.json exists at the repository root on the deployed
    host, and _isolated_vault_path is the only thing standing between a
    mis-set cwd and a wrong-password attempt against the operator's
    credentials plus an auth-throttle lockout.
    """
    home = tempfile.mkdtemp(prefix="bd_phase6_vaultguard_")
    other = tempfile.mkdtemp(prefix="bd_phase6_elsewhere_")
    from bulk_downloader import secrets_store as ss
    saved_cwd = os.getcwd()
    saved_secrets = ss.SECRETS_FILE
    try:
        # Positive control first: the intended arrangement resolves inside the
        # home, so the refusals below are about the arrangement, not the guard.
        os.chdir(home)
        resolved = _isolated_vault_path(home)
        assert resolved == os.path.join(os.path.realpath(home),
                                        str(saved_secrets))
        # cwd elsewhere: a relative vault path would land outside the home.
        os.chdir(other)
        with pytest.raises(ServerPreconditionError) as excinfo:
            _isolated_vault_path(home)
        assert "VAULT_NOT_ISOLATED" in str(excinfo.value)
        assert os.path.realpath(other) in str(excinfo.value)
        # An absolute SECRETS_FILE is refused even from the right cwd.
        os.chdir(home)
        ss.SECRETS_FILE = os.path.join(other, "secrets.json")
        with pytest.raises(ServerPreconditionError) as excinfo:
            _isolated_vault_path(home)
        assert "VAULT_NOT_ISOLATED" in str(excinfo.value)
        assert "is absolute" in str(excinfo.value)
    finally:
        ss.SECRETS_FILE = saved_secrets
        os.chdir(saved_cwd)


