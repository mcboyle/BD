"""F16: every app_config read/merge/replace writer shares one OS lock."""
from __future__ import annotations

import fcntl
import importlib
import json
import multiprocessing
import os
import queue
from pathlib import Path
from typing import Any

import pytest


BD_GATE_SCOPE = "module"

_WRITERS = frozenset({"app", "global"})
_APP_KEY = "row311_app_writer"
_GLOBAL_KEY = "row311_global_writer"


def _writer_process(
    writer: str,
    work: str,
    messages: Any,
    start: Any,
    release_unlocked_read: Any,
    bypass_lock: bool,
) -> None:
    """Run one production writer, exposing only scheduling observations."""
    os.chdir(work)
    real_flock = fcntl.flock
    lock_held = False
    reads = 0

    def observed_flock(fd: int, operation: int) -> None:
        nonlocal lock_held
        if operation & fcntl.LOCK_UN:
            if not bypass_lock:
                real_flock(fd, operation)
            lock_held = False
            messages.put(("lock_released", writer, None))
            return
        if operation & fcntl.LOCK_EX:
            lock_target = str(Path(f"/proc/self/fd/{fd}").resolve())
            messages.put(("lock_attempt", writer, lock_target))
            if bypass_lock:
                messages.put(("lock_bypassed", writer, None))
                return
            real_flock(fd, operation)
            lock_held = True
            messages.put(("lock_acquired", writer, None))
            return
        real_flock(fd, operation)

    try:
        fcntl.flock = observed_flock
        if writer == "app":
            app = importlib.import_module("bulk_downloader.app")

            app._app_cfg.clear()
            app._app_cfg[_APP_KEY] = "app-value"
            write = app._save_app_config
        elif writer == "global":
            global_config = importlib.import_module("bulk_downloader.global_config")

            write = lambda: global_config.set_config(  # noqa: E731
                {_GLOBAL_KEY: "global-value"}
            )
        else:  # pragma: no cover - the parent owns the exact writer population
            raise AssertionError(f"unexpected writer {writer!r}")

        real_read_text = Path.read_text
        config_path = (Path(work) / "app_config.json").resolve()

        def observed_read_text(path: Path, *args: Any, **kwargs: Any) -> str:
            nonlocal reads
            value = real_read_text(path, *args, **kwargs)
            if path.resolve() == config_path:
                reads += 1
                state = "locked_read" if lock_held else "unlocked_read"
                messages.put((state, writer, reads))
                if not lock_held and not release_unlocked_read.wait(30):
                    raise TimeoutError(
                        "UNKNOWN: parent did not release the forced stale read"
                    )
            return value

        Path.read_text = observed_read_text
        messages.put(("ready", writer, None))
        if not start.wait(30):
            raise TimeoutError("UNKNOWN: writer start coordination was unavailable")
        result = write()
        messages.put(("done", writer, {"reads": reads, "result": result}))
    except BaseException as exc:
        messages.put(("error", writer, f"{type(exc).__name__}: {exc}"))
        raise
    finally:
        fcntl.flock = real_flock


