"""bulk_downloader.subtitle_search -- Row 834: Elasticsearch full-text
subtitle / dialogue indexer.

bulk_downloader.subtitles fetches .srt sidecars but the dialogue inside
them is never indexed or searchable. This module parses SRT cues into
timestamped dialogue chunks, indexes them into Elasticsearch (index
``bd_subtitles``), and exposes full-text search that returns millisecond
video seek offsets.

Fail-soft: Elasticsearch is optional infrastructure. Any connection/HTTP
failure against it is caught and reported in the returned dict rather than
raised -- subtitle search must never crash a caller. ``search()`` reports
this as ``degraded: True`` (distinct from a real zero-match) so callers
don't confuse "ES is down" with "nothing matched".

Talks to Elasticsearch over its plain REST API via ``requests`` -- no
``elasticsearch`` client dependency required.

HTTP surface: ``bulk_downloader.app_subtitles`` exposes ``GET
/api/subtitles/search`` (this module's ``search``) and ``POST
/api/subtitles/index/<hid>`` / ``fetch`` with ``index: true`` (the
ingestion pipeline, ``index_sidecars``).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import requests

INDEX_NAME = "bd_subtitles"
_DEFAULT_ES_URL = "http://127.0.0.1:9200"
_TIMEOUT = 3

_TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})"
)


def _es_url() -> str:
    return os.environ.get("SUBTITLE_SEARCH_ELASTICSEARCH_URL", _DEFAULT_ES_URL)


def _to_ms(h: str, m: str, s: str, ms: str) -> int:
    return ((int(h) * 3600 + int(m) * 60 + int(s)) * 1000) + int(ms)


def parse_srt(text: str) -> list:
    """Parse SRT subtitle text into timestamped dialogue chunks.

    Returns a list of ``{start_ms, end_ms, text}`` dicts, one per cue, in
    file order. Cues without a recognizable timestamp line, or with no
    dialogue text, are skipped rather than raising.
    """
    chunks = []
    blocks = re.split(r"\r?\n\r?\n+", text.strip())
    for block in blocks:
        lines = block.splitlines()
        ts_idx = None
        match = None
        for i, line in enumerate(lines):
            m = _TIME_RE.search(line)
            if m:
                ts_idx = i
                match = m
                break
        if match is None:
            continue
        dialogue = " ".join(l.strip() for l in lines[ts_idx + 1:] if l.strip())
        if not dialogue:
            continue
        start_ms = _to_ms(*match.groups()[0:4])
        end_ms = _to_ms(*match.groups()[4:8])
        chunks.append({"start_ms": start_ms, "end_ms": end_ms, "text": dialogue})
    return chunks


def index_video(video_id: str, srt_path, es_url: str | None = None) -> dict:
    """Parse ``srt_path`` and bulk-index its dialogue into Elasticsearch.

    Returns ``{ok, indexed, error}``. Never raises: a missing/unreadable
    file or an unreachable Elasticsearch both return ``ok: False`` with an
    ``error`` string instead of propagating an exception.
    """
    p = Path(srt_path)
    if not p.exists():
        return {"ok": False, "indexed": 0, "error": f"file not found: {p}"}
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"ok": False, "indexed": 0, "error": str(e)[:300]}
    chunks = parse_srt(text)
    if not chunks:
        return {"ok": True, "indexed": 0, "error": None,
                "reason": "no dialogue parsed"}
    url = (es_url or _es_url()).rstrip("/")
    lines = []
    for i, c in enumerate(chunks):
        lines.append(json.dumps(
            {"index": {"_index": INDEX_NAME, "_id": f"{video_id}:{i}"}}))
        lines.append(json.dumps({
            "video_id": video_id,
            "start_ms": c["start_ms"],
            "end_ms": c["end_ms"],
            "text": c["text"],
        }))
    bulk_body = "\n".join(lines) + "\n"
    try:
        resp = requests.post(
            f"{url}/_bulk", data=bulk_body,
            headers={"Content-Type": "application/x-ndjson"},
            timeout=_TIMEOUT)
        resp.raise_for_status()
        result = resp.json()
        # Row 834 correctness lens (E2): count what Elasticsearch ACCEPTED,
        # not what we sent -- a bulk answer carries one item per action and
        # a per-item status; ``errors: true`` with every item 4xx used to be
        # reported as ``indexed: len(chunks)``.
        indexed = _accepted_items(result, len(chunks))
        has_errors = bool(result.get("errors")) or indexed < len(chunks)
        return {"ok": not has_errors, "indexed": indexed,
                "error": None if not has_errors else
                f"elasticsearch bulk index accepted {indexed} of {len(chunks)} items"}
    except requests.RequestException as e:
        return {"ok": False, "indexed": 0,
                "error": f"elasticsearch unreachable: {e}"[:300]}


def _accepted_items(bulk_result: dict, sent: int) -> int:
    """Number of bulk items Elasticsearch accepted (2xx per-item status).
    An answer without an ``items`` list (older/other servers) is trusted
    only when it also reports ``errors: false``."""
    items = bulk_result.get("items")
    if not isinstance(items, list):
        return sent if not bulk_result.get("errors") else 0
    accepted = 0
    for item in items:
        action = next(iter(item.values()), {}) if isinstance(item, dict) else {}
        status = action.get("status") if isinstance(action, dict) else None
        if isinstance(status, int) and 200 <= status < 300:
            accepted += 1
    return accepted


def sidecar_paths(video_path) -> list:
    """The .srt sidecars bulk_downloader.subtitles writes next to a video:
    ``<video>.srt`` and ``<video>.<lang>.srt`` (its naming convention)."""
    p = Path(video_path)
    found = []
    bare = p.with_suffix(".srt")
    if bare.is_file():
        found.append(bare)
    for sib in sorted(p.parent.glob(f"{p.stem}.*.srt")) if p.parent.is_dir() else []:
        if sib.is_file() and sib != bare:
            found.append(sib)
    return found


def index_sidecars(video_path, video_id: str, es_url: str | None = None) -> dict:
    """The ingestion pipeline (row 834): index every .srt sidecar of
    ``video_path`` under ``video_id`` (one ``<video_id>:<lang>`` document
    id space per file). Never raises; ``ok`` is False when any file failed
    or none was found."""
    files = []
    indexed = 0
    for path in sidecar_paths(video_path):
        lang = path.suffixes[-2].lstrip(".") if len(path.suffixes) >= 2 else "default"
        one = index_video(f"{video_id}:{lang}", path, es_url=es_url)
        files.append({"path": str(path), "lang": lang, **one})
        indexed += one.get("indexed", 0)
    if not files:
        return {"ok": False, "indexed": 0, "files": [],
                "error": f"no .srt sidecar next to {video_path}"}
    ok = all(f["ok"] for f in files)
    return {"ok": ok, "indexed": indexed, "files": files,
            "error": None if ok else "one or more sidecars failed to index"}


def search(query: str, k: int = 10, es_url: str | None = None) -> dict:
    """Full-text search over indexed subtitle dialogue.

    Returns ``{ok, degraded, results, error}`` where ``results`` is a list
    of ``{video_id, start_ms, end_ms, text, score}`` -- millisecond video
    seek offsets for the matching dialogue. When Elasticsearch is
    unreachable, returns ``{ok: True, degraded: True, results: []}``
    rather than raising, so callers can tell "search unavailable" apart
    from a genuine zero-match.
    """
    q = (query or "").strip()
    if not q:
        return {"ok": False, "degraded": False, "results": [],
                "error": "missing query"}
    try:
        k = int(k or 10)
    except (TypeError, ValueError):
        k = 10
    k = max(1, min(50, k))
    url = (es_url or _es_url()).rstrip("/")
    body = {"query": {"match": {"text": q}}, "size": k}
    try:
        resp = requests.post(f"{url}/{INDEX_NAME}/_search", json=body,
                              timeout=_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as e:
        return {"ok": True, "degraded": True, "results": [],
                "error": f"elasticsearch unreachable: {e}"[:300]}
    hits = (payload.get("hits") or {}).get("hits") or []
    results = []
    for h in hits:
        src = h.get("_source", {})
        results.append({
            "video_id": src.get("video_id"),
            "start_ms": src.get("start_ms"),
            "end_ms": src.get("end_ms"),
            "text": src.get("text"),
            "score": h.get("_score"),
        })
    return {"ok": True, "degraded": False, "results": results, "error": None}


def status(es_url: str | None = None) -> dict:
    """Reachability probe, surfaced by ``/api/subtitles/status``."""
    url = (es_url or _es_url()).rstrip("/")
    try:
        resp = requests.get(url, timeout=_TIMEOUT)
        resp.raise_for_status()
        return {"ok": True, "reachable": True, "index": INDEX_NAME}
    except requests.RequestException as e:
        return {"ok": True, "reachable": False, "index": INDEX_NAME,
                "error": str(e)[:200]}
