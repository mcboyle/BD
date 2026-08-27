"""``bd-claim`` keeps registry locking and atomic claim publication together.

The registry lock protects cooperating ``bd-claim`` readers and writers. Atomic
replacement separately protects observers which read the claim pathname without
participating in that lock. The controlled publication boundary below measures
both properties during the same real writer operation; it does not infer either
one from source text.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import threading

import pytest


BD_GATE_SCOPE = "module"

REPO = Path(__file__).resolve().parents[1]
CLAIM = REPO / "toolchain" / "bin" / "bd-claim"


def _load_claim():
    loader = importlib.machinery.SourceFileLoader(
        f"row295_bd_claim_{threading.get_ident()}", str(CLAIM))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    subject = importlib.util.module_from_spec(spec)
    loader.exec_module(subject)
    return subject


def _exercise_union(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *,
                    remove_lock: bool = False,
                    plain_publish: bool = False) -> dict:
    subject = _load_claim()
    repo = tmp_path / (
        "no-lock" if remove_lock else
        "plain-publish" if plain_publish else "union"
    )
    (repo / ".git").mkdir(parents=True)

    seed_rc = subject.add(
        repo, ["old.py"], "seed", owner="shared-owner", ttl=0)
    claim_file = repo / ".git" / "bd-claims" / "shared-owner.json"
    initial_bytes = claim_file.read_bytes()
    initial_record = json.loads(initial_bytes)
    assert seed_rc == 0
    assert initial_record["owner"] == "shared-owner"
    assert initial_record["paths"] == ["old.py"]
    assert set(initial_record) == {
        "owner", "owner_pid", "owner_start", "expires_at", "label",
        "paths", "pid", "ppid",
    }

    if remove_lock:
        @contextmanager
        def no_claims_lock(_repo):
            yield

        subject._claims_lock = no_claims_lock

    if plain_publish:
        def publish_directly(path: Path, record: dict) -> None:
            path.write_text(json.dumps(record, indent=1), encoding="utf-8")

        subject._publish_claim = publish_directly

    publication_paused = threading.Event()
    release_publication = threading.Event()
    seam_counts = {"replace": 0, "direct_write": 0}
    real_replace = os.replace
    real_write_text = Path.write_text

    def controlled_replace(source, destination, *args, **kwargs):
        if Path(destination) != claim_file:
            return real_replace(source, destination, *args, **kwargs)
        seam_counts["replace"] += 1
        publication_paused.set()
        assert release_publication.wait(5.0), "atomic publisher was not released"
        return real_replace(source, destination, *args, **kwargs)

    def controlled_write_text(path, data, *args, **kwargs):
        if path != claim_file:
            return real_write_text(path, data, *args, **kwargs)
        seam_counts["direct_write"] += 1
        payload = data.encode(kwargs.get("encoding") or "utf-8")
        split = max(1, len(payload) // 2)
        descriptor = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        try:
            assert os.write(descriptor, payload[:split]) == split
            publication_paused.set()
            assert release_publication.wait(5.0), "plain publisher was not released"
            remainder = payload[split:]
            assert os.write(descriptor, remainder) == len(remainder)
        finally:
            os.close(descriptor)
        return len(data)

    monkeypatch.setattr(os, "replace", controlled_replace)
    monkeypatch.setattr(Path, "write_text", controlled_write_text)

    writer_result: dict[str, object] = {}

    def write_claim() -> None:
        try:
            writer_result["rc"] = subject.add(
                repo, ["new.py"], "writer", owner="shared-owner", ttl=0)
        except BaseException as exc:  # retained for an exact parent-thread verdict
            writer_result["error"] = repr(exc)

    writer = threading.Thread(target=write_claim, name="row295-claim-writer")
    writer.start()
    assert publication_paused.wait(5.0), (
        "writer did not reach a claim publication seam within 5.0s")
    assert writer.is_alive(), "writer was not paused at the publication seam"
    assert seam_counts["replace"] + seam_counts["direct_write"] == 1

    lock_path = repo / ".git" / "bd-claims" / ".registry.lock"
    assert lock_path.is_file(), "the fixture did not create .registry.lock"
    lock_descriptor = os.open(lock_path, os.O_RDWR)
    lock_observation = {"contended": 0, "acquired": 0}
    try:
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_observation["contended"] += 1
        else:
            lock_observation["acquired"] += 1
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
    finally:
        os.close(lock_descriptor)

    observed_bytes = claim_file.read_bytes()
    try:
        observed_record = json.loads(observed_bytes)
        observed_json_error = None
    except ValueError as exc:
        observed_record = None
        observed_json_error = str(exc)

    release_publication.set()
    writer.join(5.0)
    assert not writer.is_alive(), "claim writer exceeded its 5.0s completion bound"
    final_bytes = claim_file.read_bytes()
    final_record = json.loads(final_bytes)

    return {
        "final_record": final_record,
        "initial_bytes": initial_bytes,
        "initial_record": initial_record,
        "lock": lock_observation,
        "observed_bytes": observed_bytes,
        "observed_json_error": observed_json_error,
        "observed_record": observed_record,
        "plain_publish": plain_publish,
        "remove_lock": remove_lock,
        "seams": seam_counts,
        "writer_result": writer_result,
    }


def _assert_lock_and_atomic_publish(observation: dict) -> None:
    assert observation["writer_result"] == {"rc": 0}
    assert observation["lock"] == {"contended": 1, "acquired": 0}, (
        "registry lock was not held across claim publication: "
        f"{observation['lock']!r}")
    assert observation["seams"] == {"replace": 1, "direct_write": 0}, (
        "concurrent observer saw a partially written claim instead of the old "
        "complete record remaining visible until atomic replacement: "
        f"seams={observation['seams']!r} "
        f"json_error={observation['observed_json_error']!r} "
        f"bytes={observation['observed_bytes']!r}")
    assert observation["observed_json_error"] is None
    assert observation["observed_bytes"] == observation["initial_bytes"]
    assert observation["observed_record"] == observation["initial_record"]
    assert observation["final_record"]["paths"] == ["new.py", "old.py"]


def test_registry_lock_and_atomic_publish_hold_during_the_same_add(
        tmp_path, monkeypatch):
    observation = _exercise_union(tmp_path, monkeypatch)
    assert observation["remove_lock"] is False
    assert observation["plain_publish"] is False
    _assert_lock_and_atomic_publish(observation)


def test_lock_removal_negative_control_fails_the_union_contract(
        tmp_path, monkeypatch):
    observation = _exercise_union(tmp_path, monkeypatch, remove_lock=True)
    assert observation["seams"] == {"replace": 1, "direct_write": 0}
    assert observation["observed_record"] == observation["initial_record"]
    assert observation["lock"] == {"contended": 0, "acquired": 1}
    with pytest.raises(AssertionError, match="registry lock was not held"):
        _assert_lock_and_atomic_publish(observation)


def test_plain_write_negative_control_exposes_partial_claim(
        tmp_path, monkeypatch):
    observation = _exercise_union(tmp_path, monkeypatch, plain_publish=True)
    assert observation["lock"] == {"contended": 1, "acquired": 0}
    assert observation["seams"] == {"replace": 0, "direct_write": 1}
    assert observation["observed_json_error"] is not None
    assert observation["observed_record"] is None
    with pytest.raises(AssertionError, match="partially written claim"):
        _assert_lock_and_atomic_publish(observation)


def test_transform_control_imports_without_asserting_union_behavior():
    subject = _load_claim()
    assert callable(subject.add)
    assert callable(subject.live_claims)
