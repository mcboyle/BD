"""bh2-37 (findings/BH2-inbox-drain-bd-agy-sonnet-1.md #2): bd-inbox-drain.sh's PM relay filter drops
a batch as "cron noise" when it holds a cron line and its only other message has no '[from' header.

The filter counted non-cron lines among the '[from ' lines only, so a headerless message counted 0 and
the whole batch was skipped with the marker advanced: the message was never announced and never retried.

The candidate is a harness script, run from its absolute FIX path (BD_BH2_37_CANDIDATE). It never sees
real tmux or bd-say: only inbox/PM is populated (the per-seat loop, the one tmux caller, has nothing to
visit), a fake tmux that refuses every session is first on PATH anyway, and BD_INBOX_DRAIN_SAY is a
recorder script.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_BH2_37_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required (BD_BH2_37_CANDIDATE)")

_HEADER = "# BATCH 2 2026-10-02T17:00:00.000000+00:00\n"


def _run(tmp_path: Path, batch_text: str, seat: str = ""):
    cand = Path(CANDIDATE)
    assert cand.is_file() and os.access(cand, os.X_OK), f"BD_BH2_37_CANDIDATE not an executable file: {cand}"
    root = tmp_path / "root"
    pm = root / "inbox" / "PM"
    pm.mkdir(parents=True)
    (root / "PM-SEAT").write_text("bd-pm-test\n")
    if seat:
        (root / "inbox" / seat).mkdir()
    batch = pm / "BATCH-20261002T170000Z-0123abcd.md"
    batch.write_text(batch_text)
    assert batch.read_text().strip(), "fixture batch is empty"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    tmux = bin_dir / "tmux"
    tmux.write_text(f'#!/bin/sh\necho "tmux $*" >> "{calls}"\nexit 1\n')
    say = bin_dir / "fake-say"
    say.write_text(f'#!/bin/sh\necho "say $*" >> "{calls}"\nexit 0\n')
    for f in (tmux, say):
        f.chmod(0o755)
    env = dict(os.environ)
    env.update({
        "PATH": f"{bin_dir}:{env.get('PATH', '/usr/bin:/bin')}",
        "BD_INBOX_DRAIN_ROOT": str(root),
        "BD_INBOX_DRAIN_SAY": str(say),
        "BD_INBOX_DRAIN_DEBOUNCE_SEC": "0",
        "BD_INBOX_DRAIN_MIN_ITEMS": "0",
    })
    env.pop("BD_INBOX_DRAIN_DRY", None)
    proc = subprocess.run(["bash", str(cand)], env=env, capture_output=True, text=True, timeout=60)
    log = calls.read_text() if calls.exists() else ""
    return proc, log, pm / ".relay-drained", batch


def test_a_headerless_message_beside_cron_noise_is_announced(tmp_path):
    proc, log, marker, batch = _run(
        tmp_path, _HEADER + "[from cron:bd-relay] WAKE /x/board/pm.md\nplease review /x/DONE.md (no from header)\n")
    assert proc.returncode == 0, proc.stderr
    assert f"say pm INBOX 1 relay batch(es): {batch}" in log, (
        "bh2-37: a batch whose only non-cron message has no '[from' header was dropped as cron noise; calls=" + repr(log))


def test_a_cron_only_batch_is_still_dropped(tmp_path):
    """Negative control: the O1418 filter still drops pure cron noise and advances the marker."""
    proc, log, marker, _batch = _run(
        tmp_path, _HEADER + "[from cron:bd-relay] WAKE /x/board/pm.md\n\n[from: cron:bd-auto-away] AUTO-AWAY\n")
    assert proc.returncode == 0, proc.stderr
    assert "say " not in log, log
    assert marker.exists(), "cron-only batch must advance the marker silently"


def test_a_from_headed_message_beside_cron_noise_is_announced(tmp_path):
    """Positive control: the shape the old filter already handled stays announced."""
    proc, log, _marker, batch = _run(
        tmp_path, _HEADER + "[from cron:bd-relay] WAKE /x/board/pm.md\n[from bd-worker-x 17:00Z] DONE /x/DONE.md\n")
    assert proc.returncode == 0, proc.stderr
    assert f"say pm INBOX 1 relay batch(es): {batch}" in log, log


def test_no_real_tmux_or_say_is_reached(tmp_path):
    proc, log, _marker, _batch = _run(tmp_path, _HEADER + "[from cron:bd-relay] WAKE\nheaderless\n")
    assert proc.returncode == 0, proc.stderr
    assert not [ln for ln in log.splitlines() if ln.startswith("tmux ")], (
        "the per-seat loop ran against the fixture root; only inbox/PM exists: " + log)


def test_a_seat_inbox_reaches_the_fake_tmux_not_a_real_one(tmp_path):
    """Positive control for the check above: with a seat mailbox present the per-seat loop asks tmux, and the
    recorder is what answers (it refuses the session, so nothing is filed or sent for that seat)."""
    proc, log, _marker, _batch = _run(tmp_path, _HEADER + "[from cron:bd-relay] WAKE\n", seat="bd-worker-fixture")
    assert proc.returncode == 0, proc.stderr
    assert "tmux has-session -t bd-worker-fixture:" in log, log
