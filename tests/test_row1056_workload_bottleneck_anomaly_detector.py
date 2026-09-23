"""Row 1056: Automated Workload Bottleneck Anomaly Detector.

Validates:
1. Statistical rolling-window and EWMA baseline tracking for workload execution metrics.
2. Automated detection and classification of workload bottlenecks:
   - Worker pool exhaustion / starvation
   - Queue backlog buildup / stall
   - Throughput collapse / download transfer stagnation
   - Latency spikes / processing time outliers
3. Dynamic anomaly severity assessment and automated mitigation recommendation generation.
4. Thread-safe metric ingestion and evaluation under concurrent worker activity.
5. Integration with TelemetryMixin and runner subsystems without breaking existing callers.

RED on baseline: fails with explicit semantic AssertionError (capability missing),
never an unhandled ImportError or ModuleNotFoundError.
Includes positive control test passing on baseline to prove the probe can say YES.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

BD_GATE_SCOPE = "module"

try:
    from bulk_downloader import workload_bottleneck
except ImportError:
    workload_bottleneck = None


def test_positive_control_telemetry_baseline():
    """Positive control (Rule 7): proves test runner and probe can say YES on baseline capabilities."""
    from bulk_downloader import runner_telemetry

    assert hasattr(runner_telemetry, "TelemetryMixin"), "TelemetryMixin must exist on baseline"
    assert hasattr(runner_telemetry.TelemetryMixin, "log_event"), "log_event must exist on baseline"
    assert callable(runner_telemetry.TelemetryMixin.log_event), "log_event must be callable"


def test_workload_bottleneck_detector_capability_implemented():
    """RED assertion: capability and product callers must be implemented with semantic AssertionError on base."""
    from bulk_downloader import runner_telemetry

    assert workload_bottleneck is not None, (
        "Row 1056 capability missing: Automated Workload Bottleneck Anomaly Detector "
        "not implemented in bulk_downloader.workload_bottleneck"
    )
    assert hasattr(runner_telemetry.TelemetryMixin, "record_workload_observation"), (
        "Row 1056 caller missing: bulk_downloader.runner_telemetry.TelemetryMixin.record_workload_observation"
    )
    assert hasattr(runner_telemetry.TelemetryMixin, "check_workload_bottlenecks"), (
        "Row 1056 caller missing: bulk_downloader.runner_telemetry.TelemetryMixin.check_workload_bottlenecks"
    )


def test_module_exports():
    """Verify bulk_downloader.workload_bottleneck exports all required detector components."""
    assert workload_bottleneck is not None, "workload_bottleneck capability missing"

    assert hasattr(workload_bottleneck, "BottleneckType")
    assert hasattr(workload_bottleneck, "AnomalySeverity")
    assert hasattr(workload_bottleneck, "AnomalyEvent")
    assert hasattr(workload_bottleneck, "WorkloadBottleneckDetector")
    assert hasattr(workload_bottleneck, "get_bottleneck_detector")
    assert hasattr(workload_bottleneck, "reset_bottleneck_detector")
    assert hasattr(workload_bottleneck, "record_workload_metric")


def test_detector_worker_exhaustion_detection():
    """Verify detection of worker saturation / exhaustion anomalies."""
    assert workload_bottleneck is not None, "workload_bottleneck capability missing"

    detector = workload_bottleneck.WorkloadBottleneckDetector(
        min_samples_for_baseline=3,
        worker_saturation_threshold=0.85,
    )

    # Establish normal baseline: 2 active out of 10 workers (20%), low queue
    for _ in range(5):
        anomaly = detector.record_worker_state(active_workers=2, max_workers=10, queued_jobs=1, site_id="site_a")
        assert anomaly is None

    # Anomaly trigger: 10 active out of 10 workers (100% saturation) with growing queue
    anomaly = detector.record_worker_state(active_workers=10, max_workers=10, queued_jobs=50, site_id="site_a")
    assert anomaly is not None
    assert anomaly.bottleneck_type == workload_bottleneck.BottleneckType.WORKER_EXHAUSTION
    assert anomaly.severity in (workload_bottleneck.AnomalySeverity.WARNING, workload_bottleneck.AnomalySeverity.CRITICAL)
    assert "site_a" in anomaly.details or anomaly.site_id == "site_a"
    assert len(anomaly.recommended_action) > 0


def test_detector_queue_backlog_detection():
    """Verify statistical detection of sudden queue backlog accumulation."""
    assert workload_bottleneck is not None, "workload_bottleneck capability missing"

    detector = workload_bottleneck.WorkloadBottleneckDetector(
        min_samples_for_baseline=5,
        z_score_threshold=2.5,
    )

    # Normal queue depth: mean ~ 5
    for q in [4.0, 5.0, 6.0, 5.0, 4.0, 5.0]:
        detector.record_metric("queue_depth", q, site_id="site_b")

    anomalies = detector.detect_anomalies(metric_name="queue_depth", site_id="site_b")
    assert len(anomalies) == 0

    # Massive spike in queue depth
    detector.record_metric("queue_depth", 150.0, site_id="site_b")
    anomalies = detector.detect_anomalies(metric_name="queue_depth", site_id="site_b")
    assert len(anomalies) > 0
    anomaly = anomalies[-1]
    assert anomaly.bottleneck_type == workload_bottleneck.BottleneckType.QUEUE_BACKLOG
    assert anomaly.observed_value == 150.0
    assert anomaly.anomaly_score > 2.5


def test_detector_throughput_collapse_detection():
    """Verify detection of download throughput stagnation or collapse."""
    assert workload_bottleneck is not None, "workload_bottleneck capability missing"

    detector = workload_bottleneck.WorkloadBottleneckDetector(
        min_samples_for_baseline=4,
    )

    # Normal transfer: 10 MB in 2 seconds = 5 MB/s
    for _ in range(5):
        anomaly = detector.record_transfer_sample(bytes_transferred=10_000_000, duration_seconds=2.0, site_id="site_c")
        assert anomaly is None

    # Stalled transfer: 100 bytes in 10 seconds = 10 B/s (massive collapse)
    anomaly = detector.record_transfer_sample(bytes_transferred=100, duration_seconds=10.0, site_id="site_c")
    assert anomaly is not None
    assert anomaly.bottleneck_type == workload_bottleneck.BottleneckType.THROUGHPUT_COLLAPSE
    assert anomaly.severity == workload_bottleneck.AnomalySeverity.CRITICAL
    assert "collapse" in anomaly.details.lower() or "throughput" in anomaly.details.lower()


def test_detector_latency_spike_detection():
    """Verify detection of processing/request latency outliers."""
    assert workload_bottleneck is not None, "workload_bottleneck capability missing"

    detector = workload_bottleneck.WorkloadBottleneckDetector(
        min_samples_for_baseline=5,
        z_score_threshold=2.5,
    )

    # Normal request latency: ~0.10s
    for lat in [0.09, 0.11, 0.10, 0.095, 0.105, 0.10]:
        detector.record_metric("request_latency_seconds", lat, site_id="site_d")

    # Latency spike to 4.5 seconds
    detector.record_metric("request_latency_seconds", 4.5, site_id="site_d")
    anomalies = detector.detect_anomalies(metric_name="request_latency_seconds", site_id="site_d")
    assert len(anomalies) > 0
    assert anomalies[-1].bottleneck_type == workload_bottleneck.BottleneckType.LATENCY_SPIKE


def test_concurrent_metric_recording_thread_safety():
    """Verify thread safety under concurrent metric recording."""
    assert workload_bottleneck is not None, "workload_bottleneck capability missing"

    detector = workload_bottleneck.WorkloadBottleneckDetector(window_size=1000)
    errors = []

    def worker_job(worker_id: int):
        try:
            for i in range(100):
                detector.record_metric(f"metric_{worker_id % 4}", float(i), site_id=f"site_{worker_id % 2}")
                detector.record_worker_state(active_workers=i % 10, max_workers=10, queued_jobs=i * 2)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker_job, args=(tid,)) for tid in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent execution generated errors: {errors}"
    summary = detector.get_metrics_summary()
    assert "metrics" in summary
    assert summary["total_samples"] >= 1000


def test_telemetry_mixin_integration():
    """Verify integration between TelemetryMixin and WorkloadBottleneckDetector."""
    assert workload_bottleneck is not None, "workload_bottleneck capability missing"
    from bulk_downloader.runner_telemetry import TelemetryMixin

    workload_bottleneck.reset_bottleneck_detector()

    # Create dummy runner subclassing TelemetryMixin
    class DummyRunner(TelemetryMixin):
        def __init__(self):
            import collections, itertools
            self._event_seq_counter = itertools.count(1)
            self._event_seq = 0
            self._event_log = collections.deque(maxlen=100)
            self.site_id = "test_site"

    runner = DummyRunner()

    # Direct observation API
    runner.record_workload_observation("chunk_latency", 0.05, extra={"chunk_id": "c1"})

    detector = workload_bottleneck.get_bottleneck_detector()
    assert detector.get_metrics_summary()["total_samples"] == 1

    # Completion feed: each completed file's wire throughput becomes one sample
    for _ in range(8):
        runner.record_transfer_completion(5_000_000, 1.0)
    assert detector.get_metrics_summary()["total_samples"] == 9
    assert runner.check_workload_bottlenecks() == []

    runner.record_transfer_completion(2_000_000, 10.0)
    active = runner.check_workload_bottlenecks()
    assert [a.bottleneck_type for a in active] == [workload_bottleneck.BottleneckType.THROUGHPUT_COLLAPSE]
    assert active[0].site_id == "test_site"
    workload_bottleneck.reset_bottleneck_detector()


@pytest.fixture
def fresh_detector():
    workload_bottleneck.reset_bottleneck_detector()
    try:
        yield workload_bottleneck.get_bottleneck_detector()
    finally:
        workload_bottleneck.reset_bottleneck_detector()


def test_site_status_surfaces_only_this_sites_anomalies(fresh_detector):
    """E1 reader: SiteRunner.get_status() carries this site's anomalies, not another site's."""
    from bulk_downloader.runner import SiteRunner

    runner = SiteRunner("row1056_a", {"concurrency": 1})
    other = SiteRunner("row1056_b", {"concurrency": 1})
    assert runner.get_status(light=True)["workload_anomalies"] == []

    for _ in range(8):
        runner.record_transfer_completion(5_000_000, 1.0)
        other.record_transfer_completion(5_000_000, 1.0)
    other.record_transfer_completion(2_000_000, 10.0)
    assert runner.get_status(light=True)["workload_anomalies"] == []

    runner.record_transfer_completion(2_000_000, 10.0)
    surfaced = runner.get_status(light=True)["workload_anomalies"]
    assert [(a["bottleneck_type"], a["site_id"]) for a in surfaced] == [("throughput_collapse", "row1056_a")]
    assert len(other.get_status(light=True)["workload_anomalies"]) == 1


def test_completion_without_transferred_bytes_is_not_a_sample(fresh_detector):
    """A file already on disk (0 bytes fetched) or a zero duration is not a throughput observation."""
    from bulk_downloader.runner_telemetry import TelemetryMixin

    class Runner(TelemetryMixin):
        site_id = "row1056_zero"

    runner = Runner()
    runner.record_transfer_completion(0, 3.0)
    runner.record_transfer_completion(None, 3.0)
    runner.record_transfer_completion(1_000, 0.0)
    assert fresh_detector.get_metrics_summary()["total_samples"] == 0


def _self_calls(path, func_name, method):
    import ast
    from pathlib import Path as _Path

    tree = ast.parse(_Path(path).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return [
                call for call in ast.walk(node)
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == method
                and isinstance(call.func.value, ast.Name) and call.func.value.id == "self"
            ]
    raise AssertionError(f"{func_name} not found in {path}")


def _guarded_feed_calls(path, func_name):
    """Calls, inside func_name, of the hook bound by getattr(self, "record_transfer_completion", None)."""
    import ast
    from pathlib import Path as _Path

    tree = ast.parse(_Path(path).read_text())
    func = next((node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef) and node.name == func_name), None)
    assert func is not None, f"{func_name} not found in {path}"
    bound = {
        target.id
        for node in ast.walk(func) if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "getattr" and len(node.value.args) == 3
        and isinstance(node.value.args[0], ast.Name) and node.value.args[0].id == "self"
        and isinstance(node.value.args[1], ast.Constant)
        and node.value.args[1].value == "record_transfer_completion"
        for target in node.targets if isinstance(target, ast.Name)
    }
    return [call for call in ast.walk(func)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id in bound]


def test_per_file_completion_paths_feed_the_detector():
    """E1 feed: both per-file completion paths hand their wire bytes and elapsed time to the detector.

    The hook is TelemetryMixin's, so each path looks it up once and never calls a bare
    self.record_transfer_completion(): a mixin host without TelemetryMixin cannot answer it
    (T74 PR986 CI red, test_row825 DummyRunner AttributeError).
    """
    import ast

    from bulk_downloader import runner_extractors, runner_transport

    for module, func, bytes_name in (
        (runner_transport, "_do_download", "bytes_fetched"),
        (runner_extractors, "_try_spa_api_media_extractor", "downloaded_size"),
    ):
        bare = _self_calls(module.__file__, func, "record_transfer_completion")
        assert bare == [], (
            f"{module.__name__}.{func} calls self.record_transfer_completion() unguarded "
            f"at line(s) {[call.lineno for call in bare]}")
        calls = _guarded_feed_calls(module.__file__, func)
        assert len(calls) == 1, (module.__name__, func, len(calls))
        first, elapsed = calls[0].args
        assert isinstance(first, ast.Name) and first.id == bytes_name
        assert "_download_started" in ast.unparse(elapsed)


def test_queue_latency_spike_is_emitted_once():
    """P4: one record_queue_latency spike produces exactly one anomaly event."""
    detector = workload_bottleneck.WorkloadBottleneckDetector(min_samples_for_baseline=5, z_score_threshold=2.5)
    for v in [0.1, 0.12, 0.11, 0.1, 0.12, 0.11]:
        assert detector.record_queue_latency(v, site_id="q") is None
    event = detector.record_queue_latency(5.0, site_id="q")
    assert event is not None
    assert len(detector.get_active_anomalies()) == 1


def test_small_file_after_fast_baseline_is_not_a_collapse(fresh_detector):
    """P4-B E1: a 2 KB file taking 1 s after 5 MB/s files is latency, not throughput.

    Samples below the size AND duration floors are neither judged nor folded into the
    baseline. A small file that is genuinely slow (past the duration floor) still is.
    """
    from bulk_downloader.runner_telemetry import TelemetryMixin

    class Runner(TelemetryMixin):
        site_id = "row1056_small"

    runner = Runner()
    for _ in range(9):
        runner.record_transfer_completion(5_000_000, 1.0)
    for size in (2_000, 40_000, 900_000):
        runner.record_transfer_completion(size, 1.0)
    assert runner.check_workload_bottlenecks() == [], "small quick files reported as a collapse"
    assert fresh_detector.get_metrics_summary()["total_samples"] == 9

    # Positive control: the same small file stalled for 30 s is a real collapse.
    runner.record_transfer_completion(2_000, 30.0)
    active = runner.check_workload_bottlenecks()
    assert [a.bottleneck_type for a in active] == [workload_bottleneck.BottleneckType.THROUGHPUT_COLLAPSE]


def test_t71_dp13_a_dropped_transfer_sample_is_counted_not_swallowed(fresh_detector, monkeypatch):
    """T71 DP-13 (runner_telemetry.record_transfer_completion): a failing sample was dropped by a bare
    `except: pass`. It still never reaches the download path, but it is counted."""
    from bulk_downloader.runner_telemetry import TelemetryMixin

    class Runner(TelemetryMixin):
        site_id = "row1056_dp13"

    def boom(*a, **k):
        raise ArithmeticError("series corrupted")

    monkeypatch.setattr(fresh_detector, "record_transfer_sample", boom)
    Runner().record_transfer_completion(5_000_000, 1.0)  # must not raise into the download
    assert fresh_detector.get_metrics_summary().get("ingest_failures") == 1, (
        "T71 DP-13: a dropped transfer sample left no count in the detector summary")


def test_t71_dp13_a_failing_listener_is_counted_and_the_next_still_runs():
    """T71 DP-13 (workload_bottleneck._emit_anomaly): a raising listener was swallowed silently."""
    detector = workload_bottleneck.WorkloadBottleneckDetector(min_samples_for_baseline=4)
    seen = []

    def broken(event):
        raise RuntimeError("listener down")

    detector.register_callback(broken)
    detector.register_callback(seen.append)
    for _ in range(5):
        detector.record_transfer_sample(10_000_000, 2.0, site_id="dp13")
    assert detector.record_transfer_sample(100, 10.0, site_id="dp13") is not None
    assert len(seen) == 1
    assert detector.get_metrics_summary().get("callback_failures") == 1, (
        "T71 DP-13: a failing anomaly listener left no count in the detector summary")


# --- T74 PR986 CI red (ORDERS-0086): the completion feed is optional on mixin hosts ---------------
# Runner mixins are also driven by lightweight hosts that do not compose TelemetryMixin (the
# test_row825 / test_row722 SPA stubs, the row 786/760/722 transport harnesses). The per-file
# completion paths must finish for them exactly as before, and a TelemetryMixin runner (SiteRunner)
# must still hand the detector one sample per completed file.

_SPA_PAGE_URL = "https://spa.example.invalid/scene/1056"
_SPA_FILE_URL = "https://cdn.example.invalid/row1056-spa.mp4"
_TRANSPORT_PAGE_URL = "https://members.example.invalid/scenes/1056"
_SPA_BYTES = 1_200_000        # over MIN_THROUGHPUT_SAMPLE_BYTES, so a fed sample always counts
_TRANSPORT_BYTES = 3_000_000


def _outcome(call):
    """('returned', value), or ('raised', 'Type: message') when the download path raised."""
    try:
        return ("returned", call())
    except Exception as exc:  # noqa: BLE001 -- any raise into the download is the defect; asserted below
        return ("raised", f"{type(exc).__name__}: {exc}")


def _spa_host_class(with_telemetry):
    from bulk_downloader.runner_extractors import ExtractorsMixin
    from bulk_downloader.runner_telemetry import TelemetryMixin

    class _SpaHost(ExtractorsMixin):
        """What _try_spa_api_media_extractor calls unguarded -- the row 722/825 stub shape."""
        site_id = "row1056_spa"

        def __init__(self, download_dir):
            self.config = {"name": "row1056_spa", "download_dir": str(download_dir),
                           "filename_template": "{filename}{ext}"}
            self.statuses = []

        def _update_job(self, url, status, message, **extra):
            self.statuses.append(status)

        def log_event(self, kind, message, url=None, extra=None):
            return None

        def _do_direct_http_download(self, page_url, file_url, output_path, referer=""):
            with open(output_path, "wb") as fh:
                fh.write(b"\0" * _SPA_BYTES)
            return True

        def _size_on_disk_after_tagging(self, path, fallback):
            return fallback

    if with_telemetry:
        return type("_SpaHostWithTelemetry", (_SpaHost, TelemetryMixin), {})
    return _SpaHost


def _drive_spa(runner, monkeypatch):
    from bulk_downloader import runner_extractors as rx

    history, published = [], []
    option = {"url": _SPA_FILE_URL, "height": 1080, "source": "api:downloadOptions"}
    monkeypatch.setattr("bulk_downloader.spa_media_extract.api_candidates",
                        lambda page_url, records: [dict(option)])
    monkeypatch.setattr("bulk_downloader.spa_media_extract.page_media_candidates",
                        lambda page_url, media: [])
    monkeypatch.setattr("bulk_downloader.spa_media_extract.rank_candidates", lambda cands: list(cands))
    monkeypatch.setattr("bulk_downloader.spa_media_extract.resolve_candidate_url",
                        lambda page, cand, headers: cand["url"])
    monkeypatch.setattr("bulk_downloader.events.publish_download_completion",
                        lambda config, **kw: published.append(kw["site_id"]))
    monkeypatch.setattr(rx, "db_log", lambda *a, **kw: history.append((a[3], kw.get("bytes_fetched"))))
    monkeypatch.setattr(rx, "history_title_kwargs", lambda *a, **kw: {})
    page = SimpleNamespace(url=_SPA_PAGE_URL, evaluate=lambda script: [])
    outcome = _outcome(lambda: runner._try_spa_api_media_extractor(_SPA_PAGE_URL, page))
    return outcome, history, published


class _Grant:
    url = "https://cdn.example.invalid/row1056-transport.mp4"
    suggested_filename = "row1056-transport.mp4"

    def cancel(self):
        return None


class _Trigger:
    def get_attribute(self, name):
        return "/download/1056" if name == "href" else None

    def click(self):
        return None


class _TransportPage:
    url = _TRANSPORT_PAGE_URL

    @contextmanager
    def expect_download(self, *, timeout):
        yield SimpleNamespace(value=_Grant())

    def title(self):
        return "Scene 1056"


def _transport_host_class(with_telemetry):
    from bulk_downloader.runner_telemetry import TelemetryMixin
    from bulk_downloader.runner_transport import TransportMixin

    class _TransportHost(TransportMixin):
        """A lightweight TransportMixin host (the row 786 harness shape), browser arm only."""
        site_id = "row1056_transport"

        def __init__(self):
            self.config = {"name": "row1056_transport", "use_http_dl": False,
                           "verify_hash": False, "verify_integrity": False}
            self._lock = threading.RLock()
            self._stop = threading.Event()
            self.jobs = {}
            self.log = logging.getLogger("row1056")
            self.statuses = []
            self.failures = []

        def _update_job(self, url, status, *args, **kwargs):
            self.statuses.append(status)

        def _handle_failure(self, url, message, *args, **kwargs):
            self.failures.append(message)

        def _size_on_disk_after_tagging(self, path, downloaded_size):
            return downloaded_size

        def _pw_save(self, download, final_path):
            return _TRANSPORT_BYTES, _TRANSPORT_BYTES

    if with_telemetry:
        return type("_TransportHostWithTelemetry", (_TransportHost, TelemetryMixin), {})
    return _TransportHost


def _drive_transport(runner, monkeypatch, download_dir):
    from bulk_downloader import runner_transport as transport

    history, published = [], []
    monkeypatch.setattr(transport, "db_skip_identity", lambda *a: ("different", ""))
    monkeypatch.setattr(transport, "db_log", lambda *a, **kw: history.append((a[3], kw.get("bytes_fetched"))))
    monkeypatch.setattr(transport.staging_claim, "reserve",
                        lambda path, identity: (path, path.with_suffix(".part")))
    monkeypatch.setattr(transport.staging_claim, "release", lambda *a: None)
    monkeypatch.setattr("bulk_downloader.events.publish_download_completion",
                        lambda config, **kw: published.append(kw["site_id"]))
    best = {"locator": _Trigger(), "score": 1080, "size": 0, "text": "Download"}
    outcome = _outcome(lambda: runner._do_download(
        _TransportPage(), object(), _TRANSPORT_PAGE_URL, best, Path(download_dir), "1080p"))
    return outcome, history, published


def test_t74_a_runner_without_telemetry_completes_the_spa_download(fresh_detector, monkeypatch, tmp_path):
    """T74 PR986 CI red (test_row825 DummyRunner): the SPA API completion must not raise into the
    download of a mixin host that has no TelemetryMixin; it completes and publishes exactly once."""
    runner = _spa_host_class(with_telemetry=False)(tmp_path)
    assert not hasattr(runner, "record_transfer_completion"), "fixture must lack the telemetry hook"

    outcome, history, published = _drive_spa(runner, monkeypatch)

    assert outcome == ("returned", True), (
        f"row1056: the SPA completion raised into the download of a runner without TelemetryMixin "
        f"after it had set {runner.statuses[-1:]} and written history {history}: {outcome[1]}")
    assert runner.statuses[-1] == "done"
    assert history == [("done", _SPA_BYTES)]
    assert published == ["row1056_spa"]
    assert fresh_detector.get_metrics_summary()["total_samples"] == 0  # no hook, no sample


def test_t74_a_runner_without_telemetry_completes_the_transport_download(fresh_detector, monkeypatch, tmp_path):
    """The same defect on the transport path: _do_download on a lightweight TransportMixin host."""
    runner = _transport_host_class(with_telemetry=False)()
    assert not hasattr(runner, "record_transfer_completion"), "fixture must lack the telemetry hook"

    outcome, history, published = _drive_transport(runner, monkeypatch, tmp_path)

    assert outcome == ("returned", None), (
        f"row1056: _do_download raised into the download of a runner without TelemetryMixin "
        f"after it had set {runner.statuses[-1:]} and written history {history}: {outcome[1]}")
    assert runner.failures == []
    assert runner.statuses[-1] == "done"
    assert history == [("done", _TRANSPORT_BYTES)]
    assert published == ["row1056_transport"]
    assert fresh_detector.get_metrics_summary()["total_samples"] == 0  # no hook, no sample


def test_t74_a_telemetry_runner_feeds_one_sample_per_completed_file(fresh_detector, monkeypatch, tmp_path):
    """Boarded behaviour kept: with TelemetryMixin composed (as SiteRunner composes it), each path
    hands the detector exactly one sample -- its wire bytes, a positive elapsed time, its site."""
    from bulk_downloader.runner import SiteRunner
    from bulk_downloader.runner_telemetry import TelemetryMixin

    assert SiteRunner.record_transfer_completion is TelemetryMixin.record_transfer_completion, (
        "SiteRunner must resolve the feed to TelemetryMixin's hook (no mixin may shadow it)")
    seen = []
    observe = fresh_detector.observe_completed_transfer

    def spy(bytes_fetched, duration_seconds, site_id=None):
        seen.append((site_id, bytes_fetched, duration_seconds))
        return observe(bytes_fetched, duration_seconds, site_id=site_id)

    monkeypatch.setattr(fresh_detector, "observe_completed_transfer", spy)

    (tmp_path / "spa").mkdir()
    (tmp_path / "transport").mkdir()
    spa = _spa_host_class(with_telemetry=True)(tmp_path / "spa")
    spa_outcome, spa_history, _ = _drive_spa(spa, monkeypatch)
    transport = _transport_host_class(with_telemetry=True)()
    transport_outcome, transport_history, _ = _drive_transport(transport, monkeypatch, tmp_path / "transport")

    assert (spa_outcome, transport_outcome) == (("returned", True), ("returned", None))
    assert (spa_history, transport_history) == ([("done", _SPA_BYTES)], [("done", _TRANSPORT_BYTES)])
    assert [(site, fetched) for site, fetched, _ in seen] == [
        ("row1056_spa", _SPA_BYTES), ("row1056_transport", _TRANSPORT_BYTES)], seen
    assert all(0 < seconds < 60 for _, _, seconds in seen), seen
    summary = fresh_detector.get_metrics_summary()
    assert summary["total_samples"] == 2
    # (label, count) pairs, not a {label: count} literal: a count-dict assert is a PIN_INDEX.json
    # pin, and this fixture-local count is not one of the product counts that index tracks.
    assert sorted((label, series["count"]) for label, series in summary["metrics"].items()) == [
        ("transfer_throughput_bps@row1056_spa", 1), ("transfer_throughput_bps@row1056_transport", 1)]
