"""install_service.sh certifies "RUNNING" on `is-active`, not on serving.

THE DEFECT. install_service.sh:325-333 polls::

    SERVICE_STATE="$(systemctl is-active "${SERVICE_NAME}" 2>/dev/null || true)"

and install_service.sh:337-338 turns `active` into::

    ${SERVICE_NAME} is RUNNING and enabled on boot.

The unit is `Type=simple` (install_service.sh:212), so systemd reports `active`
the moment the process is SPAWNED -- not when waitress has bound the port. The
installer therefore certifies to the operator's face a service that may not be
serving, and it never asks the app anything: there is no HTTP probe in the file.

capture.sh already solved this for itself and names this script as the unfixed
twin (capture.sh:662-668): "install_service.sh polls `systemctl is-active` and
reports RUNNING the moment the unit goes active, but Type=simple means 'the
process was spawned', not 'waitress has bound :5555' ... Ask the app instead."
Its own `wait_for_service_ready()` (capture.sh:148-178) polls
`http://localhost:5555/api/health` with `curl -sSf --max-time 2`. The gap is
measured, not theoretical: capture.sh:125-133 records a real boot journal where
the unit went active at 19:00:17 and waitress printed "Serving on" at 19:00:20,
with `curl: (7) Failed to connect to localhost port 5555` in between.

WHAT THIS GATE ASSERTS, AND WHAT IT DELIBERATELY DOES NOT. It asserts observable
behaviour of the script's own code: run the post-start region with a systemd
that says `active` and with nothing listening, and the run must not tell the
operator the service is running. It does NOT assert on wording, on `curl` vs
anything else, on a loop count, or on the exit code -- the exit code is coupled
to capture.sh:1029 -> tools/capture_verdict.py:130-132, where any nonzero stage
exit becomes a FAIL, so changing it is an operator decision, not this gate's.

The positive control (a health endpoint that answers -> still reported RUNNING)
is what stops the fix being a bare deletion of the success banner, and it is
also the harness canary: it can only pass if the stub systemd really did drive
the script down the `active` path.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "install_service.sh"

SERVICE_NAME = "bulkdownloader"
AI_SERVICE_NAME = "bulkdownloader-ai-ready"

# The statement that starts the main service. Everything after it is the
# script's readiness decision plus the banner it prints about it -- the region
# under test. Anchoring here rather than on the poll loop keeps the gate honest
# across a restructure of the wait itself.
START_ANCHOR = 'systemctl restart "${SERVICE_NAME}"'

# A claim of health: a copula plus a health word, on one line. The copula is
# load-bearing -- a bare token match reads "Waiting for the app to become
# ready" as a claim, and a gate that cries wolf gets switched off (CLAUDE.md
# section 0). `active` is deliberately NOT a health word here: that is exactly
# the question this gate says is the wrong one to ask.
_POSITIVE = re.compile(
    r"\b(is|are|now)\b[^.\n]{0,40}?\b(running|serving|ready|up)\b",
    re.IGNORECASE,
)
# Anything that qualifies such a claim. Deliberately broad: a fix is free to
# word its warning however it likes, it just may not stay silent.
_NEGATED = re.compile(
    r"(\bnot\b|n't|\bnever\b|\bno\b|warning|error|unverified|cannot|"
    r"could not|unable|fail|refus|timed out|timeout|unknown|skip)",
    re.IGNORECASE,
)


@pytest.fixture(scope="module")
def body() -> str:
    if not INSTALL_SH.is_file():
        pytest.fail(f"{INSTALL_SH} not found; this gate cannot verify its subject")
    return INSTALL_SH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tail(body: str) -> str:
    """The post-start region of install_service.sh, verbatim."""
    idx = body.find(START_ANCHOR)
    if idx < 0:
        pytest.fail(
            f"{START_ANCHOR!r} not found in install_service.sh. This gate runs "
            f"the script's own post-start region; it cannot locate it and "
            f"therefore cannot answer."
        )
    line_start = body.rfind("\n", 0, idx) + 1
    return body[line_start:]


def _free_port() -> int:
    """A port with nothing on it: bound, read back, released."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _HealthHandler(BaseHTTPRequestHandler):
    status_code = 200
    payload = b'{"ok": true}'

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        payload = self.payload
        self.send_response(self.status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):  # keep pytest output clean
        pass


