"""Cut 843: Apt-Cacher-NG Integration for Container and VM OS Updates.

Acceptance criteria:
(1) apt proxy configuration correctly generated.
(2) cache hit reduces subsequent install times to <500ms.
(3) automatic bypass if proxy is unavailable.
Remedies for prior refute (O943 / row843-local-VERDICT-correctness.md):
- E1: Wire bd-apt-cache into worker bootstrap / container setup scripts.
- E2: No pytest.skip (Hermetic Test Gate, Fleet Rule 46).
- E3: Graceful handling of PermissionError / OSError on unprivileged default paths.
- E4: Hermetic network testing: no leaks to unmocked live host ports.
- E5: Valid behavioral RED provenance on base.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Generator
import pytest

BD_GATE_SCOPE = "module"

_ROOT = Path(__file__).resolve().parents[1]
_TOOL = _ROOT / "toolchain" / "bin" / "bd-apt-cache"
_CLOUD_SETUP = _ROOT / "scripts" / "cloud-setup.sh"
_PROVISION_HOST = _ROOT / "scripts" / "provision_test_host.sh"


class MockAptCacherServer(HTTPServer):
    """Hermetic mock HTTP server simulating apt-cacher-ng behavior and latency."""

    def __init__(self, server_address, RequestHandlerClass):
        super().__init__(server_address, RequestHandlerClass)
        self.cached_packages: dict[str, bytes] = {}
        self.miss_delay_sec: float = 0.6  # simulated WAN delay for cold miss (>=500ms)
        self.request_log: list[str] = []


class MockAptCacherHandler(BaseHTTPRequestHandler):
    server: MockAptCacherServer

    def do_GET(self):
        self.server.request_log.append(self.path)
        if self.path in ("/acng-report.html", "/"):
            body = b"<html><body>Apt-Cacher-NG Report Mock</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path.startswith("/deb/"):
            pkg_name = self.path
            if pkg_name in self.server.cached_packages:
                # Cache hit: instant response (<500ms)
                body = self.server.cached_packages[pkg_name]
                self.send_response(200)
                self.send_header("Content-Type", "application/x-debian-package")
                self.send_header("X-Apt-Cache", "HIT")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                # Cache miss: simulate WAN fetch latency (>=500ms)
                time.sleep(self.server.miss_delay_sec)
                payload = b"DEBIAN_PACKAGE_PAYLOAD_" + pkg_name.encode("utf-8")
                self.server.cached_packages[pkg_name] = payload
                self.send_response(200)
                self.send_header("Content-Type", "application/x-debian-package")
                self.send_header("X-Apt-Cache", "MISS")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        pass  # Quiet during tests


@pytest.fixture
def mock_proxy_server() -> Generator[tuple[str, MockAptCacherServer], None, None]:
    """Start an ephemeral hermetic mock Apt-Cacher-NG server on an OS-assigned port."""
    server = MockAptCacherServer(("127.0.0.1", 0), MockAptCacherHandler)
    port = server.server_address[1]
    proxy_url = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield proxy_url, server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def _run_tool(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run bd-apt-cache with given arguments."""
    assert _TOOL.exists(), f"bd-apt-cache tool must exist at {_TOOL}"
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(_TOOL), *args],
        capture_output=True,
        text=True,
        env=full_env,
    )


def test_tool_executable_exists():
    """Verify tool exists and is marked executable."""
    assert _TOOL.is_file(), f"Tool not found: {_TOOL}"
    assert os.access(_TOOL, os.X_OK), f"Tool not executable: {_TOOL}"


def test_apt_proxy_configuration_generated(tmp_path: Path, mock_proxy_server):
    """AC 1: apt proxy configuration correctly generated when proxy is reachable."""
    proxy_url, _ = mock_proxy_server
    proxy_file = tmp_path / "apt" / "apt.conf.d" / "01proxy"

    # Execute bd-apt-cache to configure proxy targeting mock proxy URL
    result = _run_tool(
        "--configure",
        "--proxy-url", proxy_url,
        "--proxy-file", str(proxy_file),
    )
    assert result.returncode == 0, f"Configure failed: {result.stderr}\nstdout: {result.stdout}"
    assert proxy_file.exists(), "Proxy configuration file was not created"

    content = proxy_file.read_text(encoding="utf-8")
    assert f'Acquire::http::Proxy "{proxy_url}";' in content
    assert content.endswith("\n")

    # Verify JSON status mode
    json_result = _run_tool(
        "--status",
        "--proxy-url", proxy_url,
        "--proxy-file", str(proxy_file),
        "--json",
    )
    assert json_result.returncode == 0
    status = json.loads(json_result.stdout)
    assert status["proxy_available"] is True
    assert status["configured"] is True
    assert status["proxy_url"] == proxy_url
    assert status["proxy_file"] == str(proxy_file)


