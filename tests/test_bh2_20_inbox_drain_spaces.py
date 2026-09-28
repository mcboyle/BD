"""Regression test for BH2-20: bd-inbox-drain.sh null-safe iteration and cat failure handling.

Defect: In bd-inbox-drain.sh, `for f in $NEWF` word-splits filenames with spaces.
The script fails to cat the fragments but still writes the # TERSE header, passing the
[ -s "$LIST" ] check. The marker is advanced and file contents are permanently lost.
Fix: Iterate files using null-delimited find/read (-print0 / read -d '') and verify
that every file in the batch is successfully concatenated before advancing the marker,
while still failing the write when the batch redirect itself fails (REFUTE bd-cx-worker-2).
"""

import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE_SCRIPT = os.environ.get("BD_BH2_20_CANDIDATE", "")

pytestmark = pytest.mark.skipif(
    not CANDIDATE_SCRIPT,
    reason="candidate opt-in required (BD_BH2_20_CANDIDATE)",
)


def _setup_test_env(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)

    # Mock tmux so tmux has-session succeeds
    mock_tmux = bin_dir / "tmux"
    mock_tmux.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    mock_tmux.chmod(0o755)

    # Mock say
    say_log = tmp_path / "say.log"
    mock_say = bin_dir / "bd-say.sh"
    mock_say.write_text(
        f"#!/usr/bin/env bash\necho \"$@\" >> '{say_log}'\nexit 0\n",
        encoding="utf-8",
    )
    mock_say.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["BD_INBOX_DRAIN_ROOT"] = str(tmp_path)
    env["BD_INBOX_DRAIN_SAY"] = str(mock_say)
    env["BD_INBOX_DRAIN_NOW"] = "1727547600"
    return env, say_log


def test_candidate_handles_filenames_with_spaces(tmp_path: Path):
    """Filenames with spaces must be preserved and their content concatenated."""
    assert os.path.isfile(CANDIDATE_SCRIPT), (
        f"candidate script {CANDIDATE_SCRIPT} missing"
    )

    env, _say_log = _setup_test_env(tmp_path)
    inbox_dir = tmp_path / "inbox" / "seat-worker"
    inbox_dir.mkdir(parents=True)

    msg_file = inbox_dir / "msg with spaces in name.md"
    msg_content = "[from: operator] critical order with spaces in payload"
    msg_file.write_text(msg_content, encoding="utf-8")

    proc = subprocess.run(
        ["bash", CANDIDATE_SCRIPT],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"script failed: {proc.stderr}\n{proc.stdout}"

    batch_files = list(inbox_dir.glob("BATCH-*.md"))
    assert len(batch_files) == 1, f"expected 1 batch file, found: {batch_files}"

    batch_content = batch_files[0].read_text(encoding="utf-8")
    assert "--- msg with spaces in name.md" in batch_content, (
        f"filename with spaces was not properly header-formatted:\n{batch_content}"
    )
    assert msg_content in batch_content, (
        f"content of message with spaces was lost from batch:\n{batch_content}"
    )

    marker = inbox_dir / ".drained"
    assert marker.is_file(), (
        "marker .drained was not advanced after successful delivery"
    )


def test_candidate_does_not_advance_marker_on_cat_fail(tmp_path: Path):
    """If cat fails for a file, the marker must NOT be advanced and batch aborted."""
    assert os.path.isfile(CANDIDATE_SCRIPT), (
        f"candidate script {CANDIDATE_SCRIPT} missing"
    )

    env, say_log = _setup_test_env(tmp_path)
    inbox_dir = tmp_path / "inbox" / "seat-worker"
    inbox_dir.mkdir(parents=True)

    # Create unreadable file followed by readable file
    unreadable = inbox_dir / "a_unreadable.md"
    unreadable.write_text("secret payload", encoding="utf-8")
    unreadable.chmod(0o000)

    readable = inbox_dir / "z_readable.md"
    readable.write_text("subsequent readable payload", encoding="utf-8")

    try:
        assert not os.access(unreadable, os.R_OK), (
            "positive control: fixture file is still readable"
        )
        proc = subprocess.run(
            ["bash", CANDIDATE_SCRIPT],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        marker = inbox_dir / ".drained"
        assert not marker.is_file(), (
            ".drained marker was incorrectly advanced despite cat failure on a_unreadable.md"
        )
        assert proc.returncode == 5, (
            f"expected H254 rc 5, got {proc.returncode}: {proc.stderr}"
        )
        assert "H254 NOT-ANNOUNCED" in proc.stderr, proc.stderr
        assert not say_log.exists(), (
            f"batch was announced: {say_log.read_text(encoding='utf-8')}"
        )
    finally:
        unreadable.chmod(0o644)


def test_candidate_does_not_announce_when_batch_redirect_fails(tmp_path: Path):
    """An unwritable existing batch file must fail the write, not announce stale content."""
    assert os.path.isfile(CANDIDATE_SCRIPT), (
        f"candidate script {CANDIDATE_SCRIPT} missing"
    )

    env, say_log = _setup_test_env(tmp_path)
    inbox_dir = tmp_path / "inbox" / "seat-worker"
    inbox_dir.mkdir(parents=True)

    (inbox_dir / "new.md").write_text("[from: operator] fresh order", encoding="utf-8")
    stale = inbox_dir / "BATCH-20240928T1820Z.md"  # the name BD_INBOX_DRAIN_NOW yields
    stale.write_text("stale batch content", encoding="utf-8")
    stale.chmod(0o444)

    try:
        assert not os.access(stale, os.W_OK), (
            "positive control: stale batch is still writable"
        )
        proc = subprocess.run(
            ["bash", CANDIDATE_SCRIPT],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 5, (
            f"expected H254 rc 5, got {proc.returncode}: {proc.stderr}"
        )
        assert "H254 NOT-ANNOUNCED" in proc.stderr, proc.stderr
        assert not (inbox_dir / ".drained").is_file(), (
            "marker advanced on a failed batch write"
        )
        assert not say_log.exists(), (
            f"stale batch was announced: {say_log.read_text(encoding='utf-8')}"
        )
        assert stale.read_text(encoding="utf-8") == "stale batch content", (
            "existing batch file was altered"
        )
    finally:
        stale.chmod(0o644)
