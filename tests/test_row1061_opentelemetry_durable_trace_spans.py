"""Row 1061: Zero-Overhead Durable Distributed Trace Spans with OpenTelemetry Protocol.

Validates OpenTelemetry-compliant trace spans, W3C traceparent context propagation,
zero-overhead no-op bypass when disabled, bounded durable ring buffer storage,
and OTLP v1 canonical payload export.
"""

import importlib.util
import time
import pytest

BD_GATE_SCOPE = "repo-wide"


def _otel():
    # Probe with find_spec and fail OUTSIDE any except block, so the base RED is this
    # capability assertion, not a collection-time ModuleNotFoundError (bounce E3).
    if importlib.util.find_spec("bulk_downloader.otel_trace_spans") is None:
        pytest.fail("row1061: no trace-span capability (bulk_downloader.otel_trace_spans absent)")


def test_opentelemetry_spans_import_and_premise():
    """RED test: Verify OpenTelemetry tracer, span models, and buffers exist."""
    _otel()
    from bulk_downloader.otel_trace_spans import (
        OTLPTraceSpan,
        DurableSpanBuffer,
        OpenTelemetryTracer,
        SpanKind,
        SpanStatusCode,
        get_tracer,
        trace_span,
    )

    assert OTLPTraceSpan is not None
    assert DurableSpanBuffer is not None
    assert OpenTelemetryTracer is not None
    assert SpanKind is not None
    assert SpanStatusCode is not None
    assert callable(get_tracer)
    assert callable(trace_span)


def test_w3c_traceparent_propagation():
    """Verify standard W3C traceparent formatting and parsing."""
    _otel()
    from bulk_downloader.otel_trace_spans import (
        OpenTelemetryTracer,
        inject_traceparent,
        extract_traceparent,
    )

    tracer = OpenTelemetryTracer()
    with tracer.start_span("test.operation") as span:
        header = inject_traceparent(span)
        # Format: 00-<32 hex trace_id>-<16 hex span_id>-01
        parts = header.split("-")
        assert len(parts) == 4
        assert parts[0] == "00"
        assert len(parts[1]) == 32
        assert len(parts[2]) == 16
        assert parts[3] in ("00", "01")

        extracted_trace_id, extracted_span_id = extract_traceparent(header)
        assert extracted_trace_id == span.trace_id
        assert extracted_span_id == span.span_id


def test_trace_span_lifecycle_and_timing():
    """Verify span timestamps, durations, attributes, and status reporting."""
    _otel()
    from bulk_downloader.otel_trace_spans import OpenTelemetryTracer, SpanStatusCode, SpanKind

    tracer = OpenTelemetryTracer()
    with tracer.start_span("db.query", kind=SpanKind.CLIENT) as span:
        span.set_attribute("db.system", "sqlite")
        span.set_attribute("db.statement", "SELECT 1")
        span.add_event("cache_miss", {"reason": "cold_start"})
        time.sleep(0.01)
        span.set_status(SpanStatusCode.OK)

    assert span.duration_nano > 0
    assert span.end_time_unix_nano >= span.start_time_unix_nano
    assert span.attributes["db.system"] == "sqlite"
    assert len(span.events) == 1
    assert span.events[0]["name"] == "cache_miss"
    assert span.status["code"] == SpanStatusCode.OK.value


def test_zero_overhead_noop_when_disabled():
    """Verify zero-overhead NoOpSpan is returned when tracing is disabled."""
    _otel()
    from bulk_downloader.otel_trace_spans import OpenTelemetryTracer, NoOpSpan

    tracer = OpenTelemetryTracer(enabled=False)
    with tracer.start_span("fast.loop") as span:
        assert isinstance(span, NoOpSpan)
        span.set_attribute("key", "val")  # Should do nothing safely
        span.add_event("event", {})

    assert span.trace_id == "0" * 32
    assert span.span_id == "0" * 16


def test_durable_ring_buffer_bounded_capacity():
    """Verify DurableSpanBuffer caps memory usage and preserves newest spans."""
    _otel()
    from bulk_downloader.otel_trace_spans import DurableSpanBuffer, OpenTelemetryTracer

    buffer = DurableSpanBuffer(max_capacity=50)
    tracer = OpenTelemetryTracer(buffer=buffer)

    for i in range(120):
        with tracer.start_span(f"op_{i}"):
            pass

    spans = buffer.get_spans()
    assert len(spans) == 50
    # Newest spans survive (op_70 to op_119)
    assert spans[-1].name == "op_119"


