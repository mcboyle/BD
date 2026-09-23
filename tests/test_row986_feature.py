"""Row 986: Hierarchical Multi-Stream Progress Telemetry Renderer.

Validates multi-stream hierarchical progress tracking, recursive metric
aggregation (bytes, throughput, ETA, status rollup), telemetry snapshotting/deltas,
ANSI and plain-text tree formatting with box drawing, width capping, and
thread-safe concurrent updates.

Refutes N6-A / P4-B (tree ac6a5284) are pinned below: the product feeds and
serves the tree (SiteRunner._http_download's progress tick and exit drive one
stream per file under the site root; get_status and /api/status serve it),
bookkeeping is bounded by streams rather than events, parent cycles are
refused and every walk terminates, and the delta / composite-FAILED / ETA /
clamp / visible-width behaviours are pinned.

RED on baseline: bulk_downloader.runner_progress_telemetry does not exist.
The module is named runner_* so bd-coupling-meter files it in the 'runner'
subsystem with its only product importer, runner_telemetry (RULING-0096).
"""
from __future__ import annotations

import json
import re
import threading
import unicodedata
from pathlib import Path

import pytest

try:
    from bulk_downloader.runner_progress_telemetry import (
        HierarchicalProgressTracker,
        ProgressSnapshot,
        ProgressTelemetryRenderer,
        StreamNode,
        StreamStatus,
        create_progress_tracker,
        render_progress_hierarchy,
    )
except (ImportError, ModuleNotFoundError):
    HierarchicalProgressTracker = None
    ProgressSnapshot = None
    ProgressTelemetryRenderer = None
    StreamNode = None
    StreamStatus = None
    create_progress_tracker = None
    render_progress_hierarchy = None

BD_GATE_SCOPE = "module"


def test_module_exports():
    """RED assertion 1: bulk_downloader.runner_progress_telemetry must be importable and export components."""
    assert HierarchicalProgressTracker is not None, "HierarchicalProgressTracker capability must be present (bulk_downloader.runner_progress_telemetry)"
    assert ProgressSnapshot is not None, "ProgressSnapshot capability must be present"
    assert ProgressTelemetryRenderer is not None, "ProgressTelemetryRenderer capability must be present"
    assert StreamNode is not None, "StreamNode capability must be present"
    assert StreamStatus is not None, "StreamStatus capability must be present"
    assert create_progress_tracker is not None, "create_progress_tracker capability must be present"
    assert render_progress_hierarchy is not None, "render_progress_hierarchy capability must be present"


def test_stream_node_lifecycle_and_metrics():
    """Verify stream node lifecycle states, byte progress calculations, and speed/ETA metrics."""
    node = StreamNode(stream_id="stream-1", label="Segment 1", total_bytes=1000)
    assert node.status == StreamStatus.IDLE
    assert node.percent == 0.0

    node.start()
    assert node.status == StreamStatus.ACTIVE
    assert node.started_at is not None

    # Advance progress
    node.update(completed_bytes=500, speed_bps=250.0)
    assert node.completed_bytes == 500
    assert node.percent == 50.0
    assert node.speed_bps == 250.0
    assert node.eta_seconds == 2.0  # 500 remaining / 250 bps = 2.0s

    node.complete()
    assert node.status == StreamStatus.COMPLETED
    assert node.completed_at is not None
    assert node.completed_bytes == 1000
    assert node.percent == 100.0


def test_hierarchical_rollup_aggregation():
    """Verify multi-stream hierarchy aggregates child progress, rate, and status up to root."""
    tracker = HierarchicalProgressTracker()
    tracker.create_stream("job-100", label="Batch Video Download", total_bytes=3000)

    tracker.create_stream("seg-1", parent_id="job-100", label="Video Track", total_bytes=1000)
    tracker.create_stream("seg-2", parent_id="job-100", label="Audio Track", total_bytes=1000)
    tracker.create_stream("seg-3", parent_id="job-100", label="Subtitles", total_bytes=1000)

    tracker.start_stream("job-100")
    tracker.start_stream("seg-1")
    tracker.start_stream("seg-2")
    tracker.start_stream("seg-3")

    tracker.update_stream("seg-1", completed_bytes=1000, speed_bps=100.0)
    tracker.update_stream("seg-2", completed_bytes=500, speed_bps=200.0)
    tracker.update_stream("seg-3", completed_bytes=250, speed_bps=50.0)

    # Rollup check on root node
    summary = tracker.get_rollup("job-100")
    assert summary["completed_bytes"] == 1750  # 1000 + 500 + 250
    assert summary["total_bytes"] == 3000
    assert round(summary["percent"], 1) == 58.3
    assert summary["speed_bps"] == 350.0  # 100 + 200 + 50
    assert summary["status"] == StreamStatus.ACTIVE.value

    # Complete all
    tracker.complete_stream("seg-1")
    tracker.complete_stream("seg-2")
    tracker.complete_stream("seg-3")
    final_summary = tracker.get_rollup("job-100")
    assert final_summary["completed_bytes"] == 3000
    assert final_summary["percent"] == 100.0
    assert final_summary["status"] == StreamStatus.COMPLETED.value


