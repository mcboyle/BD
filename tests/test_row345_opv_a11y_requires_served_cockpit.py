"""Row 345: OPV-A11Y must never grade an unavailable or wrong page.

The Flask application and loopback requests in these tests are real.  Only the
Playwright transport is replaced so the gate does not require an X server or a
browser download in CI, and so the exact navigation response is observable.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap


BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parents[1]
_TOOL = _REPO / "toolchain" / "bin" / "bd-opv"
_RESULT_PREFIX = "ROW345_RESULT "

_PROBE = textwrap.dedent(
    f"""
    import html
    import importlib
    import importlib.machinery
    import importlib.util
    import json
    import os
    from pathlib import Path
    import re
    import sys
    import urllib.error
    import urllib.request

    tool = Path({str(_TOOL)!r})
    spec = importlib.util.spec_from_loader(
        "row345_bd_opv_probe",
        importlib.machinery.SourceFileLoader("row345_bd_opv_probe", str(tool)),
    )
    assert spec is not None and spec.loader is not None
    opv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(opv)

    mode = os.environ["ROW345_MODE"]
    state = {{
        "navigations": [],
        "identity_probes": 0,
        "axe_injections": 0,
        "axe_runs": 0,
        "browser_closes": 0,
        "waits": 0,
    }}

    class Response:
        def __init__(self, status):
            self.status = status

    class Page:
        def __init__(self):
            self.body = ""

        def goto(self, url, **kwargs):
            try:
                with urllib.request.urlopen(url, timeout=5) as response:
                    status = response.status
                    body = response.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as error:
                status = error.code
                body = error.read().decode("utf-8", "replace")
            self.body = body
            state["navigations"].append({{
                "status": status,
                "body_prefix": body[:80],
                "url": url,
            }})
            return Response(status)

        def wait_for_timeout(self, milliseconds):
            assert milliseconds == 1200
            state["waits"] += 1

        def evaluate(self, source):
            if "document.title" in source:
                state["identity_probes"] += 1
                if mode == "wrong-identity":
                    return {{"title": "Authentication", "heading": "Sign in"}}
                title_match = re.search(r"<title>(.*?)</title>", self.body,
                                        flags=re.IGNORECASE | re.DOTALL)
                heading_match = re.search(r"<h1[^>]*>(.*?)</h1>", self.body,
                                          flags=re.IGNORECASE | re.DOTALL)
                title = html.unescape(title_match.group(1)).strip() if title_match else ""
                heading_html = heading_match.group(1) if heading_match else ""
                heading = html.unescape(re.sub(r"<[^>]+>", "", heading_html)).strip()
                return {{"title": title, "heading": heading}}
            if "axe.run" in source:
                state["axe_runs"] += 1
                return {{"v": 0, "passes": 7, "ids": []}}
            state["axe_injections"] += 1
            return None

    class Browser:
        def new_page(self):
            return Page()

        def close(self):
            state["browser_closes"] += 1

    class Chromium:
        def launch(self, **kwargs):
            assert kwargs["headless"] is False
            return Browser()

    class Playwright:
        chromium = Chromium()

    class Manager:
        def __enter__(self):
            return Playwright()

        def __exit__(self, exc_type, exc, traceback):
            return False

    owner, root = opv._enter_resource_boundary()
    try:
        os.environ["DISPLAY"] = ":row345"
        axe_path = root / "axe.min.js"
        axe_path.write_text("window.axe = {{}};", encoding="utf-8")
        os.environ["BD_AXE_JS"] = str(axe_path)

        f43 = None
        auth_after = None
        if mode == "registry-order":
            f43 = opv.chk_f43()
            auth_after = os.environ.get("BD_AUTH_TOKEN")
        elif mode == "locked-page":
            os.environ["BD_AUTH_TOKEN"] = "row345-locked"
            app_module = importlib.import_module("bulk_downloader.app")
            importlib.reload(app_module)
        else:
            importlib.import_module("bulk_downloader.app")

        sync_api = importlib.import_module("playwright.sync_api")
        sync_api.sync_playwright = lambda: Manager()

        a11y = opv.chk_a11y()
        payload = {{
            "mode": mode,
            "f43": f43,
            "auth_after": auth_after,
            "a11y": a11y,
            **state,
        }}
        print({_RESULT_PREFIX!r} + json.dumps(payload, sort_keys=True))
    finally:
        opv._leave_resource_boundary(owner, root)
    """
)


def _run_probe(mode: str) -> dict:
    env = dict(os.environ)
    for key in {
        "BD_AUTH_TOKEN", "BD_AXE_JS", "BD_INSTALL_DIR", "BD_WORK", "DISPLAY",
        "PLAYWRIGHT_BROWSERS_PATH", "_BD_OPV_REEXEC",
    }:
        env.pop(key, None)
    env.update({
        "BD_DISABLE_KEEPALIVE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "ROW345_MODE": mode,
    })
    done = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=_REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    records = [
        line[len(_RESULT_PREFIX):]
        for line in done.stdout.splitlines()
        if line.startswith(_RESULT_PREFIX)
    ]
    assert done.returncode == 0, done.stdout + done.stderr
    assert len(records) == 1, (
        f"expected one probe record, got {len(records)}\nstdout={done.stdout}\n"
        f"stderr={done.stderr}"
    )
    result = json.loads(records[0])
    assert len(result["navigations"]) == 1, result
    return result


def test_f43_then_a11y_restores_auth_and_grades_the_served_cockpit() -> None:
    result = _run_probe("registry-order")
    navigation = result["navigations"][0]
    assert result["f43"] == [
        "PASS",
        "enqueue token denied with required_scope=admin; "
        "admin token issued one child token",
    ]
    assert (
        result["auth_after"] is None
        and navigation["status"] == 200
        and result["a11y"][0] == "PASS"
    ), (
        "OPV registry order produced a wrong accessibility verdict: "
        f"F43={result['f43']!r} auth_after={result['auth_after']!r} "
        f"navigated_status={navigation['status']} "
        f"body_prefix={navigation['body_prefix']!r} A11Y={result['a11y']!r}"
    )
    assert result["identity_probes"] == 1
    assert result["axe_injections"] == 1
    assert result["axe_runs"] == 1
    assert result["browser_closes"] == 1


def test_f43_internal_subject_failures_are_not_precondition_skips(
        monkeypatch) -> None:
    spec = importlib.util.spec_from_loader(
        "row367_bd_opv_f43_failure_probe",
        importlib.machinery.SourceFileLoader(
            "row367_bd_opv_f43_failure_probe", str(_TOOL)
        ),
    )
    assert spec is not None and spec.loader is not None
    opv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(opv)
    assert any(check_id == "OPV-F4.3" for check_id, _, _ in opv.REGISTRY)

    class Response:
        def __init__(self, status, body):
            self.status_code = status
            self._body = body

        def get_json(self):
            return self._body

    class Client:
        def __init__(self, scenario):
            self.scenario = scenario
            self.calls = []

        def get(self, path, **_kwargs):
            self.calls.append(("GET", path))
            if path == "/api/api_tokens":
                status = 200 if self.scenario == "auth-bypass" else 401
                return Response(status, {"ok": status == 200})
            if path == "/api/csrf":
                return Response(200, {"csrf_token": "row367-csrf"})
            raise AssertionError(f"unexpected GET {path}")

        def post(self, path, **_kwargs):
            self.calls.append(("POST", path))
            assert path == "/api/api_tokens"
            if self.scenario == "enqueue-mint-failed":
                return Response(500, {"error": "fixture enqueue mint broke"})
            if self.scenario == "enqueue-token-missing":
                return Response(200, {"ok": True})
            raise AssertionError(f"unexpected POST for {self.scenario}")

    class ClientContext:
        def __init__(self, client):
            self.client = client

        def __enter__(self):
            return self.client, object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    scenarios = (
        "auth-bypass",
        "enqueue-mint-failed",
        "enqueue-token-missing",
    )
    clients = []
    auth_tokens = []
    outcomes = []
    for scenario in scenarios:
        client = Client(scenario)
        clients.append(client)

        def authenticated_client(token, *, _client=client):
            auth_tokens.append(token)
            return ClientContext(_client)

        monkeypatch.setattr(opv, "_authenticated_client", authenticated_client)
        outcomes.append(opv.chk_f43())

    assert auth_tokens == ["opv-master", "opv-master", "opv-master"]
    assert [client.calls for client in clients] == [
        [("GET", "/api/api_tokens")],
        [
            ("GET", "/api/api_tokens"),
            ("GET", "/api/csrf"),
            ("POST", "/api/api_tokens"),
        ],
        [
            ("GET", "/api/api_tokens"),
            ("GET", "/api/csrf"),
            ("POST", "/api/api_tokens"),
        ],
    ]
    assert outcomes == [
        (
            "FAIL",
            "auth not enforced (got 200 not 401) -- scoping bypassed by design",
        ),
        ("FAIL", "could not mint enqueue token (500)"),
        ("FAIL", "mint returned no token field"),
    ]


def test_a11y_refuses_a_real_401_before_running_axe() -> None:
    result = _run_probe("locked-page")
    navigation = result["navigations"][0]
    assert navigation["status"] == 401
    assert "authentication required" in navigation["body_prefix"]
    assert result["a11y"][0] == "SKIP", result["a11y"]
    assert "HTTP 401" in result["a11y"][1]
    assert result["identity_probes"] == 0
    assert result["axe_injections"] == 0
    assert result["axe_runs"] == 0
    assert result["browser_closes"] == 1


def test_a11y_refuses_a_200_response_without_cockpit_identity() -> None:
    result = _run_probe("wrong-identity")
    navigation = result["navigations"][0]
    assert navigation["status"] == 200
    assert navigation["body_prefix"].lower().startswith("<!doctype html>")
    assert result["a11y"][0] == "SKIP", result["a11y"]
    assert "cockpit page identity mismatch" in result["a11y"][1]
    assert result["identity_probes"] == 1
    assert result["axe_injections"] == 0
    assert result["axe_runs"] == 0
    assert result["browser_closes"] == 1


def test_a11y_still_passes_for_a_200_response_with_cockpit_identity() -> None:
    result = _run_probe("served-page")
    navigation = result["navigations"][0]
    assert navigation["status"] == 200
    assert result["a11y"] == [
        "PASS",
        "headed cockpit rendered; axe WCAG2.1-AA ran: 7 passes, 0 violations (none)",
    ]
    assert result["axe_injections"] == 1
    assert result["axe_runs"] == 1
    assert result["browser_closes"] == 1


def test_transform_control_imports_bd_opv_without_judging_a11y() -> None:
    spec = importlib.util.spec_from_loader(
        "row345_bd_opv_import_control",
        importlib.machinery.SourceFileLoader(
            "row345_bd_opv_import_control", str(_TOOL)
        ),
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert any(check_id == "OPV-A11Y" for check_id, _, _ in module.REGISTRY)