def test_otlp_json_payload_compliance():
    """Verify canonical OTLP v1/traces payload structure export."""
    _otel()
    from bulk_downloader.otel_trace_spans import OpenTelemetryTracer, export_otlp_payload

    tracer = OpenTelemetryTracer()
    with tracer.start_span("http.request") as span:
        span.set_attribute("http.status_code", 200)

    payload = export_otlp_payload([span], service_name="BulkDownloader")
    assert "resourceSpans" in payload
    resource = payload["resourceSpans"][0]
    assert "resource" in resource
    assert "scopeSpans" in resource
    scope_spans = resource["scopeSpans"][0]["spans"]
    assert len(scope_spans) == 1
    exported_span = scope_spans[0]
    assert exported_span["name"] == "http.request"
    assert exported_span["traceId"] == span.trace_id
    assert exported_span["spanId"] == span.span_id


def test_runner_telemetry_caller_integration():
    """Verify SiteRunner TelemetryMixin caller exposes create_trace_span integration."""
    _otel()
    from bulk_downloader.runner_telemetry import TelemetryMixin
    from bulk_downloader.otel_trace_spans import SpanStatusCode

    class DummyRunner(TelemetryMixin):
        site_id = "test_site_42"

    runner = DummyRunner()
    span = runner.create_trace_span("download.chunk_fetch", attributes={"chunk_idx": 3})
    assert span is not None
    span.set_status(SpanStatusCode.OK)
    span.end()
    assert span.attributes["runner.site_id"] == "test_site_42"
    assert span.attributes["chunk_idx"] == 3



@pytest.mark.parametrize("header", [
    None, "", "garbage", "00-xyz", "00-" + "g" * 32 + "-" + "1" * 16 + "-01",
    "00-" + "0" * 32 + "-" + "1" * 16 + "-01",          # all-zero trace-id is invalid (W3C)
    "00-" + "a" * 32 + "-" + "0" * 16 + "-01",          # all-zero parent-id is invalid
    "ff-" + "a" * 32 + "-" + "1" * 16 + "-01",          # version ff is invalid
    "00-" + "a" * 32 + "-" + "1" * 16,                  # flags missing
])
def test_malformed_traceparent_is_treated_as_absent(header):
    """Bounce E2: a missing or malformed traceparent yields (None, None), never minted ids."""
    _otel()
    from bulk_downloader.otel_trace_spans import extract_traceparent
    try:
        got = extract_traceparent(header)
    except (AttributeError, TypeError, ValueError) as exc:  # pre-fix: None crashed on None.strip
        got = f"raised {exc!r}"
    assert got == (None, None)


def test_valid_traceparent_still_parses_and_absent_parent_starts_a_root():
    """Bounce E2 control: a valid header parses; an absent parent gives a root span with a fresh trace."""
    _otel()
    from bulk_downloader.otel_trace_spans import OpenTelemetryTracer, extract_traceparent
    tid, sid = "4bf92f3577b34da6a3ce929d0e0e4736", "00f067aa0ba902b7"
    assert extract_traceparent(f"00-{tid}-{sid}-01") == (tid, sid)
    t, p = extract_traceparent("garbage")
    with OpenTelemetryTracer().start_span("root", trace_id=t, parent_span_id=p) as span:
        pass
    assert span.parent_span_id is None and len(span.trace_id) == 32 and span.trace_id != "0" * 32


def test_child_span_inherits_trace_and_parent():
    """Bounce E3: a child started under a parent carries the parent's trace_id and span_id."""
    _otel()
    from bulk_downloader.otel_trace_spans import OpenTelemetryTracer
    tracer = OpenTelemetryTracer()
    with tracer.start_span("parent") as parent:
        with tracer.start_span("child", parent_span=parent) as child:
            pass
    assert child.trace_id == parent.trace_id
    assert child.parent_span_id == parent.span_id
    assert child.span_id != parent.span_id


