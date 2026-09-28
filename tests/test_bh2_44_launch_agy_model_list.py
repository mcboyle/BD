"""BH2-44: bd-launch-agy.sh refuses a model id that agy itself does not list.

Defect (findings/BH2-launch-agycodex-bd-kimi-audit-2.md#LOW-4): an explicit model id was passed straight to
`agy --model`, so a typo launched a seat that failed at its first turn. The earlier G1/G2 fixes hard-coded an
allowlist that refused ids the fleet launches (A1-A census), and their test ran the real launcher unstubbed (B4-B).
The candidate reads the list from `agy models` (the CLI owns it) and refuses when that list cannot be read.
`agy models` is NOT read-only (it writes ~/.gemini/antigravity-cli state), so --dry-run never runs it (the BH2-13 contract,
HOLD-BH2-44-R1-bd-dispatch-B.md); the gate is exercised on the real-launch path.

Harness candidate test (bd-harness-cut shape): opt in with
BD_BH2_44_CANDIDATE=/home/mboyle/bd-persist/harness-work/FIX/bh2-44-r1-bd-worker-A3-A/bd-launch-agy.sh.
Hermetic: `agy` is a stub at BD_AGY_BIN that replays a REAL `agy models` capture (tests/fixtures/agy_models_20260928.*)
or fails. `tmux` and `mkdir` are PATH stubs that log every call. The mkdir stub REFUSES the real role-systemprompts dir
(exit 97), which is the launcher's first write outside BD_AGY_WORKDIR. So a real-launch run that passes the gate stops
there, with a distinctive diagnostic, before any fleet file, trust entry or tmux session. HOME and BD_AGY_WORKDIR are in
tmp_path. The fixture refuses a SUT with no --dry-run arm or no role-systemprompts mkdir (the stub would not hold).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_BH2_44_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

FIXTURES = Path(__file__).parent / "fixtures"
# The ids the fleet launched on AGY (census of "LAUNCHED: bd-agy-* ... on <model>" lines, 2026-09-28: 41/4/2/1).
IN_USE = (
    "gemini-3.8-flash-high",
    "claude-sonnet-4-6",
    "gpt-oss-120b-medium",
    "gemini-3.1-pro-high",
)
STOPPED_AT_FIRST_WRITE = "BH2-44-STUB: real role-systemprompts write blocked"

STUB_AGY = """#!/bin/bash
echo "agy $*" >> "$BH2_44_LOG"
[ "${1:-}" = models ] || exit 0
case "$BH2_44_AGY_MODE" in
  fail) echo "Error: Please sign in to view available models." >&2; exit 1 ;;
  empty) echo "Fetching available models..." >&2; exit 0 ;;
