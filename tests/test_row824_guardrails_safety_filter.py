import asyncio
import sys
from unittest.mock import patch

import importlib

import pytest

import bdctl

guardrails = importlib.import_module("bulk_downloader.guardrails")
ssrf_egress_exemptions = importlib.import_module("bulk_downloader.ssrf_egress_exemptions")

BD_GATE_SCOPE = "module"


def test_pre_download_filter_blocks_known_violation_title():
    seen = {}

    async def request(payload, endpoint):
        seen["payload"] = payload
        seen["endpoint"] = endpoint
        return {"message": {"content": "unsafe\nS1"}}

    allowed = asyncio.run(guardrails.pre_download_safety_check(
        {"title": "known violation prompt"}, enabled=True, request=request
    ))

    assert allowed is False
    assert seen["endpoint"] == guardrails.DEFAULT_GUARDRAILS_ENDPOINT
    assert seen["payload"]["messages"][0]["content"] == "title: known violation prompt"


def test_pre_download_filter_passes_benign_metadata():
    async def request(payload, endpoint):
        return {"message": {"content": "safe"}}

    allowed = asyncio.run(guardrails.pre_download_safety_check(
        {"title": "benign download request"}, enabled=True, request=request
    ))

    assert allowed is True


def test_pre_download_filter_uses_default_transport_without_blocking_loop(monkeypatch):
    seen = {}

    class Response:
        def read(self):
            return b'{"message": {"content": "unsafe"}}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def urlopen(request, timeout):
        seen["endpoint"] = request.full_url
        assert timeout == 2
        return Response()

    monkeypatch.setattr(guardrails, "urlopen", urlopen)

    allowed = asyncio.run(guardrails.pre_download_safety_check(
        {"title": "known violation prompt"}, enabled=True
    ))

    assert allowed is False
    assert seen["endpoint"] == guardrails.DEFAULT_GUARDRAILS_ENDPOINT


def test_pre_download_filter_fails_open_when_endpoint_is_unreachable():
    async def request(payload, endpoint):
        raise OSError("connection refused")

    allowed = asyncio.run(guardrails.pre_download_safety_check(
        {"title": "benign download request"}, enabled=True, request=request
    ))

    assert allowed is True


def test_pre_download_filter_bypasses_endpoint_when_disabled():
    async def request(payload, endpoint):
        raise AssertionError("disabled filter must not query its endpoint")

    allowed = asyncio.run(guardrails.pre_download_safety_check(
        {"title": "known violation prompt"}, enabled=False, request=request
    ))

    assert allowed is True


def test_negative_control_unhandled_violation_never_passes():
    """Negative control: unsafe response must never evaluate to allowed."""
    async def request(payload, endpoint):
        return {"unsafe": True}

    allowed = asyncio.run(guardrails.pre_download_safety_check(
        {"title": "violation"}, enabled=True, request=request
    ))
    assert allowed is False


def test_exact_count_of_accounted_egress_and_max_metadata_chars():
    """Exact count assertion: exactly 27 egress exemptions (row826 +1: semantic_search::_rerank) and 4096 char limit."""
    assert len(ssrf_egress_exemptions.ACCOUNTED) == 27
    assert guardrails._MAX_METADATA_CHARS == 4096
    assert "bulk_downloader/guardrails.py::_default_request" in ssrf_egress_exemptions.ACCOUNTED


def test_bdctl_parser_registers_enable_guardrails_flag():
    """Verify bdctl root parser and subparsers accept --enable-guardrails."""
    called = []

    def fake_status(args):
        called.append(args.enable_guardrails)

    with patch.object(sys, "argv", ["bdctl", "--enable-guardrails", "status"]), \
         patch.object(bdctl, "cmd_status", fake_status):
        bdctl.main()
    assert called == [True]

    called.clear()
    with patch.object(sys, "argv", ["bdctl", "status", "--enable-guardrails"]), \
         patch.object(bdctl, "cmd_status", fake_status):
        bdctl.main()
    assert called == [True]

    called.clear()
    with patch.object(sys, "argv", ["bdctl", "status"]), \
         patch.object(bdctl, "cmd_status", fake_status):
        bdctl.main()
    assert called == [False]


def test_bdctl_cli_add_rejects_violation_when_guardrails_enabled(monkeypatch, capsys):
    """Production CLI entry point invokes safety check and rejects violations."""
    posted_requests = []

    def fake_request(method, path, body=None, query=None):
        posted_requests.append((method, path, body))
        return {"ok": True}

    async def fake_safety(metadata, *, enabled=False, request=None):
        if "violation" in str(metadata.get("title", "")) or "violation" in str(metadata.get("url", "")):
            return False
        return True

    monkeypatch.setattr(bdctl, "_request", fake_request)
    monkeypatch.setattr(guardrails, "pre_download_safety_check", fake_safety)

    class Args:
        site = None
        urls = ["https://example.com/violation-test"]
        mode = "append"
        enable_guardrails = True

    with pytest.raises(SystemExit) as exc_info:
        bdctl.cmd_add(Args())

    assert "All URLs blocked" in str(exc_info.value)
    assert len(posted_requests) == 0
    captured = capsys.readouterr()
    assert "Blocked unsafe download: https://example.com/violation-test" in captured.err


def test_bdctl_cli_add_passes_benign_when_guardrails_enabled(monkeypatch):
    """Production CLI entry point allows benign downloads through."""
    posted_requests = []

    def fake_request(method, path, body=None, query=None):
        posted_requests.append((method, path, body))
        return {"ok": True}

    async def fake_safety(metadata, *, enabled=False, request=None):
        return True

    monkeypatch.setattr(bdctl, "_request", fake_request)
    monkeypatch.setattr(guardrails, "pre_download_safety_check", fake_safety)

    class Args:
        site = None
        urls = ["https://example.com/benign-item"]
        mode = "append"
        enable_guardrails = True

    bdctl.cmd_add(Args())
    assert len(posted_requests) == 1
    assert posted_requests[0][1] == "/api/quick_add"
    assert posted_requests[0][2] == {"url": "https://example.com/benign-item"}


def test_bdctl_cli_main_dispatches_add_with_enable_guardrails(monkeypatch):
    """Integration: bdctl.main() with ['--enable-guardrails', 'add', ...]."""
    checked = []

    async def fake_safety(metadata, *, enabled=False, request=None):
        checked.append((metadata, enabled))
        return False

    monkeypatch.setattr(guardrails, "pre_download_safety_check", fake_safety)

    with patch.object(sys, "argv", ["bdctl", "--enable-guardrails", "add", "https://example.com/blocked-url"]), \
         pytest.raises(SystemExit) as exc_info:
        bdctl.main()

    assert "All URLs blocked" in str(exc_info.value)
    assert len(checked) == 1
    assert checked[0][1] is True
    assert checked[0][0]["title"] == "https://example.com/blocked-url"