def test_exception_inside_span_sets_error_status_and_still_ends():
    """Bounce E3: an exception leaving the span marks it ERROR, records the type, and ends it."""
    _otel()
    from bulk_downloader.otel_trace_spans import DurableSpanBuffer, OpenTelemetryTracer, SpanStatusCode
    buf = DurableSpanBuffer(max_capacity=4)
    tracer = OpenTelemetryTracer(buffer=buf)
    with pytest.raises(ValueError):
        with tracer.start_span("boom") as span:
            raise ValueError("bad chunk")
    assert span.status == {"code": SpanStatusCode.ERROR.value, "message": "bad chunk"}
    assert span.attributes["error.type"] == "ValueError"
    assert span.end_time_unix_nano > 0 and buf.get_spans() == [span]


def _worker_harness(monkeypatch, process_one, update_job=None):
    """A TelemetryMixin runner borrowing SiteRunner._process_worker_url, with a fresh global tracer."""
    import threading
    from bulk_downloader import otel_trace_spans as ots
    from bulk_downloader.runner import SiteRunner
    from bulk_downloader.runner_telemetry import TelemetryMixin

    buf = ots.DurableSpanBuffer(max_capacity=16)
    monkeypatch.setattr(ots, "_GLOBAL_TRACER", ots.OpenTelemetryTracer(buffer=buf))

    class Worker(TelemetryMixin):
        site_id = "site_1061"
        _WORKER_CLAIM_PROCESSED = SiteRunner._WORKER_CLAIM_PROCESSED
        _process_worker_url = SiteRunner._process_worker_url

        def __init__(self):
            self._worker_heartbeats_lock = threading.Lock()
            self._worker_url_generations, self._worker_current_urls = {}, {}

        def _claim_worker_item(self, worker_idx, url, run_generation=None):
            self._worker_current_urls[worker_idx] = url
            self._worker_url_generations[worker_idx] = run_generation
            return "claimed", run_generation

        def _update_job(self, *a, **k):
            if update_job is not None:
                update_job(*a)

        def _process_one(self, browser, url, persistent_ctx=None):
            process_one(url)

    return Worker(), buf


def test_per_url_download_path_records_a_span(monkeypatch):
    """Bounce E1: the real per-URL worker path creates and ends one span per URL."""
    _otel()
    from bulk_downloader.otel_trace_spans import SpanStatusCode
    worker, buf = _worker_harness(monkeypatch, lambda url: None)
    url = "https://media.example.test/v/1.mp4?token=SECRET-1061"
    assert worker._process_worker_url(0, None, url) == worker._WORKER_CLAIM_PROCESSED
    spans = buf.get_spans()
    assert [s.name for s in spans] == ["runner.process_url"], "row1061: no span on the per-URL download path"
    span = spans[0]
    assert span.attributes["runner.site_id"] == "site_1061"
    assert span.attributes["server.address"] == "media.example.test"
    assert span.end_time_unix_nano > 0 and span.status["code"] != SpanStatusCode.ERROR.value
    # the URL's query (tokens) never lands in a span attribute
    assert not any("SECRET-1061" in str(v) for v in span.attributes.values())


def test_per_url_failure_marks_the_span_error_and_propagates(monkeypatch):
    """Bounce E1: a failing URL leaves an ERROR span and the exception still reaches the caller."""
    _otel()
    from bulk_downloader.otel_trace_spans import SpanStatusCode

    def boom(url):
        raise RuntimeError("transport reset")

    worker, buf = _worker_harness(monkeypatch, boom)
    with pytest.raises(RuntimeError, match="^transport reset$"):
        worker._process_worker_url(0, None, "https://media.example.test/v/2.mp4")
    assert [(s.name, s.status, s.attributes.get("error.type"), s.end_time_unix_nano > 0)
            for s in buf.get_spans()] == [
        ("runner.process_url", {"code": SpanStatusCode.ERROR.value, "message": "transport reset"},
         "RuntimeError", True)]
    assert worker._worker_current_urls == {} and worker._worker_url_generations == {}