def test_deep_hierarchy_and_nesting():
    """Verify multi-level nesting (Root -> Worker -> Task -> Chunk) propagates metrics correctly."""
    tracker = HierarchicalProgressTracker()
    tracker.create_stream("pipeline", label="Data Pipeline")
    tracker.create_stream("worker-1", parent_id="pipeline", label="Worker A")
    tracker.create_stream("task-1", parent_id="worker-1", label="Task Alpha")
    tracker.create_stream("chunk-1", parent_id="task-1", label="Chunk 1", total_bytes=500)
    tracker.create_stream("chunk-2", parent_id="task-1", label="Chunk 2", total_bytes=500)

    tracker.start_stream("chunk-1")
    tracker.update_stream("chunk-1", completed_bytes=250, speed_bps=50.0)

    # Rollup should reach the very top
    top_summary = tracker.get_rollup("pipeline")
    assert top_summary["completed_bytes"] == 250
    assert top_summary["total_bytes"] == 1000
    assert top_summary["percent"] == 25.0
    assert top_summary["active_children_count"] >= 1


def test_progress_telemetry_snapshot_and_delta():
    """Verify structured telemetry snapshot serialization and delta tracking for SSE."""
    tracker = HierarchicalProgressTracker()
    tracker.create_stream("stream-root", label="Root Stream", total_bytes=2000)
    tracker.create_stream("stream-sub", parent_id="stream-root", label="Child Stream", total_bytes=2000)

    snap1 = tracker.take_snapshot()
    assert snap1.stream_count == 2
    json_str = snap1.to_json()
    parsed = json.loads(json_str)
    assert "streams" in parsed
    assert "timestamp" in parsed

    # Update only the child
    tracker.update_stream("stream-sub", completed_bytes=1000)
    delta = tracker.get_delta_since(snap1.sequence_id)
    assert len(delta["updated_streams"]) == 2  # child and affected parent
    assert delta["updated_streams"][0]["stream_id"] in ("stream-sub", "stream-root")


def test_progress_telemetry_renderer_tree_formatting():
    """Verify tree rendering with box-drawing characters and progress bars."""
    tracker = HierarchicalProgressTracker()
    tracker.create_stream("root", label="Master Job", total_bytes=1000)
    tracker.create_stream("child1", parent_id="root", label="Stream A", total_bytes=500)
    tracker.create_stream("child2", parent_id="root", label="Stream B", total_bytes=500)

    tracker.start_stream("root")
    tracker.start_stream("child1")
    tracker.update_stream("child1", completed_bytes=250, speed_bps=1024 * 1024)

    renderer = ProgressTelemetryRenderer(tracker, bar_width=10, use_ansi=False)
    rendered = renderer.render_tree("root")

    assert "Master Job" in rendered
    assert "Stream A" in rendered
    assert "Stream B" in rendered
    # Box-drawing tree connectors
    assert any(c in rendered for c in ("├──", "└──", "│"))
    # Progress indicator presence
    assert "%" in rendered
    assert "MB/s" in rendered or "KB/s" in rendered or "B/s" in rendered


def test_renderer_plain_text_and_width_capping():
    """Verify plain-text output, ANSI code stripping, and terminal width truncation."""
    tracker = HierarchicalProgressTracker()
    tracker.create_stream(
        "long-stream",
        label="Very Long Stream Label Exceeding Normal Column Constraints In Enterprise Terminal Displays",
        total_bytes=100000,
    )
    tracker.update_stream("long-stream", completed_bytes=50000)

    renderer = ProgressTelemetryRenderer(tracker, max_width=60, use_ansi=False)
    lines = renderer.render_tree("long-stream").splitlines()

    for line in lines:
        assert len(line) <= 60
        assert "\033[" not in line  # Zero ANSI escapes when use_ansi=False


