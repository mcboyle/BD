"""Tests for bh2-27: bd-verdict-route worker rotation and failed-send skip.

Verifies that find_new_worker rotates reassignments across active worker seats
and skips seats that failed delivery in the current pass (BH2-vroute-008).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_BH2_27_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


def _setup_fixture(
    tmp_path: Path,
    sessions: list[str],
    failing_seats: list[str] | None = None,
    rows: list[str] | None = None,
) -> tuple[Path, Path, Path, Path, Path]:
    persist = tmp_path / "persist"
    rw = tmp_path / "rw"
    bin_dir = tmp_path / "bin"
    logs = persist / "logs"
    state = persist / "state"

    logs.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    rw.mkdir(parents=True, exist_ok=True)

    # Retired author configuration: retired-author-1 is retired
    retired_tsv = state / "retired-seats.tsv"
    retired_tsv.write_text("2026-09-01T00:00:00Z\tretired-author-1\toperator\n")

    # Tmux mock setup
    sessions_file = tmp_path / "sessions.txt"
    sessions_file.write_text("\n".join(sessions) + "\n")

    tmux_sh = bin_dir / "tmux"
    tmux_sh.write_text(
        f"""#!/bin/sh
if [ "$1" = "list-sessions" ]; then
    cat "{sessions_file}"
    exit 0
elif [ "$1" = "has-session" ]; then
    shift
    target=""
    while [ $# -gt 0 ]; do
        case "$1" in
            -t) shift; target="${{1#=}}" ;;
            *) shift ;;
        esac
    done
    grep -qxF "$target" "{sessions_file}" && exit 0 || exit 1
fi
exit 0
"""
    )
    tmux_sh.chmod(0o755)

    # Say mock setup
    say_log = tmp_path / "say.log"
    say_log.touch()

    fail_file = tmp_path / "fails.txt"
    if failing_seats:
        fail_file.write_text("\n".join(failing_seats) + "\n")
    else:
        fail_file.touch()

    say_sh = bin_dir / "bd-say"
    say_sh.write_text(
        f"""#!/bin/sh
target="$1"
text="$2"
echo "$target: $text" >> "{say_log}"
if grep -qxF "$target" "{fail_file}"; then
    exit 5
