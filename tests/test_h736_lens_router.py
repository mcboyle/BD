"""H736 (ORDERS-0129): lens router for HARNESS-FIX H<n>-DONE.md records.

Candidate: BD_H736_CANDIDATE=<abs path of bd-lens-router.sh>; unset, the deployed
bd-persist/harness/bd-lens-router.sh is judged when it exists, else the module skips. A supplied
but absent or non-executable candidate FAILS: pointing it at the deployed path before the router
is installed is the RED (base harness has no router). tmux and bd-say are replaced by recording
fakes; the FIX dir is a tmp fixture. Every scenario asserts the exact exit code and the distinctive diagnostic.
"""
from __future__ import annotations

import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
DEPLOYED = Path("/home/mboyle/bd-persist/harness/bd-lens-router.sh")
CANDIDATE = os.environ.get("BD_H736_CANDIDATE", "") or (str(DEPLOYED) if DEPLOYED.exists() else "")
pytestmark = pytest.mark.skipif(
    not CANDIDATE, reason=f"BD_H736_CANDIDATE unset and {DEPLOYED} not deployed")

CODEX_IDLE = "gpt-6-astra  ~/x\n\nAsk Codex to do anything\n"
CODEX_BUSY = "Working (12s . esc to interrupt)\n\nAsk Codex to do anything\n"
CLAUDE_IDLE = "some output\n❯ \n"
CLAUDE_BUSY = "Thinking\n❯ \n"


def _exe(path: Path, body: str) -> Path:
    path.write_text(textwrap.dedent(body))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class Fleet:
    """tmp HARNESS-FIX dir + fake tmux (sessions/panes from files) + fake bd-say (records calls)."""

    def __init__(self, tmp: Path):
        self.fix = tmp / "HARNESS-FIX"
        self.fix.mkdir()
        self.panes = tmp / "panes"
        self.panes.mkdir()
        self.say_log = tmp / "say.log"
        self.tmux = _exe(tmp / "tmux", f"""\
            #!/usr/bin/env bash
            P={self.panes}
            case "$1" in
              ls) ls "$P" ;;
              has-session) s=${{3#=}}; [ -f "$P/$s" ] ;;
              capture-pane) s=${{4#=}}; s=${{s%:}}; cat "$P/$s" ;;
              *) exit 1 ;;
            esac
            """)
        self.say = _exe(tmp / "say", f"""\
            #!/usr/bin/env bash
            printf '%s\\t%s\\n' "$1" "$2" >>{self.say_log}
            [ "${{SAY_FAIL:-0}}" = 1 ] && exit 4
            exit 0
            """)

    def seat(self, name: str, pane: str) -> None:
        (self.panes / name).write_text(pane)

    def done(self, hid: str, builder: str, verdict: str = "VERDICT: PATCH", age: int = 0) -> Path:
        p = self.fix / f"{hid}-DONE.md"
        p.write_text(f"{verdict}\nITEM: {hid} some harness fix -- ORDERS-0124, {builder}\nFILE: x\n")
        t = 1_700_000_000 + age
        os.utime(p, (t, t))
        return p

    def queue(self, seat: str) -> list[str]:
        q = self.fix / f"REVIEW-QUEUE-{seat}.txt"
        return q.read_text().split() if q.exists() else []

    def says(self) -> list[tuple[str, str]]:
        if not self.say_log.exists():
            return []
        return [tuple(l.split("\t", 1)) for l in self.say_log.read_text().splitlines()]

    def run(self, **env: str) -> subprocess.CompletedProcess:
        e = dict(os.environ, BD_LR_FIX=str(self.fix), BD_LR_TMUX=str(self.tmux), BD_LR_SAY=str(self.say))
        e.update(env)
        return subprocess.run([CANDIDATE], env=e, capture_output=True, text=True, timeout=30)


@pytest.fixture
def fleet(tmp_path: Path) -> Fleet:
    return Fleet(tmp_path)


def test_candidate_present_and_executable():
    p = Path(CANDIDATE)
    assert p.is_file() and os.access(p, os.X_OK), f"candidate missing/non-executable: {p}"
    assert "H736" in p.read_text()


def test_routes_codex_first_and_writes_queue_line_and_says(fleet: Fleet):
    fleet.done("H900", "bd-fixer-D-B")
    fleet.seat("bd-review-correctness-N3-B", CLAUDE_IDLE)
    fleet.seat("bd-fixer-cx1", CODEX_IDLE)
    r = fleet.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ROUTED H900 lens=bd-fixer-cx1 NEW done-sha=" in r.stdout
    assert fleet.queue("bd-fixer-cx1") == ["H900"]
    assert fleet.queue("bd-review-correctness-N3-B") == []
    (seat, msg), = fleet.says()
    assert seat == "bd-fixer-cx1"
    assert f"{fleet.fix}/H900-DONE.md" in msg and "H900-REVIEW-bd-fixer-cx1.md" in msg
    assert len(msg) < 1024  # H113: codex courier cap


def test_never_the_builder(fleet: Fleet):
    fleet.done("H901", "bd-fixer-cx1")
    fleet.seat("bd-fixer-cx1", CODEX_IDLE)
    fleet.seat("bd-review-correctness-N3-B", CLAUDE_IDLE)
    r = fleet.run()
    assert r.returncode == 0, r.stdout
    assert "SKIP H901 lens=bd-fixer-cx1 builder" in r.stdout
    assert "ROUTED H901 lens=bd-review-correctness-N3-B" in r.stdout
    assert fleet.queue("bd-fixer-cx1") == []


