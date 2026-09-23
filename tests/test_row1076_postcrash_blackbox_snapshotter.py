"""Row 1076: Post-Crash Flight-Recorder Blackbox Snapshotter.

Provides in-memory circular ring buffer of recent events, thread stack capture,
process telemetry snapshotting, and post-crash blackbox dumps upon unhandled exceptions.

RED on baseline: bulk_downloader lacks BlackboxSnapshotter and flight recorder engine.
"""
from __future__ import annotations

import errno
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

import pytest

try:
    from bulk_downloader.blackbox_snapshotter import (
        BlackboxSnapshotter,
        FlightRecorderRingBuffer,
        capture_crash_snapshot,
        get_blackbox_snapshotter,
        record_flight_event,
    )
except ImportError:
    BlackboxSnapshotter = None
    FlightRecorderRingBuffer = None
    capture_crash_snapshot = None
    get_blackbox_snapshotter = None
    record_flight_event = None

BD_GATE_SCOPE = "repo-wide"


@pytest.fixture(autouse=True)
def _app_files_stay_out_of_the_checkout(tmp_path, monkeypatch):
    """The first import of bulk_downloader.app, from whichever test runs first, starts the app's disk
    heartbeat (state/heartbeat.json, unless BD_DISABLE_KEEPALIVE is set) and creates logs/bulk_downloader.log
    and live_recordings/, all relative to the working directory. tests/conftest.py (isolated_bd_home)
    already runs every test from tmp_path with BD_DISABLE_KEEPALIVE set, so this changes nothing there;
    under --noconftest it keeps the app's files out of the checkout, whichever tests are run."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BD_DISABLE_KEEPALIVE", "1")


def test_blackbox_snapshotter_contract():
    """Verify blackbox_snapshotter module exports BlackboxSnapshotter and singleton accessors."""
    try:
        from bulk_downloader import blackbox_snapshotter
    except ImportError:
        blackbox_snapshotter = None

    assert blackbox_snapshotter is not None, (
        "Baseline lacks blackbox_snapshotter module (Row 1076)"
    )
    assert hasattr(blackbox_snapshotter, "BlackboxSnapshotter"), (
        "Baseline lacks BlackboxSnapshotter (Row 1076)"
    )
    assert hasattr(blackbox_snapshotter, "get_blackbox_snapshotter"), (
        "Baseline lacks get_blackbox_snapshotter (Row 1076)"
    )


def test_flight_recorder_ring_buffer_retention():
    """Verify FlightRecorderRingBuffer records events and respects capacity limits."""
    assert FlightRecorderRingBuffer is not None, "Row 1076 capability missing: FlightRecorderRingBuffer"

    buf = FlightRecorderRingBuffer(capacity=5)
    for i in range(8):
        buf.record("network", {"step": i, "status": "ok"})

    events = buf.get_recent_events()
    assert len(events) == 5
    assert events[0]["data"]["step"] == 3
    assert events[-1]["data"]["step"] == 7


def test_blackbox_crash_snapshot_contents():
    """Verify snapshot includes thread frames, exception traceback, and telemetry."""
    assert BlackboxSnapshotter is not None, "Row 1076 capability missing: BlackboxSnapshotter"

    with tempfile.TemporaryDirectory() as tmpdir:
        snapshotter = BlackboxSnapshotter(dump_dir=Path(tmpdir))
        snapshotter.record_event("worker", {"lane": "site_test", "action": "download"})

        try:
            raise ValueError("Corrupt block header in chunk 42")
        except ValueError as exc:
            snapshot = snapshotter.snapshot(exc_info=exc, context={"lane_id": "lane_99"})

        assert snapshot["exception"]["type"] == "ValueError"
        assert "Corrupt block header in chunk 42" in snapshot["exception"]["message"]
        assert len(snapshot["threads"]) >= 1
        assert "process" in snapshot
        assert snapshot["context"]["lane_id"] == "lane_99"
        assert len(snapshot["flight_events"]) == 1

        # Check dump to disk
        dump_files = list(Path(tmpdir).glob("blackbox_crash_*.json"))
        assert len(dump_files) == 1
        with open(dump_files[0], "r", encoding="utf-8") as f:
            disk_data = json.load(f)
        assert disk_data["exception"]["type"] == "ValueError"


def test_excepthook_installation_and_trigger():
    """Verify excepthook automatically captures unhandled exceptions into blackbox."""
    assert BlackboxSnapshotter is not None, "Row 1076 capability missing: BlackboxSnapshotter"

    with tempfile.TemporaryDirectory() as tmpdir:
        snapshotter = BlackboxSnapshotter(dump_dir=Path(tmpdir))
        original_hook = sys.excepthook

        try:
            snapshotter.install_excepthook()
            assert sys.excepthook is not original_hook

            try:
                raise RuntimeError("Uncaught thread panic")
            except RuntimeError:
                sys.excepthook(*sys.exc_info())

            metrics = snapshotter.get_metrics()
            assert metrics["snapshots_taken"] == 1
            assert len(list(Path(tmpdir).glob("blackbox_crash_*.json"))) == 1
        finally:
            snapshotter.uninstall_excepthook()
            assert sys.excepthook is original_hook


def test_app_errorhandler_integration_through_caller(isolated_blackbox):
    """Verify bulk_downloader.app errorhandler 500 triggers blackbox snapshotting through caller."""
    from bulk_downloader import app as bd_app

    assert hasattr(bd_app, "_on_internal_error"), "Baseline app lacks _on_internal_error"
    try:
        from bulk_downloader.blackbox_snapshotter import get_blackbox_snapshotter
    except ImportError:
        get_blackbox_snapshotter = None

    assert get_blackbox_snapshotter is not None, "Baseline lacks get_blackbox_snapshotter (Row 1076)"
    snapshotter = get_blackbox_snapshotter()
    initial_count = snapshotter.get_metrics()["snapshots_taken"]

    # Invoke app error handler simulating a 500 crash
    test_exc = RuntimeError("Simulated internal server fault")
    with bd_app.app.test_request_context("/api/download/start", method="POST"):
        resp = bd_app._on_internal_error(test_exc)

    assert resp is not None
    metrics_after = snapshotter.get_metrics()
    assert metrics_after["snapshots_taken"] > initial_count


def test_blackbox_telemetry_metrics():
    """Verify telemetry reporting metrics accurately."""
    assert BlackboxSnapshotter is not None, "Row 1076 capability missing: BlackboxSnapshotter"

    snapshotter = BlackboxSnapshotter()
    snapshotter.record_event("test_topic", {"key": "val"})

    metrics = snapshotter.get_metrics()
    assert metrics["events_recorded"] >= 1
    assert "snapshots_taken" in metrics
    assert "timestamp" in metrics


# --- REFUTE N6-A / P1-A E1-E4 ---

@pytest.fixture
def isolated_blackbox(tmp_path, monkeypatch):
    """Point the process singleton at tmp_path so no test dumps into the repo CWD."""
    assert BlackboxSnapshotter is not None, "Row 1076 capability missing: BlackboxSnapshotter"
    from bulk_downloader import blackbox_snapshotter as mod

    snap = BlackboxSnapshotter(dump_dir=tmp_path / "blackbox")
    monkeypatch.setattr(mod, "_GLOBAL_SNAPSHOTTER", snap)
    yield snap
    snap.uninstall_excepthook()


def _only_dump(snap):
    files = sorted(snap.dump_dir.glob("blackbox_crash_*.json"))
    assert len(files) == 1, f"expected one dump, found {files}"
    return json.loads(files[0].read_text(encoding="utf-8"))


def test_e1_real_flask_500_dump_names_the_real_exception_and_view(isolated_blackbox):
    from flask import Flask
    from bulk_downloader import app as bd_app

    fl = Flask("row1076_probe")
    fl.config["PROPAGATE_EXCEPTIONS"] = False
    fl.register_error_handler(500, bd_app._on_internal_error)

    @fl.route("/api/row1076/boom", methods=["POST"])
    def row1076_exploding_view():
        return 1 / 0

    resp = fl.test_client().post("/api/row1076/boom")
    assert resp.status_code == 500
    dump = _only_dump(isolated_blackbox)
    assert dump["exception"]["type"] == "ZeroDivisionError", dump["exception"]["type"]
    assert "row1076_exploding_view" in dump["exception"]["traceback"]
    assert "InternalServerError" not in dump["exception"]["type"]
    assert dump["context"] == {"path": "/api/row1076/boom", "method": "POST"}


def test_e1_wrapper_without_original_falls_back_to_the_handled_exception():
    from werkzeug.exceptions import InternalServerError
    from bulk_downloader.blackbox_snapshotter import _crash_cause

    try:
        raise KeyError("row1076-handled")
    except KeyError as handled:
        assert _crash_cause(InternalServerError()) is handled
    bare = InternalServerError()
    assert _crash_cause(bare) is bare, "no cause in flight: keep the wrapper"


def test_e2_unserializable_flight_event_still_writes_a_whole_dump(tmp_path):
    snap = BlackboxSnapshotter(dump_dir=tmp_path)
    snap.record_event("odd", {"when": object()})
    snap.snapshot(exc_info=ValueError("row1076-e2"))
    dump = _only_dump(snap)
    assert dump["exception"]["message"] == "row1076-e2"
    assert "object object" in dump["flight_events"][0]["data"]["when"]
    assert snap.get_metrics()["snapshots_taken"] == 1
    assert not list(tmp_path.glob("*.tmp"))


def test_e2_a_failed_write_is_not_counted_and_leaves_no_file(tmp_path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")
    snap = BlackboxSnapshotter(dump_dir=blocker / "blackbox")
    snap.snapshot(exc_info=ValueError("row1076-e2-fail"))
    assert snap.get_metrics()["snapshots_taken"] == 0, "a dump that was never written was counted"
    assert snap.last_dump_path is None
    assert sorted(p.name for p in tmp_path.iterdir()) == ["not_a_dir"]
    metrics = snap.get_metrics()
    assert metrics.get("failures") == 1, f"row1076: a failed dump write left no record: {metrics}"
    assert metrics["last_failure"].startswith("dump write: NotADirectoryError"), metrics["last_failure"]


def test_e3_site_log_event_feeds_the_flight_ring(isolated_blackbox):
    import collections
    import itertools
    from bulk_downloader.runner_telemetry import TelemetryMixin

    class _Runner(TelemetryMixin):
        site_id = "row1076site"
        config = {}

        def __init__(self):
            self._event_seq_counter = itertools.count(1)
            self._event_log = collections.deque(maxlen=10)

    _Runner().log_event("download", "row1076 fetched", url="https://example.invalid/a", extra={"bytes": 3})
    events = isolated_blackbox.ring_buffer.get_recent_events()
    assert [e["topic"] for e in events] == ["site.download"]
    assert events[0]["data"]["site_id"] == "row1076site"
    assert events[0]["data"]["extra"] == {"bytes": 3}


def _scratch_install(tmp_path, monkeypatch):
    """What boot_once needs to run against a scratch install, set here rather than taken
    from tests/conftest.py, so the file runs the same with and without --noconftest: the
    environment, and the process-wide boot latches (schema booted; site runtime bound to
    one sites file), put back after the test as conftest's teardown would clear them --
    else a second boot in another tmp dir is refused ('sites configuration path changed
    after runtime activation')."""
    from bulk_downloader import app as bd_app

    monkeypatch.chdir(tmp_path)
    for name, value in (("BD_HOME", str(tmp_path)), ("BD_INSTALL_DIR", str(tmp_path)),
                        ("BD_DISABLE_KEEPALIVE", "1"), ("BD_TEST_MODE", "1")):
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(bd_app, "_BOOTED_PATHS", set())
    for latch, idle in (("_SITE_RUNTIME_PATH", None), ("_SITE_RUNTIME_READY", False),
                        ("_SITE_RUNTIME_ROLLBACK_PENDING", False)):
        monkeypatch.setattr(bd_app, latch, idle)


def test_e3_boot_once_installs_the_crash_hooks(isolated_blackbox, tmp_path, monkeypatch):
    from bulk_downloader import app as bd_app

    _scratch_install(tmp_path, monkeypatch)
    bd_app.boot_once(force=True)
    assert isolated_blackbox.get_metrics()["excepthook_installed"] is True
    assert sys.excepthook is not sys.__excepthook__


def test_e3_worker_thread_crash_is_captured_and_chained(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(threading, "excepthook", seen.append)
    snap = BlackboxSnapshotter(dump_dir=tmp_path)
    snap.install_excepthook()
    try:
        def row1076_worker():
            raise OSError("row1076-thread")

        t = threading.Thread(target=row1076_worker, name="row1076-worker")
        t.start()
        t.join()
    finally:
        snap.uninstall_excepthook()
    assert threading.excepthook == seen.append, "original threading hook not restored"
    assert len(seen) == 1 and seen[0].exc_type is OSError, "original threading hook not chained"
    dump = _only_dump(snap)
    assert dump["exception"]["type"] == "OSError"
    assert "row1076_worker" in dump["exception"]["traceback"]
    assert dump["context"] == {"thread": "row1076-worker"}


def test_e4_sys_excepthook_tuple_form_keeps_traceback_and_chains(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: seen.append(a))
    snap = BlackboxSnapshotter(dump_dir=tmp_path)
    snap.install_excepthook()
    try:
        def row1076_main_crash():
            raise LookupError("row1076-main")

        try:
            row1076_main_crash()
        except LookupError:
            info = sys.exc_info()
            sys.excepthook(*info)
    finally:
        snap.uninstall_excepthook()
    assert len(seen) == 1 and seen[0][0] is LookupError, "original sys.excepthook not chained"
    dump = _only_dump(snap)
    assert dump["exception"]["type"] == "LookupError"
    assert "row1076_main_crash" in dump["exception"]["traceback"]


def test_e4_recent_events_limit_is_honoured():
    buf = FlightRecorderRingBuffer(capacity=10)
    for i in range(6):
        buf.record("t", i)
    assert [e["data"] for e in buf.get_recent_events(limit=2)] == [4, 5]


def test_e4_default_dump_dir_is_the_app_log_dir(tmp_path, monkeypatch):
    from bulk_downloader import log as bd_log

    assert BlackboxSnapshotter().dump_dir == Path(bd_log._LOG_DIR) / "blackbox"
    # _LOG_DIR is itself 'logs', so the line above cannot tell the app log dir from a
    # hard-coded 'logs/blackbox': move the log dir and the default must follow it.
    monkeypatch.setattr(bd_log, "_LOG_DIR", tmp_path / "applogs")
    assert BlackboxSnapshotter().dump_dir == tmp_path / "applogs" / "blackbox"


def test_r7_no_app_log_dir_is_an_error_not_a_dump_dir_in_the_cwd(monkeypatch):
    """Without the app's log dir to read, the snapshotter says so; it used to fall back,
    silently, to 'logs/blackbox' under whatever directory the process runs in."""
    from bulk_downloader import log as bd_log

    monkeypatch.delattr(bd_log, "_LOG_DIR")
    with pytest.raises(AttributeError, match="_LOG_DIR"):
        BlackboxSnapshotter()


def _crash_storm(snap, n):
    """Drive n distinct 500s through the product error handler (app._on_internal_error)."""
    from flask import Flask
    from bulk_downloader import app as bd_app

    fl = Flask("row1076_storm")
    fl.config["PROPAGATE_EXCEPTIONS"] = False
    fl.register_error_handler(500, bd_app._on_internal_error)

    @fl.route("/api/row1076/storm/<int:i>")
    def row1076_storm_view(i):
        raise RuntimeError(f"storm {i}")

    client = fl.test_client()
    for i in range(n):
        assert client.get(f"/api/row1076/storm/{i}").status_code == 500
    files = sorted(p for p in snap.dump_dir.glob("blackbox_crash_*.json") if p.is_file())
    paths = [json.loads(f.read_text(encoding="utf-8"))["context"]["path"] for f in files]
    return files, paths


def test_e5_crash_storm_keeps_at_most_max_dumps_newest_files(isolated_blackbox):
    """N5-A E1: 25 crashes through the 500 handler leave the newest 20 dumps, not 25,
    and nothing beside them: disk use stays within 20 dumps' worth of bytes."""
    files, paths = _crash_storm(isolated_blackbox, 25)
    on_disk = sorted(isolated_blackbox.dump_dir.iterdir())
    total = sum(p.stat().st_size for p in on_disk)
    biggest = max((p.stat().st_size for p in files), default=0)
    assert len(files) <= 20 and total <= 20 * biggest, (
        f"row1076: {len(files)} blackbox dumps / {total} bytes kept for 25 crashes (unbounded)")
    assert on_disk == files, "row1076: stray entries beside the kept dumps"
    assert paths == [f"/api/row1076/storm/{i}" for i in range(5, 25)], paths
    assert isolated_blackbox.get_metrics()["snapshots_taken"] == 25


def test_e5_control_storm_below_the_bound_keeps_every_dump(isolated_blackbox):
    """Positive control (passes pre-fix too): the probe counts dumps -- 5 crashes, 5 distinct files."""
    files, paths = _crash_storm(isolated_blackbox, 5)
    assert paths == [f"/api/row1076/storm/{i}" for i in range(5)], paths


def _kept_messages(dump_dir):
    return [json.loads(p.read_text(encoding="utf-8"))["exception"]["message"]
            for p in sorted(dump_dir.glob("blackbox_crash_*.json")) if p.is_file()]


def test_e5_bound_is_configurable_and_positive(tmp_path):
    from bulk_downloader.blackbox_snapshotter import DEFAULT_MAX_DUMPS

    assert BlackboxSnapshotter(dump_dir=tmp_path).max_dumps == DEFAULT_MAX_DUMPS == 20
    snap = BlackboxSnapshotter(dump_dir=tmp_path, max_dumps=3)
    for i in range(5):
        snap.snapshot(exc_info=ValueError(f"row1076-cap-{i}"))
    assert _kept_messages(tmp_path) == ["row1076-cap-2", "row1076-cap-3", "row1076-cap-4"]
    with pytest.raises(ValueError):
        BlackboxSnapshotter(dump_dir=tmp_path, max_dumps=0)


class _FaultyOs:
    """Stands in for the snapshotter module's ``os``: the named calls fail with the given
    (errno, message) and are recorded; everything else is the real ``os``."""

    def __init__(self, **faults):
        self.calls = []
        self._faults = faults

    def __getattr__(self, name):
        if name not in self._faults:
            return getattr(os, name)

        def _fail(*args, **kwargs):
            self.calls.append((name, args))
            raise OSError(*self._faults[name])
        return _fail


def test_e5_a_write_that_fails_after_its_temp_file_exists_leaves_nothing_behind(tmp_path, monkeypatch):
    """N5-A E1 / P1-A E2: a dump write that dies once its temp file exists (disk full at
    the rename) is not counted and leaves no partial file. Pruning only sees finished
    dumps, so a left-behind temp file would stay forever -- one per failed crash dump."""
    from bulk_downloader import blackbox_snapshotter as mod

    fake_os = _FaultyOs(replace=(errno.ENOSPC, "No space left on device"))
    monkeypatch.setattr(mod, "os", fake_os)
    snap = BlackboxSnapshotter(dump_dir=tmp_path)
    for i in range(3):
        snap.snapshot(exc_info=ValueError(f"row1076-enospc-{i}"))
    assert [(name, Path(args[0]).name.endswith(".json.tmp")) for name, args in fake_os.calls] == [
        ("replace", True)] * 3, fake_os.calls
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == [], f"row1076: {len(left)} failed dump write(s) left partial temp files behind: {left}"
    assert snap.get_metrics()["snapshots_taken"] == 0
    assert snap.last_dump_path is None
    metrics = snap.get_metrics()
    assert metrics.get("failures") == 3, f"row1076: failed dump writes left no record: {metrics}"
    assert metrics["last_failure"] == "dump write: OSError: [Errno 28] No space left on device"


def test_e5_a_temp_file_that_cannot_be_removed_does_not_escape_the_snapshot(tmp_path, monkeypatch):
    """Fail-open: when the cleanup fails too (the filesystem went read-only after an I/O
    error), snapshot() still returns the in-memory payload instead of raising."""
    from bulk_downloader import blackbox_snapshotter as mod

    fake_os = _FaultyOs(replace=(errno.EIO, "Input/output error"), unlink=(errno.EROFS, "Read-only file system"))
    monkeypatch.setattr(mod, "os", fake_os)
    snap = BlackboxSnapshotter(dump_dir=tmp_path)
    try:
        payload = snap.snapshot(exc_info=ValueError("row1076-erofs"))
    except OSError as exc:
        payload = exc
    assert isinstance(payload, dict), f"row1076: a failed temp-file cleanup escaped the snapshot: {payload!r}"
    assert payload["exception"]["message"] == "row1076-erofs"
    assert [name for name, _ in fake_os.calls] == ["replace", "unlink"]
    assert snap.get_metrics()["snapshots_taken"] == 0
    metrics = snap.get_metrics()
    assert metrics.get("failures") == 2, f"row1076: the failed write and its failed cleanup left no record: {metrics}"
    assert metrics["last_failure"] == "temp-file cleanup: OSError: [Errno 30] Read-only file system"


def test_e5_two_crashes_in_the_same_millisecond_keep_two_dumps(tmp_path, monkeypatch):
    """The dump name carries a sequence number: two crashes in one millisecond
    on one thread are two dumps, oldest first, not one overwriting the other."""
    import types
    from bulk_downloader import blackbox_snapshotter as mod

    monkeypatch.setattr(mod, "time", types.SimpleNamespace(time=lambda: 1790000000.123))
    snap = BlackboxSnapshotter(dump_dir=tmp_path)
    snap.snapshot(exc_info=ValueError("row1076-same-ms-a"))
    snap.snapshot(exc_info=ValueError("row1076-same-ms-b"))
    assert _kept_messages(tmp_path) == ["row1076-same-ms-a", "row1076-same-ms-b"]


def test_e5_prune_skips_an_entry_it_cannot_delete_and_keeps_the_newest(tmp_path, monkeypatch):
    """An old entry that cannot be unlinked (here a directory) is skipped and recorded:
    the rest are still pruned and every crash counts."""
    from bulk_downloader import blackbox_snapshotter as mod

    snap = BlackboxSnapshotter(dump_dir=tmp_path / "blackbox", max_dumps=2)
    monkeypatch.setattr(mod, "_GLOBAL_SNAPSHOTTER", snap)
    stuck = snap.dump_dir / "blackbox_crash_0000000000000_000000_0_0.json"
    stuck.mkdir(parents=True)
    _, paths = _crash_storm(snap, 4)
    assert paths == ["/api/row1076/storm/2", "/api/row1076/storm/3"], paths
    assert stuck.is_dir()
    assert snap.get_metrics()["snapshots_taken"] == 4
    metrics = snap.get_metrics()
    # crashes 2, 3 and 4 each found one dump too many, oldest first: the stuck entry
    assert metrics.get("failures") == 3, f"row1076: refused prunes left no record: {metrics}"
    assert metrics["last_failure"].startswith(f"prune {stuck.name}: IsADirectoryError"), metrics["last_failure"]


def test_e5_module_imports_and_dumps_without_the_posix_resource_module(tmp_path, monkeypatch):
    """N5-A note: 'resource' is POSIX-only. Where it cannot be imported (Windows) the module
    still imports and still dumps, only without rusage."""
    import importlib.util
    from bulk_downloader import blackbox_snapshotter as mod

    # control: with 'resource' (POSIX, where this suite runs) the rusage is recorded
    control = mod.BlackboxSnapshotter(dump_dir=tmp_path).snapshot(dump_to_disk=False)["process"]
    assert "max_rss_kb" in control and "rusage_unavailable" not in control
    monkeypatch.setitem(sys.modules, "resource", None)  # 'import resource' -> ImportError
    # a fresh copy of the module, loaded as a member of its package (its imports are relative)
    spec = importlib.util.spec_from_file_location("bulk_downloader._row1076_blackbox_without_resource",
                                                  mod.__file__)
    probe = importlib.util.module_from_spec(spec)
    import_error = None
    try:
        spec.loader.exec_module(probe)
    except ImportError as exc:
        import_error = exc
    assert import_error is None, f"row1076: blackbox_snapshotter needs POSIX 'resource' to import: {import_error}"
    assert probe.resource is None
    payload = probe.BlackboxSnapshotter(dump_dir=tmp_path).snapshot(exc_info=ValueError("row1076-nores"))
    assert "max_rss_kb" not in payload["process"] and payload["process"]["pid"] == os.getpid()
    assert payload["process"].get("rusage_unavailable") == "no 'resource' module on this platform"
    assert payload["exception"]["message"] == "row1076-nores"
    assert len(list(tmp_path.glob("blackbox_crash_*.json"))) == 1


# --- r1 repair: R1-R5 (verifier minors) and R7 (DP-13: crash-path failures on the record) ---

def _telemetry_runner():
    """A minimal TelemetryMixin host: log_event is the product feed of the flight ring."""
    import collections
    import itertools
    from bulk_downloader.runner_telemetry import TelemetryMixin

    class _Runner(TelemetryMixin):
        site_id = "row1076site"
        config = {}

        def __init__(self):
            self._event_seq_counter = itertools.count(1)
            self._event_log = collections.deque(maxlen=10)

    return _Runner()


# Signed / credentialed URLs of the kinds a download log carries (placeholder values).
_R1_URLS = {
    "amz": ("https://bucket.s3.example.com/vid/a.mp4?X-Amz-Credential=row1076cred"
            "&X-Amz-Expires=3600&X-Amz-Signature=row1076amzsig"),
    "token": "https://cdn.example.com/media/v.mp4?token=row1076tokenvalue&quality=720",
    "sig": "https://cdn.example.com/media/w.mp4?sig=row1076sigvalue&id=5",
    "cloudfront": ("https://d1.cloudfront.example/x.m3u8?Expires=1790000000"
                   "&Signature=row1076cfsignature&Key-Pair-Id=row1076keypair"),
    "userinfo": "https://row1076user:row1076pass@files.example.com/private/f.zip",
}
_R1_SECRETS = ("row1076cred", "row1076amzsig", "row1076tokenvalue", "row1076sigvalue",
               "row1076cfsignature", "row1076keypair", "row1076user", "row1076pass")


def test_r1_a_dump_on_disk_carries_no_signed_url_secret(isolated_blackbox):
    """R1 (m4): log_event feeds the full url and extra into the flight ring, and a crash dump
    is durable, so a signed-URL token reached logs/blackbox/*.json. The write goes through
    the artifact redaction layer: no secret on disk -- flight events, exception message and
    traceback, context -- while host, path and counts stay; the snapshotter's own process
    section is kept whole."""
    import platform

    u = _R1_URLS
    _telemetry_runner().log_event("download", "fetched " + u["token"], url=u["amz"],
                                  extra={"src": u["sig"], "bytes": 3})

    def row1076_signed_fetch():
        raise RuntimeError("download failed for " + u["cloudfront"])

    try:
        row1076_signed_fetch()
    except RuntimeError as exc:
        payload = isolated_blackbox.snapshot(exc_info=exc, context={"source": u["userinfo"]})
    raw = isolated_blackbox.last_dump_path.read_text(encoding="utf-8")
    leaked = [secret for secret in _R1_SECRETS if secret in raw]
    assert leaked == [], f"row1076: signed-URL secrets written to the blackbox dump: {leaked}"
    dump = json.loads(raw)
    event = dump["flight_events"][0]["data"]
    assert event["url"].startswith("https://bucket.s3.example.com/vid/a.mp4?"), event["url"]
    assert event["extra"]["src"].startswith("https://cdn.example.com/media/w.mp4?"), event["extra"]
    assert event["extra"]["bytes"] == 3 and event["site_id"] == "row1076site"
    assert "https://cdn.example.com/media/v.mp4?" in event["message"]
    assert "https://d1.cloudfront.example/x.m3u8?" in dump["exception"]["message"]
    assert "row1076_signed_fetch" in dump["exception"]["traceback"]
    assert dump["context"] == {"source": "https://files.example.com/private/f.zip"}
    assert dump["process"]["platform"] == platform.platform()
    # redaction is at the durable boundary: the in-memory payload is returned as captured
    assert payload["context"] == {"source": u["userinfo"]}


def _r1_http_get(url):
    raise ValueError("row1076 fetch failed: " + url)


def _r1_session_fetch(url):
    status = _r1_http_get(url)
    return status


def _r1_fetch(base, query):
    return _r1_session_fetch(f"{base}?{query}")


def test_r1_b_a_redacted_traceback_keeps_every_frame(isolated_blackbox):
    """R1: redaction must not cost the dump its diagnosis. The redactor reads the string it is
    given as one value, so over a whole traceback the '?' quoted from one frame's source line
    opened a 'query' that ran on into the next frames: the chunk naming _r1_session_fetch read
    as a secret key and everything after its '=' -- the frame that raised, the exception line --
    became <scrubbed>. Redacted line by line, every line stays; only the signature goes."""
    try:
        _r1_fetch("https://cdn.example.com/row1076/f.bin", "X-Amz-Signature=row1076amzsig")
    except ValueError as exc:
        payload = isolated_blackbox.snapshot(exc_info=exc)
    raw = isolated_blackbox.last_dump_path.read_text(encoding="utf-8")
    assert "row1076amzsig" not in raw, "row1076: the signature reached the blackbox dump"
    written = json.loads(raw)["exception"]["traceback"].splitlines()
    captured = payload["exception"]["traceback"].splitlines()
    assert written == [line.replace("row1076amzsig", "<scrubbed>") for line in captured], (
        "row1076: redaction cut the traceback:\n" + "\n".join(written))
    assert written[-1] == ("ValueError: row1076 fetch failed: "
                           "https://cdn.example.com/row1076/f.bin?X-Amz-Signature=<scrubbed>")


def test_r1_c_a_set_or_bytes_value_keeps_every_item_and_line(tmp_path):
    """R1: a set and a bytes body are redacted item by item and line by line as well. Written
    as one repr line (json.dumps(default=str)), a set of URLs lost every URL after the first
    secret and a multi-line body every line after it; the secrets stay out either way."""
    snap = BlackboxSnapshotter(dump_dir=tmp_path)
    urls = {"https://a.example/row1076/x?page=1", "https://b.example/row1076/y?sig=row1076setsig"}
    body = (b"GET /row1076/f.bin\n"
            b"https://cdn.example/f.bin?page=1&X-Amz-Signature=row1076bytesig\n"
            b"\xffrow1076 tail")
    snap.snapshot(context={"urls": urls, "body": body})
    raw = snap.last_dump_path.read_text(encoding="utf-8")
    assert "row1076setsig" not in raw and "row1076bytesig" not in raw, "row1076: a secret reached the dump"
    context = json.loads(raw)["context"]
    assert sorted(context["urls"]) == ["https://a.example/row1076/x?page=1",
                                       "https://b.example/row1076/y?sig=<scrubbed>"], (
        f"row1076: the set was not written item by item: {context['urls']!r}")
    assert context["body"].split("\n") == [
        "GET /row1076/f.bin",
        "https://cdn.example/f.bin?page=1&X-Amz-Signature=<scrubbed>",
        "\\xffrow1076 tail"], f"row1076: the bytes body lost lines: {context['body']!r}"


def test_g4_a_flight_event_keyed_by_a_tuple_or_a_url_is_dumped_with_its_keys_redacted(tmp_path):
    """G4: dict keys were written as they came. json.dumps refuses a tuple key, so one such
    event failed every dump while it was in the ring; a signed URL used as a key reached
    the disk whole. A key other than a number or None is written as its redacted text."""
    snap = BlackboxSnapshotter(dump_dir=tmp_path)
    url = "https://cdn.example.com/row1076/v.mp4?X-Amz-Signature=row1076keysig&page=2"
    snap.record_event("site.row1076-keys", {("row1076", 1): "tuple", 7: "int", None: "none", url: 200})
    for i in range(2):
        snap.snapshot(exc_info=ValueError(f"row1076-g4-keys-{i}"))
    files = sorted(tmp_path.glob("blackbox_crash_*.json"))
    metrics = snap.get_metrics()
    assert len(files) == 2, f"row1076: {len(files)} dumps for 2 crashes: {metrics['last_failure']}"
    assert (metrics["snapshots_taken"], metrics["failures"]) == (2, 0), metrics
    raw = files[-1].read_text(encoding="utf-8")
    assert "row1076keysig" not in raw, "row1076: a signed-URL secret in a key reached the dump"
    assert json.loads(raw)["flight_events"][0]["data"] == {
        "('row1076', 1)": "tuple", "7": "int", "null": "none",
        "https://cdn.example.com/row1076/v.mp4?X-Amz-Signature=<scrubbed>&page=2": 200}


def test_g4_a_flight_event_that_holds_itself_or_nests_too_deep_is_dumped_with_a_marker(tmp_path):
    """G4: the redaction walked every container with no guard: an event that holds itself,
    or one nested thousands deep, drove it past the recursion limit, so every dump failed
    while the event was in the ring. The cycle and the level past the limit are written
    as markers, and every crash is dumped."""
    from bulk_downloader import blackbox_snapshotter as mod

    looped = {"row1076": "loop"}
    looped["self"] = looped
    deep = []
    inner = deep
    for _ in range(3000):
        inner.append([])
        inner = inner[0]
    snap = BlackboxSnapshotter(dump_dir=tmp_path)
    snap.record_event("site.row1076-cycle", looped)
    snap.record_event("site.row1076-deep", deep)
    for i in range(2):
        snap.snapshot(exc_info=ValueError(f"row1076-g4-nest-{i}"))
    files = sorted(tmp_path.glob("blackbox_crash_*.json"))
    metrics = snap.get_metrics()
    assert len(files) == 2, f"row1076: {len(files)} dumps for 2 crashes: {metrics['last_failure']}"
    assert (metrics["snapshots_taken"], metrics["failures"]) == (2, 0), metrics
    cycle_event, deep_event = json.loads(files[-1].read_text(encoding="utf-8"))["flight_events"]
    assert cycle_event["data"] == {"row1076": "loop", "self": "<cycle>"}, cycle_event["data"]
    node, levels = deep_event["data"], 0
    while isinstance(node, list):
        node, levels = node[0], levels + 1
    # the walk starts at the dump's flight_events list: 2 levels (the list, the event) sit above data
    assert (node, levels) == ("<nested deeper than 100 levels>", mod._MAX_VALUE_DEPTH - 2), (node, levels)


class _BrokenStr:
    def __str__(self):
        raise RuntimeError("row1076 broken __str__")


class _NonStrStr:
    def __str__(self):
        return 1076


def test_g4_a_flight_event_value_whose_str_fails_is_dumped_as_that_failure(tmp_path):
    """G4 (same class): a value is written as its str(); one whose __str__ raises, or returns
    no str, failed every dump while it was in the ring. It is written as the failure."""
    snap = BlackboxSnapshotter(dump_dir=tmp_path)
    snap.record_event("site.row1076-str", {"raises": _BrokenStr(), "not_str": _NonStrStr()})
    for i in range(2):
        snap.snapshot(exc_info=ValueError(f"row1076-g4-str-{i}"))
    files = sorted(tmp_path.glob("blackbox_crash_*.json"))
    metrics = snap.get_metrics()
    assert len(files) == 2, f"row1076: {len(files)} dumps for 2 crashes: {metrics['last_failure']}"
    assert (metrics["snapshots_taken"], metrics["failures"]) == (2, 0), metrics
    assert json.loads(files[-1].read_text(encoding="utf-8"))["flight_events"][0]["data"] == {
        "raises": "<unprintable _BrokenStr: str() raised RuntimeError>",
        "not_str": "<unprintable _NonStrStr: str() raised TypeError>"}


def test_r2_a_worker_thread_that_calls_sys_exit_is_no_crash(tmp_path, monkeypatch):
    """R2 (m2): threading's default hook ignores SystemExit silently -- a worker that calls
    sys.exit() just ends. So it leaves no dump (under the cap such dumps would push real ones
    out), the previous hook is still chained, and a real crash on the next thread still dumps."""
    seen = []
    monkeypatch.setattr(threading, "excepthook", seen.append)
    snap = BlackboxSnapshotter(dump_dir=tmp_path)
    snap.install_excepthook()

    class _Finished(SystemExit):
        pass

    def row1076_exit_subclass():
        raise _Finished(2)

    def row1076_real_crash():
        raise OSError("row1076-r2-real")

    try:
        for target in (sys.exit, row1076_exit_subclass, row1076_real_crash):
            t = threading.Thread(target=target, name="row1076-r2")
            t.start()
            t.join()
    finally:
        snap.uninstall_excepthook()
    assert [args.exc_type for args in seen] == [SystemExit, _Finished, OSError], "previous hook not chained"
    dumped = [json.loads(p.read_text(encoding="utf-8"))["exception"]["type"]
              for p in sorted(tmp_path.glob("blackbox_crash_*.json"))]
    assert dumped == ["OSError"], f"row1076: thread exits dumped as crashes: {dumped}"
    assert snap.get_metrics()["snapshots_taken"] == 1


def test_r3_after_the_clock_steps_back_the_dump_just_written_is_kept(tmp_path, monkeypatch):
    """R3 (m3): the dump name starts with the wall-clock ms. After the clock steps back (NTP,
    VM restore) the newest crash's name sorts first: it must not be the dump pruned,
    last_dump_path must exist, and the count bound still holds."""
    import types
    from bulk_downloader import blackbox_snapshotter as mod

    clock = [1790000000.0 + 3600]
    monkeypatch.setattr(mod, "time", types.SimpleNamespace(time=lambda: clock[0]))
    snap = BlackboxSnapshotter(dump_dir=tmp_path, max_dumps=3)
    for i in range(3):
        snap.snapshot(exc_info=ValueError(f"row1076-future-{i}"))
    clock[0] -= 3600  # the wall clock steps back an hour
    snap.snapshot(exc_info=ValueError("row1076-after-step-back"))
    assert snap.last_dump_path.exists(), "row1076: the dump just written was pruned (clock stepped back)"
    assert _kept_messages(tmp_path) == ["row1076-after-step-back", "row1076-future-1", "row1076-future-2"]
    assert snap.get_metrics()["snapshots_taken"] == 4


def test_g3_after_the_clock_steps_back_the_newest_written_dumps_are_kept(tmp_path, monkeypatch):
    """G3: dumps are pruned in write order (the sequence number in the name), not by the
    wall-clock ms the name starts with. Pruned by name, every crash after a step back
    deleted the previous post-step dump and the stale future-dated ones stayed. A restarted
    process (a new snapshotter, its own count at 0) numbers on from the dumps it finds; a
    stray file without a sequence number counts as the oldest."""
    import types
    from bulk_downloader import blackbox_snapshotter as mod

    clock = [1790000000.0 + 3600]
    monkeypatch.setattr(mod, "time", types.SimpleNamespace(time=lambda: clock[0]))
    snap = BlackboxSnapshotter(dump_dir=tmp_path, max_dumps=3)
    for i in range(3):
        snap.snapshot(exc_info=ValueError(f"row1076-future-{i}"))
    clock[0] -= 3600  # the wall clock steps back an hour
    for i in range(3):
        snap.snapshot(exc_info=ValueError(f"row1076-after-{i}"))
        clock[0] += 1
    kept = sorted(_kept_messages(tmp_path))
    assert kept == ["row1076-after-0", "row1076-after-1", "row1076-after-2"], (
        f"row1076: stale future-dated dumps kept over newer crashes: {kept}")
    (tmp_path / "blackbox_crash_copy.json").write_text(json.dumps({"exception": {"message": "row1076-stray"}}))
    clock[0] -= 7200  # back again, across a restart
    restarted = BlackboxSnapshotter(dump_dir=tmp_path, max_dumps=3)
    for i in range(2):
        restarted.snapshot(exc_info=ValueError(f"row1076-restarted-{i}"))
        clock[0] += 1
    kept = sorted(_kept_messages(tmp_path))
    assert kept == ["row1076-after-2", "row1076-restarted-0", "row1076-restarted-1"], (
        f"row1076: a restarted process pruned its own newer dumps first: {kept}")
    assert (snap.get_metrics()["snapshots_taken"], restarted.get_metrics()["snapshots_taken"]) == (6, 2)
    assert snap.get_metrics()["failures"] == restarted.get_metrics()["failures"] == 0


def test_g3_two_crashes_dumping_at_once_keep_at_most_max_dumps(tmp_path, monkeypatch):
    """G3: a dump's prune pass counts the dumps listed before it is written. Two crashes
    dumping at once must not both count the same listing, or each keeps one too many:
    one dump at a time keeps the bound exact. Each listing waits (up to 1 s) for the
    other crash's listing; when dumps are serialized the second never comes."""
    snap = BlackboxSnapshotter(dump_dir=tmp_path, max_dumps=2)
    snap.snapshot(exc_info=ValueError("row1076-first"))
    both_listed = threading.Barrier(2, timeout=1.0)
    list_dumps = snap._list_dumps

    def list_then_wait_for_the_other_crash():
        dumps = list_dumps()
        try:
            both_listed.wait()
        except threading.BrokenBarrierError:
            pass
        return dumps

    monkeypatch.setattr(snap, "_list_dumps", list_then_wait_for_the_other_crash)
    crashes = [threading.Thread(target=snap.snapshot, kwargs={"exc_info": ValueError(f"row1076-at-once-{i}")})
               for i in range(2)]
    for t in crashes:
        t.start()
    for t in crashes:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in crashes), "row1076: a crash dump never finished"
    kept = sorted(_kept_messages(tmp_path))
    assert len(kept) <= 2, f"row1076: two crashes at once left {len(kept)} dumps, max_dumps is 2: {kept}"
    assert kept == ["row1076-at-once-0", "row1076-at-once-1"], kept
    assert snap.get_metrics()["snapshots_taken"] == 3
    assert snap.get_metrics()["failures"] == 0


def test_r4_the_wrapped_cause_is_named_outside_an_except_block(tmp_path):
    """R4 (m1): an InternalServerError carrying original_exception, snapshotted where no
    exception is in flight (sys.exc_info() empty), names the wrapped cause. Only the
    original_exception branch of _crash_cause can supply it here: the sys.exc_info()
    fallback has nothing."""
    from werkzeug.exceptions import InternalServerError

    assert sys.exc_info() == (None, None, None)
    snap = BlackboxSnapshotter(dump_dir=tmp_path)
    snap.snapshot(exc_info=InternalServerError(original_exception=ZeroDivisionError("row1076-r4-cause")))
    dump = _only_dump(snap)
    assert dump["exception"]["type"] == "ZeroDivisionError", dump["exception"]["type"]
    assert dump["exception"]["message"] == "row1076-r4-cause"


def test_r5_every_test_here_runs_from_tmp_path_with_the_heartbeat_off(tmp_path):
    """R5 (m6): with or without tests/conftest.py, this file's tests run from their own tmp_path with
    the app's disk heartbeat off, so the app's relative logs/, state/ and live_recordings/ never land
    in the checkout. Under --noconftest only the autouse fixture above provides this."""
    assert Path.cwd().samefile(tmp_path), f"row1076: the tests run from {Path.cwd()}, so the app writes its files there"
    assert os.environ.get("BD_DISABLE_KEEPALIVE") == "1", "row1076: the app's disk heartbeat is on during the tests"


class _Unprintable(Exception):
    """A real exception whose str() raises: snapshot() cannot build its message."""

    def __str__(self):
        raise RuntimeError("row1076-unprintable")


def test_r7_a_snapshot_that_fails_in_the_excepthooks_is_recorded_and_chained(tmp_path, monkeypatch):
    """R7: the hooks must never raise into the crashing program, so a snapshot that fails
    there is recorded (get_metrics failures / last_failure), not dropped silently, and the
    previous hooks are still chained."""
    seen_sys, seen_thread = [], []
    monkeypatch.setattr(sys, "excepthook", lambda *a: seen_sys.append(a))
    monkeypatch.setattr(threading, "excepthook", seen_thread.append)
    snap = BlackboxSnapshotter(dump_dir=tmp_path)
    snap.install_excepthook()
    try:
        try:
            raise _Unprintable()
        except _Unprintable:
            sys.excepthook(*sys.exc_info())

        def row1076_unprintable_worker():
            raise _Unprintable()

        t = threading.Thread(target=row1076_unprintable_worker)
        t.start()
        t.join()
    finally:
        snap.uninstall_excepthook()
    assert [a[0] for a in seen_sys] == [_Unprintable], "original sys.excepthook not chained"
    assert [a.exc_type for a in seen_thread] == [_Unprintable], "original threading hook not chained"
    metrics = snap.get_metrics()
    assert metrics.get("failures") == 2, f"row1076: failed crash snapshots left no record: {metrics}"
    assert metrics["last_failure"] == "snapshot: RuntimeError: row1076-unprintable"
    assert metrics["snapshots_taken"] == 0 and list(tmp_path.iterdir()) == []


def test_r7_a_capture_that_fails_in_the_500_handler_is_recorded_and_the_client_gets_json(isolated_blackbox):
    """R7: the 500 handler still answers when the crash capture fails, and the failure is
    recorded in the snapshotter's metrics instead of being swallowed."""
    from flask import Flask
    from bulk_downloader import app as bd_app

    fl = Flask("row1076_r7")
    fl.config["PROPAGATE_EXCEPTIONS"] = False
    fl.register_error_handler(500, bd_app._on_internal_error)

    @fl.route("/api/row1076/unprintable")
    def row1076_unprintable_view():
        raise _Unprintable()

    try:
        resp = fl.test_client().get("/api/row1076/unprintable")
    except Exception as escaped:  # the capture runs in the 500 handler and must never raise there
        pytest.fail(f"row1076: the failed crash capture escaped the 500 handler: {escaped!r}")
    assert resp.status_code == 500
    assert resp.get_json() == {"ok": False, "error": "internal server error"}
    metrics = isolated_blackbox.get_metrics()
    assert metrics.get("failures") == 1, f"row1076: a failed crash capture left no record: {metrics}"
    assert metrics["last_failure"] == "snapshot: RuntimeError: row1076-unprintable"
    assert metrics["snapshots_taken"] == 0


def test_r7_a_temp_file_that_was_never_created_is_no_cleanup_failure(tmp_path, monkeypatch):
    """A write refused at open() made no temp file: one failure, the write's own -- not a
    second, false 'temp-file cleanup' failure for a file that never existed."""
    from bulk_downloader import blackbox_snapshotter as mod

    def _refuse(path, *args, **kwargs):
        raise PermissionError(errno.EACCES, "Permission denied", str(path))

    monkeypatch.setattr(mod, "open", _refuse, raising=False)
    snap = BlackboxSnapshotter(dump_dir=tmp_path)
    snap.snapshot(dump_to_disk=True)
    metrics = snap.get_metrics()
    assert metrics.get("failures") == 1, f"row1076: {metrics.get('failures')} failures recorded for one refused write"
    assert metrics["last_failure"].startswith("dump write: PermissionError: [Errno 13]"), metrics["last_failure"]
    assert list(tmp_path.iterdir()) == [] and metrics["snapshots_taken"] == 0


def test_r7_a_getrusage_failure_is_named_in_the_dump(tmp_path, monkeypatch):
    """R7: a failing getrusage no longer vanishes: the dump is still written, without rusage,
    and its process section says why."""
    import types
    from bulk_downloader import blackbox_snapshotter as mod

    def _getrusage(who):
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(mod, "resource", types.SimpleNamespace(RUSAGE_SELF=0, getrusage=_getrusage))
    snap = BlackboxSnapshotter(dump_dir=tmp_path)
    snap.snapshot(exc_info=ValueError("row1076-rusage"))
    process = _only_dump(snap)["process"]
    assert process.get("rusage_unavailable") == "getrusage failed: [Errno 1] Operation not permitted", process
    assert "max_rss_kb" not in process


def test_r7_a_dump_dir_that_cannot_be_listed_for_pruning_is_recorded(tmp_path):
    """R7: the dump is written and counted; the prune pass that cannot list the directory
    (os.listdir's PermissionError -- pathlib's glob would return [] and pruning would stop
    silently) is recorded instead of only logged."""
    class _UnlistableDir(type(tmp_path)):
        def iterdir(self):
            raise PermissionError(errno.EACCES, "Permission denied")

    snap = BlackboxSnapshotter(dump_dir=tmp_path)
    snap.dump_dir = _UnlistableDir(tmp_path)
    snap.snapshot(exc_info=ValueError("row1076-unlistable"))
    metrics = snap.get_metrics()
    assert metrics["snapshots_taken"] == 1 and snap.last_dump_path.exists()
    assert metrics.get("failures") == 1, f"row1076: a failed prune listing left no record: {metrics}"
    assert metrics["last_failure"] == "prune listing: PermissionError: [Errno 13] Permission denied"


def test_r7_a_dump_another_process_already_pruned_is_no_failure(tmp_path):
    """Two processes can share the dump directory: a dump the other one deleted between our
    listing and our unlink is gone as intended, so it is neither an error nor a failure."""
    ghost = "blackbox_crash_0000000000000_000000_1_1.json"

    class _RacyDir(type(tmp_path)):
        def iterdir(self):
            yield from super().iterdir()
            yield self / ghost  # listed, but already deleted

    snap = BlackboxSnapshotter(dump_dir=tmp_path, max_dumps=1)
    snap.dump_dir = _RacyDir(tmp_path)
    snap.snapshot(exc_info=ValueError("row1076-racy"))
    metrics = snap.get_metrics()
    assert metrics.get("failures") == 0, metrics.get("last_failure")
    assert _kept_messages(tmp_path) == ["row1076-racy"]


def test_r7_log_event_does_not_hide_a_broken_flight_feed(isolated_blackbox, monkeypatch):
    """R7: feeding the ring is an in-memory append that cannot fail, so log_event no longer
    wraps it in a silent except. Were the feed ever broken, it shows at the caller instead of
    leaving a recorder without a flight."""
    from bulk_downloader import blackbox_snapshotter as mod

    def _broken_feed(topic, data, level="INFO"):
        raise RuntimeError("row1076-feed-broke")

    monkeypatch.setattr(mod, "record_flight_event", _broken_feed)
    with pytest.raises(RuntimeError, match="row1076-feed-broke"):
        _telemetry_runner().log_event("download", "row1076 fetched")


def test_r7_boot_once_does_not_hide_a_failed_crash_hook_install(isolated_blackbox, tmp_path, monkeypatch):
    """R7: installing the hooks only swaps two hook functions, so boot_once no longer wraps it
    in a silent except: a failed install fails the boot (which boot_once's latch retries)
    instead of leaving the process without crash capture, unnoticed."""
    from bulk_downloader import app as bd_app

    def _broken_install():
        raise RuntimeError("row1076-install-broke")

    _scratch_install(tmp_path, monkeypatch)
    monkeypatch.setattr(isolated_blackbox, "install_excepthook", _broken_install)
    with pytest.raises(RuntimeError, match="row1076-install-broke"):
        bd_app.boot_once(force=True)


def test_e3_installing_the_crash_hooks_twice_chains_each_crash_once(tmp_path, monkeypatch):
    """boot_once(force=True) installs again: a second install must not make the hook chain
    to itself (endless recursion on the next crash) nor leave it behind after uninstall."""
    seen = []

    def previous(exc_type, exc_value, exc_tb):
        seen.append(exc_type)

    monkeypatch.setattr(sys, "excepthook", previous)
    snap = BlackboxSnapshotter(dump_dir=tmp_path)
    snap.install_excepthook()
    snap.install_excepthook()
    try:
        try:
            raise OSError("row1076-twice")
        except OSError as exc:
            try:
                sys.excepthook(OSError, exc, exc.__traceback__)
            except Exception as escaped:  # a crash hook must never raise into the crashing program
                pytest.fail(f"row1076: the twice-installed crash hook raised: {escaped!r}")
    finally:
        snap.uninstall_excepthook()
    assert seen == [OSError]
    assert snap.get_metrics()["snapshots_taken"] == 1, "row1076: one crash, more than one dump"
    assert sys.excepthook is previous, "row1076: uninstall left a blackbox hook installed"


def test_e3_a_thread_hook_call_without_a_thread_is_still_dumped(tmp_path, monkeypatch):
    """threading.excepthook's args.thread 'can be None' (threading docs)."""
    seen = []
    monkeypatch.setattr(threading, "excepthook", lambda args: seen.append(args.thread))
    snap = BlackboxSnapshotter(dump_dir=tmp_path)
    snap.install_excepthook()
    try:
        try:
            raise OSError("row1076-no-thread")
        except OSError as exc:
            try:
                threading.excepthook(threading.ExceptHookArgs([OSError, exc, exc.__traceback__, None]))
            except Exception as escaped:  # a crash hook must never raise into the crashing thread
                pytest.fail(f"row1076: the thread hook raised on a call without a thread: {escaped!r}")
    finally:
        snap.uninstall_excepthook()
    assert seen == [None]
    dump = _only_dump(snap)
    assert dump["context"] == {"thread": ""} and dump["exception"]["message"] == "row1076-no-thread"


def test_e4_a_snapshot_of_what_is_not_an_exception_still_names_it(tmp_path):
    snap = BlackboxSnapshotter(dump_dir=tmp_path)
    odd = snap.snapshot(exc_info="row1076-not-an-exception", dump_to_disk=False)["exception"]
    assert odd == {"type": "UnknownException", "message": "row1076-not-an-exception",
                   "traceback": "row1076-not-an-exception"}
    # sys.exc_info() taken outside an except block
    empty = snap.snapshot(exc_info=(None, None, None), dump_to_disk=False)["exception"]
    assert (empty["type"], empty["message"]) == ("None", "None")


def test_e4_the_process_snapshotter_is_made_once_under_the_app_log_dir(tmp_path, monkeypatch):
    from bulk_downloader import blackbox_snapshotter as mod
    from bulk_downloader import log as bd_log

    monkeypatch.setattr(mod, "_GLOBAL_SNAPSHOTTER", None)
    monkeypatch.setattr(bd_log, "_LOG_DIR", tmp_path / "applogs")
    first = get_blackbox_snapshotter()
    assert get_blackbox_snapshotter() is first
    assert first.dump_dir == tmp_path / "applogs" / "blackbox"
    assert first.get_metrics()["excepthook_installed"] is False
