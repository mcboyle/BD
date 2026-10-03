import json
import subprocess

import pytest
from bulk_downloader import netns_isolation as ni

BD_GATE_SCOPE = "module"


@pytest.mark.parametrize("operation", ["add", "del"])
def test_default_runner_timeout_returns_failure(monkeypatch, operation):
    calls = []
    argv = ["ip", "netns", operation, "bd_timeout_test"]

    def expired(command, **kwargs):
        calls.append((command, kwargs))
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 0))

    monkeypatch.setattr(ni.subprocess, "run", expired)
    assert ni._run(None, argv) == 1
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command == argv
    assert kwargs == {
        "stdin": subprocess.DEVNULL,
        "capture_output": True,
        "text": True,
        "timeout": 30,
    }, f"NETNS_{operation.upper()}_TIMEOUT_UNBOUNDED: {kwargs!r}"


def test_create_timeout_bounds_cleanup_too(monkeypatch):
    calls = []

    def expired(command, **kwargs):
        calls.append((command, kwargs.get("timeout")))
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 0))

    monkeypatch.setattr(ni.subprocess, "run", expired)
    assert ni.create("bd_timeout_test") is False
    assert calls == [
        (["ip", "netns", "add", "bd_timeout_test"], 30),
        (["ip", "netns", "del", "bd_timeout_test"], 30),
    ], f"NETNS_TIMEOUT_CLEANUP_UNBOUNDED: {calls!r}"


@pytest.mark.parametrize("returncode", [0, 7])
def test_injected_runner_negative_control_is_byte_identical(returncode):
    calls = []
    argv = ["ip", "netns", "del", "bd_control"]

    def strict_runner(command, *, stdin, capture_output, text):
        calls.append([command, stdin, capture_output, text])
        return subprocess.CompletedProcess(command, returncode)

    rc = ni._run(strict_runner, argv)
    result = json.dumps([rc, calls], separators=(",", ":")).encode()
    expected = (
        f'[{returncode},[[["ip","netns","del","bd_control"],-3,true,true]]]'
    ).encode()
    assert result == expected, f"NETNS_INJECTED_RUNNER_CHANGED: {result!r}"


def test_injected_timeout_still_returns_failure():
    calls = []
    argv = ["ip", "netns", "add", "bd_timeout_test"]

    def strict_expired(command, *, stdin, capture_output, text):
        calls.append((command, stdin, capture_output, text))
        raise subprocess.TimeoutExpired(command, 1)

    assert ni._run(strict_expired, argv) == 1
    assert calls == [(argv, subprocess.DEVNULL, True, True)]
