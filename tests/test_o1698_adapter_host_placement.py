import argparse
import fcntl
import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_ADAPTER_HOST_PLACEMENT_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


@pytest.fixture
def ops(tmp_path, monkeypatch):
    candidate = Path(CANDIDATE)
    assert candidate.is_file() and os.access(candidate, os.X_OK), candidate
    spec = importlib.util.spec_from_loader("placement_candidate", SourceFileLoader("placement_candidate", str(candidate)))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for key in tuple(os.environ):
        if key.startswith("BD_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("BD_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("BD_LAUNCH_STAMP", str(tmp_path / "gate.stamp"))
    monkeypatch.setenv("BD_HOSTS_FILE", str(tmp_path / "hosts"))
    monkeypatch.setenv("BD_NFS_LOG", str(tmp_path / "nfs.log"))
    monkeypatch.setenv("BD_HUB_HOST", "10.0.70.164")
    (tmp_path / "hosts").write_text("test5 10.0.70.164 local\nspare12 10.0.70.183\n")
    (tmp_path / "nfs.log").write_text(
        "2026-10-03T00:00:00Z\tfixture\tMOUNT spare12 10.0.70.183 apply=1 rc=0\n"
        "2026-10-03T00:01:00Z\tfixture\tVERIFY spare12 10.0.70.183 RESULT=PASS lat=1\n"
    )
    helper = tmp_path / "helper"
    helper.write_text("#!/bin/sh\nprintf '%s\\n' 'RESULT seat=fixture-seat pid=42 pid_start=123 rc=0'\n")
    helper.chmod(0o755)
    for key in ("BD_LAUNCH_HELPER", "BD_LAUNCH_CODEX_HELPER", "BD_LAUNCH_KIMI_HELPER", "BD_LAUNCH_AGY_HELPER", "BD_LAUNCH_GROK_HELPER", "BD_ROLE_CLAIM_HELPER"):
        monkeypatch.setenv(key, str(helper))
    monkeypatch.setattr(module.time, "time", lambda: 1000.0)
    monkeypatch.setattr(module, "check_quota_circuit_breaker", lambda pool: {"state": "OK", "pool": pool})
    monkeypatch.setattr(module, "premise", lambda path: None)
    monkeypatch.setattr(module, "hub_gate", lambda: {"load1": 1, "procs": 2, "iowait": 0})
    real_run = module.subprocess.run
    def shim_run(argv, **kwargs):
        if argv == ["hostname", "-I"]:
            return subprocess.CompletedProcess(argv, 0, "10.0.70.164 172.17.0.1\n", "")
        return real_run(argv, **kwargs)
    monkeypatch.setattr(module.subprocess, "run", shim_run)
    monkeypatch.setattr(module.socket, "gethostname", lambda: "hub-fixture")
    module.fixture_root = tmp_path
    return module


def invoke(ops, *extra):
    brief = ops.fixture_root / "brief.md"
    brief.write_text("Offline parser fixture\n")
    ops.launch(["worker", "A", "--brief", str(brief), "--name", "fixture-seat", *extra])


def test_dry_route_and_zero_writes(ops, capsys):
    invoke(ops, "--host", "10.0.70.183", "--dry-run")
    output = capsys.readouterr().out
    assert "route:" in output and output.count("--host 10.0.70.183") == 1
    assert not (ops.fixture_root / "PLACEMENTS.tsv").exists()
    assert not (ops.fixture_root / "gate.stamp").exists()


@pytest.mark.parametrize("pool,model", [("A", None), ("B", None), ("C", None), ("D", None)])
def test_each_route_carries_host(ops, pool, model):
    args = argparse.Namespace(role="worker", pool=pool, name="fixture-seat", model=model, host="10.0.70.183")
    argv = list(map(str, ops.launch_route(args)))
    assert argv[-2:] == ["--host", "10.0.70.183"]
    assert argv.count("--host") == 1


@pytest.mark.parametrize("kind", ["absent", "old", "failed", "other-host", "malformed"])
def test_verify_refusals(ops, kind):
    log = ops.fixture_root / "nfs.log"
    original = log.read_text()
    assert "MOUNT" in original and "RESULT=PASS" in original
    text = {
        "absent": "\n".join(line for line in original.splitlines() if "VERIFY" not in line) + "\n",
        "old": original.replace("00:01:00", "00:00:00"),
        "failed": original + "2026-10-03T00:02:00Z\tfixture\tVERIFY spare12 10.0.70.183 RESULT=FAIL\n",
        "other-host": original.replace("VERIFY spare12 10.0.70.183", "VERIFY other 10.0.70.185"),
        "malformed": original.replace("2026-10-03T00:01:00Z", "broken-time"),
    }[kind]
    log.write_text(text)
    with pytest.raises(ops.Refusal, match="NO-VERIFY"):
        invoke(ops, "--host", "10.0.70.183", "--dry-run")


def test_unknown_host(ops):
    with pytest.raises(ops.Refusal, match="UNKNOWN-HOST"):
        invoke(ops, "--host", "10.0.70.999", "--dry-run")


def test_off_hub(ops, monkeypatch):
    monkeypatch.setattr(ops.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "10.0.70.183\n", ""))
    with pytest.raises(ops.Refusal, match="OFF-HUB"):
        invoke(ops, "--host", "10.0.70.183", "--dry-run")