def test_a_failure_before_the_span_starts_records_nothing_and_still_propagates(monkeypatch):
    """The claim-transition publication fails before any span exists: the exception reaches
    the caller unchanged, _process_one never runs, no span is recorded, the slot is released."""
    _otel()
    processed = []

    def failing_publication(*_args):
        raise RuntimeError("transition publication exploded")

    worker, buf = _worker_harness(monkeypatch, processed.append, update_job=failing_publication)
    with pytest.raises(RuntimeError, match="^transition publication exploded$"):
        worker._process_worker_url(0, None, "https://media.example.test/v/4.mp4")
    assert processed == [] and buf.get_spans() == []
    assert worker._worker_current_urls == {} and worker._worker_url_generations == {}


def test_a_url_whose_host_does_not_parse_is_still_processed_with_an_empty_host(monkeypatch):
    """Self-lens S1: urlparse(url).hostname raises ValueError for a malformed bracketed host.
    The span must record "" and hand the URL to _process_one exactly as base does -- the
    span is never the reason a URL fails."""
    _otel()
    from urllib.parse import urlparse
    url = "https://[media.example.test/v/3.mp4"
    with pytest.raises(ValueError):  # precondition: this URL's host really does not parse
        _ = urlparse(url).hostname
    seen = []
    worker, buf = _worker_harness(monkeypatch, seen.append)
    try:
        result = worker._process_worker_url(0, None, url)
    except ValueError as exc:  # pre-fix: the span's host parse failed the URL
        result = f"raised {exc!r}"
    assert (result, seen) == (worker._WORKER_CLAIM_PROCESSED, [url])
    assert [(s.name, s.attributes, s.status["code"]) for s in buf.get_spans()] == [
        ("runner.process_url", {"server.address": "", "runner.site_id": "site_1061"}, "UNSET")]


# T70 CI red (ORDERS-0067): tests/test_row664_dedup_refusal_reaches_history.py drives
# SiteRunner._process_worker_url on a runner double WITHOUT TelemetryMixin, so the span
# wrapper's `self.create_trace_span` raised AttributeError there. The double below has
# the same shape; the pair of tests pins both sides of the guard with values.
_DEDUP_SITE_ID = "row1061-dedup"
_DEDUP_SITE_NAME = "Row 1061 Dedup"
_DEDUP_URL = "https://media.example.test/v/already-downloaded.mp4?token=SECRET-1061"
_DEDUP_REASON = "Duplicate of history row 1061"
_DEDUP_GENERATION = 61


def _drive_dedup_refusal(monkeypatch, tmp_path, with_telemetry):
    """Claim -> SiteRunner._process_one -> dedup refusal -> history, on an isolated DB and tracer."""
    _otel()
    import threading
    from bulk_downloader import db
    from bulk_downloader import otel_trace_spans as ots
    from bulk_downloader.runner import SiteRunner
    from bulk_downloader.runner_telemetry import TelemetryMixin

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "row1061.sqlite3"))
    db.db_init()
    assert db.db_search(site_id=_DEDUP_SITE_ID) == []  # precondition: empty isolated history
    buf = ots.DurableSpanBuffer(max_capacity=8)
    monkeypatch.setattr(ots, "_GLOBAL_TRACER", ots.OpenTelemetryTracer(buffer=buf))

    class DedupRefusalRunner:
        site_id = _DEDUP_SITE_ID
        config = {"name": _DEDUP_SITE_NAME}
        _WORKER_CLAIM_PROCESSED = SiteRunner._WORKER_CLAIM_PROCESSED

        def __init__(self):
            self._lock = threading.Lock()
            self._worker_heartbeats_lock = threading.Lock()
            self._worker_url_generations = {0: _DEDUP_GENERATION}
            self._worker_current_urls = {0: _DEDUP_URL}
            self.jobs = {_DEDUP_URL: {"status": "pending", "message": ""}}
            self.updates = []

        def _claim_worker_item(self, worker_idx, url, run_generation=None):
            return "claimed", _DEDUP_GENERATION

        def _update_job(self, url, status, message, **_kwargs):
            self.updates.append((url, status, message))
            self.jobs[url].update(status=status, message=message)

        def _process_one(self, browser, url, persistent_ctx=None):
            return SiteRunner._process_one(self, browser, url, persistent_ctx)

        def _dedup_preflight(self, url, job):
            return _DEDUP_REASON

    # The double's own methods come first in the MRO; the mixin only adds create_trace_span et al.
    cls = (type("TelemetryDedupRefusalRunner", (DedupRefusalRunner, TelemetryMixin), {})
           if with_telemetry else DedupRefusalRunner)
    subject = cls()
    assert hasattr(subject, "create_trace_span") is with_telemetry  # precondition: the double's shape
    try:
        result = SiteRunner._process_worker_url(
            subject, 0, object(), _DEDUP_URL, persistent_ctx=object(),
            run_generation=_DEDUP_GENERATION)
    except AttributeError as exc:  # the T70 red: the worker path demanded create_trace_span
        result = f"raised {exc!r}"
    rows = db.db_search(site_id=_DEDUP_SITE_ID, status="skipped_duplicate")
    return subject, result, rows, buf.get_spans()