def test_cache_hit_reduces_subsequent_install_times(mock_proxy_server):
    """AC 2: cache hit reduces subsequent install times to <500ms.

    Tests both cold miss (>=500ms due to upstream network wait) and
    warm cache hit (<500ms, typically <50ms).
    """
    proxy_url, server = mock_proxy_server
    pkg_path = "/deb/libtest-pkg-1.0.deb"

    # First request: Cold miss (simulated upstream latency 600ms)
    cold_result = _run_tool(
        "--benchmark-fetch",
        "--proxy-url", proxy_url,
        "--package-path", pkg_path,
        "--json",
    )
    assert cold_result.returncode == 0, cold_result.stderr
    cold_data = json.loads(cold_result.stdout)
    assert cold_data["cache_status"] == "MISS"
    assert cold_data["elapsed_ms"] >= 500, (
        f"Negative control: cold miss should take >=500ms, took {cold_data['elapsed_ms']}ms"
    )

    # Second request: Cache hit (served from cache <500ms)
    warm_result = _run_tool(
        "--benchmark-fetch",
        "--proxy-url", proxy_url,
        "--package-path", pkg_path,
        "--json",
    )
    assert warm_result.returncode == 0, warm_result.stderr
    warm_data = json.loads(warm_result.stdout)
    assert warm_data["cache_status"] == "HIT"
    assert warm_data["elapsed_ms"] < 500, (
        f"Positive control: warm cache hit must be <500ms, took {warm_data['elapsed_ms']}ms"
    )


def test_automatic_bypass_if_proxy_unavailable(tmp_path: Path):
    """AC 3: automatic bypass if proxy is unavailable."""
    # Find a closed port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        closed_port = s.getsockname()[1]
    dead_proxy_url = f"http://127.0.0.1:{closed_port}"

    proxy_file = tmp_path / "01proxy"
    # Pre-populate stale proxy config to ensure bypass removes or deactivates it
    proxy_file.write_text('Acquire::http::Proxy "http://stale.proxy:3142";\n', encoding="utf-8")
    assert proxy_file.exists()

    result = _run_tool(
        "--configure",
        "--proxy-url", dead_proxy_url,
        "--proxy-file", str(proxy_file),
        "--timeout", "0.5",
        "--json",
    )
    assert result.returncode == 0, f"Expected clean exit on bypass: {result.stderr}"
    data = json.loads(result.stdout)
    assert data["proxy_available"] is False
    assert data["bypass_active"] is True
    assert not proxy_file.exists(), "Proxy file must be removed upon automatic bypass"


def test_explicit_bypass_removes_stale_proxy_configuration(tmp_path: Path):
    """The CLI bypass path removes a stale proxy configuration."""
    proxy_file = tmp_path / "01proxy"
    proxy_file.write_text('Acquire::http::Proxy "http://stale.proxy:3142";\n', encoding="utf-8")

    result = _run_tool("--bypass", "--proxy-file", str(proxy_file), "--json")

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["success"] is True
    assert data["bypass_active"] is True
    assert data["removed"] is True
    assert not proxy_file.exists(), "Explicit bypass must remove the stale proxy file"


def test_unprivileged_permission_error_handled_gracefully(tmp_path: Path, mock_proxy_server):
    """Remedy E3: PermissionError/OSError on default path handled gracefully without crashing."""
    proxy_url, _ = mock_proxy_server
    read_only_dir = tmp_path / "readonly_apt"
    read_only_dir.mkdir(mode=0o555)
    proxy_file = read_only_dir / "01proxy"

    # Attempt configure targeting unwritable directory in unprivileged mode
    result = _run_tool(
        "--configure",
        "--proxy-url", proxy_url,
        "--proxy-file", str(proxy_file),
        "--json",
        env={"BD_APT_NO_SUDO": "1"},
    )
    # Must exit cleanly (code 0) reporting non-fatal permission failure, never uncaught traceback
    assert result.returncode == 0, f"Traceback or non-zero exit: {result.stderr}"
    data = json.loads(result.stdout)
    assert data["configured"] is False
    assert "error" in data or "reason" in data
    assert "Traceback" not in result.stderr


def test_worker_bootstrap_invokes_bd_apt_cache():
    """Remedy E1: scripts/cloud-setup.sh and provision_test_host.sh invoke bd-apt-cache."""
    assert _CLOUD_SETUP.is_file(), f"Missing {_CLOUD_SETUP}"
    cloud_setup_text = _CLOUD_SETUP.read_text(encoding="utf-8")
    assert "bd-apt-cache" in cloud_setup_text, (
        "scripts/cloud-setup.sh does not invoke bd-apt-cache during worker initialization"
    )

    assert _PROVISION_HOST.is_file(), f"Missing {_PROVISION_HOST}"
    provision_text = _PROVISION_HOST.read_text(encoding="utf-8")
    assert "bd-apt-cache" in provision_text, (
        "scripts/provision_test_host.sh does not invoke bd-apt-cache during VM/host initialization"
    )


def test_selftest_contract():
    """Verify --selftest emits parseable verdict."""
    result = _run_tool("--selftest")
    assert result.returncode == 0, f"Selftest failed: {result.stderr}\n{result.stdout}"
    assert "SELFTEST PASS" in result.stdout
    assert "SELFTEST FAIL" not in result.stdout
