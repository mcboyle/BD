"""Row 1061: Zero-Overhead Durable Distributed Trace Spans with OpenTelemetry Protocol.

Implements lightweight OpenTelemetry-compliant trace spans, W3C traceparent propagation,
bounded ring buffer durability, and OTLP v1 canonical payload export.
"""

from __future__ import annotations

import collections
import contextlib
import enum
import json
import logging
import re
import secrets
import threading
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)


class SpanKind(str, enum.Enum):
    INTERNAL = "INTERNAL"
    SERVER = "SERVER"
    CLIENT = "CLIENT"
    PRODUCER = "PRODUCER"
    CONSUMER = "CONSUMER"


class SpanStatusCode(str, enum.Enum):
    UNSET = "UNSET"
    OK = "OK"
    ERROR = "ERROR"


def _new_trace_id() -> str:
    return secrets.token_hex(16)


def _new_span_id() -> str:
    return secrets.token_hex(8)


class BaseSpan:
    trace_id: str = "0" * 32
    span_id: str = "0" * 16
    name: str = ""
    start_time_unix_nano: int = 0
    end_time_unix_nano: int = 0
    attributes: Dict[str, Any] = {}
    events: List[Dict[str, Any]] = []
    status: Dict[str, Any] = {"code": SpanStatusCode.UNSET.value, "message": ""}

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        pass

    def set_status(self, code: SpanStatusCode, message: str = "") -> None:
        pass

    def end(self) -> None:
        pass

    @property
    def duration_nano(self) -> int:
        return 0

    def __enter__(self) -> BaseSpan:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


class NoOpSpan(BaseSpan):
    """Zero-overhead dummy span returned when tracing is disabled."""

    def __init__(self):
        self.attributes = {}
        self.events = []
        self.status = {"code": SpanStatusCode.UNSET.value, "message": ""}


class OTLPTraceSpan(BaseSpan):
    """OpenTelemetry-compliant distributed trace span."""

    def __init__(
        self,
        name: str,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Optional[Dict[str, Any]] = None,
        tracer: Optional[OpenTelemetryTracer] = None,
    ):
        self.name = name
        self.trace_id = trace_id or _new_trace_id()
        self.span_id = span_id or _new_span_id()
        self.parent_span_id = parent_span_id
        self.kind = kind
        self.tracer = tracer

        self.start_time_unix_nano = time.time_ns()
        self.end_time_unix_nano = 0
        self.attributes = dict(attributes or {})
        self.events = []
        self.status = {"code": SpanStatusCode.UNSET.value, "message": ""}
        self._ended = False

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        self.events.append({
            "name": name,
            "time_unix_nano": time.time_ns(),
            "attributes": dict(attributes or {}),
        })

    def set_status(self, code: SpanStatusCode, message: str = "") -> None:
        self.status = {"code": code.value, "message": message}

    def end(self) -> None:
        if not self._ended:
            self.end_time_unix_nano = time.time_ns()
            self._ended = True
            if self.tracer and self.tracer.buffer:
                self.tracer.buffer.append(self)

    @property
    def duration_nano(self) -> int:
        if self.end_time_unix_nano > 0:
            return self.end_time_unix_nano - self.start_time_unix_nano
        return time.time_ns() - self.start_time_unix_nano

    def __enter__(self) -> OTLPTraceSpan:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None:
            self.set_status(SpanStatusCode.ERROR, str(exc_val))
            self.set_attribute("error.type", exc_type.__name__)
        self.end()


def inject_traceparent(span: BaseSpan) -> str:
    """Format span context into W3C traceparent header string."""
    return f"00-{span.trace_id}-{span.span_id}-01"


_TRACEPARENT_RE = re.compile(r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})(-.*)?$")


