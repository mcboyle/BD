"""v3.66.1186 -- capture vault ownership is keyed and serialized."""
from __future__ import annotations

import fcntl
import importlib.machinery
import importlib.util
import os
import signal
import subprocess
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
        for _ in range(200):
            if lock.exists():
                break
            time.sleep(0.01)
        assert lock.exists(), "first preamble never created the lock"
        time.sleep(0.05)
        second = subprocess.run(
            ["bash", "-s"], input=block, capture_output=True, text=True,
            env=env, timeout=10)
        assert second.returncode != 0
        assert "CAPTURE-VAULT-CONCURRENCY-REFUSED" in second.stderr
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