def row(age=119, host="10.0.70.183", rc="0", seat="fixture-seat", pid_start="123"):
    return {"event": "RESULT", "host": host, "seat": seat, "pool": "A", "role": "worker", "key": "fixture", "pid": "42", "pid_start": pid_start, "rc": rc, "launch_s": str(1000-age)}


def journal(ops, *rows):
    path = ops.fixture_root / "PLACEMENTS.tsv"
    path.write_text("".join("\t".join(f"{k}={v}" for k,v in item.items()) + "\n" for item in rows))
    assert path.stat().st_size > 0
    return path


def observed_launch(ops, original_run, calls, argv, **kwargs):
    if "--host" in list(map(str, argv)):
        lines = (ops.fixture_root / "PLACEMENTS.tsv").read_text().splitlines()
        assert len(lines) == 1 and "event=INTENT" in lines[0] and "rc=pending" in lines[0]
        with open(ops.fixture_root / "gate.stamp.lock", "a") as fd:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        calls.append(argv)
    return original_run(argv, **kwargs)


def ssh_probe(original_run, calls, rc, start, argv, **kwargs):
    if argv[0] == "timeout":
        calls.append(argv)
        assert argv[:4] == ["timeout", "-k2", "15", "ssh"]
        assert "BatchMode=yes" in argv and "ConnectTimeout=5" in argv
        return subprocess.CompletedProcess(argv, rc, f"HOST load1=1 cores=2 mem=999 blocked=0\nPID 42 {start}\nNFS=OK\n" if start else "", "")
    return original_run(argv, **kwargs)


def claim_reap(original_run, releases, argv, **kwargs):
    if argv == ["hostname", "-I"]:
        return original_run(argv, **kwargs)
    releases.append(argv)
    return ""


@pytest.mark.parametrize("age,admit", [(119, False), (120, True), (121, True)])
def test_per_host_spacing_from_journal(ops, age, admit):
    journal(ops, row(age))
    if admit:
        invoke(ops, "--host", "10.0.70.183", "--dry-run")
    else:
        with pytest.raises(ops.Refusal, match="LAUNCH-RATE"):
            invoke(ops, "--host", "10.0.70.183", "--dry-run")


def test_intent_result_under_lock(ops, monkeypatch):
    real_run = ops.run
    calls = []
    monkeypatch.setattr(ops, "run", lambda argv, **kw: observed_launch(ops, real_run, calls, argv, **kw))
    invoke(ops, "--host", "10.0.70.183")
    lines = (ops.fixture_root / "PLACEMENTS.tsv").read_text().splitlines()
    assert len(lines) == 2 and len(calls) == 1
    assert "event=RESULT" in lines[1] and "pid=42" in lines[1] and "pid_start=123" in lines[1] and "rc=0" in lines[1]


