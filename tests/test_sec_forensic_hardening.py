from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
from flask import Flask

from bulk_downloader.network.url_sanitizer import (
    SSRFBlocked,
    is_safe_url,
    resolve_and_pin_host,
    validate_url,
)
from bulk_downloader.routes.export_import import export_import_bp, run_argv_command


@pytest.fixture
def app() -> Flask:
    app = Flask(__name__)
    app.register_blueprint(export_import_bp)
    return app

@pytest.fixture
def client(app: Flask):
    return app.test_client()

def test_url_sanitizer_blocks_loopback() -> None:
    for target in ("http://127.0.0.1/api", "http://127.0.0.2:8000/info", "http://[::1]:9000/metrics", "http://localhost:3000/admin", "http://localhost.localdomain/secret"):
        with pytest.raises(SSRFBlocked):
            validate_url(target)
        safe, _ = is_safe_url(target)
        assert safe is False

def test_url_sanitizer_blocks_private_subnets_by_default() -> None:
    for target in ("http://10.0.70.164:5432/cockpit", "http://172.16.0.1/admin", "http://192.168.1.1/router"):
        with pytest.raises(SSRFBlocked):
            validate_url(target)

def test_url_sanitizer_blocks_cloud_metadata_unconditionally() -> None:
    target = "http://169.254.169.254/latest/meta-data/"
    with pytest.raises(SSRFBlocked):
        validate_url(target, allowed_subnets=["169.254.0.0/16", "0.0.0.0/0"])
    safe, reason = is_safe_url(target, allowed_subnets=["169.254.0.0/16"])
    assert safe is False
    assert "cloud metadata" in reason

def test_url_sanitizer_blocks_dangerous_schemes() -> None:
    for target in ("file:///etc/passwd", "gopher://evil.com/1", "ftp://ftp.example.com/", "data:text/plain;base64,SGVsbG8="):
        with pytest.raises(SSRFBlocked):
            validate_url(target)

def test_url_sanitizer_allows_declared_interface_subnets() -> None:
    target = "http://10.0.70.50:8080/cluster"
    with pytest.raises(SSRFBlocked):
        validate_url(target)
    assert validate_url(target, allowed_subnets=["10.0.70.0/24"]) == target
    safe, _ = is_safe_url(target, allowed_subnets=["10.0.70.0/24"])
    assert safe is True
    with pytest.raises(SSRFBlocked):
        validate_url("http://192.168.1.1/", allowed_subnets=["10.0.70.0/24"])

def test_resolve_and_pin_host_returns_pinned_tuple() -> None:
    with mock.patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
        pinned_ip, host_header, port = resolve_and_pin_host("https://example.com/api")
        assert pinned_ip == "93.184.216.34"
        assert host_header == "example.com"
        assert port == 443

def test_structured_argv_invocation_enforcement() -> None:
    with pytest.raises(TypeError, match="must be an argv list, not a string"):
        run_argv_command("echo test")
    with pytest.raises(TypeError, match="must be str"):
        run_argv_command(["ls", 123])
    proc = run_argv_command(["python3", "-c", "import sys; sys.stdout.write('argv-ok')"])
    assert proc.returncode == 0
    assert proc.stdout == "argv-ok"

def test_export_import_validate_url_route(client) -> None:
    r = client.post("/api/export_import/validate_url", json={"url": "http://127.0.0.1:8080/data"})
    assert r.status_code == 400
    assert "loopback" in r.get_json()["error"]
    r = client.post("/api/export_import/validate_url", json={"url": "http://10.0.70.20/api", "allowed_subnets": ["10.0.70.0/24"]})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

def test_export_import_run_conversion_route(client) -> None:
    r = client.post("/api/export_import/run_conversion", json={"argv": "rm -rf /"})
    assert r.status_code == 400
    r = client.post("/api/export_import/run_conversion", json={"argv": ["python3", "-c", "print('converted')"]})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    assert "converted" in r.get_json()["stdout"]

def test_forensic_audit_remediations_verified() -> None:
    from bulk_downloader.llm_eval import _SECRET
    assert _SECRET == "api_key=sk-EVALSECRET0123456789"
    root = Path(__file__).resolve().parent.parent
    content = (root / "bulk_downloader/llm_eval.py").read_text(encoding="utf-8")
    assert '_SECRET = "api_key=sk-EVALSECRET0123456789"' not in content
    from bulk_downloader.task_circuit_breaker import compute_backoff
    results = [compute_backoff(attempt=2, base=1.0, max_backoff=60.0) for _ in range(20)]
    assert len(set(results)) > 5, "compute_backoff must have non-zero variance (jittered)"
    from bulk_downloader.routes.export_import import run_argv_command
    with pytest.raises(TypeError, match="must be an argv list"):
        run_argv_command("echo test")
