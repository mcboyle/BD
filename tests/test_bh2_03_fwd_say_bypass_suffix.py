"""BH2-03 fix-forward: the PM/integrator batch-bypass guard in harness bd-say.sh.

BH2-03 (live 2026-09-28T19:06Z) replaced the prefix globs (ORDER*, STALL*, LIMIT*, ...) with
`grep -qiE "$ESC_RE"`, whose alternation ends in \\b, so an escalation word carrying a suffix
(ORDERS-0026, STALLED, LIMITS, CRASHED, OUTAGES) went to the batch queue again. The candidate
keeps the ESC_RE grep and restores the globs beside it.

The script lives in bd-persist (host-local, outside git): opt in with BD_BH2_03_FWD_CANDIDATE.
The guard line is evaluated with the script's own WAKE/ESC_RE definitions, never by running it.
"""

import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_BH2_03_FWD_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

GUARD_MARK = "bypassing batch queue"


@pytest.fixture
def script() -> Path:
    p = Path(CANDIDATE)
    assert p.is_file(), f"candidate does not exist: {p}"
    return p


def _decide(script: Path, message: str) -> str:
    """BYPASS or RELAY for `message` from a non-PM sender, per the script's own guard line."""
    lines = script.read_text(encoding="utf-8").splitlines()
    defs = [
        ln.strip()
        for ln in lines
        if ln.strip().startswith(("WAKE=", "PM_WAKE_RE=", "ESC_RE="))
    ]
    idx = next(i for i, ln in enumerate(lines) if GUARD_MARK in ln)
    guard = lines[idx - 1]
    assert guard.lstrip().startswith("if "), (
        f"guard line not found above {GUARD_MARK!r}: {guard!r}"
    )
    prog = "\n".join(
        [
            *defs,
            'T="$1"; T0="$1"; _SND="fixture-bh2-03-fwd"; BD_SAY_NO_RELAY=0',
            "cond=$(printf '%s' \"$2\" | sed -e 's/^[[:space:]]*if //' -e 's/; then.*//')",
            'eval "if $cond; then echo BYPASS; else echo RELAY; fi"',
        ]
    )
    res = subprocess.run(
        ["bash", "-c", prog, "_", message, guard],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"},
    )
    assert res.returncode == 0, res.stderr
    return res.stdout.strip()


# suffixed escalation words: bypassed before BH2-03, queued by it
@pytest.mark.parametrize(
    "msg",
    [
        "ORDERS-0026 row1007: rebase",
        "STALLED: band on test5",
        "LIMITS hit pool B",
        "CRASHED bd-worker-A1-A",
        "OUTAGES gitea",
        "POLLING bd-status",
        "REFUSED-x row9",
    ],
)
def test_suffixed_escalation_bypasses(script: Path, msg: str) -> None:
    assert _decide(script, msg) == "BYPASS"


# BH2-03's own gain must survive: ESC_RE words anywhere in the text
@pytest.mark.parametrize(
    "msg",
    [
        "WAKE BOARD NOW",
        "LANDED row1007",
        "bh2-28 DONE: /x/DONE.md",
        "CLAIM row12",
        "DISPATCH row5",
        "REFUTE bh2-03",
        "ESCALATION: pool B",
        "ORDER-O1502 new seats",
    ],
)
def test_esc_re_words_bypass(script: Path, msg: str) -> None:
    assert _decide(script, msg) == "BYPASS"


@pytest.mark.parametrize(
    "msg",
    [
        "routine status check",
        "working on candidate cut",
        "note: review progress updated",
        "hello world",
    ],
)
def test_routine_messages_relay(script: Path, msg: str) -> None:
    assert _decide(script, msg) == "RELAY"


def test_esc_re_itself_unchanged(script: Path) -> None:
    """ESC_RE is shared with the other-seat batch path; the fix must not widen it."""
    text = script.read_text(encoding="utf-8")
    assert (
        'ESC_RE="\\\\b($WAKE|ESC|STOP|DONE|LANDED|REFUTE|CLAIM|DISPATCH)\\\\b"' in text
    )


def test_syntax_clean(script: Path) -> None:
    res = subprocess.run(
        ["bash", "-n", str(script)], capture_output=True, text=True, check=False
    )
    assert res.returncode == 0, res.stderr
