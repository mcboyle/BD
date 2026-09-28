"""O1498: seat->PM bd-say traffic reaches the PM pane only for escalations and blockers.

BD_O1498_PM_GATEKEEPER_CANDIDATE = absolute path of the candidate bd-say.sh (opt-in; no live fallback).
Hermetic (rule 41): the candidate is copied into tmp_path with every /home/mboyle path rewritten into the
sandbox; tmux is a PATH stub that records send-keys per target; bd-batch-relay is a stub that exits 1 with
"Daemon dead (no pidfile)", the measured production state since 2026-09-28T05:11Z.
RED on the pre-O1498 script: a routine send is relayed, the relay fails, and the text is typed into the PM pane.
"""
BD_GATE_SCOPE = "module"
import os
import subprocess
from pathlib import Path

import pytest

CANDIDATE = os.environ.get("BD_O1498_PM_GATEKEEPER_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

PM, PM2, TRIAGE, WORKER = "bd-pm-Z9", "bd-pm-Z8", "bd-sentinel-Z9", "bd-worker-Z9"

TMUX_STUB = r"""#!/bin/bash
# records send-keys; has-session/ls answer from $SB/live; capture-pane shows what was typed plus an empty prompt
SB=@SB@
cmd=$1; shift
tgt=""; args=("$@")
for ((i=0; i<${#args[@]}; i++)); do [ "${args[i]}" = -t ] && tgt=${args[i+1]}; [ "${args[i]}" = -pt ] && tgt=${args[i+1]}; done
tgt=${tgt#=}; tgt=${tgt%%:*}; tgt=${tgt%%.*}
case "$cmd" in
  has-session) grep -qxF -- "$tgt" "$SB/live" ;;
  ls|list-sessions) cat "$SB/live" ;;
  send-keys)
    if [ "${args[2]:-}" = -l ]; then printf '%s\t%s\n' "$tgt" "${args[3]}" >> "$SB/typed"; printf '%s\n' "${args[3]}" >> "$SB/buf-$tgt"
    else printf '%s\tKEY:%s\n' "$tgt" "${args[*]: -1}" >> "$SB/keys"; fi ;;
  capture-pane) cat "$SB/buf-$tgt" 2>/dev/null; printf '\n\xe2\x9d\xaf \n' ;;
  display|display-message|list-panes) exit 0 ;;
  *) exit 0 ;;
esac
"""

RELAY_DEAD = "#!/bin/sh\necho 'Daemon dead (no pidfile)' >&2\nexit 1\n"


@pytest.fixture
def sb(tmp_path):
    cand = Path(CANDIDATE)
    assert cand.is_file() and os.access(cand, os.R_OK), f"candidate supplied but unreadable: {cand}"
    p = tmp_path / "persist"; (p / "harness").mkdir(parents=True)
    (tmp_path / "none").mkdir(); (tmp_path / "bin").mkdir()
    src = cand.read_text()
    for old, new in (("/home/mboyle/bd-persist/", f"{p}/"), ("/home/mboyle/bd-role-claim.sh", f"{tmp_path}/none/rc"),
                     ("/home/mboyle/bin/", f"{tmp_path}/none/"), ("/home/mboyle/teamwork_projects/", f"{tmp_path}/none/")):
        src = src.replace(old, new)
    assert "/home/mboyle" not in src, "sandbox rewrite left a host path"
    say = tmp_path / "say.sh"; say.write_text(src); say.chmod(0o755)
    (p / "PM-SEAT").write_text(f"{PM2}\n{PM}\n")
    (p / "TRIAGE-SEAT").write_text(f"{TRIAGE}\n")
    (tmp_path / "live").write_text(f"{PM}\n{PM2}\n{TRIAGE}\n{WORKER}\n")
    tm = tmp_path / "bin" / "tmux"; tm.write_text(TMUX_STUB.replace("@SB@", str(tmp_path))); tm.chmod(0o755)
    rl = tmp_path / "relay-dead"; rl.write_text(RELAY_DEAD); rl.chmod(0o755)
    return tmp_path


def send(sb, text, sender=WORKER, target=PM, **extra):
    env = {"PATH": f"{sb}/bin:/usr/bin:/bin", "HOME": str(sb), "BD_SEAT": sender, "BD_SAY_LOG": f"{sb}/say.log",
           "BD_SAY_INBOX_ROOT": f"{sb}/inbox", "BD_SAY_REROUTE": "0", "BD_BATCH_RELAY": f"{sb}/relay-dead",
           "BD_SAY_RETIRED_ROOT": f"{sb}/retired", "LC_ALL": "C.UTF-8"}
    env.update(extra)
    r = subprocess.run(["bash", str(sb / "say.sh"), target, text], env=env, cwd=sb, capture_output=True, text=True,
                       timeout=60, check=False)
    typed = [ln.split("\t", 1)[0] for ln in (sb / "typed").read_text().splitlines()] if (sb / "typed").exists() else []
    filed = sorted(q.parent.name for q in (sb / "inbox").glob("*/*.md")) if (sb / "inbox").exists() else []
    for f in ("typed", "keys"):
        (sb / f).unlink(missing_ok=True)
    for b in sb.glob("buf-*"):
        b.unlink()
    for q in (sb / "inbox").glob("*/*.md") if (sb / "inbox").exists() else []:
        q.unlink()
    return r, typed, filed


ROUTINE = [
    "findings/REVERIFY-bh-drain-bd-agy-worker-1-bd-agy-flash-1.md",       # say.log 17:55:03Z, mode=type
    "B cache: no usage-b seat. /home/x/NOTE-BRIEFQ-DISPOSITION.md",       # 17:54:24Z, mode=type
    "briefq 0 sent, decide: /home/x/NOTE-BRIEFQ-DISPOSITION.md",          # 17:53:16Z, mode=type
    "O1496 7/7 live confirmed /home/x/CONFIRM-APPLY-LANE.md",             # 17:50:12Z, mode=type
]


@pytest.mark.parametrize("text", ROUTINE)
def test_routine_seat_to_pm_goes_to_triage_never_pm_pane(sb, text):
    r, typed, filed = send(sb, text)
    assert PM not in typed, f"O1498: routine seat->PM send reached the PM pane: {text!r} typed={typed} out={r.stdout[-300:]}"
    assert filed == [TRIAGE], f"O1498: routine send not filed to triage {TRIAGE}: filed={filed} typed={typed}"
    assert "retargeted to triage seat" in r.stdout


def test_retarget_moves_the_typing_target_too(sb):
    # DONE is a batch-bypass word (ESC_RE): after the retarget it is typed -- into the TRIAGE pane, not the PM's.
    r, typed, filed = send(sb, "DONE /home/x/DONE.md")
    assert typed == [TRIAGE], f"O1498: retargeted wake-word send typed into {typed}, expected [{TRIAGE}]"


@pytest.mark.parametrize("text", [
    "BLOCKED no brief /home/x/BLOCKED.md",
    "HIGH finding /home/x/FINDING.md",
    "WAKE BLOCKED unassigned /home/x/BLOCKED.md",   # 17:49:49Z: BLOCKED mid-text rides RELAY=1 on the old script
    "ESC r22 row722 wt gone: /home/x/ESC.md",
    "eff-p2 REFUTE, needs fixer: /home/x/NOTE.md",
    "FABLE ruling needed /home/x/Q.md",
    "idle, awaiting row",
    "B2-B up, need row: /home/x/NOTE.md",
])
def test_escalation_and_blocker_reach_pm_pane(sb, text):
    r, typed, filed = send(sb, text)
    assert typed == [PM], f"O1498: escalation did not reach the PM pane: {text!r} typed={typed} filed={filed} out={r.stdout[-300:]}"
    assert TRIAGE not in filed


@pytest.mark.parametrize("sender,text,extra", [
    (PM2, "routine note /home/x/N.md", {}),                           # a PM seat
    ("cron:bd-pm-heartbeat", "HB tick /home/x/HB.md", {}),
    ("ssh:mboyle", "operator note /home/x/N.md", {}),
    (TRIAGE, "forwarded /home/x/FWD.md", {}),                          # the triage seat itself
    (WORKER, "STOP all sends /home/x/STOP.md", {}),
    ("cron:bd-relay", "WAKE /home/x/board/pm.md", {"BD_SAY_MODE": "board", "BD_SAY_TARGET": PM}),
])
def test_exemptions_still_reach_pm(sb, sender, text, extra):
    r, typed, filed = send(sb, text, sender=sender, **extra)
    assert "retargeted to triage" not in r.stdout, f"O1498: exempt sender {sender} was retargeted: {r.stdout[-300:]}"
    assert TRIAGE not in typed and TRIAGE not in filed, f"exempt {sender}: typed={typed} filed={filed}"
    assert PM in typed or "PM" in filed, f"exempt {sender} reached nothing: typed={typed} filed={filed} rc={r.returncode}"


@pytest.mark.parametrize("text,want", [
    ("WAKE BLOCKED unassigned /home/x/BLOCKED.md", "pane"),
    ("eff-p2 REFUTE, needs fixer: /home/x/NOTE.md", "pane"),
    ("findings/REVERIFY-x.md", "triage"),
])
def test_live_relay_neither_queues_escalations_nor_routine_pm_traffic(sb, text, want):
    # a WORKING relay (exit 0) must not swallow a PM-direct send into its queue, nor keep routine PM traffic from triage
    live = sb / "relay-live"
    live.write_text(f"#!/bin/sh\necho \"$*\" >> {sb}/relay-enq\nexit 0\n"); live.chmod(0o755)
    r, typed, filed = send(sb, text, BD_BATCH_RELAY=str(live))
    assert not (sb / "relay-enq").exists(), f"O1498: PM traffic was queued on the relay: {text!r}"
    if want == "pane":
        assert typed == [PM], f"O1498: escalation queued instead of reaching the PM pane: {text!r} typed={typed}"
    else:
        assert filed == [TRIAGE] and PM not in typed, f"O1498: routine send missed triage: typed={typed} filed={filed}"


def test_fixture_positive_control(sb):
    # the stub can say YES: a plain worker->worker send is typed and recorded
    r, typed, filed = send(sb, "DONE /home/x/DONE.md", target=WORKER)
    assert typed == [WORKER], f"stub recorded nothing: rc={r.returncode} out={r.stdout[-300:]} err={r.stderr[-300:]}"
