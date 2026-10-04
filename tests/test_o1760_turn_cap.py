"""O1760 turn cap: harness/bd-turn-cap.sh counts each LIVE seat's assistant turns from its own transcript -> OK|WRAP|RETIRE.

The harness is deployed from bd-persist, not this repo: BD_O1760_TURN_CAP_CANDIDATE is the absolute path of the candidate
SCRIPT (harness-work/FIX/o1760-turn-cap/bd-turn-cap.sh). Hermetic: tmux and ssh are fakes on PATH-free seams
(BD_TURN_CAP_TMUX / BD_REMOTE_SSH), /proc is a fixture tree (BD_TURN_CAP_PROC), HOME and every config dir live under
tmp_path. Thresholds: WRAP >= 80, RETIRE >= 120; grandfathered (baseline row AND pre-cut turns in the current session)
WRAP >= baseline+10, RETIRE >= baseline+20. Rule 6: an unreadable source is UNKNOWN, never a silent 0.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1760_TURN_CAP_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

WATCH = os.environ.get("BD_O1760_TURN_WATCH", "/home/mboyle/bd-persist/harness/bd-turn-watch.sh")
CUT = "2026-10-03T19:32Z"
PRE = "2026-10-03T18:00:00.000Z"  # before the cut
POST = "2026-10-03T20:00:00.000Z"  # after the cut

FAKE_TMUX = r"""#!/bin/bash
# sessions file: "<name> <pane_pid>" per line, under $FAKE_TMUX_DIR
f="$FAKE_TMUX_DIR/sessions"
case "$1" in
  ls) [ -s "$f" ] || exit 1; cut -d' ' -f1 "$f" ;;
  list-panes) t=${3#=}; t=${t%:}; awk -v t="$t" '$1==t{print $2; f=1} END{exit !f}' "$f" ;;
  has-session) t=${3#=}; grep -q "^$t " "$f" ;;
  *) exit 1 ;;
