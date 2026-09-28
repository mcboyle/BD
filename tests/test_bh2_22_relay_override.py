"""BH2-22 (findings/BH2-board-coalescing-bd-agy-sonnet-1.md #2): bd-relay.sh TARGET_OVERRIDE misroutes a seat inbox.

TARGET_OVERRIDE (BD_RELAY_TARGET, else BD_SAY_TARGET / BD_SAY_PANE) names the pane that gets the WAKE. The relay also
added inbox/<override>/ to EVERY role's sweep, so the first role in ROLES (pm) coalesced that seat's direct messages
onto board/pm.md and archived them: a message to one seat landed on the PM board, and an override seat that is the
integrator incumbent lost its messages to pm as well. Fix: the override redirects the WAKE only; inboxes are swept
by role and incumbent, as without it.

The harness is deployed from bd-persist, not this repo: BD_BH2_22_CANDIDATE is the absolute path of the candidate
bd-relay.sh. Every run is hermetic (tmp inbox/board/log/lock, stub claim + say scripts, no tmux session named).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_BH2_22_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

OVERRIDE = "bh2-22-no-such-tmux-session"


@pytest.fixture
def env(tmp_path: Path) -> dict[str, Path]:
    d = {k: tmp_path / k for k in ("inbox", "board", "blobs")}
    for p in d.values():
        p.mkdir()
    claim = tmp_path / "claim.sh"
    claim.write_text(
        '#!/bin/bash\ncase "$2" in pm) echo pm-seat ;; integrator) echo int-seat ;; esac\n'
    )
    say = tmp_path / "say.sh"
    say.write_text(f'#!/bin/bash\necho "$*" >> "{tmp_path}/said.log"\n')
    for s in (claim, say):
        s.chmod(0o755)
    d.update(tmp=tmp_path, claim=claim, say=say)
    return d


def _msg(env: dict[str, Path], box: str, text: str) -> Path:
    (env["inbox"] / box).mkdir(exist_ok=True)
    p = env["inbox"] / box / f"20260928T000000Z-{box}.md"
    p.write_text(f"[from tester] {text}\n")
    return p


def _relay(
    env: dict[str, Path], target: str | None
) -> subprocess.CompletedProcess[str]:
    script = Path(CANDIDATE)
    assert script.is_file(), f"candidate missing: {CANDIDATE}"
    e = {
        k: v
        for k, v in os.environ.items()
        if k not in ("BD_RELAY_TARGET", "BD_SAY_TARGET", "BD_SAY_PANE")
    }
    e.update(
        LC_ALL="C",
        BD_RELAY_INBOX_ROOT=str(env["inbox"]),
        BD_RELAY_BOARD_DIR=str(env["board"]),
        BD_RELAY_LOG=str(env["tmp"] / "relay.log"),
        BD_RELAY_LOCK=str(env["tmp"] / "relay.lock"),
        BD_RELAY_ROLES="pm integrator",
        BD_RELAY_CLAIM_SH=str(env["claim"]),
        BD_RELAY_SAY_SH=str(env["say"]),
        BD_RELAY_BLOB_DIR=str(env["blobs"]),
    )
    if target is not None:
        e["BD_RELAY_TARGET"] = target
    return subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        env=e,
        timeout=60,
        check=False,
    )


def _board(env: dict[str, Path], role: str) -> str:
    p = env["board"] / f"{role}.md"
    return p.read_text() if p.exists() else ""


def test_override_seat_inbox_is_not_swept_onto_pm_board(env: dict[str, Path]) -> None:
    m = _msg(env, OVERRIDE, "direct-to-override-seat")
    res = _relay(env, OVERRIDE)
    assert res.returncode == 0, res
    assert "direct-to-override-seat" not in _board(env, "pm"), (
        "RELAY_OVERRIDE_MISROUTE: a seat's direct message was coalesced onto board/pm.md"
    )
    assert m.exists(), (
        "RELAY_OVERRIDE_MISROUTE: the seat's message was archived off its own inbox"
    )


def test_override_incumbent_inbox_goes_to_its_own_role_board(
    env: dict[str, Path],
) -> None:
    _msg(env, "int-seat", "for-the-integrator")
    res = _relay(env, "int-seat")
    assert res.returncode == 0, res
    assert "for-the-integrator" not in _board(env, "pm"), (
        "RELAY_OVERRIDE_MISROUTE: integrator incumbent's message landed on board/pm.md"
    )
    assert "for-the-integrator" in _board(env, "integrator")


@pytest.mark.parametrize("target", [None, OVERRIDE])
def test_control_role_and_incumbent_inboxes_still_coalesce(
    env: dict[str, Path], target: str | None
) -> None:
    pm_role = _msg(env, "PM", "pm-role-box")
    pm_inc = _msg(env, "pm-seat", "pm-incumbent-box")
    int_role = _msg(env, "integrator", "integrator-role-box")
    int_inc = _msg(env, "int-seat", "integrator-incumbent-box")
    res = _relay(env, target)
    assert res.returncode == 0, res
    pm, integ = _board(env, "pm"), _board(env, "integrator")
    assert "pm-role-box" in pm and "pm-incumbent-box" in pm
    assert "integrator-role-box" in integ and "integrator-incumbent-box" in integ
    assert "integrator-role-box" not in pm and "pm-role-box" not in integ
    for p in (pm_role, pm_inc, int_role, int_inc):
        assert not p.exists() and (p.parent / "coalesced" / p.name).exists()


def test_control_no_override_leaves_unrelated_seat_inbox_alone(
    env: dict[str, Path],
) -> None:
    m = _msg(env, OVERRIDE, "not-a-role")
    res = _relay(env, None)
    assert res.returncode == 0, res
    assert m.exists() and _board(env, "pm") == "" and _board(env, "integrator") == ""