@pytest.mark.parametrize("ssh_rc,start,diagnostic,dead", [(0, "123", "ALIVE", False), (0, "999", "DEAD", True), (255, "123", "KEEP", False), (124, "123", "KEEP", False), (0, "", "KEEP", False)])
def test_reap_identity_and_unreachable(ops, monkeypatch, capsys, ssh_rc, start, diagnostic, dead):
    path = journal(ops, row(121))
    before = path.read_bytes()
    calls = []
    real_run = ops.subprocess.run
    monkeypatch.setattr(ops.subprocess, "run", lambda argv, **kw: ssh_probe(real_run, calls, ssh_rc, start, argv, **kw))
    releases = []
    original_run = ops.run
    monkeypatch.setattr(ops, "run", lambda argv, **kw: claim_reap(original_run, releases, argv, **kw))
    ops.reap([])
    assert diagnostic in capsys.readouterr().out
    assert len(calls) == 1
    assert len(releases) == int(dead)
    if dead:
        assert len(path.read_text().splitlines()) == 2 and "rc=dead" in path.read_text().splitlines()[-1]
    else:
        assert path.read_bytes() == before


def test_remote_proc_probe_handles_spaces(ops, tmp_path):
    root = tmp_path / "proc"
    (root / "42").mkdir(parents=True)
    (root / "42" / "stat").write_text("42 (name with ) spaces) " + " ".join(["S"] + ["0"]*18 + ["123", "0"]) + "\n")
    assert ops.pid_start(root / "42" / "stat") == "123"


