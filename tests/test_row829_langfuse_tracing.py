"""tests/test_row829_langfuse_tracing.py -- Row 829 Langfuse LLM Observability Hook tests.

Acceptance:
  (1) Trace payload schema conformance (prompt tokens, completion tokens, latency, model ID, error category).
  (2) Non-blocking execution (tracing timeouts add 0ms to extractor latency).
  (3) Graceful no-op when endpoint is down.
  (4) Zero impact when LANGFUSE_HOST is not configured.
"""

BD_GATE_SCOPE = "module"

import os
import threading
import time
import pytest
from unittest.mock import patch, MagicMock

from bulk_downloader import aiassist
from bulk_downloader import hooks as aiassist_hooks


@pytest.fixture(autouse=True)
def clean_langfuse_state(monkeypatch):
    """Ensure clean tracing state before and after each test."""
    if hasattr(aiassist, "_reset_tracing"):
        aiassist._reset_tracing()
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    yield
    if hasattr(aiassist, "_reset_tracing"):
        aiassist._reset_tracing()


def test_langfuse_trace_payload_schema_conformance(monkeypatch):
    """(1) Trace payload schema conformance:
    Verifies trace span carries prompt_tokens, completion_tokens, latency, model ID, error category,
    and conforms to standard Langfuse ingestion format.
    """
    assert hasattr(aiassist, "build_trace_payload"), "aiassist must expose build_trace_payload"

    payload = aiassist.build_trace_payload(
        kind="suggest",
        model="qwen2.5vl:7b",
        prompt_tokens=150,
        completion_tokens=42,
        latency_ms=1250.5,
        ok=True,
        error_category=""
    )

    # Top-level span schema
    assert isinstance(payload, dict)
    assert payload["model"] == "qwen2.5vl:7b"
    assert payload["prompt_tokens"] == 150
    assert payload["completion_tokens"] == 42
    assert payload["latency_ms"] == 1250.5
    assert payload["ok"] is True
    assert payload["error_category"] is None or payload["error_category"] == ""
    assert "timestamp" in payload
    assert "trace_id" in payload

    # Langfuse batch ingestion schema
    assert "batch" in payload
    assert isinstance(payload["batch"], list)
    assert len(payload["batch"]) >= 2  # trace-create + generation-create

    trace_event = next(e for e in payload["batch"] if e.get("type") == "trace-create")
    assert trace_event["body"]["id"] == payload["trace_id"]
    assert "suggest" in trace_event["body"]["name"]

    gen_event = next(e for e in payload["batch"] if e.get("type") == "generation-create")
    gen_body = gen_event["body"]
    assert gen_body["traceId"] == payload["trace_id"]
    assert gen_body["model"] == "qwen2.5vl:7b"
    assert gen_body["usage"]["promptTokens"] == 150
    assert gen_body["usage"]["completionTokens"] == 42
    assert gen_body["usage"]["totalTokens"] == 192
    assert gen_body["level"] == "DEFAULT"

    # Error case schema conformance
    err_payload = aiassist.build_trace_payload(
        kind="classify",
        model="qwen2.5:7b",
        prompt_tokens=80,
        completion_tokens=0,
        latency_ms=300.0,
        ok=False,
        error_category="rate_limit"
    )
    assert err_payload["ok"] is False
    assert err_payload["error_category"] == "rate_limit"
    err_gen = next(e for e in err_payload["batch"] if e.get("type") == "generation-create")
    assert err_gen["body"]["level"] == "ERROR"
    assert err_gen["body"]["statusMessage"] == "rate_limit"


def test_langfuse_non_blocking_execution(monkeypatch):
    """(2) Non-blocking execution:
    Tracing timeouts or slow network must add 0ms to caller latency (<5ms queue overhead).
    """
    assert hasattr(aiassist, "record_trace"), "aiassist must expose record_trace"
    # a fixture host: were the opener NOT intercepted below, nothing on the
    # network would be reached (no LAN egress from a unit test)
    monkeypatch.setenv("LANGFUSE_HOST", "http://langfuse.fixture.invalid:3002")

    # Simulate a network call that never returns until the test releases it:
    # a synchronous caller would block here, a non-blocking one returns at once.
    # (An Event, not a sleep: deterministic and released explicitly below -- T4.)
    release = threading.Event()

    def slow_urlopen(*args, **kwargs):
        release.wait()
        raise TimeoutError("connection timed out")

    with patch.object(aiassist_hooks, "_hook_urlopen", side_effect=slow_urlopen) as opener:
        t0 = time.perf_counter()
        # Enqueue 10 traces
        for i in range(10):
            aiassist.record_trace(
                kind="suggest",
                model="qwen2.5vl:7b",
                prompt_tokens=100 + i,
                completion_tokens=20,
                latency_ms=500.0,
                ok=True
            )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        # Must execute virtually instantaneously on the caller thread
        assert elapsed_ms < 50.0, f"record_trace took {elapsed_ms:.2f}ms on caller thread (must be non-blocking)"
        release.set()  # let the background sender finish its (failing) calls
        # drain ALL 10 while the opener is still intercepted: the worker must
        # never reach a real socket after this block ends (correctness E1)
        aiassist.flush_traces(timeout=5.0)
        assert opener.call_count == 10, opener.call_count
        assert aiassist._trace_queue.unfinished_tasks == 0