esac
cat "$BH2_44_FIXTURE.stderr" >&2
cat "$BH2_44_FIXTURE.stdout"
"""
STUB_TMUX = """#!/bin/bash
echo "tmux $*" >> "$BH2_44_LOG"
case "$1" in has-session) exit 1 ;; esac
exit 0
"""
STUB_MKDIR = """#!/bin/bash
echo "mkdir $*" >> "$BH2_44_LOG"
case "$*" in *role-systemprompts*) echo "BH2-44-STUB: real role-systemprompts write blocked" >&2; exit 97 ;; esac
exec /bin/mkdir "$@"
"""


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    cand = Path(CANDIDATE)
    assert cand.is_file() and os.access(cand, os.X_OK), (
        f"BD_BH2_44_CANDIDATE not an executable file: {cand}"
    )
    text = cand.read_text()
    assert (
        "--dry-run)" in text
        and "mkdir -p /home/mboyle/bd-persist/role-systemprompts" in text
    ), (
        "SUT lacks the --dry-run arm or the role-systemprompts mkdir the stub relies on: refusing to run it"
    )
    fixture = FIXTURES / "agy_models_20260928"
    assert fixture.with_suffix(".stdout").read_text().count("\t") >= 4, (
        "fixture capture is empty"
    )
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for name, body in (("agy", STUB_AGY), ("tmux", STUB_TMUX), ("mkdir", STUB_MKDIR)):
        (stubs / name).write_text(body)
        (stubs / name).chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    return {
        **os.environ,
        "PATH": f"{stubs}:{os.environ['PATH']}",
        "HOME": str(home),
        "LC_ALL": "C",
        "BD_AGY_BIN": str(stubs / "agy"),
        "BD_AGY_WORKDIR": str(tmp_path / "seat"),
        "BH2_44_LOG": str(tmp_path / "calls.log"),
        "BH2_44_FIXTURE": str(fixture),
    }


def _run(
    env: dict[str, str], model: str, mode: str = "real", dry: bool = False
) -> tuple[int, str, list[str]]:
    argv = ["bash", CANDIDATE, "worker", "bh2-44-probe-seat", model]
    proc = subprocess.run(
        argv + (["--dry-run"] if dry else []),
        env={**env, "BH2_44_AGY_MODE": mode},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    log = Path(env["BH2_44_LOG"])
    calls = log.read_text().splitlines() if log.exists() else []
    return proc.returncode, proc.stdout + proc.stderr, calls


def _nothing_launched(env: dict[str, str], calls: list[str]) -> None:
    assert not [
        c for c in calls if c.startswith(("tmux new-session", "tmux send-keys"))
    ], calls
    assert not [c for c in calls if c.startswith("agy") and c != "agy models"], calls
    assert not (Path(env["HOME"]) / ".gemini").exists(), (
        "trustedFolders.json was written"
    )


def _refused_before_any_write(env: dict[str, str], calls: list[str]) -> None:
    _nothing_launched(env, calls)
    assert not [c for c in calls if c.startswith("mkdir")], calls
    assert not Path(env["BD_AGY_WORKDIR"]).exists(), "the seat workdir was created"


@pytest.mark.parametrize("model", [*IN_USE, "flash", "pro"])
def test_ids_in_use_and_tiers_pass_the_gate(env: dict[str, str], model: str) -> None:
    # Positive control: the gate passes and the launch reaches its first real write (blocked by the stub).
    rc, out, calls = _run(env, model)
    assert rc == 97 and STOPPED_AT_FIRST_WRITE in out, f"rc={rc} out={out!r}"
    assert "agy models" in calls, (
        f"the launcher never asked agy for its model list: {calls}"
    )
    _nothing_launched(env, calls)


@pytest.mark.parametrize(
    "model",
    [
        "gemini-3.8-flash-hgih",
        "gemini-3.8-flash",  # a prefix of listed ids: exact match, not substring
        "claude-3-5-sonnet-20240620",
        "Gemini 3.8 Flash (High)",
    ],
)
def test_unlisted_id_is_refused_before_any_write(
    env: dict[str, str], model: str
) -> None:
    rc, out, calls = _run(env, model)
    assert rc == 2 and f"model {model} is not one agy lists" in out, (
        f"rc={rc} out={out!r}"
    )
    _refused_before_any_write(env, calls)


@pytest.mark.parametrize(
    ("mode", "needle"),
    [("fail", "could not list models"), ("empty", "listed no model ids")],
)
def test_unreadable_list_is_could_not_look_not_permission(
    env: dict[str, str], mode: str, needle: str
) -> None:
    rc, out, calls = _run(env, "gemini-3.8-flash-high", mode)
    assert rc == 8 and needle in out, f"rc={rc} out={out!r}"
    _refused_before_any_write(env, calls)


@pytest.mark.parametrize("model", ["gemini-3.8-flash-high", "gemini-3.8-flash-hgih"])
def test_dry_run_never_runs_agy(env: dict[str, str], model: str) -> None:
    # BH2-13 contract: DRY runs nothing outside the script. `agy models` writes ~/.gemini state, so DRY skips the gate.
    rc, out, calls = _run(env, model, mode="fail", dry=True)
    assert rc == 0 and "DRY bh2-44-probe-seat " in out, f"rc={rc} out={out!r}"
    assert not [c for c in calls if c.startswith("agy")], calls
    _refused_before_any_write(env, calls)
