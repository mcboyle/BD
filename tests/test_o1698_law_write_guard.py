import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_LAW_WRITE_GUARD_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")
LAW = "/fixture/bd-persist/FLEET_RULE.md"
HOOKS = ("bd-tripwire-hook.py", "bd-tripwire-any.py")


@pytest.fixture
def invoke(tmp_path):
    root = Path(CANDIDATE)
    assert root.is_absolute() and root.is_dir(), "LAWGUARD candidate directory absent"
    for name in HOOKS:
        assert (root / name).is_file() and os.access(root / name, os.X_OK), name
    pm = tmp_path / "PM-SEAT"
    pm.write_text("bd-pm-C\n")
    claim = tmp_path / "claim"
    claim.write_text("#!/bin/sh\nexit 2\n")
    claim.chmod(0o755)
    log = tmp_path / "law.log"
    env = dict(os.environ)
    for key in ("BD_LAW_WRITE_MODE", "BD_SEAT", "BD_TRIPWIRE_OK"):
        env.pop(key, None)
    env.update(
        BD_SEAT="bd-cx-worker44",
        PWD=str(tmp_path),
        BD_LAW_WRITE_ROOT="/fixture/bd-persist",
        BD_LAW_WRITE_LOG=str(log),
        BD_TRIPWIRE_LOG=str(tmp_path / "trip.log"),
        BD_LAW_WRITE_PM_SEAT_FILE=str(pm),
        BD_LAW_WRITE_ROLE_CLAIM=str(claim),
    )

    def run(hook, tool="Write", path=LAW, command=None, mode=None, seat=None, **extra):
        ti = {"file_path": path}
        if command is not None:
            ti = {"command": command}
        ti.update(extra.pop("tool_input", {}))
        event = {"tool_name": tool, "tool_input": ti, "cwd": "/tmp"}
        supplied_event = extra.pop("event", {})
        if "toolCall" in supplied_event:
            event = {"cwd": "/tmp"}
        event.update(supplied_event)
        current = dict(env, **extra)
        if current.get("BD_LAW_WRITE_ROOT") is None:
            current.pop("BD_LAW_WRITE_ROOT", None)
        if mode is not None:
            current["BD_LAW_WRITE_MODE"] = mode
        if seat is not None:
            current["BD_SEAT"] = seat
        result = subprocess.run(
            [sys.executable, str(root / hook)],
            input=json.dumps(event), text=True, capture_output=True,
            env=current, cwd=tmp_path, timeout=10, check=False,
        )
        content = log.read_text() if log.exists() else ""
        return result, content

    return run


@pytest.mark.parametrize("hook", HOOKS)
@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit"])
@pytest.mark.parametrize("mode,rc,diagnostic", [(None, 0, "WOULD-DENY"), ("deny", 2, "DENY")])
def test_non_pm_law_write(invoke, hook, tool, mode, rc, diagnostic):
    result, log = invoke(hook, tool, mode=mode)
    assert result.returncode == rc, "LAWGUARD non-PM law write escaped enforcement"
    assert f"law-write-guard {diagnostic} seat=bd-cx-worker44 path={LAW}" in log, (
        "LAWGUARD expected distinctive law-write diagnostic missing"
    )
    if rc:
        assert len(result.stderr.splitlines()) == 1
        assert "law-write-guard DENY" in result.stderr


@pytest.mark.parametrize("hook", HOOKS)
@pytest.mark.parametrize("path", [LAW, "/fixture/bd-persist/LIVE-RULES-C.md", "/fixture/bd-persist/sub/FLEET_RULE-FLOOR.md"])
def test_pm_write_allowed(invoke, hook, path):
    result, log = invoke(hook, path=path, seat="bd-pm-C", mode="deny")
    assert result.returncode == 0 and not log and not result.stderr


