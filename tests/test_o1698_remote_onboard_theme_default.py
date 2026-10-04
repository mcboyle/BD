import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_REMOTE_ONBOARD_THEME_DEFAULT_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


@pytest.fixture
def seat(tmp_path):
    library = Path(CANDIDATE) / "lib_remote_seat.sh"
    assert library.is_file(), "candidate absent: lib_remote_seat.sh"
    hub, remote = tmp_path / "hub", tmp_path / "remote"
    cwd = hub / "seat cwd"
    cwd.mkdir(parents=True)
    remote.mkdir()
    calls = tmp_path / "calls.jsonl"
    calls.write_text("")
    ssh = tmp_path / "ssh"
    ssh.write_text(f"#!{sys.executable}\n" + '''import json, os, shlex, subprocess, sys
args = shlex.split(sys.argv[-1])
with open(os.environ["FIX_CALLS"], "a") as out:
    out.write(json.dumps(args) + "\\n")
if args[0] == "tmux":
    sys.exit(1)
if args[:2] == ["sh", "-c"] and args[2].startswith("command -v "):
    sys.exit(0)
args = [a.replace(os.environ["FIX_HUB"], os.environ["FIX_REMOTE"]) for a in args]
sys.exit(subprocess.run(args, input=sys.stdin.buffer.read(), check=False).returncode)
''')
    ssh.chmod(0o755)
    rsync = tmp_path / "rsync"
    rsync.write_text("#!/bin/sh\nexit 0\n")
    rsync.chmod(0o755)
    env = dict(os.environ, HOME=str(hub), BD_SEAT="fixture-worker",
               BD_SELF_HOSTS="10.0.70.164", BD_HUB_HOST="10.0.70.164",
               BD_SEAT_HOSTS=str(tmp_path / "hosts.tsv"),
               BD_REMOTE_SHARED_ROOT=str(tmp_path / "shared"),
               BD_REMOTE_SSH=str(ssh), BD_REMOTE_RSYNC=str(rsync),
               BD_REMOTE_SEAT_LIB=str(library), FIX_HUB=str(hub),
               FIX_REMOTE=str(remote), FIX_CALLS=str(calls), FIX_CWD=str(cwd))
    env.pop("CLAUDE_CONFIG_DIR", None)
    return {"hub": hub, "remote": remote, "cwd": cwd, "env": env, "calls": calls}


def configure(seat, pool="A"):
    cfg = seat["hub"] / (".claude" if pool == "A" else ".claude-" + pool.lower())
    cfg.mkdir()
    source = seat["hub"] / ".claude.json" if pool == "A" else cfg / ".claude.json"
    source.write_text(json.dumps({"hasCompletedOnboarding": True,
                                 "projects": {str(seat["cwd"]): {"hasTrustDialogAccepted": True}}}))
    settings = cfg / "settings.json"
    settings.write_text("{}\n")
    rcfg = seat["remote"] / cfg.relative_to(seat["hub"])
    rcfg.mkdir()
    state = seat["remote"] / source.relative_to(seat["hub"])
    state.write_text(json.dumps({"remoteOnly": "keep", "projects": {}}))
    credential = rcfg / ".credentials.json"
    credential.write_text("non-secret-remote-fixture\n")
    seat["env"].update(FIX_CFG=str(cfg), FIX_POOL=pool)
    return source, state, credential


def prep(seat):
    command = '. "$BD_REMOTE_SEAT_LIB"; POOL=$FIX_POOL; pool_cfg(){ printf "%s\\n" "$FIX_CFG"; }; bd_remote_prep fixture.remote fixture-A "$FIX_CWD" claude "$FIX_CFG/settings.json"'
    return subprocess.run(["bash", "-c", command], env=seat["env"], capture_output=True,
                          text=True, timeout=10, check=False)


@pytest.mark.parametrize("pool", ["A", "B", "C", "D"])
@pytest.mark.parametrize("theme", ["absent", "empty", "null"])
def test_default_theme_allows_remote_launch_prep(seat, pool, theme):
    source, state, credential = configure(seat, pool)
    if theme != "absent":
        data = json.loads(source.read_text())
        data["theme"] = "" if theme == "empty" else None
        source.write_text(json.dumps(data))
    source_before, credential_before = source.read_bytes(), credential.read_bytes()
    result = prep(seat)
    output = result.stdout + result.stderr
    assert result.returncode == 0, "REMOTE-THEME-DEFAULT-REFUSED: " + output
    assert "staged fixture-A on fixture.remote" in result.stdout
    data = json.loads(state.read_text())
    assert data["theme"] == "dark", "REMOTE-THEME-DEFAULT-NOT-SEEDED"
    assert data["remoteOnly"] == "keep"
    remote_cwd = str(seat["cwd"]).replace(str(seat["hub"]), str(seat["remote"]))
    assert data["projects"][remote_cwd]["hasTrustDialogAccepted"] is True
    assert source.read_bytes() == source_before
    assert credential.read_bytes() == credential_before
    assert Path(seat["env"]["BD_SEAT_HOSTS"]).is_file()
    calls = [json.loads(line) for line in seat["calls"].read_text().splitlines()]
    assert len([call for call in calls if call[:2] == ["tmux", "has-session"]]) == 1


def test_present_theme_is_copied_control(seat):
    source, state, _ = configure(seat)
    data = json.loads(source.read_text())
    data["theme"] = "light"
    source.write_text(json.dumps(data))
    result = prep(seat)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(state.read_text())["theme"] == "light", "REMOTE-THEME-PRESENT-CHANGED"


@pytest.mark.parametrize("invalid", ["onboarding-false", "onboarding-missing", "trust-missing"])
def test_required_source_fields_still_refuse_control(seat, invalid):
    source, state, credential = configure(seat)
    data = json.loads(source.read_text())
    if invalid == "onboarding-false":
        data["hasCompletedOnboarding"] = False
    elif invalid == "onboarding-missing":
        del data["hasCompletedOnboarding"]
    else:
        data["projects"][str(seat["cwd"])].clear()
    source.write_text(json.dumps(data))
    before, credential_before = state.read_bytes(), credential.read_bytes()
    result = prep(seat)
    assert result.returncode == 6, "REMOTE-REQUIRED-SOURCE-ACCEPTED: " + result.stdout + result.stderr
    assert "REMOTE-ONBOARDING-SOURCE-INVALID" in result.stdout
    assert state.read_bytes() == before
    assert credential.read_bytes() == credential_before
    assert not Path(seat["env"]["BD_SEAT_HOSTS"]).exists()
    assert not any(json.loads(line)[0] == "tmux" for line in seat["calls"].read_text().splitlines())