def test_reap_probe_busy_and_max_four(ops, monkeypatch, capsys):
    rows = [row(121, host=f"10.0.70.{180+i}", seat=f"fixture-{i}") for i in range(6)]
    journal(ops, *rows)
    real_run = ops.subprocess.run
    probes = []
    monkeypatch.setattr(ops.subprocess, "run", lambda argv, **kw: ssh_probe(real_run, probes, 255, "", argv, **kw))
    with open(ops.fixture_root / "probe-10.0.70.180.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ops.reap([])
    assert "PROBE-BUSY" in capsys.readouterr().out
    assert len(probes) == 4


def test_cli_refusal_code_and_diagnostic(ops, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["operations.py", "bd-command", "launch", "worker", "A", "--host", "10.0.70.999", "--dry-run"])
    assert ops.main() == 2
    assert "REFUSED: UNKNOWN-HOST" in capsys.readouterr().err


def test_missing_launcher_receipt_is_in_doubt(ops):
    (ops.fixture_root / "helper").write_text("#!/bin/sh\necho 'launched fixture without receipt'\n")
    with pytest.raises(ops.Refusal, match="PLACEMENT-NO-PID"):
        invoke(ops, "--host", "10.0.70.183")
    lines = (ops.fixture_root / "PLACEMENTS.tsv").read_text().splitlines()
    assert len(lines) == 2 and "rc=in-doubt" in lines[-1] and "pid=UNKNOWN" in lines[-1]


def test_helper_failure_records_exact_status(ops):
    (ops.fixture_root / "helper").write_text("#!/bin/sh\necho 'fixture launch failed' >&2\nexit 6\n")
    with pytest.raises(ops.Refusal, match="helper rc=6: fixture launch failed"):
        invoke(ops, "--host", "10.0.70.183")
    lines = (ops.fixture_root / "PLACEMENTS.tsv").read_text().splitlines()
    assert len(lines) == 2 and "event=RESULT" in lines[-1] and "rc=6" in lines[-1]


def test_plain_hub_keeps_no_host_route(ops, capsys):
    invoke(ops, "--dry-run")
    output = capsys.readouterr().out
    assert "route:" in output and "--host" not in output
    assert "host hub-fixture" in output


def test_plain_hub_live_shaped_launcher_succeeds(ops, capsys):
    (ops.fixture_root / "helper").write_text("#!/bin/sh\necho 'plain hub launched fixture without receipt'\n")
    invoke(ops)
    assert "plain hub launched fixture without receipt" in capsys.readouterr().out
    assert not (ops.fixture_root / "PLACEMENTS.tsv").exists()
    assert (ops.fixture_root / "gate.stamp").exists()


def test_reap_script_reads_actual_field22(ops, tmp_path):
    root = tmp_path / "proc"
    (root / "42").mkdir(parents=True)
    (root / "42" / "stat").write_text("42 (fixture ) spaced) " + " ".join(["S"] + ["0"] * 18 + ["123", "0"]) + "\n")
    (root / "loadavg").write_text("1.0 0 0 1/2 3\n")
    (root / "meminfo").write_text("MemAvailable: 999 kB\n")
    (root / "stat").write_text("procs_blocked 0\n")
    nonce = tmp_path / "nonce"
    nonce.write_text("fixture\n")
    result = subprocess.run(["/bin/sh", "-c", ops.placement_probe_script(), "sh", str(root), str(nonce), "fixture", "10.0.70.164", "42", "43"], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert "PID 42 123\n" in result.stdout and "PID 43 DEAD\n" in result.stdout
    assert result.stdout.startswith("HOST ") and result.stdout.endswith("NFS=BAD\n")


def test_fsync_both_rows(ops, monkeypatch):
    synced = []
    real_fsync = ops.os.fsync
    def fsync(fd):
        synced.append(fd)
        real_fsync(fd)
    monkeypatch.setattr(ops.os, "fsync", fsync)
    invoke(ops, "--host", "10.0.70.183")
    assert len(synced) == 2


def test_malformed_rows_skip_log_and_result_count(ops, capsys):
    path = ops.fixture_root / "PLACEMENTS.tsv"
    path.write_text("truncated append\nhost=10.0.70.183\tseat=broken\trc=0\tlaunch_s=bad-time\n")
    initial = path.read_text()
    assert len(initial.splitlines()) == 2
    invoke(ops, "--host", "10.0.70.183")
    stderr = capsys.readouterr().err
    assert stderr.count("JOURNAL-SKIP") == 2
    assert "line=1" in stderr and "line=2" in stderr
    assert path.read_text().startswith(initial)
    lines = path.read_text().splitlines()
    assert len(lines) == 4 and "event=RESULT" in lines[-1] and "journal_skip=2" in lines[-1]


def test_malformed_row_keeps_valid_host_pace(ops, capsys):
    path = journal(ops, row(119))
    with path.open("a") as output:
        output.write("partial row\n")
    before = path.read_bytes()
    with pytest.raises(ops.Refusal, match="LAUNCH-RATE"):
        invoke(ops, "--host", "10.0.70.183", "--dry-run")
    assert "JOURNAL-SKIP" in capsys.readouterr().err
    assert path.read_bytes() == before


@pytest.mark.parametrize("shape", ["malformed", "cannot-open"])
def test_plain_hub_never_reads_journal(ops, monkeypatch, capsys, shape):
    path = ops.fixture_root / "PLACEMENTS.tsv"
    if shape == "malformed":
        path.write_text("partial row\n")
    else:
        path.mkdir()
    (ops.fixture_root / "helper").write_text("#!/bin/sh\necho 'plain hub succeeds without journal access'\n")
    monkeypatch.setattr(ops, "placement_rows", lambda *args, **kwargs: pytest.fail("plain hub read the journal"))
    invoke(ops)
    assert "plain hub succeeds without journal access" in capsys.readouterr().out


def test_remote_journal_cannot_open_refuses(ops):
    (ops.fixture_root / "PLACEMENTS.tsv").mkdir()
    with pytest.raises(ops.Refusal, match="PLACEMENTS COULD NOT OPEN"):
        invoke(ops, "--host", "10.0.70.183", "--dry-run")


@pytest.mark.parametrize("named", [False, True])
def test_grok_host_refused_before_validation_or_launcher(ops, monkeypatch, named):
    def unexpected(*args, **kwargs):
        pytest.fail("grok --host reached validation/slot/launcher")
    monkeypatch.setattr(ops, "verified_host", unexpected)
    monkeypatch.setattr(ops, "launch_slot", unexpected)
    monkeypatch.setattr(ops, "_launch_route", unexpected)
    args = ["worker", "grok", "--host", "10.0.70.183"]
    if named:
        args += ["--name", "fixture-seat"]
    with pytest.raises(ops.Refusal, match="--host not supported for pool grok"):
        ops.launch(args)
    assert not (ops.fixture_root / "PLACEMENTS.tsv").exists()
    assert not (ops.fixture_root / "gate.stamp").exists()


def test_grok_plain_route_preserved(ops):
    args = argparse.Namespace(role="worker", pool="grok", name="fixture-seat", model=None, host=None)
    argv = list(map(str, ops.launch_route(args)))
    assert argv[1:] == ["worker", "grok", "fixture-seat"]


@pytest.mark.parametrize("remote", [False, True])
def test_control_handover_keeps_plugin_and_model_environment(ops, monkeypatch, remote):
    root = ops.fixture_root
    plugin = root / "plugins" / "fixture"
    plugin.mkdir(parents=True)
    monkeypatch.setattr(ops, "PERSIST", root)
    prompts = root / "role-prompts"
    prompts.mkdir()
    (prompts / "pm.prompt").write_text("fixture PM bootstrap\n")
    monkeypatch.setenv("BD_LAUNCH_ROLE_DIR", str(prompts))
    cardinality = root / "cardinality.tsv"
    cardinality.write_text("pm\tSINGLE\n")
    monkeypatch.setenv("BD_ROLE_CARDINALITY", str(cardinality))
    monkeypatch.setattr(ops, "incumbent", lambda role: "fixture-old-pm")
    receipt = root / "launch.json"
    helper = Path(os.environ["BD_LAUNCH_HELPER"])
    helper.write_text("#!/usr/bin/python3\nimport json,os,sys\nfrom pathlib import Path\n"
                      + "Path(" + repr(str(receipt)) + ").write_text(json.dumps({'argv':sys.argv[1:],'model':os.getenv('BD_LAUNCH_MODEL'),'dup':os.getenv('BD_LAUNCH_ALLOW_DUP')}))\n"
                      + "print('RESULT seat=fixture-pm pid=42 pid_start=123 rc=0')\n")
    args = ["pm", "A", "--successor", "--model", "opus", "--plugin-dir", str(plugin)]
    if remote:
        args += ["--host", "10.0.70.183"]
    ops.launch(args)
    data = json.loads(receipt.read_text())
    assert data["model"] == "opus" and data["dup"] == "1"
    assert data["argv"].count("--plugin-dir") == 1
    assert data["argv"][data["argv"].index("--plugin-dir") + 1] == str(plugin)
    assert data["argv"].count("--host") == int(remote)


@pytest.mark.parametrize("pool", ["codex", "kimi", "agy", "agy-claude"])
@pytest.mark.parametrize("named", [False, True])
def test_non_claude_host_refused_before_validation_or_launcher(ops, monkeypatch, pool, named):
    def unexpected(*args, **kwargs):
        pytest.fail("non-Claude --host reached validation/slot/launcher")
    monkeypatch.setattr(ops, "verified_host", unexpected)
    monkeypatch.setattr(ops, "launch_slot", unexpected)
    monkeypatch.setattr(ops, "_launch_route", unexpected)
    route = argparse.Namespace(role="worker", pool=pool, host="10.0.70.183", name=None, model=None)
    with pytest.raises(ops.Refusal, match="--host is Claude-pools-only until"):
        ops.launch_route(route)
    args = ["worker", pool, "--host", "10.0.70.183"]
    if named:
        args += ["--name", "fixture-seat"]
    with pytest.raises(ops.Refusal, match="--host is Claude-pools-only until"):
        ops.launch(args)
    assert not (ops.fixture_root / "PLACEMENTS.tsv").exists()
    assert not (ops.fixture_root / "gate.stamp").exists()


def test_plain_launch_uses_hostname_for_dry_line_and_pace_key(ops, monkeypatch, capsys):
    monkeypatch.setattr(ops.socket, "gethostname", lambda: "hub-fixture")
    invoke(ops, "--dry-run")
    assert "host hub-fixture last never" in capsys.readouterr().out
    assert not (ops.fixture_root / "launch-gate.hub-fixture.stamp").exists()
    invoke(ops)
    assert (ops.fixture_root / "launch-gate.hub-fixture.stamp").read_text() == "1000.000\n"
    assert not (ops.fixture_root / "launch-gate.10.0.70.164.stamp").exists()