@pytest.mark.parametrize("hook", HOOKS)
@pytest.mark.parametrize("tool,command,path", [
    ("Read", None, LAW),
    ("Bash", f"cat {LAW}", LAW),
    ("Bash", f"sed -n '1p' {LAW}", LAW),
    ("Bash", f"echo '>> {LAW}'", LAW),
    ("Bash", f"echo '>' {LAW}", LAW),
    ("Bash", f"cp {LAW} /tmp/copy.md", LAW),
    ("Write", None, "/tmp/FLEET_RULE.md"),
    ("Edit", None, "/fixture/bd-persist/COMMON.md"),
    ("Write", None, "/fixture/bd-persist/FLEET_RULE.md.backup"),
    ("Write", None, "/fixture/bd-persistent/FLEET_RULE.md"),
])
def test_negative_controls(invoke, hook, tool, command, path):
    result, log = invoke(hook, tool, path=path, command=command, mode="deny")
    assert result.returncode == 0 and not log and not result.stderr, "LAWGUARD unrelated/read control refused"


@pytest.mark.parametrize("hook", HOOKS)
@pytest.mark.parametrize("mode,rc,diagnostic", [(None, 0, "WOULD-DENY"), ("deny", 2, "DENY")])
@pytest.mark.parametrize("command", [
    f"printf lesson >> {LAW}", f"printf lesson>{LAW}", f"echo x 2>{LAW}",
    f"cat <<'EOF' >> {LAW}\nlesson\nEOF", f"tee -a {LAW}",
    f"sed -i 's/a/b/' {LAW}", f"sed --in-place=.bak 's/a/b/' {LAW}",
    f"cp /tmp/lesson {LAW}", f"mv /tmp/lesson {LAW}",
    "cp -t /fixture/bd-persist /tmp/FLEET_RULE.md",
    f"env X=1 bash -c 'printf lesson >> {LAW}'",
    "cd /fixture/bd-persist && printf lesson >> FLEET_RULE.md",
    f"BD_TRIPWIRE_OK='approved' printf lesson >> {LAW}",
    "printf lesson >> /fixture/bd-persist/LIVE-RULES-new.md",
])
def test_bash_destinations(invoke, hook, command, mode, rc, diagnostic):
    result, log = invoke(hook, "Bash", command=command, mode=mode)
    assert result.returncode == rc, "LAWGUARD Bash law destination escaped enforcement"
    assert f"law-write-guard {diagnostic}" in log, "LAWGUARD expected Bash law-write diagnostic missing"
    if rc:
        assert "law-write-guard DENY" in result.stderr


@pytest.mark.parametrize("hook", HOOKS)
def test_unknown_and_old_pm_are_not_pm(invoke, hook):
    for seat in ("", "bd-pm-D"):
        result, log = invoke(hook, seat=seat, mode="deny")
        assert result.returncode == 2 and "law-write-guard DENY" in log


@pytest.mark.parametrize("hook", HOOKS)
def test_claim_identity(invoke, hook, tmp_path):
    claim = tmp_path / "known-claim"
    claim.write_text("#!/bin/sh\nprintf 'pm\\tbd-pm-C\\n'\n")
    claim.chmod(0o755)
    result, log = invoke(hook, seat="", mode="deny", BD_LAW_WRITE_ROLE_CLAIM=str(claim))
    assert result.returncode == 0 and not log


@pytest.mark.parametrize("hook", HOOKS)
def test_ambiguous_claim_is_not_pm(invoke, hook, tmp_path):
    claim = tmp_path / "multi-claim"
    claim.write_text("#!/bin/sh\nprintf 'pm\\tbd-pm-C\\nworker\\tbd-cx-worker44\\n'\n")
    claim.chmod(0o755)
    for seed in range(1, 21):
        result, log = invoke(hook, seat="", mode="deny", BD_LAW_WRITE_ROLE_CLAIM=str(claim), PYTHONHASHSEED=str(seed))
        assert result.returncode == 2, f"LAWGUARD ambiguous claim allowed seed={seed}"
        assert log.splitlines()[-1] == f"law-write-guard DENY seat=UNKNOWN path={LAW}", f"seed={seed}"