def test_langfuse_graceful_noop_when_endpoint_down(monkeypatch):
    """(3) Graceful no-op when endpoint is down:
    Connection refused or HTTP 500 does not raise or fail callers.
    """
    assert hasattr(aiassist, "record_trace"), "aiassist must expose record_trace"
    assert hasattr(aiassist, "flush_traces"), "aiassist must expose flush_traces"

    # Dead loopback port
    monkeypatch.setenv("LANGFUSE_HOST", "http://127.0.0.1:59999")

    # Emitting trace must not raise
    aiassist.record_trace(
        kind="classify",
        model="qwen2.5:7b",
        prompt_tokens=50,
        completion_tokens=10,
        latency_ms=100.0,
        ok=True
    )

    # Flushes without raising exception
    aiassist.flush_traces(timeout=0.5)


def test_langfuse_noop_when_unconfigured(monkeypatch):
    """(4) Graceful zero-overhead no-op when LANGFUSE_HOST is not set."""
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    assert hasattr(aiassist, "record_trace"), "aiassist must expose record_trace"

    # No error, no background network calls: the opener the sender really
    # uses (hooks._hook_urlopen) is the one intercepted -- a bare
    # urllib.request.urlopen patch can never fail (correctness E5).
    with patch.object(aiassist_hooks, "_hook_urlopen") as opener:
        aiassist.record_trace(
            kind="suggest",
            model="test:model",
            prompt_tokens=10,
            completion_tokens=5,
            latency_ms=50.0,
            ok=True
        )
        aiassist.flush_traces(timeout=0.2)
        opener.assert_not_called()
    # zero overhead: no queue and no worker thread were ever created
    assert aiassist._trace_queue is None
    assert aiassist._trace_worker_thread is None


def test_aiassist_call_model_dispatches_trace(monkeypatch):
    """Integration: _call_model automatically enqueues a Langfuse trace when configured."""
    monkeypatch.setenv("LANGFUSE_HOST", "http://langfuse.fixture.invalid:3002")

    from bulk_downloader.ai_provider import GenerationResult

    fake_result = GenerationResult(
        ok=True,
        text='{"selectors": [".video-title"]}',
        model="qwen2.5vl:7b",
        provider="ollama",
        latency_ms=450,
        prompt_tokens=120,
        completion_tokens=30
    )

    traces_recorded = []

    def mock_record(*args, **kwargs):
        traces_recorded.append((args, kwargs))

    with patch.object(aiassist, "_get_provider") as mock_prov_getter, \
         patch.object(aiassist, "record_trace", side_effect=mock_record):
        prov = MagicMock()
        prov.generate.return_value = fake_result
        mock_prov_getter.return_value = prov

        res = aiassist._call_model("test prompt")
        assert res.ok is True
        assert len(traces_recorded) == 1
        kwargs = traces_recorded[0][1]
        assert kwargs.get("model") == "qwen2.5vl:7b"
        assert kwargs.get("prompt_tokens") == 120
        assert kwargs.get("completion_tokens") == 30


# ---- fixer (O928) controls: correctness REFUTE E1 / E3 ----

def test_generation_carries_start_and_end_timestamps_spanning_latency():
    """E3: Langfuse derives span duration from startTime/endTime (ISO-8601),
    not from a decorative latency float."""
    from datetime import datetime
    payload = aiassist.build_trace_payload(kind="suggest", model="m", latency_ms=1500.0)
    gen = next(e for e in payload["batch"] if e["type"] == "generation-create")["body"]
    start, end = datetime.fromisoformat(gen["startTime"]), datetime.fromisoformat(gen["endTime"])
    assert start.tzinfo is not None and end.tzinfo is not None
    assert abs((end - start).total_seconds() - 1.5) < 1e-6
    assert gen["endTime"] == payload["timestamp"]


def test_sender_uses_the_pinned_redirect_guarded_opener(monkeypatch):
    """E1 companion: the sender never calls a bare urlopen -- a bare opener
    would follow a redirect off the configured host and would also be what a
    test's patch of hooks._hook_urlopen fails to intercept."""
    seen = []

    def fake_open(req, timeout=15):
        seen.append((req.full_url, timeout))
        raise OSError("fixture: no network")

    monkeypatch.setattr(aiassist_hooks, "_hook_urlopen", fake_open)
    monkeypatch.setattr(aiassist, "urlopen", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("bare urlopen reached")))
    monkeypatch.setenv("LANGFUSE_HOST", "http://langfuse.fixture.invalid:3002")
    monkeypatch.setenv("LANGFUSE_TIMEOUT", "0.7")
    aiassist._send_trace_batch(aiassist.build_trace_payload(kind="suggest"))
    assert seen == [("http://langfuse.fixture.invalid:3002/api/public/ingestion", 0.7)]


def test_flush_waits_for_in_flight_sends_not_just_an_empty_queue(monkeypatch):
    """E1: flush_traces() returned as soon as the queue was empty while the
    last item was still being sent; it now waits for task_done."""
    entered, release = threading.Event(), threading.Event()

    def slow_open(req, timeout=15):
        entered.set()
        release.wait(5)
        raise OSError("fixture")

    monkeypatch.setattr(aiassist_hooks, "_hook_urlopen", slow_open)
    monkeypatch.setenv("LANGFUSE_HOST", "http://langfuse.fixture.invalid:3002")
    aiassist.record_trace(kind="suggest", model="m")
    assert entered.wait(5)
    assert aiassist._trace_queue.empty()            # dequeued ...
    assert aiassist._trace_queue.unfinished_tasks == 1  # ... but in flight
    t0 = time.perf_counter()
    aiassist.flush_traces(timeout=0.3)
    assert time.perf_counter() - t0 >= 0.25        # waited, did not return early
    release.set()
    aiassist.flush_traces(timeout=5.0)
    assert aiassist._trace_queue.unfinished_tasks == 0
