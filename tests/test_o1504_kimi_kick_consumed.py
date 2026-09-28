"""bd-launch-kimi.sh must MEASURE that the kick was consumed before reporting it (FINDING-BH-bd-kimi-audit-1-200 (b)).

The launcher sent "BEGIN. SEAT: <name>. ROLE: <role>." and printed "LAUNCHED ... kick=consumed" without looking: a kick
left in the input box (never submitted) still reported consumed, rc 0. Kimi echoes a submitted message into the
transcript as " ✨ <text>"; an unsent one stays in the input box as "│ > <text>". The pane fixtures below are cut from a
real Kimi Code 2.1.1 capture (seat bd-kimi-audit-1, 2026-09-28) with only the seat/role substituted.

The candidate lives outside the repo (harness-work/FIX/o1504-kimi-consumed-bd-worker-A2-A/bd-launch-kimi.sh), opted in
with BD_O1504_KIMI_CANDIDATE. tmux is a PATH stub (same shape as tests/test_bh2_14_kimi_dead_session.py); the seats root
and the role-claim tool are env seams in tmp_path, so no live seat, claim or session is touched.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_O1504_KIMI_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

ROLE = "adjudicator"  # any role with a prompt under bd-persist/role-prompts (read-only)
NAME = "bd-o1504-stubseat"
KICK = f"BEGIN. SEAT: {NAME}. ROLE: {ROLE}."

WELCOME = """\
 │  ▐█▛█▛█▌  Welcome to Kimi Code!                                            │
 │  ▐█████▌  Send /help for help information.                                 │
 ╰──────────────────────────────────────────────────────────────────────────────╯

 ✦ Kimi Code Desktop is here — Everything you love about Kimi Code, now on
   No session yet — one will be created on your first message.
"""
PANES = {
    "consumed": WELCOME
    + f"\n ✨ {KICK}\n … thinking 7 times, call 9 tools, 4 messages\n\n ● Ran a command · $ ls -la\n",
    "stuck": WELCOME
    + f"\n ╭────────────────────╮\n │ > {KICK}                │\n ╰────────────────────╯\n",
    "foreign": WELCOME
    + f"\n ✨ BEGIN. SEAT: {NAME}-2. ROLE: {ROLE}.\n ● Ran a command · $ ls -la\n",
}

TMUX_STUB = r"""#!/bin/bash
echo "$*" >> "$STUB_DIR/calls"
case "$1" in
  has-session)
    [ -e "$STUB_DIR/started" ] || exit 1
    [ "$STUB_MODE" = dies-after-kick ] && [ -e "$STUB_DIR/kicked" ] && exit 1
    exit 0 ;;
  new-session) touch "$STUB_DIR/started"; exit 0 ;;
  capture-pane)
    if [ -e "$STUB_DIR/kicked" ]; then cat "$STUB_DIR/pane.after"; else cat "$STUB_DIR/pane.before"; fi
    exit 0 ;;
  send-keys) case "$*" in *"BEGIN. SEAT:"*) touch "$STUB_DIR/kicked" ;; esac; exit 0 ;;
esac
exit 0
"""


def _exe(p: Path, body: str) -> None:
    p.write_text(body, encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _launch(
    tmp_path: Path, mode: str, tries: int = 2
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    cand = Path(CANDIDATE)
    assert cand.is_file(), f"BD_O1504_KIMI_CANDIDATE={CANDIDATE} is not a file"
    stub = tmp_path / "stub"
    bindir = stub / "bin"
    bindir.mkdir(parents=True)
    _exe(bindir / "tmux", TMUX_STUB)
    _exe(stub / "claim.sh", "#!/bin/bash\nexit 0\n")
    (stub / "pane.before").write_text(WELCOME, encoding="utf-8")
    (stub / "pane.after").write_text(
        PANES.get(mode, PANES["consumed"]), encoding="utf-8"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("BD_KIMI_")}
    env.update(
        LC_ALL="C",
        PATH=f"{bindir}:{env.get('PATH', '/usr/bin:/bin')}",
        STUB_DIR=str(stub),
        STUB_MODE=mode,
        BD_KIMI_SEATS_ROOT=str(tmp_path / "seats"),
        BD_KIMI_ROLE_CLAIM=str(stub / "claim.sh"),
        BD_KIMI_START_TRIES="3",
        BD_KIMI_CONSUME_TRIES=str(tries),
    )
    r = subprocess.run(
        ["bash", str(cand), ROLE, "B", NAME],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    calls_f = stub / "calls"
    return r, calls_f.read_text(
        encoding="utf-8"
    ).splitlines() if calls_f.exists() else []


def test_submitted_kick_reports_consumed(tmp_path: Path) -> None:
    """Positive control: the real " ✨ <kick>" transcript echo -> LAUNCHED ... kick=consumed, rc 0."""
    r, _ = _launch(tmp_path, "consumed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"LAUNCHED {NAME} on kimi" in r.stdout and "kick=consumed" in r.stdout


def test_unsent_kick_is_not_consumed(tmp_path: Path) -> None:
    r, calls = _launch(tmp_path, "stuck")
    assert r.returncode == 7, (
        f"KIMI_KICK_CONSUMED_UNMEASURED rc={r.returncode}: {r.stdout}{r.stderr}"
    )
    assert f"{NAME} did not consume the kick in 2 tries (exit 7)" in r.stdout
    assert (
        KICK in r.stdout
    )  # the pane tail (the stuck input box) is carried in the diagnostic
    assert "LAUNCHED" not in r.stdout
    assert sum(c.startswith("capture-pane") and "-S -200" in c for c in calls) == 2, (
        calls
    )


def test_another_seats_echo_does_not_count(tmp_path: Path) -> None:
    """Negative control: a submitted kick for a different seat name is not this seat's kick."""
    r, _ = _launch(tmp_path, "foreign")
    assert r.returncode == 7, r.stdout + r.stderr
    assert "LAUNCHED" not in r.stdout


def test_session_dying_after_kick_fails_6(tmp_path: Path) -> None:
    r, _ = _launch(tmp_path, "dies-after-kick")
    assert r.returncode == 6, r.stdout + r.stderr
    assert f"session {NAME} EXITED after the kick" in r.stdout
