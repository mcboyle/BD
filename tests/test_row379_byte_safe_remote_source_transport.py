"""Remote generated source is bytes, not nested shell grammar (row 379)."""
from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
import pathlib


BD_GATE_SCOPE = "module"

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL = ROOT / "toolchain" / "bin" / "bd-sweep-run"


def _load_tool():
    loader = SourceFileLoader("bd_sweep_run_row379", str(TOOL))
    spec = importlib.util.spec_from_loader("bd_sweep_run_row379", loader)
    assert spec and spec.loader, "precondition: bd-sweep-run must be loadable"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _corrupt_base64(payload: str) -> str:
    """Keep the payload decodable while changing its decoded bytes."""
    assert payload and payload[0] in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    replacement = "A" if payload[0] != "A" else "B"
    corrupted = replacement + payload[1:]
    assert corrupted != payload, "precondition: corruption must change the payload"
    return corrupted


def test_remote_runner_source_arrives_byte_for_byte_without_a_nested_heredoc(tmp_path):
    """A delimiter collision and UTF-8 source are data, never shell syntax."""
    mod = _load_tool()
    rundir = tmp_path / "run"
    runner = (
        "#!/usr/bin/env bash\n"
        "# the old nested-heredoc terminator must be harmless source data\n"
        "BD_SWEEP_RUNNER_EOF\n"
        "printf '%s\\n' 'snowman: ☃; quote: '\"'\"''\n"
    )
    expected = runner.encode("utf-8")
    payload, digest = mod.encode_remote_source(runner)

    assert b"BD_SWEEP_RUNNER_EOF" in expected, "precondition: delimiter collision absent"
    assert b"\xe2\x98\x83" in expected, "precondition: UTF-8 byte case absent"
    assert payload, "precondition: source encoder returned no transport bytes"
    assert len(digest) == 64, "precondition: source digest is not SHA-256 shaped"

    launch = mod.build_launch(str(rundir), runner)
    assert payload in launch, "the launch script did not carry the encoded source"
    assert "base64 -d" in launch, "the launch script did not decode source bytes"
    assert "cat >" not in launch and "<<'" not in launch, (
        "runner source is still embedded as a nested shell heredoc")

    rc, out, err = mod.LocalTransport().run(launch)
    assert rc == 0, "the byte-safe launch did not complete: %s %s" % (out, err)
    runner_path = rundir / "runner.sh"
    assert runner_path.exists(), "the source was not published as runner.sh"
    assert runner_path.read_bytes() == expected, "remote runner bytes changed in transport"


def test_corrupt_remote_source_is_refused_before_runner_publication(tmp_path):
    """Negative control: decodable corruption cannot become a runnable source."""
    mod = _load_tool()
    rundir = tmp_path / "run"
    runner = "#!/usr/bin/env bash\nprintf '%s\\n' pristine\n"
    payload, _digest = mod.encode_remote_source(runner)
    launch = mod.build_launch(str(rundir), runner)
    corrupted = _corrupt_base64(payload)
    assert payload in launch, "precondition: launch lacks the source payload"
    tampered_launch = launch.replace(payload, corrupted, 1)
    assert tampered_launch != launch, "precondition: launch corruption did not apply"

    rc, out, err = mod.LocalTransport().run(tampered_launch)
    assert rc == 70, "corrupt source was not refused (rc=%s, out=%r, err=%r)" % (rc, out, err)
    assert "BD-SWEEP-SOURCE-DIGEST-MISMATCH" in out, out
    assert not (rundir / "runner.sh").exists(), (
        "corrupt source was published despite its digest mismatch")
