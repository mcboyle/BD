"""The derived affected band must name real current provider-resolve suites."""
import json
from pathlib import Path
import subprocess
import sys

import pytest


BD_GATE_SCOPE = "module"
ROOT = Path(__file__).resolve().parents[1]

_PROVIDER_INPUTS = (
    "bulk_downloader/ytdlp_updater.py",
    "bulk_downloader/runner_extractors.py",
    "bulk_downloader/ytdlp_extractor.py",
    "bulk_downloader/provider_resolve_impl/youtube.py",
)
_REQUIRED_SUITES = (
    "tests/test_v3_66_16_phase4_p4_provider_resolve.py",
    "tests/test_v3_66_26_phase4_youtube_cipher.py",
)
_EXPECTED_COMMAND = (
    sys.executable,
    "toolchain/bin/bd-band-derive",
    "--files",
    *_PROVIDER_INPUTS,
    "--json",
)


def test_provider_resolve_band_contains_only_existing_current_suites():
    result = subprocess.run(
        list(_EXPECTED_COMMAND),
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    band = json.loads(result.stdout)["band"]
    missing = [path for path in band if not (ROOT / path).is_file()]
    assert not missing, f"derived provider-resolve band names missing suites: {missing}"
    for required in _REQUIRED_SUITES:
        assert required in band


def _assert_exact_provider_band_delegation(monkeypatch, gate):
    calls = []
    delegated_band = list(_REQUIRED_SUITES)

    def recording_run(command, **kwargs):
        calls.append((tuple(command), kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"band": delegated_band}),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", recording_run)
    gate()

    expected = [(
        _EXPECTED_COMMAND,
        {
            "cwd": ROOT,
            "check": True,
            "capture_output": True,
            "text": True,
        },
    )]
    assert calls == expected, (
        "provider band delegation receipt missing or mismatched: "
        f"expected one exact bd-band-derive call, observed {calls}"
    )


def test_provider_band_gate_requires_exact_delegation_receipt(monkeypatch):
    assert len(_PROVIDER_INPUTS) == 4
    assert len(_REQUIRED_SUITES) == 2
    assert all((ROOT / path).is_file() for path in _PROVIDER_INPUTS)
    assert all((ROOT / path).is_file() for path in _REQUIRED_SUITES)

    _assert_exact_provider_band_delegation(
        monkeypatch,
        test_provider_resolve_band_contains_only_existing_current_suites,
    )


def test_provider_band_delegation_negative_control_rejects_noop(monkeypatch):
    gate_calls = []

    def no_op_gate():
        gate_calls.append("entered")

    with pytest.raises(
        AssertionError,
        match=r"provider band delegation receipt missing or mismatched",
    ):
        _assert_exact_provider_band_delegation(monkeypatch, no_op_gate)
    assert gate_calls == ["entered"]


def test_provider_band_transform_control_imports_tool_without_judging_delegation():
    assert (ROOT / "toolchain" / "bin" / "bd-band-derive").is_file()