@pytest.mark.parametrize("hook", HOOKS)
def test_guard_error_is_logged_and_fail_open(invoke, hook):
    result, log = invoke(hook, path=LAW + "\0", mode="deny")
    assert result.returncode == 0, "LAWGUARD hook error must fail open"
    assert "law-write-guard ERROR" in log and "Traceback" not in result.stderr


@pytest.mark.parametrize("hook", HOOKS)
def test_unwritable_log_fails_open(invoke, hook, tmp_path):
    result, _ = invoke(hook, mode="deny", BD_LAW_WRITE_LOG=str(tmp_path))
    assert result.returncode == 0 and "law-write-guard ERROR" in result.stderr


@pytest.mark.parametrize("hook", HOOKS)
@pytest.mark.parametrize("tool", ["mcp__fleet_sync__publish_lesson", "mcp__fleet-sync__publish_lesson"])
def test_rule_publish_is_warn_only(invoke, hook, tool):
    result, log = invoke(hook, tool, mode="deny", tool_input={"lesson_type": "rule"})
    assert result.returncode == 0, "LAWGUARD MCP rule publishing must remain warn-only"
    assert "law-write-guard WOULD-DENY" in log, "LAWGUARD MCP rule mutation escaped notice"
    assert "route lessons to COMMON.md" in result.stderr
    pm, _ = invoke(hook, tool, mode="deny", seat="bd-pm-C", tool_input={"lesson_type": "rule"})
    assert pm.returncode == 0 and not pm.stderr


@pytest.mark.parametrize("hook", HOOKS)
def test_lesson_publish_untouched(invoke, hook):
    result, log = invoke(hook, "mcp__fleet_sync__publish_lesson", mode="deny", tool_input={"lesson_type": "lesson"})
    assert result.returncode == 0 and not result.stderr and not log


@pytest.mark.parametrize("hook", HOOKS)
def test_default_protected_root(invoke, hook):
    path = str(Path.home() / "bd-persist" / "FLEET_RULE.md")
    result, log = invoke(hook, path=path, mode="deny", BD_LAW_WRITE_ROOT=None)
    assert result.returncode == 2 and f"path={path}" in log


@pytest.mark.parametrize("hook,rc", [("bd-tripwire-hook.py", 2), ("bd-tripwire-any.py", 0)])
def test_shadow_preserves_legacy_tripwires(invoke, hook, rc):
    result, log = invoke(hook, "Bash", command=f"printf lesson >> {LAW}; git add -A")
    assert result.returncode == rc, "LAWGUARD shadow bypassed an existing tripwire"
    assert "law-write-guard WOULD-DENY" in log
    if rc:
        assert "T1 git add -A/." in result.stderr


@pytest.mark.parametrize("hook", HOOKS)
def test_symlink_and_multiedit_targets(invoke, hook, tmp_path):
    link = tmp_path / "law-link"
    link.symlink_to(LAW)
    result, log = invoke(hook, path=str(link), mode="deny")
    assert result.returncode == 2 and "law-write-guard DENY" in log
    result, log = invoke(hook, "MultiEdit", path="/tmp/innocent.md", mode="deny",
                         tool_input={"edits": [{"file_path": LAW, "old_string": "x", "new_string": "y"}]})
    assert result.returncode == 2 and "law-write-guard DENY" in log