fi
exit 0
"""
    )
    say_sh.chmod(0o755)

    route_log = logs / "route.log"
    route_log.touch()

    # Create cuts with retired author
    if rows:
        for r in rows:
            rdir = rw / f"row{r}"
            rev = rdir / ".review"
            rev.mkdir(parents=True, exist_ok=True)
            (rdir / "DONE.md").write_text("seat: retired-author-1\n")
            (rev / "VERDICT-refute.md").write_text("VERDICT: REFUTE\nobj: test-obj\n")

    return persist, rw, bin_dir, say_log, route_log


def _run_vroute(
    candidate: str,
    persist: Path,
    rw: Path,
    bin_dir: Path,
    route_log: Path,
    cursor_file: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = dict(
        os.environ,
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        BD_PERSIST_ROOT=str(persist),
        BD_REVIEW_WT=str(rw),
        BD_VERDICT_ROUTE_LOG=str(route_log),
        BD_VERDICT_ROUTE_SAY=str(bin_dir / "bd-say"),
        BD_LANDED_GATE="/nonexistent",
        BD_VERDICT_FIXER="OWNER",
    )
    if cursor_file:
        env["BD_VERDICT_CURSOR"] = str(cursor_file)
    if extra_env:
        env.update(extra_env)

    return subprocess.run(
        ["bash", candidate],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_vroute_skips_failed_worker_seat_in_same_pass(tmp_path: Path) -> None:
    """A worker seat that fails send must be skipped on subsequent reassignments in the same pass."""
    persist, rw, bin_dir, say_log, route_log = _setup_fixture(
        tmp_path,
        sessions=["bd-agy-worker-1", "bd-agy-worker-2"],
        failing_seats=["bd-agy-worker-1"],
        rows=["101", "102"],
    )

    res = _run_vroute(CANDIDATE, persist, rw, bin_dir, route_log)
    assert res.returncode == 0, f"stdout: {res.stdout}, stderr: {res.stderr}"

    say_lines = [
        line.strip() for line in say_log.read_text().splitlines() if line.strip()
    ]
    assert len(say_lines) == 2, f"unexpected say calls: {say_lines}"

    # Row 101 attempts worker-1 (which fails)
    assert say_lines[0].startswith("bd-agy-worker-1: ")
    # Row 102 MUST skip worker-1 and route to worker-2
    assert say_lines[1].startswith("bd-agy-worker-2: ")


def test_vroute_rotates_round_robin_across_active_workers(tmp_path: Path) -> None:
    """When multiple cuts are reassigned, targets must alternate round-robin across eligible workers."""
    persist, rw, bin_dir, say_log, route_log = _setup_fixture(
        tmp_path,
        sessions=["bd-agy-worker-1", "bd-agy-worker-2", "bd-worker-A1-A"],
        failing_seats=None,
        rows=["101", "102", "103"],
    )

    res = _run_vroute(CANDIDATE, persist, rw, bin_dir, route_log)
    assert res.returncode == 0, f"stdout: {res.stdout}, stderr: {res.stderr}"

    say_lines = [
        line.strip() for line in say_log.read_text().splitlines() if line.strip()
    ]
    assert len(say_lines) == 3, f"unexpected say calls: {say_lines}"

    targets = [line.split(":")[0].strip() for line in say_lines]
    # In candidate, each cut gets a distinct worker rotated round-robin
    assert targets == ["bd-agy-worker-1", "bd-agy-worker-2", "bd-worker-A1-A"]


def test_vroute_holds_none_when_all_workers_fail_delivery(tmp_path: Path) -> None:
    """When all eligible workers fail delivery in a pass, further cuts route to NONE and are held."""
    persist, rw, bin_dir, say_log, route_log = _setup_fixture(
        tmp_path,
        sessions=["bd-agy-worker-1", "bd-agy-worker-2"],
        failing_seats=["bd-agy-worker-1", "bd-agy-worker-2"],
        rows=["101", "102", "103"],
    )

    res = _run_vroute(CANDIDATE, persist, rw, bin_dir, route_log)
    assert res.returncode == 0, f"stdout: {res.stdout}, stderr: {res.stderr}"

    say_lines = [
        line.strip() for line in say_log.read_text().splitlines() if line.strip()
    ]
    # Only 2 delivery attempts: worker-1 and worker-2. Row 103 routes to NONE and is HELD without calling bd-say.
    assert len(say_lines) == 2, f"unexpected say calls: {say_lines}"
    targets = [line.split(":")[0].strip() for line in say_lines]
    assert targets == ["bd-agy-worker-1", "bd-agy-worker-2"]

    route_text = route_log.read_text()
    assert "SEND-HELD NONE" in route_text


def test_vroute_cursor_persistence_across_passes(tmp_path: Path) -> None:
    """Worker selection cursor persists across passes so subsequent passes continue rotation."""
    persist, rw, bin_dir, say_log, route_log = _setup_fixture(
        tmp_path,
        sessions=["bd-agy-worker-1", "bd-agy-worker-2"],
        failing_seats=None,
        rows=["101"],
    )
    cursor_file = persist / "state" / "verdict-worker.cursor"

    # Pass 1: routes row 101
    res1 = _run_vroute(
        CANDIDATE, persist, rw, bin_dir, route_log, cursor_file=cursor_file
    )
    assert res1.returncode == 0, res1.stderr
    assert cursor_file.is_file()
    assert cursor_file.read_text().strip() == "bd-agy-worker-1"

    # Set up row 102 for Pass 2
    rdir2 = rw / "row102"
    rev2 = rdir2 / ".review"
    rev2.mkdir(parents=True, exist_ok=True)
    (rdir2 / "DONE.md").write_text("seat: retired-author-1\n")
    (rev2 / "VERDICT-refute.md").write_text("VERDICT: REFUTE\nobj: test-obj-2\n")

    res2 = _run_vroute(
        CANDIDATE, persist, rw, bin_dir, route_log, cursor_file=cursor_file
    )
    assert res2.returncode == 0, res2.stderr
    assert cursor_file.read_text().strip() == "bd-agy-worker-2"

    say_lines = [
        line.strip() for line in say_log.read_text().splitlines() if line.strip()
    ]
    assert len(say_lines) == 2
    assert say_lines[0].startswith("bd-agy-worker-1: ")
    assert say_lines[1].startswith("bd-agy-worker-2: ")


def test_vroute_failed_seat_recovers_on_next_pass_even_with_failed_file(tmp_path: Path) -> None:
    """bh2-27 G2 (bd-cx-worker-2 REFUTE): a send failure excludes the seat for THIS pass only. G1 appended failures to
    BD_VERDICT_FAILED_FILE and excluded them on every later run with no expiry, so one transient failure held the
    repaired seat forever (SEND-HELD NONE). The env var is set here on purpose: it must not create a cross-run blacklist."""
    persist, rw, bin_dir, say_log, route_log = _setup_fixture(
        tmp_path,
        sessions=["bd-agy-worker-1"],
        failing_seats=["bd-agy-worker-1"],
        rows=["101"],
    )
    extra = {"BD_VERDICT_FAILED_FILE": str(tmp_path / "failed-workers.txt")}

    res1 = _run_vroute(CANDIDATE, persist, rw, bin_dir, route_log, extra_env=extra)
    assert res1.returncode == 0, res1.stderr
    assert "SEND-FAILED bd-agy-worker-1" in route_log.read_text()

    # Delivery repaired; a new cut arrives for the next pass.
    (tmp_path / "fails.txt").write_text("")
    rdir2 = rw / "row102"
    (rdir2 / ".review").mkdir(parents=True, exist_ok=True)
    (rdir2 / "DONE.md").write_text("seat: retired-author-1\n")
    (rdir2 / ".review" / "VERDICT-refute.md").write_text("VERDICT: REFUTE\nobj: test-obj-2\n")
    pass1_len = len(route_log.read_text())

    res2 = _run_vroute(CANDIDATE, persist, rw, bin_dir, route_log, extra_env=extra)
    assert res2.returncode == 0, res2.stderr
    pass2 = route_log.read_text()[pass1_len:]
    assert "SEND-HELD NONE" not in pass2, f"repaired seat still excluded on pass 2: {pass2}"

    say_lines = [line.strip() for line in say_log.read_text().splitlines() if line.strip()]
    assert len(say_lines) == 2, f"unexpected say calls: {say_lines}"
    assert say_lines[1].startswith("bd-agy-worker-1: ") and "row102" in say_lines[1], say_lines
