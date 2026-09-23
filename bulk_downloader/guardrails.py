"""Fail-open, async metadata safety inspection for pre-download callers."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.request import Request, urlopen


DEFAULT_GUARDRAILS_ENDPOINT = "http://10.0.70.125:8005/api/chat"
_MAX_METADATA_CHARS = 4096

RequestFn = Callable[[dict[str, Any], str], Mapping[str, Any] | Awaitable[Mapping[str, Any]]]


def _metadata_text(metadata: Mapping[str, object]) -> str:
    """Build a bounded, stable input from untrusted download metadata."""
    values = []
    for key in ("title", "description", "tags"):
        value = metadata.get(key)
        if value not in (None, "", []):
            values.append(f"{key}: {value}")
    return "\n".join(values)[:_MAX_METADATA_CHARS]


def _default_request(payload: dict[str, Any], endpoint: str) -> Mapping[str, Any]:
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=2) as response:  # nosec B310 - fixed operator endpoint
        decoded = json.loads(response.read().decode("utf-8"))
    return decoded if isinstance(decoded, Mapping) else {}


def _is_unsafe(response: Mapping[str, Any]) -> bool:
    if response.get("unsafe") is True or response.get("blocked") is True:
        return True
    message = response.get("message")
    content = message.get("content") if isinstance(message, Mapping) else response.get("content")
    return isinstance(content, str) and content.strip().lower().startswith("unsafe")


async def pre_download_safety_check(
    metadata: Mapping[str, object],
    *,
    enabled: bool = False,
    request: RequestFn | None = None,
) -> bool:
    """Return whether a download may proceed; disabled or unavailable checks allow it."""
    if not enabled:
        return True

    payload = {
        "model": "llama-guard3:8b",
        "messages": [{"role": "user", "content": _metadata_text(metadata)}],
        "stream": False,
    }
    try:
        if request is None:
            response = await asyncio.to_thread(
                _default_request, payload, DEFAULT_GUARDRAILS_ENDPOINT
            )
        else:
            response = request(payload, DEFAULT_GUARDRAILS_ENDPOINT)
        if inspect.isawaitable(response):
            response = await response
        return not _is_unsafe(response)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return True


def check_batch(metadatas: list[Mapping[str, object]], *, enabled: bool = False) -> list[bool]:
    """Run pre_download_safety_check for every item on one runtime loop, concurrently.

    Replaces one ``asyncio.run`` event loop per item; verdicts come back in input order.
    """
    from .async_worker_runtime import AsyncWorkerRuntime

    runtime = AsyncWorkerRuntime()
    runtime.start()
    try:
        return runtime.run_all([pre_download_safety_check(m, enabled=enabled) for m in metadatas])
    finally:
        runtime.shutdown()
