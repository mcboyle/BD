"""v3.66.1191 -- capture vault ownership is keyed and serialized."""
from __future__ import annotations

import fcntl
import importlib.machinery
import importlib.util
import os
import signal
import select
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parent.parent
CAPTURE = REPO / "capture.sh"
GC_PATH = REPO / "toolchain" / "bin" / "bd-gc"


def _load_gc(name: str):
    spec = importlib.util.spec_from_loader(
        name, importlib.machinery.SourceFileLoader(name, str(GC_PATH)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _vault_block() -> str:
    lines = CAPTURE.read_text().splitlines()
    start = next(i for i, line in enumerate(lines)
                 if line.startswith("CAPTURE_VAULT=0"))
    end = next(i for i in range(start, len(lines)) if lines[i].rstrip() == "fi")
    return "\n".join(lines[start:end + 1]) + "\n"


def _wait_claimed(proc):
    assert proc.stdout is not None
    for _ in range(4):
        line = proc.stdout.readline()
        assert line, "capture exited before CLAIMED barrier"
        if line.strip() == "CLAIMED": return
    raise AssertionError("capture never emitted CLAIMED barrier")


def test_the_vault_path_carries_the_run_id():
    block = _vault_block()
    assert 'CAPTURE_VAULT_DIR="/tmp/bd_capture_vault-${CAPTURE_RUN_ID:-$$}"' in block
    assert "20-capture-vault.conf" in block


def test_a_second_concurrent_vault_capture_refuses_with_a_named_reason(tmp_path):
    block = _vault_block()
    lock = tmp_path / "global.lock"
    env = dict(os.environ, CAPTURE_VAULT_PW="unit-test-value",
               CAPTURE_VAULT_GLOBAL_LOCK=str(lock))
    first = subprocess.Popen(
        ["bash", "-s"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, env=env)
    assert first.stdin is not None
    first.stdin.write(block + "echo CLAIMED\nsleep 30\n")
    first.stdin.close()
    try:
        _wait_claimed(first)
        second = subprocess.run(
            ["bash", "-s"], input=block, capture_output=True, text=True,
            env=env, timeout=10)
        assert second.returncode != 0
        assert second.stderr.count("CAPTURE-VAULT-CONCURRENCY-REFUSED") == 1
        assert second.stderr.count(f"holder_pid={first.pid}") == 1
    finally:
        first.send_signal(signal.SIGTERM)
        first.wait(timeout=10)


def test_two_sequential_vault_claims_both_succeed(tmp_path):
    block = _vault_block()
    env = dict(os.environ, CAPTURE_VAULT_PW="unit-test-value",
               CAPTURE_VAULT_GLOBAL_LOCK=str(tmp_path / "global.lock"))
    for _ in range(2):
        result = subprocess.run(["bash", "-s"], input=block,
                                capture_output=True, text=True, env=env,
                                timeout=10)
        assert result.returncode == 0, result.stderr
        assert "ENABLED" in result.stdout


def test_a_non_vault_capture_claims_the_same_singleton(tmp_path):
    block = _vault_block()
    env = dict(os.environ,
               CAPTURE_VAULT_GLOBAL_LOCK=str(tmp_path / "global.lock"))
    env.pop("CAPTURE_VAULT_PW", None)
    first = subprocess.Popen(
        ["bash", "-s"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, env=env)
    assert first.stdin is not None
    first.stdin.write(block + "echo CLAIMED\nsleep 30\n")
    first.stdin.close()
    try:
        _wait_claimed(first)
        second = subprocess.run(
            ["bash", "-s"], input=block, capture_output=True, text=True,
            env={**env, "CAPTURE_VAULT_PW": "unit-test-value"}, timeout=10)
        assert second.returncode == 73
        assert "CAPTURE-VAULT-CONCURRENCY-REFUSED" in second.stderr
    finally:
        first.send_signal(signal.SIGTERM)
        first.wait(timeout=10)


def test_singleton_is_not_inherited_by_a_detached_descendant(tmp_path):
    block = _vault_block(); lock = tmp_path / "global.lock"
    env = dict(os.environ, CAPTURE_VAULT_GLOBAL_LOCK=str(lock))
    ready_read, ready_write = os.pipe()
    script = (block + "setsid bash -c 'fd=$1; ready=$2; shift 2; "
              'eval "exec ${fd}>&-"; printf "LOCK_FD_CLOSED\\n" >&${ready}; '
              'eval "exec ${ready}>&-"; exec "$@"\' '
              'bd-close-fd-exec "$CAPTURE_VAULT_LOCK_FD" "$READY_FD" sleep 30 '
              '</dev/null >/dev/null 2>&1 &\necho DESCENDANT=$!\n')
    try:
        first = subprocess.run(
            ["bash", "-s"], input=script, capture_output=True, text=True,
            env={**env, "READY_FD": str(ready_write)}, pass_fds=(ready_write,),
            timeout=10)
    finally:
        os.close(ready_write)
    assert first.returncode == 0, first.stderr
    pid = int(next(x.split("=", 1)[1] for x in first.stdout.splitlines()
                   if x.startswith("DESCENDANT=")))
    try:
        os.kill(pid, 0)
        readable, _, _ = select.select([ready_read], [], [], 10)
        assert readable, "descendant never reached the post-close barrier"
        assert os.read(ready_read, 4096) == b"LOCK_FD_CLOSED\n"
        second = subprocess.run(["bash", "-s"], input=block,
                                capture_output=True, text=True, env=env, timeout=10)
        assert second.returncode == 0, second.stderr
        assert second.stdout.count("capture vault skipped") == 1
    finally:
        os.close(ready_read)
        os.kill(pid, signal.SIGTERM)

    heartbeat = (REPO / "scripts/lib/heartbeat.sh").read_text()
    assert 'eval "exec ${fd}>&-"' in heartbeat
    assert 'eval "exec ${fd}>&-"' in CAPTURE.read_text()


def test_keyed_vault_ownership_refuses_without_clobbering(tmp_path):
    run_id = f"pytest-{os.getpid()}-{time.time_ns()}"; vault = Path("/tmp") / f"bd_capture_vault-{run_id}"
    vault.mkdir(); (vault / "peer-secret").write_text("keep")
    env = dict(os.environ, CAPTURE_VAULT_PW="unit-test-value", CAPTURE_RUN_ID=run_id,
               CAPTURE_VAULT_GLOBAL_LOCK=str(tmp_path / "global.lock"))
    try:
        result = subprocess.run(["bash", "-s"], input=_vault_block()+"capture_vault_dir_claim\n",
                                capture_output=True, text=True, env=env, timeout=10)
        assert result.returncode == 73
        assert "CAPTURE-VAULT-OWNERSHIP-REFUSED" in result.stderr
        assert (vault / "peer-secret").read_text() == "keep"
    finally: shutil.rmtree(vault, ignore_errors=True)


def test_vault_acquisition_failures_are_named_and_do_not_continue(tmp_path):
    cases = {"chmod": ("chmod(){ return 1; }", "chmod 0700 failed"), "directory-open": ('capture_vault_open_dir(){ return 1; }', "directory descriptor open failed"), "lock-open": ('capture_vault_open_lock(){ return 1; }', "lock descriptor open failed"), "flock": ("flock(){ return 1; }", "lock acquisition failed")}
    for name, (override, expected_reason) in cases.items():
        run_id=f"pytest-{name}-{os.getpid()}-{time.time_ns()}"; vault=Path("/tmp")/f"bd_capture_vault-{run_id}"
        env=dict(os.environ, CAPTURE_VAULT_PW="unit-test-value", CAPTURE_RUN_ID=run_id,
                 CAPTURE_VAULT_GLOBAL_LOCK=str(tmp_path/f"{name}.lock"))
        try:
            result=subprocess.run(["bash","-s"], input=_vault_block()+override+"\ncapture_vault_dir_claim\necho CONTINUED\n", capture_output=True,text=True,env=env,timeout=10)
            assert result.returncode == 73, (name,result.stderr)
            assert "CAPTURE-VAULT-SETUP-REFUSED" in result.stderr
            assert expected_reason in result.stderr
            assert "CONTINUED" not in result.stdout
        finally:
            if vault.is_dir(): shutil.rmtree(vault,ignore_errors=True)
            elif vault.exists(): vault.unlink()


def test_setup_fault_never_removes_a_substituted_peer(tmp_path):
    run_id=f"pytest-substitute-{os.getpid()}-{time.time_ns()}"; vault=Path("/tmp")/f"bd_capture_vault-{run_id}"
    moved=Path(str(vault)+"-owned")
    env=dict(os.environ, CAPTURE_VAULT_PW="unit-test-value", CAPTURE_RUN_ID=run_id,
             CAPTURE_VAULT_GLOBAL_LOCK=str(tmp_path/"global.lock"))
    override=('chmod(){ command mv "$CAPTURE_VAULT_DIR" "$CAPTURE_VAULT_DIR-owned"; '
              'command mkdir "$CAPTURE_VAULT_DIR"; return 1; }')
    try:
        result=subprocess.run(["bash","-s"], input=_vault_block()+override+"\ncapture_vault_dir_claim\n",
                              capture_output=True,text=True,env=env,timeout=10)
        assert result.returncode == 73
        assert vault.is_dir(), "replacement claim was removed"
        assert moved.is_dir(), "descriptor-owned original was lost"
    finally:
        shutil.rmtree(vault,ignore_errors=True); shutil.rmtree(moved,ignore_errors=True)


def test_teardown_removes_only_the_descriptor_owned_vault(tmp_path):
    gc = _load_gc("bd_gc_1186_finish")
    own = tmp_path / "bd_capture_vault-own"
    peer = tmp_path / "bd_capture_vault-peer"
    own.mkdir(); peer.mkdir()
    (own / "secret").write_text("owned")
    (peer / "secret").write_text("peer")
    fd = os.open(own, os.O_RDONLY | os.O_DIRECTORY)
    try:
        ok, why = gc.finish_capture_vault(own, fd, allowed_parent=tmp_path)
    finally:
        os.close(fd)
    assert ok, why
    assert not own.exists()
    assert (peer / "secret").read_text() == "peer"


def test_shell_descriptor_is_inherited_by_the_real_bd_gc_cli(tmp_path):
    vault = Path(tempfile.mkdtemp(dir="/tmp", prefix="bd_capture_vault-shell-"))
    peer = tmp_path / "bd_capture_vault-peer"
    peer.mkdir()
    (vault / "secret").write_text("owned")
    (peer / "secret").write_text("peer")
    script = (
        'exec {owned}<"$VAULT"\n'
        'exec "$PY" "$GC" --finish-capture-vault "$VAULT" '
        '--owned-fd "$owned"\n')
    try:
        result = subprocess.run(
            ["bash", "-c", script], cwd=REPO, text=True, capture_output=True,
            env={**os.environ, "VAULT": str(vault), "PY": os.sys.executable,
                 "GC": str(GC_PATH)}, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert not vault.exists()
        assert (peer / "secret").read_text() == "peer"
    finally:
        if vault.exists():
            import shutil
            shutil.rmtree(vault)


def test_a_replaced_vault_path_is_refused_and_the_foreign_peer_survives(tmp_path):
    gc = _load_gc("bd_gc_1186_replace")
    own = tmp_path / "bd_capture_vault-own"
    moved = tmp_path / "moved"
    own.mkdir()
    fd = os.open(own, os.O_RDONLY | os.O_DIRECTORY)
    os.rename(own, moved)
    own.mkdir()
    (own / "foreign").write_text("keep")
    try:
        ok, why = gc.finish_capture_vault(own, fd, allowed_parent=tmp_path)
    finally:
        os.close(fd)
    assert not ok and "creation identity" in why
    assert (own / "foreign").read_text() == "keep"
    assert moved.is_dir()


def test_vault_gc_never_takes_a_live_vault(tmp_path, monkeypatch):
    gc = _load_gc("bd_gc_1186_live")
    monkeypatch.setattr(gc, "PREFIXES", (str(tmp_path / "bd_capture_vault-"),))
    vault = tmp_path / "bd_capture_vault-live"
    vault.mkdir()
    lock_path = vault / ".bd-capture-vault.lock"
    lock_path.touch()
    os.utime(vault, (1, 1))
    fd = os.open(lock_path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ok, why = gc.is_candidate(vault, time.time(), 60)
        assert not ok and "LIVE" in why
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    ok, why = gc.is_candidate(vault, time.time(), 60)
    assert ok and "ABANDONED" in why


def test_keyed_vault_forensics_are_bounded(tmp_path, monkeypatch):
    gc = _load_gc("bd_gc_1186_bound")
    monkeypatch.setattr(gc, "PREFIXES", (str(tmp_path / "bd_capture_vault-"),))
    monkeypatch.setattr(gc, "FORENSICS_KEEP", 2)
    made = []
    for index in range(3):
        vault = tmp_path / f"bd_capture_vault-{index}"
        vault.mkdir()
        (vault / ".bd-capture-vault.lock").touch()
        os.utime(vault, (100 + index, 100 + index))
        made.append(vault)
    eligible, skipped = gc.scan(time.time(), 60, root=str(tmp_path))
    assert {Path(path) for path, _ in eligible} == {made[0]}
    assert sum("newest" in why for _path, why in skipped) == 2


def test_the_keyed_vault_is_still_not_in_the_bundle_namespace():
    block = _vault_block()
    assert "/tmp/bd_capture-" not in block
    assert "bd_capture_vault-${CAPTURE_RUN_ID" in block