def test_negative_only_builder_free_exit_3_nothing_written(fleet: Fleet):
    fleet.done("H902", "bd-fixer-cx1")
    fleet.seat("bd-fixer-cx1", CODEX_IDLE)
    fleet.seat("bd-review-correctness-N3-B", CLAUDE_BUSY)
    r = fleet.run()
    assert r.returncode == 3, r.stdout
    assert "UNROUTED H902 NEW builders=bd-fixer-cx1" in r.stdout
    assert "NO-FREE-LENS candidates=1 free=1" in r.stdout
    assert not list(fleet.fix.glob("REVIEW-QUEUE-*.txt"))
    assert fleet.says() == []


def test_busy_panes_are_not_free(fleet: Fleet):
    fleet.done("H903", "bd-fixer-D-B")
    fleet.seat("bd-fixer-cx1", CODEX_BUSY)
    fleet.seat("bd-review-correctness-N3-B", CLAUDE_BUSY)
    r = fleet.run()
    assert r.returncode == 3
    assert "NO-FREE-LENS candidates=1 free=0" in r.stdout


def test_reviewed_locked_queued_or_non_patch_are_not_candidates(fleet: Fleet):
    fleet.done("H904", "bd-fixer-D-B")
    (fleet.fix / "H904-REVIEW-bd-worker-cx3.md").write_text("VERDICT: BOARD\n")
    fleet.done("H905", "bd-fixer-D-B")
    (fleet.fix / "H905.review-lock").mkdir()
    fleet.done("H913", "bd-fixer-D-B")  # P4-A REFUTE: verdict in the H<n>/.review/ layout counts as reviewed
    (fleet.fix / "H913" / ".review").mkdir(parents=True)
    (fleet.fix / "H913" / ".review" / "VERDICT-correctness-bd-review-correctness-P4-A.md").write_text("VERDICT: REFUTE\n")
    fleet.done("H906", "bd-fixer-D-B")
    (fleet.fix / "REVIEW-QUEUE-bd-fixer-cx2.txt").write_text("H906\n")
    fleet.seat("bd-fixer-cx2", CODEX_BUSY)  # alive, pending: not re-routed
    fleet.done("H907", "bd-fixer-D-B", verdict="VERDICT: CLOSED-DONE")
    fleet.seat("bd-fixer-cx1", CODEX_IDLE)
    r = fleet.run()
    assert r.returncode == 0, r.stdout
    assert "NONE denominator=5" in r.stdout
    assert fleet.queue("bd-fixer-cx1") == []


def test_requeue_when_queued_seat_is_dead(fleet: Fleet):
    fleet.done("H908", "bd-fixer-D-B")
    (fleet.fix / "REVIEW-QUEUE-bd-review-correctness-P2-B.txt").write_text("H908\n")  # no such session
    fleet.seat("bd-fixer-cx1", CODEX_IDLE)
    r = fleet.run()
    assert r.returncode == 0, r.stdout
    assert "ROUTED H908 lens=bd-fixer-cx1 REQUEUE dead-seat=bd-review-correctness-P2-B" in r.stdout
    assert fleet.queue("bd-fixer-cx1") == ["H908"]
    assert fleet.queue("bd-review-correctness-P2-B") == ["H908"]  # old queue file never edited


def test_lens_with_pending_queue_is_not_free_and_oldest_done_first(fleet: Fleet):
    fleet.done("H910", "bd-fixer-D-B", age=100)
    fleet.done("H909", "bd-fixer-D-B", age=0)
    fleet.seat("bd-fixer-cx1", CODEX_IDLE)
    (fleet.fix / "REVIEW-QUEUE-bd-fixer-cx1.txt").write_text("H800\n")
    fleet.done("H800", "bd-fixer-E-A")  # pending for cx1: unreviewed, unlocked
    fleet.seat("bd-worker-cx3", CODEX_IDLE)
    r = fleet.run(BD_LR_PER_TICK="1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SKIP lens=bd-fixer-cx1 pending=1" in r.stderr
    assert "ROUTED H909 lens=bd-worker-cx3" in r.stdout
    assert "ROUTED H910" not in r.stdout  # per-tick cap honoured
    assert fleet.queue("bd-fixer-cx1") == ["H800"]


def test_dry_run_writes_nothing(fleet: Fleet):
    fleet.done("H911", "bd-fixer-D-B")
    fleet.seat("bd-fixer-cx1", CODEX_IDLE)
    r = fleet.run(DRY_RUN="1")
    assert r.returncode == 0
    assert "ROUTE(dry) H911 lens=bd-fixer-cx1" in r.stdout
    assert not list(fleet.fix.glob("REVIEW-QUEUE-*.txt"))
    assert fleet.says() == []


def test_say_failure_keeps_queue_line(fleet: Fleet):
    fleet.done("H912", "bd-fixer-D-B")
    fleet.seat("bd-fixer-cx1", CODEX_IDLE)
    r = fleet.run(SAY_FAIL="1")
    assert r.returncode == 0, r.stdout
    assert "SAY-FAILED H912 lens=bd-fixer-cx1 rc=4" in r.stdout
    assert fleet.queue("bd-fixer-cx1") == ["H912"]


def test_missing_fix_dir_exit_2(fleet: Fleet):
    r = fleet.run(BD_LR_FIX=str(fleet.fix / "nope"))
    assert r.returncode == 2
    assert "CONFIG fix-dir-missing" in r.stdout
