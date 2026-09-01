"""Row 467 -- `bdctl health --json` must answer, not exit, on a non-200.

``_request`` raised a bare ``SystemExit`` on ANY urllib ``HTTPError``, so
``bdctl health --json`` printed NOTHING to stdout against a 503 and
``json.loads("")`` failed.  /api/health answers 503 with a FULL body whenever
the probe is degraded, and the commonest degradation is
``credential_vault_locked`` -- the state of every host after every restart
until a human unlocks it.  So the operator's own CLI was unusable on exactly
the hosts an operator most needs to inspect, and its failure was a bare
SystemExit rather than the structured answer the flag promises.

WHAT IS DELIBERATELY NOT DONE HERE.  ``app_health``'s 503 is CORRECT: a
degraded probe must not answer 200.  Fixing a client by degrading a server's
honest refusal is the exact inversion this contract exists to prevent, so
nothing in this file touches the server, and
``test_the_server_still_refuses_with_503`` pins that.

THE THREE OUTCOMES STAY THREE (CLAUDE.md A7).  A 503 whose body names the
vault, a 500 carrying some other error, and a server that never answered lead
to different actions -- read the refusal, repair the service, or start it --
so the JSON names which happened rather than collapsing them.
"""
from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path


BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_BDCTL = _REPO / "bdctl.py"

# A locked-vault 503 as bulk_downloader/app_health.py:api_health actually
# renders one: ok False, a named `degraded`, and the vault's own block.
_LOCKED_VAULT_503 = {
    "ok": False,
    "version": "3.66.0-row467",
    "degraded": "credential_vault_locked",
    "vault_ready": False,
    "credentials": {"state": "locked", "ok": False, "missing_count": 0},
    "db_ok": True,
    "queue_depth": 0,
    "active_downloads": 0,
    "sites_loaded": 0,
}

_HEALTHY_200 = {
    "ok": True,
    "version": "3.66.0-row467",
    "vault_ready": True,
    "db_ok": True,
    "queue_depth": 0,
    "active_downloads": 0,
    "sites_loaded": 2,
    "uptime_s": 12.5,
}

_GENUINE_ERROR_500 = {"ok": False, "error": "unhandled: KeyError('runners')"}


class _Handler(http.server.BaseHTTPRequestHandler):
    """Serves one scripted (status, body) for every path, and COUNTS."""

    status = 503
    body: object = _LOCKED_VAULT_503
    hits: list[str] = []

    def do_GET(self):  # noqa: N802 - stdlib callback name
        type(self).hits.append(self.path)
        payload = json.dumps(type(self).body).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_POST = do_GET

    def log_message(self, *_args):
        pass


class _Server:
    def __init__(self, status, body):
        cls = type("_H%d" % id(self), (_Handler,),
                   {"status": status, "body": body, "hits": []})
        self.handler = cls
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), cls)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=10)
        return False

    @property
    def hits(self):
        return self.handler.hits


def _run_bdctl(argv, port):
    env = dict(os.environ, BD_URL=f"http://127.0.0.1:{port}")
    env.pop("BD_TOKEN", None)
    proc = subprocess.run([sys.executable, str(_BDCTL), *argv],
                          capture_output=True, text=True, timeout=60,
                          cwd=str(_REPO), env=env)
    return proc.returncode, proc.stdout, proc.stderr


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ── RED: a 503 must produce valid JSON ────────────────────────────────────

def test_a_locked_vault_503_emits_valid_json_naming_the_vault():
    with _Server(503, _LOCKED_VAULT_503) as srv:
        rc, out, err = _run_bdctl(["health", "--json"], srv.port)

    # PRECONDITION: the stub actually served the request. An assertion about
    # bdctl's rendering of a 503 is vacuous if no 503 was ever served.
    assert srv.hits == ["/api/health"], srv.hits
    assert out.strip(), (
        "RED: `health --json` printed NOTHING on a 503 -- json.loads('') "
        f"cannot succeed. stderr={err!r}")

    parsed = json.loads(out)          # the exact failure the row records
    assert parsed["ok"] is False, parsed
    # The server's OWN structured body, where a consumer expects to find it.
    assert parsed["degraded"] == "credential_vault_locked", parsed
    assert parsed["credentials"]["state"] == "locked", parsed
    assert parsed["version"] == "3.66.0-row467", parsed
    # ... and the transport outcome, named.
    assert parsed["bdctl"]["error_kind"] == "http_error", parsed["bdctl"]
    assert parsed["bdctl"]["http_status"] == 503, parsed["bdctl"]
    assert parsed["bdctl"]["path"] == "/api/health", parsed["bdctl"]
    # Loud as well as structured, and the exit stays in the documented set.
    assert rc == 1, (rc, out, err)
    assert "503" in err, err


# ── The three outcomes must not collapse into one diagnostic ──────────────

def test_a_genuine_500_is_distinguishable_from_the_503():
    with _Server(500, _GENUINE_ERROR_500) as srv:
        rc, out, err = _run_bdctl(["health", "--json"], srv.port)
    assert srv.hits == ["/api/health"], srv.hits
    parsed = json.loads(out)
    assert parsed["bdctl"]["error_kind"] == "http_error", parsed["bdctl"]
    assert parsed["bdctl"]["http_status"] == 500, parsed["bdctl"]
    assert parsed["error"].startswith("unhandled:"), parsed
    # The distinguishing field, asserted against the OTHER case's value.
    assert parsed["bdctl"]["http_status"] != 503
    assert "degraded" not in parsed, parsed
    assert rc == 1, (rc, out, err)