esac
"""

FAKE_SSH = r"""#!/bin/bash
while [ $# -gt 0 ]; do case "$1" in -n) shift ;; -o) shift 2 ;; -*) shift ;; *) break ;; esac; done
host=$1; shift
printf '%s\n' "$host" >> "$FAKE_SSH_LOG"
case " $FAKE_DOWN " in *" $host "*) exit 255 ;; esac
FAKE_TMUX_DIR="$FAKE_TMUX_DIR/vm-$host" exec bash -c "$*"
"""


class Fleet:
    """A fixture hub: fake tmux sessions, a fake /proc, pool config dirs, agy/kimi/codex logs, a bd-persist root."""

    def __init__(self, root: Path):
        self.root = root
        self.home = root / "home"
        self.proc = root / "proc"
        self.p = root / "bd-persist"
        self.tmuxdir = root / "tmux"
        self.cfg = self.home / ".claude-a"
        for d in (self.home, self.proc, self.p / "state", self.tmuxdir, self.cfg / "sessions", self.cfg / "projects" / "-w"):
            d.mkdir(parents=True, exist_ok=True)
        self.hosts = self.p / "state" / "SEAT-HOSTS.tsv"
        self.hosts.write_text("# seat\thost\tat\tby\n")
        self.base = self.p / "state" / "turn-cap-baseline.tsv"
        self.bin = root / "bin"
        self.bin.mkdir()
        for name, body in (("tmux", FAKE_TMUX), ("ssh", FAKE_SSH)):
            f = self.bin / name
            f.write_text(body)
            f.chmod(0o755)
        self.next_pid = 1000

    # ---- fixture /proc ----
    def _proc(self, ppid: int, argv: list[str], env: dict | None = None, cwd: str | None = None, tmux_dir: Path | None = None) -> int:
        self.next_pid += 1
        pid = self.next_pid
        d = self.proc / str(pid)
        d.mkdir()
        (d / "stat").write_text(f"{pid} ({os.path.basename(argv[0])[:15]}) S {ppid} 1 1 0 -1\n")
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
        (d / "environ").write_bytes(b"\0".join(f"{k}={v}".encode() for k, v in (env or {}).items()) + b"\0")
        if cwd:
            os.symlink(cwd, d / "cwd")
        return pid

    def _session(self, seat: str, agent_argv: list[str], host: str | None = None, **kw) -> int:
        pane = self._proc(1, ["-bash"])
        pid = self._proc(pane, agent_argv, **kw)
        self._proc(pid, ["python3", "-m", "some_mcp_server"])  # a child that must not be mistaken for the agent
        tdir = self.tmuxdir / f"vm-{host}" if host else self.tmuxdir
        tdir.mkdir(parents=True, exist_ok=True)
        with open(tdir / "sessions", "a") as f:
            f.write(f"{seat} {pane}\n")
        if host:
            with open(self.hosts, "a") as f:
                f.write(f"{seat}\t{host}\t2026-10-03T19:00:00Z\tbd-dispatch-T\n")
        return pid

    # ---- platforms ----
    def claude(self, seat: str, turns: int, pre: int = 0, host: str | None = None, transcript: str = "file") -> Path:
        """A Claude seat with `turns` model turns, the first `pre` of them before the cut."""
        pid = self._session(seat, ["claude", "--settings", '{"env":{"X":"codex kimi"}}'], host=host,
                            env={"CLAUDE_CONFIG_DIR": str(self.cfg), "HOME": str(self.home)}, cwd="/var/tmp/bd-seats/worker")
        sid = f"sid-{seat}"
        (self.cfg / "sessions" / f"{pid}.json").write_text(json.dumps({"pid": pid, "sessionId": sid, "tmux": f"{seat}:@1.%1"}))
        t = self.cfg / "projects" / "-w" / f"{sid}.jsonl"
        if transcript == "dir":  # exists but cannot be read as a file -- UNKNOWN, also for root
            t.mkdir()
            return t
        if transcript == "none":
            return t
        lines = []
        for i in range(turns):
            ts = PRE if i < pre else POST
            lines.append({"type": "user", "timestamp": ts, "message": {"role": "user", "content": f"prompt {i}"}})
            # a tool call inside the turn: not a turn
            lines.append({"type": "assistant", "timestamp": ts, "message": {"id": f"t{i}", "stop_reason": "tool_use",
                                                                            "content": [{"type": "tool_use"}]}})
            # the final message, written as two content-block lines with ONE id: one turn
            for blk in ("thinking", "text"):
                lines.append({"type": "assistant", "timestamp": ts, "message": {"id": f"e{i}", "stop_reason": "end_turn",
                                                                                "content": [{"type": blk}]}})
            # a subagent's sidechain end_turn: not the seat's turn
            lines.append({"type": "assistant", "isSidechain": True, "timestamp": ts,
                          "message": {"id": f"s{i}", "stop_reason": "end_turn", "content": []}})
        t.write_text("".join(json.dumps(x) + "\n" for x in lines))
        return t

    def agy(self, seat: str, turns: int, older: int = 0) -> None:
        ws = f"/var/tmp/bd-seats/{seat}"
        self._session(seat, ["/home/x/.local/bin/agy", "--project", "p"], cwd=ws)
        h = self.home / ".gemini" / "antigravity-cli" / "history.jsonl"
        h.parent.mkdir(parents=True, exist_ok=True)
        with open(h, "a") as f:
            for i in range(older):  # an earlier conversation in the same workspace: not the current session
                f.write(json.dumps({"display": "x", "timestamp": 1791050000000, "workspace": ws, "conversationId": "old"}) + "\n")
            f.write(json.dumps({"display": "y", "timestamp": 1791050000000, "workspace": "/elsewhere", "conversationId": "new"}) + "\n")
            for i in range(turns):
                f.write(json.dumps({"display": "z", "timestamp": 1791057000000, "workspace": ws, "conversationId": "new"}) + "\n")

    def kimi(self, seat: str, turns: int) -> None:
        wd = f"/var/tmp/bd-seats/{seat}"
        self._session(seat, ["kimi-code"], cwd=wd)
        k = self.home / ".kimi"
        sd = k / "sessions" / f"wd_{seat}" / "session_1"
        (sd / "agents" / "main").mkdir(parents=True)
        with open(k / "session_index.jsonl", "a") as f:
            f.write(json.dumps({"sessionId": "session_1", "sessionDir": str(sd), "workDir": wd}) + "\n")
        with open(sd / "agents" / "main" / "wire.jsonl", "w") as f:
            for i in range(turns):
                f.write(json.dumps({"type": "turn.prompt", "agentId": "main"}) + "\n")
                f.write(json.dumps({"type": "llm.request", "agentId": "main"}) + "\n")
                f.write(json.dumps({"type": "turn.ended", "agentId": "main", "turnId": i, "time": 1791057000000}) + "\n")

    def codex(self, seat: str, turns: int, pre: int = 0) -> None:
        self._session(seat, ["/home/x/.local/bin/codex", "--remote", "unix:///s.sock"], cwd="/var/tmp/bd-seats/codex-worker")
        r = self.home / ".codex" / "sessions" / "2026" / "10" / "03" / f"rollout-{seat}.jsonl"
        r.parent.mkdir(parents=True, exist_ok=True)
        with open(r, "w") as f:
            f.write(json.dumps({"timestamp": "2026-10-03T18:00:00.000Z", "payload": {"text": f"Identify as seat {seat}."}}) + "\n")
            for i in range(pre + turns):
                tid = f"{i:08d}-0000-0000-0000-000000000000"
                for _ in range(3):  # several events per turn: one turn
                    f.write(json.dumps({"timestamp": PRE if i < pre else POST, "turn_id": tid},
                                       separators=(",", ":")) + "\n")

    # ---- run ----
    def run(self, *args: str, down: str = "") -> list[str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("BD_")}
        env.update(LC_ALL="C", HOME=str(self.home), BD_TURN_CAP_P=str(self.p), BD_TURN_CAP_TMUX=str(self.bin / "tmux"),
                   BD_TURN_CAP_PROC=str(self.proc), BD_TURN_CAP_WATCH=WATCH, BD_REMOTE_SSH=str(self.bin / "ssh"),
                   FAKE_TMUX_DIR=str(self.tmuxdir), FAKE_SSH_LOG=str(self.root / "ssh.log"), FAKE_DOWN=down)
        r = subprocess.run(["bash", CANDIDATE, *args], env=env, capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        return r.stdout.splitlines()

    @staticmethod
    def row(out: list[str], seat: str) -> list[str]:
        hits = [ln.split() for ln in out if ln.split()[:1] == [seat]]
        assert len(hits) == 1, (seat, out)
        return hits[0]


@pytest.fixture
def fleet(tmp_path):
    return Fleet(tmp_path)


def test_absolute_cap_wrap_at_80_retire_at_120(fleet):
    for seat, n in (("bd-w-79", 79), ("bd-w-80", 80), ("bd-w-85", 85), ("bd-w-119", 119), ("bd-w-120", 120), ("bd-w-125", 125)):
        fleet.claude(seat, n)
    out = fleet.run("--dry-run")
    got = {s: fleet.row(out, s)[1:4] for s in ("bd-w-79", "bd-w-80", "bd-w-85", "bd-w-119", "bd-w-120", "bd-w-125")}
    assert got == {
        "bd-w-79": ["79", "-", "OK"],
        "bd-w-80": ["80", "-", "WRAP"],  # the boundary: a > instead of >= reads this as OK
        "bd-w-85": ["85", "-", "WRAP"],
        "bd-w-119": ["119", "-", "WRAP"],
        "bd-w-120": ["120", "-", "RETIRE"],
        "bd-w-125": ["125", "-", "RETIRE"],
    }, out
    assert out[0].startswith("TURN-CAP ") and "seats=6 " in out[0] and "hub tmux bd-*=6" in out[0], out[0]


def test_turn_is_a_model_turn_not_a_line_or_tool_use(fleet):
    t = fleet.claude("bd-w-turns", 85)
    lines = t.read_text().splitlines()
    assert sum('"type": "assistant"' in ln for ln in lines) == 85 * 4  # 4 assistant lines per turn in the fixture
    assert fleet.row(fleet.run("--dry-run"), "bd-w-turns")[1] == "85"


def test_grandfathered_seat_uses_baseline_plus_10_and_20(fleet):
    for seat, n in (("bd-g-130", 130), ("bd-g-135", 135), ("bd-g-136", 136), ("bd-g-145", 145)):
        fleet.claude(seat, n, pre=125)
    fleet.base.write_text("# O1760 baseline\n" + "".join(f"{s}\t125\t{CUT}\n" for s in ("bd-g-130", "bd-g-135", "bd-g-136", "bd-g-145")))
    out = fleet.run()
    assert fleet.row(out, "bd-g-130")[1:4] == ["130", "125", "OK"], out
    assert fleet.row(out, "bd-g-135")[1:4] == ["135", "125", "WRAP"], out
    assert fleet.row(out, "bd-g-136")[1:4] == ["136", "125", "WRAP"], out
    assert fleet.row(out, "bd-g-145")[1:4] == ["145", "125", "RETIRE"], out  # RETIRE boundary: baseline+20 exactly


def test_absent_from_baseline_is_not_grandfathered(fleet):
    fleet.claude("bd-g-old", 130, pre=125)
    fleet.claude("bd-g-new", 130, pre=125)
    fleet.base.write_text(f"bd-g-old\t125\t{CUT}\n")
    out = fleet.run()
    assert fleet.row(out, "bd-g-old")[3] == "OK", out
    assert fleet.row(out, "bd-g-new")[1:4] == ["130", "-", "RETIRE"], out


def test_relaunched_seat_with_a_baseline_row_is_held_to_the_absolute_cap(fleet):
    # same name, fresh session after the cut (O1760 relaunch): its baseline belongs to the earlier session
    fleet.claude("bd-g-relaunched", 85, pre=0)
    fleet.base.write_text(f"bd-g-relaunched\t125\t{CUT}\n")
    r = fleet.row(fleet.run(), "bd-g-relaunched")
    assert r[1:4] == ["85", "125", "WRAP"], r


def test_unreadable_or_missing_transcript_is_unknown_not_zero(fleet):
    fleet.claude("bd-u-dir", 0, transcript="dir")
    fleet.claude("bd-u-none", 0, transcript="none")
    fleet.claude("bd-u-zero", 0)  # positive control: a readable, empty transcript is FOUND NONE = 0
    out = fleet.run("--dry-run")
    assert fleet.row(out, "bd-u-dir")[1:4] == ["UNKNOWN", "-", "UNKNOWN"], out
    assert "unreadable" in " ".join(fleet.row(out, "bd-u-dir")), out
    assert fleet.row(out, "bd-u-none")[1:4] == ["UNKNOWN", "-", "UNKNOWN"], out
    assert "no transcript" in " ".join(fleet.row(out, "bd-u-none")), out
    assert fleet.row(out, "bd-u-zero")[1:4] == ["0", "-", "OK"], out
    assert "UNKNOWN=2 " in out[0], out[0]


def test_seat_without_an_agent_process_is_unknown(fleet):
    fleet._session("bd-u-shell", ["vim"])
    r = fleet.row(fleet.run("--dry-run"), "bd-u-shell")
    assert r[1:4] == ["UNKNOWN", "-", "UNKNOWN"], r


def test_baseline_written_once_and_never_rewritten(fleet):
    fleet.claude("bd-b-pre", 50, pre=40)
    fleet.claude("bd-b-post", 30, pre=0)
    fleet.claude("bd-b-unk", 0, transcript="none")
    fleet.run("--dry-run")
    assert not fleet.base.exists(), "--dry-run wrote the baseline"
    out = fleet.run()
    assert "written now" in out[0], out[0]
    body = fleet.base.read_text()
    rows = [ln.split("\t") for ln in body.splitlines() if not ln.startswith("#")]
    assert rows == [["bd-b-pre", "40", CUT]], body  # turns BEFORE the cut; a post-cut session is not grandfathered
    assert "# UNKNOWN bd-b-unk" in body, body
    before = fleet.base.read_bytes()
    fleet.claude("bd-b-later", 60, pre=60)
    out2 = fleet.run()
    assert fleet.base.read_bytes() == before, "baseline rewritten"
    assert out2[0].endswith(" present"), out2[0]
    assert fleet.row(out2, "bd-b-later")[2] == "-", out2


def test_sixty_seats_print_at_most_40_lines(fleet):
    for i in range(60):
        fleet.claude(f"bd-many-{i:02d}", 90 if i < 5 else 10)
    out = fleet.run("--dry-run")
    assert len(out) <= 40, len(out)
    assert sum(ln.split()[3:4] == ["WRAP"] for ln in out) == 5, out
    assert any(ln.startswith("OK 55 seats summarised") for ln in out), out
    full = fleet.run("--dry-run", "--all")
    assert len(full) == 61, len(full)


def test_sixty_non_ok_seats_still_fit_and_say_how_many_are_hidden(fleet):
    for i in range(60):
        fleet.claude(f"bd-hot-{i:02d}", 125 if i < 10 else 85)
    out = fleet.run("--dry-run")
    assert len(out) <= 40, len(out)
    assert [ln.split()[3] for ln in out[1:11]] == ["RETIRE"] * 10, out  # RETIRE rows first
    assert out[-1].startswith("+") and "rerun with --all" in out[-1], out


def test_agy_and_kimi_turns(fleet):
    fleet.agy("bd-a-agy", 7, older=40)
    fleet.kimi("bd-k-kimi", 3)
    out = fleet.run("--dry-run", "--src")
    assert fleet.row(out, "bd-a-agy")[1:4] == ["7", "-", "OK"], out
    assert fleet.row(out, "bd-k-kimi")[1:4] == ["3", "-", "OK"], out
    assert fleet.row(out, "bd-k-kimi")[-1].endswith("agents/main/wire.jsonl"), out


def test_kimi_without_a_session_is_unknown(fleet):
    fleet._session("bd-k-nosess", ["kimi-code"], cwd="/var/tmp/bd-seats/bd-k-nosess")
    (fleet.home / ".kimi").mkdir()
    (fleet.home / ".kimi" / "session_index.jsonl").write_text("")
    assert fleet.row(fleet.run("--dry-run"), "bd-k-nosess")[3] == "UNKNOWN"


@pytest.mark.skipif(not os.path.isfile(WATCH), reason="bd-turn-watch.sh (count_for) not on this host")
def test_codex_reuses_count_for(fleet):
    fleet.codex("bd-cx-t1", 12)
    fleet._session("bd-cx-t2", ["codex"], cwd="/x")  # no rollout carries its kick
    out = fleet.run("--dry-run")
    assert fleet.row(out, "bd-cx-t1")[1:4] == ["12", "-", "OK"], out
    assert fleet.row(out, "bd-cx-t2")[1:4] == ["UNKNOWN", "-", "UNKNOWN"], out


@pytest.mark.skipif(not os.path.isfile(WATCH), reason="bd-turn-watch.sh (count_for) not on this host")
def test_codex_counts_the_whole_session_not_only_turns_after_the_cut(fleet):
    fleet.codex("bd-cx-p1", 5, pre=80)  # 80 turns before the cut, 5 after: the session has 85
    out = fleet.run("--dry-run")
    assert fleet.row(out, "bd-cx-p1")[1:4] == ["85", "-", "WRAP"], out


def test_remote_seats_from_seat_hosts_are_probed_over_ssh(fleet):
    fleet.claude("bd-r-up", 85, host="10.9.9.1")
    fleet.claude("bd-r-down", 85, host="10.9.9.2")
    with open(fleet.hosts, "a") as f:
        f.write("bd-r-back\t10.9.9.1\t2026-10-03T18:00:00Z\tx\nbd-r-back\t-\t2026-10-03T19:00:00Z\tx\n")  # last row wins: hub
    out = fleet.run("--dry-run", down="10.9.9.2")
    up = fleet.row(out, "bd-r-up")
    assert up[1:4] == ["85", "-", "WRAP"] and "@10.9.9.1" in " ".join(up), out
    down = fleet.row(out, "bd-r-down")
    assert down[1:4] == ["UNKNOWN", "-", "UNKNOWN"] and "rc=255" in " ".join(down), out
    assert "SEAT-HOSTS remote rows=2" in out[0], out[0]
    assert sorted(set((fleet.root / "ssh.log").read_text().split())) == ["10.9.9.1", "10.9.9.2"]


def test_no_tmux_exits_zero_with_a_head_line(fleet):
    out = fleet.run("--dry-run")
    assert len(out) == 1 and "seats=0 " in out[0], out
