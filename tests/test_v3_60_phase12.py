"""v3.60 (Phase 12) — regression tests for the power-user backlog.

Covers #98 — universal bdctl `--json` output mode. Tests drive bdctl
as a subprocess against a live in-process server, matching the Phase 6
test pattern.

v3.66.7: same fix as test_v3_53_phase6.py — switch from
`threading.Thread(target=app.run)` to `werkzeug.serving.make_server`
with `port=0` + explicit `.shutdown()` from a session-scoped teardown.
The class-level `_server_started`/`_port`/`_env` singleton stays;
only the boot mechanics + teardown change. See the phase6 docstring
for the full rationale.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest


_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BDCTL = os.path.join(_REPO, "bdctl.py")


# v3.66.7: capture env/sys.modules/cwd at import time so the session-
# scoped teardown can restore them after the in-process server is
# shut down cleanly.
_ENV_KEYS_TO_ISOLATE = ("BD_HOME", "BD_URL", "BD_DISABLE_KEEPALIVE",
                        "BD_INSTALL_DIR")
_ENV_SNAPSHOT_AT_IMPORT = {
    k: os.environ.get(k) for k in _ENV_KEYS_TO_ISOLATE
}
_BD_MODULES_AT_IMPORT = {
    m: sys.modules[m] for m in list(sys.modules)
    if m.startswith("bulk_downloader")
}
_CWD_AT_IMPORT = os.getcwd()


class TestBdctlJson:
    """#98 — bdctl --json. One live server shared across the class."""

    _server_started = False
    _port = None
    _env = None
    _server_obj = None    # v3.66.7: werkzeug BaseWSGIServer for clean
                          # shutdown from the session teardown.
    _server_thread = None

    @classmethod
    def _ensure_server(cls):
        if cls._server_started:
            return
        os.environ["BD_DISABLE_KEEPALIVE"] = "1"
        import tempfile
        tmp = tempfile.mkdtemp()
        os.environ["BD_HOME"] = tmp
        # The class-level server outlives each function fixture's cwd.  Pin the
        # configured runtime to its own absolute install path before importing
        # app so later tests cannot make the live server appear to switch from
        # one relative sites_config.json identity to another.
        os.environ["BD_INSTALL_DIR"] = tmp
        os.chdir(tmp)
        saved_modules = {k: v for k, v in sys.modules.items()
                         if k.startswith("bulk_downloader")}
        for mod in list(sys.modules):
            if mod.startswith("bulk_downloader"):
                del sys.modules[mod]
        import bulk_downloader.app as a
        # This server intentionally survives function-scoped fixtures that
        # replace the process environment.  Make its config identity an
        # explicit module binding rather than an auto-resolved environment
        # candidate, exactly as a long-lived embedding caller must do.
        a.SITES_FILE = Path(tmp) / "sites_config.json"
        from werkzeug.serving import make_server
        # v3.66.7: kernel-picks-port atomically at bind. Removes the
        # bind→close→re-bind TOCTOU window the old _free_port-style
        # block had.
        cls._server_obj = make_server("127.0.0.1", 0, a.app,
                                       threaded=True)
        cls._port = cls._server_obj.server_port
        cls._server_thread = threading.Thread(
            target=cls._server_obj.serve_forever, daemon=True)
        cls._server_thread.start()
        # Wait for port to actually accept connections.
        for _ in range(50):
            try:
                s = socket.create_connection(("127.0.0.1", cls._port),
                                              timeout=0.2)
                s.close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Phase 12 server did not come up")
        cls._env = dict(os.environ,
                        BD_URL=f"http://127.0.0.1:{cls._port}")
        cls._server_started = True
        for _bd_mod in [_m for _m in list(sys.modules)
                        if _m.startswith("bulk_downloader")]:
            del sys.modules[_bd_mod]
        sys.modules.update(saved_modules)

    def _run(self, args):
        self._ensure_server()
        r = subprocess.run([sys.executable, _BDCTL] + args,
                           capture_output=True, text=True,
                           env=self._env, timeout=30, cwd=_REPO)
        return r.returncode, r.stdout.strip(), r.stderr.strip()

    def test_health_json_is_valid_json(self):
        rc, out, err = self._run(["health", "--json"])
        parsed = json.loads(out)  # raises if not JSON
        assert "version" in parsed

    def test_health_json_exit_code_preserved(self):
        """--json must not change the 0/1 health exit semantics."""
        rc, out, err = self._run(["health", "--json"])
        assert rc in (0, 1)

    def test_pause_all_accepts_json(self):
        """pause-all had no --json before Phase 12 — now it does."""
        rc, out, err = self._run(["pause-all", "--json"])
        parsed = json.loads(out)
        assert "ok" in parsed

    def test_resume_all_accepts_json(self):
        rc, out, err = self._run(["resume-all", "--json"])
        parsed = json.loads(out)
        assert "ok" in parsed

    def test_audit_accepts_json(self):
        rc, out, err = self._run(["audit", "--json"])
        # audit --json emits the raw response dict
        parsed = json.loads(out)
        assert isinstance(parsed, dict)

    def test_json_flag_universally_accepted(self):
        """Every subcommand must at least *accept* --json without an
        'unrecognized arguments' error (exit 2 from argparse)."""
        for cmd in ("sites", "health", "pause-all", "resume-all",
                    "audit", "doctor"):
            rc, out, err = self._run([cmd, "--json"])
            assert "unrecognized arguments" not in err, (
                f"{cmd} rejected --json")

    def test_doctor_json_is_valid(self):
        rc, out, err = self._run(["doctor", "--json"])
        parsed = json.loads(out)
        # doctor --json emits the report dict
        assert "checks" in parsed or "summary" in parsed