def _run_forced_schedule(tmp_path: Path, *, bypass_lock: bool) -> dict[str, Any]:
    """Exercise the complete two-writer population under a forced interleave."""
    assert len(_WRITERS) == 2, "writer denominator unexpectedly changed"
    config_path = tmp_path / "app_config.json"
    config_path.write_text(json.dumps({"baseline": "kept"}), encoding="utf-8")

    context = multiprocessing.get_context("spawn")
    messages = context.Queue()
    start = context.Event()
    release_unlocked_read = context.Event()
    children = [
        context.Process(
            target=_writer_process,
            args=(
                writer,
                str(tmp_path),
                messages,
                start,
                release_unlocked_read,
                bypass_lock,
            ),
            name=f"row311-{writer}-writer",
        )
        for writer in sorted(_WRITERS)
    ]

    lock_path = tmp_path / "app_config.json.lock"
    events: list[tuple[str, str, Any]] = []
    ready: set[str] = set()
    attempts: list[tuple[str, str]] = []
    acquired: list[str] = []
    locked_reads: list[str] = []
    unlocked_reads: list[str] = []
    done: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    parent_lock_held = True

    with lock_path.open("a+b") as parent_lock:
        fcntl.flock(parent_lock.fileno(), fcntl.LOCK_EX)
        try:
            for child in children:
                child.start()

            while len(ready) < len(_WRITERS) and not errors:
                try:
                    kind, writer, detail = messages.get(timeout=30)
                except queue.Empty:
                    pytest.fail(
                        "UNKNOWN: writer readiness was unavailable; "
                        f"ready={sorted(ready)}, events={events!r}"
                    )
                events.append((kind, writer, detail))
                if kind == "ready":
                    ready.add(writer)
                elif kind == "error":
                    errors[writer] = detail

            assert not errors, f"writers failed before the transaction: {errors}"
            assert ready == _WRITERS, (
                f"writer population did not become ready: {sorted(ready)}"
            )
            start.set()

            while len(done) < len(_WRITERS) and not errors:
                try:
                    kind, writer, detail = messages.get(timeout=30)
                except queue.Empty:
                    pytest.fail(
                        "UNKNOWN: concurrent-writer measurement timed out; "
                        f"events={events!r}"
                    )
                events.append((kind, writer, detail))
                if kind == "lock_attempt":
                    attempts.append((writer, detail))
                elif kind == "lock_acquired":
                    acquired.append(writer)
                elif kind == "locked_read":
                    locked_reads.append(writer)
                elif kind == "unlocked_read":
                    unlocked_reads.append(writer)
                elif kind == "done":
                    done[writer] = detail
                elif kind == "error":
                    errors[writer] = detail

                # Both real lockers are now queued at the kernel boundary.  If
                # either writer reached its read without the lock, release the
                # parent lock so a partially protected sibling can finish.
                if parent_lock_held and (
                    {writer for writer, _target in attempts} == _WRITERS
                    or unlocked_reads
                ):
                    fcntl.flock(parent_lock.fileno(), fcntl.LOCK_UN)
                    parent_lock_held = False

                # With no lock (or the deliberate bypass), both stale reads
                # must exist before either replace.  With only one protected
                # writer, let that writer finish before releasing the stale
                # unprotected snapshot so the lost update stays deterministic.
                if set(unlocked_reads) == _WRITERS or (
                    len(set(unlocked_reads)) == 1 and done
                ):
                    release_unlocked_read.set()
        finally:
            start.set()
            release_unlocked_read.set()
            if parent_lock_held:
                fcntl.flock(parent_lock.fileno(), fcntl.LOCK_UN)

    for child in children:
        child.join(timeout=30)
    alive = [child.name for child in children if child.is_alive()]
    for child in children:
        if child.is_alive():
            child.terminate()
            child.join(timeout=10)

    assert not alive, f"UNKNOWN: test-owned writer processes did not exit: {alive}"
    exitcodes = {child.name: child.exitcode for child in children}
    assert all(code == 0 for code in exitcodes.values()), (
        f"writer process failure: exitcodes={exitcodes}, errors={errors}, "
        f"events={events!r}"
    )
    assert not errors, f"writer errors: {errors}"

    return {
        "ready": ready,
        "attempts": attempts,
        "acquired": acquired,
        "locked_reads": locked_reads,
        "unlocked_reads": unlocked_reads,
        "done": done,
        "config": json.loads(config_path.read_text(encoding="utf-8")),
        "events": events,
    }


def _assert_disjoint_keys_survived(config: dict[str, Any]) -> None:
    missing = sorted({_APP_KEY, _GLOBAL_KEY} - set(config))
    assert not missing, (
        "forced two-writer schedule lost independent app_config key(s): "
        f"missing={missing}, final={config}"
    )
    assert config == {
        "baseline": "kept",
        _APP_KEY: "app-value",
        _GLOBAL_KEY: "global-value",
    }


def test_concurrent_app_config_writers_preserve_both_disjoint_keys(tmp_path):
    result = _run_forced_schedule(tmp_path, bypass_lock=False)

    assert result["ready"] == _WRITERS
    assert set(result["done"]) == _WRITERS
    reads_by_writer = {
        writer: info["reads"] for writer, info in result["done"].items()
    }
    assert reads_by_writer["app"] == 1
    assert reads_by_writer["global"] == 1
    assert result["config"].get("baseline") == "kept"
    _assert_disjoint_keys_survived(result["config"])
    attempt_writers = [writer for writer, _target in result["attempts"]]
    assert sorted(attempt_writers) == sorted(_WRITERS), (
        "both app_config writers must attempt the shared OS transaction lock "
        f"exactly once; attempts={result['attempts']}, events={result['events']!r}"
    )
    expected_lock = str(tmp_path / "app_config.json.lock")
    assert {target for _writer, target in result["attempts"]} == {expected_lock}, (
        "both app_config writers must contend on the same lock file; "
        f"attempts={result['attempts']}"
    )
    assert sorted(result["acquired"]) == sorted(_WRITERS)
    assert sorted(result["locked_reads"]) == sorted(_WRITERS)
    assert result["unlocked_reads"] == []


def test_forced_schedule_loses_a_key_when_the_os_lock_is_bypassed(tmp_path):
    """Negative control: the harness must recreate the exact lost update."""
    result = _run_forced_schedule(tmp_path, bypass_lock=True)

    assert result["ready"] == _WRITERS
    assert set(result["done"]) == _WRITERS
    assert sorted(writer for writer, _target in result["attempts"]) == sorted(_WRITERS)
    assert result["acquired"] == []
    assert sorted(result["unlocked_reads"]) == sorted(_WRITERS)
    assert result["locked_reads"] == []
    missing = {_APP_KEY, _GLOBAL_KEY} - set(result["config"])
    assert len(missing) == 1, (
        "negative control did not force exactly one independent-key loss: "
        f"missing={sorted(missing)}, final={result['config']}"
    )
    with pytest.raises(AssertionError, match="forced two-writer schedule lost"):
        _assert_disjoint_keys_survived(result["config"])