def test_an_unreachable_server_is_distinguishable_and_still_loud():
    """NEGATIVE CONTROL: no server at all must not read as an empty success."""
    port = _free_port()          # bound, then released: nothing is listening
    rc, out, err = _run_bdctl(["health", "--json"], port)

    parsed = json.loads(out)
    assert parsed["ok"] is False, parsed
    assert parsed["bdctl"]["error_kind"] == "unreachable", parsed["bdctl"]
    # Nothing answered, so there is no status to report -- and reporting a
    # fabricated one would be the defect this cut is about.
    assert parsed["bdctl"]["http_status"] is None, parsed["bdctl"]
    assert "degraded" not in parsed, parsed
    # LOUD: the human diagnostic survives on stderr and the exit is nonzero.
    assert rc != 0, (rc, out, err)
    assert "Can't reach" in err, err


def test_a_non_json_error_body_is_carried_verbatim_not_dropped():
    """An HTML error page from a reverse proxy is not JSON; the refusal must
    still be JSON, and must still carry what the server said."""
    class _Html(_Handler):
        status = 502
        body = None
        hits: list[str] = []

        def do_GET(self):  # noqa: N802
            type(self).hits.append(self.path)
            payload = b"<html><body>502 Bad Gateway</body></html>"
            self.send_response(502)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Html)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        rc, out, err = _run_bdctl(["health", "--json"], port)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)

    assert _Html.hits == ["/api/health"], _Html.hits
    parsed = json.loads(out)
    assert parsed["ok"] is False, parsed
    assert parsed["bdctl"]["http_status"] == 502, parsed["bdctl"]
    assert "Bad Gateway" in parsed["bdctl"]["raw_body"], parsed["bdctl"]
    assert rc == 1, (rc, out, err)


# ── NEGATIVE CONTROLS: nothing else changed ───────────────────────────────

def test_a_healthy_200_still_emits_exactly_what_it_emitted_before():
    """THE MIRROR DEFECT.  A client that now reports a refusal over a healthy
    server is as broken as one that reported nothing over a refusal."""
    with _Server(200, _HEALTHY_200) as srv:
        rc, out, err = _run_bdctl(["health", "--json"], srv.port)
    assert srv.hits == ["/api/health"], srv.hits
    parsed = json.loads(out)
    # Byte-for-byte the server payload: no envelope, no bdctl key, no
    # rewriting of a successful answer.
    assert parsed == _HEALTHY_200, parsed
    assert "bdctl" not in parsed, parsed
    assert rc == 0, (rc, out, err)
    assert err.strip() == "", err


def test_the_human_path_keeps_its_diagnostic_and_its_nonzero_exit():
    """Row 467 confines the change to --json. Without the flag a 503 must
    still fail exactly as it did."""
    with _Server(503, _LOCKED_VAULT_503) as srv:
        rc, out, err = _run_bdctl(["health"], srv.port)
    assert srv.hits == ["/api/health"], srv.hits
    assert rc != 0, (rc, out, err)
    assert "HTTP 503" in err, err
    assert "credential_vault_locked" in err, err
    # It must NOT have quietly become the JSON path.
    assert out.strip() == "" or "{" not in out, out


def test_a_healthy_200_human_path_still_prints_the_report():
    with _Server(200, _HEALTHY_200) as srv:
        rc, out, err = _run_bdctl(["health"], srv.port)
    assert srv.hits == ["/api/health"], srv.hits
    assert rc == 0, (rc, out, err)
    assert "status:" in out and "OK" in out, out
    assert "3.66.0-row467" in out, out


def test_other_commands_keep_the_unchanged_http_error_behaviour():
    """The refusal is raised for all 48 _request call sites; only cmd_health
    opts into the structure. A sibling command must be untouched."""
    with _Server(503, _GENUINE_ERROR_500) as srv:
        rc, out, err = _run_bdctl(["pause-all", "--json"], srv.port)
    assert srv.hits == ["/api/pause_all"] or srv.hits, srv.hits
    assert rc != 0, (rc, out, err)
    assert "HTTP 503" in err, err
    assert out.strip() == "", out


def test_request_failed_is_a_systemexit_carrying_the_same_message():
    """The mechanism, asserted directly: an uncaught RequestFailed must be
    indistinguishable from the bare SystemExit it replaced."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_row467_bdctl", _BDCTL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    exc = mod.RequestFailed("HTTP 503 x on GET /api/health\n  body",
                            kind="http_error", status=503, reason="x",
                            body_text="body", body_json={"ok": False},
                            method="GET", path="/api/health")
    assert isinstance(exc, SystemExit)
    assert exc.code == "HTTP 503 x on GET /api/health\n  body", exc.code
    assert str(exc) == "HTTP 503 x on GET /api/health\n  body", str(exc)
    assert exc.kind == "http_error" and exc.status == 503

    # And the envelope builder keeps the two kinds apart.
    unreachable = mod.RequestFailed("Can't reach x", kind="unreachable",
                                    reason="refused", method="GET",
                                    path="/api/health")
    assert unreachable.status is None
    assert (mod._health_refusal_envelope(exc)["bdctl"]["error_kind"]
            != mod._health_refusal_envelope(unreachable)["bdctl"]["error_kind"])


def test_the_server_still_refuses_with_503():
    """app_health's honest refusal is NOT weakened to make the client pass.

    A degraded probe answers 503 with a body; that is the behaviour row 467
    depends on and must not be traded away.
    """
    source = (_REPO / "bulk_downloader" / "app_health.py").read_text("utf-8")
    assert source.count(
        'return jsonify(payload), (200 if payload["ok"] else 503)') == 2, (
        "api_health / api_health_v2 must still answer 503 when not ok")