@pytest.fixture(scope="session", autouse=True)
def _cleanup_phase12_singleton_server():
    """v3.66.7 — session-scoped teardown: shutdown the in-process
    server, join the thread, then restore env + sys.modules to pre-
    boot state so downstream test files don't inherit BD_HOME/BD_URL.

    Same shape as test_v3_53_phase6.py's fixture. Order is critical:
    shutdown the server FIRST (so its daemon thread releases the
    bulk_downloader.app reference), THEN restore sys.modules.
    """
    yield
    if TestBdctlJson._server_obj is not None:
        try:
            TestBdctlJson._server_obj.shutdown()
        except Exception:
            pass
    if TestBdctlJson._server_thread is not None:
        TestBdctlJson._server_thread.join(timeout=5)
    TestBdctlJson._server_obj = None
    TestBdctlJson._server_thread = None
    TestBdctlJson._server_started = False
    TestBdctlJson._port = None
    TestBdctlJson._env = None
    # Restore env
    for k, v in _ENV_SNAPSHOT_AT_IMPORT.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    # Restore sys.modules
    for m in [m for m in list(sys.modules)
              if m.startswith("bulk_downloader")]:
        del sys.modules[m]
    for m, mod in _BD_MODULES_AT_IMPORT.items():
        sys.modules[m] = mod
    # Restore cwd
    try:
        os.chdir(_CWD_AT_IMPORT)
    except OSError:
        pass


class TestMaybeJsonHelper:
    """Unit-test the _maybe_json helper directly."""

    def test_maybe_json_returns_false_without_flag(self):
        sys.path.insert(0, _REPO)
        import bdctl

        class FakeArgs:
            json = False
        assert bdctl._maybe_json({"a": 1}, FakeArgs()) is False

    def test_maybe_json_returns_true_with_flag(self):
        sys.path.insert(0, _REPO)
        import bdctl
        import contextlib
        import io

        class FakeArgs:
            json = True
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = bdctl._maybe_json({"a": 1}, FakeArgs())
        assert result is True
        assert json.loads(buf.getvalue()) == {"a": 1}

    def test_maybe_json_handles_missing_attr(self):
        """An args object with no .json attribute → treated as False."""
        sys.path.insert(0, _REPO)
        import bdctl

        class FakeArgs:
            pass
        assert bdctl._maybe_json({"a": 1}, FakeArgs()) is False
