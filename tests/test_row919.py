"""Cut 919: Deterministic Test Fixture Synthesis from Sanitized Traces.

Acceptance criteria:
(1) complete credential sanitization (netloc, headers, queries, postData, emails)
(2) offline execution in CI without network access
(3) fixture parity with recorded payloads
Remedies for prior refute (O943 / row919-local-VERDICT-correctness.md):
- E1: Generate complete executable pytest test cases with mock HTTP servers
- E2: Scrub netloc/postData/query credentials exhaustively
- E3: Emit valid base64 mock media containers (no corrupted raw 'X' text)
- E4: Wire test into CI workflows (.github/workflows/ci.yml)
- E5: Behavioral RED on base
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest

BD_GATE_SCOPE = "repo-wide"

_ROOT = Path(__file__).resolve().parents[1]
_TOOL = _ROOT / "toolchain" / "bin" / "bd-fixture-gen"
_CI_YML = _ROOT / ".github" / "workflows" / "ci.yml"

_SAMPLE_HAR_DATA = {
    "log": {
        "version": "1.2",
        "creator": {"name": "bd-trace-recorder", "version": "1.0"},
        "entries": [
            {
                "startedDateTime": "2026-09-20T07:00:00.000Z",
                "time": 45,
                "request": {
                    "method": "POST",
                    "url": "https://admin:superSecret123@media.example.com/api/v1/stream?api_key=secretKey999&token=jwt_xyz_888&user_id=42",
                    "httpVersion": "HTTP/1.1",
                    "headers": [
                        {"name": "Host", "value": "media.example.com"},
                        {"name": "Authorization", "value": "Bearer mySecretBearerToken"},
                        {"name": "Cookie", "value": "session_id=sessionSecret999"},
                        {"name": "X-Auth-Token", "value": "customToken123"}
                    ],
                    "postData": {
                        "mimeType": "application/json",
                        "text": json.dumps({
                            "username": "testuser",
                            "password": "myPlainPassword",
                            "email": "victim@example.com",
                        })
                    }
                },
                "response": {
                    "status": 200,
                    "statusText": "OK",
                    "httpVersion": "HTTP/1.1",
                    "headers": [
                        {"name": "Content-Type", "value": "application/json"}
                    ],
                    "content": {
                        "size": 65,
                        "mimeType": "application/json",
                        "text": '{"status": "ok", "streamUrl": "https://media.example.com/vod/sample.mp4"}'
                    }
                }
            },
            {
                "startedDateTime": "2026-09-20T07:00:01.000Z",
                "time": 60,
                "request": {
                    "method": "GET",
                    "url": "https://media.example.com/vod/sample.mp4?auth_token=streamAuth456",
                    "httpVersion": "HTTP/1.1",
                    "headers": [
                        {"name": "Host", "value": "media.example.com"}
                    ]
                },
                "response": {
                    "status": 200,
                    "statusText": "OK",
                    "httpVersion": "HTTP/1.1",
                    "headers": [
                        {"name": "Content-Type", "value": "video/mp4"}
                    ],
                    "content": {
                        "size": 32,
                        "mimeType": "video/mp4",
                        "encoding": "base64",
                        "text": "AAAAHGZ0eXBtcDQyAAAAAGlzb21tcDQyAAAAAAAAAAAAAAAA"
                    }
                }
            }
        ]
    }
}


def _create_fixture_har(tmp_path: Path) -> Path:
    har_file = tmp_path / "sample_trace.har"
    har_file.write_text(json.dumps(_SAMPLE_HAR_DATA), encoding="utf-8")
    return har_file


def _run_tool(*args: str) -> subprocess.CompletedProcess[str]:
    """Run bd-fixture-gen with arguments."""
    assert _TOOL.is_file(), f"bd-fixture-gen must exist at {_TOOL}"
    assert os.access(_TOOL, os.X_OK), f"bd-fixture-gen must be executable at {_TOOL}"
    return subprocess.run(
        [sys.executable, str(_TOOL), *args],
        capture_output=True,
        text=True,
    )


def test_tool_executable_exists():
    """Verify toolchain binary exists and is executable."""
    assert _TOOL.is_file(), f"Tool not found: {_TOOL}"
    assert os.access(_TOOL, os.X_OK), f"Tool not executable: {_TOOL}"


def test_complete_credential_sanitization(tmp_path: Path):
    """AC 1 & Remedy E2: Exhaustive credential and personal data sanitization."""
    har_path = _create_fixture_har(tmp_path)
    out_dir = tmp_path / "sanitized_out"

    result = _run_tool(str(har_path), "--out", str(out_dir), "--name", "sample")
    assert result.returncode == 0, f"Tool failed: {result.stderr}\n{result.stdout}"

    sanitized_har_path = out_dir / "trace.sanitized.har"
    assert sanitized_har_path.is_file(), f"Missing sanitized HAR at {sanitized_har_path}"

    har_data = json.loads(sanitized_har_path.read_text(encoding="utf-8"))
    entries = har_data.get("log", {}).get("entries", [])
    assert len(entries) >= 2, "Expected at least 2 entries in sanitized HAR"

    # Check Request 1 (was: https://admin:superSecret123@media.example.com/api/v1/stream?api_key=secretKey999&token=jwt_xyz_888&user_id=42)
    req1 = entries[0]["request"]
    url1 = req1["url"]
    assert "admin" not in url1 and "superSecret123" not in url1, f"Netloc credentials leaked in {url1}"
    assert "secretKey999" not in url1 and "jwt_xyz_888" not in url1, f"Query secrets leaked in {url1}"
    assert "user_id=42" in url1, "Non-sensitive query parameter should be preserved"

    # Check Headers
    header_names = {h["name"].lower() for h in req1.get("headers", [])}
    assert "authorization" not in header_names, "Authorization header leaked"
    assert "cookie" not in header_names, "Cookie header leaked"
    assert "x-auth-token" not in header_names, "X-Auth-Token header leaked"
    assert "host" in header_names, "Safe headers like Host must be preserved"

    # Check PostData (was: password, email)
    post_data_raw = req1.get("postData", {}).get("text", "")
    assert "myPlainPassword" not in post_data_raw, f"Password leaked in postData: {post_data_raw}"
    assert "victim@example.com" not in post_data_raw, f"Email leaked in postData: {post_data_raw}"


def test_valid_base64_media_mocking(tmp_path: Path):
    """Remedy E3: Valid base64 encoding for media responses without corruption."""
    har_path = _create_fixture_har(tmp_path)
    out_dir = tmp_path / "media_out"
    result = _run_tool(str(har_path), "--out", str(out_dir), "--name", "media_test")
    assert result.returncode == 0, result.stderr

    har_data = json.loads((out_dir / "trace.sanitized.har").read_text(encoding="utf-8"))
    media_entry = next(
        e for e in har_data["log"]["entries"]
        if e["response"]["content"].get("mimeType") == "video/mp4"
    )
    content = media_entry["response"]["content"]
    assert content.get("encoding") == "base64", "Media content must be base64-encoded"

    media_b64 = content.get("text", "")
    assert "XXXX" not in media_b64, "Raw 'X' characters must not corrupt base64 media payload"

    # Must decode cleanly without binascii errors
    decoded = base64.b64decode(media_b64)
    assert len(decoded) > 0, "Decoded mock media payload must not be empty"


def test_executable_pytest_test_case_generation_and_offline_run(tmp_path: Path):
    """AC 2 & Remedy E1: Generate complete executable pytest test case and run offline."""
    har_path = _create_fixture_har(tmp_path)
    out_dir = tmp_path / "pytest_gen_out"
    result = _run_tool(str(har_path), "--out", str(out_dir), "--name", "stream")
    assert result.returncode == 0, result.stderr

    generated_test_path = out_dir / "test_stream_fixture.py"
    assert generated_test_path.is_file(), f"Generated test file missing: {generated_test_path}"

    test_content = generated_test_path.read_text(encoding="utf-8")
    assert "def test_" in test_content, "Generated fixture must define real pytest test functions"
    assert "Mock" in test_content or "server" in test_content or "http" in test_content

    # Execute generated test file with pytest in a child process
    test_run = subprocess.run(
        [sys.executable, "-m", "pytest", str(generated_test_path), "-v"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert test_run.returncode == 0, (
        f"Generated test file failed to pass:\nSTDOUT:\n{test_run.stdout}\nSTDERR:\n{test_run.stderr}"
    )
    assert "passed" in test_run.stdout


def test_ci_workflow_wiring():
    """Remedy E4: Verify tests/test_row919.py is wired into .github/workflows/ci.yml."""
    assert _CI_YML.is_file(), f"Missing {_CI_YML}"
    ci_text = _CI_YML.read_text(encoding="utf-8")
    assert "tests/test_row919.py" in ci_text, (
        "tests/test_row919.py is not wired into .github/workflows/ci.yml"
    )
