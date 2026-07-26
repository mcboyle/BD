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


def _free_port():
    """v3.66.7: retained for any external/back-compat caller. Nothing
    in this file calls it any more — the make_server(port=0) path is
    atomic and avoids the bind→close→re-bind TOCTOU window the old
    helper had. Same shape as the no-op stub kept in
    test_extension_live.py after the v3.66.6 fix."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _get_server():
    """Boot the Flask server once; return its base URL. Idempotent."""
    global _SERVER_BASE, _SERVER_HOME, _SERVER_OBJ, _SERVER_THREAD, _SERVER_MODULES
    if _SERVER_BASE is not None:
        _pin_cwd()
        return _SERVER_BASE
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
    for _ in range(80):
        try:
            urllib.request.urlopen(base + "/api/health", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    else:
        raise RuntimeError("Flask server did not come up")
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


