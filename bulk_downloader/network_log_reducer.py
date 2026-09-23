"""bulk_downloader.network_log_reducer -- Structured Network Event Log Reduction & Trace Archival.

Phase 1078: High-throughput log reduction, repetitive segment/request compaction,
error and latency-outlier preservation, and structured trace archival.
"""
from __future__ import annotations

import collections
import dataclasses
import gzip
import json
import logging
from pathlib import Path
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

from .capture_artifact_redact import redact_value
from .capture_redact import PLACEHOLDER, SENSITIVE_HEADER, redact_media_url, scrub_headers

logger = logging.getLogger("bulk_downloader.network_log_reducer")

# A URL embedded in free text: scheme://... up to whitespace, a quote, or a bracket.
_EMBEDDED_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s\"'<>()\[\]]+")
# "Name: value" as a header is quoted in prose; the value runs to ';', a newline or the end.
_QUOTED_HEADER_RE = re.compile(r"(?<![A-Za-z0-9-])(?P<name>[A-Za-z][A-Za-z0-9-]*)[ \t]*:[ \t]+(?P<value>[^;\r\n]+)")


def _redact_text(text: Any) -> Any:
    """Redact credentials in a free-text field (an error string, a URL-valued header).

    redact_media_url treats its input as ONE URL: on prose it keeps nothing after
    the first URL's query and misses userinfo of a URL that is not at the start.
    So each embedded URL is redacted on its own, the prose between them goes
    through the free-string floor (userinfo, key=secret, JWT), and a credential
    header quoted as "Authorization: Bearer ..." loses its value.
    """
    if not isinstance(text, str) or not text:
        return text
    parts: List[str] = []
    pos = 0
    for m in _EMBEDDED_URL_RE.finditer(text):
        parts.append(redact_value(text[pos:m.start()]))
        parts.append(redact_media_url(m.group(0)))
        pos = m.end()
    parts.append(redact_value(text[pos:]))
    return _QUOTED_HEADER_RE.sub(
        lambda m: (f"{m.group('name')}: {PLACEHOLDER}"
                   if SENSITIVE_HEADER.search(m.group("name")) else m.group(0)),
        "".join(parts))


@dataclasses.dataclass
class NetworkEvent:
    """Normalized representation of a single network log event."""
    url: str
    method: str = "GET"
    status_code: int = 200
    duration_ms: float = 0.0
    bytes_transferred: int = 0
    content_type: str = ""
    timestamp: float = dataclasses.field(default_factory=time.time)
    error: Optional[str] = None
    headers: Dict[str, str] = dataclasses.field(default_factory=dict)
    trace_id: Optional[str] = None

    @property
    def is_error(self) -> bool:
        return bool(self.error) or (self.status_code >= 400)

    def to_dict(self) -> Dict[str, Any]:
        """Serialized form written to trace archives: credential-bearing URL
        parts and header values are redacted (capture_redact floor)."""
        return {
            "url": redact_media_url(self.url),
            "method": self.method,
            "status_code": self.status_code,
            "duration_ms": self.duration_ms,
            "bytes_transferred": self.bytes_transferred,
            "content_type": self.content_type,
            "timestamp": self.timestamp,
            "error": _redact_text(self.error),
            "headers": {k: _redact_text(v) for k, v in scrub_headers(dict(self.headers)).items()},
            "trace_id": self.trace_id,
        }


def normalize_network_event(raw: Any) -> NetworkEvent:
    """Convert heterogeneous raw dict or existing NetworkEvent to canonical NetworkEvent."""
    if isinstance(raw, NetworkEvent):
        return raw

    if not isinstance(raw, dict):
        return NetworkEvent(url=str(raw))

    url = str(raw.get("url") or "")
    method = str(raw.get("method") or "GET").upper()

    status = raw.get("response_status")
    if status is None:
        status = raw.get("status_code", raw.get("status", 200))
    try:
        status_code = int(status)
    except (ValueError, TypeError):
        status_code = 200

    duration = raw.get("elapsed_ms")
    if duration is None:
        duration = raw.get("duration_ms", raw.get("duration", 0.0))
    try:
        duration_ms = float(duration)
    except (ValueError, TypeError):
        duration_ms = 0.0

    bytes_val = raw.get("bytes")
    if bytes_val is None:
        bytes_val = raw.get("bytes_transferred", raw.get("size", 0))
    try:
        bytes_transferred = int(bytes_val)
    except (ValueError, TypeError):
        bytes_transferred = 0

    content_type = str(raw.get("content_type") or raw.get("mime_type") or "")
    ts = raw.get("timestamp") or time.time()
    try:
        timestamp = float(ts)
    except (ValueError, TypeError):
        timestamp = time.time()

    error = raw.get("error")
    if error is not None:
        error = str(error)

    headers = raw.get("headers") or {}
    if not isinstance(headers, dict):
        headers = {}

    trace_id = raw.get("trace_id")
    if trace_id is not None:
        trace_id = str(trace_id)

    return NetworkEvent(
        url=url,
        method=method,
        status_code=status_code,
        duration_ms=duration_ms,
        bytes_transferred=bytes_transferred,
        content_type=content_type,
        timestamp=timestamp,
        error=error,
        headers=headers,
        trace_id=trace_id,
    )