def test_thread_safety_concurrent_updates():
    """Verify thread-safe updates across multiple concurrent worker threads."""
    tracker = HierarchicalProgressTracker()
    tracker.create_stream("concurrent-root", label="Concurrent Root", total_bytes=10000)

    worker_count = 10
    updates_per_worker = 100

    for i in range(worker_count):
        tracker.create_stream(f"w-{i}", parent_id="concurrent-root", label=f"Worker {i}", total_bytes=1000)

    def worker_func(idx: int):
        for _ in range(updates_per_worker):
            tracker.update_stream(f"w-{idx}", delta_bytes=10, speed_bps=500.0)

    threads = [threading.Thread(target=worker_func, args=(i,)) for i in range(worker_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    summary = tracker.get_rollup("concurrent-root")
    # 10 workers * 100 updates * 10 bytes = 10000 bytes
    assert summary["completed_bytes"] == 10000
    assert summary["percent"] == 100.0


def test_runner_telemetry_hierarchical_progress_capability():
    """Verify TelemetryMixin exposes hierarchical multi-stream progress capability."""
    from bulk_downloader.runner_telemetry import TelemetryMixin

    class DummyRunner(TelemetryMixin):
        site_id = "test-site"

    runner = DummyRunner()
    has_tracker = hasattr(runner, "get_progress_tracker")
    assert has_tracker is True, "TelemetryMixin must expose get_progress_tracker for hierarchical multi-stream progress"

    tracker = runner.get_progress_tracker()
    tracker.create_stream("test-site", label="Test Site Root", total_bytes=1000)
    tracker.create_stream("sub-1", parent_id="test-site", label="Track 1", total_bytes=500)
    tracker.update_stream("sub-1", completed_bytes=250)

    rendered = runner.render_progress_telemetry("test-site", use_ansi=False)
    assert "Test Site Root" in rendered
    assert "Track 1" in rendered


# --- N6-A E1 / P4-B E1: the REAL product callers feed and serve the tree -----


class _ScriptedResponse:
    """httpx stream stand-in. `after_chunk` runs when the transfer loop asks for
    the next chunk, i.e. after it fully handled the previous one."""

    def __init__(self, chunks, content_length, after_chunk, error=None):
        self.status_code = 200
        self.headers = {"Content-Length": str(content_length)}
        self._chunks = list(chunks)
        self._after_chunk = after_chunk
        self._error = error

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_bytes(self, chunk_size=None):
        for chunk in self._chunks:
            yield chunk
            self._after_chunk()
        if self._error is not None:
            raise self._error


def _tick_clock():
    value = [0.0]

    def tick():
        value[0] += 1.1
        return value[0]

    return tick


def _transfer_runner(monkeypatch, site_id, responses):
    """The test_live_telemetry sequential-transfer harness: a SiteRunner shell
    whose _http_download runs the real transfer loop on scripted responses."""
    import httpx

    from bulk_downloader import rate_limit
    from bulk_downloader import runner as runner_mod
    from bulk_downloader import runner_transport as transport

    runner = runner_mod.SiteRunner.__new__(runner_mod.SiteRunner)
    runner.site_id = site_id
    runner.config = {"parallel_chunks": 1, "use_curl_cffi": False}
    runner._stop = threading.Event()
    runner._pause = threading.Event()
    runner._pause.set()
    runner._pick_fastest_mirror = lambda url: url
    runner._recommended_chunk_bytes = lambda: 1024
    runner._current_cap_mbps = lambda: 0
    runner._download_proxy_url = lambda: None
    runner._observe_throughput = lambda *args: None
    runner.log_event = lambda *args, **kwargs: None
    runner.log = type("Log", (), {"warning": lambda *args, **kwargs: None})()
    ticks = []
    runner._update_job = lambda *args, **extra: ticks.append(extra.get("file_size"))
    pending = list(responses)
    monkeypatch.setattr(httpx.Client, "stream", lambda self, *args, **kwargs: pending.pop(0))
    monkeypatch.setattr(transport.time, "time", _tick_clock())
    monkeypatch.setattr(transport, "record_bandwidth", lambda delta: None)
    slot = type("Slot", (), {"release": lambda self: None})()
    monkeypatch.setattr(rate_limit, "acquire", lambda url: slot)
    return runner, ticks


def _ctx():
    return type("Ctx", (), {"cookies": lambda self: []})()


def test_sequential_http_transfer_feeds_site_progress_tree(monkeypatch, tmp_path):
    """E1: SiteRunner._http_download's own ~1 Hz progress tick feeds the file's
    stream under the site root, and the transfer's return completes it."""
    page_url = "https://page.test/row986-a"
    seen = []

    def observe():
        tracker = runner.get_progress_tracker()
        seen.append((tracker.get_rollup(page_url), tracker.get_rollup("row986-transfer")))

    response = _ScriptedResponse([b"ab", b"cdef", b"gh"], 8, observe)
    runner, ticks = _transfer_runner(monkeypatch, "row986-transfer", [response])

    size, transferred = runner._http_download(
        page_url, object(), _ctx(), "https://cdn.test/row986-a.mp4", Path(tmp_path) / "a.mp4")

    assert (size, transferred) == (8, 8)
    assert ticks == [2, 6, 8]
    assert [leaf.get("completed_bytes") for leaf, _root in seen] == [2, 6, 8]
    first, first_root = seen[0]
    assert (first["total_bytes"], first["percent"], first["status"]) == (8, 25.0, "active")
    assert first_root["status"] == "active"
    mid, mid_root = seen[1]
    assert mid["percent"] == 75.0
    assert mid["speed_bps"] > 0
    assert mid["eta_seconds"] == pytest.approx(2 / mid["speed_bps"])
    assert (mid_root["completed_bytes"], mid_root["total_bytes"], mid_root["percent"]) == (6, 8, 75.0)
    assert mid_root["eta_seconds"] == round(2 / mid_root["speed_bps"], 1)
    tracker = runner.get_progress_tracker()
    done = tracker.get_rollup(page_url)
    root = tracker.get_rollup("row986-transfer")
    assert (done["status"], done["completed_bytes"], done["percent"]) == ("completed", 8, 100.0)
    assert (root["status"], root["completed_bytes"], root["total_bytes"], root["percent"]) == (
        "completed", 8, 8, 100.0)
    assert tracker.get_stream(page_url).label == "a.mp4"


def test_failed_http_transfer_fails_its_stream_and_the_site_root(monkeypatch, tmp_path):
    """E1 outcome arm: a transfer that raises fails its stream (URL-free reason, no
    residual speed) and the site root's composite; the retry of the same page
    re-activates that one stream and completes it."""
    import httpx

    from bulk_downloader import runner_transport as transport

    page_url = "https://page.test/row986-b"
    seen = []

    def observe():
        seen.append(runner.get_progress_tracker().get_rollup(page_url))

    broken = _ScriptedResponse(
        [b"ab"], 8, observe,
        error=httpx.ReadError("reset by https://cdn.test/b.mp4?token=row986-secret"))
    retry = _ScriptedResponse([b"abcdefgh"], 8, observe)
    runner, _ticks = _transfer_runner(monkeypatch, "row986-fail", [broken, retry])
    final_path = Path(tmp_path) / "b.mp4"

    with pytest.raises(transport._HTTPDownloadFailed):
        runner._http_download(page_url, object(), _ctx(), "https://cdn.test/b.mp4", final_path)

    assert (seen[0]["status"], seen[0]["completed_bytes"]) == ("active", 2)
    assert seen[0]["speed_bps"] > 0
    tracker = runner.get_progress_tracker()
    failed = tracker.get_stream(page_url)
    assert failed.status == StreamStatus.FAILED
    assert failed.metadata["error"] == "_HTTPDownloadFailed: http error"
    assert "row986-secret" not in failed.metadata["error"]
    assert (failed.speed_bps, failed.eta_seconds) == (0.0, None)
    root = tracker.get_rollup("row986-fail")
    assert (root["status"], root["speed_bps"], root["eta_seconds"]) == ("failed", 0.0, None)

    size, _transferred = runner._http_download(
        page_url, object(), _ctx(), "https://cdn.test/b.mp4", final_path)

    assert size == 8
    assert seen[-1]["status"] == "active"
    done = tracker.get_stream(page_url)
    assert done.status == StreamStatus.COMPLETED
    assert "error" not in done.metadata
    assert tracker.get_rollup("row986-fail")["status"] == "completed"
    assert tracker.get_stream("row986-fail").children == [page_url]


def test_operator_stop_cancels_the_transfer_stream(monkeypatch, tmp_path):
    """E1 outcome arm, stop: a transfer ended by the operator's Stop is not a
    failure -- its stream ends CANCELLED (no error, no speed) and the site root
    reads idle, not failed; the restarted transfer completes the same stream."""
    from bulk_downloader import runner_transport as transport

    page_url = "https://page.test/row986-stop"

    def stop_the_site():
        runner._stop.set()

    stopped = _ScriptedResponse([b"ab", b"cd"], 8, stop_the_site)
    restarted = _ScriptedResponse([b"abcdefgh"], 8, lambda: None)
    runner, _ticks = _transfer_runner(monkeypatch, "row986-stop", [stopped, restarted])
    final_path = Path(tmp_path) / "s.mp4"

    with pytest.raises(transport._HTTPDownloadFailed, match="stopped"):
        runner._http_download(page_url, object(), _ctx(), "https://cdn.test/s.mp4", final_path)

    tracker = runner.get_progress_tracker()
    node = tracker.get_stream(page_url)
    assert (node.status, node.completed_bytes, node.speed_bps, node.eta_seconds) == (
        StreamStatus.CANCELLED, 2, 0.0, None)
    assert "error" not in node.metadata
    assert tracker.get_rollup("row986-stop")["status"] == "idle"
    lines = runner.render_progress_telemetry().splitlines()
    assert lines[0].startswith("[IDLE] row986-stop [")
    assert lines[1].startswith("└── [CANCELLED] s.mp4 [")

    runner._stop.clear()
    size, _transferred = runner._http_download(
        page_url, object(), _ctx(), "https://cdn.test/s.mp4", final_path)

    assert size == 8
    assert tracker.get_stream(page_url).status == StreamStatus.COMPLETED
    assert tracker.get_rollup("row986-stop")["status"] == "completed"


def test_get_status_serves_progress_rollup_and_tree():
    """E1 reader: SiteRunner.get_status carries the site's rollup (light) and the
    rendered tree (full), fed through the mixin entry the transfer tick calls."""
    from bulk_downloader.runner import SiteRunner

    runner = SiteRunner("row986_status", {"concurrency": 1})
    assert runner.get_status(light=True)["progress_telemetry"]["rollup"]["total_bytes"] == 0

    runner.record_transfer_progress("https://page.test/s1", 300, 600, 100.0, label="s1.mp4")
    runner.record_transfer_progress("https://page.test/s2", 400, 400, 0.0, label="s2.mp4")
    runner.finish_transfer_progress("https://page.test/s2")

    light = runner.get_status(light=True)["progress_telemetry"]
    assert "tree" not in light
    rollup = light["rollup"]
    assert (rollup["stream_id"], rollup["completed_bytes"], rollup["total_bytes"]) == ("row986_status", 700, 1000)
    assert (rollup["percent"], rollup["eta_seconds"], rollup["status"]) == (70.0, 3.0, "active")
    full = runner.get_status(light=False)["progress_telemetry"]
    assert full["rollup"] == rollup
    lines = full["tree"].splitlines()
    assert lines[0].startswith("[ACTIVE] row986_status [")
    assert any("s1.mp4" in line and "[ACTIVE]" in line for line in lines[1:])
    assert any("s2.mp4" in line and "[COMPLETED]" in line for line in lines[1:])


def test_api_status_route_serves_progress_telemetry(fresh_app):
    """E1 surface: GET /api/status (the operator's status poll) serves each site's
    progress rollup, plus the rendered tree outside light mode."""
    from bulk_downloader.app_state import runners

    sid = fresh_app.post("/api/sites", json={"name": "row986-route"}).get_json()["id"]
    runners[sid].record_transfer_progress("https://page.test/r1", 25, 100, 5.0, label="r1.mp4")

    light = fresh_app.get("/api/status?light=1").get_json()[sid]["progress_telemetry"]
    assert "tree" not in light
    rollup = light["rollup"]
    assert (rollup["completed_bytes"], rollup["total_bytes"], rollup["status"]) == (25, 100, "active")
    assert rollup["eta_seconds"] == 15.0
    full = fresh_app.get("/api/status").get_json()[sid]["progress_telemetry"]
    assert "r1.mp4" in full["tree"]


# --- N6-A E2: bookkeeping bounded by streams, not events ----------------------


def _container_entries(tracker):
    """Entries held by every container attribute of the tracker (one level down too)."""
    total = 0
    for value in vars(tracker).values():
        if isinstance(value, (dict, list, set, tuple)):
            total += len(value)
            members = value.values() if isinstance(value, dict) else value
            total += sum(len(m) for m in members if isinstance(m, (dict, list, set, tuple)))
    return total


def test_bookkeeping_is_bounded_by_streams_not_events():
    """N6-A E2: 20000 updates to one stream leave the same bookkeeping as 10 (the old
    per-event history held 20001 entries after 20000 updates)."""

    def entries_after(updates):
        tracker = HierarchicalProgressTracker()
        tracker.create_stream("root")
        tracker.create_stream("leaf", parent_id="root", total_bytes=10**9)
        for i in range(updates):
            tracker.update_stream("leaf", completed_bytes=i, speed_bps=1.0)
        return _container_entries(tracker)

    assert entries_after(20000) == entries_after(10)


def test_finished_streams_are_evicted_beyond_the_cap():
    """N6-A E2 for the wired product (one stream per file): finished leaves past
    max_finished_streams are evicted oldest first with their bytes folded into the
    parent (the rollup stays exact), active streams stay, and the delta names the
    removals -- or demands a resync once its bounded removal log is outrun."""
    tracker = HierarchicalProgressTracker(max_finished_streams=3)
    tracker.create_stream("site")
    tracker.report_transfer("live", "site", 5, total_bytes=10, speed_bps=1.0)
    start = tracker.take_snapshot().sequence_id
    for i in range(7):
        tracker.report_transfer(f"f{i}", "site", 100, total_bytes=100, speed_bps=50.0)
        tracker.complete_stream(f"f{i}")
    tracker.report_transfer("f7", "site", 100, total_bytes=100, speed_bps=50.0)
    before_last = tracker.take_snapshot().sequence_id
    tracker.complete_stream("f7")

    assert tracker.get_stream("site").children == ["live", "f5", "f6", "f7"]
    assert tracker.get_stream("f0") is None
    rollup = tracker.get_rollup("site")
    assert (rollup["completed_bytes"], rollup["total_bytes"], rollup["retired_children_count"]) == (805, 810, 5)
    assert (rollup["status"], rollup["eta_seconds"]) == ("active", 5.0)
    recent = tracker.get_delta_since(before_last)
    assert recent["removed_stream_ids"] == ["f4"]
    assert recent["resync_required"] is False
    assert sorted(s["stream_id"] for s in recent["updated_streams"]) == ["f7", "site"]
    stale = tracker.get_delta_since(start)
    assert stale["resync_required"] is True

    # The product path at its default cap: 250 transfers through the mixin.
    from bulk_downloader.runner_telemetry import TelemetryMixin

    class Host(TelemetryMixin):
        site_id = "row986-bounded"

    host = Host()
    for i in range(250):
        host.record_transfer_progress(f"https://page.test/{i}", 10, 10, 5.0, label=f"{i}.mp4")
        host.finish_transfer_progress(f"https://page.test/{i}")
    site = host.get_progress_tracker().get_rollup("row986-bounded")
    assert len(host.get_progress_tracker().get_stream("row986-bounded").children) == 100
    assert (site["completed_bytes"], site["total_bytes"], site["status"]) == (2500, 2500, "completed")


# --- N6-A E3: cycles refused; every walk terminates ---------------------------


def test_create_stream_refuses_a_parent_cycle():
    """N6-A E3: a parent link that would close a cycle is refused (self-parent, a
    re-parent under a descendant, a forward reference closed later); a legal
    re-parent moves the subtree cleanly."""
    tracker = HierarchicalProgressTracker()
    tracker.create_stream("a")
    tracker.create_stream("b", parent_id="a")
    tracker.create_stream("c", parent_id="b")
    with pytest.raises(ValueError, match="cycle"):
        tracker.create_stream("a", parent_id="c")
    with pytest.raises(ValueError, match="cycle"):
        tracker.create_stream("x", parent_id="x")
    tracker.create_stream("p", parent_id="q")
    with pytest.raises(ValueError, match="cycle"):
        tracker.create_stream("q", parent_id="p")
    assert tracker.get_stream("a").parent_id is None
    assert tracker.get_stream("x") is None

    tracker.create_stream("q")
    assert tracker.get_stream("q").children == ["p"]
    tracker.create_stream("c", parent_id="a")
    assert (tracker.get_stream("a").children, tracker.get_stream("b").children) == (["b", "c"], [])
    tracker.update_stream("c", completed_bytes=7)
    assert tracker.get_rollup("a")["completed_bytes"] == 7


# --- N6-A E4 / P4-B E2: delta, composite FAILED, ETA, clamp, visible width ----


def test_delta_since_returns_only_streams_changed_after_the_sequence():
    """N6-A E4 (delta_all escaped): a delta lists exactly the streams modified after
    the sequence -- the changed leaf and its ancestors -- and nothing when idle."""
    tracker = HierarchicalProgressTracker()
    tracker.create_stream("job")
    tracker.create_stream("left", parent_id="job", total_bytes=100)
    tracker.create_stream("right", parent_id="job", total_bytes=100)
    tracker.create_stream("other")
    tracker.create_stream("other-leaf", parent_id="other", total_bytes=100)
    mark = tracker.take_snapshot().sequence_id
    assert tracker.get_delta_since(mark)["updated_streams"] == []

    tracker.update_stream("left", completed_bytes=10)
    delta = tracker.get_delta_since(mark)
    assert sorted(s["stream_id"] for s in delta["updated_streams"]) == ["job", "left"]
    assert delta["current_sequence_id"] > mark

    later = delta["current_sequence_id"]
    tracker.update_stream("other-leaf", completed_bytes=1)
    assert sorted(s["stream_id"] for s in tracker.get_delta_since(later)["updated_streams"]) == [
        "other", "other-leaf"]
    assert len(tracker.get_delta_since(0)["updated_streams"]) == 5


def test_failure_propagates_to_every_ancestor():
    """N6-A E4 / P4-B M2 (failed_not_composite survived): a failed leaf makes every
    ancestor's composite FAILED; the dead leaf stops feeding the ancestors' speed
    and ETA (its 80 unmoved bytes are not outstanding work: 50 left at 10 B/s is
    5.0 s, not 130 / 10); the delta carries the ancestors whose composite changed."""
    tracker = HierarchicalProgressTracker()
    tracker.create_stream("site")
    tracker.create_stream("batch", parent_id="site")
    tracker.create_stream("ok", parent_id="batch", total_bytes=100)
    tracker.create_stream("bad", parent_id="batch", total_bytes=100)
    tracker.create_stream("sibling", parent_id="site", total_bytes=100)
    for sid in ("ok", "bad", "sibling"):
        tracker.start_stream(sid)
    tracker.update_stream("ok", completed_bytes=50, speed_bps=10.0)
    tracker.update_stream("bad", completed_bytes=20, speed_bps=30.0)
    tracker.complete_stream("sibling")
    assert tracker.get_rollup("site")["speed_bps"] == 40.0
    mark = tracker.take_snapshot().sequence_id

    tracker.fail_stream("bad", "connection reset")

    for sid in ("site", "batch"):
        assert tracker.get_rollup(sid)["status"] == "failed", sid
    assert tracker.get_rollup("ok")["status"] == "active"
    site = tracker.get_rollup("site")
    assert (site["completed_bytes"], site["total_bytes"], site["speed_bps"]) == (170, 220, 10.0)
    assert site["eta_seconds"] == 5.0
    bad = tracker.get_stream("bad")
    assert (bad.speed_bps, bad.eta_seconds, bad.metadata["error"]) == (0.0, None, "connection reset")
    touched = sorted(s["stream_id"] for s in tracker.get_delta_since(mark)["updated_streams"])
    assert touched == ["bad", "batch", "site"]
    # The rendered tree tags each line with its rollup's composite status.
    lines = ProgressTelemetryRenderer(tracker, use_ansi=False).render_tree("site").splitlines()
    assert [re.search(r"\[([A-Z]+)\]", line).group(1) for line in lines] == [
        "FAILED", "FAILED", "ACTIVE", "FAILED", "COMPLETED"]


def test_cancelled_stream_ends_without_failing_its_ancestors():
    """An operator stop cancels a stream: no error, no residual speed, no FAILED
    composite above it, and it is finished (the cap evicts it like the others)."""
    tracker = HierarchicalProgressTracker(max_finished_streams=1)
    tracker.create_stream("site")
    tracker.create_stream("f1", parent_id="site", total_bytes=10)
    tracker.create_stream("f2", parent_id="site", total_bytes=10)
    tracker.update_stream("f1", completed_bytes=4, speed_bps=2.0, metadata={"error": "old"})
    tracker.update_stream("f2", completed_bytes=6, speed_bps=3.0)

    tracker.cancel_stream("f1")

    f1 = tracker.get_stream("f1")
    assert (f1.status, f1.speed_bps, f1.eta_seconds) == (StreamStatus.CANCELLED, 0.0, None)
    assert "error" not in f1.metadata
    site = tracker.get_rollup("site")
    assert (site["status"], site["completed_bytes"], site["speed_bps"]) == ("active", 10, 3.0)

    tracker.cancel_stream("f2")

    assert tracker.get_stream("f1") is None
    site = tracker.get_rollup("site")
    assert (site["status"], site["completed_bytes"], site["retired_children_count"]) == ("idle", 10, 1)


def test_an_ended_transfer_counts_only_the_bytes_it_moved():
    """Verify r3 MED: a cancelled (or failed) transfer moves no more bytes, so its
    unmoved remainder is not outstanding work -- not while it is in the tree, not
    after eviction folds it into the parent, and a retry after the eviction still
    ends the site at 100%, completed."""
    tracker = HierarchicalProgressTracker(max_finished_streams=2)
    tracker.create_stream("site")
    tracker.report_transfer("big", "site", 1000, total_bytes=4000, speed_bps=100.0)
    tracker.cancel_stream("big")
    tracker.report_transfer("live", "site", 100, total_bytes=200, speed_bps=10.0)
    site = tracker.get_rollup("site")
    assert (site["completed_bytes"], site["total_bytes"], site["eta_seconds"]) == (1100, 1200, 10.0)

    for i in range(2):
        tracker.report_transfer(f"f{i}", "site", 50, total_bytes=50, speed_bps=5.0)
        tracker.complete_stream(f"f{i}")
    assert tracker.get_stream("big") is None
    site = tracker.get_rollup("site")
    assert (site["completed_bytes"], site["total_bytes"], site["retired_children_count"]) == (1200, 1300, 1)
    assert site["eta_seconds"] == 10.0

    tracker.report_transfer("big", "site", 4000, total_bytes=4000, speed_bps=100.0)
    tracker.complete_stream("big")
    tracker.complete_stream("live")
    site = tracker.get_rollup("site")
    assert (site["percent"], site["status"], site["eta_seconds"]) == (100.0, "completed", None)


def test_an_unknown_size_leaves_the_eta_unknown():
    """Verify r3 LOW: a transfer with no declared size (a chunked response) has no
    known remaining time -- its ETA is unknown, never 0, so its line shows none --
    and it adds only the bytes it moved to its parent's total, so the parent's ETA
    is the sized transfers' remaining bytes over the summed speed."""
    tracker = HierarchicalProgressTracker()
    tracker.create_stream("site")
    tracker.report_transfer("chunked", "site", 300, total_bytes=None, speed_bps=30.0, label="chunked")
    tracker.report_transfer("sized", "site", 100, total_bytes=200, speed_bps=20.0, label="sized")
    assert tracker.get_stream("chunked").eta_seconds is None
    site = tracker.get_rollup("site")
    assert (site["completed_bytes"], site["total_bytes"], site["speed_bps"]) == (400, 500, 50.0)
    assert site["eta_seconds"] == 2.0
    lines = ProgressTelemetryRenderer(tracker, use_ansi=False).render_tree("site").splitlines()
    chunked_line = [line for line in lines if " chunked " in line]
    assert len(chunked_line) == 1 and "ETA" not in chunked_line[0]


def test_rollup_eta_is_remaining_bytes_over_summed_speed():
    """P4-B M3 (rollup ETA forced 0 survived): root 1000 declared; c1 400/400 done;
    c2 300/600 at 100 B/s -> 700/1000, 70%, ETA exactly 3.0, active. A leaf at
    50/100 and 10 B/s reads 50% and ETA 5.0."""
    tracker = HierarchicalProgressTracker()
    tracker.create_stream("root", total_bytes=1000)
    tracker.create_stream("c1", parent_id="root", total_bytes=400)
    tracker.create_stream("c2", parent_id="root", total_bytes=600)
    tracker.update_stream("c1", completed_bytes=400)
    tracker.complete_stream("c1")
    tracker.update_stream("c2", completed_bytes=300, speed_bps=100.0)
    root = tracker.get_rollup("root")
    assert (root["completed_bytes"], root["total_bytes"], root["percent"]) == (700, 1000, 70.0)
    assert (root["eta_seconds"], root["status"]) == (3.0, "active")

    tracker.create_stream("leaf", total_bytes=100)
    tracker.update_stream("leaf", completed_bytes=50, speed_bps=10.0)
    leaf = tracker.get_rollup("leaf")
    assert (leaf["percent"], leaf["eta_seconds"]) == (50.0, 5.0)


def test_negative_progress_is_clamped_to_zero():
    """P4-B M5 (clamp removal survived): a negative absolute position and a delta
    below zero both clamp at 0, in the node and in the parent's rollup."""
    tracker = HierarchicalProgressTracker()
    tracker.create_stream("root")
    tracker.create_stream("leaf", parent_id="root", total_bytes=100)
    tracker.update_stream("leaf", completed_bytes=-50)
    assert tracker.get_stream("leaf").completed_bytes == 0
    tracker.update_stream("leaf", completed_bytes=5)
    tracker.update_stream("leaf", delta_bytes=-10)
    assert tracker.get_stream("leaf").completed_bytes == 0
    root = tracker.get_rollup("root")
    assert (root["completed_bytes"], root["percent"]) == (0, 0.0)


_SGR = re.compile(r"\x1b\[[0-9;]*m")


def _columns(text):
    visible = _SGR.sub("", text)
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in visible)


def test_ansi_render_width_cap_counts_visible_columns():
    """N6-A E4 (P3): with ANSI on, the width cap counts visible columns, not escape
    bytes (the len() cut left 71 visible of 80); escapes survive whole and a reset
    closes the cut line. Wide (CJK) glyphs count two columns."""
    tracker = HierarchicalProgressTracker()
    tracker.create_stream("root", label="R" * 120, total_bytes=100)
    tracker.create_stream("wide", parent_id="root", label="漢" * 60, total_bytes=100)
    tracker.start_stream("root")
    tracker.start_stream("wide")

    lines = ProgressTelemetryRenderer(tracker, max_width=80, use_ansi=True).render_tree("root").splitlines()
    assert "\x1b[36m[ACTIVE]\x1b[0m" in lines[0]
    assert _columns(lines[0]) == 80
    assert lines[0].endswith("\x1b[0m...")
    assert _columns(lines[1]) == 80
    assert lines[1].startswith("└── \x1b[36m[ACTIVE]")

    plain = ProgressTelemetryRenderer(tracker, max_width=80, use_ansi=False).render_tree("root").splitlines()
    assert [_columns(line) for line in plain] == [80, 80]
    assert "\x1b" not in "".join(plain)


def test_nested_render_keeps_subtree_indentation():
    """Found while fixing: .strip() on each rendered line dropped the leading tree
    indentation of every subtree under a last child."""
    tracker = HierarchicalProgressTracker()
    tracker.create_stream("root", label="root")
    tracker.create_stream("a", parent_id="root", label="A")
    tracker.create_stream("a1", parent_id="a", label="A1")
    tracker.create_stream("b", parent_id="root", label="B")
    tracker.create_stream("b1", parent_id="b", label="B1")
    tracker.create_stream("b1x", parent_id="b1", label="B1X")
    lines = ProgressTelemetryRenderer(tracker, use_ansi=False).render_tree("root").splitlines()
    assert [line.split("[", 1)[0] for line in lines] == [
        "", "├── ", "│   └── ", "└── ", "    └── ", "        └── "]


def test_render_neutralizes_control_sequences_in_labels():
    """A label is data (a file name can carry an escape): no escape or control
    character from it reaches the terminal or splits the tree onto extra lines."""
    tracker = HierarchicalProgressTracker()
    tracker.create_stream("root", label="site")
    tracker.create_stream("evil", parent_id="root", label="a\x1b[2J\x1b]0;pwned\x07b\nc\rd")
    for use_ansi in (True, False):
        text = ProgressTelemetryRenderer(tracker, use_ansi=use_ansi).render_tree("root")
        lines = text.splitlines()
        assert len(lines) == 2, use_ansi
        assert "\x1b[2J" not in text
        assert "\x07" not in text
        assert "\r" not in text
        assert "b?c?d" in lines[1]


def test_walks_terminate_even_on_a_corrupted_cycle():
    """N6-A E3: even a cycle forced past create_stream (public node fields edited
    directly) cannot hang update/rollup/render/fail/complete/delta: every walk
    carries a visited set, so each call returns and releases the lock.
    (Last in the file: on the unfixed tree the walk spins forever in a daemon thread.)"""
    tracker = HierarchicalProgressTracker()
    tracker.create_stream("a")
    tracker.create_stream("b", parent_id="a", total_bytes=10)
    tracker.create_stream("c", parent_id="b", total_bytes=10)
    tracker.get_stream("a").parent_id = "c"
    tracker.get_stream("c").children.append("a")
    results = {}

    def walk():
        tracker.update_stream("c", completed_bytes=4, speed_bps=2.0)
        results["rollup"] = tracker.get_rollup("a")
        results["tree"] = render_progress_hierarchy(tracker, "a")
        tracker.fail_stream("c", "boom")
        tracker.complete_stream("c")
        results["delta"] = tracker.get_delta_since(0)

    worker = threading.Thread(target=walk, daemon=True)
    worker.start()
    worker.join(5)
    assert not worker.is_alive(), "a walk looped on the corrupted cycle (its lock is now held forever)"
    assert results["rollup"]["total_children_count"] == 2
    assert len(results["tree"].splitlines()) == 3
    assert sorted(s["stream_id"] for s in results["delta"]["updated_streams"]) == ["a", "b", "c"]
