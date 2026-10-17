from __future__ import annotations

import http.server
import socket
import threading
import urllib.error
import urllib.request

import pytest

from tests.conftest import find_free_port, wait_until


BD_GATE_SCOPE = "module"


class LifecycleHandler(http.server.BaseHTTPRequestHandler):

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "healthy"}')
        elif self.path == "/ready":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return

class EphemeralLifecycleServer:

    def __init__(self, port: int | None=None) -> None:
        self.allocated_port: int = port if port is not None else find_free_port()
        self.server: http.server.HTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.actual_port: int = 0
        self.running: bool = False

    def start(self) -> EphemeralLifecycleServer:
        try:
            self.server = http.server.HTTPServer(("127.0.0.1", self.allocated_port), LifecycleHandler)
            self.actual_port = self.server.server_address[1]
        except OSError:
            self.server = http.server.HTTPServer(("127.0.0.1", 0), LifecycleHandler)
            self.actual_port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.running = True
        wait_until(self.is_ready, timeout=5.0, error_message=f"Server on port {self.actual_port} failed to start")
        return self

    def is_ready(self) -> bool:
        if not self.running or self.actual_port == 0:
            return False
        try:
            with socket.create_connection(("127.0.0.1", self.actual_port), timeout=0.2):
                return True
        except OSError:
            return False

    def get_url(self, path: str="/") -> str:
        return f"http://127.0.0.1:{self.actual_port}{path}"

    def stop(self) -> None:
        if self.server is not None:
            self.running = False
            self.server.shutdown()
            self.server.server_close()
            if self.thread is not None:
                self.thread.join(timeout=5.0)
            wait_until(lambda: not self.is_ready(), timeout=5.0, error_message=f"Server on port {self.actual_port} failed to release socket")

def test_server_lifecycle_clean_start_query_and_stop() -> None:
    srv = EphemeralLifecycleServer().start()
    try:
        assert srv.actual_port > 1024
        assert srv.actual_port not in (5555, 8000)
        health_url = srv.get_url("/health")
        with urllib.request.urlopen(health_url, timeout=5.0) as resp:
            assert resp.status == 200
            body = resp.read().decode()
            assert "healthy" in body
        ready_url = srv.get_url("/ready")
        with urllib.request.urlopen(ready_url, timeout=5.0) as resp:
            assert resp.status == 200
            assert resp.read().decode() == "OK"
    finally:
        srv.stop()

def test_dynamic_port_allocation_replaces_fixed_ports() -> None:
    port1 = find_free_port()
    port2 = find_free_port()
    assert port1 > 1024
    assert port2 > 1024
    assert port1 not in (5555, 8000)
    assert port2 not in (5555, 8000)

def test_parallel_servers_zero_port_collision() -> None:
    servers: list[EphemeralLifecycleServer] = []
    try:
        for _ in range(4):
            s = EphemeralLifecycleServer().start()
            servers.append(s)
        ports = [s.actual_port for s in servers]
        assert len(set(ports)) == 4, f"Port collision detected among parallel instances: {ports}"
        for s in servers:
            with urllib.request.urlopen(s.get_url("/health"), timeout=5.0) as resp:
                assert resp.status == 200
    finally:
        for s in servers:
            s.stop()

def test_condition_polling_eliminates_arbitrary_sleep_flakiness() -> None:
    counter = {"ticks": 0}

    def increment_and_check() -> bool:
        counter["ticks"] += 1
        return counter["ticks"] >= 3
    result = wait_until(increment_and_check, timeout=2.0, interval=0.01)
    assert result is True
    assert counter["ticks"] >= 3

def test_regression_fixed_port_collision_negative_control() -> None:
    s1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s1.bind(("127.0.0.1", 0))
    fixed_port = s1.getsockname()[1]
    s1.listen(1)
    s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError):
            s2.bind(("127.0.0.1", fixed_port))
    finally:
        s1.close()
        s2.close()