def extract_url_pattern(url: str) -> str:
    """Normalize and sanitize a URL into an aggregate pattern template."""
    if not url:
        return ""
    # Credential redaction is owned by capture_redact (query secrets, userinfo,
    # signed path segments); this function only generalizes what is left.
    safe = redact_media_url(url)
    try:
        parsed = urlparse(safe)
    except ValueError:
        return safe

    # Replace numeric sequences in path with generalized token
    path_pattern = re.sub(r"\d+", "<seq>", parsed.path)
    return urlunparse((parsed.scheme, parsed.netloc, path_pattern, "", parsed.query, ""))


@dataclasses.dataclass
class ReducedEventSummary:
    """Aggregated summary of repetitive network events."""
    pattern: str
    method: str
    event_count: int
    total_bytes: int
    status_distribution: Dict[int, int]
    min_duration_ms: float
    max_duration_ms: float
    avg_duration_ms: float
    first_timestamp: float
    last_timestamp: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "reduced_summary",
            "pattern": self.pattern,
            "method": self.method,
            "event_count": self.event_count,
            "total_bytes": self.total_bytes,
            "status_distribution": {str(k): v for k, v in self.status_distribution.items()},
            "min_duration_ms": self.min_duration_ms,
            "max_duration_ms": self.max_duration_ms,
            "avg_duration_ms": self.avg_duration_ms,
            "first_timestamp": self.first_timestamp,
            "last_timestamp": self.last_timestamp,
        }


@dataclasses.dataclass
class ReductionResult:
    """Structured result of network log reduction."""
    total_original_events: int
    reduced_summaries: List[ReducedEventSummary]
    individual_events: List[NetworkEvent]

    @property
    def reduction_ratio(self) -> float:
        if self.total_original_events == 0:
            return 1.0
        retained = len(self.reduced_summaries) + len(self.individual_events)
        return float(retained) / float(self.total_original_events)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_original_events": self.total_original_events,
            "reduced_summaries": [s.to_dict() for s in self.reduced_summaries],
            "individual_events": [e.to_dict() for e in self.individual_events],
            "reduction_ratio": self.reduction_ratio,
        }


class NetworkEventLogReducer:
    """Reduces repetitive network event logs while preserving error and latency fidelity."""

    def __init__(
        self,
        min_group_size: int = 5,
        outlier_latency_ms: float = 1000.0,
    ) -> None:
        self.min_group_size = max(2, min_group_size)
        self.outlier_latency_ms = outlier_latency_ms

    def reduce_events(self, raw_events: Iterable[Any]) -> ReductionResult:
        """Compact raw network events into reduced summaries and outlier/error entries."""
        events: List[NetworkEvent] = [normalize_network_event(e) for e in raw_events]
        total_count = len(events)
        if total_count == 0:
            return ReductionResult(
                total_original_events=0,
                reduced_summaries=[],
                individual_events=[],
            )

        # Categorize into candidates for reduction vs must-preserve
        must_preserve: List[NetworkEvent] = []
        reduction_groups: Dict[Tuple[str, str], List[NetworkEvent]] = collections.defaultdict(list)

        for e in events:
            # Errors (4xx, 5xx, or network failure) are preserved with full fidelity
            if e.is_error:
                must_preserve.append(e)
                continue

            # Latency outliers are preserved individually
            if e.duration_ms >= self.outlier_latency_ms:
                must_preserve.append(e)
                continue

            # Group benign repetitive events by method and URL pattern
            pattern = extract_url_pattern(e.url)
            reduction_groups[(pattern, e.method)].append(e)

        summaries: List[ReducedEventSummary] = []
        individual: List[NetworkEvent] = list(must_preserve)

        for (pattern, method), group in reduction_groups.items():
            if len(group) < self.min_group_size:
                # Group too small to compress; retain as individual events
                individual.extend(group)
            else:
                durations = [g.duration_ms for g in group]
                timestamps = [g.timestamp for g in group]
                status_dist: Dict[int, int] = collections.defaultdict(int)
                total_bytes = 0
                for g in group:
                    status_dist[g.status_code] += 1
                    total_bytes += g.bytes_transferred

                summary = ReducedEventSummary(
                    pattern=pattern,
                    method=method,
                    event_count=len(group),
                    total_bytes=total_bytes,
                    status_distribution=dict(status_dist),
                    min_duration_ms=min(durations),
                    max_duration_ms=max(durations),
                    avg_duration_ms=sum(durations) / len(durations),
                    first_timestamp=min(timestamps),
                    last_timestamp=max(timestamps),
                )
                summaries.append(summary)

        return ReductionResult(
            total_original_events=total_count,
            reduced_summaries=summaries,
            individual_events=individual,
        )