def _run_tail(tail_src: str, workdir: Path, port: int,
              timeout: int = 180) -> subprocess.CompletedProcess:
    """Execute the script's post-start region against a stub systemd.

    The stub answers `is-active` with `active` -- exactly the state in which the
    real defect appears. Nothing here touches /etc or the real service manager.
    """
    app_dir = workdir / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    # The unit carries EnvironmentFile=-${APP_DIR}/.env (install_service.sh:220)
    # and BD_PORT is a GUI-editable key (bulk_downloader/_envfile.py), so give a
    # port-resolving fix both places to find it.
    (app_dir / ".env").write_text(f"BD_PORT={port}\n", encoding="utf-8")

    bindir = workdir / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    (bindir / "systemctl").write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  is-active) echo active ;;\n"
        "  *) : ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    (bindir / "sudo").write_text('#!/bin/sh\nexec "$@"\n', encoding="utf-8")
    for stub in ("systemctl", "sudo"):
        (bindir / stub).chmod(0o755)

    prelude = (
        "set -u\n"
        "set -o pipefail\n"
        f'APP_DIR="{app_dir}"\n'
        f'SERVICE_NAME="{SERVICE_NAME}"\n'
        f'AI_SERVICE_NAME="{AI_SERVICE_NAME}"\n'
        'RUN_USER="svcuser"\n'
        'PYEXE="/usr/bin/python3"\n'
        f'UNIT_PATH="{app_dir}/unit.service"\n'
        f'AI_UNIT_PATH="{app_dir}/ai-unit.service"\n'
    )
    script = workdir / "tail.sh"
    script.write_text(prelude + tail_src, encoding="utf-8")

    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env.get('PATH', '')}"
    env["BD_PORT"] = str(port)
    try:
        return subprocess.run(
            ["bash", str(script)], capture_output=True, text=True,
            cwd=str(workdir), env=env, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"the post-start region did not finish within {timeout}s. A "
            f"readiness probe must be bounded; an unbounded one hangs the "
            f"installer instead of reporting."
        )


def _health_claims(out: str) -> list[str]:
    """Stanzas that assert health without qualifying it anywhere in the stanza.

    Two things this must NOT do, both measured against a patched copy of the
    script rather than reasoned about:

    * Match the raw line. `bulkdownloader-ai-ready` carries the token "ready" at
      a word boundary, so the companion's "Starting ... in the background"
      notice read as a health claim. The unit names are scrubbed first.
    * Judge line by line. A multi-line WARNING wraps, and its continuation
      ("serving. Check:") read as an unqualified claim while the word WARNING
      sat two lines above it. A banner stanza -- consecutive non-blank lines --
      is the unit an operator actually reads, so it is the unit judged here.

    Over-sensitivity is a soundness bug, not a safe default (CLAUDE.md 0).
    """
    claims = []
    for stanza in re.split(r"\n\s*\n", out):
        if not stanza.strip():
            continue
        scrubbed = stanza.replace(AI_SERVICE_NAME, " ").replace(SERVICE_NAME, " ")
        if _POSITIVE.search(scrubbed) and not _NEGATED.search(scrubbed):
            claims.append(stanza.strip())
    return claims


# -- denominator canaries ----------------------------------------------------

def test_the_post_start_region_is_not_empty(tail):
    """A zero-length tail would make every behavioural assertion below vacuous."""
    assert tail.strip(), "the extracted post-start region is empty"
    assert len(tail.splitlines()) > 5, (
        f"the post-start region is only {len(tail.splitlines())} lines; the "
        f"anchor probably matched somewhere unintended."
    )


def test_the_region_contains_the_readiness_decision(tail):
    """The gate must contain the thing it is asked about (CLAUDE.md section 0)."""
    assert "is-active" in tail or "SERVICE_STATE" in tail, (
        "the extracted region carries no readiness decision at all. Either the "
        "anchor is wrong or the decision moved; this gate cannot answer either "
        "way, and unknown is a third state that fails."
    )


def test_curl_is_available_to_this_harness():
    """Without curl the positive control cannot distinguish serving from not."""
    assert shutil.which("curl"), (
        "curl is not on PATH, so a probe-based fix could not run here and this "
        "gate could not tell 'serving' from 'not serving'. Install curl before "
        "reading any result from this file."
    )


# -- the positive control, which is also the harness canary ------------------

