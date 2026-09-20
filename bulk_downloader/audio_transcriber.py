"""Row856: optional post-download whisper transcription/indexing hook.

Talks to a declared satellite whisper-compatible HTTP endpoint to transcribe
a downloaded audio file, producing a WebVTT transcript and lightweight topic
metadata (keyword extraction over the transcript text).

Failure semantics: a transcription failure (network error, bad response,
missing/unreadable file) must never affect download success. Every public
entry point below catches its own errors and returns {"ok": False, ...}
instead of raising. No call in this module authenticates to, or otherwise
interacts with, any download site — it only ever talks to the configured
transcription endpoint.

Follows the same injectable-transport shape as ollama_boot_probe.py: the
HTTP call is a constructor/call parameter so tests exercise fixture-
controlled responses instead of a real network endpoint.
"""
from __future__ import annotations

import base64
import json
import os
import re
from collections import Counter
from typing import Any, Callable, Optional
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "to",
    "of", "in", "on", "for", "with", "as", "at", "by", "it", "this", "that",
    "be", "we", "you", "i", "so", "if", "not", "just", "there", "have",
    "has", "had", "do", "does", "did", "will", "would", "can", "could",
    "about", "into", "from", "our", "your", "their", "its",
})

DEFAULT_TIMEOUT = 30.0


class _NoRedirect(HTTPRedirectHandler):
    """The declared satellite endpoint is the ONLY origin this client talks to
    (ssrf_egress_exemptions: declared-endpoint-only). A 3xx to anywhere -- same
    origin or not -- is refused rather than followed; following it would let the
    satellite steer the request (and the audio payload) to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        raise HTTPError(req.full_url, code, f"redirect to {newurl!r} refused: declared-endpoint-only", headers, fp)


_OPENER = build_opener(_NoRedirect())


def _same_origin(a: str, b: str) -> bool:
    ua, ub = urlsplit(a), urlsplit(b)
    return (ua.scheme, ua.hostname, ua.port or (443 if ua.scheme == "https" else 80)) == \
           (ub.scheme, ub.hostname, ub.port or (443 if ub.scheme == "https" else 80))


def _request_json(url: str, payload: dict, timeout: float) -> dict:
    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with _OPENER.open(request, timeout=timeout) as response:
        final = response.geturl()
        if not _same_origin(final, url):
            raise ValueError(f"response came from {final!r}, not the declared endpoint origin")
        parsed = json.loads(response.read().decode("utf-8", "replace"))
    if not isinstance(parsed, dict):
        raise ValueError("transcription endpoint returned non-object JSON")
    return parsed


def _format_ts(seconds: float) -> str:
    """hh:mm:ss.mmm from seconds. Rounds to whole milliseconds FIRST so 59.9999
    carries into 00:01:00.000 instead of rendering 00:00:60.000; a non-numeric
    or negative value renders as zero (a cue is never lost to a bad timestamp)."""
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        value = 0.0
    if value != value or value in (float("inf"), float("-inf")):
        value = 0.0
    total_ms = max(0, int(round(value * 1000)))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"


def segments_to_vtt(segments: list[dict]) -> str:
    """Render whisper-style segments ([{start, end, text}, ...]) as WebVTT."""
    lines = ["WEBVTT", ""]
    for seg in segments:
        if not isinstance(seg, dict):
            continue  # a null / malformed segment is skipped, never a crash
        start = _format_ts(seg.get("start") if seg.get("start") is not None else 0.0)
        end = _format_ts(seg.get("end") if seg.get("end") is not None else 0.0)
        text = _clean_text(seg.get("text"))
        lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _clean_text(value: Any) -> str:
    """Segment text as a str that can always be encoded as UTF-8: None -> "",
    lone surrogates (JSON \\ud800 escapes) are dropped rather than raised later
    at file-write time."""
    if value is None:
        return ""
    text = str(value)
    return text.encode("utf-8", "ignore").decode("utf-8").strip()


def extract_topics(text: str, top_n: int = 8) -> list[str]:
    """Cheap keyword-frequency topic extraction over the transcript text."""
    words = re.findall(r"[A-Za-z']+", text.lower())
    counts = Counter(w for w in words if len(w) > 2 and w not in _STOPWORDS)
    return [word for word, _ in counts.most_common(top_n)]


class TranscriberError(RuntimeError):
    def __init__(self, code: str, message: str):
        # No super().__init__(message) here: BaseException.__new__ already
        # populates self.args from the (code, message) passed to the
        # constructor, so an explicit call is a redundant, unobservable no-op.
        self.code = code


class AudioTranscriber:
    """Client for a declared satellite whisper-compatible endpoint.

    `request_json` is injectable so callers/tests can supply a
    fixture-controlled transport instead of a real network call.
    """

    def __init__(self, endpoint: str, timeout: float = DEFAULT_TIMEOUT,
                 request_json: Callable[[str, dict, float], dict] = _request_json):
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self._request_json = request_json

    def transcribe(self, audio_path: str) -> dict:
        """Return {segments: [...], language: str} from the satellite endpoint.

        Raises TranscriberError on any transport/response problem; never
        touches any download site.
        """
        try:
            with open(audio_path, "rb") as fh:
                audio_bytes = fh.read()
        except OSError as exc:
            raise TranscriberError("audio_unreadable", str(exc)) from None
        payload = {"audio_b64": base64.b64encode(audio_bytes).decode("ascii"),
                   "filename": os.path.basename(audio_path)}
        try:
            data = self._request_json(self.endpoint + "/v1/transcribe", payload, self.timeout)
        except TranscriberError:
            raise
        except Exception as exc:
            raise TranscriberError("transcribe_request_failed", str(exc)) from None
        segments = data.get("segments")
        if not isinstance(segments, list):
            raise TranscriberError("bad_response", "response missing 'segments' list")
        return {"segments": segments, "language": data.get("language", "")}


def index_audio(audio_path: str, endpoint: str,
                 request_json: Optional[Callable[[str, dict, float], dict]] = None,
                 timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Transcribe one audio file and build VTT + topic metadata.

    Never raises: on any failure returns {"ok": False, "error": ..., "code": ...}.
    On success returns {"ok": True, "vtt": <str>, "topics": [...], "language": <str>}.
    """
    transcriber = AudioTranscriber(endpoint, timeout=timeout,
                                    request_json=request_json or _request_json)
    try:
        result = transcriber.transcribe(audio_path)
    except TranscriberError as exc:
        return {"ok": False, "code": exc.code, "error": str(exc)}
    except Exception as exc:  # defensive: a transcription failure must never propagate
        return {"ok": False, "code": "unexpected_error", "error": str(exc)}
    try:
        vtt = segments_to_vtt(result["segments"])
        full_text = " ".join(_clean_text(seg.get("text")) for seg in result["segments"]
                             if isinstance(seg, dict))
    except Exception as exc:  # rendering never propagates either
        return {"ok": False, "code": "render_failed", "error": str(exc)}
    return {
        "ok": True,
        "vtt": vtt,
        "topics": extract_topics(full_text),
        "language": result.get("language", ""),
    }


