"""IA-05: a fresh per-run capture instance is ready while its vault awaits first use.

`capture.sh --parallel` gives each run a fresh state dir, whose master-password
vault has no password yet, so /api/health answers 503
degraded=credential_vault_uninitialized (row 413, by design) until capture.sh's
post-start unlock initializes it. install_capture_service.sh demanded HTTP 200
before returning, so on spare8 every start failed after 40 tries ("last HTTP
503") and the unlock that would have fixed it never ran. After a manual unlock
the same start passed -- the gate, not the app, was wrong.
"""
from __future__ import annotations

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import test_parallel_capture_services as cap

BD_GATE_SCOPE = "module"

_FRESH = {"ok": False, "degraded": "credential_vault_uninitialized", "db_ok": True,
          "credentials": {"state": "uninitialized", "is_initialized": False,
                          "is_unlocked": False, "reference_count": 0}}


def _start(tmp_path: Path, status: int, payload: dict | None) -> tuple[subprocess.CompletedProcess, list]:
    probes: list[tuple[str, int]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            probes.append((self.path, status))
            body = json.dumps(payload).encode() if payload is not None else b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    root = tmp_path / "installer"
    root.mkdir()
    env, _, _, installer = cap._installer_env(root, fake_curl=False)
    env["CAPTURE_SERVICE_READY_TRIES"] = "2"
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        state = tmp_path / "state"
        state.mkdir()
        proc = subprocess.run([str(installer), "start", "ia05", str(server.server_port), str(state)],
                              cwd=cap.REPO, env=env, capture_output=True, text=True, timeout=30)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
    return proc, probes


def test_uninitialized_vault_503_is_a_started_instance(tmp_path):
    proc, probes = _start(tmp_path, 503, _FRESH)
    out = proc.stdout + proc.stderr
    assert probes and probes[0][0] == "/api/health", probes
    assert proc.returncode == 0, out
    assert proc.stdout.lower().count("started") == 1, out
    assert "credential_vault_uninitialized" in proc.stdout, out


def test_another_503_is_still_refused(tmp_path):
    other = dict(_FRESH, degraded="sites_config_unknown")
    proc, _ = _start(tmp_path, 503, other)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "HTTP 503" in proc.stderr and "started" not in proc.stdout.lower()


def test_a_bodyless_503_is_still_refused(tmp_path):
    proc, _ = _start(tmp_path, 503, None)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "started" not in proc.stdout.lower()