@pytest.mark.parametrize("mode,decision,diagnostic", [(None, "allow", "WOULD-DENY"), ("deny", "deny", "DENY")])
@pytest.mark.parametrize("tool,args", [
    ("run_command", {"CommandLine": f"printf lesson >> {LAW}"}),
    ("write_to_file", {"TargetFile": LAW, "CodeContent": "lesson"}),
    ("replace_file_content", {"TargetFile": LAW, "ReplacementContent": "lesson"}),
    ("multi_replace_file_content", {"TargetFile": LAW, "ReplacementChunks": []}),
    ("write_to_file", {"AbsolutePath": LAW, "CodeContent": "lesson"}),
])
def test_agy_law_write(invoke, tool, args, mode, decision, diagnostic):
    result, log = invoke("bd-tripwire-any.py", mode=mode, seat="bd-worker-agy",
                         event={"toolCall": {"name": tool, "args": args}})
    assert result.returncode == 0, "LAWGUARD AGY reply must use decision JSON, never rc2"
    reply = json.loads(result.stdout)
    assert reply["decision"] == decision, "LAWGUARD AGY law write escaped enforcement"
    assert f"law-write-guard {diagnostic}" in log, "LAWGUARD AGY write diagnostic missing"
    if decision == "deny":
        assert "law-write-guard DENY" in reply["reason"]


@pytest.mark.parametrize("mode", [None, "deny"])
@pytest.mark.parametrize("tool,args,seat", [
    ("view_file", {"AbsolutePath": LAW}, "bd-worker-agy"),
    ("run_command", {"CommandLine": f"cat {LAW}"}, "bd-worker-agy"),
    ("write_to_file", {"TargetFile": "/fixture/bd-persist/COMMON.md"}, "bd-worker-agy"),
    ("write_to_file", {"TargetFile": LAW}, "bd-pm-C"),
])
def test_agy_controls(invoke, tool, args, seat, mode):
    result, log = invoke("bd-tripwire-any.py", mode=mode, seat=seat,
                         event={"toolCall": {"name": tool, "args": args}})
    assert result.returncode == 0 and json.loads(result.stdout) == {"decision": "allow"}
    assert not log and not result.stderr, "LAWGUARD AGY read/PM/unrelated control refused"


def test_agy_shadow_preserves_legacy_tripwire(invoke):
    command = f"printf lesson >> {LAW}; git add -A"
    result, log = invoke("bd-tripwire-any.py", event={"toolCall": {"name": "run_command", "args": {"CommandLine": command}}})
    assert result.returncode == 0
    reply = json.loads(result.stdout)
    assert reply["decision"] == "deny" and "T1 git add -A/." in reply["reason"]
    assert "law-write-guard WOULD-DENY" in log, "LAWGUARD AGY shadow diagnostic missing"


@pytest.mark.parametrize("hook", HOOKS)
@pytest.mark.parametrize("mode,rc,diagnostic", [(None, 0, "WOULD-DENY"), ("deny", 2, "DENY")])
@pytest.mark.parametrize("command", [
    f"echo `printf lesson >> {LAW}`",
    f'echo "`printf lesson >> {LAW}`"',
    f'echo `sh -c "printf lesson >> {LAW}"`',
    f"echo $(printf lesson >> {LAW})",
])
def test_backtick_substitution(invoke, hook, mode, rc, diagnostic, command):
    result, log = invoke(hook, "Bash", command=command, mode=mode)
    assert result.returncode == rc, "LAWGUARD backtick law write escaped enforcement"
    assert f"law-write-guard {diagnostic}" in log, "LAWGUARD backtick write diagnostic missing"


@pytest.mark.parametrize("hook", HOOKS)
@pytest.mark.parametrize("mode", [None, "deny"])
@pytest.mark.parametrize("command", [
    f"echo '`printf lesson >> {LAW}`'",
    f'echo "\\`printf lesson >> {LAW}\\`"',
    f"echo `cat {LAW}`",
    "echo `printf lesson >> /fixture/bd-persist/COMMON.md`",
])
def test_backtick_controls(invoke, hook, mode, command):
    result, log = invoke(hook, "Bash", command=command, mode=mode)
    assert result.returncode == 0 and not log and not result.stderr, "LAWGUARD literal/read/unrelated backtick refused"