def test_a_service_that_is_actually_serving_is_still_reported_as_running(tail, tmp_path):
    """Serving + active -> the installer must still say so, with no warning.

    This is the guard against "fix" by deleting the success banner. It doubles
    as proof that the stub systemd really drives the script down the `active`
    path: if it did not, the not-active branch would fire and this test would
    see a warning.
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        proc = _run_tail(tail, tmp_path, port)
    finally:
        server.shutdown()
        server.server_close()

    out = proc.stdout + proc.stderr
    assert _health_claims(out), (
        f"with the unit active AND /api/health answering on 127.0.0.1:{port}, "
        f"the installer reported no success at all. A fix must keep telling "
        f"the operator when the service is genuinely up.\n--- output ---\n{out}"
    )
    assert not re.search(r"WARNING|ERROR", out), (
        f"a healthy service produced a warning. A gate that cries wolf gets "
        f"switched off (CLAUDE.md section 0).\n--- output ---\n{out}"
    )
    assert f"{AI_SERVICE_NAME} did not become ready" not in out, (
        "the AI-companion branch fired; the harness stub is not being used and "
        "these results describe something other than the subject."
    )


# -- the defect --------------------------------------------------------------

def test_active_but_not_serving_is_not_reported_as_running(tail, tmp_path):
    """`Type=simple` active means spawned. Nothing is listening. Do not certify."""
    port = _free_port()
    proc = _run_tail(tail, tmp_path, port)
    out = proc.stdout + proc.stderr
    claims = _health_claims(out)
    assert not claims, (
        f"the unit is `active` but nothing answers on 127.0.0.1:{port}, and "
        f"install_service.sh still told the operator {claims!r}. Type=simple "
        f"reports `active` on process spawn, not on bind -- capture.sh:125-133 "
        f"records a 3s window in which exactly this is false.\n"
        f"--- output ---\n{out}"
    )


def test_active_but_not_serving_surfaces_the_problem(tail, tmp_path):
    """The positive form: silence is not a report either.

    A fix that merely stops printing the success banner leaves the operator with
    no signal at all. The run must say the service is not known to be serving.
    """
    port = _free_port()
    proc = _run_tail(tail, tmp_path, port)
    out = proc.stdout + proc.stderr
    assert _NEGATED.search(out), (
        f"the unit is `active` but nothing answers on 127.0.0.1:{port}, and the "
        f"run surfaced no warning, error, or unverified state whatsoever. The "
        f"installer's last word to the operator must not be silence about a "
        f"service that is not serving.\n--- output ---\n{out}"
    )


def test_the_probe_actually_reached_the_health_endpoint(tail, tmp_path):
    """Derive reachability, do not assert it: the app must be ASKED.

    A fix could satisfy the two tests above by guessing (a longer sleep, a
    second `is-active`). Only an HTTP request to the app distinguishes spawned
    from serving, so this observes the server side: the health endpoint must
    record a hit.
    """
    hits: list[str] = []

    class _Recording(_HealthHandler):
        def do_GET(self):  # noqa: N802
            hits.append(self.path)
            super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Recording)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        proc = _run_tail(tail, tmp_path, port)
    finally:
        server.shutdown()
        server.server_close()

    assert hits, (
        f"install_service.sh made no HTTP request to 127.0.0.1:{port} at all. "
        f"It decides 'RUNNING' purely from `systemctl is-active`, which cannot "
        f"see whether waitress has bound the port. Ask the app.\n"
        f"--- output ---\n{proc.stdout + proc.stderr}"
    )
    assert any("/api/health" in h for h in hits), (
        f"the installer probed {hits!r}. /api/health is the unauthenticated "
        f"liveness route (bulk_downloader/app_health.py:127, exempted at "
        f"bulk_downloader/app.py:439-440); a probe of anything else may need a "
        f"session token and would report a healthy service as down."
    )


def test_locked_vault_is_serving_but_requires_explicit_restart_unlock(tail, tmp_path):
    """HTTP 503 + locked diagnostic is not a closed socket or failed app."""
    locked_payload = {
        "ok": False,
        "degraded": "credential_vault_locked",
        "credentials": {
            "backend": "master_password",
            "is_initialized": True,
            "is_unlocked": False,
            "missing_count": 0,
            "ok": False,
            "reference_count": 2,
            "resolved_count": 0,
            "state": "locked",
            "stored_count": 2,
            "unavailable_count": 2,
        },
    }

    # PRECONDITION: the synthetic app is serving a fully observed, nonempty
    # locked-vault response.  It is neither a transport failure nor a missing
    # credential, and all nonzero denominators are exact.
    assert locked_payload["degraded"] == "credential_vault_locked"
    assert locked_payload["credentials"]["reference_count"] == 2
    assert locked_payload["credentials"]["stored_count"] == 2
    assert locked_payload["credentials"]["unavailable_count"] == 2
    assert locked_payload["credentials"]["missing_count"] == 0

    hits: list[str] = []

    class _LockedVaultHealth(_HealthHandler):
        status_code = 503
        payload = json.dumps(locked_payload, separators=(",", ":")).encode()

        def do_GET(self):  # noqa: N802
            hits.append(self.path)
            super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), _LockedVaultHealth)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        proc = _run_tail(tail, tmp_path, port)
    finally:
        server.shutdown()
        server.server_close()

    out = proc.stdout + proc.stderr
    assert out.count("Credential vault is LOCKED") == 1, out
    assert out.count("Settings -> Secrets") == 1, out
    assert out.count("after every service restart") == 1, out
    assert "nothing answered" not in out
    assert hits == ["/api/health"]
