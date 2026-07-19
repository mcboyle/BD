"""Phase 9.5 -- LLM observability and audit.

Report-first observability over the shared contract. `llm_exec.execute` emits one
metadata record per call into this in-process log; `report()` aggregates per task:
success/failure/timeout/schema-failure/parse-failure counts, avg + p95 latency,
cache hit-rate, fallback count, review-required count, model-unavailable count, and
last error.

The recorded metadata is hashes + status flags ONLY (produced by
`llm_exec.result_metadata`) -- it never contains raw prompts, raw input, secrets,
cookies, auth headers, signed URLs, or capture bodies.
"""

from typing import Any, Dict, List, Optional

_events: List[Dict[str, Any]] = []
_MAX = 5000


def record(metadata: Dict[str, Any]) -> None:
    """Append one call's metadata. Best-effort, bounded, never raises."""
    try:
        _events.append(dict(metadata))
        if len(_events) > _MAX:
            del _events[:-_MAX]
    except Exception:  # pragma: no cover
        pass


def clear() -> None:
    _events.clear()


def events(task_id: Optional[str] = None) -> List[Dict[str, Any]]:
    if task_id is None:
        return list(_events)
    return [e for e in _events if e.get("task_id") == task_id]


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((pct / 100.0) * (len(s) - 1)))))
    return float(s[k])


def _blank() -> Dict[str, Any]:
    return {
        "total": 0, "success": 0, "failure": 0, "timeout": 0,
        "schema_failure": 0, "parse_failure": 0, "provider_unavailable": 0,
        "forbidden_input": 0, "invalid_spec": 0, "fallback": 0,
        "review_required": 0, "cache_hit": 0, "last_error": "",
        "_latencies": [], "_cache_calls": 0,
    }


def _accumulate(d: Dict[str, Any], e: Dict[str, Any]) -> None:
    d["total"] += 1
    status = e.get("status", "")
    if status == "success":
        d["success"] += 1
    else:
        d["failure"] += 1
    if status == "timeout":
        d["timeout"] += 1
    elif status == "schema_failure":
        d["schema_failure"] += 1
    elif status == "invalid_json":
        d["parse_failure"] += 1
    elif status == "provider_unavailable":
        d["provider_unavailable"] += 1
    elif status == "forbidden_input":
        d["forbidden_input"] += 1
    elif status == "invalid_spec":
        d["invalid_spec"] += 1
    if e.get("fallback_status") == "used":
        d["fallback"] += 1
    if e.get("review_required"):
        d["review_required"] += 1
    if e.get("cache_hit"):
        d["cache_hit"] += 1
    # cache participation = any call that consulted the cache; we approximate it as
    # all calls in the task (hit-rate denominator). Latency only for non-cache calls.
    d["_cache_calls"] += 1
    lat = e.get("latency_ms")
    if isinstance(lat, (int, float)) and not e.get("cache_hit"):
        d["_latencies"].append(float(lat))
    if e.get("error"):
        d["last_error"] = e["error"]


def _finalize(d: Dict[str, Any]) -> None:
    lats = d.pop("_latencies", [])
    cache_calls = d.pop("_cache_calls", 0) or 1
    d["avg_latency_ms"] = (sum(lats) / len(lats)) if lats else 0.0
    d["p95_latency_ms"] = _percentile(lats, 95)
    d["cache_hit_rate"] = d["cache_hit"] / cache_calls
    d["model_unavailable"] = d["provider_unavailable"] > 0


def report(task_id: Optional[str] = None) -> Dict[str, Any]:
    """Aggregate the audit log into a per-task report."""
    evs = events(task_id)
    by_task: Dict[str, Dict[str, Any]] = {}
    for e in evs:
        t = e.get("task_id", "?")
        _accumulate(by_task.setdefault(t, _blank()), e)
    for d in by_task.values():
        _finalize(d)
    return {"tasks": by_task, "total_events": len(evs)}