def _assert_base_dedup_outcome(subject, result, rows):
    assert result == subject._WORKER_CLAIM_PROCESSED
    assert subject.updates == [
        (_DEDUP_URL, "running", "Claimed by worker"),
        (_DEDUP_URL, "skipped_duplicate", _DEDUP_REASON),
    ]
    assert subject.jobs[_DEDUP_URL] == {"status": "skipped_duplicate", "message": _DEDUP_REASON}
    assert [(r["site_name"], r["url"], r["message"]) for r in rows] == [
        (_DEDUP_SITE_NAME, _DEDUP_URL, _DEDUP_REASON)]
    assert subject._worker_current_urls == {} and subject._worker_url_generations == {}


def test_dedup_refusal_on_a_runner_without_telemetry_is_unchanged_and_records_no_span(
        monkeypatch, tmp_path):
    """T70 red: a runner without create_trace_span reaches the dedup/history path exactly as on
    base (one skipped_duplicate history row carrying the reason) and no span is recorded."""
    subject, result, rows, spans = _drive_dedup_refusal(monkeypatch, tmp_path, with_telemetry=False)
    _assert_base_dedup_outcome(subject, result, rows)
    assert spans == []


def test_dedup_refusal_on_a_telemetry_runner_is_unchanged_and_records_one_span(
        monkeypatch, tmp_path):
    """The guard's other side: a runner WITH TelemetryMixin gets the same outcome plus exactly one
    ended runner.process_url span (host only -- the token in the query never reaches it)."""
    subject, result, rows, spans = _drive_dedup_refusal(monkeypatch, tmp_path, with_telemetry=True)
    _assert_base_dedup_outcome(subject, result, rows)
    assert [(s.name, s.attributes, s.status, s.parent_span_id) for s in spans] == [
        ("runner.process_url",
         {"server.address": "media.example.test", "runner.site_id": _DEDUP_SITE_ID},
         {"code": "UNSET", "message": ""}, None)]
    assert spans[0].end_time_unix_nano >= spans[0].start_time_unix_nano > 0


def test_the_worker_dispatch_line_keeps_the_shape_the_row664_mutant_anchor_resolves_on():
    """T70 red 2 (test_row532: "2 python-subject anchors did not resolve"): wrapping the dispatch
    in a `with` re-indented it, so row664's M2 anchor (both specs) matched nothing. Each must
    resolve exactly once, inside SiteRunner._process_worker_url."""
    import ast
    import json
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parents[1]
    source = (root / "bulk_downloader" / "runner.py").read_text(encoding="utf-8")
    fn = next(node for node in ast.walk(ast.parse(source))
              if isinstance(node, ast.FunctionDef) and node.name == "_process_worker_url")
    anchors = []
    for spec in ("row664_dedup_refusal_history.json",
                 "row664_dedup_refusal_history_transform_control.json"):
        doc = json.loads((root / "tests" / "mutants" / spec).read_text(encoding="utf-8"))
        anchors += [m["old_regex"] for m in doc["mutants"]
                    if m.get("file") == "bulk_downloader/runner.py"
                    and "_process_one" in m.get("old_regex", "")]
    assert len(anchors) == 2  # precondition: both row664 specs still carry the dispatch anchor
    for rx in anchors:
        lines = [source.count("\n", 0, m.start()) + 1 for m in re.finditer(rx, source)]
        assert len(lines) == 1 and fn.lineno <= lines[0] <= fn.end_lineno, (
            f"row664 dispatch anchor resolves at runner.py lines {lines}; "
            f"_process_worker_url spans {fn.lineno}-{fn.end_lineno}")
