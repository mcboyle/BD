"""bulk_downloader.embeddings_client -- Row 823: satellite GPU inference offload.

Routes embedding requests to the dedicated GPU embeddings proxy
(``http://10.0.70.72:8081``, Ollama serving ``bge-m3``) instead of the local
CPU feature-hashing embedder in :mod:`bulk_downloader.embeddings`. On any
connection error or timeout talking to the satellite endpoint, falls back to
the local embedder so search/indexing never hard-fails when the GPU box is
unreachable. Touches no captured-site authentication flow (Fleet Rule 21) -- this is
fleet infrastructure (an internal Ollama instance), not a captured site.

TRANSPORT.  The POST goes through ``OllamaProvider._http_post`` -- the Ollama
plumbing :mod:`bulk_downloader.embeddings` designates for exactly this follow
-- so this module opens NO egress site of its own: the row 805 census
(``tools/ssrf_client_census.py`` / ``bulk_downloader/ssrf_egress_exemptions.py``)
already accounts for ``AIProvider._http_post`` and its population is unchanged
by this module.  ``_http_post`` never raises and returns
``(ok, status, parsed_json_or_text, latency_ms)``; every non-OK outcome is a
local fallback here.
"""
from __future__ import annotations

from . import embeddings as _local
from .ai_provider import OllamaProvider

DEFAULT_ENDPOINT = "http://10.0.70.72:8081/api/embeddings"
DEFAULT_MODEL = "bge-m3"
DEFAULT_TIMEOUT = 5.0


def embed(text: str, endpoint: str = DEFAULT_ENDPOINT, model: str = DEFAULT_MODEL,
          timeout: float = DEFAULT_TIMEOUT, dims: int = _local.DEFAULT_DIMS) -> list[float]:
    """Embed ``text`` via the satellite GPU proxy; fall back to the local
    CPU embedder on connection error, timeout or a malformed reply. Never raises."""
    body = {"model": model, "prompt": text or ""}
    ok, _status, payload, _ms = OllamaProvider(endpoint=endpoint)._http_post(
        endpoint, body, {}, timeout)
    if not ok:
        return _local.embed(text, dims=dims)
    vec = _valid_vector(payload)
    if vec is None:
        return _local.embed(text, dims=dims)
    return vec


def _valid_vector(payload: object) -> list[float] | None:
    """The satellite's ``embedding`` as floats, or None when the response is
    not a non-empty list of finite numbers (null body, ``{}``, ``[]``,
    ``[null]``, strings, bools): every malformed shape is a fallback, never
    an exception (the documented contract of :func:`embed`)."""
    if not isinstance(payload, dict):
        return None
    vec = payload.get("embedding")
    if not isinstance(vec, list) or not vec:
        return None
    out: list[float] = []
    for x in vec:
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            return None
        f = float(x)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        out.append(f)
    return out
