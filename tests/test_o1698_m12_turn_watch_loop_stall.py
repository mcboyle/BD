"""O1698 M12 (harness-work/O1698/briefs/BRIEF-o1698-m12-turn-watch-loop-stall.md): turn-watch LOOP / RETRY / STALL flags.

bd-turn-watch.sh (cron /5) only acted on the 150/200 turn caps. The candidate adds, per live seat, turns/<seat>.flag:
LOOP (>=5 byte-identical tool_use among the last 20), RETRY (>=4 consecutive is_error on one tool) and STALL (0 turns
in 30 min while holding work, pane not busy), one advisory per newly raised flag, cleared with the condition. The
advisory is a WOULD-SAY log line by default and a say only with BD_TURN_M12_SAY=1; the fixture sets the gate per run.
The transcript is the seat's OWN session from the Claude session registry: 20+ seats share one cwd, so "newest file
in the project dir" names another seat's transcript.

m12 is installed, so the default candidate is the live harness/bd-turn-watch.sh, not a FIX generation. The test is opted
in with BD_TEST_O1698_M12_TURN_WATCH_LOOP_STALL=1 (path override: BD_O1698_M12_TURN_WATCH_LOOP_STALL_CANDIDATE).
Hermetic: bd-persist dir, session registry, transcripts and the say are tmp_path fixtures, and a `tmux` stub first on
PATH pins every tmux call (the script's and anything it runs) to a private -L socket, so no live seat is listed,
captured or sent to -- even when the candidate is the seam-less live base (RED run).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

DEFAULT_CANDIDATE = "/home/mboyle/bd-persist/harness/bd-turn-watch.sh"
CANDIDATE = os.environ.get("BD_O1698_M12_TURN_WATCH_LOOP_STALL_CANDIDATE", DEFAULT_CANDIDATE)
LIVE_REAPER = Path("/home/mboyle/bd-persist/harness/bd-idle-reaper.sh")
REAL_TMUX = shutil.which("tmux")

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("BD_TEST_O1698_M12_TURN_WATCH_LOOP_STALL") != "1", reason="candidate opt-in required"
    ),
    pytest.mark.skipif(REAL_TMUX is None, reason="tmux not installed"),
]

OLD_MIN = 45


def _utc(minutes_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tool_use(i: str, cmd: str, name: str = "Bash") -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": i, "name": name, "input": {"command": cmd}}]}}


def _tool_result(i: str, err: bool) -> dict:
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": i, "is_error": err}]}}


class Fleet:
    """A fixture bd-persist + Claude config + private tmux server."""

    def __init__(self, root: Path, sock: str) -> None:
        self.root = root
        self.sock = sock
        self.p = root / "bd-persist"
        self.cfg = root / "cfg"
        self.bin = root / "bin"
        self.wt = root / "wt"
        for d in (self.p / "turns", self.p / "queues", self.cfg / "sessions", self.cfg / "projects", self.bin, self.wt):
            d.mkdir(parents=True, exist_ok=True)
        (self.p / "DISPATCH-LEDGER.tsv").write_text("")
        (self.p / "PM-SEAT").write_text("bd-pm-fixture\n")
        tmux = self.bin / "tmux"
        tmux.write_text(f'#!/bin/bash\nexec {REAL_TMUX} -L {sock} "$@"\n')
        tmux.chmod(0o755)
        say = self.bin / "say"
        say.write_text(f'#!/bin/bash\necho "$1|$2" >> {root}/said\n')
        say.chmod(0o755)
        if LIVE_REAPER.is_file():
            self.reaper = LIVE_REAPER  # positive control: the regex is read out of the real reaper
        else:
            self.reaper = root / "reaper"
            self.reaper.write_text(
                "  busy=0; printf '%s' \"$pane\" | grep -qE 'esc to interrupt|Working \\(|Thinking' && busy=1\n"
            )

    def tmux(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([REAL_TMUX, "-L", self.sock, *args], capture_output=True, text=True, check=False)

    def seat(self, name: str, *, pane: str = "> ", count_min_ago: int | None = OLD_MIN, sid: str | None = None,
             cwd: str = "/var/tmp/bd-seats/worker", events: list[dict] | None = None, updated: int = 1) -> None:
        self.tmux("new-session", "-d", "-s", name, f"printf '{pane}\\n'; sleep 600")
        deadline = time.time() + 3
        while time.time() < deadline and not self.tmux("capture-pane", "-pt", name).stdout.strip():
            time.sleep(0.05)
        sid = sid or f"sid-{name}"
        reg = {"pid": os.getpid(), "sessionId": sid, "cwd": cwd, "tmux": f"{name}:@1.%1", "updatedAt": updated}
        (self.cfg / "sessions" / f"{name}.json").write_text(json.dumps(reg))
        if events is not None:
            self.transcript(sid, events, cwd)
        if count_min_ago is not None:
            (self.p / "turns" / f"{name}.count").write_text(_utc(count_min_ago) + "\n")

    def transcript(self, sid: str, events: list[dict], cwd: str = "/var/tmp/bd-seats/worker") -> Path:
        d = self.cfg / "projects" / cwd.replace("/", "-")
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{sid}.jsonl"
        f.write_text("".join(json.dumps(e) + "\n" for e in events))
        return f

    def claim(self, seat: str, row: str, minutes_ago: int = OLD_MIN, marker: str | None = None) -> None:
        d = self.p / "queues" / "X-CLAIMS" / row
        d.mkdir(parents=True)
        (d / "SEAT").write_text(seat + "\n")
        t = time.time() - minutes_ago * 60
        os.utime(d / "SEAT", (t, t))
        if marker:
            (d / marker).write_text("done\n")

    def run(self, *args: str, say: bool = True) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.pop("BD_TURN_M12_SAY", None)
        if say:
            env["BD_TURN_M12_SAY"] = "1"
        env.update(
            PATH=f"{self.bin}:{env.get('PATH', '/usr/bin:/bin')}",
            HOME=str(self.root),
            BD_TURN_P=str(self.p),
            BD_TURN_SAY=str(self.bin / "say"),
            BD_TURN_SESSIONS=str(self.cfg / "sessions" / "*.json"),
            BD_TURN_REAPER=str(self.reaper),
            BD_TURN_WT_ROOT=str(self.wt),
        )
        env.pop("BD_TURN_ONLY", None)
        env.pop("TMUX", None)
        return subprocess.run(["bash", CANDIDATE, *args], cwd=self.root, env=env, capture_output=True, text=True,
                              timeout=120, check=False)

    def flag(self, seat: str) -> str:
        f = self.p / "turns" / f"{seat}.flag"
        return f.read_text() if f.exists() else ""

    def said(self) -> list[str]:
        f = self.root / "said"
        return f.read_text().splitlines() if f.exists() else []

    def log(self) -> str:
        f = self.p / "turns" / "turn-watch.log"
        return f.read_text() if f.exists() else ""


@pytest.fixture
def fleet(tmp_path: Path):
    f = Fleet(tmp_path, f"bdtw-m12-{os.getpid()}-{time.monotonic_ns()}")
    try:
        yield f
    finally:
        f.tmux("kill-server")


def _same(n: int) -> list[dict]:
    out: list[dict] = []
    for i in range(n):
        out += [_tool_use(f"S{i}", "ls /x"), _tool_result(f"S{i}", False)]
    return out


def _distinct(n: int) -> list[dict]:
    out: list[dict] = []
    for i in range(n):
        out += [_tool_use(f"D{i}", f"ls /x{i}"), _tool_result(f"D{i}", False)]
    return out


def _errors(n: int) -> list[dict]:
    out: list[dict] = []
    for i in range(n):
        out += [_tool_use(f"E{i}", f"make t{i}"), _tool_result(f"E{i}", True)]
    return out


def test_private_server_is_the_only_one_seen(fleet: Fleet) -> None:
    fleet.seat("bd-m12-probe", events=_distinct(1))
    r = fleet.run()
    assert r.returncode == 0, r.stderr
    seats = [ln.split()[0] for ln in r.stdout.splitlines() if ln.strip()]
    assert seats == ["bd-m12-probe"], r.stdout


def test_six_identical_tool_use_raise_loop(fleet: Fleet) -> None:
    fleet.seat("bd-m12-loop", events=_same(6))
    fleet.run()
    flag = fleet.flag("bd-m12-loop")
    assert flag.startswith("LOOP "), f"no LOOP flag: {flag!r}\n{fleet.log()}"
    assert "Bash x6 of last 6 tool_use" in flag
    assert fleet.said() == [f"bd-m12-loop|ADVISORY M12 LOOP: {fleet.p}/turns/bd-m12-loop.flag"]


def test_five_identical_tool_use_is_the_loop_threshold(fleet: Fleet) -> None:
    fleet.seat("bd-m12-five", events=_same(5) + _distinct(3))
    fleet.run()
    flag = fleet.flag("bd-m12-five")
    assert flag.startswith("LOOP "), f"5 identical (spec >=5) raised no LOOP: {flag!r}\n{fleet.log()}"
    assert "Bash x5 of last 8 tool_use" in flag


def test_six_distinct_tool_use_raise_nothing(fleet: Fleet) -> None:
    fleet.seat("bd-m12-distinct", events=_distinct(6))
    fleet.run()
    assert fleet.flag("bd-m12-distinct") == ""
    assert fleet.said() == []


def test_loop_counts_only_the_last_twenty_tool_use(fleet: Fleet) -> None:
    fleet.seat("bd-m12-old-loop", events=_same(6) + _distinct(20))
    fleet.run()
    assert fleet.flag("bd-m12-old-loop") == ""


def test_four_consecutive_errors_raise_retry_three_do_not(fleet: Fleet) -> None:
    fleet.seat("bd-m12-retry", events=_distinct(2) + _errors(4))
    fleet.seat("bd-m12-three", events=_distinct(2) + _errors(3))
    fleet.run()
    assert fleet.flag("bd-m12-retry").startswith("RETRY "), fleet.log()
    assert "Bash x4 consecutive is_error" in fleet.flag("bd-m12-retry")
    assert fleet.flag("bd-m12-three") == ""


def test_errors_spread_over_tools_are_not_a_retry(fleet: Fleet) -> None:
    events: list[dict] = []
    for i, name in enumerate(("Bash", "Read", "Bash", "Read", "Bash")):
        events += [_tool_use(f"M{i}", f"x{i}", name), _tool_result(f"M{i}", True)]
    fleet.seat("bd-m12-mixed", events=events)
    fleet.run()
    assert fleet.flag("bd-m12-mixed") == ""


def test_stall_needs_old_count_held_claim_and_idle_pane(fleet: Fleet) -> None:
    fleet.seat("bd-m12-stall", events=_distinct(1))
    fleet.claim("bd-m12-stall", "row-stall")
    fleet.seat("bd-m12-working", pane="Working (3m 10s - esc to interrupt)", events=_distinct(1))
    fleet.claim("bd-m12-working", "row-working")
    fleet.seat("bd-m12-noclaim", events=_distinct(1))
    fleet.seat("bd-m12-fresh", count_min_ago=5, events=_distinct(1))
    fleet.claim("bd-m12-fresh", "row-fresh")
    fleet.seat("bd-m12-done", events=_distinct(1))
    fleet.claim("bd-m12-done", "row-done", marker="RESULT")
    fleet.run()
    flag = fleet.flag("bd-m12-stall")
    assert flag.startswith("STALL "), f"no STALL: {flag!r}\n{fleet.log()}"
    assert "holding: claim:X-CLAIMS/row-stall" in flag
    for quiet in ("bd-m12-working", "bd-m12-noclaim", "bd-m12-fresh", "bd-m12-done"):
        assert fleet.flag(quiet) == "", (quiet, fleet.flag(quiet))


def test_claim_younger_than_stall_window_is_not_a_stall(fleet: Fleet) -> None:
    fleet.seat("bd-m12-young", events=_distinct(1))
    fleet.claim("bd-m12-young", "row-young", minutes_ago=10)
    fleet.run()
    assert fleet.flag("bd-m12-young") == "", f"10-min claim (spec: held >=30m) raised: {fleet.flag('bd-m12-young')!r}"


def _age(path: Path, minutes_ago: int) -> None:
    t = time.time() - minutes_ago * 60
    os.utime(path, (t, t))


def test_finished_row_releases_the_claim(fleet: Fleet) -> None:
    # 3/3 shadow STALLs on the live fleet were idle seats whose rows were DONE: claim dirs are never released
    fleet.seat("bd-m12-wtdone", events=_distinct(1))
    fleet.claim("bd-m12-wtdone", "row-wt")
    (fleet.wt / "row-wt-bd-m12-wtdone").mkdir()
    (fleet.wt / "row-wt-bd-m12-wtdone" / "DONE.md").write_text("VERDICT: PATCH\n")
    fleet.seat("bd-m12-fixdone", events=_distinct(1))
    fleet.claim("bd-m12-fixdone", "row-fix")
    (fleet.p / "harness-work" / "FIX" / "row-fix").mkdir(parents=True)
    (fleet.p / "harness-work" / "FIX" / "row-fix" / "DONE.md").write_text("VERDICT: PATCH\n")
    fleet.seat("bd-m12-control2", events=_distinct(1))
    fleet.claim("bd-m12-control2", "row-open")
    (fleet.wt / "row-open-bd-m12-other").mkdir()   # another seat's DONE.md for the same row does not release it
    (fleet.wt / "row-open-bd-m12-other" / "DONE.md").write_text("VERDICT: PATCH\n")
    fleet.run()
    assert fleet.flag("bd-m12-wtdone") == ""
    assert fleet.flag("bd-m12-fixdone") == ""
    assert fleet.flag("bd-m12-control2").startswith("STALL "), fleet.log()


def test_only_the_newest_claim_is_current(fleet: Fleet) -> None:
    fleet.seat("bd-m12-moved", events=_distinct(1))
    fleet.claim("bd-m12-moved", "row-abandoned", minutes_ago=300)   # no marker, but the seat moved on
    fleet.claim("bd-m12-moved", "row-current", minutes_ago=60, marker="RESULT")
    fleet.seat("bd-m12-stuck", events=_distinct(1))
    fleet.claim("bd-m12-stuck", "row-old", minutes_ago=300, marker="RESULT")
    fleet.claim("bd-m12-stuck", "row-new", minutes_ago=60)
    fleet.run()
    assert fleet.flag("bd-m12-moved") == ""
    assert "holding: claim:X-CLAIMS/row-new " in fleet.flag("bd-m12-stuck"), fleet.log()
    assert "row-old" not in fleet.flag("bd-m12-stuck")


def test_lens_verdict_after_the_claim_releases_it(fleet: Fleet) -> None:
    for seat, verdict_min_ago in (("bd-m12-lens-done", 10), ("bd-m12-lens-open", 90)):
        fleet.seat(seat, events=_distinct(1))
        fleet.claim(seat, f"row-{seat}", minutes_ago=OLD_MIN)
        review = fleet.wt / f"cut-of-{seat}" / ".review"
        review.mkdir(parents=True)
        v = review / f"VERDICT-correctness-{seat}.md"
        v.write_text("VERDICT: BOARD\n")
        _age(v, verdict_min_ago)
    fleet.run()
    assert fleet.flag("bd-m12-lens-done") == ""
    assert fleet.flag("bd-m12-lens-open").startswith("STALL "), fleet.log()   # verdict predates the claim


def test_dispatched_ledger_row_counts_as_holding(fleet: Fleet) -> None:
    fleet.seat("bd-m12-ledger", events=_distinct(1))
    (fleet.p / "DISPATCH-LEDGER.tsv").write_text(f"{_utc(OLD_MIN)}\tbd-m12-ledger\trow-l\tdispatched\t/brief.md\n")
    fleet.run()
    assert "holding: dispatched:row-l" in fleet.flag("bd-m12-ledger"), fleet.log()


def test_own_session_from_registry_not_newest_in_shared_cwd(fleet: Fleet) -> None:
    fleet.seat("bd-m12-a", sid="sid-a", events=_same(6))
    fleet.seat("bd-m12-b", sid="sid-b", events=_distinct(6))
    t = time.time() + 60   # seat b's transcript is the newest file in the shared project dir
    os.utime(fleet.cfg / "projects" / "-var-tmp-bd-seats-worker" / "sid-b.jsonl", (t, t))
    fleet.run()
    assert fleet.flag("bd-m12-a").startswith("LOOP "), fleet.log()
    assert fleet.flag("bd-m12-b") == ""


def test_two_runs_say_once_and_flag_clears_with_condition(fleet: Fleet) -> None:
    fleet.seat("bd-m12-clear", events=_same(6))
    fleet.run()
    raised = fleet.flag("bd-m12-clear").split()[1]
    fleet.run()
    assert fleet.flag("bd-m12-clear").split()[1] == raised   # raised-ts kept while the flag stands
    assert len(fleet.said()) == 1
    assert "M12 WOULD-SAY" not in fleet.log()
    fleet.transcript("sid-bd-m12-clear", _same(6) + _distinct(20))
    fleet.run()
    assert fleet.flag("bd-m12-clear") == ""
    assert "M12 CLEAR bd-m12-clear" in fleet.log()
    assert len(fleet.said()) == 1


def test_default_gate_logs_would_say_once_and_sends_nothing(fleet: Fleet) -> None:
    fleet.seat("bd-m12-dry", events=_same(6))
    fleet.run(say=False)
    fleet.run(say=False)
    assert fleet.flag("bd-m12-dry").startswith("LOOP ")
    assert fleet.said() == [], "BD_TURN_M12_SAY unset must not send"
    assert fleet.log().count(f"M12 WOULD-SAY bd-m12-dry LOOP {fleet.p}/turns/bd-m12-dry.flag") == 1, fleet.log()


def test_could_not_look_keeps_flag_and_raises_nothing(fleet: Fleet) -> None:
    fleet.seat("bd-m12-unk", events=_same(6))
    fleet.seat("bd-m12-noreg", events=_same(6))
    (fleet.cfg / "sessions" / "bd-m12-noreg.json").unlink()
    fleet.run()
    assert fleet.flag("bd-m12-noreg") == ""
    assert "M12 COULD-NOT-LOOK bd-m12-noreg loop/retry: no live session-registry entry" in fleet.log()
    before = fleet.flag("bd-m12-unk")
    (fleet.cfg / "sessions" / "bd-m12-unk.json").unlink()
    fleet.run()
    assert fleet.flag("bd-m12-unk") == before
    assert len(fleet.said()) == 1


def test_codex_seat_gets_stall_only(fleet: Fleet) -> None:
    fleet.seat("bd-cx-m12", events=_same(6), count_min_ago=None)
    fleet.run()
    assert fleet.flag("bd-cx-m12") == ""
    assert "COULD-NOT-LOOK bd-cx-m12 loop" not in fleet.log()


def test_codex_seat_without_turns_holding_work_is_could_not_look_not_stall(fleet: Fleet) -> None:
    # FR-6 / Rule 6: 0 rollouts found is COULD-NOT-LOOK, never "0 turns" -> STALL
    fleet.seat("bd-cx-m12-held", events=_distinct(1), count_min_ago=None)
    fleet.claim("bd-cx-m12-held", "row-cx-held")
    fleet.run()
    assert fleet.flag("bd-cx-m12-held") == "", f"0-rollout seat raised: {fleet.flag('bd-cx-m12-held')!r}"
    # live 0c00f6d8 logs lifetime_turns=0; O1799 (installs first, O1804) logs lifetime_turns=COULD-NOT-COUNT
    log = fleet.log()
    assert any(f"M12 COULD-NOT-LOOK bd-cx-m12-held stall: lifetime_turns={v} " in log for v in ("0", "COULD-NOT-COUNT")), log
    assert fleet.said() == []


def test_stall_kept_when_turn_count_becomes_unreadable(fleet: Fleet) -> None:
    fleet.seat("bd-m12-st-unk", events=_distinct(1))
    fleet.claim("bd-m12-st-unk", "row-st-unk")
    fleet.run()
    before = fleet.flag("bd-m12-st-unk")
    assert before.startswith("STALL "), f"no STALL: {before!r}\n{fleet.log()}"
    (fleet.p / "turns" / "bd-m12-st-unk.count").unlink()
    fleet.run()
    assert fleet.flag("bd-m12-st-unk") == before, f"STALL not kept on COULD-NOT-LOOK:\n{fleet.log()}"
    assert "M12 COULD-NOT-LOOK bd-m12-st-unk stall: lifetime_turns=0" in fleet.log()
    assert len(fleet.said()) == 1


def test_turn_cap_still_warns(fleet: Fleet) -> None:
    fleet.seat("bd-m12-cap", events=_distinct(1))
    # stamped after the fixture session was created: the cap counts turns since the seat's epoch (H629)
    (fleet.p / "turns" / "bd-m12-cap.count").write_text("".join(_utc(-1) + "\n" for _ in range(160)))
    fleet.run()
    assert (fleet.p / "turns" / "bd-m12-cap.state").read_text().strip() == "WARN"
    assert (fleet.p / "turns" / "bd-m12-cap.NO-NEW-TASKS").exists()


@pytest.mark.skipif(not LIVE_REAPER.is_file(), reason="live bd-idle-reaper.sh absent")
def test_busy_regex_read_from_live_reaper(fleet: Fleet) -> None:
    # "Running..." is only in the live reaper's regex (not the fixture fallback): busy there means it was read from it
    fleet.seat("bd-m12-running", pane="Running...", events=_distinct(1))
    fleet.claim("bd-m12-running", "row-running")
    fleet.seat("bd-m12-control", events=_distinct(1))
    fleet.claim("bd-m12-control", "row-control")
    fleet.run()
    assert fleet.flag("bd-m12-control").startswith("STALL "), fleet.log()
    assert fleet.flag("bd-m12-running") == ""
    assert "no pane-busy regex" not in fleet.log()


def test_selftest_passes(fleet: Fleet) -> None:
    r = fleet.run("--selftest")
    assert r.returncode == 0 and "SELFTEST PASS" in r.stdout, r.stdout + r.stderr