def post_download_hook(audio_path: str, output_dir: str, endpoint: Optional[str],
                        request_json: Optional[Callable[[str, dict, float], dict]] = None,
                        timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Optional post-download hook: index `audio_path` and write sidecar
    `<basename>.vtt` / `<basename>.topics.json` files into `output_dir`.

    Non-blocking by construction: any failure (including no endpoint
    configured) is reported in the returned dict and never raised, so a
    caller wiring this into a download pipeline cannot have it fail the
    download. No site login or site interaction happens here at all.
    """
    if not endpoint:
        return {"ok": False, "skipped": True, "code": "no_endpoint", "error": "no transcription endpoint configured"}
    try:
        indexed = index_audio(audio_path, endpoint, request_json=request_json, timeout=timeout)
    except Exception as exc:  # belt-and-suspenders: index_audio already catches, but the hook must too
        return {"ok": False, "skipped": False, "code": "unexpected_error", "error": str(exc)}
    if not indexed.get("ok"):
        return {"ok": False, "skipped": False, **indexed}
    base = os.path.splitext(os.path.basename(audio_path))[0]
    try:
        os.makedirs(output_dir, exist_ok=True)
        vtt_path = os.path.join(output_dir, base + ".vtt")
        topics_path = os.path.join(output_dir, base + ".topics.json")
        with open(vtt_path, "w", encoding="utf-8") as fh:
            fh.write(indexed["vtt"])
        with open(topics_path, "w", encoding="utf-8") as fh:
            json.dump({"topics": indexed["topics"], "language": _clean_text(indexed["language"])}, fh)
    except (OSError, ValueError, TypeError) as exc:  # UnicodeEncodeError is a ValueError
        return {"ok": False, "skipped": False, "code": "write_failed", "error": str(exc)}
    return {"ok": True, "vtt_path": vtt_path, "topics_path": topics_path, "topics": indexed["topics"]}
