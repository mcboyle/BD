"""IA-04: a fresh install's 503 is first-run vault setup, and the installer says so.

On a fresh install the master-password vault has no password yet, so
/api/health answers 503 degraded=credential_vault_uninitialized (row 413, by
design). spare8's install_service.sh ended with the generic "SERVING, but its
health check failed with HTTP 503" and no next step. The post-start region runs
here against a stub health server (the harness in
test_install_service_waits_for_serving) serving the exact fresh-install body.
"""
from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer

import test_install_service_waits_for_serving as svc

BD_GATE_SCOPE = "module"

# The credential half of spare8's fresh-install /api/health body (21:54Z).
_FRESH = {
    "ok": False,
    "degraded": "credential_vault_uninitialized",
    "db_ok": True,
    "vault_ready": False,
    "credentials": {
        "backend": "master_password", "is_initialized": False, "is_unlocked": False,
        "missing_count": 0, "ok": False, "reference_count": 0, "resolved_count": 0,
        "state": "uninitialized", "stored_count": 0, "unavailable_count": 0,
    },
}


def _tail() -> str:
    body = svc.INSTALL_SH.read_text(encoding="utf-8")
    idx = body.find(svc.START_ANCHOR)
    assert idx >= 0, "UNKNOWN: install_service.sh post-start anchor not found"
    return body[body.rfind("\n", 0, idx) + 1:]


def _serve(tmp_path, status: int, payload: dict) -> tuple[str, list[str]]:
    hits: list[str] = []

    class _Handler(svc._HealthHandler):
        status_code = status

        def do_GET(self):  # noqa: N802
            hits.append(self.path)
            self.payload = json.dumps(payload, separators=(",", ":")).encode()
            super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        proc = svc._run_tail(_tail(), tmp_path, server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
    return proc.stdout + proc.stderr, hits


def test_fresh_install_503_names_the_first_run_vault_step(tmp_path):
    out, hits = _serve(tmp_path, 503, _FRESH)
    assert hits and hits[0] == "/api/health"
    assert "health check failed" not in out, out
    assert "first-run setup" in out, out
    assert "no master password yet" in out, out
    assert "Settings -> Secrets" in out and "/api/secrets/unlock" in out, out
    assert "nothing answered" not in out


def test_another_503_still_reports_a_failed_health_check(tmp_path):
    # Negative control: only the uninitialized-vault shape gets the first-run text.
    other = dict(_FRESH, degraded="sites_config_unknown",
                 credentials=dict(_FRESH["credentials"], state="unlocked",
                                  is_initialized=True, is_unlocked=True, ok=True))
    out, _ = _serve(tmp_path, 503, other)
    assert "health check failed" in out and "HTTP 503" in out, out
    assert "first-run setup" not in out, out
