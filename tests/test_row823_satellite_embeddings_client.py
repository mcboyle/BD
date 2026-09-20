"""RED-first tests for Row 823: satellite GPU embeddings offload.

Routes embedding requests to the dedicated GPU embeddings proxy
(10.0.70.72:8081, Ollama bge-m3) with a fall back to the local CPU embedder
(bulk_downloader.embeddings.embed) on connection error or timeout.
"""
from __future__ import annotations

import io
import json
import socket
import time
import urllib.error

import pytest

BD_GATE_SCOPE = "module"


# The satellite is reached through ``OllamaProvider._http_post`` (the one
# censused urlopen in ai_provider), so the seam these tests patch is the name
# THAT module bound at import, not ``urllib.request.urlopen``.
UPSTREAM_SEAM = "bulk_downloader.ai_provider.urlopen"


class _Resp:
    """Minimal urlopen() response double returning canned JSON bytes."""

    status = 200

    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body


def _satellite(monkeypatch, body: bytes):
    """Route the provider's urlopen to a canned satellite reply and record the request."""
    seen = {}

    def _open(req, timeout=None):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data.decode("utf-8"))
        seen["timeout"] = timeout
        return _Resp(body)

    monkeypatch.setattr(UPSTREAM_SEAM, _open)
    return seen


def test_successful_vector_response_from_satellite_endpoint(monkeypatch):
    """Success gate: the REMOTE vector comes back verbatim, and it is NOT
    what the local CPU fallback would have produced (E1: a fallback must
    not be able to pass this gate)."""
    from bulk_downloader import embeddings_client as EC
    from bulk_downloader import embeddings as LOCAL
    remote = [0.25, -0.5, 1.0]
    seen = _satellite(monkeypatch, json.dumps({"embedding": remote}).encode("utf-8"))
    vec = EC.embed("hello world")
    assert vec == remote
    assert all(isinstance(x, float) for x in vec)
    assert vec != LOCAL.embed("hello world", dims=EC._local.DEFAULT_DIMS)
    assert seen["url"] == EC.DEFAULT_ENDPOINT
    assert seen["body"] == {"model": EC.DEFAULT_MODEL, "prompt": "hello world"}
    assert seen["timeout"] == EC.DEFAULT_TIMEOUT


@pytest.mark.parametrize("body", [
    b"null",
    b"[]",
    b"{}",
    b'{"embedding": null}',
    b'{"embedding": []}',
    b'{"embedding": [null]}',
    b'{"embedding": ["0.1"]}',
    b'{"embedding": [true]}',
    b'{"embedding": [NaN]}',
    b'{"embedding": {"0": 1.0}}',
])
def test_malformed_satellite_response_falls_back_without_raising(monkeypatch, body):
    """E2: null / [] / [null] and friends are a documented local fallback,
    never an AttributeError/TypeError escaping embed()."""
    from bulk_downloader import embeddings_client as EC
    from bulk_downloader import embeddings as LOCAL
    _satellite(monkeypatch, body)
    vec = EC.embed("hello world")
    assert vec == LOCAL.embed("hello world", dims=EC._local.DEFAULT_DIMS)


def test_integer_elements_are_accepted_as_floats(monkeypatch):
    from bulk_downloader import embeddings_client as EC
    _satellite(monkeypatch, b'{"embedding": [1, 2]}')
    assert EC.embed("x") == [1.0, 2.0]


def test_falls_back_to_local_cpu_embedding_within_200ms_on_socket_timeout(monkeypatch):
    from bulk_downloader import embeddings_client as EC
    from bulk_downloader import embeddings as LOCAL

    def _timeout(*a, **k):
        raise socket.timeout("timed out")

    monkeypatch.setattr(UPSTREAM_SEAM, _timeout)

    start = time.monotonic()
    vec = EC.embed("hello world")
    elapsed_ms = (time.monotonic() - start) * 1000

    assert elapsed_ms < 200
    assert vec == LOCAL.embed("hello world", dims=EC._local.DEFAULT_DIMS)


def test_falls_back_on_connection_refused(monkeypatch):
    from bulk_downloader import embeddings_client as EC
    from bulk_downloader import embeddings as LOCAL

    def _refused(*a, **k):
        raise urllib.error.URLError(ConnectionRefusedError("refused"))

    monkeypatch.setattr(UPSTREAM_SEAM, _refused)
    vec = EC.embed("some text")
    assert vec == LOCAL.embed("some text", dims=EC._local.DEFAULT_DIMS)


def test_zero_site_login_interaction():
    import inspect
    from bulk_downloader import embeddings_client as EC
    src = inspect.getsource(EC)
    assert "login" not in src.lower()
    assert "10.0.70.95" not in src


def test_a_bad_http_status_falls_back_without_raising(monkeypatch):
    """A 500 from the proxy is (ok=False) at the shared helper, a local fallback here."""
    from bulk_downloader import embeddings_client as EC
    from bulk_downloader import embeddings as LOCAL

    def _http_error(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(UPSTREAM_SEAM, _http_error)
    assert EC.embed("some text") == LOCAL.embed("some text", dims=EC._local.DEFAULT_DIMS)


def test_a_failed_status_wins_over_a_plausible_error_body(monkeypatch):
    """ok=False from the helper is authoritative: a 503 whose error body happens
    to carry an ``embedding`` list is still a local fallback, never the remote vector."""
    from bulk_downloader import embeddings_client as EC
    from bulk_downloader import embeddings as LOCAL
    decoy = b'{"embedding": [9.0, 9.0, 9.0]}'

    def _http_error(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, io.BytesIO(decoy))

    monkeypatch.setattr(UPSTREAM_SEAM, _http_error)
    vec = EC.embed("some text")
    assert vec != [9.0, 9.0, 9.0]
    assert vec == LOCAL.embed("some text", dims=EC._local.DEFAULT_DIMS)


def test_the_module_opens_no_egress_site_of_its_own():
    """The train dropped row 823 as SSRF-CENSUS: an ``urllib.request.urlopen``
    in this module was a 25th non-httpx egress site the census did not account
    for.  The fix routes the POST through the censused ``AIProvider._http_post``,
    so the tree-derived census must hold NO site in this module and the module
    must not bind a transport itself.  Positive control beside the zero: the
    helper it delegates to IS in the census."""
    import ast
    import pathlib
    import sys
    from bulk_downloader import embeddings_client as EC
    root = pathlib.Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from tools import ssrf_client_census as census_tool
    result = census_tool.egress_census(root)
    assert len(result.sites) > 0, "the egress population is empty -- it proves nothing"
    keys = set(result.keys)
    assert "bulk_downloader/ai_provider.py::AIProvider._http_post" in keys, sorted(keys)
    mine = sorted(k for k in keys if k.startswith("bulk_downloader/embeddings_client.py::"))
    assert mine == [], f"embeddings_client opens its own egress site(s): {mine}"
    tree = ast.parse(pathlib.Path(EC.__file__).read_text(encoding="utf-8"))
    imported = {
        (n.module if isinstance(n, ast.ImportFrom) else a.name)
        for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in n.names
    }
    assert not imported & {"urllib.request", "requests", "aiohttp", "httpx", "socket"}, sorted(imported)