@dataclasses.dataclass
class TraceArchiveInfo:
    """Metadata regarding an archived network trace."""
    trace_id: str
    archive_file: Path
    total_events: int
    reduced_summaries_count: int
    individual_events_count: int
    created_at: float


class NetworkTraceArchiver:
    """Archives reduced network traces to disk with metadata manifests."""

    def __init__(self, archive_dir: Path | str = "traces") -> None:
        self.archive_dir = Path(archive_dir)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    def archive_trace(
        self,
        trace_id: str,
        reduction_result: ReductionResult,
        metadata: Optional[Dict[str, Any]] = None,
        compress: bool = False,
    ) -> TraceArchiveInfo:
        """Write reduction result to a structured trace file."""
        ext = ".jsonl.gz" if compress else ".jsonl"
        out_file = self.archive_dir / f"{trace_id}{ext}"

        manifest = {
            "manifest_version": "1.0",
            "trace_id": trace_id,
            "created_at": time.time(),
            "total_original_events": reduction_result.total_original_events,
            "reduced_summaries_count": len(reduction_result.reduced_summaries),
            "individual_events_count": len(reduction_result.individual_events),
            "reduction_ratio": reduction_result.reduction_ratio,
            "metadata": metadata or {},
        }

        records: List[Dict[str, Any]] = [manifest]
        for s in reduction_result.reduced_summaries:
            records.append(s.to_dict())
        for e in reduction_result.individual_events:
            records.append(e.to_dict())

        lines = [json.dumps(r, sort_keys=True) for r in records]
        content = "\n".join(lines) + "\n"

        if compress:
            with gzip.open(out_file, "wt", encoding="utf-8") as f:
                f.write(content)
        else:
            out_file.write_text(content, encoding="utf-8")

        return TraceArchiveInfo(
            trace_id=trace_id,
            archive_file=out_file,
            total_events=reduction_result.total_original_events,
            reduced_summaries_count=len(reduction_result.reduced_summaries),
            individual_events_count=len(reduction_result.individual_events),
            created_at=manifest["created_at"],
        )

    def read_trace(self, trace_id_or_path: str | Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Read back an archived trace file, returning (manifest, records)."""
        target = Path(trace_id_or_path)
        if not target.exists():
            # Check within archive_dir
            for cand in [self.archive_dir / f"{trace_id_or_path}.jsonl", self.archive_dir / f"{trace_id_or_path}.jsonl.gz"]:
                if cand.exists():
                    target = cand
                    break

        if not target.exists():
            raise FileNotFoundError(f"Trace not found: {trace_id_or_path}")

        if target.name.endswith(".gz"):
            with gzip.open(target, "rt", encoding="utf-8") as f:
                raw_lines = f.readlines()
        else:
            raw_lines = target.read_text(encoding="utf-8").splitlines()

        lines = [line.strip() for line in raw_lines if line.strip()]
        if not lines:
            return {}, []

        manifest = json.loads(lines[0])
        records = [json.loads(line) for line in lines[1:]]
        return manifest, records


def reduce_network_log(
    capture_or_log: Any,
    min_group_size: int = 5,
    outlier_latency_ms: float = 1000.0,
) -> ReductionResult:
    """Convenience helper to reduce raw capture or network_log."""
    if isinstance(capture_or_log, dict):
        nl = capture_or_log.get("network_log") or []
    else:
        nl = capture_or_log or []

    reducer = NetworkEventLogReducer(
        min_group_size=min_group_size,
        outlier_latency_ms=outlier_latency_ms,
    )
    return reducer.reduce_events(nl)