@pytest.mark.parametrize("mode,decision,diagnostic", [(None, "allow", "WOULD-DENY"), ("deny", "deny", "DENY")])
@pytest.mark.parametrize("cwd", ["/fixture/bd-persist", "'/fixture/bd-persist'", '"/fixture/bd-persist"'])
@pytest.mark.parametrize("command", ["printf lesson >> FLEET_RULE.md", 'printf lesson >> "$PWD/FLEET_RULE.md"'])
def test_agy_cwd(invoke, cwd, command, mode, decision, diagnostic):
    event = {"toolCall": {"name": "run_command", "args": {"CommandLine": command, "Cwd": cwd}}}
    result, log = invoke("bd-tripwire-any.py", mode=mode, seat="bd-worker-agy", event=event)
    assert result.returncode == 0, "LAWGUARD AGY Cwd reply must use decision JSON"
    assert json.loads(result.stdout)["decision"] == decision, "LAWGUARD AGY Cwd law write escaped enforcement"
    assert f"law-write-guard {diagnostic}" in log, "LAWGUARD AGY Cwd diagnostic missing"
    assert f"path={LAW}" in log


@pytest.mark.parametrize("mode,decision,diagnostic", [(None, "allow", "WOULD-DENY"), ("deny", "deny", "DENY")])
@pytest.mark.parametrize("tool,args", [
    ("edit_file", {"TargetFile": LAW}),
    ("delete_file", {"TargetFile": LAW}),
    ("delete_file", {"Source": LAW}),
    ("move_file", {"Source": "/tmp/lesson", "Destination": LAW}),
    ("move_file", {"Source": LAW, "Destination": "/tmp/archived.md"}),
    ("move_file", {"source": "/tmp/lesson", "destination": LAW}),
    ("move_file", {"Source": "FLEET_RULE.md", "Destination": "/tmp/archived.md", "Cwd": "/fixture/bd-persist"}),
    ("move_file", {"Source": "/tmp/lesson", "Destination": "FLEET_RULE.md", "Cwd": "/fixture/bd-persist"}),
])
def test_agy_latent_write(invoke, tool, args, mode, decision, diagnostic):
    result, log = invoke("bd-tripwire-any.py", mode=mode, seat="bd-worker-agy",
                         event={"toolCall": {"name": tool, "args": args}})
    assert result.returncode == 0, "LAWGUARD AGY latent reply must use decision JSON"
    assert json.loads(result.stdout)["decision"] == decision, "LAWGUARD AGY latent law write escaped enforcement"
    assert f"law-write-guard {diagnostic}" in log, "LAWGUARD AGY latent write diagnostic missing"
    assert f"path={LAW}" in log


@pytest.mark.parametrize("mode", [None, "deny"])
@pytest.mark.parametrize("tool,args,seat", [
    ("run_command", {"CommandLine": "printf lesson >> FLEET_RULE.md", "Cwd": "/tmp"}, "bd-worker-agy"),
    ("run_command", {"CommandLine": "cat FLEET_RULE.md", "Cwd": "/fixture/bd-persist"}, "bd-worker-agy"),
    ("run_command", {"CommandLine": "printf lesson >> FLEET_RULE.md", "Cwd": "/fixture/bd-persist"}, "bd-pm-C"),
    ("edit_file", {"TargetFile": "/fixture/bd-persist/COMMON.md"}, "bd-worker-agy"),
    ("delete_file", {"Source": "/fixture/bd-persist/COMMON.md"}, "bd-worker-agy"),
    ("move_file", {"Source": "/fixture/bd-persist/COMMON.md", "Destination": "/tmp/archived.md"}, "bd-worker-agy"),
    ("move_file", {"Source": LAW, "Destination": "/tmp/archived.md"}, "bd-pm-C"),
])
def test_agy_cwd_and_latent_controls(invoke, tool, args, seat, mode):
    result, log = invoke("bd-tripwire-any.py", mode=mode, seat=seat,
                         event={"toolCall": {"name": tool, "args": args}})
    assert result.returncode == 0 and json.loads(result.stdout) == {"decision": "allow"}
    assert not log and not result.stderr, "LAWGUARD Cwd/latent read-PM-unrelated control refused"