def extract_traceparent(header: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Parse W3C traceparent header into (trace_id, parent_span_id).

    A missing or malformed header is treated as absent (W3C Trace Context):
    (None, None), so the caller starts a new root span instead of claiming a
    parent that names no span.
    """
    if not isinstance(header, str):
        return None, None
    m = _TRACEPARENT_RE.match(header.strip())
    if not m:
        return None, None
    version, trace_id, span_id, _flags, rest = m.groups()
    if version == "ff" or (version == "00" and rest) or trace_id == "0" * 32 or span_id == "0" * 16:
        return None, None
    return trace_id, span_id


class DurableSpanBuffer:
    """Thread-safe bounded ring buffer for durable trace retention."""

    def __init__(self, max_capacity: int = 1000):
        self.max_capacity = max_capacity
        self._deque: collections.deque[OTLPTraceSpan] = collections.deque(maxlen=max_capacity)
        self._lock = threading.Lock()

    def append(self, span: OTLPTraceSpan) -> None:
        with self._lock:
            self._deque.append(span)

    def get_spans(self) -> List[OTLPTraceSpan]:
        with self._lock:
            return list(self._deque)

    def clear(self) -> None:
        with self._lock:
            self._deque.clear()

    def flush_to_file(self, filepath: str) -> int:
        spans = self.get_spans()
        payload = export_otlp_payload(spans)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        return len(spans)


class OpenTelemetryTracer:
    """Zero-overhead tracer with optional durable buffering."""

    def __init__(
        self,
        enabled: bool = True,
        buffer: Optional[DurableSpanBuffer] = None,
    ):
        self.enabled = enabled
        self.buffer = buffer or DurableSpanBuffer()

    def start_span(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        parent_span: Optional[BaseSpan] = None,
        trace_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> BaseSpan:
        if not self.enabled:
            return NoOpSpan()

        tid = trace_id or (parent_span.trace_id if parent_span else None)
        pid = parent_span_id or (parent_span.span_id if parent_span else None)

        return OTLPTraceSpan(
            name=name,
            trace_id=tid,
            parent_span_id=pid,
            kind=kind,
            attributes=attributes,
            tracer=self,
        )


def _attr_to_otlp(val: Any) -> Dict[str, Any]:
    if isinstance(val, bool):
        return {"boolValue": val}
    if isinstance(val, int):
        return {"intValue": str(val)}
    if isinstance(val, float):
        return {"doubleValue": val}
    return {"stringValue": str(val)}


def export_otlp_payload(
    spans: List[OTLPTraceSpan],
    service_name: str = "BulkDownloader",
) -> Dict[str, Any]:
    """Serialize trace spans to canonical OTLP v1 JSON format."""
    otlp_spans = []
    for s in spans:
        span_dict: Dict[str, Any] = {
            "traceId": s.trace_id,
            "spanId": s.span_id,
            "name": s.name,
            "kind": s.kind.value if isinstance(s.kind, SpanKind) else str(s.kind),
            "startTimeUnixNano": str(s.start_time_unix_nano),
            "endTimeUnixNano": str(s.end_time_unix_nano),
            "attributes": [{"key": k, "value": _attr_to_otlp(v)} for k, v in s.attributes.items()],
            "status": s.status,
        }
        if s.parent_span_id:
            span_dict["parentSpanId"] = s.parent_span_id
        if s.events:
            span_dict["events"] = [
                {
                    "name": ev["name"],
                    "timeUnixNano": str(ev["time_unix_nano"]),
                    "attributes": [{"key": k, "value": _attr_to_otlp(v)} for k, v in ev["attributes"].items()],
                }
                for ev in s.events
            ]
        otlp_spans.append(span_dict)

    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": service_name}}
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "bulk_downloader.tracer", "version": "1.0.0"},
                        "spans": otlp_spans,
                    }
                ],
            }
        ]
    }


_GLOBAL_TRACER: Optional[OpenTelemetryTracer] = None


def get_tracer() -> OpenTelemetryTracer:
    global _GLOBAL_TRACER
    if _GLOBAL_TRACER is None:
        _GLOBAL_TRACER = OpenTelemetryTracer()
    return _GLOBAL_TRACER


@contextlib.contextmanager
def trace_span(
    name: str,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: Optional[Dict[str, Any]] = None,
) -> Iterator[BaseSpan]:
    tracer = get_tracer()
    with tracer.start_span(name, kind=kind, attributes=attributes) as span:
        yield span
